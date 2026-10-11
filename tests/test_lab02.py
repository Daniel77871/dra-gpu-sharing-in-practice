import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from conftest import ROOT

LAB = ROOT / "labs/02-setup"
IMAGE = "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755"
EXPECTED = {"a40": ("dra-a40", "NVIDIA A40"), "rtx4090": ("dra-4090", "NVIDIA GeForce RTX 4090")}


def load_module(name):
    path = LAB / "analysis" / f"{name}.py"
    assert path.is_file(), f"missing implementation: {path}"
    spec = importlib.util.spec_from_file_location(f"lab02_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_labels(metadata):
    assert metadata["labels"]["app.kubernetes.io/part-of"] == "dra-book"
    assert metadata["labels"]["app.kubernetes.io/component"]


def test_manifests_labels_selectors_and_pinned_jobs():
    files = sorted((LAB / "manifests").glob("*.yaml"))
    assert files, "lab02 manifests are missing"
    objects = [doc for path in files for doc in yaml.safe_load_all(path.read_text()) if doc]
    assert len(objects) == 6
    by_kind = {}
    for doc in objects:
        assert_labels(doc["metadata"])
        by_kind.setdefault(doc["kind"], []).append(doc)
        if doc["kind"] != "Namespace":
            assert doc["metadata"]["namespace"] == "gpu-setup-lab"
    assert by_kind["Namespace"][0]["metadata"]["name"] == "gpu-setup-lab"
    claims = {c["metadata"]["name"]: c for c in by_kind["ResourceClaimTemplate"]}
    jobs = {j["metadata"]["name"]: j for j in by_kind["Job"]}
    for role, (_, product) in EXPECTED.items():
        template = claims[f"{role}-whole-gpu"]
        assert_labels(template["spec"]["metadata"])
        request = template["spec"]["spec"]["devices"]["requests"][0]
        exact = request["exactly"]
        assert request["name"] == "gpu"
        assert exact["deviceClassName"] == "gpu.nvidia.com"
        assert exact["allocationMode"] == "ExactCount" and exact["count"] == 1
        assert exact["selectors"][0]["cel"]["expression"] == f'device.attributes["gpu.nvidia.com"].productName == "{product}"'
        job = jobs[f"gpu-smoke-{role}"]
        assert job["spec"]["backoffLimit"] == 0
        assert "ttlSecondsAfterFinished" not in job["spec"]
        pod = job["spec"]["template"]
        assert_labels(pod["metadata"])
        assert pod["spec"]["restartPolicy"] == "Never"
        assert pod["spec"]["automountServiceAccountToken"] is False
        assert pod["spec"]["tolerations"] == [{"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}]
        assert pod["spec"]["resourceClaims"] == [{"name": "gpu", "resourceClaimTemplateName": f"{role}-whole-gpu"}]
        container = pod["spec"]["containers"][0]
        assert container["image"] == IMAGE
        assert container["imagePullPolicy"] == "IfNotPresent"
        assert container["resources"]["claims"] == [{"name": "gpu"}]
        assert "nodeName" not in pod["spec"]


def test_values_exactly_encode_original_set_flags_and_configmap_matches_workload():
    gpu = yaml.safe_load((LAB / "values/gpu-operator.yaml").read_text())
    assert gpu == {"devicePlugin": {"enabled": False}, "driver": {"enabled": True, "manager": {"env": [
        {"name": "NODE_LABEL_FOR_GPU_POD_EVICTION", "value": "nvidia.com/dra-kubelet-plugin"}]}}}
    dra = yaml.safe_load((LAB / "values/dra-driver.yaml").read_text())
    assert dra == {"nvidiaDriverRoot": "/run/nvidia/driver", "gpuResourcesEnabledOverride": True,
                   "featureGates": {"MPSSupport": True}, "resources": {"computeDomains": {"enabled": False}}}
    config = yaml.safe_load((LAB / "manifests/03-workload.yaml").read_text())
    assert config["data"]["gpu_smoke.py"] == (LAB / "workload/gpu_smoke.py").read_text()


def test_claim_template_cel_stays_on_one_quoted_line_in_manifests_and_book():
    book = (ROOT / "book/02-deploy/verify.md").read_text()
    for filename, product in [("01-a40-claim-template.yaml", "NVIDIA A40"),
                              ("02-rtx4090-claim-template.yaml", "NVIDIA GeForce RTX 4090")]:
        text = (LAB / "manifests" / filename).read_text()
        line = f"""expression: 'device.attributes["gpu.nvidia.com"].productName == "{product}"'"""
        assert line in [item.strip() for item in text.splitlines()]
        assert line in [item.strip() for item in book.splitlines()]


@pytest.fixture
def completed_run(tmp_path):
    (tmp_path / "run.json").write_text(json.dumps({"run_id": "fixture", "mode": "smoke"}))
    for role, (node, product) in EXPECTED.items():
        pod = {"metadata": {"name": f"gpu-smoke-{role}-fixture"}, "spec": {"nodeName": node}}
        claim = {"metadata": {"name": f"generated-{role}"}, "status": {"allocation": {"devices": {
            "results": [{"driver": "gpu.nvidia.com", "pool": node, "device": "gpu-0"}]}}}}
        (tmp_path / f"{role}-pod-allocated.json").write_text(json.dumps(pod))
        (tmp_path / f"{role}-claim-allocated.json").write_text(json.dumps(claim))
        (tmp_path / f"{role}-claim-completed.json").write_text(json.dumps(claim))
        (tmp_path / f"{role}-pod.log").write_text(f"GPU fixture\nPYTORCH_CUDA_OK {product}\n")
        (tmp_path / f"{role}-deletion.json").write_text(json.dumps({
            "pod_deleted_at": "2026-10-11T00:00:01Z", "claim_deleted_at": "2026-10-11T00:00:02Z",
            "claim_absent_before_job_deletion": False, "observation_seconds": 2}))
    return tmp_path


def test_summarize_real_artifact_shape_and_cli(completed_run):
    module = load_module("summarize")
    summary = module.build_summary(completed_run)
    assert summary["run_id"] == "fixture"
    assert len(summary["gpus"]) == 2
    for row in summary["gpus"]:
        assert row["claim_allocated"] is True
        assert row["claim_deleted_after_pod_deletion"] is True
        assert row["pytorch_cuda_ok"] is True
    rendered = module.render_table(summary)
    assert "node | GPU | claim allocated | claim deleted after Pod deletion | pytorch CUDA ok" in rendered
    assert "dra-a40 | NVIDIA A40 | yes | yes | yes" in rendered
    result = subprocess.run([sys.executable, str(LAB / "analysis/summarize.py"), str(completed_run), "--write"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads((completed_run / "summary.json").read_text()) == summary


def test_summarize_reports_missing_cuda_timeout_and_early_claim_deletion(completed_run):
    module = load_module("summarize")
    (completed_run / "a40-pod.log").write_text("CUDA initialization failed\n")
    (completed_run / "a40-deletion.json").write_text(json.dumps({"pod_deleted_at": None,
        "claim_deleted_at": None, "claim_absent_before_job_deletion": False, "observation_seconds": 300}))
    (completed_run / "rtx4090-deletion.json").write_text(json.dumps({
        "pod_deleted_at": "2026-10-11T00:00:01Z", "claim_deleted_at": "2026-10-11T00:00:00Z",
        "claim_absent_before_job_deletion": True, "observation_seconds": 1}))
    rows = module.build_summary(completed_run)["gpus"]
    assert rows[0]["pytorch_cuda_ok"] is False
    assert rows[0]["claim_deleted_after_pod_deletion"] is False
    assert rows[1]["claim_deleted_after_pod_deletion"] is False
    assert rows[1]["claim_absent_before_job_deletion"] is True
    assert "earlier" in module.render_table({"gpus": rows})


def test_helm_values_compare_ignores_map_order_and_reports_changed_required_flags():
    module = load_module("verify")
    expected = {"featureGates": {"MPSSupport": True}, "nvidiaDriverRoot": "/run/nvidia/driver"}
    actual = {"nvidiaDriverRoot": "/run/nvidia/driver", "featureGates": {"MPSSupport": True}, "extra": True}
    errors, extras = module.value_differences(expected, actual)
    assert errors == [] and extras == ["extra"]
    actual["featureGates"]["MPSSupport"] = False
    errors, _ = module.value_differences(expected, actual)
    assert any("featureGates.MPSSupport" in error for error in errors)


def ready_pod(name, node):
    return {"metadata": {"name": name}, "spec": {"nodeName": node}, "status": {
        "phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]}}


def test_inventory_verify_accepts_completed_validators_and_rejects_wrong_products():
    module = load_module("verify")
    nodes = {"items": [{"metadata": {"name": node}, "status": {"conditions": [
        {"type": "Ready", "status": "True"}]}} for node in ["dra-m1", "dra-non", "dra-a40", "dra-4090"]]}
    operator = {"items": [ready_pod("gpu-operator-fixture", "dra-m1"),
        ready_pod("gpu-operator-node-feature-discovery-master-fixture", "dra-m1"),
        ready_pod("gpu-operator-node-feature-discovery-gc-fixture", "dra-non")]}
    for role, (node, _) in EXPECTED.items():
        for prefix in ["nvidia-driver-daemonset-", "nvidia-container-toolkit-daemonset-", "nvidia-operator-validator-",
                       "gpu-feature-discovery-", "nvidia-dcgm-exporter-"]:
            operator["items"].append(ready_pod(prefix + role, node))
        operator["items"].append({"metadata": {"name": "nvidia-cuda-validator-" + role}, "spec": {"nodeName": node},
                                   "status": {"phase": "Succeeded"}})
    for node in ["dra-m1", "dra-non", "dra-a40", "dra-4090"]:
        operator["items"].append(ready_pod("gpu-operator-node-feature-discovery-worker-" + node, node))
    plugins = {"items": [ready_pod(f"dra-driver-nvidia-gpu-kubelet-plugin-{role}", node)
                          for role, (node, _) in EXPECTED.items()]}
    classes = {"items": [{"metadata": {"name": name}} for name in ["gpu.nvidia.com", "mig.nvidia.com", "vfio.gpu.nvidia.com"]]}
    slices = {"items": [{"spec": {"nodeName": node, "driver": "gpu.nvidia.com", "devices": [{
        "name": "gpu-0", "attributes": {"productName": {"string": product}}}]}} for node, product in EXPECTED.values()]}
    assert module.inventory_errors(nodes, operator, plugins, classes, slices) == []
    slices["items"][0]["spec"]["devices"][0]["attributes"]["productName"]["string"] = "fixture-wrong-gpu"
    assert any("productName" in error for error in module.inventory_errors(nodes, operator, plugins, classes, slices))
    plugins["items"].pop()
    assert any("dra-4090" in error for error in module.inventory_errors(nodes, operator, plugins, classes, slices))


def test_operator_checks_do_not_pass_before_operands_are_created():
    module = load_module("verify")
    errors = module.operator_errors({"items": [ready_pod("gpu-operator-fixture", "dra-m1")]})
    assert any("nvidia-driver-daemonset-" in error and "dra-a40" in error for error in errors)
    assert any("nvidia-container-toolkit-daemonset-" in error and "dra-4090" in error for error in errors)


def test_restore_rejects_original_daemonset_uid_or_spec_changes():
    module = load_module("install_state")
    original = {"items": [{"metadata": {"name": "cni", "namespace": "kube-system", "uid": "original"},
                            "spec": {"template": {"spec": {"containers": [{"name": "cni", "image": "fixture"}]}}}}]}
    current = json.loads(json.dumps(original))
    assert module.daemonset_errors(original, current) == []
    current["items"][0]["metadata"]["uid"] = "replaced"
    assert any("UID" in error for error in module.daemonset_errors(original, current))
    current = json.loads(json.dumps(original))
    current["items"][0]["spec"]["template"]["spec"]["containers"][0]["image"] = "changed"
    assert any("spec" in error for error in module.daemonset_errors(original, current))


@pytest.mark.parametrize("scenario", ["empty", "foreign-role", "api-error"])
def test_restore_lists_all_namespaced_kinds_before_namespace_deletion(tmp_path, scenario):
    lab = tmp_path / "labs/02-setup"
    shutil.copytree(LAB, lab, ignore=shutil.ignore_patterns("out", "__pycache__"))
    shutil.copytree(ROOT / "labs/common", tmp_path / "labs/common", ignore=shutil.ignore_patterns("out", "__pycache__"))
    backup = lab / "out/fixture"
    backup.mkdir(parents=True)
    for filename in ["original-daemonsets.json", "created-daemonsets.json", "original-nodes.json"]:
        (backup / filename).write_text(json.dumps({"items": []}))
    (backup / "original-nodes.json").write_text(json.dumps({"items": [
        {"metadata": {"name": node, "uid": node + "-fixture"}} for node in ["dra-a40", "dra-4090"]]}))
    binary = tmp_path / "bin"
    binary.mkdir()
    actions = tmp_path / "actions.jsonl"
    fake = binary / "kubectl"
    fake.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
from pathlib import Path
args = [a for a in sys.argv[1:] if not a.startswith('--request-timeout=')]
with Path(os.environ['LAB02_ACTIONS']).open('a') as output:
    output.write(json.dumps(args) + '\\n')
if 'api-resources' in args:
    print('serviceaccounts\\nconfigmaps\\nroles.rbac.authorization.k8s.io\\nevents')
elif 'get' in args:
    resource = args[args.index('get') + 1]
    if resource == 'nodes':
        items = [{'metadata': {'name': node, 'uid': node + '-fixture'}} for node in ['dra-a40', 'dra-4090']]
    elif resource == 'roles.rbac.authorization.k8s.io':
        if os.environ['LAB02_SCENARIO'] == 'api-error':
            raise SystemExit(7)
        items = [{'kind': 'Role', 'metadata': {'name': 'foreign-role'}}] if os.environ['LAB02_SCENARIO'] == 'foreign-role' else []
    elif resource == 'serviceaccounts':
        items = [{'kind': 'ServiceAccount', 'metadata': {'name': 'default'}}]
    elif resource == 'configmaps':
        items = [{'kind': 'ConfigMap', 'metadata': {'name': 'kube-root-ca.crt'}}]
    else:
        items = []
    print(json.dumps({'items': items}))
elif 'delete' not in args and 'label' not in args:
    raise SystemExit('unexpected fake kubectl command')
''')
    fake.chmod(0o755)
    helm = binary / "helm"
    helm.write_text("#!/usr/bin/env bash\n# Releases were already removed in this synthetic restore fixture.\nexit 1\n")
    helm.chmod(0o755)
    env = os.environ.copy()
    env.update(PATH=str(binary) + os.pathsep + env["PATH"], LAB02_SCENARIO=scenario,
               LAB02_ACTIONS=str(actions))
    result = subprocess.run(["bash", str(lab / "scripts/restore.sh"), "fixture"], env=env,
                            capture_output=True, text=True, timeout=10)
    deleted = [json.loads(line) for line in actions.read_text().splitlines() if '"delete"' in line]
    if scenario == "empty":
        assert result.returncode == 0, result.stderr
        assert len(deleted) == 2
    else:
        assert result.returncode != 0
        assert deleted == []
        assert not (backup / "restored.txt").exists()


@pytest.fixture
def simulated_cluster(tmp_path):
    lab = tmp_path / "labs/02-setup"
    shutil.copytree(LAB, lab, ignore=shutil.ignore_patterns("out", "__pycache__"))
    shutil.copytree(ROOT / "labs/common", tmp_path / "labs/common", ignore=shutil.ignore_patterns("out", "__pycache__"))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"applied": False, "released": [], "deleted": [], "early": True, "api_error": False}))
    binary = tmp_path / "bin"
    binary.mkdir()
    fake = binary / "kubectl"
    fake.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
from pathlib import Path
path = Path(os.environ['LAB02_FAKE_STATE'])
state = json.loads(path.read_text())
args = [a for a in sys.argv[1:] if not a.startswith('--request-timeout=')]
def emit(value):
    if value is not None:
        print(json.dumps(value) if isinstance(value, (dict, list)) else value)
def save():
    path.write_text(json.dumps(state))
def role_of(value):
    return 'rtx4090' if 'rtx4090' in value else 'a40'
def pod(role):
    node = 'dra-4090' if role == 'rtx4090' else 'dra-a40'
    return {'metadata': {'name': 'gpu-smoke-'+role+'-fixture'}, 'spec': {'nodeName': node},
            'status': {'phase': 'Succeeded' if role in state['released'] else 'Running',
                       'resourceClaimStatuses': [{'name': 'gpu', 'resourceClaimName': 'generated-'+role}]}}
def claim(role):
    if role in state['deleted'] or (state['early'] and role in state['released']):
        return None
    node = 'dra-4090' if role == 'rtx4090' else 'dra-a40'
    return {'metadata': {'name': 'generated-'+role}, 'status': {'allocation': {'devices': {
            'results': [{'driver': 'gpu.nvidia.com', 'pool': node, 'device': 'gpu-0'}]}}}}
if 'apply' in args:
    state['applied'] = True; save(); emit('applied')
elif 'exec' in args:
    if 'nvidia-smi' in args:
        emit('0')
    else:
        state['released'].append(role_of(' '.join(args))); save()
elif 'wait' in args:
    emit('condition met')
elif 'logs' in args:
    role = role_of(' '.join(args))
    emit('PYTORCH_CUDA_OK '+('NVIDIA A40' if role == 'a40' else 'NVIDIA GeForce RTX 4090'))
elif 'delete' in args:
    index = args.index('delete')
    if '-l' in args and index+2 < len(args) and not args[index+2].startswith('-'):
        raise SystemExit('name cannot be provided when a selector is specified')
    role = role_of(' '.join(args))
    state['deleted'].append(role); save(); emit('deleted')
elif 'get' in args:
    index = args.index('get'); resource = args[index+1]
    name = args[index+2] if index+2 < len(args) and not args[index+2].startswith('-') else ''
    output = args[args.index('-o')+1] if '-o' in args else ''
    if resource == 'namespace':
        if state['applied']:
            emit('dra-book' if output.startswith('jsonpath') else 'namespace/gpu-setup-lab')
    elif resource.startswith('resourceclaims') and '-A' in args:
        emit({'items': []})
    elif resource == 'node':
        emit({'metadata': {'name': name, 'labels': {'kubernetes.io/hostname': name}}})
    elif resource == 'pods':
        selector = args[args.index('-l')+1]
        if selector == 'app=nvidia-driver-daemonset':
            node = args[args.index('--field-selector')+1].split('=')[1]
            emit({'items': [{'metadata': {'name': 'driver-fixture'}, 'spec': {'nodeName': node,
                  'containers': [{'name': 'nvidia-driver-ctr'}]}, 'status': {'phase': 'Running',
                  'containerStatuses': [{'name': 'nvidia-driver-ctr', 'ready': True}]}}]})
        else:
            emit({'items': [pod(role_of(selector))]})
    elif resource == 'resourceclaim':
        role = role_of(name)
        if role in state['deleted'] and state['api_error']:
            raise SystemExit(7)
        obj = claim(role)
        emit(('gpu-0' if output.startswith('jsonpath') else 'resourceclaim/'+name) if obj and output != 'json' and output != 'yaml' else obj)
    elif resource == 'job':
        role = role_of(name)
        if state['applied'] and output.startswith('jsonpath'):
            emit('Complete=True')
    elif resource == 'configmaps,resourceclaimtemplates.resource.k8s.io':
        emit({'items': [{'kind': 'ConfigMap', 'metadata': {'name': 'gpu-setup-workload'}}]
              if state.get('foreign_configmap') else []})
    elif resource == 'pod':
        role = role_of(name)
        if role not in state['deleted']:
            emit('pod/'+name if output == 'name' else pod(role))
    else:
        emit({'items': []})
else:
    raise SystemExit('unexpected fake kubectl command')
''')
    fake.chmod(0o755)
    env = os.environ.copy()
    env.update(PATH=str(binary) + os.pathsep + env["PATH"], LAB02_FAKE_STATE=str(state),
               WAIT_INTERVAL="0.01", SETUP_TIMEOUT="3", SMOKE="1")
    return lab, state, env


@pytest.mark.parametrize("early", [True, False])
def test_run_collects_allocated_claims_and_records_both_deletion_orders(simulated_cluster, early):
    lab, state, env = simulated_cluster
    config = json.loads(state.read_text())
    config["early"] = early
    state.write_text(json.dumps(config))
    result = subprocess.run(["bash", str(lab / "scripts/run.sh"), "fixture"], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    output = lab / "out/fixture"
    summary = json.loads((output / "summary.json").read_text())
    for row in summary["gpus"]:
        assert row["claim_allocated"] and row["pytorch_cuda_ok"]
        assert row["claim_absent_before_job_deletion"] == early
        assert row["claim_deleted_after_pod_deletion"] != early
        assert (output / f'{row["role"]}-claim-allocated.yaml').is_file()
        assert (output / f'{row["role"]}-claim-completed.yaml').is_file()
    assert (output / "events.yaml").is_file()
    again = subprocess.run(["bash", str(lab / "scripts/run.sh"), "fixture"], env=env,
                           capture_output=True, text=True, timeout=5)
    assert again.returncode == 0


def test_run_does_not_treat_api_error_as_claim_disappearance(simulated_cluster):
    lab, state, env = simulated_cluster
    config = json.loads(state.read_text())
    config.update(api_error=True, early=False)
    state.write_text(json.dumps(config))
    result = subprocess.run(["bash", str(lab / "scripts/run.sh"), "fixture"], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    summary = json.loads((lab / "out/fixture/summary.json").read_text())
    assert "error" in summary
    assert not summary["gpus"][0]["claim_deleted_at"]
    again = subprocess.run(["bash", str(lab / "scripts/run.sh"), "fixture"], env=env,
                           capture_output=True, text=True, timeout=5)
    assert again.returncode != 0


def test_run_does_not_adopt_a_foreign_named_manifest_object(simulated_cluster):
    lab, state, env = simulated_cluster
    config = json.loads(state.read_text())
    config["foreign_configmap"] = True
    state.write_text(json.dumps(config))
    result = subprocess.run(["bash", str(lab / "scripts/run.sh"), "fixture"], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert not json.loads(state.read_text())["applied"]
