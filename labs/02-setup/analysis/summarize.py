#!/usr/bin/env python3
"""Build the setup table from collected artifacts; Python stdlib only."""

import argparse
import json
from pathlib import Path

GPUS = {"a40": ("dra-a40", "NVIDIA A40"), "rtx4090": ("dra-4090", "NVIDIA GeForce RTX 4090")}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() and path.stat().st_size else {}


def build_summary(out_dir):
    out_dir = Path(out_dir)
    summary = read_json(out_dir / "run.json")
    rows = []
    for role, (expected_node, product) in GPUS.items():
        claim = read_json(out_dir / f"{role}-claim-allocated.json")
        completed = read_json(out_dir / f"{role}-claim-completed.json")
        pod = read_json(out_dir / f"{role}-pod-allocated.json")
        deletion = read_json(out_dir / f"{role}-deletion.json")
        log_path = out_dir / f"{role}-pod.log"
        log = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
        results = claim.get("status", {}).get("allocation", {}).get("devices", {}).get("results", [])
        early = deletion.get("claim_absent_before_job_deletion", False) or deletion.get("claim_absent_before_pod_deletion", False)
        rows.append({"role": role, "node": pod.get("spec", {}).get("nodeName", expected_node), "gpu": product,
            "pod_name": pod.get("metadata", {}).get("name"), "claim_name": claim.get("metadata", {}).get("name"),
            "claim_allocated": bool(results) and all(r.get("driver") == "gpu.nvidia.com" and r.get("pool") == expected_node for r in results),
            "claim_present_after_completion": bool(completed.get("metadata", {}).get("name")),
            "reserved_for_after_completion": completed.get("status", {}).get("reservedFor", []),
            "claim_absent_before_job_deletion": deletion.get("claim_absent_before_job_deletion", False),
            "claim_absent_before_pod_deletion": deletion.get("claim_absent_before_pod_deletion", False),
            "pod_deleted_at": deletion.get("pod_deleted_at"), "claim_deleted_at": deletion.get("claim_deleted_at"),
            "claim_deleted_after_pod_deletion": bool(deletion.get("pod_deleted_at") and deletion.get("claim_deleted_at")) and not early,
            "delete_observation_seconds": deletion.get("observation_seconds"),
            "pytorch_cuda_ok": f"PYTORCH_CUDA_OK {product}" in log.splitlines()})
    summary["gpus"] = rows
    error = out_dir / "run-error.txt"
    if error.is_file():
        summary["error"] = error.read_text(encoding="utf-8").strip()
    return summary


def render_table(summary):
    lines = ["node | GPU | claim allocated | claim deleted after Pod deletion | pytorch CUDA ok",
             "--- | --- | --- | --- | ---"]
    for row in summary["gpus"]:
        deleted = "yes" if row["claim_deleted_after_pod_deletion"] else "no"
        if row.get("claim_deleted_at") and (row.get("claim_absent_before_job_deletion") or row.get("claim_absent_before_pod_deletion")):
            deleted = "earlier"
        lines.append(" | ".join([row["node"], row["gpu"], "yes" if row["claim_allocated"] else "no",
                                 deleted, "yes" if row["pytorch_cuda_ok"] else "no"]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="out/<run-id> or summary.json")
    parser.add_argument("--write", action="store_true", help="also write summary.json in the run directory")
    args = parser.parse_args(argv)
    if args.path.is_file():
        if args.write:
            parser.error("--write requires a run directory")
        summary = read_json(args.path)
    else:
        summary = build_summary(args.path)
        if args.write:
            (args.path / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(render_table(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
