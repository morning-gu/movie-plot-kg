from movie_kg.eval.report import md_table


def test_md_table_renders():
    out = md_table(["指标", "值"], [["F1", 0.9], ["P", 1.0]])
    assert "| 指标 | 值 |" in out
    assert "| --- | --- |" in out
    assert "| F1 | 0.9 |" in out
