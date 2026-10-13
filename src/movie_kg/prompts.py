from pathlib import Path

PROMPT_DIR = Path("config/prompts")


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def render(template: str, **tokens: str) -> str:
    out = template
    for key, value in tokens.items():
        out = out.replace(f"<<{key.upper()}>>", str(value))
    return out
