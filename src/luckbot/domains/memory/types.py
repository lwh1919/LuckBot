"""记忆索引与 hybrid_search 使用的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IndexSourceDocument:
    """一次索引同步中的单个来源文档。"""

    path: str  # 逻辑路径，如 memory/foo.md 或 extra/foo.md
    source: str  # 当前为 memory
    abs_path: str  # 真实磁盘路径
    content: str  # 进入 chunk/embedding/fts 的文本视图
    content_hash: str  # content 的哈希，用于增量同步
    mtime_ms: int
    size: int


@dataclass
class Chunk:
    """单条可索引文本段（来自长期记忆 Markdown 切分）。"""

    text: str
    start_line: int
    end_line: int
    chunk_hash: str  # 内容哈希，用于 embedding_cache 与稳定 chunk_id


@dataclass
class Hit:
    """检索结果中的一条命中（展示 snippet，完整 text 供工具使用）。"""

    chunk_id: str
    path: str  # 逻辑路径（相对 memory_root，如 memory/foo.md）
    source: str  # 当前索引来源为 memory
    start_line: int
    end_line: int
    text: str
    score: float  # 融合或 rerank 后的分数
    snippet: str  # 展示用正文：默认即整段 chunk，可被 LUCKBOT_MEMORY_SEARCH_SNIPPET_MAX_CHARS 截断


@dataclass
class SearchOptions:
    """hybrid_search 的行为参数：条数、阈值、向量/全文权重、预筛选与 KNN 规模。"""

    max_results: int = 6
    min_score: float = 0.35
    vector_weight: float = 0.7
    text_weight: float = 0.3
    prefilter_m: int = 256  # 仅对 FTS 高分 chunk 做余弦时的上限
    vec_knn_k: int = 64  # sqlite-vec KNN 的 k（会与 max_results、prefilter 取 max）
    rerank: bool | None = None  # True/False 覆盖 env；None 跟随 LUCKBOT_MEMORY_RERANK_MODE
    mmr: bool | None = None  # 同上，LUCKBOT_MEMORY_MMR_MODE


@dataclass
class MemoryPaths:
    """某 owner 下记忆目录与索引文件的已解析绝对路径字符串。"""

    state_dir: str
    owner_id: str
    memory_root: str  # …/memory/{owner_id}
    memory_md_subdir: str  # …/memory/{owner_id}/memory，日常 md 与 flush 写入处
    index_sqlite: str  # …/memory/{owner_id}/index.sqlite
    extra_resolved: list[str] = field(default_factory=list)  # LUCKBOT_MEMORY_EXTRA_PATHS 中的单文件 md
