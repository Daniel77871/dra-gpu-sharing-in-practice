#!/usr/bin/env python3
"""Local JSON checks for the explicit provider-switch scripts (stdlib only)."""
import copy
import csv
import datetime
import io
import json
from pathlib import Path
import sys
from urllib.parse import quote

NAMESPACE = "nvidia-dra-driver-gpu"
DAEMONSET = "dra-driver-nvidia-gpu-kubelet-plugin"
DRIVER = "gpu.nvidia.com"
HAMI_DRIVER = "hami-core-gpu.project-hami.io"
LAB_NAMESPACE = "gpu-isolation-lab"


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def canonical_spec(spec):
    value = copy.deepcopy(spec)
    meta = value.get("template", {}).get("metadata", {})
    for key in ("creationTimestamp", "deletionTimestamp", "resourceVersion", "uid", "managedFields", "generation", "selfLink"):
        meta.pop(key, None)
    return value


def excluded_spec(spec, node):
    """Append a field constraint to every OR term without weakening any term."""
    changed = copy.deepcopy(spec)
    changed["updateStrategy"] = {"type": "OnDelete"}
    try:
        terms = changed["template"]["spec"]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
    except KeyError as error:
        raise ValueError("original nodeSelectorTerms missing; refusing to broaden affinity") from error
    if not isinstance(terms, list) or not terms or any(not isinstance(term, dict) or not term for term in terms):
        raise ValueError("original nodeSelectorTerms empty or invalid")
    exclusion = {"key": "metadata.name", "operator": "NotIn", "values": [node]}
    for term in terms:
        fields = term.setdefault("matchFields", [])
        if exclusion not in fields:
            fields.append(copy.deepcopy(exclusion))
    return changed


def resumed_spec(spec):
    changed = copy.deepcopy(spec)
    changed["updateStrategy"] = {"type": "OnDelete"}
    return changed


def identity(obj):
    meta = obj["metadata"]
    return {key: meta.get(key) for key in ("namespace", "name", "uid")}


def owned_pods(pods, original, node):
    uid = original["metadata"]["uid"]
    return [pod for pod in pods["items"] if pod.get("spec", {}).get("nodeName") == node
            and pod["metadata"].get("namespace") == NAMESPACE
            and any(ref.get("kind") == "DaemonSet" and ref.get("uid") == uid and ref.get("controller") is True
                    for ref in pod["metadata"].get("ownerReferences", []))]


def node_slices(slices, node):
    return [obj for obj in slices["items"] if obj.get("spec", {}).get("nodeName") == node]


def node_owner_matches(obj, state):
    return any(ref.get("kind") == "Node" and ref.get("name") == state["target_node"]
               and ref.get("uid") == state["node"]["uid"] for ref in obj["metadata"].get("ownerReferences", []))


def canonical_slice(obj):
    spec = copy.deepcopy(obj["spec"])
    spec.get("pool", {}).pop("generation", None)
    spec["devices"] = sorted(spec.get("devices", []), key=lambda device: device["name"])
    return spec


def slice_specs(slices, node):
    return sorted(json.dumps(canonical_slice(obj), sort_keys=True) for obj in node_slices(slices, node))


def read_backup(directory):
    directory = Path(directory)
    original = load(directory / "original-daemonset.json")
    state = load(directory / "backup.json")
    if identity(original) != state["daemonset"] or original["metadata"]["uid"] != (directory / "uid.txt").read_text().strip():
        raise ValueError("backup identity does not match original DaemonSet")
    if original["spec"]["updateStrategy"] != load(directory / "update-strategy.json"):
        raise ValueError("backup updateStrategy does not match original DaemonSet")
    return original, state


def check_ds(current, original, state):
    if identity(current) != identity(original):
        raise ValueError("DaemonSet UID changed; refusing mutation")
    versions = {
        "original": original["spec"],
        "excluded": excluded_spec(original["spec"], state["target_node"]),
        "resumed": resumed_spec(original["spec"]),
    }
    for name, spec in versions.items():
        if canonical_spec(current["spec"]) == canonical_spec(spec):
            return name
    raise ValueError("DaemonSet spec changed; refusing concurrent overwrite")


