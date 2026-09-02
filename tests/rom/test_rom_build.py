#! python3
# coding: utf-8
"""ROM-required tests. These NEVER run in CI -- there is no ROM there.

Point them at your own ROMs and run them locally:

    FFTA_US_ROM=/path/to/FFTA_US.gba FFTA_JP_ROM=/path/to/FFTA_JP.gba \
        python -m pytest tests/rom -q

On Windows PowerShell:

    $env:FFTA_US_ROM = "C:\\path\\to\\FFTA_US.gba"
    $env:FFTA_JP_ROM = "C:\\path\\to\\FFTA_JP.gba"
    python -m pytest tests/rom -q

Without both variables every test here skips, so the suite is safe to collect
anywhere.  See docs/LOCAL_ROM_QA.md.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import build as builder  # noqa: E402

US = os.environ.get("FFTA_US_ROM")
JP = os.environ.get("FFTA_JP_ROM")

needs_roms = pytest.mark.skipif(
    not (US and JP and Path(US).is_file() and Path(JP).is_file()),
    reason="set FFTA_US_ROM and FFTA_JP_ROM to run (see docs/LOCAL_ROM_QA.md)")


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


@needs_roms
def test_supplied_roms_are_the_supported_revisions():
    assert sha256(US) == builder.PRISTINE_US, "FFTA_US_ROM is not the supported US ROM"
    assert sha256(JP) == builder.PRISTINE_JP, "FFTA_JP_ROM is not the supported JP ROM"


@needs_roms
def test_build_reproduces_the_pinned_release(tmp_path):
    """The whole point: your ROMs must produce exactly this release."""
    out = tmp_path / "out.gba"
    proc = subprocess.run(
        [sys.executable, str(REPO / "build.py"), "--us", US, "--jp", JP,
         "--output", str(out), "--work-dir", str(tmp_path / "work")],
        capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out.is_file()
    assert sha256(out) == builder.EXPECTED_OUTPUT


@needs_roms
def test_build_is_deterministic(tmp_path):
    """Two independent runs must agree byte for byte."""
    outs = []
    for i in (1, 2):
        out = tmp_path / f"out{i}.gba"
        proc = subprocess.run(
            [sys.executable, str(REPO / "build.py"), "--us", US, "--jp", JP,
             "--output", str(out), "--work-dir", str(tmp_path / f"work{i}")],
            capture_output=True, text=True, cwd=str(REPO))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]
    assert hashlib.sha256(outs[0]).hexdigest().upper() == builder.EXPECTED_OUTPUT


@needs_roms
def test_localized_output_is_rejected_as_an_input(tmp_path):
    """Feeding the build its own output back must fail the hash gate."""
    out = tmp_path / "out.gba"
    subprocess.run(
        [sys.executable, str(REPO / "build.py"), "--us", US, "--jp", JP,
         "--output", str(out), "--work-dir", str(tmp_path / "work")],
        capture_output=True, text=True, cwd=str(REPO), check=True)
    proc = subprocess.run(
        [sys.executable, str(REPO / "build.py"), "--us", str(out), "--jp", JP,
         "--output", str(tmp_path / "again.gba"),
         "--work-dir", str(tmp_path / "work2")],
        capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode != 0
    assert "already-localized" in (proc.stdout + proc.stderr)
    assert not (tmp_path / "again.gba").exists()


@needs_roms
def test_swapped_roms_are_rejected(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(REPO / "build.py"), "--us", JP, "--jp", US,
         "--output", str(tmp_path / "out.gba"),
         "--work-dir", str(tmp_path / "work")],
        capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode != 0
    assert "swapped" in (proc.stdout + proc.stderr)


@needs_roms
def test_public_audit_rom_substring_check_passes():
    """No tracked file may reproduce a long contiguous run of ROM bytes."""
    proc = subprocess.run(
        [sys.executable, str(REPO / "tools/public_audit.py"), "--us", US, "--jp", JP],
        capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "rom-substring" in proc.stdout
    assert "SKIP  rom-substring" not in proc.stdout
