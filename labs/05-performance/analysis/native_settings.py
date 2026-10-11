"""Explicit native-provider backup, feature-gate preparation and restoration."""
import argparse
import copy
import json
import os
import re
import sys

import run as benchmark

DESIRED = {'MPSSupport': 'true', 'TimeSlicingSettings': 'true', 'ConsumableShares': 'false'}


def gates(spec):
    container = next(c for c in spec['template']['spec']['containers'] if c['name'] == 'gpus')
    if any('--feature-gates' in arg for arg in container.get('args', [])):
        raise ValueError('explicit CLI feature gates are unsupported; refusing to override them')
    entries = next(e.get('value', '') for e in container['env'] if e['name'] == 'FEATURE_GATES')
    return dict(item.split('=', 1) for item in entries.split(',') if item)


def prepared(spec):
    result = copy.deepcopy(spec)
    values = gates(result)
    values.update(DESIRED)
    container = next(c for c in result['template']['spec']['containers'] if c['name'] == 'gpus')
    entry = next(e for e in container['env'] if e['name'] == 'FEATURE_GATES')
    entry['value'] = ','.join(key + '=' + value for key, value in values.items())
    result['updateStrategy'] = {'type': 'OnDelete'}
    return result


def pods(kube, original, namespace):
    uid = original['metadata']['uid']
    return [p for p in kube.get('pods', '-n', namespace)['items'] if any(
        r.get('kind') == 'DaemonSet' and r.get('uid') == uid for r in p['metadata'].get('ownerReferences', []))]


def check(kube, state, original, allowed):
    p = state['parameters']
    current = kube.get('daemonset', p['daemonset'], '-n', p['namespace'])
    if current['metadata']['uid'] != original['metadata']['uid'] or current['spec'] not in allowed:
        raise ValueError('native DaemonSet UID/spec changed outside this operation')
    live = pods(kube, original, p['namespace'])
    protected = {pod['metadata']['name']: pod['metadata']['uid'] for pod in live
                 if pod['spec']['nodeName'] != p['node']}
    if protected != state['protected']:
        raise ValueError('another node plugin changed; refusing to continue')
    return current, live


def patch(kube, current, expected, namespace):
    body = [{'op': 'test', 'path': '/metadata/uid', 'value': current['metadata']['uid']},
            {'op': 'test', 'path': '/spec', 'value': current['spec']},
            {'op': 'replace', 'path': '/spec', 'value': expected}]
    kube.call('patch', 'daemonset', current['metadata']['name'], '-n', namespace,
              '--type=json', '-p', json.dumps(body))


