#!/usr/bin/env python3
"""Node-list HAMi installation with the Chapter 4 provider and host checks."""
import argparse
import copy
import importlib.util
import os
from pathlib import Path
import re
import sys

HAMI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HAMI.parent / "provider" / "scripts"))
from control_nodes import BOOK, DS, INSTANCE, NS, NativeNodes, load, node_list, original_control, save, utc

module_spec = importlib.util.spec_from_file_location("hami_multi_checks", HAMI / "analysis/control.py")
checks = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(checks)
HOST_IMAGE = "docker.io/pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755"
DRIVER = "hami-core-gpu.project-hami.io"


class HamiNodes:
    def __init__(self, out, run_id, nodes):
        self.out, self.run_id, self.nodes = Path(out), run_id, nodes
        self.native = NativeNodes(out, nodes)
        self.kube = self.native.kube
        self.expected = self.out / "provider-nodes-expected.json"
        self.owned = self.out / "provider-nodes-owned.json"
        self.helpers = {}

    def host_start(self, node):
        directory = self.out / node
        directory.mkdir(exist_ok=True)
        recorded = directory / "host-helper-created.json"
        if recorded.exists():
            old = load(recorded)
            current = self.kube.get("pod", old["metadata"]["name"], "-n", NS, "--ignore-not-found")
            if current:
                checks.c.same_pod(old, current)
                self.helpers[node] = current
                return
        before = self.kube.get("pods", "-n", NS, "--field-selector", "spec.nodeName=" + node)
        save(directory / "host-pods-before.json", before)
        self.kube.call("debug", "node/" + node, "-n", NS, "--profile=sysadmin", "--image=" + HOST_IMAGE,
                       "--image-pull-policy=IfNotPresent", "--attach=false", "--container=hami-host-helper",
                       "--env=NVIDIA_VISIBLE_DEVICES=void", "--", "bash", "-c", "sleep 3600")
        old_uids = {item["metadata"]["uid"] for item in before["items"]}
        current = self.kube.get("pods", "-n", NS, "--field-selector", "spec.nodeName=" + node)
        found = [pod for pod in current["items"] if pod["metadata"]["uid"] not in old_uids and len(pod["spec"]["containers"]) == 1
                 and pod["spec"]["containers"][0]["name"] == "hami-host-helper" and pod["spec"]["containers"][0]["image"] == HOST_IMAGE]
        if len(found) != 1:
            raise ValueError("cannot identify one new pinned host helper on " + node)
        pod = found[0]
        if pod["spec"].get("nodeName") != node or not pod["spec"]["containers"][0].get("securityContext", {}).get("privileged") or not any(v.get("hostPath", {}).get("path") == "/" for v in pod["spec"].get("volumes", [])):
            raise ValueError("host helper node/privileged/root-mount identity differs")
        labels = dict(pod["metadata"].get("labels", {}))
        labels.update({BOOK: "dra-book", INSTANCE: self.run_id, "app.kubernetes.io/component": "hami-host-helper"})
        patch = [{"op": "test", "path": "/metadata/uid", "value": pod["metadata"]["uid"]},
                 {"op": "test", "path": "/spec", "value": pod["spec"]},
                 {"op": "add", "path": "/metadata/labels", "value": labels}]
        pod = self.kube.call("patch", "pod", pod["metadata"]["name"], "-n", NS, "--type=json", "-p", __import__("json").dumps(patch), "-o", "json", json_result=True)
        save(recorded, pod)
        self.helpers[node] = pod
        self.kube.call("wait", "pod/" + pod["metadata"]["name"], "-n", NS, "--for=condition=Ready", "--timeout=300s")

    def host(self, node, payload):
        import json
        self.host_start(node)
        pod = self.helpers[node]
        current = self.kube.get("pod", pod["metadata"]["name"], "-n", NS)
        checks.c.same_pod(pod, current)
        return self.kube.call("exec", pod["metadata"]["name"], "-n", NS, "-c", "hami-host-helper", "--", "python", "-c", (HAMI / "scripts/host.py").read_text(), json.dumps(payload), json_result=True)["result"]

    def stop_helpers(self):
        failures = []
        for node, pod in list(self.helpers.items()):
            try:
                self.kube.guarded_delete(pod)
                self.kube.wait(lambda: not self.kube.get("pod", pod["metadata"]["name"], "-n", NS, "--ignore-not-found"), "host helper remains", 120)
                del self.helpers[node]
            except Exception as error:
                failures.append(node + "/" + pod["metadata"]["name"] + ": " + str(error))
        if failures:
            raise RuntimeError("Host helper cleanup failed: " + "; ".join(failures) +
                               ". Snapshots retained; retry with the same provider run-id.")

    def render(self):
        (self.out / "provider-input.yaml").write_text(self.kube.call("kustomize", HAMI / "manifests/provider"))
        rendered = self.kube.call("create", "--dry-run=client", "--validate=false", "-f", self.out / "provider-input.yaml", "-o", "json", json_result=True)
        items, base = [], None
        for item in rendered.get("items", [rendered]):
            item["metadata"].setdefault("labels", {})[INSTANCE] = self.run_id
            if item["kind"] == "DaemonSet":
                base = item
            else:
                if item["kind"] in ("ClusterRole", "ClusterRoleBinding"):
                    item["metadata"]["name"] = "node-list-hami-provider"
                if item["kind"] == "ClusterRoleBinding":
                    item["roleRef"]["name"] = "node-list-hami-provider"
                items.append(item)
        if base is None:
            raise ValueError("Chapter 4 HAMi DaemonSet is missing")
        for node in self.nodes:
            obj = copy.deepcopy(base)
            obj["metadata"]["name"] = node + "-hami-provider"
            labels = obj["spec"]["template"]["metadata"]["labels"]
            labels.update({INSTANCE: self.run_id, "dra-book.io/gpu-node": node})
            obj["spec"]["selector"]["matchLabels"] = dict(labels)
            terms = obj["spec"]["template"]["spec"]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
            for term in terms:
                for match in term.get("matchFields", []):
                    if match["key"] == "metadata.name" and match["operator"] == "In":
                        match["values"] = [node]
            items.append(obj)
        expected = {"apiVersion": "v1", "kind": "List", "items": items}
        if self.expected.exists() and load(self.expected) != expected:
            raise ValueError("provider source differs from the recorded input; preserve this run")
        save(self.expected, expected)
        return expected

    def existing(self):
        current = self.kube.get("-f", self.expected, "--ignore-not-found")
        if not current:
            current = {"items": []}
        elif "items" not in current:
            current = {"items": [current]}
        checks.provider_set(current, str(self.expected), self.run_id)
        if current["items"]:
            if not self.owned.exists():
                raise ValueError("provider objects predate this owned run; refusing adoption")
            prior = {(obj["kind"], obj["metadata"]["name"]): obj["metadata"]["uid"] for obj in load(self.owned)["items"]}
            if any(prior.get((obj["kind"], obj["metadata"]["name"])) != obj["metadata"]["uid"] for obj in current["items"]):
                raise ValueError("provider object UID changed; refusing adoption or deletion")
        return current

    def owned_publications(self, slices):
        path = self.out / "provider-nodes-recorded-slices.json"
        if not path.exists():
            path = self.out / "provider-nodes-ready-slices.json"
        recorded = load(path)["items"] if path.exists() else []
        by_uid = {sl["metadata"]["uid"]: sl for sl in recorded if sl["spec"].get("nodeName") in self.nodes}
        result = []
        for sl in slices["items"]:
            if sl["spec"].get("nodeName") not in self.nodes:
                continue
            self.native.check_slice(sl, DRIVER)
            prior = by_uid.get(sl["metadata"]["uid"])
            if not prior or original_control.identity(prior) != original_control.identity(sl) or prior["spec"] != sl["spec"]:
                raise ValueError("HAMi publication UID/spec differs from the installation snapshot")
            result.append(prior)
        return result

    def record_publications(self, slices):
        """Preserve the first observed publication even during a partial install."""
        if self.native.phase(self.kube.get("daemonset", DS, "-n", NS)) != "excluded" or not self.native.native_absent():
            raise ValueError("native plugins must be absent before recording HAMi publications")
        providers = [obj for obj in self.existing()["items"] if obj["kind"] == "DaemonSet"]
        own_uids = {obj["metadata"]["uid"] for obj in providers}
        pods, daemonsets = self.kube.get("pods", "-A"), self.kube.get("daemonsets", "-A")
        other_pods = {"items": [pod for pod in pods["items"] if not any(
            owner.get("controller") and owner.get("uid") in own_uids for owner in pod["metadata"].get("ownerReferences", []))]}
        other_daemonsets = {"items": [obj for obj in daemonsets["items"] if obj["metadata"]["uid"] not in own_uids]}
        for node in self.nodes:
            original_control.check_alternatives(other_pods, other_daemonsets, self.native.baseline(), {"target_node": node})
        path = self.out / "provider-nodes-recorded-slices.json"
        prior_path = path if path.exists() else self.out / "provider-nodes-ready-slices.json"
        recorded = [sl for sl in load(prior_path)["items"] if sl["spec"].get("nodeName") in self.nodes] if prior_path.exists() else []
        by_node = {sl["spec"]["nodeName"]: sl for sl in recorded}
        for node in self.nodes:
            selected = original_control.node_slices(slices, node)
            if not selected:
                continue
            producer = [obj for obj in providers if obj["metadata"]["name"] == node + "-hami-provider"]
            if len(selected) != 1 or len(producer) != 1 or len(original_control.owned_pods(pods, producer[0], node)) != 1:
                raise ValueError("cannot prove one owned HAMi producer for the publication on " + node)
            sl = selected[0]
            self.native.check_slice(sl, DRIVER)
            prior = by_node.get(node)
            if prior and (original_control.identity(prior) != original_control.identity(sl) or prior["spec"] != sl["spec"]):
                raise ValueError("HAMi publication UID/spec changed during installation")
            by_node[node] = prior or sl
        save(path, {"items": list(by_node.values()), "observed_at": utc()})

    def ready(self):
        native_phase = self.native.phase(self.kube.get("daemonset", DS, "-n", NS))
        if native_phase != "excluded":
            raise ValueError("native provider is not excluded on the selected nodes")
        self.native.protected(self.kube.get("pods", "-A"))
        self.existing()
        slices = self.kube.get("resourceslices")
        self.record_publications(slices)
        pods = self.kube.get("pods", "-n", NS)
        for node in self.nodes:
            ds = self.kube.get("daemonset", node + "-hami-provider", "-n", NS)
            found = original_control.owned_pods(pods, ds, node)
            if len(found) != 1 or found[0]["metadata"].get("deletionTimestamp") or not any(c.get("type") == "Ready" and c.get("status") == "True" for c in found[0].get("status", {}).get("conditions", [])):
                return False
            selected = original_control.node_slices(slices, node)
            if not selected:
                return False
            for sl in selected:
                self.native.check_slice(sl, DRIVER)
                if any(not d.get("allowMultipleAllocations") or not all(d.get("capacity", {}).get(key, {}).get("requestPolicy") for key in ("memory", "cores")) for d in sl["spec"]["devices"]):
                    raise ValueError("HAMi consumable memory/cores request policy missing")
        save(self.out / "provider-nodes-ready-slices.json", slices)
        return True

    def install(self):
        if (self.out / "provider-nodes-restored.json").exists():
            raise ValueError("this provider run was restored; choose a new provider run-id")
        self.render()
        self.existing()
        self.native.backup()
        self.native.require_unallocated()
        # Check both hosts before changing any provider field.
        for node in self.nodes:
            path = self.out / node / "host-before.json"
            if not path.exists():
                save(path, self.host(node, {"operation": "assert-empty"}))
        if self.native.phase(self.kube.get("daemonset", DS, "-n", NS)) != "excluded":
            self.native.exclude()
        self.native.protected(self.kube.get("pods", "-A"))
        try:
            (self.out / "provider-nodes-apply.txt").write_text(self.kube.call("apply", "-f", self.expected))
        finally:
            # An apply can succeed partly; record only the objects from this exact input.
            current = self.kube.get("-f", self.expected, "--ignore-not-found") or {"items": []}
            if "items" not in current:
                current = {"items": [current]}
            checks.provider_set(current, str(self.expected), self.run_id)
            save(self.owned, current)
            for node in self.nodes:
                footprint = self.host(node, {"operation": "footprint"})
                path = self.out / node / "host-installed.json"
                if not path.exists():
                    save(path, footprint)
            self.record_publications(self.kube.get("resourceslices"))
        self.kube.wait(self.ready, "HAMi plugins and both consumable publications did not become Ready", 300)
        for node in self.nodes:
            footprint = self.host(node, {"operation": "footprint"})
            library = next((row for row in footprint["/usr/local/vgpu"] if row["path"] == "/usr/local/vgpu/libvgpu.so"), {})
            if library.get("sha256") != checks.LIBRARY_SHA:
                raise ValueError("installed libvgpu.so digest differs on " + node)
            save(self.out / node / "host-installed.json", footprint)
        save(self.out / "provider-nodes-installed.json", {"nodes": self.nodes, "run_id": self.run_id, "verified_at": utc()})

    def uninstall(self):
        if not self.expected.exists() or not self.owned.exists():
            raise ValueError("original provider and owned HAMi input snapshots are required")
        self.native.require_unallocated(restoring=True)
        phase = self.native.phase(self.kube.get("daemonset", DS, "-n", NS))
        self.native.protected(self.kube.get("pods", "-A"))
        self.native.idle()
        current = self.existing()
        if phase == "excluded":
            # Refuse replacement UIDs/specs before stopping any owned provider.
            self.owned_publications(self.kube.get("resourceslices"))
        # Stop every owned provider before removing any node-owned publication.
        for obj in current["items"]:
            if obj["kind"] == "DaemonSet":
                self.kube.guarded_delete(obj)
        selector = BOOK + "=dra-book," + INSTANCE + "=" + self.run_id + ",app.kubernetes.io/component=hami-provider"
        self.kube.wait(lambda: not self.kube.get("pods", "-n", NS, "-l", selector)["items"], "terminating HAMi plugin Pods remain", 300)
        if self.native.phase(self.kube.get("daemonset", DS, "-n", NS)) == "excluded":
            self.kube.wait(self.native.native_absent, "terminating native plugin Pods remain", 300)
            slices = self.kube.get("resourceslices")
            selected = self.owned_publications(slices)
            save(self.out / "hami-slices-before-delete.json", {"items": selected})
            for sl in selected:
                self.kube.guarded_delete(sl)
            self.kube.wait(lambda: not any(sl["spec"].get("nodeName") in self.nodes for sl in self.kube.get("resourceslices")["items"]), "stale HAMi ResourceSlices remain", 120)
        # Restore and verify the native UID/full spec, strategy, plugins and
        # publications before host cleanup can fail on any selected node.
        self.native.restore()
        failures = []
        for obj in self.existing()["items"]:
            if obj["kind"] != "DaemonSet":
                try:
                    self.kube.guarded_delete(obj)
                except Exception as error:
                    failures.append({"stage": "provider-object", "kind": obj["kind"],
                                     "name": obj["metadata"]["name"], "error": str(error)})
        for node in self.nodes:
            try:
                if not (self.out / node / "host-before.json").exists() or not (self.out / node / "host-installed.json").exists():
                    raise ValueError("host-before/host-installed evidence missing on " + node)
                self.host_start(node)
                checked = self.host(node, {"operation": "footprint"})
                marker = self.out / node / "host-restored-clean.json"
                if not marker.exists():
                    value = self.host(node, {"operation": "remove-hooks", "before": load(self.out / node / "host-before.json"),
                                            "installed": load(self.out / node / "host-installed.json"), "checked": checked})
                    save(marker, value)
                value = self.host(node, {"operation": "footprint"})
                if any(value[root] for root in ("/usr/local/vgpu", "/var/lib/kubelet/plugins/" + DRIVER, "/tmp/vgpulock")):
                    raise ValueError("HAMi host artifacts remain on " + node)
            except Exception as error:
                failures.append({"stage": "host", "node": node, "error": str(error)})
        save(self.out / "provider-nodes-cleanup.json", {"nodes": self.nodes, "run_id": self.run_id,
             "native_uid_spec_match": True, "host_artifacts_absent": not any(item["stage"] == "host" for item in failures),
             "failures": failures, "verified_at": utc()})
        if failures:
            detail = "; ".join((item.get("node") or item["kind"] + "/" + item["name"]) + ": " + item["error"] for item in failures)
            raise RuntimeError("Original NVIDIA provider UID/spec and target plugins/ResourceSlices were restored and verified. "
                               "HAMi cleanup failed: " + detail + ". Snapshots retained; retry uninstall with the same provider run-id.")
        save(self.out / "provider-nodes-restored.json", {"nodes": self.nodes, "run_id": self.run_id, "native_uid_spec_match": True, "host_artifacts_absent": True, "verified_at": utc()})


def main():
    if os.environ.get("I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER") != "1":
        raise ValueError("set I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("run_id")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?", args.run_id):
        raise ValueError("provider run-id must fit a Kubernetes instance label")
    nodes = node_list(os.environ.get("NODES", "dra-a40"))
    out = Path(os.environ.get("OUT_DIR", str(HAMI.parent / "out" / args.run_id)))
    session = HamiNodes(out, args.run_id, nodes)
    lock = out / ".provider-nodes-lock"
    lock.mkdir()
    try:
        getattr(session, args.action)()
    finally:
        try:
            session.stop_helpers()
        except Exception:
            if args.action == "uninstall":
                (out / "provider-nodes-restored.json").unlink(missing_ok=True)
            raise
        finally:
            lock.rmdir()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, TimeoutError, FileExistsError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
