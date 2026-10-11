#!/usr/bin/env python3
"""Kubectl-only orchestration; all timing clocks and sampling stay on the GPU node."""
import argparse
import copy
import csv
import datetime as dt
import io
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid

import analyze

LAB = Path(__file__).resolve().parents[1]
DATA = LAB.parents[1] / 'data/e3'
NAMESPACE = 'gpu-performance-lab'
PART = 'app.kubernetes.io/part-of'
COMPONENT = 'app.kubernetes.io/component'
RUN_LABEL = 'dra-book-run'
IMAGE_DIGEST = 'sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755'
HAMI_LIBRARY_SHA256 = '56fc0944896337cc0ee439cbc7364105ccac11e8dc38add6a3dc467cea34b18f'


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def items(text):
    """kubectl dry-run emits either a List or consecutive JSON documents."""
    decoder, result = json.JSONDecoder(), []
    while text.strip():
        text = text.lstrip()
        value, end = decoder.raw_decode(text)
        result.extend(value['items'] if value.get('kind') == 'List' else [value])
        text = text[end:]
    return result


def selection(environ):
    smoke = environ.get('SMOKE', '0') == '1'
    if environ.get('SMOKE', '0') not in ('0', '1') or environ.get('HAMI', '0') not in ('0', '1'):
        raise ValueError('SMOKE and HAMI must be 0 or 1')
    modes = environ.get('MODES', 'all').split()
    if modes == ['all']:
        modes = ['exclusive', 'sequential', 'timeslicing', 'mps']
    workloads = environ.get('WORKLOADS', ' '.join(analyze.WORKLOADS)).split()
    ns = [int(n) for n in environ.get('NS', '1 2 4').split()]
    blocks = int(environ.get('BLOCKS', '5'))
    if smoke:
        modes, workloads, ns, blocks = ['exclusive', 'sequential', 'timeslicing', 'mps'], ['bursty-aligned'], [2], 1
    if environ.get('HAMI', '0') == '1' and 'hami' not in modes:
        modes.append('hami')
    if not modes or any(m not in analyze.MODES for m in modes):
        raise ValueError('unknown mode')
    if not workloads or any(w not in analyze.WORKLOADS for w in workloads):
        raise ValueError('unknown workload')
    if not ns or any(n not in (1, 2, 4) for n in ns) or not 1 <= blocks <= 5:
        raise ValueError('NS must be a subset of 1 2 4; BLOCKS must be 1..5')
    return dict(smoke=smoke, modes=modes, workloads=workloads, ns=ns, blocks=blocks,
                node=environ.get('GPU_NODE', 'dra-a40'),
                gpu_uuid=environ.get('GPU_UUID', 'GPU-29ffee3e-bee4-e341-917a-fe7e43e977af'),
                hami_class=environ.get('HAMI_DEVICE_CLASS', 'hami-core-gpu.project-hami.io'))


def frozen_config(options):
    frozen = analyze.read_json(DATA / 'benchmark-config.json')
    frozen['gpu_uuid'] = options['gpu_uuid']
    if options['smoke']:
        # Reducing bursts reduces BOTH total GEMMs and total waits by the same
        # factor. Do not divide each sleep: that would change the duty cycle.
        for base in frozen['workloads'].values():
            base['iterations'] = math_ceil_div(base['iterations'], 10)
    return frozen


def math_ceil_div(value, divisor):
    return (value + divisor - 1) // divisor


def matrix(options):
    result = []
    for cell in analyze.read_json(DATA / 'matrix.json'):
        n, mode = cell['concurrency'], cell['mode']
        wanted_n = n in options['ns'] or (mode == 'exclusive' and n == 1)
        if cell['block'] <= options['blocks'] and cell['workload_id'] in options['workloads'] and mode in options['modes'] and wanted_n:
            result.append(cell)
    return result


class Kube:
    def __init__(self, out):
        self.out = Path(out)

    def command(self, *args):
        return ['kubectl', '--request-timeout=30s', *args]

    def call(self, *args, stdin=None, timeout=90):
        command = self.command(*args)
        result = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=timeout)
        with (self.out / 'commands.jsonl').open('a') as stream:
            stream.write(json.dumps({'utc': utc(), 'command': command, 'exit_code': result.returncode,
                                     'stdout': result.stdout, 'stderr': result.stderr}) + '\n')
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or 'kubectl failed')
        return result.stdout

    def get(self, resource, *args):
        raw = self.call('get', resource, *args, '-o', 'json')
        return json.loads(raw) if raw.strip() else None

    def apply(self, obj):
        return self.call('apply', '-f', '-', stdin=json.dumps(obj))


def selector(run):
    return f'{PART}=dra-book,{RUN_LABEL}={run["nonce"]}'


