#!/usr/bin/env python3
"""Supervise our sampler; stop requests are nonce-bound, never arbitrary PID kills."""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

SCRIPT = Path(__file__).with_name("gpu-sample.sh")


def read_record(path):
    return json.loads(path.read_text()) if path.exists() else None


def write_record(path, record):
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(record) + "\n")
    temporary.replace(path)


def supervise(node, csv, nonce):
    pid_file = Path(str(csv) + ".pid")
    stop_file = Path(str(csv) + ".stop")
    record = {"pid": os.getpid(), "node": node, "csv": str(csv), "nonce": nonce,
              "state": "starting", "heartbeat": time.time()}
    stopping = False
    def stop_signal(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    child = subprocess.Popen(["bash", str(SCRIPT), "__worker", node, str(csv)],
                             stdin=subprocess.DEVNULL, start_new_session=True)
    record["sample_pid"] = child.pid
    try:
        while child.poll() is None:
            request = read_record(stop_file)
            if stopping or (request and request.get("nonce") == nonce):
                # This group was created by this supervisor and is still our child.
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                break
            record["state"] = "ready" if csv.stat().st_size else "starting"
            record["heartbeat"] = time.time()
            write_record(pid_file, record)
            time.sleep(0.05)
        record["state"] = "stopped" if stopping or read_record(stop_file) else "failed"
        record["exit"] = child.returncode
        record["heartbeat"] = time.time()
        write_record(pid_file, record)
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "__supervise"))
    parser.add_argument("node")
    parser.add_argument("csv", type=Path)
    parser.add_argument("nonce", nargs="?")
    args = parser.parse_args(argv)
    csv = args.csv.resolve()
    pid_file = Path(str(csv) + ".pid")
    stop_file = Path(str(csv) + ".stop")
    if args.action == "__supervise":
        supervise(args.node, csv, args.nonce)
        return 0
    try:
        record = read_record(pid_file)
        if record and (record["node"] != args.node or record["csv"] != str(csv)):
            raise ValueError("PID file belongs to another node or CSV")
        if args.action == "stop":
            if record and record["state"] not in ("stopped", "failed"):
                write_record(stop_file, {"nonce": record["nonce"]})
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    current = read_record(pid_file)
                    if current and current.get("nonce") == record["nonce"] and current["state"] in ("stopped", "failed"):
                        break
                    time.sleep(0.05)
                else:
                    raise ValueError("supervisor did not acknowledge stop within 5 seconds; PID file retained")
            pid_file.unlink(missing_ok=True)
            stop_file.unlink(missing_ok=True)
            print(f"Stopped sampler: {csv}")
            return 0
        if record:
            if record["state"] == "ready" and time.time() - record["heartbeat"] < 3:
                print(f"Sampler already running: {csv}")
                return 0
            raise ValueError("stale or failed sampler; stop it and choose a new CSV")
        if csv.exists():
            raise ValueError("CSV already exists; choose a new output path")
        csv.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.setdefault("OUT_DIR", str(csv.parent))
        nonce = uuid.uuid4().hex
        # Exclusive creation prevents two start requests from owning one CSV.
        with pid_file.open("x") as pid_output:
            json.dump({"pid": None, "node": args.node, "csv": str(csv), "nonce": nonce,
                       "state": "starting", "heartbeat": time.time()}, pid_output)
        with csv.open("xb") as output, Path(str(csv) + ".stderr.log").open("wb") as errors:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "__supervise", args.node,
                                      str(csv), nonce], stdout=output, stderr=errors, stdin=subprocess.DEVNULL,
                                     env=env, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while child.poll() is None and time.monotonic() < deadline:
                record = read_record(pid_file)
                if record and record["state"] == "ready":
                    print(f"Started sampler pid={child.pid}: {csv}")
                    return 0
                time.sleep(0.05)
            raise ValueError(f"sampler did not start; inspect {csv.name}.stderr.log")
        except BaseException:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            raise
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(f"Sampler failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
