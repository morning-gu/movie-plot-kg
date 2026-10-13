from movie_kg.ingestion.srt import parse_srt, parse_ts


def test_parse_ts():
    assert parse_ts("00:03:12") == 192.0
    assert parse_ts("00:03:12,500") == 192.5
    assert parse_ts("01:00:00") == 3600.0


def test_parse_srt_multiline():
    text = "1\n00:00:01,000 --> 00:00:03,500\n第一行\n第二行\n\n2\n00:00:04,000 --> 00:00:05,000\n你到底想怎样?\n"
    entries = parse_srt(text)
    assert len(entries) == 2
    assert entries[0].text == "第一行\n第二行"
    assert entries[1].start == "00:00:04,000"
