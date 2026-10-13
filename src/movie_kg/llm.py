from typing import Any

from pydantic import BaseModel, ValidationError


class LLMError(Exception):
    pass


class LLMClient:
    """OpenAI 兼容端点封装:结构化 JSON 输出 + 重试 + 用量统计。"""

    def __init__(self, cfg, client: Any = None):
        from openai import OpenAI
        self.cfg = cfg
        self._client = client or OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def chat_json(self, *, model: str, messages: list[dict], schema: type[BaseModel],
                  temperature: float = 0.1, max_tokens: int = 4096,
                  max_retries: int = 3) -> BaseModel:
        last_err: Exception | None = None
        for _ in range(max_retries):
            resp = self._client.chat.completions.create(
                model=model, messages=messages, temperature=temperature,
                max_tokens=max_tokens, response_format={"type": "json_object"})
            self._add_usage(resp)
            try:
                return schema.model_validate_json(resp.choices[0].message.content or "")
            except ValidationError as e:
                last_err = e
        raise LLMError(f"JSON 校验失败({max_retries} 次重试后): {last_err}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.cfg.embedding_model, input=texts)
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]

    def _add_usage(self, resp) -> None:
        u = getattr(resp, "usage", None)
        if u is not None:
            self.usage["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
            self.usage["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
