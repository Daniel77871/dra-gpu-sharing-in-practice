#!/usr/bin/env python3
"""Guard fresh-cluster installation and compare DaemonSet UID/spec backups."""

import argparse
import json
from pathlib import Path

NAMESPACES = {"gpu-operator", "nvidia-dra-driver-gpu"}


def daemonset_errors(original, current):
    indexed = {(d["metadata"]["namespace"], d["metadata"]["name"]): d for d in current["items"]}
    errors = []
    for old in original["items"]:
        key = (old["metadata"]["namespace"], old["metadata"]["name"])
        new = indexed.get(key)
        if not new:
            errors.append(f"missing original DaemonSet: {key}")
        elif old["metadata"]["uid"] != new["metadata"]["uid"]:
            errors.append(f"DaemonSet UID changed: {key}")
        elif old["spec"] != new["spec"]:
            errors.append(f"DaemonSet spec changed: {key}")
    return errors


def fresh_errors(namespaces, daemonsets, releases, classes, slices):
    errors = []
    errors.extend(f'existing namespace: {n["metadata"]["name"]}' for n in namespaces["items"] if n["metadata"]["name"] in NAMESPACES)
    errors.extend(f'existing GPU DaemonSet: {d["metadata"]["name"]}' for d in daemonsets["items"] if
                  d["metadata"]["namespace"] in NAMESPACES or "nvidia" in d["metadata"]["name"].lower())
    errors.extend(f'existing GPU release: {r["name"]}' for r in releases if
                  r["namespace"] in NAMESPACES or r["name"] in {"gpu-operator", "dra-driver-nvidia-gpu"})
    errors.extend(f'existing DeviceClass: {c["metadata"]["name"]}' for c in classes["items"] if "nvidia.com" in c["metadata"]["name"])
    errors.extend("existing NVIDIA ResourceSlice" for s in slices["items"] if "nvidia.com" in s["spec"]["driver"])
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["fresh", "compare", "created", "node-labels"])
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    data = [json.loads(p.read_text(encoding="utf-8")) for p in args.files]
    if args.mode == "compare":
        errors = daemonset_errors(*data)
    elif args.mode == "fresh":
        errors = fresh_errors(*data)
    elif args.mode == "created":
        print(json.dumps({"items": [d for d in data[0]["items"] if d["metadata"]["namespace"] in NAMESPACES]}, indent=2))
        return 0
    else:
        current = {n["metadata"]["name"]: n for n in data[1]["items"]}
        labels = []
        for old in data[0]["items"]:
            if old["metadata"]["name"] not in {"dra-a40", "dra-4090"}:
                continue
            name = old["metadata"]["name"]
            if name not in current or old["metadata"]["uid"] != current[name]["metadata"]["uid"]:
                raise SystemExit(f"node UID changed or missing: {name}")
            previous = old["metadata"].get("labels", {}).get("nvidia.com/dra-kubelet-plugin")
            labels.append(name + " " + ("nvidia.com/dra-kubelet-plugin=" + previous if previous is not None else "nvidia.com/dra-kubelet-plugin-"))
        print("\n".join(labels))
        return 0
    for error in errors:
        print("FAIL: " + error)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
