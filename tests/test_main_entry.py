from __future__ import annotations

import importlib.util
from pathlib import Path


def test_main_module_exports_entrypoint() -> None:
    main_path = Path(__file__).resolve().parents[1] / "main.py"
    spec = importlib.util.spec_from_file_location("luckbot_main", main_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)
