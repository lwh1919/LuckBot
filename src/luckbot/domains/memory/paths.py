"""
记忆目录管理模块：处理文件路径解析、Markdown 文件列举、安全读取验证。

目录布局：
- ``{state_dir}/memory/{owner_id}/MEMORY.md``: 主记忆文件
- ``{state_dir}/memory/{owner_id}/memory/**/*.md``: 记忆子目录中的 Markdown 文件
- ``{state_dir}/memory/{owner_id}/index.sqlite``: SQLite 索引数据库

注意：会话 JSONL 文件仅作为对话记录，不直接纳入检索。只有当内容被写入
``memory/*.md`` 或 ``MEMORY.md`` 等长期记忆文件，并通过 ``MemoryIndex.sync`` 同步后，
才会被 ``memory_search`` 检索。

本模块只管理 memory 根目录内的静态长期记忆路径与读写白名单。
"""


from __future__ import annotations

import shutil
import hashlib
import os
from pathlib import Path

from luckbot.domains.session import default_owner_id
from luckbot.domains.session.state import resolve_state_dir

from .types import IndexSourceDocument, MemoryPaths


def extra_paths_from_env() -> list[str]:
    """逗号分隔的额外 md 绝对/相对路径，仅 ``.md`` 文件进入 ``extra_resolved``。"""
    raw = (os.getenv("LUCKBOT_MEMORY_EXTRA_PATHS", "") or "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def resolve_memory_paths(
    owner: str | None = None,
    state_dir: Path | None = None,
) -> MemoryPaths:
    """汇总当前用户的记忆根目录、markdown 子目录、索引库路径及额外 md 列表。"""
    root = state_dir or resolve_state_dir()
    oid = default_owner_id(owner or "local")
    memory_root = root / "memory" / oid
    memory_md = memory_root / "memory"
    index_db = memory_root / "index.sqlite"

    state_s = str(root.resolve())
    mem_root_s = str(memory_root.resolve())
    extra_resolved: list[str] = []
    for ep in extra_paths_from_env():
        p = Path(ep).expanduser()
        if not p.is_absolute():
            p = (root / p).resolve()
        else:
            p = p.resolve()
        if p.suffix.lower() == ".md" and p.is_file():
            extra_resolved.append(str(p))

    return MemoryPaths(
        state_dir=state_s,
        owner_id=oid,
        memory_root=mem_root_s,
        memory_md_subdir=str(memory_md.resolve()),
        index_sqlite=str(index_db.resolve()),
        extra_resolved=extra_resolved,
    )


def ensure_memory_tree(paths: MemoryPaths) -> None:
    """创建 ``memory_root`` / ``memory_md_subdir``，并保证 ``MEMORY.md`` 存在（空文件亦可）。"""
    Path(paths.memory_root).mkdir(parents=True, exist_ok=True)
    Path(paths.memory_md_subdir).mkdir(parents=True, exist_ok=True)
    mem_file = Path(paths.memory_root) / "MEMORY.md"
    if not mem_file.exists():
        mem_file.write_text("", encoding="utf-8")


def clear_memory_store(paths: MemoryPaths) -> None:
    """删除当前 owner 下整块本地记忆目录（Markdown、子目录、``index.sqlite`` 等）。

    不删除 ``LUCKBOT_MEMORY_EXTRA_PATHS`` 指向的磁盘上其他位置的文件。
    删除后调用 :func:`ensure_memory_tree` 恢复空 ``MEMORY.md`` 与目录结构。
    """
    root = Path(paths.memory_root).resolve()
    if root.exists():
        shutil.rmtree(root)
    ensure_memory_tree(paths)


def _hash_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def list_memory_documents(paths: MemoryPaths) -> list[IndexSourceDocument]:
    """返回静态记忆来源文档，供统一索引层使用。"""
    base = Path(paths.memory_root).resolve()
    out: list[IndexSourceDocument] = []

    mem = base / "MEMORY.md"
    if mem.is_file():
        content = mem.read_text(encoding="utf-8", errors="replace")
        stat = mem.stat()
        out.append(
            IndexSourceDocument(
                path="MEMORY.md",
                source="memory",
                abs_path=str(mem),
                content=content,
                content_hash=_hash_text(content),
                mtime_ms=int(stat.st_mtime * 1000),
                size=stat.st_size,
            )
        )

    sub = base / "memory"
    if sub.is_dir():
        for p in sorted(sub.rglob("*.md")):
            if p.is_file():
                rel = str(Path("memory") / p.relative_to(sub)).replace("\\", "/")
                content = p.read_text(encoding="utf-8", errors="replace")
                stat = p.stat()
                out.append(
                    IndexSourceDocument(
                        path=rel,
                        source="memory",
                        abs_path=str(p.resolve()),
                        content=content,
                        content_hash=_hash_text(content),
                        mtime_ms=int(stat.st_mtime * 1000),
                        size=stat.st_size,
                    )
                )

    for abs_ep in paths.extra_resolved:
        p = Path(abs_ep)
        rel = f"extra/{p.name}"
        content = p.read_text(encoding="utf-8", errors="replace")
        stat = p.stat()
        out.append(
            IndexSourceDocument(
                path=rel,
                source="memory",
                abs_path=str(p),
                content=content,
                content_hash=_hash_text(content),
                mtime_ms=int(stat.st_mtime * 1000),
                size=stat.st_size,
            )
        )

    return out


def _norm_rel(rel: str) -> str:
    """统一路径分隔符、去掉前导 ``/``，便于与白名单规则比较。"""
    s = rel.strip().replace("\\", "/")
    if s.startswith("/"):
        s = s[1:]
    return s


def _memory_write_rel(rel: str) -> str | None:
    r = _norm_rel(rel)
    if not r or ".." in Path(r).parts:
        return None
    if not r.lower().endswith(".md"):
        return None
    if r == "MEMORY.md":
        return r
    if r.startswith("memory/"):
        return r
    return None


def resolve_memory_write_path(rel: str, paths: MemoryPaths) -> Path | None:
    """写路径校验通过后返回绝对路径；否则 None。"""
    r = _memory_write_rel(rel)
    if r is None:
        return None
    base = Path(paths.memory_root)
    if r == "MEMORY.md":
        return (base / "MEMORY.md").resolve()
    if r.startswith("memory/"):
        return (base / r).resolve()
    return None


def _memory_read_rel(rel: str, paths: MemoryPaths) -> Path | None:
    r = _norm_rel(rel)
    if not r or ".." in Path(r).parts:
        return None
    if not r.lower().endswith(".md"):
        return None
    base = Path(paths.memory_root)
    if r == "MEMORY.md":
        return (base / "MEMORY.md").resolve()
    if r.startswith("memory/"):
        return (base / r).resolve()
    if r.startswith("extra/"):
        name = Path(r).name
        for abs_ep in paths.extra_resolved:
            if Path(abs_ep).name == name:
                return Path(abs_ep).resolve()
    return None


def resolve_memory_read_path(rel: str, paths: MemoryPaths) -> Path | None:
    """在白名单通过时返回磁盘绝对路径，否则 None（防目录穿越）。"""
    return _memory_read_rel(rel, paths)
