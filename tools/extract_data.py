#!/usr/bin/env python3
"""Extract named experiments and record the read-only source files used."""

import argparse
import json
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.extractors import REGISTRY, SourceReader, discover, register

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LAB = Path("/Users/daniel/dra-gpu-oom-lab")


@register("e1", help="A40 shared-GPU CUDA OOM, six original rounds")
def extract_e1(reader, output):
    """Read only named evidence files; publish no inventory or command archive."""
    source = "results/a40-20261006T062717Z"
    for mode in ("exit", "hold"):
        for repeat in (1, 2, 3):
            trial = f"{mode}-r{repeat}"
            destination = output / "raw" / trial
            destination.mkdir(parents=True, exist_ok=True)
            for name in ("a.jsonl", "b.jsonl", "gpu.jsonl", "phases.json"):
                (destination / name).write_bytes(reader.read_bytes(f"{source}/{trial}/{name}"))
            claim = json.loads(reader.read_text(f"{source}/{trial}/claim.json"))
            # Explicit fields keep allocations and both consumers, without annotations.
            claim["metadata"] = {key: value for key, value in claim["metadata"].items()
                                 if key in ("name", "namespace", "uid", "creationTimestamp", "labels")}
            (destination / "claim.json").write_text(json.dumps(claim, ensure_ascii=False, indent=2) + "\n")
            meta = json.loads(reader.read_text(f"{source}/{trial}/trial.json"))
            meta.pop("xid_collection_note", None)
            (destination / "trial.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
            for role in ("survivor", "pressure"):
                pod = json.loads(reader.read_text(f"{source}/{trial}/{role}-final.json"))
                # Only fields needed to independently verify termination and the image.
                snapshot = {"metadata": {"name": pod["metadata"]["name"]},
                            "spec": {"nodeName": pod["spec"]["nodeName"]},
                            "status": {"phase": pod["status"]["phase"], "containerStatuses": []}}
                for container in pod["status"]["containerStatuses"]:
                    state = container.get("state", {})
                    terminated = state.get("terminated")
                    snapshot["status"]["containerStatuses"].append({
                        "name": container["name"], "image": container["image"], "imageID": container["imageID"],
                        "restartCount": container["restartCount"],
                        "state": {"terminated": {key: value for key, value in terminated.items()
                                                  if key != "containerID"}} if terminated else state})
                (destination / f"{role}-final.json").write_text(json.dumps(snapshot, indent=2) + "\n")
    (output / "summary-original.csv").write_bytes(reader.read_bytes(f"{source}/summary.csv"))
    (output / "version.json").write_bytes(reader.read_bytes(f"{source}/version.json"))


def main(argv=None):
    discover()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", type=Path, default=DEFAULT_LAB, help=f"read-only source lab (default: {DEFAULT_LAB})")
    parser.add_argument("--root", type=Path, default=ROOT, help="destination repository")
    subcommands = parser.add_subparsers(dest="exp", required=True)
    for exp, (_, help_text) in sorted(REGISTRY.items()):
        subcommands.add_parser(exp, help=help_text)
    args = parser.parse_args(argv)
    output = args.root / "data" / args.exp
    if output.resolve().is_relative_to(args.lab.resolve()):
        parser.error("destination must be outside the read-only lab")
    try:
        output.mkdir(parents=True, exist_ok=True)
        reader = SourceReader(args.lab)
        REGISTRY[args.exp][0](reader, output)
        reader.write_sources(output)
    except (OSError, ValueError) as error:
        print(f"Extraction failed: {error}")
        return 1
    print(f"Extracted {args.exp}: {len(reader.sources)} source file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
