#!/usr/bin/env python3
"""Read-only MPS control and host/client identity checks (stdlib only)."""
import importlib.util
from pathlib import Path
import re
import shlex
import sys

_spec = importlib.util.spec_from_file_location('isolation_control', Path(__file__).with_name('control.py'))
_control = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_control)
GPU_UUID, events, load, save = _control.GPU_UUID, _control.events, _control.load, _control.save


def one_server(text):
    servers = text.split()
    if len(servers) != 1 or not servers[0].isdigit():
        raise ValueError('requires exactly one active MPS server')
    return int(servers[0])


def memory_mib(text):
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([GM]?)', text.strip())
    if not match:
        raise ValueError('unexpected MPS memory limit readback: ' + text)
    return float(match[1]) * (1024 if match[2] == 'G' else 1)


def client_pids(text):
    values = text.split()
    if not values or any(not value.isdigit() for value in values) or len(set(values)) != len(values):
        raise ValueError('unexpected MPS clients readback')
    return [int(value) for value in values]


def readback(raw, quota, wanted_clients):
    server = one_server(raw['get_server_list'])
    default = memory_mib(raw['get_default_device_pinned_mem_limit ' + GPU_UUID])
    memory = memory_mib(raw[f'get_device_pinned_mem_limit {server} {GPU_UUID}'])
    threads = float(raw[f'get_active_thread_percentage {server}'].strip())
    if default != quota * 1024 or memory != quota * 1024 or threads != 100 or float(raw['get_default_active_thread_percentage']) != 100:
        raise ValueError('effective MPS memory/thread limit differs from this round')
    clients = client_pids(raw[f'get_client_list {server}'])
    if len(clients) != wanted_clients:
        raise ValueError('effective MPS clients count differs from this round')
    return {'server_pid': server, 'server_memory_limit_mib': memory,
            'default_memory_limit_mib': default, 'active_thread_percentage': threads, 'clients': clients}


def map_client(host_pid, status, cgroup, command, pods, ready):
    fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
    namespace_pids = [int(pid) for pid in fields['NSpid'].split()]
    if namespace_pids[0] != host_pid:
        raise ValueError('MPS host PID differs from /proc status; check the reader PID namespace')
    uid, namespace_pid = int(fields['Uid'].split()[0]), namespace_pids[-1]
    # The scope parent uses Path(__file__).resolve() when starting children.
    # Kubernetes projects ConfigMap keys through a timestamped directory, so
    # these clients have the resolved path in /proc/<host-pid>/cmdline.
    arguments = command.split('\0') if '\0' in command else shlex.split(command)
    workload = any(re.fullmatch(
        r'/opt/lab/(?:\.\.\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}\.\d+/)?isolation_workload\.py',
        argument) for argument in arguments)
    matches = []
    for pod in pods:
        pod_uid = pod['metadata']['uid']
        container = next(c['containerID'].split('://', 1)[-1]
                         for c in pod['status']['containerStatuses'] if c['name'] == 'workload')
        if (pod_uid in cgroup or pod_uid.replace('-', '_') in cgroup) and container in cgroup:
            candidates = [row for row in ready if row.get('pid') == namespace_pid
                          and row.get('uid') == uid and row.get('gpu_uuid') == GPU_UUID
                          and row.get('pod_name') == pod['metadata']['name']]
            if len(candidates) == 1 and workload:
                matches.append({'host_pid': host_pid, 'namespace_pid': namespace_pid, 'uid': uid,
                                'pod': pod['metadata']['name'], 'pod_uid': pod_uid,
                                'container_id': container, 'client_id': candidates[0]['client_id']})
    if len(matches) != 1:
        raise ValueError('cannot map MPS host PID to exactly one experimental CUDA client')
    return matches[0]


