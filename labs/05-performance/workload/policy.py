"""Immutable work schedules and the approved, sequential180-batch matrix."""
import copy
import os
import hashlib
import json
import random

UUID = os.environ.get('GPU_UUID', 'GPU-29ffee3e-bee4-e341-917a-fe7e43e977af')
SEED = 20261007
IMAGE = 'docker.io/pytorch/pytorch@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755'
HAMI_IMAGE = 'docker.io/projecthami/k8s-dra-driver@sha256:ae7e844acbd099a109bfa84296905ea941eed292a7991c06e2eeee443457ede3'
LIBRARY_SHA = '56fc0944896337cc0ee439cbc7364105ccac11e8dc38add6a3dc467cea34b18f'
WORKLOADS = ('continuous', 'bursty-aligned', 'bursty-staggered')
ORDERS = (
    ('exclusive', 'timeslicing', 'hami', 'mps'),
    ('timeslicing', 'mps', 'exclusive', 'hami'),
    ('mps', 'hami', 'timeslicing', 'exclusive'),
    ('hami', 'exclusive', 'mps', 'timeslicing'),
    ('exclusive', 'hami', 'timeslicing', 'mps'),
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def build_matrix():
    result = []
    for block, modes in enumerate(ORDERS, 1):
        for mode_number, mode in enumerate(modes):
            pairs = [(w, n) for w in WORKLOADS for n in (1, 2, 4)]
            random.Random(SEED + block * 100 + mode_number).shuffle(pairs)
            for workload, n in pairs:
                arm = 'sequential' if mode == 'exclusive' and n > 1 else mode
                result.append({'block': block, 'repeat': block, 'mode_group': mode, 'mode': arm,
                               'workload_id': workload, 'concurrency': n,
                               'trial_id': 'b%d-%s-%s-n%d' % (block, workload, arm, n)})
    return result


def task_config(base, workload_id, concurrency, index):
    if concurrency not in (1, 2, 4) or not 0 <= index < concurrency or base['iterations'] < 1:
        raise ValueError('invalid concurrency/task/work count')
    if workload_id not in WORKLOADS:
        raise ValueError('unknown workload')
    result = copy.deepcopy(base)
    sleep = int(base.get('sleep_ns', 0)) if workload_id != 'continuous' else 0
    if workload_id != 'continuous' and sleep <= 0:
        raise ValueError('bursty requires positive sleep')
    offset = index * sleep // concurrency if workload_id == 'bursty-staggered' else 0
    result.update(task_id=str(index), workload_id=workload_id, concurrency=concurrency,
                  sleep_ns=sleep, prefix_sleep_ns=offset, final_sleep_ns=sleep - offset)
    return result


def choose_candidate(candidates):
    good = [r for r in candidates if r['utilization'] >= 90 and r['physical_delta_mib'] < 2048
            and r['median_tflops'] > 0 and not r.get('error')]
    if not good:
        raise ValueError('no eligible calibration candidate')
    best = max(r['median_tflops'] for r in good)
    return min((r for r in good if r['median_tflops'] >= 0.95 * best), key=lambda r: r['shape'])
