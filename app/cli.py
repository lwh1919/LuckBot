"""Compatibility shim for stale `luckbot` console scripts.

Some existing virtual environments still point the `luckbot` entrypoint at
`app.cli:main`. Keep this tiny bridge so those environments keep working even
before the package is reinstalled.
"""

from __future__ import annotations

import os
import sys

try:
    from luckbot.entrypoints.cli import main
except ModuleNotFoundError:
    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _SRC = os.path.join(_ROOT, "src")
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)
    from luckbot.entrypoints.cli import main


__all__ = ["main"]
