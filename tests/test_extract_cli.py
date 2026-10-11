import os
import subprocess
import sys

import pytest

from conftest import ROOT


def test_registry_cli_rejects_unknown_experiment():
    command = [sys.executable, str(ROOT / "tools/extract_data.py")]
    result = subprocess.run(command + ["--help"], text=True, capture_output=True, env={**os.environ, "COLUMNS": "400"})
    assert result.returncode == 0
    assert "--lab" in result.stdout and "dra-gpu-oom-lab" in result.stdout
    result = subprocess.run(command + ["e99"], text=True, capture_output=True)
    assert result.returncode != 0 and "invalid choice" in result.stderr


def test_extractor_records_only_files_read_and_keeps_lab_read_only(tmp_path, monkeypatch):
    from tools import extract_data
    from tools.extractors import REGISTRY, SourceReader, register
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "sample.csv").write_text("value\n42\n")
    (lab / "unused.csv").write_text("unused\n")
    monkeypatch.setattr(extract_data, "discover", lambda: None)
    monkeypatch.setitem(REGISTRY, "e1", None)
    del REGISTRY["e1"]
    @register("e1", help="synthetic experiment")
    def extract(reader, output):
        output.joinpath("summary.csv").write_text(reader.read_text("sample.csv"))
    root = tmp_path / "repo"
    assert extract_data.main(["--root", str(root), "--lab", str(lab), "e1"]) == 0
    output = root / "data/e1"
    assert (output / "summary.csv").read_text() == "value\n42\n"
    sources = (output / "SOURCES.md").read_text()
    assert "sample.csv" in sources and "unused.csv" not in sources and str(lab) not in sources
    assert (lab / "sample.csv").read_text() == "value\n42\n"
    reader = SourceReader(lab)
    with pytest.raises(ValueError, match="outside"):
        reader.read_text("../escape")
