import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from conftest import ROOT

COMMON = ROOT / "labs/common"


@pytest.fixture
def fake_kubectl(tmp_path):
    fixtures = {
        "claims": {"items": []},
        "node": {"metadata": {"name": "worker", "labels": {"kubernetes.io/hostname": "worker", "zone": "east"}}},
        "pods": {"items": [{"metadata": {"name": "driver-fixture"}, "spec": {
            "nodeName": "worker", "containers": [{"name": "nvidia-driver-ctr"}]},
            "status": {"phase": "Running", "containerStatuses": [{"name": "nvidia-driver-ctr", "ready": True}]}}]},
        "memory": "0\n", "exit": 0,
    }
    state = tmp_path / "state.json"
    state.write_text(json.dumps(fixtures))
    binary = tmp_path / "bin"
    binary.mkdir()
    executable = binary / "kubectl"
    executable.write_text(f"#!{sys.executable}\n" + '''import json, os, sys, time
state = json.load(open(os.environ['FAKE_KUBE_STATE']))
args = [a for a in sys.argv[1:] if not a.startswith('--request-timeout=')]
if state.get('exit', 0):
    raise SystemExit(state['exit'])
if 'get' in args:
    resource = args[args.index('get') + 1]
    if resource.startswith('resourceclaims'):
        print(json.dumps(state['claims']))
    elif resource == 'node':
        print(json.dumps(state['node']))
    elif resource == 'pods':
        if '-l' not in args or args[args.index('-l') + 1] != 'app=nvidia-driver-daemonset':
            raise SystemExit(9)
        print(json.dumps(state['pods']))
    elif resource == 'job':
        print(state.get('conditions', ''))
    elif resource == 'pod':
        print(state.get('phase', 'Pending'))
    else:
        raise SystemExit(9)
elif 'exec' in args:
    if '-c' not in args or args[args.index('-c') + 1] != 'nvidia-driver-ctr':
        raise SystemExit(9)
    if '-lms' in args:
        if args[args.index('-lms') + 1] != '500':
            raise SystemExit(9)
        print('timestamp, uuid, memory.used [MiB]', flush=True)
        while True:
            print('synthetic, GPU-fixture, 0', flush=True)
            time.sleep(0.05)
    else:
        print(state['memory'], end='')
else:
    print('ok')
''')
    executable.chmod(0o755)
    env = os.environ.copy()
    env.update(PATH=str(binary) + os.pathsep + env["PATH"], FAKE_KUBE_STATE=str(state))
    return env, state, fixtures


def run_script(script, args, env):
    return subprocess.run(["bash", str(COMMON / script), *args], env=env,
                          text=True, capture_output=True, timeout=10)


