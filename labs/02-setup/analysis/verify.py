#!/usr/bin/env python3
"""Check read-only kubectl/Helm snapshots without third-party libraries."""

import argparse
import json
from pathlib import Path

GPUS = {"dra-a40": "NVIDIA A40", "dra-4090": "NVIDIA GeForce RTX 4090"}
CLASSES = {"gpu.nvidia.com", "mig.nvidia.com", "vfio.gpu.nvidia.com"}


def ready(obj):
    return any(c.get("type") == "Ready" and c.get("status") == "True" for c in obj.get("status", {}).get("conditions", []))


def operator_errors(operator):
    pods = operator["items"]
    errors = [] if pods else ["gpu-operator namespace has no Pods"]
    for pod in pods:
        name = pod["metadata"]["name"]
        if pod.get("status", {}).get("phase") == "Succeeded" and name.startswith("nvidia-cuda-validator-"):
            continue
        if not ready(pod):
            errors.append(f"gpu-operator Pod not Ready: {name}")
    for node in GPUS:
        for prefix in ["nvidia-driver-daemonset-", "nvidia-container-toolkit-daemonset-", "nvidia-operator-validator-",
                       "gpu-feature-discovery-", "nvidia-dcgm-exporter-", "nvidia-cuda-validator-"]:
            if not any(p["metadata"]["name"].startswith(prefix) and p.get("spec", {}).get("nodeName") == node for p in pods):
                errors.append(f"missing GPU Operator operand {prefix} on {node}")
    for node in ["dra-m1", "dra-non", *GPUS]:
        if not any(p["metadata"]["name"].startswith("gpu-operator-node-feature-discovery-worker-") and
                   p.get("spec", {}).get("nodeName") == node for p in pods):
            errors.append(f"missing NFD worker on {node}")
    for prefix in ["gpu-operator-node-feature-discovery-master-", "gpu-operator-node-feature-discovery-gc-"]:
        if not any(p["metadata"]["name"].startswith(prefix) for p in pods):
            errors.append(f"missing {prefix}")
    if not any(p["metadata"]["name"].startswith("gpu-operator-") and
               not p["metadata"]["name"].startswith("gpu-operator-node-feature-discovery-") for p in pods):
        errors.append("missing GPU Operator controller Pod")
    return errors


def inventory_errors(nodes, operator, plugins, classes, slices):
    errors = []
    names = {n["metadata"]["name"] for n in nodes["items"]}
    for node in {"dra-m1", "dra-non", *GPUS} - names:
        errors.append(f"missing node: {node}")
    for node in nodes["items"]:
        if not ready(node):
            errors.append(f'node not Ready: {node["metadata"]["name"]}')
    errors.extend(operator_errors(operator))
    for node, product in GPUS.items():
        matches = [p for p in plugins["items"] if p.get("spec", {}).get("nodeName") == node and
                   p["metadata"]["name"].startswith("dra-driver-nvidia-gpu-kubelet-plugin-")]
        if len(matches) != 1 or not ready(matches[0]):
            errors.append(f"expected one Ready DRA kubelet plugin on {node}, found {len(matches)}")
        published = [s for s in slices["items"] if s["spec"].get("driver") == "gpu.nvidia.com" and s["spec"].get("nodeName") == node]
        if len(published) != 1:
            errors.append(f"expected one ResourceSlice on {node}, found {len(published)}")
            continue
        devices = published[0]["spec"].get("devices", [])
        if len(devices) != 1 or devices[0].get("attributes", {}).get("productName", {}).get("string") != product:
            errors.append(f"ResourceSlice productName mismatch on {node}: expected {product}")
    found = {c["metadata"]["name"] for c in classes["items"]}
    errors.extend(f"missing DeviceClass: {name}" for name in sorted(CLASSES - found))
    return errors


def value_differences(expected, actual, prefix=""):
    errors, extras = [], []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in expected:
            path = f"{prefix}.{key}" if prefix else key
            if key not in actual:
                errors.append(f"{path}: missing (expected {expected[key]!r})")
            else:
                changed, extra = value_differences(expected[key], actual[key], path)
                errors.extend(changed)
                extras.extend(extra)
        extras.extend(f"{prefix}.{key}" if prefix else key for key in sorted(actual.keys() - expected.keys()))
    elif type(expected) is not type(actual) or expected != actual:
        errors.append(f"{prefix}: expected {expected!r}, observed {actual!r}")
    return errors, extras


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["inventory", "values", "operator"])
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    data = [json.loads(p.read_text(encoding="utf-8")) for p in args.files]
    if args.mode == "values":
        errors, extras = value_differences(data[0], data[1] or {})
        for key in extras:
            print(f"INFO: additional Helm user value: {key}")
    elif args.mode == "operator":
        errors = operator_errors(data[0])
    else:
        errors = inventory_errors(*data)
    for error in errors:
        print(f"FAIL: {error}")
    if not errors:
        print(f"PASS: {args.mode}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
