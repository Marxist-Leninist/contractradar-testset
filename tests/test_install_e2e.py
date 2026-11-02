"""First-run install, then generation with and without Poppler.

Poppler stays installed on the machine. A run that must not see it gets a
PATH with only make and the installer. Generation writes to a temp directory.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _which(name: str) -> str | None:
    """Find a tool on PATH, skipping the interpreter running pytest.

    `uv run` puts its own python3 first, and that build has no ensurepip.
    The no-uv install has to use the python3 a developer would, such as Homebrew.
    """
    skip = {os.path.realpath(os.path.dirname(sys.executable))}
    entries = []
    for entry in os.environ["PATH"].split(os.pathsep):
        if not entry:
            continue
        real = os.path.realpath(entry)
        if real in skip:
            continue
        if "/uv/" in real:
            continue
        entries.append(entry)
    return shutil.which(name, path=os.pathsep.join(entries))


def _stage(tmp_path: Path) -> None:
    shutil.copy(ROOT / "Makefile", tmp_path / "Makefile")
    shutil.copy(ROOT / "requirements.txt", tmp_path / "requirements.txt")
    shutil.copytree(
        ROOT / "crtest",
        tmp_path / "crtest",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(ROOT / "schema", tmp_path / "schema")


def _path_with(directory: Path, names: list[str]) -> str:
    """A PATH with the named tools and ordinary system directories, and no Poppler.

    System directories are kept so `python3 -m venv` can run ensurepip. A
    directory that itself contains pdftoppm is left out, which is the whole
    point of the missing-package run.
    """
    directory.mkdir()
    for name in names:
        source = _which(name)
        if source is None:
            pytest.skip(f"{name} is not on PATH")
        os.symlink(source, directory / name)
    system = []
    for entry in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if any((Path(entry) / tool).exists() for tool in ("pdftoppm", "pdftotext")):
            continue
        system.append(entry)
    return os.pathsep.join([str(directory), *system])


def _install(cwd: Path, path: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = path
    return subprocess.run(
        ["make", "install"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def _generate(cwd: Path, path: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = path
    return subprocess.run(
        [str(cwd / ".venv" / "bin" / "python"), "-m", "crtest.gen", "--out", str(cwd / "generated"), "--only", "CR-001"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def test_empty_env_installs_without_poppler_and_gen_names_the_gap(tmp_path):
    _stage(tmp_path)
    path = _path_with(tmp_path / "bin", ["make", "uv", "python3"])
    result = _install(tmp_path, path)
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "Traceback" not in output
    assert "not on PATH" not in output
    assert "Next: make cases" in output
    assert not (tmp_path / "cases").exists()

    python = tmp_path / ".venv" / "bin" / "python"
    assert python.is_file()
    imported = subprocess.run([str(python), "-c", "import reportlab, pypdf, PIL"], capture_output=True, text=True)
    assert imported.returncode == 0, imported.stderr

    blocked = _generate(tmp_path, path)
    blocked_output = blocked.stdout + blocked.stderr
    assert blocked.returncode != 0
    assert "Traceback" not in blocked_output
    assert "FileNotFoundError" not in blocked_output
    assert "pdftoppm: not on PATH" in blocked_output
    assert "brew install poppler" in blocked_output
    assert not (tmp_path / "generated").exists()


def test_empty_env_without_uv_installs_without_poppler(tmp_path):
    _stage(tmp_path)
    path = _path_with(tmp_path / "bin", ["make", "python3"])
    result = _install(tmp_path, path)
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "Traceback" not in output
    assert "not on PATH" not in output
    assert (tmp_path / ".venv" / "bin" / "python").is_file()


def test_gen_renders_one_case_once_poppler_is_visible(tmp_path):
    poppler = _which("pdftoppm")
    if poppler is None:
        pytest.skip("poppler is not installed")
    probe = subprocess.run([poppler, "-v"], capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("pdftoppm is on PATH but does not start")

    _stage(tmp_path)
    installed = _install(tmp_path, os.environ["PATH"])
    assert installed.returncode == 0, installed.stdout + installed.stderr

    rendered = _generate(tmp_path, os.environ["PATH"])
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    case = tmp_path / "generated" / "CR-001"
    assert (case / "document.pdf").is_file()
    assert (case / "gold.json").is_file()
    assert (case / "pages" / "page-1.png").is_file()
    assert not (tmp_path / "cases").exists()
