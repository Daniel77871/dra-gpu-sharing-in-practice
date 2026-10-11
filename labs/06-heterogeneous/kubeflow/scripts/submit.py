#!/usr/bin/env python3
"""Submit committed KFP IR with curl and collect Pod/Claim lifecycle evidence."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import urlencode

from kfp_state import (ROOT, RUN_LABEL, PART_OF, create_owned, get, kc, require_owned,
                       save, state_load, utc)

CLAIMS = {'h1': 'gpu-memory-small', 'h2': 'gpu-memory-large', 'l1': 'gpu-controlled-failure'}
CLAIM_QUIET_SECONDS = 5  # Original E4B runner.py waits five seconds before its final empty-claim check.


def rest(base, path, output, method='GET', body=None, upload=None):
    args = ['curl', '--fail-with-body', '--silent', '--show-error', '--max-time', '30',
            '--request', method, base.rstrip('/') + path]
    if upload:
        args += ['--form', 'uploadfile=@' + str(upload)]
    if body is not None:
        args += ['--header', 'Content-Type: application/json', '--data-binary', '@-']
    result = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                            text=True, capture_output=True, check=False)
    output.write_text(result.stdout)
    if result.returncode:
        raise RuntimeError('KFP REST request failed: ' + path + ' (' + result.stderr.strip() + ')')
    return json.loads(result.stdout) if result.stdout.strip() else {}


class Watch:
    def __init__(self, resource, namespace, folder, on_object=None):
        self.resource, self.error = resource, None
        self.on_object, self.lock, self.stopping = on_object, threading.Lock(), threading.Event()
        initial = get(resource, namespace=namespace)
        save(folder / (resource + '-before.json'), initial)
        self.seen = {o['metadata']['uid']: o for o in initial['items']}
        self.events = []
        self.path = folder / ('watch-' + resource + '.jsonl')
        prefix = '/apis/resource.k8s.io/v1' if resource == 'resourceclaims' else '/api/v1'
        uri = prefix + '/namespaces/' + namespace + '/' + resource + '?' + urlencode({
            'watch': 'true', 'resourceVersion': initial['metadata']['resourceVersion'], 'allowWatchBookmarks': 'true'})
        self.stderr = (folder / ('watch-' + resource + '.stderr')).open('w')
        self.process = subprocess.Popen(['kubectl', '--request-timeout=0', 'get', '--raw', uri],
                                        stdout=subprocess.PIPE, stderr=self.stderr, text=True)
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        try:
            with self.path.open('a') as stream:
                for line in self.process.stdout:
                    event = json.loads(line)
                    if event.get('type') == 'ERROR':
                        self.error = event
                    obj = event.get('object', {})
                    if event.get('type') != 'BOOKMARK' and obj.get('metadata', {}).get('uid'):
                        with self.lock:
                            self.seen[obj['metadata']['uid']] = obj
                            self.events.append(event)
                        if self.on_object:
                            self.on_object(obj)
                    stream.write(json.dumps({'observed_at': utc(), **event}) + '\n')
                    stream.flush()
        except Exception as error:
            self.error = 'Watch reader failed: ' + str(error)
        finally:
            if not self.stopping.is_set() and self.error is None:
                self.error = 'Unexpected watch EOF: ' + self.resource

    def snapshot(self):
        with self.lock:
            return list(self.seen.values()), list(self.events)

    def healthy(self):
        if self.error or (not self.stopping.is_set() and self.process.poll() is not None):
            raise RuntimeError('Kubernetes watch failed; lifecycle evidence is incomplete: ' + str(self.error or self.resource))

    def close(self):
        self.stopping.set()
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait()
        self.thread.join(timeout=5)
        self.stderr.close()


def workload_records(text):
    records = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get('event'):
            records.append(value)
    return records


class LogCapture:
    """Open a log stream as soon as the Pod watch sees main running."""
    def __init__(self, namespace, folder, task_id):
        self.namespace, self.folder, self.task_id = namespace, folder, task_id
        self.streams, self.seen, self.lock = {}, {}, threading.Lock()

    def observe(self, pod):
        if (not pod.get('spec', {}).get('resourceClaims')
                or pod['metadata'].get('labels', {}).get('task-id') != self.task_id):
            return
        uid = pod['metadata']['uid']
        with self.lock:
            self.seen[uid] = pod
            main = next((c for c in pod.get('status', {}).get('containerStatuses', []) if c['name'] == 'main'), None)
            if not main or not any(key in main.get('state', {}) for key in ('running', 'terminated')):
                return
            if uid in self.streams:
                process, output, error = self.streams[uid]
                if process.poll() in (None, 0):
                    return
                output.close(); error.close()
            output = (self.folder / ('pod-' + uid + '.log')).open('w')
            error = (self.folder / ('pod-' + uid + '.stderr')).open('a')
            process = subprocess.Popen(['kubectl', 'logs', pod['metadata']['name'], '-n', self.namespace,
                                        '-c', 'main', '--follow', '--request-timeout=0'], stdout=output, stderr=error)
            self.streams[uid] = (process, output, error)

    def pods(self):
        with self.lock:
            return list(self.seen.values())

    def records(self):
        records = []
        for path in self.folder.glob('pod-*.log'):
            uid = path.name[len('pod-'):-len('.log')]
            records.extend({**record, 'log_pod_uid': uid} for record in workload_records(path.read_text()))
        return records

    def close(self):
        # Watches have stopped before this call, so no new stream can appear.
        for process, output, error in self.streams.values():
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
            output.close(); error.close()


def prerequisites(state, case):
    ns = get('namespace', state['namespace'])
    record = next(o for o in state['created'] if o['kind'] == 'Namespace')
    if ns is None:
        raise RuntimeError('KFP Namespace does not exist; install the backend first')
    require_owned(ns, record, state['run_id'])
    providers = set()
    memory = {}
    for obj in get('resourceslices')['items']:
        if obj['spec']['driver'] != 'hami-core-gpu.project-hami.io':
            continue
        node = obj['spec'].get('nodeName')
        for device in obj['spec'].get('devices', []):
            if node in ('dra-a40', 'dra-4090') and device.get('allowMultipleAllocations') and 'memory' in device.get('capacity', {}):
                providers.add(node)
                memory[node] = device['capacity']['memory']['value']
    if providers != {'dra-a40', 'dra-4090'}:
        raise RuntimeError('HAMi-DRA consumable capacity must be published on both GPU nodes')
    for claim in kc('get', 'resourceclaims', '-A', '-o', 'json')['items']:
        results = (claim.get('status', {}).get('allocation') or {}).get('devices', {}).get('results', [])
        if any(result.get('pool') in providers for result in results):
            raise RuntimeError('A GPU Claim is already allocated; release it before this isolated trial')
    device_class = get('deviceclass', state['device_class'])
    record = next(o for o in state['created'] if o['kind'] == 'DeviceClass')
    if device_class is None:
        raise RuntimeError('Installed DeviceClass is absent')
    require_owned(device_class, record, state['run_id'])
    return memory


def prepare_case(state_path, state, case, folder, task_id):
    namespace = state['namespace']
    # This is the same stable-name ConfigMap described by configMapGenerator.
    source = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'gpu-code', 'namespace': namespace,
              'labels': {'app.kubernetes.io/component': 'gpu-workload'}}, 'immutable': True,
              'data': {name: (ROOT / 'workload' / name).read_text() for name in ('gpu_workload.py', 'gpu_primitives.py')}}
    create_owned(source, state, state_path)
    templates = json.loads((ROOT / 'manifests/templates.json').read_text())['items']
    template = next(o for o in templates if o['metadata']['name'] == CLAIMS[case])
    template['metadata']['namespace'] = namespace
    request = template['spec']['spec']['devices']['requests'][0]['exactly']
    request['deviceClassName'] = state['device_class']
    if case == 'l1' and os.environ.get('A40_GPU_UUID'):
        request['selectors'][0]['cel']['expression'] = 'device.attributes["hami-core-gpu.project-hami.io"].uuid == ' + json.dumps(os.environ['A40_GPU_UUID'])
    create_owned(template, state, state_path)
    save(folder / 'resourceclaimtemplate-input.json', template)
    value = json.dumps({'run_id': state['run_id'], 'release': {}})
    control = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'gpu-control', 'namespace': namespace,
               'labels': {'app.kubernetes.io/component': 'workload-control'}}, 'data': {'control.json': value}}
    existing = get('configmap', 'gpu-control', namespace)
    if existing is None:
        create_owned(control, state, state_path)
    else:
        record = next(o for o in state['created'] if o['kind'] == 'ConfigMap' and o['metadata']['name'] == 'gpu-control')
        require_owned(existing, record, state['run_id'])
        patch = [{'op': 'test', 'path': '/metadata/uid', 'value': record['metadata']['uid']},
                 {'op': 'replace', 'path': '/data/control.json', 'value': value}]
        kc('patch', 'configmap', 'gpu-control', '-n', namespace, '--type=json', '-p', json.dumps(patch), '-o', 'json')
    # A stale projected control file cannot release a different trial's task key.
    ir = (ROOT / 'pipelines' / (case + '.yaml')).read_text().replace('dra-book-kubeflow\n', state['run_id'] + '\n')
    if ir.count('constant: target\n') != 1 or ir.count('task-id: target\n') != 1:
        raise RuntimeError('Committed IR task identity differs from the SDK source')
    ir = ir.replace('constant: target\n', 'constant: ' + task_id + '\n').replace('task-id: target\n', 'task-id: ' + task_id + '\n')
    path = folder / 'pipeline.yaml'
    path.write_text(ir)
    return path


def release(state, state_path, pods, task_id):
    namespace = state['namespace']
    control = get('configmap', 'gpu-control', namespace)
    record = next(o for o in state['created'] if o['kind'] == 'ConfigMap' and o['metadata']['name'] == 'gpu-control')
    require_owned(control, record, state['run_id'])
    patch = [{'op': 'test', 'path': '/metadata/uid', 'value': record['metadata']['uid']},
             {'op': 'replace', 'path': '/data/control.json', 'value': json.dumps({
                 'run_id': state['run_id'], 'release': {task_id: True}, 'release_id': 'cuda-result-recorded',
                 'issued_at_ns': time.time_ns()})}]
    kc('patch', 'configmap', 'gpu-control', '-n', namespace, '--type=json', '-p', json.dumps(patch), '-o', 'json')
    for pod in pods:
        if pod['spec'].get('resourceClaims'):
            # Changing an owned annotation asks kubelet to refresh the projected ConfigMap.
            patch = [{'op': 'test', 'path': '/metadata/uid', 'value': pod['metadata']['uid']},
                     {'op': 'add', 'path': '/metadata/annotations/dra-book~1control-version', 'value': str(time.time_ns())}]
            kc('patch', 'pod', pod['metadata']['name'], '-n', namespace, '--type=json', '-p', json.dumps(patch), '-o', 'json')


def placement_rows(pods, claims):
    out = []
    for claim in claims:
        results = (claim.get('status', {}).get('allocation') or {}).get('devices', {}).get('results', [])
        if not results:
            continue
        owners = {r['uid'] for r in claim['metadata'].get('ownerReferences', [])
                  if r['kind'] == 'Pod' and r.get('controller') is True}
        pod = next((p for p in pods if p['metadata']['uid'] in owners), None)
        if pod is None:
            continue
        for result in results:
            out.append({'job': 'gpu-task', 'pod': pod['metadata']['name'], 'pod_uid': pod['metadata']['uid'],
                        'node': pod['spec'].get('nodeName') or result['pool'], 'claim': claim['metadata']['name'],
                        'claim_uid': claim['metadata']['uid'], 'driver': result['driver'], 'pool': result['pool'],
                        'device': result['device']})
    return out


def generated_claim_uids(claim_events, pod_uids):
    return {event['object']['metadata']['uid'] for event in claim_events
            if event.get('type') in ('ADDED', 'MODIFIED', 'DELETED') and any(
                owner.get('kind') == 'Pod' and owner.get('controller') is True and owner.get('uid') in pod_uids
                for owner in event.get('object', {}).get('metadata', {}).get('ownerReferences', []))}


def natural_release(placements, claim_events, remaining, pod_uids=None):
    allocated = {row['claim_uid'] for row in placements}
    pod_uids = pod_uids if pod_uids is not None else {row['pod_uid'] for row in placements}
    generated = generated_claim_uids(claim_events, pod_uids) | allocated
    deleted = {event['object']['metadata']['uid'] for event in claim_events if event.get('type') == 'DELETED'}
    return bool(allocated) and not remaining['items'] and generated <= deleted


def cuda_identity_verified(placements, logs, slices, run_id, task_id, require_hold=False, require_exit=None):
    """Bind CUDA evidence to its log source, Pod-owned Claim and published device."""
    if not placements:
        return False
    for row in placements:
        devices = [device for sl in slices['items']
                   if sl['spec']['driver'] == row['driver'] and sl['spec']['pool']['name'] == row['pool']
                   and sl['spec'].get('nodeName') == row['node']
                   for device in sl['spec']['devices'] if device['name'] == row['device']]
        if row['driver'] != 'hami-core-gpu.project-hami.io' or len(devices) != 1:
            return False
        uuid = devices[0].get('attributes', {}).get('uuid', {}).get('string')
        matched = [event for event in logs if uuid and event.get('gpu_uuid') == uuid
                   and event.get('pod_uid') == row['pod_uid'] and event.get('log_pod_uid') == row['pod_uid']
                   and event.get('run_id') == run_id and event.get('task_id') == task_id]
        if not any(event['event'] == 'READY' for event in matched):
            return False
        if not any(event['event'] == 'CORRECT' and event.get('correct') is True for event in matched):
            return False
        if require_hold and not any(event['event'] == 'HOLD' for event in matched):
            return False
        if require_exit is not None and not any(event['event'] == 'WORKLOAD_EXIT'
                and event.get('intended_workload_exit_code') == require_exit for event in matched):
            return False
        if require_exit == 42 and not any(event['event'] == 'CONTROLLED_FAILURE'
                and event.get('intended_workload_exit_code') == 42 for event in matched):
            return False
    return True


def submit(case, round_number, state_dir, base, folder):
    if round_number < 1:
        raise RuntimeError('Round must be positive')
    state_path, state = state_load(state_dir)
    folder = Path(folder).resolve()
    if (folder / 'trial.json').exists():
        # Idempotency never launches a duplicate Run or overwrites trial evidence.
        trial = json.loads((folder / 'trial.json').read_text())
        if trial.get('verdict') == 'PASS':
            print('Recorded trial already passed: ' + str(folder)); return
        raise RuntimeError('Existing incomplete trial; inspect it and choose a new KFP_RUN_ID')
    folder.mkdir(parents=True, exist_ok=True)
    memory = prerequisites(state, case)
    save(folder / 'published-memory.json', memory)
    name = case + '-r' + str(round_number)
    task_id = name + '-' + os.urandom(6).hex()
    path = prepare_case(state_path, state, case, folder, task_id)
    capture = LogCapture(state['namespace'], folder, task_id)
    watches = []
    trial = {'case': case, 'part': 'kubeflow', 'round': round_number, 'verdict': 'FAIL',
             'placements': [], 'claims_deleted': False, 'pending_seconds': None,
             'same_pod_uid': None, 'same_claim_uid': None, 'run_state': None,
             'namespace': state['namespace'], 'task_id': task_id, 'started_at': utc()}
    save(folder / 'trial.json', trial)
    try:
        watches.append(Watch('pods', state['namespace'], folder, on_object=capture.observe))
        watches.append(Watch('resourceclaims', state['namespace'], folder))
        if not state.get('experiment_id'):
            experiment = rest(base, '/apis/v2beta1/experiments', folder / 'experiment-created.json', method='POST', body={
                'display_name': 'dra-book-' + state['run_id'], 'namespace': state['namespace']})
            state['experiment_id'] = experiment['experiment_id']
            save(state_path, state)
        upload = rest(base, '/apis/v2beta1/pipelines/upload?' + urlencode({
            'name': 'dra-book-' + name, 'namespace': state['namespace']}), folder / 'pipeline-upload.json', method='POST', upload=path)
        pipeline_id = upload.get('pipeline_id') or upload.get('id')
        if not pipeline_id:
            raise RuntimeError('KFP upload response has no pipeline ID')
        response = rest(base, '/apis/v2beta1/runs', folder / 'pipeline-created.json', method='POST', body={
            'display_name': 'dra-book-' + name, 'experiment_id': state['experiment_id'],
            'pipeline_version_reference': {'pipeline_id': pipeline_id},
            'runtime_config': {'parameters': {}, 'pipeline_root': 'minio://mlpipeline/v2/artifacts'}, 'service_account': 'pipeline-runner'})
        run_id = response['run_id']
        state.setdefault('kfp_runs', []).append(run_id)
        save(state_path, state)
        trial['kfp_run_id'] = run_id
        save(folder / 'trial.json', trial)
        seen_pods, seen_claims, released = {}, {}, False
        deadline = time.monotonic() + float(os.environ.get('KFP_TIMEOUT', '900'))
        terminal_at = None
        empty_since, last_claim_count = None, None
        while time.monotonic() < deadline:
            for watch in watches:
                watch.healthy()
            pods = get('pods', namespace=state['namespace'])
            claims = get('resourceclaims', namespace=state['namespace'])
            for obj in pods['items']:
                capture.observe(obj)
            watched_pods, _ = watches[0].snapshot()
            for obj in watched_pods:
                capture.observe(obj)
            for obj in capture.pods():
                seen_pods[obj['metadata']['uid']] = obj
            for obj in claims['items']:
                if obj.get('status', {}).get('allocation'):
                    seen_claims[obj['metadata']['uid']] = obj
            _, claim_events = watches[1].snapshot()
            for event in claim_events:
                obj = event.get('object', {})
                if obj.get('status', {}).get('allocation'):
                    seen_claims[obj['metadata']['uid']] = obj
            placements = placement_rows(list(seen_pods.values()), list(seen_claims.values()))
            save(folder / 'pods-after.json', pods)
            save(folder / 'resourceclaims-after.json', claims)
            slices = get('resourceslices')
            save(folder / 'resourceslices-after.json', slices)
            logs = capture.records()
            save(folder / 'workload-events.json', logs)
            workflows = get('workflows.argoproj.io', namespace=state['namespace'])['items']
            for workflow in workflows:
                if workflow['metadata'].get('labels', {}).get('pipeline/runid') == run_id:
                    if workflow['metadata'].get('labels', {}).get(RUN_LABEL) != state['run_id']:
                        kc('label', 'workflow', workflow['metadata']['name'], '-n', state['namespace'],
                           PART_OF + '=dra-book', 'app.kubernetes.io/component=kfp-run', RUN_LABEL + '=' + state['run_id'], '-o', 'json')
                    save(folder / 'workflow.json', workflow)
            run = rest(base, '/apis/v2beta1/runs/' + run_id + '?view=FULL', folder / 'pipeline-run.json')
            tasks = rest(base, '/apis/v2beta1/runs/' + run_id + '/tasks', folder / 'pipeline-tasks.json')
            trial['run_state'] = run.get('state')
            trial['task_states'] = [task.get('state') for task in tasks.get('tasks', [])]
            if case != 'l1' and not released and cuda_identity_verified(
                    placements, logs, slices, state['run_id'], task_id, require_hold=True):
                release(state, state_path, list(seen_pods.values()), task_id)
                released = True
                save(folder / 'release-issued.json', {'at': utc(), 'run_id': run_id})
            for watch in watches:
                watch.healthy()
            if trial['run_state'] in ('SUCCEEDED', 'FAILED', 'CANCELED', 'CANCELLED', 'SKIPPED'):
                terminal_at = terminal_at or utc()
                if natural_release(placements, claim_events, claims, set(seen_pods)):
                    if empty_since is None or last_claim_count != len(claim_events):
                        empty_since, last_claim_count = time.monotonic(), len(claim_events)
                    elif time.monotonic() - empty_since >= CLAIM_QUIET_SECONDS:
                        trial['claims_deleted'] = True
                        break
                else:
                    empty_since = None
            time.sleep(2)
        else:
            raise RuntimeError('Timed out waiting for Run terminal state and automatic Claim release')
        # Coverage ends only after every observed trial Claim UID has a DELETED event.
        for watch in watches:
            watch.healthy()
        for watch in watches:
            watch.close()
        capture.close()
        logs = capture.records()
        save(folder / 'workload-events.json', logs)
        for obj in capture.pods():
            seen_pods[obj['metadata']['uid']] = obj
        _, claim_events = watches[1].snapshot()
        trial['placements'] = placement_rows(list(seen_pods.values()), list(seen_claims.values()))
        trial['claims_deleted'] = natural_release(trial['placements'], claim_events, claims, set(seen_pods))
        trial['exit_codes'] = sorted(set(e.get('intended_workload_exit_code') for e in logs if e.get('intended_workload_exit_code') is not None))
        trial['main_exit_codes'] = [c['state']['terminated']['exitCode'] for p in seen_pods.values()
                                    for c in p.get('status', {}).get('containerStatuses', [])
                                    if c['name'] == 'main' and 'terminated' in c.get('state', {})]
        trial['attempt_count'] = len(seen_pods)
        trial['terminal_at'] = terminal_at
        expected_state = 'FAILED' if case == 'l1' else 'SUCCEEDED'
        valid = bool(len(trial['placements']) == 1 and trial['attempt_count'] == 1 and trial['claims_deleted']
                     and trial['run_state'] == expected_state and cuda_identity_verified(
                         trial['placements'], logs, slices, state['run_id'], task_id, require_exit=42 if case == 'l1' else 0))
        if case == 'h2':
            valid = valid and all(p['node'] == 'dra-a40' for p in trial['placements'])
        trial['verdict'] = 'PASS' if valid else 'FAIL'
        save(folder / 'placement.json', trial['placements'])
        allocated = {p['claim_uid'] for p in trial['placements']}
        generated = generated_claim_uids(claim_events, set(seen_pods)) | allocated
        save(folder / 'claim-lifecycle.json', {'created_claim_uids': sorted(generated),
                                              'allocated_claim_uids': sorted(allocated),
                                              'deleted_claim_uids': sorted({e['object']['metadata']['uid'] for e in claim_events
                                                  if e.get('type') == 'DELETED' and e['object']['metadata']['uid'] in generated}),
                                              'remaining_claims': claims, 'terminal_at': terminal_at,
                                              'verified': trial['claims_deleted']})
        if not valid:
            raise RuntimeError('Trial acceptance failed; inspect trial.json, Pod logs and KFP API responses')
        print(name + ': PASS')
    except Exception as error:
        trial['error'] = str(error)
        raise
    finally:
        for watch in watches:
            watch.close()
        capture.close()
        trial['finished_at'] = utc()
        save(folder / 'trial.json', trial)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('case', choices=CLAIMS)
    p.add_argument('--round', type=int, default=1)
    p.add_argument('--state-dir', required=True)
    p.add_argument('--api', default='http://127.0.0.1:8888')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    try:
        submit(a.case, a.round, a.state_dir, a.api, a.out)
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
