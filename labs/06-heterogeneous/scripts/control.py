#!/usr/bin/env python3
"""Run and collect the plain-Job GPU selection lab using kubectl (stdlib only)."""
import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

LAB = Path(__file__).resolve().parents[1]
NS = "gpu-heterogeneous-lab"
BOOK = "app.kubernetes.io/part-of"
INSTANCE = "app.kubernetes.io/instance"
NATIVE_CASES = ("a40", "rtx4090", "any-gpu", "any-gpu-pair", "attribute", "nonexistent", "prefer-4090")
CAPACITY_CASES = ("h1-8gi-any", "h2-30gi-any", "h3h4-pending-release", "h5-two-slots")
JOBS = {"h1-8gi-any": ["capacity-h1-worker"], "h2-30gi-any": ["capacity-h2-worker"],
        "h3h4-pending-release": ["capacity-pending-worker"],
        "h5-two-slots": ["capacity-parallel-primary", "capacity-parallel-secondary"]}


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def json_response(text):
    """kubectl create may print one JSON document for each manifest object."""
    values, decoder = [], json.JSONDecoder()
    text = text.strip()
    while text:
        value, consumed = decoder.raw_decode(text)
        values.append(value)
        text = text[consumed:].lstrip()
    if len(values) == 1:
        return values[0]
    return {"apiVersion": "v1", "kind": "List", "items": [item for value in values for item in value.get("items", [value])]}


def select_cases(part, requested, smoke):
    allowed = NATIVE_CASES if part == "native" else CAPACITY_CASES
    chosen = requested.replace(",", " ").split() if requested else list(allowed[:2] if smoke and part == "capacity" else allowed)
    if not chosen or len(set(chosen)) != len(chosen) or any(case not in allowed for case in chosen):
        raise ValueError("CASES must contain distinct cases from PART=" + part)
    if smoke and part == "capacity" and any(case not in CAPACITY_CASES[:2] for case in chosen):
        raise ValueError("SMOKE=1 capacity covers h1-8gi-any and h2-30gi-any only")
    return chosen