def owned(obj, run):
    labels = obj['metadata'].get('labels', {})
    if labels.get(PART) != 'dra-book' or labels.get(RUN_LABEL) != run['nonce']:
        raise ValueError('object ownership differs: ' + obj['metadata']['name'])


def driver(kube, node):
    pods = kube.get('pods', '-n', 'gpu-operator', '-l', 'app=nvidia-driver-daemonset',
                    '--field-selector', 'spec.nodeName=' + node)['items']
    running = [p for p in pods if p['status'].get('phase') == 'Running' and
               any(c['name'] == 'nvidia-driver-ctr' and c.get('ready') for c in p['status'].get('containerStatuses', []))]
    if len(running) != 1:
        raise ValueError('requires exactly one ready GPU Operator driver Pod on ' + node)
    return running[0]['metadata']['name']


def driver_args(pod):
    return ['exec', '-n', 'gpu-operator', pod, '-c', 'nvidia-driver-ctr']


def host_args(pod, action, gpu_uuid, nonce, *arguments):
    return [*driver_args(pod), '-i', '--', 'chroot', '/proc/1/root', '/usr/bin/python3', '-u', '-', action, gpu_uuid, nonce, *arguments]


def host_call(kube, pod, action, run, *arguments):
    return kube.call(*host_args(pod, action, run['options']['gpu_uuid'], run['nonce'], *arguments),
                     stdin=(LAB / 'analysis/observer.py').read_text())


def target(kube, options, mode):
    expected = 'hami-core-gpu.project-hami.io' if mode == 'hami' else 'gpu.nvidia.com'
    found = []
    for item in kube.get('resourceslices.resource.k8s.io')['items']:
        spec = item['spec']
        for device in spec.get('devices', []):
            attrs = device.get('attributes', {})
            value = attrs.get('uuid', attrs.get(spec['driver'] + '/uuid', {}))
            if value.get('string') == options['gpu_uuid']:
                found.append((spec, device))
    if len(found) != 1 or found[0][0].get('nodeName') != options['node']:
        raise ValueError('requires exactly one publisher for the target GPU/node')
    spec, device = found[0]
    if spec['driver'] != expected:
        return None  # Caller records a provider boundary; never changes a provider.
    if bool(device.get('allowMultipleAllocations')) != (mode == 'hami'):
        raise ValueError('NVIDIA ConsumableShares must be off; HAMi capacity sharing must be on')
    class_name = options['hami_class'] if mode == 'hami' else 'gpu.nvidia.com'
    device_class = kube.get('deviceclass', class_name)
    if device_class['spec'].get('config'):
        raise ValueError('DeviceClass has overriding config')
    return {'driver': expected, 'pool': spec['pool']['name'], 'device': device['name']}


def native_features(kube, options):
    pods = kube.get('pods', '-A', '-l', 'dra-driver-nvidia-gpu-component=kubelet-plugin',
                    '--field-selector', 'spec.nodeName=' + options['node'])['items']
    selected = [(pod, c) for pod in pods for c in pod['spec']['containers'] if c['name'] == 'gpus']
    if len(selected) != 1:
        raise ValueError('requires one identifiable native kubelet plugin')
    pod, container = selected[0]
    if any('--feature-gates' in arg for arg in container.get('args', [])):
        raise ValueError('CLI feature gates differ; prepare the native provider explicitly')
    raw = next((e.get('value', '') for e in container.get('env', []) if e['name'] == 'FEATURE_GATES'), '')
    flags = dict(item.split('=', 1) for item in raw.split(',') if item)
    if flags.get('MPSSupport') != 'true' or flags.get('TimeSlicingSettings') != 'true' or flags.get('ConsumableShares', 'false') != 'false':
        raise ValueError('native feature gates differ; run install-native-settings.sh explicitly')
    return pod


def idle(kube, pod, run, gpu_target, out):
    opts = run['options']
    raw = kube.call(*driver_args(pod), '--', 'nvidia-smi', '-i', opts['gpu_uuid'],
                    '--query-gpu=uuid,memory.used,compute_mode', '--format=csv,noheader,nounits')
    processes = kube.call(*driver_args(pod), '--', 'nvidia-smi', '-i', opts['gpu_uuid'],
                          '--query-compute-apps=pid', '--format=csv,noheader,nounits')
    values = [v.strip() for v in next(csv.reader(io.StringIO(raw)))]
    if values[0] != opts['gpu_uuid'] or float(values[1]) > 64 or values[2] != 'Default' or processes.strip():
        raise ValueError('target GPU must have no CUDA processes, <=64 MiB used, and Default compute mode')
    for claim in kube.get('resourceclaims.resource.k8s.io', '-A')['items']:
        allocation = claim.get('status', {}).get('allocation') or {}
        for row in allocation.get('devices', {}).get('results', []):
            if all(row.get(key) == gpu_target[key] for key in ('driver', 'pool', 'device')):
                raise ValueError('target GPU already has an allocated Claim')
    save(out, {'gpu_raw': raw, 'processes_raw': processes, 'utc': utc()})


