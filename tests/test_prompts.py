from movie_kg.prompts import load_prompt, render


def test_load_prompt_reads_file():
    text = load_prompt("event_extraction")
    assert "剧情分析专家" in text and "<<SRT>>" in text


def test_render_replaces_tokens():
    out = render("A=<<A>>, B=<<B>>", A="1", B="2")
    assert out == "A=1, B=2" and "<<" not in out
