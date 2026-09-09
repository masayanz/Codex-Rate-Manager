"""Paths to resources shipped with the application.

The helper works both from a source checkout and from a PyInstaller one-file
bundle (where resources are unpacked below ``sys._MEIPASS``).
"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(relative_path: str | Path) -> Path:
    """Return an absolute path for a bundled or source-tree resource."""

    relative = Path(relative_path)
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    return (base / relative).resolve()