def wait_for(check, timeout, description):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value is not None and value is not False:
            return value
        time.sleep(.25)
    raise ValueError('timeout: ' + description)


def rendered(kube, mode):
    raw = kube.call('kustomize', str(LAB / 'manifests/overlays' / mode), '--load-restrictor=LoadRestrictionsNone')
    return items(kube.call('create', '--dry-run=client', '--validate=false', '-f', '-', '-o', 'json', stdin=raw))


def prepare(objects, cell, frozen, run):
    result = copy.deepcopy(objects)
    jobs = []
    for obj in result:
        obj['metadata'].setdefault('labels', {})[RUN_LABEL] = run['nonce']
        if obj['kind'] in ('ResourceClaim', 'ResourceClaimTemplate'):
            spec = obj['spec']['spec'] if obj['kind'] == 'ResourceClaimTemplate' else obj['spec']
            req = spec['devices']['requests'][0]['exactly']
            driver_name = 'hami-core-gpu.project-hami.io' if cell['mode'] == 'hami' else 'gpu.nvidia.com'
            req['selectors'][0]['cel']['expression'] = f'device.attributes["{driver_name}"].uuid == "{run["options"]["gpu_uuid"]}"'
            if obj['kind'] == 'ResourceClaimTemplate':
                obj['spec']['metadata']['labels'][RUN_LABEL] = run['nonce']
            if cell['mode'] == 'hami':
                req['deviceClassName'] = run['options']['hami_class']
                req['capacity']['requests']['cores'] = str(100 // cell['concurrency'])
        if obj['kind'] != 'Job':
            continue
        for i in range(cell['concurrency']):
            job = copy.deepcopy(obj)
            job['metadata']['name'] = 'benchmark-lead' if i == 0 else f'benchmark-partner-{i}'
            template = job['spec']['template']
            template['metadata']['labels'][RUN_LABEL] = run['nonce']
            spec = template['spec']
            spec['nodeSelector']['kubernetes.io/hostname'] = run['options']['node']
            cfg = analyze.task_config(frozen['workloads'][cell['workload_id']], cell['workload_id'], cell['concurrency'], i)
            c = spec['containers'][0]
            c['args'][1] = json.dumps(cfg, sort_keys=True)
            for container in spec['containers'] + spec['initContainers']:
                for env in container['env']:
                    if env['name'] == 'EXPERIMENT_RUN':
                        env['value'] = run['nonce']
                    if env['name'] == 'GPU_UUID':
                        env['value'] = frozen['gpu_uuid']
                    if env['name'] == 'TASK_ID':
                        env['value'] = str(i)
            jobs.append(job)
    return [obj for obj in result if obj['kind'] != 'Job'], jobs


def worker_read(kube, pod):
    return json.loads(kube.call('exec', '-n', NAMESPACE, pod, '-c', 'workload', '--',
                                'python', '/opt/lab/control.py', 'read'))


def worker_send(kube, pod, command):
    kube.call('exec', '-n', NAMESPACE, pod, '-c', 'workload', '--',
              'python', '/opt/lab/control.py', 'send', json.dumps(command))


def pod_for(kube, job, run):
    pods = kube.get('pods', '-n', NAMESPACE, '-l', selector(run) + ',job-name=' + job['metadata']['name'])['items']
    if not pods:
        return None
    if len(pods) != 1:
        raise ValueError('Job has more than one attempt')
    pod = pods[0]
    owned(pod, run)
    if pod['status'].get('phase') in ('Failed', 'Succeeded'):
        raise ValueError('worker exited before READY')
    statuses = pod['status'].get('containerStatuses', [])
    if any(c.get('restartCount', 0) for c in statuses):
        raise ValueError('worker restarted')
    return pod if statuses and 'running' in statuses[0].get('state', {}) else None


def barrier(kube, pods, marker):
    values = {}
    for cfg, pod in pods:
        current = kube.get('pod', pod['metadata']['name'], '-n', NAMESPACE)
        if current['metadata']['uid'] != pod['metadata']['uid'] or current['status'].get('phase') in ('Failed', 'Succeeded'):
            raise ValueError('worker UID changed or exited before ' + marker)
        value = worker_read(kube, pod['metadata']['name'])
        if 'error.json' in value:
            raise ValueError('worker failed: ' + str(value['error.json']))
        if marker not in value:
            return None
        values[cfg['task_id']] = value
    return values


def mps_runtime(kube, driver_pod, run, workers, out):
    mode = kube.call(*driver_args(driver_pod), '--', 'nvidia-smi', '-i', run['options']['gpu_uuid'],
                     '--query-gpu=compute_mode', '--format=csv,noheader,nounits').strip()
    raw = kube.call(*driver_args(driver_pod), '--', 'nvidia-smi', '-i', run['options']['gpu_uuid'],
                   '--query-compute-apps=pid,process_name,used_memory', '--format=csv,noheader,nounits')
    evidence = {'compute_mode': mode, 'processes_raw': raw, 'expected_workers': workers}
    path = out / 'mps-runtime.json'
    save(path, evidence)
    processes = []
    for fields in csv.reader(io.StringIO(raw)):
        if not fields:
            continue
        if len(fields) != 3 or not fields[0].strip().isdigit() or int(fields[0]) <= 0:
            raise ValueError('MPS process listing differs')
        processes.append({'pid': int(fields[0]), 'process_name': fields[1].strip(), 'used_memory_mib': fields[2].strip()})
    evidence['processes'] = processes
    save(path, evidence)
    servers = [p for p in processes if Path(p['process_name']).name == 'nvidia-cuda-mps-server']
    if mode != 'Exclusive_Process' or not servers or len({p['pid'] for p in processes}) != len(processes):
        raise ValueError('MPS compute mode/server identity differs')
    # Volta+ lists the server AND clients. Names such as "python" alone cannot
    # identify a client; match its host /proc identity to this READY worker.
    clients = [p['pid'] for p in processes if p not in servers]
    identities = json.loads(host_call(kube, driver_pod, 'processes', run, json.dumps(clients))) if clients else []
    evidence['client_identities'] = identities
    save(path, evidence)
    if len(identities) != len(clients) or {p['host_pid'] for p in identities} != set(clients):
        raise ValueError('MPS client PID listing differs')
    for identity in identities:
        matching = [w for w in workers if identity['namespace_pids'][-1] == w['pid'] and
                    identity['uid'] == w['uid'] and identity['env'].get('POD_NAME') == w['pod_name'] and
                    identity['env'].get('POD_UID') == w['pod_uid'] and
                    identity['env'].get('EXPERIMENT_RUN') == w['run_id'] == run['nonce']]
        if len(matching) != 1 or '/opt/lab/worker.py' not in identity['command']:
            raise ValueError('MPS client is not an expected batch worker: ' + str(identity['host_pid']))


class Observer:
    def __init__(self, kube, pod, run, out):
        self.kube, self.pod, self.run, self.out = kube, pod, run, Path(out)
        self.nonce = uuid.uuid4().hex
        self.state = dict(run, nonce=self.nonce)
        self.output = (self.out / 'observer-stream.jsonl').open('w')
        self.errors = (self.out / 'observer-stderr.txt').open('w')
        command = ['kubectl', '--request-timeout=0s', *host_args(pod, 'observe', run['options']['gpu_uuid'], self.nonce)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=self.output, stderr=self.errors, text=True)
        self.process.stdin.write((LAB / 'analysis/observer.py').read_text())
        self.process.stdin.close()

    def ready(self):
        if self.process.poll() is not None:
            raise ValueError('GPU observer exited before start; inspect observer-stderr.txt')
        try:
            records = analyze.jsonl(self.out / 'observer-stream.jsonl')
        except json.JSONDecodeError:
            return None
        return records[0] if len(records) >= 2 and records[0].get('event') == 'OBSERVER_READY' else None

    def stop(self):
        if self.process.poll() is None:
            host_call(self.kube, self.pod, 'stop', self.state)
        self.process.wait(timeout=30)
        self.output.close()
        self.errors.close()
        records = analyze.jsonl(self.out / 'observer-stream.jsonl')
        if self.process.returncode or not records or records[-1].get('event') != 'OBSERVER_FINAL':
            raise ValueError('GPU observer did not finish successfully')
        samples = [r for r in records if 'monotonic_ns' in r]
        save(self.out / 'observer-ready.json', records[0])
        save(self.out / 'observer-final.json', records[-1])
        (self.out / 'gpu-metrics.jsonl').write_text(''.join(json.dumps(s) + '\n' for s in samples))
        (self.out / 'gpu-raw.csv').write_text(''.join(s['raw'] + '\n' for s in samples))


def terminated(kube, pod):
    current = kube.get('pod', pod['metadata']['name'], '-n', NAMESPACE)
    if current['metadata']['uid'] != pod['metadata']['uid']:
        raise ValueError('worker UID changed')
    statuses = current['status'].get('containerStatuses', [])
    if len(statuses) != 1 or 'terminated' not in statuses[0].get('state', {}):
        return None
    if statuses[0]['state']['terminated']['exitCode'] != 0 or statuses[0]['restartCount'] != 0:
        raise ValueError('worker exit was not successful')
    return current


def execute_clients(kube, driver_pod, run, cell, frozen, jobs, out, gpu_target):
    out.mkdir(parents=True, exist_ok=True)
    save(out / 'attempted.json', {'utc': utc(), 'cell': cell})
    observer = Observer(kube, driver_pod, run, out)
    pods = []
    try:
        observer_ready = wait_for(observer.ready, 30, 'physical GPU observer')
        for job in jobs:
            save(out / (job['metadata']['name'] + '-input.json'), job)
            kube.apply(job)
        for job in jobs:
            pod = wait_for(lambda: pod_for(kube, job, run), 300, 'one running Job Pod')
            cfg = json.loads(job['spec']['template']['spec']['containers'][0]['args'][1])
            pods.append((cfg, pod))
        save(out / 'pods-created.json', [pod for cfg, pod in pods])
        ready = wait_for(lambda: barrier(kube, pods, 'ready.json'), 300, 'all READY')
        clock = json.loads(host_call(kube, driver_pod, 'clock', run))
        for cfg, pod in pods:
            value = ready[cfg['task_id']]['ready.json']
            if value['config'] != cfg or value['config_sha256'] != analyze.digest(cfg) or value['clock_domain'] != clock['clock_domain'] or value['gpu_uuid'] != frozen['gpu_uuid'] or pod['spec']['nodeName'] != run['options']['node']:
                raise ValueError('READY input/clock/GPU/Pod identity mismatch')
            if not pod['spec']['containers'][0]['image'].endswith('@' + IMAGE_DIGEST):
                raise ValueError('workload image digest changed')
            if cell['mode'] == 'mps' and not value['env'].get('CUDA_MPS_PIPE_DIRECTORY'):
                raise ValueError('MPS client injection missing')
            if cell['mode'] == 'hami' and (value['env'].get('CUDA_DEVICE_SM_LIMIT_0') != str(100 // cell['concurrency']) or
                    value['env'].get('CUDA_DEVICE_MEMORY_LIMIT_0') != '8192m' or not value.get('cache_inode') or
                    value.get('library_sha256') != HAMI_LIBRARY_SHA256 or not value.get('library_map_lines')):
                raise ValueError('HAMi memory/core policy/cache/library differs from the original run')
        if observer_ready['clock_domain'] != clock['clock_domain']:
            raise ValueError('observer/controller clock mismatch')
        if cell['mode'] == 'mps':
            workers = [{'task_id': cfg['task_id'], 'pid': ready[cfg['task_id']]['ready.json']['pid'],
                        'uid': ready[cfg['task_id']]['ready.json']['uid'], 'pod_name': pod['metadata']['name'],
                        'pod_uid': pod['metadata']['uid'], 'run_id': run['nonce']} for cfg, pod in pods]
            mps_runtime(kube, driver_pod, run, workers, out)
        if cell['mode'] == 'hami' and len({ready[cfg['task_id']]['ready.json']['cache_inode'] for cfg, pod in pods}) != len(pods):
            raise ValueError('HAMi Tasks share a cache; independent claims required')
        claims = kube.get('resourceclaims.resource.k8s.io', '-n', NAMESPACE, '-l', selector(run))['items']
        if len(claims) != (len(pods) if cell['mode'] == 'hami' else 1):
            raise ValueError('claim sharing pattern differs')
        for claim in claims:
            allocated = claim.get('status', {}).get('allocation', {}).get('devices', {}).get('results', [])
            if len(allocated) != 1 or any(allocated[0].get(k) != v for k, v in gpu_target.items()):
                raise ValueError('claim allocation differs from target')
        save(out / 'claims-during.json', claims)
        save(out / 'identity.json', {'clock_domain': clock['clock_domain'], 'ready':
                                   {cfg['task_id']: ready[cfg['task_id']]['ready.json'] for cfg, pod in pods}})
        fresh_clock = json.loads(host_call(kube, driver_pod, 'clock', run))
        if fresh_clock['clock_domain'] != clock['clock_domain']:
            raise ValueError('controller clock changed before release')
        release = {'run_id': run['nonce'], 'seq': 1, 'operation': 'start',
                   'clock_domain': clock['clock_domain'], 'release_ns': fresh_clock['monotonic_ns'] + 3_000_000_000}
        for cfg, pod in pods:
            worker_send(kube, pod['metadata']['name'], release)
        armed = wait_for(lambda: barrier(kube, pods, 'armed.json'), 30, 'all armed')
        if any(armed[cfg['task_id']]['armed.json']['armed_ns'] >= release['release_ns'] for cfg, pod in pods):
            raise ValueError('arming missed the common release')
        ends = wait_for(lambda: barrier(kube, pods, 'end.json'), 900, 'all END')
        tasks = [ends[cfg['task_id']]['end.json'] for cfg, pod in pods]
        if any(t['completed_gemms'] != cfg['iterations'] or t['release_ns'] != release['release_ns'] or
               t['clock_domain'] != release['clock_domain'] or not 0 <= t['start_ns'] - release['release_ns'] <= 20_000_000
               for (cfg, pod), t in zip(pods, tasks)):
            raise ValueError('END work/start/clock mismatch')
        if max(t['start_ns'] for t in tasks) - min(t['start_ns'] for t in tasks) > 20_000_000:
            raise ValueError('start skew exceeds 20 ms')
        # Stop after END, retaining a sample on each side of the measured window.
        observer.stop()
        for cfg, pod in pods:
            worker_send(kube, pod['metadata']['name'], dict(release, seq=2, operation='verify'))
        verified = wait_for(lambda: barrier(kube, pods, 'verified.json'), 120, 'numeric validation')
        if any(not verified[cfg['task_id']]['verified.json']['correct'] for cfg, pod in pods):
            raise ValueError('numeric validation failed')
        save(out / 'control-final.json', verified)
        for cfg, pod in pods:
            worker_send(kube, pod['metadata']['name'], dict(release, seq=3, operation='stop'))
        final = [wait_for(lambda pod=pod: terminated(kube, pod), 120, 'container exit') for cfg, pod in pods]
        save(out / 'terminated-pods.json', final)
        for cfg, pod in pods:
            (out / f"task-{cfg['task_id']}.log").write_text(kube.call('logs', '-n', NAMESPACE, pod['metadata']['name'], '-c', 'workload'))
        makespan = (max(t['end_ns'] for t in tasks) - release['release_ns']) / 1e9
        save(out / 'trial.json', {'release': release, 'makespan_seconds': makespan,
                                 'valid': True, 'cleanup': {'verified': False}})
        return final
    finally:
        if observer.process.poll() is None:
            observer.stop()


def delete_owned(kube, obj, run):
    owned(obj, run)
    kind, name = obj['kind'], obj['metadata']['name']
    current = kube.get(kind, name, '-n', NAMESPACE, '--ignore-not-found')
    if current is None:
        return
    owned(current, run)
    if current['metadata']['uid'] != obj['metadata']['uid'] or current.get('spec') != obj.get('spec'):
        raise ValueError('object UID/spec changed before cleanup: ' + name)
    # API delete preconditions close the replacement race; kubectl raw performs
    # the DELETE and never removes a replacement object at the same name.
    plurals = {'Job': ('apis/batch/v1', 'jobs'), 'Pod': ('api/v1', 'pods'),
               'ResourceClaim': ('apis/resource.k8s.io/v1', 'resourceclaims'),
               'ResourceClaimTemplate': ('apis/resource.k8s.io/v1', 'resourceclaimtemplates'),
               'ConfigMap': ('api/v1', 'configmaps')}
    prefix, plural = plurals[kind]
    body = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'propagationPolicy': 'Foreground',
            'preconditions': {'uid': current['metadata']['uid']}}
    kube.call('delete', '--raw', f'/{prefix}/namespaces/{NAMESPACE}/{plural}/{name}', '-f', '-', stdin=json.dumps(body))


def cleanup(kube, run, *, abort=False):
    objects = kube.get('jobs,pods,resourceclaims.resource.k8s.io,resourceclaimtemplates.resource.k8s.io,configmaps',
                       '-n', NAMESPACE, '-l', selector(run))['items']
    pods = [obj for obj in objects if obj['kind'] == 'Pod']
    for pod in pods:
        statuses = pod.get('status', {}).get('containerStatuses', [])
        started = any(c.get('containerID') or c.get('state', {}).get('running') or c.get('state', {}).get('terminated') for c in statuses)
        if not started:
            continue
        if any('running' in c.get('state', {}) for c in statuses):
            if not abort:
                raise ValueError('live worker; collect before explicit cleanup')
            state = worker_read(kube, pod['metadata']['name'])
            command = {'run_id': run['nonce'], 'seq': state.get('command.json', {}).get('seq', 0) + 1,
                       'operation': 'stop', 'abort': True}
            worker_send(kube, pod['metadata']['name'], command)
            def exited():
                current = kube.get('pod', pod['metadata']['name'], '-n', NAMESPACE)
                states = current.get('status', {}).get('containerStatuses', [])
                return current if states and all('terminated' in c.get('state', {}) for c in states) else None
            wait_for(exited, 1000, 'aborted worker exit')
        # Init containers have no CUDA context, but must also have exited.
        current = kube.get('pod', pod['metadata']['name'], '-n', NAMESPACE)
        if any('running' in c.get('state', {}) for c in current.get('status', {}).get('initContainerStatuses', [])):
            raise ValueError('init container still running')
    for kind in ('Job', 'Pod', 'ResourceClaim', 'ResourceClaimTemplate', 'ConfigMap'):
        for obj in objects:
            if obj['kind'] == kind:
                delete_owned(kube, obj, run)
    wait_for(lambda: not kube.get('jobs,pods,resourceclaims.resource.k8s.io,resourceclaimtemplates.resource.k8s.io,configmaps',
                                 '-n', NAMESPACE, '-l', selector(run))['items'], 120, 'owned API resources absent')


def cleanup_namespace(kube):
    namespace = kube.get('namespace', NAMESPACE, '--ignore-not-found')
    if namespace is None:
        return
    if namespace['metadata'].get('labels', {}).get(PART) != 'dra-book':
        raise ValueError('refusing Namespace deletion without dra-book label')
    kinds = kube.call('api-resources', '--namespaced=true', '--verbs=list', '-o', 'name').splitlines()
    for kind in sorted(set(kinds) - {'events', 'events.events.k8s.io'}):
        for obj in kube.get(kind, '-n', NAMESPACE)['items']:
            name = obj['metadata']['name']
            default = (kind == 'serviceaccounts' and name == 'default') or (kind == 'configmaps' and name == 'kube-root-ca.crt')
            if not default:
                raise ValueError('refusing namespace deletion: resource remains ' + kind + '/' + name)
    current = kube.get('namespace', NAMESPACE, '--ignore-not-found')
    if current is None:
        return
    if current['metadata']['uid'] != namespace['metadata']['uid'] or current['metadata'].get('labels', {}).get(PART) != 'dra-book':
        raise ValueError('Namespace UID/label changed before cleanup')
    body = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': current['metadata']['uid']}}
    kube.call('delete', '--raw', '/api/v1/namespaces/' + NAMESPACE, '-f', '-', stdin=json.dumps(body))
    wait_for(lambda: kube.get('namespace', NAMESPACE, '--ignore-not-found') is None, 300, 'Namespace removed')


def collect(kube, run):
    folder = kube.out / 'collections' / dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    objects = kube.get('jobs,pods,resourceclaims.resource.k8s.io,resourceclaimtemplates.resource.k8s.io,configmaps',
                       '-n', NAMESPACE, '-l', selector(run))
    save(folder / 'objects.json', objects)
    for pod in objects['items']:
        if pod['kind'] == 'Pod' and pod.get('status', {}).get('containerStatuses'):
            try:
                log = kube.call('logs', '-n', NAMESPACE, pod['metadata']['name'], '-c', 'workload')
                (folder / (pod['metadata']['name'] + '.log')).write_text(log)
            except RuntimeError as error:
                (folder / (pod['metadata']['name'] + '.log-error.txt')).write_text(str(error))
    save(folder / 'events.json', kube.get('events', '-n', NAMESPACE))
    return folder


def check_namespace(kube):
    namespace = kube.get('namespace', NAMESPACE, '--ignore-not-found')
    if namespace and namespace['metadata'].get('labels', {}).get(PART) != 'dra-book':
        raise ValueError('refusing to adopt a foreign Namespace')
    if namespace:
        objects = kube.get('jobs,pods,resourceclaims.resource.k8s.io,resourceclaimtemplates.resource.k8s.io', '-n', NAMESPACE)['items']
        if objects:
            raise ValueError('previous workload/Claim remains; collect and clean up first')


def run_matrix(kube, run, frozen):
    pod = driver(kube, run['options']['node'])
    check_namespace(kube)
    source = (LAB / 'workload/worker.py').read_bytes()
    save(kube.out / 'versions.json', json.loads(kube.call('version', '-o', 'json')))
    for cell in matrix(run['options']):
        out = kube.out / 'batches' / cell['trial_id']
        if (out / 'complete.json').exists():
            continue
        if (out / 'attempted.json').exists() or any(out.glob('serial-task-*/attempted.json')):
            raise ValueError('an attempted batch is incomplete; preserve evidence and choose a new run-id')
        gpu_target = target(kube, run['options'], cell['mode'])
        if gpu_target is None:
            save(kube.out / 'provider-required.json', {'next_cell': cell, 'required_driver':
                 'hami-core-gpu.project-hami.io' if cell['mode'] == 'hami' else 'gpu.nvidia.com',
                 'utc': utc(), 'no_active_batch': True})
            print('Provider boundary saved. Use the separate provider install/restore scripts, then rerun the same run-id.', file=sys.stderr)
            return 78
        if cell['mode'] != 'hami':
            native_features(kube, run['options'])
        out.mkdir(parents=True, exist_ok=True)
        print(utc(), cell['trial_id'], 'start', flush=True)
        idle(kube, pod, run, gpu_target, out / 'idle-before.json')
        objects, jobs = prepare(rendered(kube, cell['mode']), cell, frozen, run)
        configmap = next(obj for obj in objects if obj['kind'] == 'ConfigMap')
        if configmap['data']['worker.py'].encode() != source:
            raise ValueError('rendered worker differs from pinned source')
        save(out / 'cell.json', cell)
        save(out / 'manifests.json', {'apiVersion': 'v1', 'kind': 'List', 'items': objects + jobs})
        for obj in objects:
            kube.apply(obj)
        if cell['mode'] == 'sequential':
            for i, job in enumerate(jobs):
                child = out / f'serial-task-{i}'
                idle(kube, pod, run, gpu_target, out / f'idle-serial-{i}.json')
                execute_clients(kube, pod, run, cell, frozen, [job], child, gpu_target)
                live_job = kube.get('job', job['metadata']['name'], '-n', NAMESPACE)
                delete_owned(kube, live_job, run)
                wait_for(lambda: not kube.get('pods', '-n', NAMESPACE, '-l', selector(run) + ',job-name=' + job['metadata']['name'])['items'], 120, 'sequential Pod removed')
                wait_for(lambda: idle_if_ready(kube, pod, run, gpu_target, out / f'idle-serial-after-{i}.json'), 120, 'GPU idle between sequential Tasks')
                child_trial = analyze.read_json(child / 'trial.json')
                child_trial['cleanup']['verified'] = True
                save(child / 'trial.json', child_trial)
        else:
            execute_clients(kube, pod, run, cell, frozen, jobs, out, gpu_target)
        cleanup(kube, run)
        wait_for(lambda: idle_if_ready(kube, pod, run, gpu_target, out / 'idle-after.json'), 120, 'GPU idle after unprepare')
        if cell['mode'] != 'sequential':
            trial = analyze.read_json(out / 'trial.json')
            trial['cleanup']['verified'] = True
            save(out / 'trial.json', trial)
        row = analyze.batch(out, frozen)
        save(out / 'complete.json', row)
        print(utc(), cell['trial_id'], 'complete', flush=True)
    rows = [analyze.read_json(kube.out / 'batches' / c['trial_id'] / 'complete.json') for c in matrix(run['options'])]
    analyze.pair_baselines(rows)
    output = kube.out / 'analysis'
    output.mkdir(exist_ok=True)
    analyze.write_csv(output / 'summary.csv', rows)
    (output / 'tables.md').write_text(analyze.table_text(rows))
    save(output / 'aggregated.json', analyze.aggregate(rows))
    save(output / 'paired-ratios.json', analyze.paired_ratios(rows))
    save(kube.out / 'completed.json', {'utc': utc(), 'batches': len(rows)})
    (kube.out / 'provider-required.json').unlink(missing_ok=True)
    return 0


def idle_if_ready(kube, pod, run, gpu_target, out):
    try:
        idle(kube, pod, run, gpu_target, out)
        return True
    except ValueError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'collect', 'cleanup'))
    parser.add_argument('run_id')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', args.run_id) or args.run_id in ('.', '..'):
        parser.error('invalid run id')
    out = LAB / 'out' / args.run_id
    if args.action == 'run':
        out.mkdir(parents=True, exist_ok=True)
    elif not (out / 'run.json').is_file():
        parser.error('run.json required; no resources are adopted for cleanup')
    kube = Kube(out)
    run = None
    try:
        if args.action == 'run':
            options = selection(os.environ)
            if (out / 'run.json').exists():
                run = analyze.read_json(out / 'run.json')
                if run['options'] != options:
                    raise ValueError('run-id parameters changed')
                if (out / 'completed.json').exists():
                    print('Already complete: ' + str(out / 'analysis/tables.md'))
                    return 0
            else:
                run = {'run_id': args.run_id, 'nonce': uuid.uuid4().hex, 'options': options, 'created_utc': utc()}
                save(out / 'run.json', run)
            frozen = frozen_config(options)
            save(out / 'benchmark-config.json', frozen)
            save(out / 'matrix.json', matrix(options))
            return run_matrix(kube, run, frozen)
        run = analyze.read_json(out / 'run.json')
        if args.action == 'collect':
            print(collect(kube, run))
        else:
            if kube.get('namespace', NAMESPACE, '--ignore-not-found') is None:
                return 0
            collect(kube, run)
            cleanup(kube, run, abort=True)
            pod = driver(kube, run['options']['node'])
            required = target(kube, run['options'], 'hami' if 'hami' in run['options']['modes'] and run['options']['modes'] == ['hami'] else 'exclusive')
            if required is None:
                required = target(kube, run['options'], 'hami')
            wait_for(lambda: idle_if_ready(kube, pod, run, required, out / 'cleanup-idle.json'), 120, 'GPU idle')
            cleanup_namespace(kube)
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        save(out / 'run-error.json', {'utc': utc(), 'error': str(error)})
        print('Run failed: ' + str(error) + '\nEvidence retained at ' + str(out), file=sys.stderr)
        if run:
            try:
                collect(kube, run)
            except Exception:
                pass
        return 1


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt('received signal ' + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(main())
