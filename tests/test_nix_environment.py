"""Prevent accidentally running a PyPI ELF executable on NixOS."""

from pathlib import Path
from shutil import which


def test_ruff_is_provided_by_nix() -> None:
    executable = which("ruff")
    assert executable is not None
    assert Path(executable).resolve().is_relative_to("/nix/store")
