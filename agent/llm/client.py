import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


def build_llm() -> ChatOpenAI:
    """根据环境变量构造 ChatOpenAI（LLM 客户端）。"""
    load_dotenv()
    base_url = os.getenv("LUCKBOT_MODEL_BASE_URL", "https://api.openai.com/v1")
    api_key = os.getenv("LUCKBOT_MODEL_API_KEY")
    model = os.getenv("LUCKBOT_MODEL_NAME", "gpt-4o-mini")

    if not api_key:
        raise ValueError("环境变量中缺少 LUCKBOT_MODEL_API_KEY。")

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.2,
    )