def holder_requests(a40, rtx4090, step):
    small, large = 8 * 2**30, 30 * 2**30
    if not (small <= rtx4090 < large <= a40 and step > 0):
        raise ValueError("published capacities cannot reproduce the memory-only cases")
    values = {"pending": ((a40 - large) // step + 1) * step,
              "a40": a40 - small, "rtx4090": rtx4090 - small}
    if any(value <= 0 or value % step or value % 2**20 for value in values.values()):
        raise ValueError("holder requests do not align with the live memory policy")
    return {name: str(value // 2**20) + "Mi" for name, value in values.items()}


def verify_same_identity(before, after):
    old = {(p.get("pod_uid"), p.get("claim_uid")) for p in before}
    new = {(p.get("pod_uid"), p.get("claim_uid")) for p in after}
    if len(old) != 1 or old != new or any(not all(pair) for pair in old):
        raise ValueError("Pending recovery replaced the Pod or ResourceClaim UID")


def quantity(value):
    match = re.fullmatch(r"([0-9]+)(Ki|Mi|Gi|Ti)?", str(value))
    if not match:
        raise ValueError("unsupported integral capacity quantity: " + str(value))
    return int(match[1]) * {None: 1, "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}[match[2]]


def placements(pods, claims):
    by_name = {c["metadata"]["name"]: c for c in claims.get("items", [])}
    result = []
    for pod in pods.get("items", []):
        spec, meta = pod["spec"], pod["metadata"]
        bound = {row["name"]: row.get("resourceClaimName") for row in pod.get("status", {}).get("resourceClaimStatuses", [])}
        for reference in spec.get("resourceClaims", []):
            claim_name = reference.get("resourceClaimName") or bound.get(reference["name"])
            claim = by_name.get(claim_name, {})
            allocation = ((claim.get("status", {}).get("allocation") or {}).get("devices", {}).get("results") or [{}])[0]
            result.append({"job": next((owner["name"] for owner in meta.get("ownerReferences", []) if owner["kind"] == "Job"), None),
                           "pod": meta["name"], "pod_uid": meta["uid"], "node": spec.get("nodeName"),
                           "claim": claim_name, "claim_uid": claim.get("metadata", {}).get("uid"),
                           **{key: allocation.get(key) for key in ("request", "driver", "pool", "device")}})
    return result


class Kube:
    def __init__(self, out):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)

    def call(self, *args, json_result=False):
        command = ["kubectl", "--request-timeout=25s", *map(str, args)]
        with (self.out / "commands.log").open("a") as handle:
            handle.write(utc() + " " + shlex.join(command) + "\n")
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "kubectl failed")
        return json_response(completed.stdout) if json_result else completed.stdout

    def get(self, resource, *args):
        return self.call("get", resource, *args, "-o", "json", json_result=True)


class Selection:
    def __init__(self, out, run_id):
        self.out, self.run_id = Path(out), run_id
        self.kube = Kube(out)
        self.selector = BOOK + "=dra-book," + INSTANCE + "=" + run_id
        self.samplers = []
        self.task_ids = {}

    def namespace_owned(self):
        namespace = self.kube.get("namespace", NS, "--ignore-not-found")
        if not namespace.get("metadata"):
            return False
        labels = namespace["metadata"].get("labels", {})
        if labels.get(BOOK) != "dra-book" or labels.get(INSTANCE) != self.run_id:
            raise ValueError("Namespace belongs to another run; cleanup that run first")
        resources = self.kube.call("api-resources", "--namespaced=true", "--verbs=list", "-o", "name").splitlines()
        inventory = []
        for resource in sorted(set(resources) - {"events", "events.events.k8s.io"}):
            inventory.extend(self.kube.get(resource, "-n", NS)["items"])
        known = {namespace["metadata"]["uid"]}
        for item in inventory:
            meta, kind = item["metadata"], item["kind"]
            labels = meta.get("labels", {})
            if labels.get(BOOK) == "dra-book" and labels.get(INSTANCE) == self.run_id:
                known.add(meta["uid"])
            # Kubernetes creates these two resources inside every Namespace.
            if (kind, meta["name"]) in (("ConfigMap", "kube-root-ca.crt"), ("ServiceAccount", "default")):
                known.add(meta["uid"])
        changed = True
        while changed:
            changed = False
            for item in inventory:
                meta = item["metadata"]
                if meta["uid"] not in known and any(owner.get("controller") and owner.get("uid") in known for owner in meta.get("ownerReferences", [])):
                    known.add(meta["uid"])
                    changed = True
        identities = [{"kind": item["kind"], "name": item["metadata"]["name"], "uid": item["metadata"]["uid"]} for item in inventory]
        foreign = [item for item in identities if item["uid"] not in known]
        # Keep only identities; discovered Secrets and annotations are not evidence.
        save(self.out / "namespace-ownership.json", {"objects": identities, "foreign": foreign})
        if foreign:
            raise ValueError("foreign objects in the lab Namespace: " + json.dumps(foreign))
        return True

    def snapshot(self, directory, stage):
        directory = Path(directory)
        pods = self.kube.get("pods", "-n", NS, "-l", self.selector)
        claims = self.kube.get("resourceclaims", "-n", NS, "-l", self.selector)
        save(directory / ("pods-" + stage + ".json"), pods)
        save(directory / ("claims-" + stage + ".json"), claims)
        save(directory / "events.json", self.kube.get("events", "-n", NS))
        (directory / "events.yaml").write_text(self.kube.call("get", "events", "-n", NS, "-o", "yaml"))
        save(directory / ("resourceslices-" + stage + ".json"), self.kube.get("resourceslices"))
        self.collect_logs(directory, pods)
        return pods, claims

    def collect_logs(self, directory, pods):
        for pod in pods.get("items", []):
            statuses = pod.get("status", {}).get("containerStatuses", [])
            if not any(row.get("name") == "workload" and (row.get("state", {}).get("running") or row.get("state", {}).get("terminated")) for row in statuses):
                continue
            job = next((owner["name"] for owner in pod["metadata"].get("ownerReferences", []) if owner["kind"] == "Job"), pod["metadata"]["name"])
            logfile = Path(directory) / "logs" / (job + ".log")
            logfile.parent.mkdir(exist_ok=True)
            logfile.write_text(self.kube.call("logs", pod["metadata"]["name"], "-n", NS, "-c", "workload"))

    def wait(self, check, message, timeout=300):
        deadline = time.monotonic() + timeout
        while True:
            result = check()
            if result:
                return result
            if time.monotonic() >= deadline:
                raise TimeoutError(message)
            time.sleep(1)

    def render(self, part):
        slices = self.kube.get("resourceslices")
        save(self.out / "resourceslices-preflight.json", slices)
        save(self.out / "version.json", self.kube.call("get", "--raw", "/version", json_result=True))
        save(self.out / "deviceclass.json", self.kube.get("deviceclass", "gpu.nvidia.com" if part == "native" else "hami-core-gpu.project-hami.io"))
        if part == "native":
            for node in ("dra-a40", "dra-4090"):
                publications = [sl for sl in slices["items"] if sl["spec"].get("nodeName") == node]
                if len(publications) != 1 or publications[0]["spec"]["driver"] != "gpu.nvidia.com" or len(publications[0]["spec"]["devices"]) != 1 or publications[0]["spec"]["devices"][0].get("allowMultipleAllocations"):
                    raise ValueError("native part needs one exclusive NVIDIA publication on each GPU node")
        manifest = self.kube.call("kustomize", LAB / "selection" / part, "--load-restrictor=LoadRestrictionsNone")
        (self.out / "rendered.yaml").write_text(manifest)
        objects = self.kube.call("create", "--dry-run=client", "--validate=false", "-f", self.out / "rendered.yaml", "-o", "json", json_result=True)
        items = objects.get("items", [objects])
        base = next(item for item in items if item["kind"] == "Job")
        items = [item for item in items if item["kind"] != "Job"]
        variants = [("native-" + case + suffix, "prefer-4090-gpu" if case == "prefer-4090" else "native-" + case + "-gpu", False)
                    for case in NATIVE_CASES for suffix in (["-primary", "-secondary"] if case == "any-gpu-pair" else [""])] + [
                        ("native-prefer-4090-holder", "native-rtx4090-gpu", False)] if part == "native" else [
                        ("capacity-h1-worker", "capacity-8gi-gpu", False),
                        ("capacity-h2-worker", "capacity-30gi-gpu", False),
                        ("capacity-pending-worker", "capacity-30gi-gpu", False),
                        ("capacity-parallel-primary", "capacity-8gi-gpu", False),
                        ("capacity-parallel-secondary", "capacity-8gi-gpu", False),
                        *[("capacity-holder-" + role, "capacity-holder-" + role + "-gpu", False) for role in ("pending", "a40", "rtx4090")]]
        for name, claim, explicit in variants:
            job = copy.deepcopy(base)
            job["metadata"]["name"] = name
            for meta in (job["metadata"], job["spec"]["template"]["metadata"]):
                meta["labels"]["app.kubernetes.io/component"] = name
            job["spec"]["template"]["spec"]["containers"][0]["args"][1] = name
            reference = job["spec"]["template"]["spec"]["resourceClaims"][0]
            reference.pop("resourceClaimTemplateName")
            reference["resourceClaimName" if explicit else "resourceClaimTemplateName"] = claim
            items.append(job)
        for obj in items:
            obj["metadata"].setdefault("labels", {})[INSTANCE] = self.run_id
            if obj["kind"] == "Job":
                obj["spec"]["template"]["metadata"]["labels"][INSTANCE] = self.run_id
                args = obj["spec"]["template"]["spec"]["containers"][0]["args"]
                args[args.index("--run-id") + 1] = self.run_id
            elif obj["kind"] == "ResourceClaimTemplate":
                obj["spec"]["metadata"]["labels"][INSTANCE] = self.run_id
            elif obj["kind"] == "ConfigMap" and obj["metadata"]["name"] == "selection-control":
                obj["data"]["control.json"] = json.dumps({"run_id": self.run_id, "release": {}})
        if part == "capacity":
            nodes = {}
            for sl in slices["items"]:
                if sl["spec"]["driver"] != "hami-core-gpu.project-hami.io":
                    continue
                node = sl["spec"].get("nodeName")
                if node not in ("dra-a40", "dra-4090"):
                    continue
                if node in nodes or len(sl["spec"]["devices"]) != 1:
                    raise ValueError("expected one HAMi GPU per node")
                device = sl["spec"]["devices"][0]
                if not device.get("allowMultipleAllocations"):
                    raise ValueError("HAMi GPU does not publish consumable capacity")
                cap = device["capacity"]
                if quantity(cap["cores"]["value"]) < 75:
                    raise ValueError("holder and target need at least 75 published cores")
                nodes[node] = (quantity(cap["memory"]["value"]), quantity(cap["memory"]["requestPolicy"]["validRange"]["step"]))
            if set(nodes) != {"dra-a40", "dra-4090"} or nodes["dra-a40"][1] != nodes["dra-4090"][1]:
                raise ValueError("capacity part needs HAMi on both GPU nodes with one memory policy")
            quotas = holder_requests(nodes["dra-a40"][0], nodes["dra-4090"][0], nodes["dra-a40"][1])
            save(self.out / "holder-requests.json", quotas)
            for obj in items:
                if obj["kind"] == "ResourceClaimTemplate" and obj["metadata"]["name"].startswith("capacity-holder-"):
                    role = obj["metadata"]["name"].removeprefix("capacity-holder-").removesuffix("-gpu")
                    obj["spec"]["spec"]["devices"]["requests"][0]["exactly"]["capacity"]["requests"]["memory"] = quotas[role]
        save(self.out / "manifests.json", {"apiVersion": "v1", "kind": "List", "items": items})
        return items

    def apply(self, directory, name, objects):
        path = Path(directory) / (name + ".json")
        save(path, {"apiVersion": "v1", "kind": "List", "items": objects})
        (Path(directory) / (name + "-apply.txt")).write_text(self.kube.call("apply", "-f", path))

    def release(self, names):
        cm = self.kube.get("configmap", "selection-control", "-n", NS)
        value = json.loads(cm["data"]["control.json"])
        if value.get("run_id") != self.run_id:
            raise ValueError("control ConfigMap belongs to another run")
        value["release"].update({self.task_ids[name]: True for name in names})
        value.update(release_id=utc(), issued_at_ns=time.time_ns())
        patch = [{"op": "test", "path": "/metadata/uid", "value": cm["metadata"]["uid"]},
                 {"op": "test", "path": "/data/control.json", "value": cm["data"]["control.json"]},
                 {"op": "replace", "path": "/data/control.json", "value": json.dumps(value)}]
        self.kube.call("patch", "configmap", "selection-control", "-n", NS, "--type=json", "-p", json.dumps(patch))

    def wait_hold(self, directory, names):
        def check():
            pods, claims = self.snapshot(directory, "current")
            found = [row for row in placements(pods, claims) if row["job"] in names]
            for pod in pods["items"]:
                if any(row.get("state", {}).get("terminated", {}).get("exitCode", 0) for row in pod.get("status", {}).get("containerStatuses", [])):
                    raise RuntimeError("CUDA workload failed; inspect saved logs")
            if len(found) != len(names) or any(not row["claim_uid"] or not row["device"] for row in found):
                return False
            slices = json.loads((Path(directory) / "resourceslices-current.json").read_text())["items"]
            for name in names:
                path = Path(directory) / "logs" / (name + ".log")
                rows = []
                if path.exists():
                    for line in path.read_text().splitlines():
                        try:
                            rows.append(json.loads(line))
                        except ValueError:
                            continue
                if not any(row.get("event") == "HOLD" for row in rows) or not any(row.get("event") == "CORRECT" and row.get("correct") is True for row in rows):
                    return False
                allocation = next(row for row in found if row["job"] == name)
                expected_driver = "gpu.nvidia.com" if name.startswith("native-") else "hami-core-gpu.project-hami.io"
                devices = [d for sl in slices if sl["spec"]["driver"] == allocation["driver"] and sl["spec"]["pool"]["name"] == allocation["pool"] and sl["spec"].get("nodeName") == allocation["node"] for d in sl["spec"]["devices"] if d["name"] == allocation["device"]]
                ready = [row for row in rows if row.get("event") == "READY"]
                if allocation["driver"] != expected_driver or len(devices) != 1 or not ready or ready[-1].get("pod_uid") != allocation["pod_uid"] or ready[-1].get("gpu_uuid") != devices[0].get("attributes", {}).get("uuid", {}).get("string"):
                    raise ValueError("CUDA UUID/Pod UID does not match the allocated device publication")
            return found
        return self.wait(check, "CUDA allocation/correctness/hold barrier timeout")

    def complete(self, names):
        for name in names:
            self.kube.call("wait", "job/" + name, "-n", NS, "--for=condition=Complete", "--timeout=300s")

    def delete_jobs(self, names):
        for name in names:
            self.kube.call("delete", "jobs", "-n", NS, "-l", self.selector + ",app.kubernetes.io/component=" + name,
                           "--ignore-not-found", "--wait=true", "--timeout=120s")
        self.wait(lambda: not self.kube.get("pods", "-n", NS, "-l", self.selector + ",app.kubernetes.io/component in (" + ",".join(names) + ")")["items"], "deleted Job Pods remain", 120)

    def start_sampling(self, directory):
        for node in ("dra-a40", "dra-4090"):
            pods = self.kube.get("pods", "-n", "gpu-operator", "-l", "app=nvidia-driver-daemonset", "--field-selector", "spec.nodeName=" + node)["items"]
            ready = [p for p in pods if not p["metadata"].get("deletionTimestamp") and any(c.get("type") == "Ready" and c.get("status") == "True" for c in p.get("status", {}).get("conditions", []))]
            if len(ready) != 1:
                raise ValueError("expected one ready GPU Operator driver Pod on " + node)
            command = ["kubectl", "-n", "gpu-operator", "exec", ready[0]["metadata"]["name"], "-c", "nvidia-driver-ctr", "--", "nvidia-smi", "--query-gpu=timestamp,uuid,name,memory.total,memory.used,memory.free,utilization.gpu", "--format=csv", "-lms", "500"]
            log = (Path(directory) / ("gpu-" + node + ".csv")).open("w")
            error = (Path(directory) / ("gpu-" + node + "-error.txt")).open("w")
            with (self.out / "commands.log").open("a") as handle:
                handle.write(utc() + " " + shlex.join(command) + "\n")
            self.samplers.append((subprocess.Popen(command, stdout=log, stderr=error), log, error))

    def stop_sampling(self):
        failed = False
        for process, log, error in self.samplers:
            failed |= process.poll() is not None
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            log.close()
            error.close()
            failed |= len(Path(log.name).read_text().splitlines()) < 2
        self.samplers.clear()
        if failed:
            raise RuntimeError("GPU sampling exited unexpectedly; inspect gpu-*-error.txt")

    def pending(self, directory, job, seconds):
        def created():
            pods, claims = self.snapshot(directory, "pending")
            found = [row for row in placements(pods, claims) if row["job"] == job]
            return found if len(found) == 1 and found[0]["claim_uid"] else False
        before = self.wait(created, "Pending Pod/Claim was not created")
        begin = time.monotonic()
        while time.monotonic() - begin < seconds:
            pods, claims = self.snapshot(directory, "pending")
            current = [row for row in placements(pods, claims) if row["job"] == job]
            verify_same_identity(before, current)
            target = next(p for p in pods["items"] if p["metadata"]["uid"] == before[0]["pod_uid"])
            if target.get("status", {}).get("phase") != "Pending" or current[0]["node"] or current[0]["device"]:
                raise ValueError("target must remain unallocated and Pending")
            time.sleep(1)
        events = json.loads((Path(directory) / "events.json").read_text())["items"]
        if not any(event.get("reason") == "FailedScheduling" and event.get("involvedObject", {}).get("uid") == before[0]["pod_uid"] for event in events):
            raise ValueError("Pending case has no FailedScheduling event for this Pod UID")
        return before, time.monotonic() - begin

    def native_idle(self, directory):
        slices = self.kube.get("resourceslices")["items"]
        devices = {(sl["spec"]["driver"], sl["spec"]["pool"]["name"], device["name"])
                   for sl in slices if sl["spec"].get("nodeName") in ("dra-a40", "dra-4090")
                   and sl["spec"]["driver"] == "gpu.nvidia.com" for device in sl["spec"]["devices"]}
        occupied = []
        for claim in self.kube.get("resourceclaims", "-A")["items"]:
            results = ((claim.get("status", {}).get("allocation") or {}).get("devices", {}).get("results") or [])
            for result in results:
                if tuple(result.get(key) for key in ("driver", "pool", "device")) in devices:
                    occupied.append({"namespace": claim["metadata"].get("namespace"),
                                     "claim": claim["metadata"]["name"], "claim_uid": claim["metadata"]["uid"],
                                     **{key: result.get(key) for key in ("request", "driver", "pool", "device")}})
        save(Path(directory) / "idle-preflight.json", {"observed_at": utc(), "occupied": occupied})
        if occupied:
            raise ValueError("prefer-4090 idle phase needs both GPUs free of exclusive allocations")

    def prefer_case(self, directory, repeat, items, trial):
        worker, holder = "native-prefer-4090", "native-prefer-4090-holder"
        trial.update(phases=[], placements=[], task_ids={})
        self.native_idle(directory)
        for phase_name, node, request in (("idle", "dra-4090", "gpu/rtx4090"),
                                          ("rtx4090-held", "dra-a40", "gpu/a40")):
            phase_dir = Path(directory) / phase_name
            phase_dir.mkdir()
            phase = {"phase": phase_name, "expected_node": node, "expected_request": request,
                     "started_at": utc(), "verdict": "incomplete"}
            trial["phases"].append(phase)
            names = [worker, holder] if phase_name == "rtx4090-held" else [worker]
            self.task_ids = {name: name + ":prefer-4090:r" + str(repeat) + ":" + phase_name for name in names}
            phase["task_ids"] = copy.deepcopy(self.task_ids)
            trial["task_ids"][phase_name] = phase["task_ids"]
            jobs = {obj["metadata"]["name"]: copy.deepcopy(obj) for obj in items
                    if obj["kind"] == "Job" and obj["metadata"]["name"] in names}
            for name, job in jobs.items():
                args = job["spec"]["template"]["spec"]["containers"][0]["args"]
                args[args.index("--task-id") + 1] = self.task_ids[name]
            try:
                if holder in names:
                    self.apply(phase_dir, "holder", [jobs[holder]])
                    phase["holder_placements"] = self.wait_hold(phase_dir, [holder])
                    if any(row["node"] != "dra-4090" or row["request"] != "gpu" for row in phase["holder_placements"]):
                        raise ValueError("RTX 4090 holder node/subrequest does not match its exclusive allocation")
                    self.snapshot(phase_dir, "holder-allocated")
                self.apply(phase_dir, "worker", [jobs[worker]])
                allocated = self.wait_hold(phase_dir, names)
                phase["placements"] = [row for row in allocated if row["job"] == worker]
                trial["placements"].extend({"phase": phase_name, **row} for row in phase["placements"])
                if any(row["node"] != node or row["request"] != request for row in phase["placements"]):
                    raise ValueError("prefer-4090 " + phase_name + " node/subrequest does not match the expected alternative")
                self.snapshot(phase_dir, "allocated")
                self.release([worker])
                self.complete([worker])
                self.snapshot(phase_dir, "worker-completed")
                self.delete_jobs([worker])
                self.kube.call("delete", "resourceclaims", "-n", NS, "-l", self.selector + ",app.kubernetes.io/component=prefer-4090-gpu",
                               "--ignore-not-found", "--wait=true", "--timeout=120s")
                if holder in names:
                    self.release([holder])
                    self.complete([holder])
                    self.snapshot(phase_dir, "completed")
                    self.delete_jobs([holder])
                    self.kube.call("delete", "resourceclaims", "-n", NS, "-l", self.selector + ",app.kubernetes.io/component=rtx4090-gpu",
                                   "--ignore-not-found", "--wait=true", "--timeout=120s")
                pods, claims = self.snapshot(phase_dir, "after-cleanup")
                phase["claims_deleted"] = not pods["items"] and not claims["items"]
                if not phase["claims_deleted"]:
                    raise ValueError("priority phase Pod/Claim cleanup is incomplete")
                phase.update(verdict="passed", finished_at=utc())
            except Exception as error:
                phase.update(verdict="failed", error=str(error), finished_at=utc())
                raise
            finally:
                save(phase_dir / "phase.json", phase)
        trial["claims_deleted"] = all(phase["claims_deleted"] for phase in trial["phases"])
        self.snapshot(directory, "after-cleanup")

    def case(self, part, case, repeat, smoke, items):
        directory = self.out / "cases" / (case + "-r" + str(repeat))
        directory.mkdir(parents=True, exist_ok=True)
        if (directory / "trial.json").exists():
            saved = json.loads((directory / "trial.json").read_text())
            if saved.get("verdict") == "passed" and saved.get("claims_deleted"):
                return
            raise ValueError("incomplete case exists; collect/cleanup and use a new run-id")
        trial = {"case": case, "part": part, "round": repeat, "smoke": smoke, "namespace": NS,
                 "started_at": utc(), "expected": "Pending" if case == "nonexistent" else "CUDA verified", "verdict": "incomplete"}
        save(directory / "trial.json", trial)
        self.snapshot(directory, "before")
        names = JOBS[case] if part == "capacity" else ["native-" + case + suffix for suffix in (["-primary", "-secondary"] if case == "any-gpu-pair" else [""])]
        holder_names = ["capacity-holder-pending"] if case == "h3h4-pending-release" else (["capacity-holder-a40", "capacity-holder-rtx4090"] if case == "h5-two-slots" else [])
        # Unique control keys prevent an older ConfigMap projection from releasing
        # a new Pod that reuses the same role name in a later round.
        self.task_ids = {name: name + ":" + case + ":r" + str(repeat) for name in names + holder_names}
        trial["task_ids"] = self.task_ids
        items = copy.deepcopy(items)
        for obj in items:
            if obj["kind"] == "Job" and obj["metadata"]["name"] in self.task_ids:
                arguments = obj["spec"]["template"]["spec"]["containers"][0]["args"]
                arguments[arguments.index("--task-id") + 1] = self.task_ids[obj["metadata"]["name"]]
        try:
            self.release_reset()
            self.start_sampling(directory)
            if case == "prefer-4090":
                self.prefer_case(directory, repeat, items, trial)
                trial.update(verdict="passed", finished_at=utc())
                return
            if holder_names:
                self.apply(directory, "holders", [obj for obj in items if obj["metadata"]["name"] in holder_names + [name + "-gpu" for name in holder_names]])
                self.wait_hold(directory, holder_names)
                self.snapshot(directory, "holders")
            self.apply(directory, "workers", [obj for obj in items if obj["kind"] == "Job" and obj["metadata"]["name"] in names])
            if case in ("nonexistent", "h3h4-pending-release"):
                pending_started = utc()
                before, elapsed = self.pending(directory, names[0], 10 if smoke else 30)
                trial["pending_seconds"] = elapsed
                save(directory / "timestamps.json", {"pending_started_at": pending_started, "holder_release_requested_at": utc()})
                if case == "nonexistent":
                    trial["placements"] = before
                    self.snapshot(directory, "allocated")
                else:
                    self.release(holder_names)
                    self.complete(holder_names)
                    self.delete_jobs(holder_names)
                    self.kube.call("delete", "resourceclaims", "-n", NS, "-l", self.selector + ",app.kubernetes.io/component=capacity-holder-pending-gpu", "--ignore-not-found", "--wait=true", "--timeout=120s")
                    after = self.wait_hold(directory, names)
                    verify_same_identity(before, after)
                    trial.update(same_pod_uid=True, same_claim_uid=True, placements=after)
                    timing = json.loads((directory / "timestamps.json").read_text())
                    timing["allocated_observed_at"] = utc()
                    save(directory / "timestamps.json", timing)
                    self.snapshot(directory, "allocated")
            else:
                trial["placements"] = self.wait_hold(directory, names)
                self.snapshot(directory, "allocated")
            if case in ("any-gpu-pair", "h5-two-slots") and len({p["node"] for p in trial["placements"]}) != 2:
                raise ValueError("two concurrent requests must allocate different GPUs")
            if case == "a40" or case in ("attribute", "h2-30gi-any", "h3h4-pending-release"):
                if any(p["node"] != "dra-a40" for p in trial["placements"]):
                    raise ValueError("request did not select the eligible A40 node")
            if case == "rtx4090" and any(p["node"] != "dra-4090" for p in trial["placements"]):
                raise ValueError("request did not select the RTX 4090 node")
            if case != "nonexistent":
                self.release(names + holder_names)
                self.complete(names + holder_names if case == "h5-two-slots" else names)
                self.snapshot(directory, "completed")
            self.delete_jobs(names + (holder_names if case == "h5-two-slots" else []))
            self.kube.call("delete", "resourceclaims", "-n", NS, "-l", self.selector, "--ignore-not-found", "--wait=true", "--timeout=120s")
            pods, claims = self.snapshot(directory, "after-cleanup")
            trial["claims_deleted"] = not claims["items"] and not pods["items"]
            if not trial["claims_deleted"]:
                raise ValueError("Pod/Claim cleanup is incomplete")
            trial.update(verdict="passed", finished_at=utc())
        except Exception as error:
            trial.update(verdict="failed", error=str(error), finished_at=utc())
            raise
        finally:
            try:
                self.stop_sampling()
            except RuntimeError as error:
                trial.update(verdict="failed", error=str(error), finished_at=utc())
                raise
            finally:
                save(directory / "trial.json", trial)

    def release_reset(self):
        cm = self.kube.get("configmap", "selection-control", "-n", NS)
        self.kube.call("patch", "configmap", "selection-control", "-n", NS, "--type=json", "-p", json.dumps([
            {"op": "test", "path": "/metadata/uid", "value": cm["metadata"]["uid"]},
            {"op": "replace", "path": "/data/control.json", "value": json.dumps({"run_id": self.run_id, "release": {}})}]))

    def cleanup(self):
        if not self.namespace_owned():
            return
        self.kube.call("delete", "jobs,pods,resourceclaims,resourceclaimtemplates,configmaps", "-n", NS, "-l", self.selector,
                       "--ignore-not-found", "--wait=true", "--timeout=120s")
        self.kube.call("delete", "namespaces", "-l", self.selector + ",app.kubernetes.io/component=heterogeneous-lab",
                       "--ignore-not-found", "--wait=true", "--timeout=120s")
        self.kube.call("wait", "--for=delete", "namespace/" + NS, "--timeout=120s")
        save(self.out / "cleanup-verification.json", {"namespace_deleted": True, "observed_at": utc()})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "collect", "cleanup"))
    parser.add_argument("run_id")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?", args.run_id):
        parser.error("run-id must fit a Kubernetes instance label")
    out = LAB / "out" / args.run_id
    session = Selection(out, args.run_id)
    if args.action == "cleanup":
        session.cleanup()
    elif args.action == "collect":
        if session.namespace_owned():
            session.snapshot(out / "collection", "current")
        print(out)
    else:
        part = os.environ.get("PART", "native")
        if part not in ("native", "capacity"):
            parser.error("PART must be native or capacity")
        smoke = os.environ.get("SMOKE", "0") == "1"
        cases = select_cases(part, os.environ.get("CASES", ""), smoke)
        metadata = {"part": part, "smoke": smoke, "cases": cases, "started_at": utc()}
        if (out / "run.json").exists():
            previous = json.loads((out / "run.json").read_text())
            if any(previous.get(key) != metadata[key] for key in ("part", "smoke", "cases")):
                raise ValueError("run-id parameters differ; choose a new run-id")
        else:
            save(out / "run.json", metadata)
        lock = out / ".selection-lock"
        lock.mkdir()
        try:
            session.namespace_owned()
            items = session.render(part)
            session.apply(out, "base", [item for item in items if item["kind"] in ("Namespace", "ConfigMap", "ResourceClaimTemplate")])
            for case in cases:
                for repeat in range(1, 2 if smoke else 4):
                    session.case(part, case, repeat, smoke, items)
            print(out)
        finally:
            session.stop_sampling()
            lock.rmdir()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, TimeoutError, FileExistsError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
