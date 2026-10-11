#!/usr/bin/env python3
"""Standard-library parsers for the read-only GPU checks; unknown state fails."""

import argparse
import csv
import json
from pathlib import Path
import sys


def requirement_matches(requirement, values):
    key = requirement["key"]
    operator = requirement["operator"]
    options = requirement.get("values", [])
    present = key in values
    value = values.get(key)
    if operator == "In":
        return present and value in options
    if operator == "NotIn":
        return not present or value not in options
    if operator == "Exists":
        return present
    if operator == "DoesNotExist":
        return not present
    if operator in ("Gt", "Lt"):
        if len(options) != 1:
            raise ValueError("invalid numeric node selector")
        if not present:
            return False
        return int(value) > int(options[0]) if operator == "Gt" else int(value) < int(options[0])
    raise ValueError(f"unknown node selector operator: {operator}")


def selector_matches(selector, node):
    terms = selector.get("nodeSelectorTerms", [])
    if not terms:
        raise ValueError("empty node selector")
    metadata = node["metadata"]
    fields = {"metadata.name": metadata["name"]}
    labels = metadata.get("labels", {})
    for term in terms:
        requirements = [(r, labels) for r in term.get("matchExpressions", [])]
        field_requirements = term.get("matchFields", [])
        if any(r["key"] != "metadata.name" for r in field_requirements):
            raise ValueError("unknown node selector field")
        requirements.extend((r, fields) for r in field_requirements)
        if requirements and all(requirement_matches(r, values) for r, values in requirements):
            return True
    return False


def blocking_claims(claims, node):
    blockers = []
    for claim in claims["items"]:
        allocation = claim.get("status", {}).get("allocation")
        if allocation is None:
            continue
        metadata = claim["metadata"]
        name = f"{metadata.get('namespace', 'default')}/{metadata['name']}"
        if not allocation.get("devices", {}).get("results"):
            blockers.append(f"{name}: unknown allocation results")
            continue
        selector = allocation.get("nodeSelector")
        if selector is None:
            blockers.append(f"{name}: allocation available without a node restriction")
            continue
        try:
            if selector_matches(selector, node):
                blockers.append(f"{name}: allocated device available on this node")
        except (KeyError, TypeError, ValueError):
            blockers.append(f"{name}: cannot determine allocation node")
    return blockers


def select_driver(pods, node, container):
    selected = []
    for pod in pods["items"]:
        spec = pod.get("spec", {})
        status = pod.get("status", {})
        if pod.get("metadata", {}).get("deletionTimestamp"):
            continue
        if spec.get("nodeName") == node and status.get("phase") == "Running":
            if any(c.get("name") == container and c.get("ready") for c in status.get("containerStatuses", [])):
                selected.append(pod["metadata"]["name"])
    if len(selected) != 1:
        raise ValueError(f"expected one ready driver Pod on {node}, found {len(selected)}")
    return selected[0]


def check_memory(text, threshold):
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        raise ValueError("nvidia-smi returned no GPUs")
    values = []
    for row in rows:
        if len(row) != 1:
            raise ValueError("expected one memory.used value per GPU")
        value = float(row[0].strip())
        if not 0 <= value < threshold:
            raise ValueError(f"memory.used={value:g} MiB is not below {threshold:g} MiB")
        values.append(value)
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    driver = sub.add_parser("driver")
    driver.add_argument("node")
    driver.add_argument("container")
    claims = sub.add_parser("claims")
    claims.add_argument("claims", type=Path)
    claims.add_argument("node", type=Path)
    memory = sub.add_parser("memory")
    memory.add_argument("csv", type=Path)
    memory.add_argument("threshold", type=float)
    args = parser.parse_args(argv)
    try:
        if args.action == "driver":
            print(select_driver(json.load(sys.stdin), args.node, args.container))
        elif args.action == "claims":
            blockers = blocking_claims(json.loads(args.claims.read_text()), json.loads(args.node.read_text()))
            if blockers:
                print("\n".join(blockers), file=sys.stderr)
                return 1
        elif args.action == "memory":
            values = check_memory(args.csv.read_text(), args.threshold)
            print("memory.used MiB: " + ", ".join(f"{v:g}" for v in values))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"GPU check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
