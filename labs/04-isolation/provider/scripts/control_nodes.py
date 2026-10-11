#!/usr/bin/env python3
"""Additive node-list provider operations; reuse the original UID/spec checks."""
import argparse
import copy
import datetime
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from urllib.parse import quote

PROVIDER = Path(__file__).resolve().parents[1]
original_spec = importlib.util.spec_from_file_location("native_provider_node_checks", PROVIDER / "control.py")
original_control = importlib.util.module_from_spec(original_spec)
original_spec.loader.exec_module(original_control)

NS = original_control.NAMESPACE
DS = original_control.DAEMONSET
BOOK = "app.kubernetes.io/part-of"
INSTANCE = "app.kubernetes.io/instance"


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def load(path):
    return json.loads(Path(path).read_text())


def json_response(text):
    """Normalize kubectl's per-object JSON output into a Kubernetes List."""
    values, decoder = [], json.JSONDecoder()
    text = text.strip()
    while text:
        value, consumed = decoder.raw_decode(text)
        values.append(value)
        text = text[consumed:].lstrip()
    if len(values) == 1:
        return values[0]
    return {"apiVersion": "v1", "kind": "List", "items": [item for value in values for item in value.get("items", [value])]} if values else {}


def node_list(value):
    nodes = value.replace(",", " ").split()
    if not nodes or len(nodes) != len(set(nodes)) or not set(nodes) <= {"dra-a40", "dra-4090"}:
        raise ValueError("NODES must list dra-a40 and/or dra-4090 once each")
    return sorted(nodes)


class Kube:
    def __init__(self, out):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)

    def call(self, *args, json_result=False):
        command = ["kubectl", "--request-timeout=25s", *map(str, args)]
        with (self.out / "commands.log").open("a") as handle:
            handle.write(utc() + " " + shlex.join(command) + "\n")
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "kubectl failed")
        return json_response(result.stdout) if json_result else result.stdout

    def get(self, kind, *args):
        return self.call("get", kind, *args, "-o", "json", json_result=True)

    def wait(self, check, message, timeout=300):
        end = time.monotonic() + timeout
        while True:
            result = check()
            if result:
                return result
            if time.monotonic() >= end:
                raise TimeoutError(message)
            time.sleep(1)

    def guarded_delete(self, obj):
        meta, kind = obj["metadata"], obj["kind"]
        current = self.get(kind, meta["name"], *( ["-n", meta["namespace"]] if meta.get("namespace") else []), "--ignore-not-found")
        if not current:
            return
        if original_control.identity(current) != original_control.identity(obj) or any(current.get(field) != obj.get(field) for field in ("spec", "rules", "subjects", "roleRef")):
            raise ValueError("object UID/spec changed; refusing deletion: " + meta["name"])
        suffix = {"Pod": "pods", "ResourceSlice": "resourceslices", "DaemonSet": "daemonsets", "DeviceClass": "deviceclasses", "ClusterRole": "clusterroles", "ClusterRoleBinding": "clusterrolebindings"}[kind]
        prefix = "/api/v1" if obj["apiVersion"] == "v1" else "/apis/" + obj["apiVersion"]
        uri = prefix + ("/namespaces/" + quote(meta["namespace"], safe="") if meta.get("namespace") else "") + "/" + suffix + "/" + quote(meta["name"], safe="")
        options = self.out / ("delete-" + kind + "-" + meta["name"] + ".json")
        save(options, original_control.delete_options(current))
        try:
            self.call("delete", "--raw", uri, "-f", options)
        except RuntimeError as error:
            if "(Conflict)" not in str(error) and "(NotFound)" not in str(error):
                raise
            rechecked = self.get(kind, meta["name"], *(["-n", meta["namespace"]] if meta.get("namespace") else []), "--ignore-not-found")
            if rechecked and (original_control.identity(rechecked) != original_control.identity(obj) or not rechecked["metadata"].get("deletionTimestamp")):
                raise ValueError("DELETE conflict did not leave the recorded UID terminating") from error


