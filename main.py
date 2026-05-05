from __future__ import annotations

import os
import sys

try:
    from luckbot.entrypoints.cli import main
except ModuleNotFoundError:
    # 兼容直接在源码 checkout 中执行 `python main.py`
    _ROOT = os.path.dirname(os.path.abspath(__file__))
    _SRC = os.path.join(_ROOT, "src")
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)
    from luckbot.entrypoints.cli import main


if __name__ == "__main__":
    main()
