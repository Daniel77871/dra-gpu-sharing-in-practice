"""HAMi scope evidence, including the separately recovered stalled round."""
import json

from . import register
from .e2 import pod
from .hami_evidence import Evidence, barrier, identity, trial

FIRST = 'results/experiment-2b-hami-scope/e2b-20261007t053126z'
SECOND = 'results/experiment-2b-hami-scope/e2b-20261007t060500z'
CASES = [(FIRST, case, repeat) for case in ('case3', 'case4a') for repeat in (1, 2, 3)] + [
    (SECOND, 'case4b', repeat) for repeat in (1, 2, 3)]
HOST_ROOTS = ('/usr/local/vgpu', '/var/lib/kubelet/plugins/hami-core-gpu.project-hami.io',
              '/tmp/vgpulock', '/var/run/cdi')


def host_projection(value):
    fields = ('path', 'mode', 'dev', 'ino', 'uid', 'size', 'sha256')
    return {root: [{key: row[key] for key in fields if key in row} for row in value[root]]
            for root in HOST_ROOTS}


@register('e2b', help='HAMi cache scope, eight completed rounds and one stalled round')
def extract_e2b(reader, output):
    evidence = Evidence(reader, output)
    # These shared deployment sources are hashed and listed without publishing
    # the complete provider snapshots. The host projection retains identities
    # and hashes, never checkpoint contents, library bytes, or CDI contents.
    for name in ('provider-input.yaml', 'deviceclass-input.yaml', 'resourceslices-provider.yaml'):
        evidence.read(f'{SECOND}/{name}')
    for name in ('host-installed.json', 'host-restored-clean.json'):
        evidence.write(f'provider-evidence/{name}', host_projection(evidence.json(f'{SECOND}/{name}')))
    for root, case, repeat in CASES:
        name = f'{case}-r{repeat}'
        source, dest = f'{root}/{name}', f'raw/{name}'
        meta = trial(evidence.json(f'{source}/trial.json'))
        meta.update(backend='hami', quota_gib=8)
        evidence.write(f'{dest}/trial.json', meta)
        value = evidence.json(f'{source}/identity.json')
        evidence.write(f'{dest}/identity.json', identity(value))
        for client in value['clients']:
            filename = f'{client["pod_name"]}-{client["container_name"]}.log'
            logs = f'{source}/recovery' if case == 'case4a' and repeat == 3 else source
            evidence.copy(f'{logs}/{filename}', f'{dest}/client-{client["client_id"]}.log')
        # Explicit observed barrier ranges from the archived experiment. The
        # stalled case has no round-15.json and that absence is not repaired.
        count = 15 if case == 'case4a' and repeat == 3 else 16 if case == 'case3' else 24
        for number in range(count):
            filename = f'round-{number:02d}.json'
            evidence.write(f'{dest}/{filename}', barrier(evidence.json(f'{source}/{filename}')))
        if case == 'case4a' and repeat == 3:
            recovered = evidence.json(f'{source}/recovery/terminated-pods.json')
            evidence.write(f'{dest}/recovery/terminated-pods.json', [pod(p) for p in recovered])
            cleaned = evidence.json(f'{source}/recovery/cleanup.json')
            evidence.write(f'{dest}/recovery/cleanup.json', {k: v for k, v in cleaned.items()
                           if k in ('verified', 'all_cuda_containers_exited', 'namespace_absent', 'utc')})
        else:
            terminated = evidence.json(f'{source}/terminated-pods.json')
            evidence.write(f'{dest}/terminated-pods.json', [pod(p) for p in terminated])
    for root, name in ((FIRST, 'case3-r1'), (SECOND, 'case4b-r1')):
        value = evidence.json(f'{root}/{name}/versions.json')
        evidence.write(f'versions-{name}.json', {'kubernetes': value['kubernetes'], 'provider': pod(value['provider'])})
    evidence.finish()
