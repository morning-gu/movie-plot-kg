from movie_kg.ingestion.timefmt import fmt_ts, parse_ts


def test_parse_ts_variants():
    assert parse_ts("00:03:12") == 192.0
    assert parse_ts("00:03:12,500") == 192.5
    assert parse_ts("00:03:12.5") == 192.5
    assert parse_ts("01:00:00") == 3600.0


def test_fmt_ts():
    assert fmt_ts(192.0) == "00:03:12"
    assert fmt_ts(210.4) == "00:03:30"   # 截断到整秒
    assert fmt_ts(3600.0) == "01:00:00"
    assert fmt_ts(parse_ts("00:03:30")) == "00:03:30"
