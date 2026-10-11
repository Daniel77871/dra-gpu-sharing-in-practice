"""Regressions for the standalone KFP install and partial-install cleanup."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from conftest import ROOT


@pytest.fixture
def kfp_state():
    path = ROOT / 'labs/06-heterogeneous/kubeflow/scripts/kfp_state.py'
    spec = importlib.util.spec_from_file_location('lab06_kfp_revision', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rendered_mysql_and_api_share_the_generated_database_credential():
    # Missing MYSQL_ROOT_PASSWORD initializes an empty password while API requests use a nonempty one.
    binary = ROOT / '.work/bin/kustomize'
    if not binary.is_file():
        pytest.skip('pinned kustomize binary is unavailable')
    rendered = subprocess.run([str(binary), 'build', '--load-restrictor', 'LoadRestrictionsNone',
                               str(ROOT / 'labs/06-heterogeneous/kubeflow/install')],
                              text=True, capture_output=True, check=True)
    objects = list(yaml.safe_load_all(rendered.stdout))
    deployments = {o['metadata']['name']: o for o in objects if o['kind'] == 'Deployment'}
    mysql = deployments['mysql']['spec']['template']['spec']['containers'][0]
    api = deployments['ml-pipeline']['spec']['template']['spec']['containers'][0]
    mysql_env = {o['name']: o for o in mysql['env']}
    api_env = {o['name']: o for o in api['env']}
    reference = {'name': 'mysql-secret', 'key': 'password'}
    assert mysql_env.get('MYSQL_ROOT_PASSWORD', {}).get('valueFrom', {}).get('secretKeyRef') == reference
    assert 'MYSQL_ALLOW_EMPTY_PASSWORD' not in mysql_env
    for key in ('DBCONFIG_PASSWORD', 'DBCONFIG_MYSQLCONFIG_PASSWORD'):
        assert api_env[key]['valueFrom']['secretKeyRef'] == reference


class KfpCleanupCluster:
    """Replace kubectl RPCs while exercising the real uninstall and DeleteOptions."""

    def __init__(self, module, folder, monkeypatch):
        self.module, self.folder = module, folder
        self.namespace = 'gpu-kubeflow-partial-lab'
        self.objects, self.delete_calls, self.failures = {}, [], {}
        self.namespace_object = self.add('Namespace', self.namespace)
        self.crd = self.add('CustomResourceDefinition', 'workflows.argoproj.io',
                            api='apiextensions.k8s.io/v1', spec={
                                'group': 'argoproj.io', 'scope': 'Namespaced',
                                'names': {'plural': 'workflows'}})
        self.other_crd = self.add('CustomResourceDefinition', 'viewers.kubeflow.org',
                                  api='apiextensions.k8s.io/v1', spec={
                                      'group': 'kubeflow.org', 'scope': 'Namespaced',
                                      'names': {'plural': 'viewers'}})
        self.device_class = self.add('DeviceClass', 'gpu-kubeflow-capacity-partial',
                                     api='resource.k8s.io/v1', spec={'selectors': []})
        self.original_crd = self.add('CustomResourceDefinition', 'preexisting.example.org',
                                     api='apiextensions.k8s.io/v1', owned=False,
                                     spec={'group': 'example.org', 'scope': 'Namespaced',
                                           'names': {'plural': 'preexisting'}})
        self.state = {'run_id': 'partial', 'namespace': self.namespace,
                      'created': copy.deepcopy([self.namespace_object, self.crd, self.other_crd, self.device_class]),
                      'reused': [['CustomResourceDefinition', '', 'preexisting.example.org']]}
        module.save(folder / 'state.json', self.state)
        module.save(folder / 'cluster-before.json', self.inventory(original_only=True))
        monkeypatch.setenv('ALLOW_KFP_UNINSTALL', '1')
        monkeypatch.setattr(module, 'kc', self.call)
        monkeypatch.setattr(module.subprocess, 'run', self.discovery)
        monkeypatch.setattr(module.time, 'sleep', lambda _: None)

    def add(self, kind, name, *, namespace=None, api='v1', owned=True, **body):
        metadata = {'name': name, 'uid': kind + '-' + name, 'resourceVersion': '7'}
        if namespace:
            metadata['namespace'] = namespace
        obj = {'apiVersion': api, 'kind': kind, 'metadata': metadata, **body}
        if owned:
            self.module.put_labels(obj, 'partial')
        self.objects[self.module.identity(obj)] = obj
        return obj

    def inventory(self, original_only=False):
        result = {kind: [] for kind in self.module.CLUSTER_TYPES}
        result['customresourcedefinitions'] = copy.deepcopy([
            o for o in self.objects.values() if o['kind'] == 'CustomResourceDefinition'
            and (not original_only or o['metadata']['name'] == 'preexisting.example.org')])
        return result

    def discovery(self, command, **kwargs):
        assert command == ['kubectl', 'api-resources', '--namespaced=true', '--verbs=list', '-o', 'name']
        return subprocess.CompletedProcess(command, 0, 'pods\nconfigmaps\nsecrets\nleases\nworkflows.argoproj.io\nevents\npersistentvolumeclaims\n', '')

    def call(self, *args, obj=None):
        if args[0] == 'get':
            resource = args[1].lower()
            aliases = {'namespace': 'namespaces', 'persistentvolume': 'persistentvolumes',
                       'customresourcedefinition': 'customresourcedefinitions', 'deviceclass': 'deviceclasses'}
            resource = aliases.get(resource, resource)
            namespace = args[args.index('-n') + 1] if '-n' in args else None
            name = args[2] if len(args) > 2 and not args[2].startswith('-') else None
            plurals = {'Lease': 'leases', 'Event': 'events', 'PersistentVolume': 'persistentvolumes',
                       'PersistentVolumeClaim': 'persistentvolumeclaims'}
            rows = [o for o in self.objects.values()
                    if (plurals.get(o['kind'], self.module.PLURALS.get(o['kind'])) == resource
                        or (o['kind'] == 'Workflow' and resource == 'workflows.argoproj.io'))
                    and (namespace is None or o['metadata'].get('namespace') == namespace)]
            if name:
                return copy.deepcopy(next((o for o in rows if o['metadata']['name'] == name), None))
            return {'apiVersion': 'v1', 'kind': 'List', 'items': copy.deepcopy(rows)}
        if args[0] == 'delete':
            assert args[1] == '--raw' and args[3:] == ('-f', '-')
            uri = args[2]
            live = next(o for o in self.objects.values()
                        if o['metadata']['name'] == uri.rsplit('/', 1)[1]
                        and self.module.PLURALS[o['kind']] in uri.split('/'))
            assert obj['preconditions'] == {'uid': live['metadata']['uid'], 'resourceVersion': '7'}
            key = self.module.identity(live)
            if live['kind'] == 'Namespace' and getattr(self, 'replace_during_delete', False):
                live['metadata']['uid'] = 'replacement-namespace'
                self.add('Workflow', 'replacement-workflow', namespace=self.namespace,
                         api='argoproj.io/v1alpha1', owned=False, spec={'entrypoint': 'external'})
                self.add('ResourceClaim', 'replacement-gpu', namespace=self.namespace,
                         api='resource.k8s.io/v1', owned=False, spec={'devices': {'requests': [{
                             'name': 'gpu', 'exactly': {'deviceClassName': 'gpu-kubeflow-capacity-partial'}}]}})
                raise RuntimeError('namespace UID precondition failed')
            if live['kind'] in self.failures:
                raise RuntimeError(self.failures[live['kind']])
            self.delete_calls.append(key)
            del self.objects[key]
            if live['kind'] == 'Namespace':
                self.objects = {k:v for k,v in self.objects.items() if v['metadata'].get('namespace') != live['metadata']['name']}
            return None
        if args[0] == 'wait':
            return 'deleted'
        raise AssertionError(args)


@pytest.fixture
def cleanup_cluster(kfp_state, tmp_path, monkeypatch):
    return KfpCleanupCluster(kfp_state, tmp_path, monkeypatch)


@pytest.mark.parametrize('kind,api', [
    ('Lease', 'coordination.k8s.io/v1'), ('Event', 'v1'), ('Pod', 'v1'),
    ('Workflow', 'argoproj.io/v1alpha1'), ('ConfigMap', 'v1'), ('Secret', 'v1')])
def test_owned_namespace_runtime_objects_do_not_block_partial_install_cleanup(cleanup_cluster, kind, api):
    # A controller can create these without dra-book labels or ownerReferences before rollout finishes.
    cluster = cleanup_cluster
    cluster.add(kind, 'workflow-controller', namespace=cluster.namespace, api=api, owned=False)
    cluster.module.uninstall(cluster.folder)
    assert cluster.module.get('namespace', cluster.namespace) is None
    assert cluster.module.get('CustomResourceDefinition', 'workflows.argoproj.io') is None
    assert cluster.module.get('DeviceClass', 'gpu-kubeflow-capacity-partial') is None
    assert cluster.module.get('CustomResourceDefinition', 'preexisting.example.org') == cluster.original_crd
    report = json.loads((cluster.folder / 'uninstall-verification.json').read_text())
    assert report['verified'] and report['preexisting_objects_unchanged']


def test_partial_uninstall_still_removes_owned_cluster_objects_after_namespace_failure(cleanup_cluster):
    # A failed namespace delete must not short-circuit CRD and DeviceClass cleanup.
    cluster = cleanup_cluster
    cluster.failures['Namespace'] = 'namespace delete unavailable'
    with pytest.raises(RuntimeError, match='namespace'):
        cluster.module.uninstall(cluster.folder)
    assert cluster.module.get('namespace', cluster.namespace) is not None
    for kind, name in [('CustomResourceDefinition', 'workflows.argoproj.io'),
                       ('CustomResourceDefinition', 'viewers.kubeflow.org'),
                       ('DeviceClass', 'gpu-kubeflow-capacity-partial')]:
        assert cluster.module.get(kind, name) is None
    assert cluster.module.get('CustomResourceDefinition', 'preexisting.example.org') == cluster.original_crd
    report = json.loads((cluster.folder / 'uninstall-verification.json').read_text())
    assert not report['verified'] and report['errors']


def test_uninstall_keeps_foreign_consumer_crd_but_cleans_other_owned_cluster_objects(cleanup_cluster):
    # A foreign workflow protects only its CRD; it must not strand unrelated created objects.
    cluster = cleanup_cluster
    cluster.add('Workflow', 'external-workflow', namespace='another-namespace',
                api='argoproj.io/v1alpha1', owned=False, spec={'entrypoint': 'external'})
    with pytest.raises(RuntimeError, match='Foreign'):
        cluster.module.uninstall(cluster.folder)
    assert cluster.module.get('CustomResourceDefinition', 'workflows.argoproj.io') is not None
    assert cluster.module.get('CustomResourceDefinition', 'viewers.kubeflow.org') is None
    assert cluster.module.get('DeviceClass', 'gpu-kubeflow-capacity-partial') is None
    assert cluster.module.get('CustomResourceDefinition', 'preexisting.example.org') == cluster.original_crd


def test_uninstall_refuses_replaced_namespace_but_cleans_unused_owned_cluster_objects(cleanup_cluster):
    # Namespace ownership must still be checked by UID before deleting all runtime contents.
    cluster = cleanup_cluster
    cluster.namespace_object['metadata']['uid'] = 'replacement-namespace'
    with pytest.raises(RuntimeError, match='UID changed'):
        cluster.module.uninstall(cluster.folder)
    assert cluster.module.get('namespace', cluster.namespace)['metadata']['uid'] == 'replacement-namespace'
    assert ('Namespace', '', cluster.namespace) not in cluster.delete_calls
    assert cluster.module.get('DeviceClass', 'gpu-kubeflow-capacity-partial') is None


def test_foreign_device_class_consumer_does_not_strand_created_crds(cleanup_cluster):
    # DeviceClass is checked first; an external Claim must protect it without skipping every CRD.
    cluster = cleanup_cluster
    cluster.add('ResourceClaim', 'external-gpu', namespace='another-namespace',
                api='resource.k8s.io/v1', owned=False, spec={'devices': {'requests': [{
                    'name': 'gpu', 'exactly': {'deviceClassName': 'gpu-kubeflow-capacity-partial'}}]}})
    with pytest.raises(RuntimeError, match='Foreign'):
        cluster.module.uninstall(cluster.folder)
    assert cluster.module.get('DeviceClass', 'gpu-kubeflow-capacity-partial') is not None
    assert cluster.module.get('CustomResourceDefinition', 'workflows.argoproj.io') is None
    assert cluster.module.get('CustomResourceDefinition', 'viewers.kubeflow.org') is None


def test_replaced_cluster_object_does_not_prevent_cleanup_of_other_owned_objects(cleanup_cluster):
    # A foreign replacement with our old name must survive, but unrelated original UIDs can be removed.
    cluster = cleanup_cluster
    cluster.device_class['metadata']['uid'] = 'replacement-device-class'
    with pytest.raises(RuntimeError, match='UID changed'):
        cluster.module.uninstall(cluster.folder)
    assert cluster.module.get('DeviceClass', 'gpu-kubeflow-capacity-partial')['metadata']['uid'] == 'replacement-device-class'
    assert cluster.module.get('CustomResourceDefinition', 'workflows.argoproj.io') is None
    assert cluster.module.get('CustomResourceDefinition', 'viewers.kubeflow.org') is None


def test_namespace_replaced_during_delete_cannot_hide_its_foreign_consumers(cleanup_cluster):
    # A namespace DELETE can reject a replacement UID after our initial ownership check.
    cluster = cleanup_cluster
    cluster.replace_during_delete = True
    with pytest.raises(RuntimeError, match='UID changed'):
        cluster.module.uninstall(cluster.folder)
    assert cluster.module.get('namespace', cluster.namespace)['metadata']['uid'] == 'replacement-namespace'
    assert cluster.module.get('DeviceClass', 'gpu-kubeflow-capacity-partial') is not None
    assert cluster.module.get('CustomResourceDefinition', 'workflows.argoproj.io') is not None
    assert cluster.module.get('CustomResourceDefinition', 'viewers.kubeflow.org') is None


def test_cleanup_succeeds_when_install_failed_before_recording_a_namespace(cleanup_cluster):
    # An absent Namespace needs no ownership record; early failures must still be cleanable.
    cluster = cleanup_cluster
    cluster.objects.pop(('Namespace', '', cluster.namespace))
    cluster.state['created'] = [o for o in cluster.state['created'] if o['kind'] != 'Namespace']
    cluster.module.save(cluster.folder / 'state.json', cluster.state)
    cluster.module.uninstall(cluster.folder)
    report = json.loads((cluster.folder / 'uninstall-verification.json').read_text())
    assert report['verified'] and report['namespace_absent']


def test_storage_verification_failure_does_not_strand_created_cluster_objects(cleanup_cluster):
    # A replaced PV after namespace deletion is preserved without skipping the owned CRDs/DeviceClass.
    cluster = cleanup_cluster
    cluster.add('PersistentVolume', 'mysql-volume', owned=False,
                spec={'persistentVolumeReclaimPolicy': 'Delete', 'claimRef': {'uid': 'original-pvc'}})
    cluster.module.save(cluster.folder / 'storage-before.json', [{'name': 'mysql-volume', 'uid': 'old-volume'}])
    cluster.objects.pop(('Namespace', '', cluster.namespace))
    with pytest.raises(RuntimeError, match='PV was replaced'):
        cluster.module.uninstall(cluster.folder)
    assert cluster.module.get('PersistentVolume', 'mysql-volume') is not None
    assert cluster.module.get('CustomResourceDefinition', 'workflows.argoproj.io') is None
    assert cluster.module.get('CustomResourceDefinition', 'viewers.kubeflow.org') is None
    assert cluster.module.get('DeviceClass', 'gpu-kubeflow-capacity-partial') is None


def test_uninstall_retry_keeps_outstanding_pv_when_its_pvc_is_already_gone(cleanup_cluster, monkeypatch):
    # Namespace deletion can remove a PVC before its PV; retries must retain that PV's original UID.
    cluster = cleanup_cluster
    pv = cluster.add('PersistentVolume', 'mysql-volume', owned=False,
                     spec={'persistentVolumeReclaimPolicy': 'Delete', 'claimRef': {'uid': 'removed-pvc'}})
    recorded = [{'name': pv['metadata']['name'], 'uid': pv['metadata']['uid']}]
    cluster.module.save(cluster.folder / 'storage-before.json', recorded)
    ticks = iter([0, 301])
    monkeypatch.setattr(cluster.module.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(RuntimeError, match='Owned PV did not disappear'):
        cluster.module.uninstall(cluster.folder)
    assert json.loads((cluster.folder / 'storage-before.json').read_text()) == recorded
    assert cluster.module.get('PersistentVolume', 'mysql-volume') is not None
    report = json.loads((cluster.folder / 'uninstall-verification.json').read_text())
    assert not report['verified'] and any(e['stage'] == 'storage' for e in report['errors'])
    assert not json.loads((cluster.folder / 'state.json').read_text()).get('uninstalled', False)
    assert cluster.module.get('CustomResourceDefinition', 'workflows.argoproj.io') is None
    assert cluster.module.get('DeviceClass', 'gpu-kubeflow-capacity-partial') is None


def test_uninstall_retry_merges_old_and_current_pv_evidence(cleanup_cluster, monkeypatch):
    cluster = cleanup_cluster
    previous = {'name': 'already-reclaimed-volume', 'uid': 'old-volume-uid'}
    cluster.module.save(cluster.folder / 'storage-before.json', [previous])
    pvc = cluster.add('PersistentVolumeClaim', 'mysql-pvc', namespace=cluster.namespace,
                      spec={'volumeName': 'mysql-volume'})
    pv = cluster.add('PersistentVolume', 'mysql-volume', owned=False,
                     spec={'persistentVolumeReclaimPolicy': 'Delete',
                           'claimRef': {'uid': pvc['metadata']['uid']}})
    call = cluster.call

    def reclaim(*args, obj=None):
        result = call(*args, obj=obj)
        if args[0] == 'delete' and '/namespaces/' in args[2]:
            cluster.objects.pop(cluster.module.identity(pv))
        return result

    monkeypatch.setattr(cluster.module, 'kc', reclaim)
    cluster.module.uninstall(cluster.folder)
    assert json.loads((cluster.folder / 'storage-before.json').read_text()) == [
        previous, {'name': pv['metadata']['name'], 'uid': pv['metadata']['uid']}]
    assert json.loads((cluster.folder / 'uninstall-verification.json').read_text())['verified']


def test_uninstall_retry_refuses_conflicting_pv_uid_without_replacing_evidence(cleanup_cluster, monkeypatch):
    cluster = cleanup_cluster
    pvc = cluster.add('PersistentVolumeClaim', 'mysql-pvc', namespace=cluster.namespace,
                      spec={'volumeName': 'mysql-volume'})
    cluster.add('PersistentVolume', 'mysql-volume', owned=False,
                spec={'persistentVolumeReclaimPolicy': 'Delete', 'claimRef': {'uid': pvc['metadata']['uid']}})
    recorded = [{'name': 'mysql-volume', 'uid': 'original-volume-uid'}]
    cluster.module.save(cluster.folder / 'storage-before.json', recorded)
    ticks = iter([0, 301])
    monkeypatch.setattr(cluster.module.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(RuntimeError, match='Storage PV UID changed'):
        cluster.module.uninstall(cluster.folder)
    assert json.loads((cluster.folder / 'storage-before.json').read_text()) == recorded
    assert cluster.module.get('namespace', cluster.namespace) is not None
    assert cluster.module.get('PersistentVolume', 'mysql-volume') is not None
    assert cluster.module.get('CustomResourceDefinition', 'workflows.argoproj.io') is None
    assert not json.loads((cluster.folder / 'uninstall-verification.json').read_text())['verified']


class KfpFailureDiagnostics:
    """Serve Pod status and logs at the subprocess boundary without a cluster."""

    def __init__(self, cluster, monkeypatch, previous_error=False, previous_timeout=False):
        self.cluster, self.previous_error, self.previous_timeout = cluster, previous_error, previous_timeout
        self.pods = {'apiVersion': 'v1', 'kind': 'List', 'items': [
            self.pod('api-server', 'ml-pipeline-api-server', {'running': {}}, restarts=6,
                     init=('wait-for-database', {'terminated': {'exitCode': 0}})),
            self.pod('mysql-worker', 'mysql', {'running': {}}),
            self.pod('persistence-worker', 'ml-pipeline-persistenceagent',
                     {'waiting': {'reason': 'PodInitializing'}},
                     init=('wait-for-ml-pipeline', {'running': {}})),
            self.pod('creating-worker', 'pending-container', {'waiting': {'reason': 'ContainerCreating'}})]}
        monkeypatch.setattr(cluster.module.subprocess, 'run', self.run)

    def pod(self, name, container, status, restarts=0, init=None):
        value = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {
            'name': name, 'namespace': self.cluster.namespace, 'uid': name + '-uid'},
            'spec': {'containers': [{'name': container, 'image': 'pinned-image'}]},
            'status': {'phase': 'Running' if 'running' in status else 'Pending',
                       'containerStatuses': [{'name': container, 'state': status, 'restartCount': restarts}]}}
        if restarts:
            value['status']['containerStatuses'][0]['lastState'] = {'terminated': {'exitCode': 1}}
        if init:
            value['spec']['initContainers'] = [{'name': init[0], 'image': 'pinned-init-image'}]
            value['status']['initContainerStatuses'] = [{'name': init[0], 'state': init[1], 'restartCount': 0}]
        return value

    def run(self, command, **kwargs):
        assert command[0] == 'kubectl'
        if 'logs' in command:
            pod = command[command.index('logs') + 1]
            container = command[command.index('-c') + 1]
            if container in ('pending-container', 'ml-pipeline-persistenceagent'):
                raise AssertionError('logs requested before a container started')
            previous = '--previous' in command
            if previous and self.previous_timeout:
                raise subprocess.TimeoutExpired(command, 45, output='partial previous log\n', stderr='log request timed out\n')
            if previous and self.previous_error:
                return subprocess.CompletedProcess(command, 1, '', 'previous log unavailable')
            return subprocess.CompletedProcess(command, 0, pod + '/' + container + (' previous' if previous else ' current') + '\n', '')
        if 'get' in command:
            kind = command[command.index('get') + 1]
            if kind == 'namespace':
                return subprocess.CompletedProcess(command, 0, json.dumps(self.cluster.namespace_object), '')
            if kind == 'pods':
                return subprocess.CompletedProcess(command, 0, json.dumps(self.pods), '')
            if kind == 'events':
                return subprocess.CompletedProcess(command, 0, 'startup probe failed\n', '')
            return subprocess.CompletedProcess(command, 0, '{"items": []}\n', '')
        if 'describe' in command:
            return subprocess.CompletedProcess(command, 0, 'container status and probe failures\n', '')
        raise AssertionError(command)


def _failed_install_cli(cluster, monkeypatch):
    def fail_install(*_):
        raise RuntimeError('deployment/ml-pipeline exceeded its progress deadline')
    monkeypatch.setattr(cluster.module, 'install', fail_install)
    monkeypatch.setenv('ALLOW_KFP_INSTALL', '1')
    monkeypatch.setattr(sys, 'argv', ['kfp_state.py', 'install', '--state-dir', str(cluster.folder),
                                     '--run-id', 'partial', '--namespace', cluster.namespace])
    with pytest.raises(RuntimeError, match='exceeded its progress deadline'):
        cluster.module.main()
    reports = list((cluster.folder / 'diagnostics').glob('*/report.json'))
    assert len(reports) == 1, 'failed installer did not retain diagnostics in KFP_STATE_DIR'
    return reports[0].parent, json.loads(reports[0].read_text())


def test_failed_install_retains_current_previous_init_mysql_and_event_diagnostics(cleanup_cluster, monkeypatch):
    # A rollout exception must produce all requested evidence before the original failure propagates.
    KfpFailureDiagnostics(cleanup_cluster, monkeypatch)
    folder, report = _failed_install_cli(cleanup_cluster, monkeypatch)
    assert (folder / 'api-server.ml-pipeline-api-server.current.log').read_text() == 'api-server/ml-pipeline-api-server current\n'
    assert (folder / 'api-server.ml-pipeline-api-server.previous.log').read_text() == 'api-server/ml-pipeline-api-server previous\n'
    assert (folder / 'api-server.wait-for-database.current.log').read_text() == 'api-server/wait-for-database current\n'
    assert (folder / 'persistence-worker.wait-for-ml-pipeline.current.log').read_text() == 'persistence-worker/wait-for-ml-pipeline current\n'
    assert (folder / 'mysql-worker.mysql.current.log').read_text() == 'mysql-worker/mysql current\n'
    assert 'startup probe failed' in (folder / 'events.txt').read_text()
    assert json.loads((folder / 'pods.json').read_text())['items']
    assert report['original_error'].endswith('exceeded its progress deadline')
    assert not report['errors']
    assert any(row['container'] == 'pending-container' for row in report['skipped_logs'])


def test_diagnostic_log_failure_keeps_original_install_error_and_collects_other_containers(cleanup_cluster, monkeypatch):
    # Missing previous logs cannot hide the rollout error or stop MySQL/events collection.
    KfpFailureDiagnostics(cleanup_cluster, monkeypatch, previous_error=True)
    folder, report = _failed_install_cli(cleanup_cluster, monkeypatch)
    assert 'previous log unavailable' in (folder / 'api-server.ml-pipeline-api-server.previous.log.stderr').read_text()
    assert (folder / 'mysql-worker.mysql.current.log').read_text().endswith(' current\n')
    assert (folder / 'events.txt').is_file()
    assert report['errors']


def test_diagnostics_do_not_read_logs_from_a_replaced_namespace(cleanup_cluster, monkeypatch):
    # A state directory must not authorize reads from a new Namespace with the same name.
    cluster = cleanup_cluster
    cluster.namespace_object['metadata']['uid'] = 'replacement-namespace'
    KfpFailureDiagnostics(cluster, monkeypatch)
    folder, report = _failed_install_cli(cluster, monkeypatch)
    assert report['errors'] and any('UID changed' in row['error'] for row in report['errors'])
    assert not list(folder.glob('*.log'))


def test_diagnostic_timeout_preserves_partial_output_and_continues_collection(cleanup_cluster, monkeypatch):
    # A timeout carries useful partial output; it must not discard it or skip MySQL logs.
    KfpFailureDiagnostics(cleanup_cluster, monkeypatch, previous_timeout=True)
    folder, report = _failed_install_cli(cleanup_cluster, monkeypatch)
    assert (folder / 'api-server.ml-pipeline-api-server.previous.log').read_text() == 'partial previous log\n'
    assert (folder / 'api-server.ml-pipeline-api-server.previous.log.stderr').read_text() == 'log request timed out\n'
    assert (folder / 'mysql-worker.mysql.current.log').read_text().endswith(' current\n')
    assert report['errors']


def test_rejected_install_guard_never_collects_existing_namespace_logs(cleanup_cluster, monkeypatch):
    # An existing state directory cannot bypass the explicit install guard through its error handler.
    cluster = cleanup_cluster
    monkeypatch.delenv('ALLOW_KFP_INSTALL', raising=False)
    monkeypatch.setattr(sys, 'argv', ['kfp_state.py', 'install', '--state-dir', str(cluster.folder),
                                     '--run-id', 'partial', '--namespace', cluster.namespace])
    def forbid_rpc(*args, **kwargs):
        raise AssertionError('kubectl called after rejected install guard')
    monkeypatch.setattr(cluster.module, 'kc', forbid_rpc)
    monkeypatch.setattr(cluster.module.subprocess, 'run', forbid_rpc)
    with pytest.raises(RuntimeError, match='ALLOW_KFP_INSTALL'):
        cluster.module.main()
    assert not (cluster.folder / 'diagnostics').exists()
