from pydantic import BaseModel

from movie_kg.prompts import load_prompt, render
from movie_kg.qa.retriever import Evidence


class AnswerOutput(BaseModel):
    answer: str
    cited_ids: list[str] = []


class AnswerResult(BaseModel):
    answer: str
    cited_ids: list[str] = []


def answer(llm, question: str, evidence: list[Evidence]) -> AnswerResult:
    cfg = getattr(llm, "cfg", None)
    model = cfg.text_model if cfg is not None else "text"
    lines = "\n".join(f"[{e.node_id}] {e.text}" for e in evidence) or "(无证据)"
    system = render(load_prompt("qa_answering"), EVIDENCE=lines, QUESTION=question)
    raw = llm.chat_json(model=model,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": "请输出回答 JSON。"}],
                        schema=AnswerOutput)
    return AnswerResult(answer=raw.answer, cited_ids=raw.cited_ids)