def check_protected(pods, original, state):
    current = sorted((identity(pod) for pod in owned_pods(pods, original, state["protected_node"])), key=lambda item: item["name"])
    if current != state["protected_pods"]:
        raise ValueError("protected plugin identity changed; stop without touching it")


def check_claims(claims, state):
    for claim in claims["items"]:
        meta = claim["metadata"]
        allocation = claim.get("status", {}).get("allocation") or {}
        target = any(result.get("pool") == state["target_node"]
                     for result in allocation.get("devices", {}).get("results", []))
        if meta.get("namespace") == LAB_NAMESPACE or target:
            raise ValueError("residual ResourceClaim: " + meta.get("namespace", "") + "/" + meta["name"])


def plugin_spec(spec, labels):
    if labels.get("app.kubernetes.io/component", "").endswith("-provider"):
        return True
    for container in spec.get("containers", []):
        if any(word in container.get("image", "") for word in ("dra-driver-nvidia-gpu", "hami-dra", "k8s-dra-driver")):
            return True
        command = " ".join(container.get("command", []) + container.get("args", []))
        if "kubelet-plugin" in command:
            return True
    return any(volume.get("hostPath", {}).get("path") in ("/var/lib/kubelet/plugins", "/var/lib/kubelet/plugins_registry")
               for volume in spec.get("volumes", []))


def could_target(spec, node):
    if spec.get("nodeName"):
        return spec["nodeName"] == node
    hostname = spec.get("nodeSelector", {}).get("kubernetes.io/hostname")
    return hostname is None or hostname == node


def check_alternatives(pods, daemonsets, original, state):
    node = state["target_node"]
    # A newly recreated native Pod may still be Pending without spec.nodeName.
    native = {pod["metadata"]["uid"] for pod in pods["items"]
              if pod["metadata"].get("namespace") == NAMESPACE and any(
                  ref.get("kind") == "DaemonSet" and ref.get("uid") == original["metadata"]["uid"]
                  and ref.get("controller") is True for ref in pod["metadata"].get("ownerReferences", []))}
    for pod in pods["items"]:
        spec, meta = pod.get("spec", {}), pod["metadata"]
        if meta["uid"] not in native and could_target(spec, node) and plugin_spec(spec, meta.get("labels", {})):
            raise ValueError("alternative provider Pod remains: " + meta.get("namespace", "") + "/" + meta["name"])
    for daemonset in daemonsets["items"]:
        if identity(daemonset) == identity(original):
            continue
        template = daemonset.get("spec", {}).get("template", {})
        labels = dict(template.get("metadata", {}).get("labels", {}))
        labels.update(daemonset["metadata"].get("labels", {}))
        if could_target(template.get("spec", {}), node) and plugin_spec(template.get("spec", {}), labels):
            meta = daemonset["metadata"]
            raise ValueError("alternative provider DaemonSet remains: " + meta.get("namespace", "") + "/" + meta["name"])


def preflight(directory, mode):
    directory = Path(directory)
    original, state = read_backup(directory)
    current = load(directory / "current-daemonset.json")
    version = check_ds(current, original, state)
    if mode == "exclude" and version == "resumed":
        raise ValueError("restoration in progress; run restore.sh before a new exclusion")
    if mode in ("remove", "remove-wait") and version != "excluded":
        raise ValueError("native provider must remain excluded during alternative slice cleanup")
    node = load(directory / "current-node.json")
    if identity(node) != state["node"]:
        raise ValueError("target Node UID changed")
    pods = load(directory / "current-pods.json")
    check_protected(pods, original, state)
    check_claims(load(directory / "current-claims.json"), state)
    check_alternatives(pods, load(directory / "current-daemonsets.json"), original, state)
    if mode == "remove" and owned_pods(pods, original, state["target_node"]):
        raise ValueError("native target plugin remains; complete exclusion before alternative slice cleanup")
    slices = load(directory / "current-slices.json")
    target_slices = node_slices(slices, state["target_node"])
    if mode == "restore" and version == "excluded" and target_slices:
        raise ValueError("target ResourceSlices remain; remove alternative provider and its slices before restore")
    if mode == "restore" and version in ("resumed", "original") and target_slices:
        baseline = load(directory / "original-slices.json")
        if slice_specs(slices, state["target_node"]) != slice_specs(baseline, state["target_node"]):
            raise ValueError("target ResourceSlice publication differs from backup")
    return version


