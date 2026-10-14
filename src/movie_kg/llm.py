from typing import Any

from pydantic import BaseModel, ValidationError


class LLMError(Exception):
    pass


class LLMClient:
    """OpenAI 兼容端点封装:结构化 JSON 输出 + 重试 + 用量统计。"""

    def __init__(self, cfg, client: Any = None):
        from openai import OpenAI
        self.cfg = cfg
        # SDK 内部不重试(重试统一由 chat_json 掌管);300s 上限防挂起连接
        self._client = client or OpenAI(base_url=cfg.base_url, api_key=cfg.api_key,
                                        timeout=300.0, max_retries=0)
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def chat_json(self, *, model: str, messages: list[dict], schema: type[BaseModel],
                  temperature: float = 0.1, max_tokens: int = 4096,
                  max_retries: int = 3) -> BaseModel:
        import openai
        last_err: Exception | None = None
        for _ in range(max_retries):
            try:
                resp = self._client.chat.completions.create(
                    model=model, messages=messages, temperature=temperature,
                    max_tokens=max_tokens, response_format={"type": "json_object"})
            except (openai.APITimeoutError, openai.APIConnectionError) as e:
                # 网络抖动/挂起:按次重试;耗尽后 LLMError → 场景级失败,
                # 不掀翻整个构建(§5.2 失败策略)
                last_err = e
                continue
            self._add_usage(resp)
            try:
                return schema.model_validate_json(resp.choices[0].message.content or "")
            except ValidationError as e:
                last_err = e
        raise LLMError(f"LLM 调用失败({max_retries} 次重试后): {last_err}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.cfg.embedding_model, input=texts)
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]

    def _add_usage(self, resp) -> None:
        u = getattr(resp, "usage", None)
        if u is not None:
            self.usage["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
            self.usage["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
