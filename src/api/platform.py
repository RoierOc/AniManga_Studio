"""OS detection helpers, shared by anime.py, webdav.py, cbz.py and friends.

Consolidates what used to be separately-duplicated `_is_wsl()` /
`_is_wsl2()` checks so there's a single source of truth for "what OS/
environment is this process running under" across the codebase.
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    """True for native Linux *and* WSL (WSL is a Linux kernel)."""
    return sys.platform.startswith("linux")


def first_windows_user_dir() -> Path | None:
    """Return the first real per-user dir under /mnt/c/Users (WSL only),
    skipping the synthetic system accounts Windows always creates."""
    users_root = Path("/mnt/c/Users")
    if not is_wsl() or not users_root.exists():
        return None
    skip = {"All Users", "Default", "Default User", "Public", "TEMP"}
    for user in sorted(users_root.iterdir()):
        if user.name in skip or user.name.startswith("TEMP."):
            continue
        return user
    return None
