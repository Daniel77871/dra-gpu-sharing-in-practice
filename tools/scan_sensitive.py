#!/usr/bin/env python3
"""Scan tracked and non-ignored files without printing credential values."""

import argparse
from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_NETWORKS = [ipaddress.ip_network((base, bits)) for base, bits in
                    [(10 << 24, 8), ((172 << 24) + (16 << 16), 12), ((192 << 24) + (168 << 16), 16)]]
PATTERNS = {
    "private-ip": re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"),
    "credential": re.compile(r'''(?i)\b(?:password|passwd|secret)["']?\s*[:=]\s*(?:["'][^"'\r\n]+["']|[^\s,;#"']+)'''),
    "bearer": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    "private-key": re.compile(r"BEGIN\s+(?:[A-Z0-9]+\s+)*PRIVATE KEY"),
    "kubeconfig": re.compile(r'''(?<![\w.-])(?:client-key-data|client-certificate-data|certificate-authority-data|token)["']?\s*:'''),
    "aws-key": re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    "github-token": re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]+\b"),
    "hf-token": re.compile(r"\bhf_[A-Za-z0-9]+\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]+\b"),
    "email": re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])"),
}


@dataclass
class Finding:
    path: str
    line: int
    kind: str
    masked: str


def load_allowlist(path):
    if not path.exists():
        return []
    patterns = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip() and not line.lstrip().startswith("#"):
            try:
                patterns.append(re.compile(line.strip()))
            except re.error as error:
                raise ValueError(f"{path.name}:{number}: invalid allowlist regex ({error})") from error
    return patterns


def scan_text(path, text, allowlist=()):
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        allowed_spans = [m.span() for pattern in allowlist for m in pattern.finditer(line)]
        for kind, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                if any(start <= match.start() and match.end() <= end for start, end in allowed_spans):
                    continue
                if kind == "private-ip":
                    try:
                        address = ipaddress.ip_address(match[0])
                    except ValueError:
                        continue
                    if not any(address in network for network in PRIVATE_NETWORKS):
                        continue
                # Full masking also protects very short credentials and source lines.
                findings.append(Finding(str(path), number, kind, f"<masked {kind}>"))
    return findings


def scan_repository(root, allowlist):
    result = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"],
                            cwd=root, check=True, capture_output=True)
    findings = []
    for raw in sorted(set(result.stdout.split(b"\0")) - {b""}):
        relative = raw.decode("utf-8", errors="surrogateescape")
        path = root / relative
        if path.is_symlink():
            content = str(path.readlink())
        elif path.is_file():
            content = path.read_bytes().decode("utf-8", errors="replace")
        else:
            continue
        findings.extend(scan_text(relative, content, allowlist))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--allowlist", type=Path)
    args = parser.parse_args(argv)
    try:
        patterns = load_allowlist(args.allowlist or args.root / "tools/sensitive-allowlist.txt")
        findings = scan_repository(args.root, patterns)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Sensitive scan failed: {type(error).__name__}")
        return 1
    for finding in findings:
        print(f"{finding.path}:{finding.line}: {finding.kind}: {finding.masked}")
    print(f"Sensitive scan: {len(findings)} hits")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