class NativeNodes:
    def __init__(self, out, nodes):
        self.out, self.nodes = Path(out), nodes
        self.kube = Kube(out)
        self.backup_dir = self.out / "provider-nodes-backup"
        self.state = load(self.backup_dir / "backup.json") if (self.backup_dir / "backup.json").exists() else None
        if self.state and self.state["nodes"] != nodes:
            raise ValueError("NODES differs from the selected original backup")

    def baseline(self):
        if not self.state:
            raise ValueError("complete original provider backup is required")
        obj = load(self.backup_dir / "original-daemonset.json")
        if obj["metadata"]["uid"] != self.state["uid"]:
            raise ValueError("backup DaemonSet UID differs from the saved baseline")
        return obj

    def excluded_spec(self, spec):
        for node in self.nodes:
            spec = original_control.excluded_spec(spec, node)
        return spec

    def phase(self, current):
        baseline = self.baseline()
        if original_control.identity(current) != original_control.identity(baseline):
            raise ValueError("original DaemonSet UID changed; refusing mutation")
        choices = {"original": baseline["spec"], "excluded": self.excluded_spec(baseline["spec"]),
                   "resumed": original_control.resumed_spec(baseline["spec"])}
        for name, spec in choices.items():
            if original_control.canonical_spec(current["spec"]) == original_control.canonical_spec(spec):
                return name
        raise ValueError("original DaemonSet spec changed; refusing concurrent overwrite")

    def protected(self, pods):
        baseline = self.baseline()
        for node, recorded in self.state["protected_pods"].items():
            current = [original_control.identity(p) for p in original_control.owned_pods(pods, baseline, node)]
            if current != recorded:
                raise ValueError("unselected native plugin UID changed: " + node)

    def require_unallocated(self, restoring=False):
        claims = self.kube.get("resourceclaims", "-A")
        save(self.out / "claims-preflight.json", claims)
        for claim in claims["items"]:
            rows = ((claim.get("status", {}).get("allocation") or {}).get("devices", {}).get("results") or [])
            if any(row.get("pool") in self.nodes for row in rows) or (restoring and claim["metadata"].get("namespace") in ("gpu-isolation-lab", "gpu-heterogeneous-lab")):
                raise ValueError("ResourceClaim remains; collect and cleanup GPU workloads first")

    def driver(self, node):
        pods = self.kube.get("pods", "-n", "gpu-operator", "-l", "app=nvidia-driver-daemonset", "--field-selector", "spec.nodeName=" + node)["items"]
        ready = [p for p in pods if not p["metadata"].get("deletionTimestamp") and any(c.get("type") == "Ready" and c.get("status") == "True" for c in p.get("status", {}).get("conditions", []))]
        if len(ready) != 1:
            raise ValueError("expected one ready GPU Operator driver Pod on " + node)
        return ["-n", "gpu-operator", "exec", ready[0]["metadata"]["name"], "-c", "nvidia-driver-ctr", "--"]

    def idle(self):
        self.require_unallocated()
        for node in self.nodes:
            if self.kube.get("node", node)["metadata"]["uid"] != self.state["node_uids"][node]:
                raise ValueError("target Node UID changed")
            directory = self.backup_dir / node
            query = "uuid,name,memory.total,memory.used,memory.free,utilization.gpu,compute_mode"
            (directory / "idle-gpu.csv").write_text(self.kube.call(*self.driver(node), "nvidia-smi", "--query-gpu=" + query, "--format=csv,noheader,nounits"))
            (directory / "idle-processes.csv").write_text(self.kube.call(*self.driver(node), "nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"))
            original_control.check_idle(directory, directory / "idle-gpu.csv", directory / "idle-processes.csv")

    def backup(self):
        if self.state:
            self.phase(self.kube.get("daemonset", DS, "-n", NS))
            self.protected(self.kube.get("pods", "-A"))
            return
        if self.backup_dir.exists():
            raise ValueError("incomplete backup exists; retain it and choose a new run-id")
        self.backup_dir.mkdir()
        baseline = self.kube.get("daemonset", DS, "-n", NS)
        pods, slices = self.kube.get("pods", "-A"), self.kube.get("resourceslices")
        if baseline["spec"].get("updateStrategy", {}).get("type") != "RollingUpdate":
            raise ValueError("native provider is already suspended; use its existing backup")
        self.excluded_spec(baseline["spec"])
        protected = {node: [original_control.identity(p) for p in original_control.owned_pods(pods, baseline, node)] for node in {"dra-a40", "dra-4090"} - set(self.nodes)}
        if any(len(recorded) != 1 for recorded in protected.values()):
            raise ValueError("expected one native plugin on every unselected GPU node")
        target_pods, node_uids = {}, {}
        for node in self.nodes:
            found = original_control.owned_pods(pods, baseline, node)
            published = original_control.node_slices(slices, node)
            if len(found) != 1 or not published or any(sl["spec"]["driver"] != original_control.DRIVER for sl in published):
                raise ValueError("expected one original native plugin and native ResourceSlices on " + node)
            target_pods[node] = original_control.identity(found[0])
            node_uids[node] = self.kube.get("node", node)["metadata"]["uid"]
        self.state = {"nodes": self.nodes, "uid": baseline["metadata"]["uid"], "node_uids": node_uids,
                      "target_pods": target_pods, "protected_pods": protected, "created_at": utc()}
        save(self.backup_dir / "original-daemonset.json", baseline)
        save(self.backup_dir / "original-pods.json", pods)
        save(self.backup_dir / "original-slices.json", slices)
        for node in self.nodes:
            # check_idle/read_backup reuse the original single-node evidence format.
            directory = self.backup_dir / node
            save(directory / "original-daemonset.json", baseline)
            save(directory / "original-slices.json", slices)
            save(directory / "update-strategy.json", baseline["spec"]["updateStrategy"])
            (directory / "uid.txt").write_text(self.state["uid"] + "\n")
            save(directory / "backup.json", {"daemonset": original_control.identity(baseline), "target_node": node})
        save(self.backup_dir / "backup.json", self.state)
        for node in self.nodes:
            original_control.check_alternatives(pods, self.kube.get("daemonsets", "-A"), baseline, {"target_node": node})
        self.idle()

    def patch(self, wanted):
        current = self.kube.get("daemonset", DS, "-n", NS)
        self.phase(current)
        self.protected(self.kube.get("pods", "-A"))
        if original_control.canonical_spec(current["spec"]) == original_control.canonical_spec(wanted):
            return
        patch = [{"op": "test", "path": "/metadata/uid", "value": self.state["uid"]},
                 {"op": "test", "path": "/spec", "value": current["spec"]},
                 {"op": "replace", "path": "/spec", "value": wanted}]
        path = self.out / "native-patch.json"
        save(path, patch)
        self.kube.call("patch", "daemonset", DS, "-n", NS, "--type=json", "--patch-file", path)

    def native_absent(self):
        baseline = self.baseline()
        pods = self.kube.get("pods", "-A")
        self.protected(pods)
        for node in self.nodes:
            found = original_control.owned_pods(pods, baseline, node)
            if found and (len(found) != 1 or original_control.identity(found[0]) != self.state["target_pods"][node]):
                raise ValueError("native plugin UID changed while waiting for deletion")
            if found:
                return False
        return True

    def exclude(self):
        self.backup()
        self.idle()
        self.patch(self.excluded_spec(self.baseline()["spec"]))
        pods = self.kube.get("pods", "-A")
        for node in self.nodes:
            for pod in original_control.owned_pods(pods, self.baseline(), node):
                if original_control.identity(pod) != self.state["target_pods"][node]:
                    raise ValueError("native plugin UID changed; refusing DELETE")
                self.kube.guarded_delete(pod)
        self.kube.wait(self.native_absent, "terminating native plugin Pods did not disappear")
        self.idle()
        baseline = {sl["metadata"]["uid"]: sl for sl in load(self.backup_dir / "original-slices.json")["items"]}
        selected = [sl for sl in self.kube.get("resourceslices")["items"] if sl["spec"].get("nodeName") in self.nodes]
        for sl in selected:
            prior = baseline.get(sl["metadata"]["uid"])
            if not prior or prior["spec"] != sl["spec"]:
                raise ValueError("native ResourceSlice UID/spec changed")
            self.check_slice(sl, original_control.DRIVER)
        for sl in selected:
            self.kube.guarded_delete(sl)
        self.kube.wait(lambda: not any(sl["spec"].get("nodeName") in self.nodes for sl in self.kube.get("resourceslices")["items"]), "native ResourceSlices remain")
        save(self.out / "native-excluded.json", {"nodes": self.nodes, "uid": self.state["uid"], "observed_at": utc()})

    def check_slice(self, sl, driver):
        node, spec = sl["spec"].get("nodeName"), sl["spec"]
        uuids = original_control.target_uuids(self.backup_dir / node, {"target_node": node})
        if spec.get("driver") != driver or spec.get("pool", {}).get("name") != node or not spec.get("devices") or any(d.get("attributes", {}).get("uuid", {}).get("string") not in uuids for d in spec["devices"]):
            raise ValueError("ResourceSlice does not describe the recorded GPU")
        if not any(ref.get("kind") == "Node" and ref.get("name") == node and ref.get("uid") == self.state["node_uids"][node] for ref in sl["metadata"].get("ownerReferences", [])):
            raise ValueError("ResourceSlice Node owner UID changed")

    def restored_ready(self):
        baseline, pods, slices = self.baseline(), self.kube.get("pods", "-A"), self.kube.get("resourceslices")
        self.protected(pods)
        for node in self.nodes:
            found = original_control.owned_pods(pods, baseline, node)
            if len(found) != 1 or found[0]["metadata"].get("deletionTimestamp") or not any(c.get("type") == "Ready" and c.get("status") == "True" for c in found[0].get("status", {}).get("conditions", [])):
                return False
            if original_control.slice_specs(slices, node) != original_control.slice_specs(load(self.backup_dir / "original-slices.json"), node):
                return False
        return True

    def restore(self):
        self.require_unallocated(restoring=True)
        phase = self.phase(self.kube.get("daemonset", DS, "-n", NS))
        for node in self.nodes:
            original_control.check_alternatives(self.kube.get("pods", "-A"), self.kube.get("daemonsets", "-A"), self.baseline(), {"target_node": node})
        if phase == "excluded":
            self.kube.wait(self.native_absent, "terminating original plugins remain; finish exclusion first")
            if any(sl["spec"].get("nodeName") in self.nodes for sl in self.kube.get("resourceslices")["items"]):
                raise ValueError("alternative ResourceSlices remain; stop HAMi and remove its recorded slices first")
            self.idle()
        self.patch(original_control.resumed_spec(self.baseline()["spec"]))
        self.kube.wait(self.restored_ready, "native plugins and original GPU ResourceSlices did not return")
        self.patch(self.baseline()["spec"])
        self.kube.wait(self.restored_ready, "native provider did not remain ready")
        restored = self.kube.get("daemonset", DS, "-n", NS)
        if self.phase(restored) != "original":
            raise ValueError("restored native DaemonSet UID/spec differs from original backup")
        save(self.out / "restored-daemonset.json", restored)
        save(self.out / "native-restored.json", {"nodes": self.nodes, "uid": self.state["uid"], "uid_spec_match": True, "observed_at": utc()})


def main():
    if os.environ.get("I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER") != "1":
        raise ValueError("set I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "exclude", "restore"))
    parser.add_argument("run_id")
    args = parser.parse_args()
    nodes = node_list(os.environ.get("NODES", "dra-a40"))
    if not args.run_id or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for c in args.run_id) or args.run_id in (".", ".."):
        raise ValueError("invalid run-id")
    out = Path(os.environ.get("OUT_DIR", str(PROVIDER.parent / "out" / args.run_id)))
    session = NativeNodes(out, nodes)
    lock = out / ".provider-nodes-lock"
    lock.mkdir()
    try:
        getattr(session, args.action)()
    finally:
        lock.rmdir()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, TimeoutError, FileExistsError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