def restart_target(kube, state, original, expected, *, allow_delete=True):
    p = state['parameters']
    _, live = check(kube, state, original, [expected])
    selected = [pod for pod in live if pod['spec']['nodeName'] == p['node']]
    if len(selected) > 1:
        raise ValueError('more than one target plugin')
    # Only an old-template Pod is deleted. Retries never delete the new Pod.
    if allow_delete and selected and gates({'template': {'spec': selected[0]['spec']}}) != gates(expected):
        pod = selected[0]
        body = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {
            'uid': pod['metadata']['uid'], 'resourceVersion': pod['metadata']['resourceVersion']}}
        kube.call('delete', '--raw', f"/api/v1/namespaces/{p['namespace']}/pods/{pod['metadata']['name']}",
                  '-f', '-', stdin=json.dumps(body))
    def ready():
        _, live = check(kube, state, original, [expected])
        selected = [pod for pod in live if pod['spec']['nodeName'] == p['node']]
        if len(selected) != 1:
            return None
        pod = selected[0]
        if gates({'template': {'spec': pod['spec']}}) != gates(expected):
            return None
        return pod if any(c['name'] == 'gpus' and c.get('ready') for c in pod['status'].get('containerStatuses', [])) else None
    return benchmark.wait_for(ready, 300, 'target native plugin with expected feature gates')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'verify', 'restore'))
    parser.add_argument('run_id')
    args = parser.parse_args(argv)
    if os.environ.get('I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER') != '1':
        parser.error('set I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1 for explicit provider operations')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', args.run_id):
        parser.error('invalid run-id')
    out = benchmark.LAB / 'out' / args.run_id / 'native-provider'
    out.mkdir(parents=True, exist_ok=True)
    kube = benchmark.Kube(out)
    try:
        options = benchmark.selection(os.environ)
        parameters = {key: options[key] for key in ('node', 'gpu_uuid')}
        parameters.update(namespace=os.environ.get('NVIDIA_DRA_NAMESPACE', 'nvidia-dra-driver-gpu'),
                          daemonset=os.environ.get('NVIDIA_DRA_DAEMONSET', 'dra-driver-nvidia-gpu-kubelet-plugin'))
        state_path, original_path = out / 'backup.json', out / 'original-daemonset.json'
        if state_path.exists():
            state, original = benchmark.analyze.read_json(state_path), benchmark.analyze.read_json(original_path)
            if state['parameters'] != parameters:
                raise ValueError('provider backup parameters changed')
        else:
            if args.action != 'install':
                raise ValueError('original backup required')
            if original_path.exists():
                raise ValueError('partial backup; preserve it and use a new provider run-id')
            original = kube.get('daemonset', parameters['daemonset'], '-n', parameters['namespace'])
            live = pods(kube, original, parameters['namespace'])
            if len([p for p in live if p['spec']['nodeName'] == parameters['node']]) != 1:
                raise ValueError('requires one original target native plugin')
            gates(original['spec'])
            state = {'parameters': parameters, 'protected': {p['metadata']['name']: p['metadata']['uid']
                     for p in live if p['spec']['nodeName'] != parameters['node']}}
            benchmark.save(original_path, original)
            (out / 'original-daemonset.yaml').write_text(kube.call('get', 'daemonset', parameters['daemonset'], '-n', parameters['namespace'], '-o', 'yaml'))
            benchmark.save(out / 'original-pods.json', live)
            benchmark.save(state_path, state)
        run = {'nonce': '0' * 32, 'options': options}
        driver = benchmark.driver(kube, options['node'])
        gpu_target = benchmark.target(kube, options, 'exclusive')
        if gpu_target is None:
            raise ValueError('native provider must be active before install/restore')
        benchmark.idle(kube, driver, run, gpu_target, out / (args.action + '-idle.json'))
        expected = prepared(original['spec'])
        restoring = copy.deepcopy(original['spec'])
        restoring['updateStrategy'] = {'type': 'OnDelete'}
        if args.action == 'install':
            if (out / 'restored.json').exists():
                raise ValueError('restored backup cannot start a new operation; choose a new provider run-id')
            current, _ = check(kube, state, original, [original['spec'], expected])
            if current['spec'] != expected:
                patch(kube, current, expected, parameters['namespace'])
            pod = restart_target(kube, state, original, expected)
            benchmark.save(out / 'installed.json', {'uid': original['metadata']['uid'], 'spec': expected,
                                                   'plugin_uid': pod['metadata']['uid']})
        elif args.action == 'verify':
            check(kube, state, original, [expected])
            restart_target(kube, state, original, expected, allow_delete=False)
        else:
            current, _ = check(kube, state, original, [expected, restoring, original['spec']])
            if current['spec'] != original['spec'] or not (out / 'restored.json').exists():
                if current['spec'] != restoring:
                    patch(kube, current, restoring, parameters['namespace'])
                restart_target(kube, state, original, restoring)
                current, _ = check(kube, state, original, [restoring])
                if current['spec'] != original['spec']:
                    patch(kube, current, original['spec'], parameters['namespace'])
            current, _ = check(kube, state, original, [original['spec']])
            benchmark.save(out / 'restored.json', {'uid': current['metadata']['uid'], 'spec': current['spec']})
        benchmark.wait_for(lambda: benchmark.target(kube, options, 'exclusive'), 120, 'native ResourceSlice')
        benchmark.idle(kube, driver, run, gpu_target, out / (args.action + '-final-idle.json'))
        print(args.action + ' verified; backup retained at ' + str(out))
        return 0
    except (OSError, ValueError, KeyError, StopIteration, RuntimeError) as error:
        benchmark.save(out / 'provider-error.json', {'error': str(error)})
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
