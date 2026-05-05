import os
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


def _normalize_openai_base_url(raw: str) -> str:
    """兼容只填站点根路径的 OpenAI 网关配置，自动补成 ``/v1`` API 根。"""
    base = (raw or "").strip()
    if not base:
        return ""

    parts = urlsplit(base)
    path = parts.path.rstrip("/")
    if path in ("",):
        path = "/v1"
    normalized = urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))
    return normalized


def build_llm() -> ChatOpenAI:
    """根据环境变量构造 ChatOpenAI（LLM 客户端）。

    ``LUCKBOT_MODEL_BASE_URL`` / ``LUCKBOT_MODEL_API_KEY`` / ``LUCKBOT_MODEL_NAME`` 须全部非空，
    源码不设默认值，未配置则抛出 ``ValueError``。

    LuckBot 允许 ``base_url`` 指向第三方 OpenAI 兼容后端；为避免 ``Responses API``
    在非标准响应上的兼容问题，默认固定走传统 Chat Completions 输出格式。
    """
    load_dotenv()
    base_url = _normalize_openai_base_url(
        (os.getenv("LUCKBOT_MODEL_BASE_URL") or "").strip()
    )
    api_key = (os.getenv("LUCKBOT_MODEL_API_KEY") or "").strip()
    model = (os.getenv("LUCKBOT_MODEL_NAME") or "").strip()

    missing = [
        name
        for name, val in (
            ("LUCKBOT_MODEL_BASE_URL", base_url),
            ("LUCKBOT_MODEL_API_KEY", api_key),
            ("LUCKBOT_MODEL_NAME", model),
        )
        if not val
    ]
    if missing:
        raise ValueError(
            "以下环境变量必须非空（无默认值）：" + "、".join(missing)
        )

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.2,
        use_responses_api=False,
        output_version="v0",
    )
