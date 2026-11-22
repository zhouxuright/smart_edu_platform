import logging
import httpx
from typing import Dict, Any, Optional
from edu_app.core.config import settings

logger = logging.getLogger(__name__)

class ModelRouter:
    """
    V4.0.0 端云协同模型智能路由器 (Model Router)
    将简单推导/格式化计算分发给边缘/端侧小模型 (SLM)，复杂推理分发给云端大模型 (LLM)，降本增效
    """
    def __init__(self):
        self.cloud_base = (settings.LLM_API_BASE or "").strip()
        self.cloud_key = (settings.LLM_API_KEY or "").strip()
        self.cloud_model = settings.LLM_MODEL or "gpt-4o"
        
        # 假定边缘小模型 HTTP Endpoint (如本地 vLLM 部署的 Qwen2.5-Math-7B)
        self.edge_base = "http://localhost:8000/v1"
        self.edge_model = "qwen2.5-math-7b"

    def classify_task_complexity(self, query: str) -> str:
        """
        判断任务复杂度类别
        - 'simple': 算术求值、公式语法检查、拼写矫正 -> 路由到边缘 SLM
        - 'complex': 概念推导、CoT启发式辅导、错题诊断 -> 路由到云端 LLM
        """
        query_lower = query.lower()
        simple_keywords = ["计算", "求解", "等于多少", "语法检查", "拼写", "1+", "2+", "sqrt", "sin("]
        
        if any(kw in query_lower for kw in simple_keywords) and len(query) < 50:
            return "simple"
        return "complex"

    async def dispatch_request(self, messages: list, task_type: Optional[str] = None) -> str:
        """
        智能路由并发派请求
        """
        last_msg = messages[-1]["content"] if messages else ""
        if not task_type:
            task_type = self.classify_task_complexity(last_msg)

        if task_type == "simple" and not settings.LLM_DISABLE:
            logger.info("ModelRouter: Dispatching to EDGE SLM (Qwen-Math)")
            try:
                # 尝试分发至端侧小模型
                return await self._call_openai_api(self.edge_base, "dummy_key", self.edge_model, messages)
            except Exception as e:
                logger.warning(f"ModelRouter: Edge SLM failed ({e}), falling back to Cloud LLM")

        # 默认分发至云端模型
        logger.info(f"ModelRouter: Dispatching to CLOUD LLM ({self.cloud_model})")
        return await self._call_openai_api(self.cloud_base, self.cloud_key, self.cloud_model, messages)

    async def _call_openai_api(self, base_url: str, api_key: str, model: str, messages: list) -> str:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.5,
            "max_tokens": 1024
        }
        timeout = httpx.Timeout(15.0)
        async with httpx.AsyncClient(timeout=timeout, base_url=base_url) as client:
            resp = await client.post("/chat/completions", headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", "")
            raise Exception(f"API Error {resp.status_code}: {resp.text}")

model_router = ModelRouter()