def select_daemon(directory):
    directory = Path(directory)
    claims = load(directory / 'claims-allocated.json')['items']
    if len(claims) != 1:
        raise ValueError('MPS requires one shared Claim')
    uid = claims[0]['metadata']['uid']
    pods = load(directory / 'mps-daemons.json')['items']
    candidates = [pod for pod in pods if pod['kind'] == 'Pod'
                  and pod['metadata']['name'].startswith('mps-control-daemon-' + uid + '-')]
    if len(candidates) != 1 or candidates[0]['spec'].get('nodeName') != 'dra-a40':
        raise ValueError('requires exactly one Claim-specific MPS daemon on dra-a40')
    daemon = candidates[0]
    containers = [container for container in daemon['spec']['containers']
                  if any(env['name'] == 'CUDA_VISIBLE_DEVICES' and env.get('value') == GPU_UUID for env in container.get('env', []))]
    refs = [ref for ref in daemon['metadata']['ownerReferences'] if ref['kind'] == 'ReplicaSet' and ref.get('controller')]
    if len(containers) != 1 or len(refs) != 1:
        raise ValueError('MPS daemon GPU/ownership differs')
    save(directory / 'mps-daemon.json', daemon)
    save(directory / 'mps-selection.json', {'claim_uid': uid, 'daemon_name': daemon['metadata']['name'],
                                          'container': containers[0]['name'], 'replicaset': refs[0]['name']})
    return daemon['metadata']['name'], containers[0]['name'], refs[0]['name']


def verify_owner(directory):
    directory = Path(directory)
    daemon, rs, deploy = (load(directory / name) for name in ('mps-daemon.json', 'mps-replicaset.json', 'mps-deployment.json'))
    owner = next(ref for ref in daemon['metadata']['ownerReferences'] if ref['kind'] == 'ReplicaSet' and ref.get('controller'))
    deploy_ref = next(ref for ref in rs['metadata']['ownerReferences'] if ref['kind'] == 'Deployment' and ref.get('controller'))
    if (rs['metadata']['uid'] != owner['uid'] or deploy['metadata']['uid'] != deploy_ref['uid']
            or deploy['metadata']['labels'].get('app') != deploy['metadata']['name']):
        raise ValueError('MPS Deployment ownership differs')


def verify(directory, quota, count):
    directory = Path(directory)
    verify_owner(directory)
    server = one_server((directory / 'mps-server-list.txt').read_text())
    names = {'get_server_list': 'mps-server-list.txt',
             'get_default_device_pinned_mem_limit ' + GPU_UUID: 'mps-default-memory.txt',
             f'get_device_pinned_mem_limit {server} {GPU_UUID}': 'mps-server-memory.txt',
             f'get_active_thread_percentage {server}': 'mps-active-thread.txt',
             'get_default_active_thread_percentage': 'mps-default-active-thread.txt',
             f'get_client_list {server}': 'mps-client-list.txt', 'ps': 'mps-ps.txt'}
    raw = {command: (directory / filename).read_text() for command, filename in names.items()}
    save(directory / 'mps-control.json', raw)
    result = readback(raw, quota, count + 1)
    ready = [row for filename in ('task-a.log', 'task-b.log') for row in events(directory / filename)
             if row['event'] in ('A_READY', 'B_READY') and row.get('gpu_uuid')]
    pods = [load(directory / (prefix + '-current.json')) for prefix in ('a', 'b')]
    mappings = [map_client(pid, *( (directory / f'client-{pid}.{field}.txt').read_text()
                                  for field in ('status', 'cgroup', 'cmdline')), pods, ready)
                for pid in result['clients']]
    if (sum(row['pod_uid'] == pods[0]['metadata']['uid'] for row in mappings) != 1
            or sum(row['pod_uid'] == pods[1]['metadata']['uid'] for row in mappings) != count):
        raise ValueError('MPS clients do not belong to the intended A/B Pods')
    if not re.search(r'^Uid:\s+0\s', (directory / 'mps-server-status.txt').read_text(), re.M):
        raise ValueError('MPS server UID is not root')
    result.update(load(directory / 'mps-selection.json'))
    result.update(gpu_uuid=GPU_UUID, daemon_node='dra-a40', daemon_gpu_uuid=GPU_UUID, clients=mappings)
    save(directory / 'mps-identity.json', result)


def main(args):
    try:
        if args[0] == 'select':
            print('\t'.join(select_daemon(args[1])))
        elif args[0] == 'server':
            print(one_server(Path(args[1]).read_text()))
        elif args[0] == 'clients':
            print(' '.join(str(pid) for pid in client_pids(Path(args[1]).read_text())))
        elif args[0] == 'verify':
            verify(args[1], int(args[2]), int(args[3]))
        else:
            raise ValueError('unknown MPS readback action')
    except (OSError, ValueError, KeyError, StopIteration) as error:
        print('MPS check failed: ' + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