def test_command_wrapper_preserves_streams_exit_status_and_run_layout(fake_kubectl, tmp_path):
    env, state, fixtures = fake_kubectl
    env["LAB_TEST_DIR"] = str(tmp_path / "lab")
    command = 'source "$1"; init_run "$LAB_TEST_DIR" fixed-run; kc get pods -l app=nvidia-driver-daemonset; printf "%s\\n" "$OUT_DIR"'
    result = subprocess.run(["bash", "-c", command, "test", str(COMMON / "lib.sh")],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0 and "driver-fixture" in result.stdout
    log = tmp_path / "lab/out/fixed-run/commands.log"
    assert "kubectl" in log.read_text() and "exit=0" in log.read_text()
    fixtures["exit"] = 7
    state.write_text(json.dumps(fixtures))
    command = 'source "$1"; init_run "$LAB_TEST_DIR" fixed-run; kc get pods'
    result = subprocess.run(["bash", "-c", command, "test", str(COMMON / "lib.sh")], env=env)
    assert result.returncode == 7 and "exit=7" in log.read_text()


def test_wait_timeout_is_bounded_and_job_failure_returns_early(fake_kubectl, tmp_path):
    env, state, fixtures = fake_kubectl
    env.update(OUT_DIR=str(tmp_path / "out"), WAIT_INTERVAL="0.01")
    result = subprocess.run(["bash", "-c", 'source "$1"; wait_pod_phase ns work Running 0', "test",
                             str(COMMON / "lib.sh")], env=env, capture_output=True, text=True, timeout=3)
    assert result.returncode != 0 and "timeout" in result.stderr.lower()
    fixtures["conditions"] = "Failed=True\n"
    state.write_text(json.dumps(fixtures))
    result = subprocess.run(["bash", "-c", 'source "$1"; wait_job_complete ns work 10', "test",
                             str(COMMON / "lib.sh")], env=env, capture_output=True, text=True, timeout=3)
    assert result.returncode == 1 and "Job failed" in result.stderr


def test_idle_check_passes_and_fails_on_memory_claims_and_query_failure(fake_kubectl, tmp_path):
    env, state, fixtures = fake_kubectl
    env["OUT_DIR"] = str(tmp_path / "out")
    assert run_script("gpu-idle-check.sh", ["worker"], env).returncode == 0
    fixtures["memory"] = "200\n"
    state.write_text(json.dumps(fixtures))
    result = run_script("gpu-idle-check.sh", ["worker"], env)
    assert result.returncode == 1 and "FAIL" in result.stdout + result.stderr
    fixtures["memory"] = "0\n"
    fixtures["claims"]["items"] = [{"metadata": {"namespace": "ns", "name": "allocated"}, "status": {
        "allocation": {"devices": {"results": [{"driver": "gpu.nvidia.com", "pool": "worker", "device": "gpu-0"}]},
                       "nodeSelector": {"nodeSelectorTerms": [{"matchFields": [
                           {"key": "metadata.name", "operator": "In", "values": ["worker"]}]}]}}}}]
    state.write_text(json.dumps(fixtures))
    result = run_script("gpu-idle-check.sh", ["worker"], env)
    assert result.returncode == 1 and "ns/allocated" in result.stdout + result.stderr
    fixtures["exit"] = 7
    state.write_text(json.dumps(fixtures))
    result = run_script("gpu-idle-check.sh", ["worker"], env)
    assert result.returncode == 1 and "FAIL" in result.stdout + result.stderr


def test_claim_selectors_and_unknown_allocations():
    spec = importlib.util.spec_from_file_location("gpu_check", COMMON / "gpu_check.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    node = {"metadata": {"name": "worker", "labels": {"zone": "east"}}}
    other = {"metadata": {"namespace": "ns", "name": "other"}, "status": {"allocation": {
        "devices": {"results": [{"device": "gpu-0"}]}, "nodeSelector": {"nodeSelectorTerms": [
            {"matchExpressions": [{"key": "zone", "operator": "In", "values": ["west"]}]}]}}}}
    assert module.blocking_claims({"items": [other]}, node) == []
    del other["status"]["allocation"]["nodeSelector"]
    assert module.blocking_claims({"items": [other]}, node)


def test_sampler_start_stop_and_repeated_stop_are_safe(fake_kubectl, tmp_path):
    env, _, _ = fake_kubectl
    env["OUT_DIR"] = str(tmp_path / "out")
    output = tmp_path / "out/gpu.csv"
    started = run_script("gpu-sample.sh", ["start", "worker", str(output)], env)
    assert started.returncode == 0, started.stderr
    pid_file = Path(str(output) + ".pid")
    assert pid_file.is_file()
    try:
        assert "GPU-fixture" in output.read_text()
        assert run_script("gpu-sample.sh", ["start", "worker", str(output)], env).returncode == 0
        result = run_script("gpu-sample.sh", ["stop", "worker", str(output)], env)
        assert result.returncode == 0, result.stderr
        assert not pid_file.exists()
        before = output.read_bytes()
        time.sleep(0.1)
        assert output.read_bytes() == before
        assert "-lms 500" in (output.parent / "commands.log").read_text()
        assert run_script("gpu-sample.sh", ["stop", "worker", str(output)], env).returncode == 0
    finally:
        if pid_file.exists():
            run_script("gpu-sample.sh", ["stop", "worker", str(output)], env)


def test_sampler_stop_request_is_atomically_published(fake_kubectl, tmp_path, monkeypatch):
    env, _, _ = fake_kubectl
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setenv("FAKE_KUBE_STATE", env["FAKE_KUBE_STATE"])
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    spec = importlib.util.spec_from_file_location("gpu_sample", COMMON / "gpu_sample.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "out/gpu.csv"
    assert module.main(["start", "worker", str(output)]) == 0
    original_write = Path.write_text
    def slow_write(path, content, *args, **kwargs):
        if ".stop" in path.name:
            original_write(path, "")
            time.sleep(0.2)  # Supervisor polls while the write is incomplete.
        return original_write(path, content, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", slow_write)
    assert module.main(["stop", "worker", str(output)]) == 0
    assert not Path(str(output) + ".pid").exists()
