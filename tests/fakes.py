from types import SimpleNamespace


def _completion(content: str, pt: int = 10, ct: int = 5):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct))


class FakeOpenAI:
    """LLMClient 的底层替身:按队列返回文本或抛异常,记录全部调用。"""

    def __init__(self, replies: list):
        self._replies = list(replies)
        self.calls: list[dict] = []
        outer = self

        class Completions:
            def create(self, **kw):
                outer.calls.append(kw)
                r = outer._replies.pop(0)
                if isinstance(r, Exception):
                    raise r
                return _completion(r)

        class Embeddings:
            def create(self, **kw):
                outer.calls.append(kw)
                n = len(kw["input"])
                return SimpleNamespace(data=[
                    SimpleNamespace(index=i, embedding=[0.1, 0.2]) for i in range(n)])

        self.chat = SimpleNamespace(completions=Completions())
        self.embeddings = Embeddings()


class ScriptedLLM:
    """高层替身:chat_json 按队列返回 schema 校验后的对象或抛异常。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[tuple] = []
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def chat_json(self, *, model, messages, schema, **kw):
        self.calls.append((model, messages))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return schema.model_validate(r) if isinstance(r, dict) else r

    def embed(self, texts):
        return [[0.0] * 8 for _ in texts]


class MockSession:
    """neo4j Session 替身:记录查询并按队列回放记录列表。"""

    def __init__(self, results: list | None = None):
        self.queries: list[tuple[str, dict]] = []
        self._results = list(results or [])

    def run(self, query, **params):
        self.queries.append((query, params))
        return list(self._results.pop(0)) if self._results else []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class MockDriver:
    def __init__(self, session: MockSession):
        self._session = session

    def session(self, **kw):
        return self._session

    def close(self):
        pass
