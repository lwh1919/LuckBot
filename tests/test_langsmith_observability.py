from __future__ import annotations

import importlib
from uuid import UUID

import pytest

langsmith_module = importlib.import_module("luckbot.core.observability.langsmith")


class _FakeClient:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.updated: list[dict[str, object]] = []

    def create_run(self, **kwargs: object) -> None:
        self.created.append(dict(kwargs))

    def update_run(self, run_id: object, **kwargs: object) -> None:
        item = dict(kwargs)
        item["run_id"] = run_id
        self.updated.append(item)


class _FailingUpdateClient(_FakeClient):
    def update_run(self, run_id: object, **kwargs: object) -> None:
        super().update_run(run_id, **kwargs)
        raise RuntimeError("conflict")


@pytest.mark.asyncio
async def test_start_langsmith_run_creates_and_updates_root_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(langsmith_module, "_CLIENT", fake)
    monkeypatch.setattr(langsmith_module, "Client", object)
    monkeypatch.setenv("LUCKBOT_LANGSMITH_ENABLED", "1")
    monkeypatch.setenv("LUCKBOT_LANGSMITH_PROJECT", "luckbot")
    langsmith_module.load_observability_settings.cache_clear()

    async with langsmith_module.start_langsmith_run(
        "root",
        run_type="chain",
        inputs={"hello": "world"},
        run_id="12345678123456781234567812345678",
    ) as run:
        run.end(outputs={"ok": True})

    assert len(fake.created) == 1
    assert len(fake.updated) == 1
    created = fake.created[0]
    assert created["name"] == "root"
    assert created["project_name"] == "luckbot"
    assert created["trace_id"] == UUID("12345678-1234-5678-1234-567812345678")
    assert created["parent_run_id"] is None


@pytest.mark.asyncio
async def test_start_langsmith_run_nests_child_under_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(langsmith_module, "_CLIENT", fake)
    monkeypatch.setattr(langsmith_module, "Client", object)
    monkeypatch.setenv("LUCKBOT_LANGSMITH_ENABLED", "1")
    monkeypatch.setenv("LUCKBOT_LANGSMITH_PROJECT", "luckbot")
    langsmith_module.load_observability_settings.cache_clear()

    async with langsmith_module.start_langsmith_run(
        "root",
        run_type="chain",
        run_id="12345678123456781234567812345678",
    ):
        async with langsmith_module.start_langsmith_run(
            "child",
            run_type="tool",
        ) as child:
            child.end(outputs={"ok": True})

    assert len(fake.created) == 2
    root, child = fake.created
    assert child["id"] != root["id"]
    assert child["parent_run_id"] == root["id"]
    assert child["trace_id"] == root["trace_id"]
    assert str(child["dotted_order"]).startswith(str(root["dotted_order"]) + ".")


@pytest.mark.asyncio
async def test_start_langsmith_run_does_not_retry_failed_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FailingUpdateClient()
    monkeypatch.setattr(langsmith_module, "_CLIENT", fake)
    monkeypatch.setattr(langsmith_module, "Client", object)
    monkeypatch.setenv("LUCKBOT_LANGSMITH_ENABLED", "1")
    monkeypatch.setenv("LUCKBOT_LANGSMITH_PROJECT", "luckbot")
    langsmith_module.load_observability_settings.cache_clear()

    async with langsmith_module.start_langsmith_run(
        "root",
        run_type="chain",
    ) as run:
        run.end(outputs={"ok": True})

    assert len(fake.created) == 1
    assert len(fake.updated) == 1
