from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableLambda
from edu_app.core.config import settings
import logging

def _fallback_text(payload):
    try:
        if isinstance(payload, dict):
            txt = payload.get("input") or payload.get("query") or ""
        else:
            txt = ""
    except Exception:
        txt = ""
    return (
        "目标：为你的问题提供清晰解释并给出学习行动。\n"
        "思考：当前使用本地回退生成，不依赖外部模型。\n"
        "行动步骤：\n"
        "1）明确关键术语与已知/待证。\n"
        "2）查阅资料并做要点摘记。\n"
        "3）用例子演算或可视化检验理解。\n"
        "结果/下一步：整理笔记形成3条练习题并复盘。\n"
        f"问题：{txt}"
    )

def get_llm(temperature=0.7, max_tokens=None):
    base = (settings.LLM_API_BASE or "").strip()
    key = (settings.LLM_API_KEY or "").strip()
    if getattr(settings, "LLM_DISABLE", False):
        logging.info("llm.disabled -> using local fallback")
        return RunnableLambda(lambda messages: type("Resp", (), {"content": _fallback_text(messages)})())
    if not base or not key or base.startswith("http://localhost") or key.lower() == "dummy":
        logging.info("llm.misconfig -> using local fallback")
        return RunnableLambda(lambda messages: type("Resp", (), {"content": _fallback_text(messages)})())
    try:
        model_name = (getattr(settings, "LLM_MODEL", None) or ("deepseek-chat" if "deepseek.com" in base else "gpt-4o"))
        params = dict(
            model=model_name,
            openai_api_key=key,
            openai_api_base=base,
            temperature=temperature,
            max_retries=3,
            request_timeout=60
        )
        if max_tokens is not None:
            params["max_tokens"] = int(max_tokens)
        logging.info(f"llm.remote -> model={model_name} base={base} temp={temperature} max_tokens={params.get('max_tokens')}")
        return ChatOpenAI(**params)
    except Exception:
        logging.exception("llm.error -> falling back to local text")
        return RunnableLambda(lambda messages: type("Resp", (), {"content": _fallback_text(messages)})())