def initialize(directory, target, protected):
    directory = Path(directory)
    original = load(directory / "original-daemonset.json")
    meta = original["metadata"]
    if meta.get("name") != DAEMONSET or meta.get("namespace") != NAMESPACE or not meta.get("uid"):
        raise ValueError("unexpected original NVIDIA DaemonSet identity")
    if original["spec"].get("selector", {}).get("matchLabels") != {"dra-driver-nvidia-gpu-component": "kubelet-plugin"}:
        raise ValueError("original NVIDIA DaemonSet selector differs from the documented installation")
    if target == protected:
        raise ValueError("target and protected node must differ")
    excluded_spec(original["spec"], target)
    if original["spec"]["updateStrategy"].get("type") != "RollingUpdate":
        raise ValueError("original DaemonSet is already suspended; choose its existing backup")
    pods = load(directory / "original-pods.json")
    targets, protected_pods = owned_pods(pods, original, target), owned_pods(pods, original, protected)
    if len(targets) != 1 or len(protected_pods) != 1:
        raise ValueError("expected one original plugin Pod on each target and protected node")
    node = load(directory / "original-node.json")
    if node["metadata"].get("name") != target or not node["metadata"].get("uid"):
        raise ValueError("target Node identity missing")
    slices = load(directory / "original-slices.json")
    for name in (target, protected):
        baseline = node_slices(slices, name)
        if not baseline or any(obj["spec"].get("driver") != DRIVER for obj in baseline):
            raise ValueError("expected only native NVIDIA ResourceSlices on " + name)
    state = {"daemonset": identity(original), "target_node": target, "protected_node": protected,
             "node": identity(node), "target_pod": identity(targets[0]),
             "protected_pods": [identity(protected_pods[0])]}
    save(directory / "update-strategy.json", original["spec"]["updateStrategy"])
    (directory / "uid.txt").write_text(meta["uid"] + "\n")
    save(directory / "backup.json", state)


def make_patch(directory, mode):
    directory = Path(directory)
    original, state = read_backup(directory)
    current = load(directory / "current-daemonset.json")
    version = check_ds(current, original, state)
    if mode == "exclude":
        desired = excluded_spec(original["spec"], state["target_node"])
    elif mode == "resume":
        desired = original["spec"] if version == "original" else resumed_spec(original["spec"])
    elif mode == "strategy":
        if version == "excluded":
            raise ValueError("cannot restore updateStrategy before restoring the template")
        desired = original["spec"]
    else:
        raise ValueError("unknown patch stage")
    patch = [] if canonical_spec(current["spec"]) == canonical_spec(desired) else [
        {"op": "test", "path": "/metadata/uid", "value": original["metadata"]["uid"]},
        {"op": "test", "path": "/spec", "value": current["spec"]},
        {"op": "replace", "path": "/spec", "value": desired},
    ]
    save(directory / (mode + "-patch.json"), patch)
    return bool(patch)


def delete_options(obj):
    meta = obj["metadata"]
    if not meta.get("uid") or not meta.get("resourceVersion"):
        raise ValueError("UID/resourceVersion missing; refusing deletion")
    return {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {
        "uid": meta["uid"], "resourceVersion": meta["resourceVersion"]}}


def target_pod_delete(directory):
    directory = Path(directory)
    original, state = read_backup(directory)
    pods = load(directory / "current-pods.json")
    check_protected(pods, original, state)
    current = owned_pods(pods, original, state["target_node"])
    if not current:
        return
    if len(current) != 1 or identity(current[0]) != state["target_pod"]:
        raise ValueError("target plugin identity changed; refusing deletion")
    pod = current[0]
    save(directory / "target-pod-delete.json", delete_options(pod))
    meta = pod["metadata"]
    print("/api/v1/namespaces/" + quote(meta["namespace"], safe="") + "/pods/" + quote(meta["name"], safe=""))


def native_absent(directory):
    original, state = read_backup(directory)
    pods = load(Path(directory) / "current-pods.json")
    check_protected(pods, original, state)
    current = owned_pods(pods, original, state["target_node"])
    if current and (len(current) != 1 or identity(current[0]) != state["target_pod"]):
        raise ValueError("target plugin identity changed while waiting for deletion")
    return not current


