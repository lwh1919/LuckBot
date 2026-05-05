"""pytest 入口：保证从任意 cwd 运行 tests 时均能 import luckbot.*。"""

from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


@pytest.fixture(autouse=True)
def _restore_os_environ() -> None:
    """避免用例间环境变量串扰。"""
    snapshot = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(snapshot)
