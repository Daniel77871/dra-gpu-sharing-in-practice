import re
import subprocess
import sys

import pytest

from conftest import ROOT


def ip(*parts):
    return ".".join(map(str, parts))


@pytest.mark.parametrize("kind,value", [
    ("private-ip", ip(10, 2, 3, 4)),
    ("private-ip", ip(172, 16, 0, 1)),
    ("private-ip", ip(192, 168, 1, 1)),
    ("credential", "pass" + "word: " + "example-value"),
    ("credential", "pass" + "wd = '" + "example-value'"),
    ("credential", '"sec' + 'ret": "' + 'example-value"'),
    ("bearer", "Bear" + "er " + "abc123456789"),
    ("private-key", "-----BEGIN " + "RSA PRIVATE KEY-----"),
    ("kubeconfig", "client-" + "key-data: " + "example"),
    ("kubeconfig", "to" + "ken: " + "example"),
    ("aws-key", "AK" + "IA" + "A" * 16),
    ("github-token", "gh" + "p_" + "a" * 36),
    ("github-token", "github_" + "pat_" + "a" * 40),
    ("hf-token", "h" + "f_" + "a" * 30),
    ("slack-token", "xo" + "xb-" + "123456789-abcdef"),
    ("email", "reader" + "@" + "example.invalid"),
])
def test_detects_and_masks_every_required_kind(kind, value):
    from tools.scan_sensitive import scan_text
    findings = scan_text("example.txt", "prefix\n" + value + "\n")
    assert any(f.kind == kind and f.line == 2 for f in findings)
    assert all(value not in f.masked for f in findings)


def test_private_ip_boundaries_safe_values_and_allowlist():
    from tools.scan_sensitive import scan_text
    safe = "\n".join([ip(172, 15, 0, 1), ip(172, 32, 0, 1), ip(8, 8, 8, 8), ip(10, 999, 1, 1)])
    assert scan_text("safe.md", safe) == []
    value = "reader" + "@" + "example.invalid"
    assert scan_text("test.txt", value, [re.compile(re.escape(value))]) == []


def test_scans_git_inventory_without_reading_ignored_files(tmp_path, monkeypatch):
    from tools import scan_sensitive
    (tmp_path / "ignored.txt").write_text(ip(10, 1, 1, 1))
    (tmp_path / "public.md").write_text("安全內容\n")
    def git_inventory(command, **kwargs):
        assert command == ["git", "ls-files", "-co", "--exclude-standard", "-z"]
        assert kwargs["cwd"] == tmp_path
        return subprocess.CompletedProcess(command, 0, stdout=b"public.md\0", stderr=b"")
    monkeypatch.setattr(scan_sensitive.subprocess, "run", git_inventory)
    assert scan_sensitive.scan_repository(tmp_path, []) == []
    (tmp_path / "public.md").write_text(ip(10, 1, 1, 1))
    findings = scan_sensitive.scan_repository(tmp_path, [])
    assert len(findings) == 1 and findings[0].path == "public.md"
    assert ip(10, 1, 1, 1) not in findings[0].masked