def recheck_target_pod_delete(directory):
    _, state = read_backup(directory)
    pods = load(Path(directory) / "current-pods.json")
    recorded = state["target_pod"]
    current = [pod for pod in pods["items"] if pod["metadata"].get("namespace") == recorded["namespace"]
               and pod["metadata"].get("name") == recorded["name"]]
    if len(current) > 1 or (current and identity(current[0]) != recorded):
        raise ValueError("target plugin identity changed after DELETE; refusing retry")
    native_absent(directory)
    if current and not current[0]["metadata"].get("deletionTimestamp"):
        raise ValueError("target plugin DELETE failed without termination; refusing resourceVersion retry")


def native_cleanup_complete(directory):
    directory = Path(directory)
    original, state = read_backup(directory)
    absent = native_absent(directory)
    if not absent:
        current = owned_pods(load(directory / "current-pods.json"), original, state["target_node"])
        if not current[0]["metadata"].get("deletionTimestamp"):
            raise ValueError("native target plugin is not terminating; complete exclude-node.sh before alternative slice cleanup")
    baseline = {obj["metadata"]["uid"]: obj for obj in node_slices(load(directory / "original-slices.json"), state["target_node"])}
    remaining = [obj for obj in load(directory / "current-slices.json")["items"] if obj["metadata"].get("uid") in baseline]
    for obj in remaining:
        previous = baseline[obj["metadata"]["uid"]]
        if identity(obj) != identity(previous) or obj["spec"] != previous["spec"] or not node_owner_matches(obj, state):
            raise ValueError("original native ResourceSlice identity or spec changed while waiting for cleanup")
    return absent and not remaining


def slice_deletes(directory):
    directory = Path(directory)
    _, state = read_backup(directory)
    baseline = {obj["metadata"]["name"]: obj for obj in node_slices(load(directory / "original-slices.json"), state["target_node"])}
    pending = []
    for index, obj in enumerate(node_slices(load(directory / "current-slices.json"), state["target_node"])):
        old = baseline.get(obj["metadata"]["name"])
        if not old or identity(old) != identity(obj) or old["spec"] != obj["spec"] or not node_owner_matches(obj, state):
            raise ValueError("ResourceSlice identity or spec changed; refusing stale-slice deletion")
        path = directory / ("stale-slice-" + str(index) + "-delete.json")
        pending.append((obj, path))
    for obj, path in pending:
        save(path, delete_options(obj))
        uri = "/apis/" + obj["apiVersion"] + "/resourceslices/" + quote(obj["metadata"]["name"], safe="")
        print(uri + "\t" + str(path))


def target_uuids(directory, state):
    slices = load(Path(directory) / "original-slices.json")
    return {device.get("attributes", {}).get("uuid", {}).get("string")
            for obj in node_slices(slices, state["target_node"]) for device in obj["spec"].get("devices", [])}


def check_idle(directory, gpu_file, process_file):
    _, state = read_backup(directory)
    rows = list(csv.reader(io.StringIO(Path(gpu_file).read_text())))
    known = target_uuids(directory, state)
    if (not rows or None in known or {row[0].strip() for row in rows} != known
            or Path(process_file).read_text().strip()):
        raise ValueError("GPU must be idle in Default mode: missing/unknown GPU or active compute process")
    for row in rows:
        if len(row) != 7 or not 0 <= int(row[3].strip()) <= 64 or row[6].strip() != "Default":
            raise ValueError("GPU must be idle in Default mode: memory.used must be at most 64 MiB and compute_mode must be Default")


