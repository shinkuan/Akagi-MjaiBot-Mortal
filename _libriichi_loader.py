"""Detect OS / architecture / Python version and load the matching
prebuilt ``libriichi3p`` extension from the ``libriichi/`` subdirectory
next to this file.

The legacy layout shipped a single top-level ``libriichi3p.so`` /
``libriichi3p.pyd`` (Linux x86_64 only), which silently fails on macOS
because the binary is for the wrong OS. ``load()`` registers the right
per-platform binary in ``sys.modules['libriichi3p']`` *before* any
``from libriichi3p... import ...`` statement in ``model.py`` / ``bot.py``,
so the standard import machinery never gets to the (possibly wrong)
top-level fallback.

Callers must invoke ``load()`` explicitly — the module performs no
side effects at import time so it stays unit-testable on interpreters
that lack a matching prebuilt binary.

Layout note: this bot's prebuilt binaries live under ``libriichi/``
(no ``3p`` suffix on the directory), but the file names themselves
use the ``libriichi3p-`` prefix — that's the release3p.zip convention.

Naming convention in ``libriichi/`` (matches release3p.zip layout):
``libriichi3p-{py_major}.{py_minor}-{rust_target_triple}.{so|pyd}``
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import platform
import sys
from pathlib import Path

_NAME = "libriichi3p"
_SUBDIR = "libriichi"


def _resolve_target() -> tuple[str, str]:
    """Return ``(rust_target_triple, ext)`` for the current interpreter."""
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin":
        if machine in ("arm64", "aarch64"):
            return "aarch64-apple-darwin", ".so"
        return "x86_64-apple-darwin", ".so"
    if system == "Linux":
        return "x86_64-unknown-linux-gnu", ".so"
    if system == "Windows":
        return "x86_64-pc-windows-msvc", ".pyd"
    raise RuntimeError(f"Unsupported platform: {system}/{machine}")


def _candidate_path(here: Path) -> Path:
    target, ext = _resolve_target()
    pyver = f"{sys.version_info.major}.{sys.version_info.minor}"
    return here / _SUBDIR / f"{_NAME}-{pyver}-{target}{ext}"


def load() -> None:
    """Idempotently load the right extension under ``sys.modules[_NAME]``."""
    if _NAME in sys.modules:
        return
    here = Path(__file__).resolve().parent
    candidate = _candidate_path(here)
    if not candidate.exists():
        target, _ = _resolve_target()
        pyver = f"{sys.version_info.major}.{sys.version_info.minor}"
        raise FileNotFoundError(
            f"No prebuilt {_NAME} for Python {pyver} on {target}: "
            f"expected {candidate}"
        )
    loader = importlib.machinery.ExtensionFileLoader(_NAME, str(candidate))
    spec = importlib.util.spec_from_file_location(
        _NAME, str(candidate), loader=loader,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not build import spec for {candidate}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_NAME] = module
    spec.loader.exec_module(module)
