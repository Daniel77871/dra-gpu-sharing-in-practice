import subprocess

from conftest import ROOT


def test_all_lab_shell_scripts_pass_bash_syntax_check():
    scripts = sorted((ROOT / "labs").rglob("*.sh"))
    assert scripts, "lab scripts have not been created"
    for script in scripts:
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