def capture_alternative_slices(directory, driver):
    directory = Path(directory)
    _, state = read_backup(directory)
    if driver not in (DRIVER, HAMI_DRIVER):
        raise ValueError("unsupported alternative provider driver")
    known = target_uuids(directory, state)
    original_uids = {obj["metadata"]["uid"] for obj in node_slices(load(directory / "original-slices.json"), state["target_node"])}
    selected = node_slices(load(directory / "current-slices.json"), state["target_node"])
    for obj in selected:
        spec = obj["spec"]
        uuids = {device.get("attributes", {}).get("uuid", {}).get("string") for device in spec.get("devices", [])}
        if (spec.get("driver") != driver or spec.get("pool", {}).get("name") != state["target_node"]
                or not uuids or None in uuids or not uuids <= known or not node_owner_matches(obj, state)):
            raise ValueError("ResourceSlice is not the known target GPU publication; refusing deletion")
        if obj["metadata"]["uid"] in original_uids:
            raise ValueError("original native ResourceSlice remains; run exclude-node.sh to remove it")
        delete_options(obj)
    if selected:
        save(directory / "alternative-slices.json", {"items": selected})
    elif not (directory / "alternative-slices.json").exists():
        save(directory / "alternative-slices.json", {"items": []})


def alternative_slice_deletes(directory):
    directory = Path(directory)
    _, state = read_backup(directory)
    snapshot = {obj["metadata"]["name"]: obj for obj in load(directory / "alternative-slices.json")["items"]}
    pending = []
    for index, obj in enumerate(node_slices(load(directory / "current-slices.json"), state["target_node"])):
        previous = snapshot.get(obj["metadata"]["name"])
        if not previous or identity(obj) != identity(previous) or obj["spec"] != previous["spec"]:
            raise ValueError("alternative ResourceSlice identity or spec changed before deletion")
        path = directory / ("alternative-slice-" + str(index) + "-delete.json")
        pending.append((obj, path))
    for obj, path in pending:
        save(path, delete_options(obj))
        uri = "/apis/" + obj["apiVersion"] + "/resourceslices/" + quote(obj["metadata"]["name"], safe="")
        print(uri + "\t" + str(path))


def restored_ready(directory):
    directory = Path(directory)
    original, state = read_backup(directory)
    pods = load(directory / "current-pods.json")
    check_protected(pods, original, state)
    target = owned_pods(pods, original, state["target_node"])
    if len(target) != 1 or target[0]["metadata"].get("deletionTimestamp"):
        return False
    if not any(condition.get("type") == "Ready" and condition.get("status") == "True"
               for condition in target[0].get("status", {}).get("conditions", [])):
        return False
    slices, baseline = load(directory / "current-slices.json"), load(directory / "original-slices.json")
    return all(slice_specs(slices, node) == slice_specs(baseline, node)
               for node in (state["target_node"], state["protected_node"]))


def record(directory, name):
    original, state = read_backup(directory)
    if name == "restored" and check_ds(load(Path(directory) / "current-daemonset.json"), original, state) != "original":
        raise ValueError("restored DaemonSet spec differs from backup")
    save(Path(directory) / (name + ".json"), {"uid": original["metadata"]["uid"],
         "target_node": state["target_node"], "protected_node": state["protected_node"],
         "utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})


def main():
    action, directory, *args = sys.argv[1:]
    if action == "initialize":
        initialize(directory, *args)
    elif action == "field":
        print(read_backup(directory)[1][args[0]])
    elif action == "validate":
        read_backup(directory)
    elif action == "preflight":
        preflight(directory, args[0])
    elif action == "patch":
        return 0 if make_patch(directory, args[0]) else 1
    elif action == "pod-delete":
        target_pod_delete(directory)
    elif action == "native-absent":
        return 0 if native_absent(directory) else 1
    elif action == "pod-delete-recheck":
        recheck_target_pod_delete(directory)
    elif action == "native-cleanup-complete":
        return 0 if native_cleanup_complete(directory) else 1
    elif action == "slice-deletes":
        slice_deletes(directory)
    elif action == "idle":
        check_idle(directory, *args)
    elif action == "capture-alternative-slices":
        capture_alternative_slices(directory, args[0])
    elif action == "alternative-slice-deletes":
        alternative_slice_deletes(directory)
    elif action == "slices-absent":
        _, state = read_backup(directory)
        return 0 if not node_slices(load(Path(directory) / "current-slices.json"), state["target_node"]) else 1
    elif action == "ready":
        return 0 if restored_ready(directory) else 1
    elif action == "record":
        record(directory, args[0])
    else:
        raise ValueError("unknown local provider action")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as error:
        print("provider: " + str(error), file=sys.stderr)
        raise SystemExit(2)
