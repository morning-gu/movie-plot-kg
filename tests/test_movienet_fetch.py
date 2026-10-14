import io
import json
import struct
import tarfile
import zipfile

import pytest

from movie_kg.ingestion.movienet.fetch import (
    extract_movie_files, parse_central_dir)


def _cd_entry(name: bytes, extra: bytes) -> bytes:
    # 46 字节固定头: csize/usize/lho 用 0xFFFFFFFF 占位(zip64 extra 提供真值)
    return (struct.pack("<IHHHHHHIII", 0x02014B50,
                        45, 45, 0, 8, 0, 0,
                        0, 0xFFFFFFFF, 0xFFFFFFFF)
            + struct.pack("<HHH", len(name), len(extra), 0)
            + struct.pack("<HHII", 0, 0, 0, 0xFFFFFFFF)
            + name + extra)


def test_parse_central_dir_reads_zip64_extra():
    # 大文件的尺寸/偏移在 zip64 extra 中,基础字段是 0xFFFFFFFF 占位符
    extra = struct.pack("<HHQQQ", 1, 24, 1000, 1100, 0xFFFFFFF0)
    buf = (_cd_entry(b"240P/tt0000001.tar", extra)
           + _cd_entry(b"240P/tt0000002.tar", b"") + b"PK\x05\x06" + b"\0" * 18)
    idx = parse_central_dir(buf)
    assert set(idx) == {"240P/tt0000001.tar", "240P/tt0000002.tar"}
    e = idx["240P/tt0000001.tar"]
    assert e == {"usize": 1000, "csize": 1100, "lho": 0xFFFFFFF0}


def _zbytes(mapping: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in mapping.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_base_pkg(path, with_meta=True, script_movie="tt0000001",
                   subtitle_movie="tt0000001"):
    # 成员名带 "./" 前缀——对齐真实 MovieNet.tar.gz 的形状(tar czf 产物)
    zips = {
        "./files/annotation.v1.zip": _zbytes(
            {"annotation/annotation/tt0000001.json":
             json.dumps({"imdb_id": "tt0000001"})}),
        "./files/subtitle1K.v1.zip": _zbytes(
            {f"subtitle/subtitle/{subtitle_movie}.srt":
             "1\n00:00:00,500 --> 00:00:02,000\nhi\n"}),
        "./files/script1K.v1.zip": _zbytes(
            {f"script/script/{script_movie}.script":
             "INT. OFFICE\n\n   ALICE\n hi\n"}),
        "./files/poster4M.meta.v1.json": b"{}",
    }
    if with_meta:
        zips["./files/meta.v1.zip"] = _zbytes(
            {"meta/tt0000001.json": json.dumps({"title": "Mini"})})
    with tarfile.open(path, "w:gz") as tf:
        for member, data in zips.items():
            ti = tarfile.TarInfo(member)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))


def test_extract_movie_files_selective(tmp_path):
    pkg = tmp_path / "MovieNet.tar.gz"
    _make_base_pkg(pkg)
    src = tmp_path / "src"
    extract_movie_files(pkg, "tt0000001", src)
    assert json.loads((src / "annotation" / "tt0000001.json").read_text())[
        "imdb_id"] == "tt0000001"
    assert (src / "subtitle" / "tt0000001.srt").exists()
    assert (src / "script" / "tt0000001.script").exists()
    assert json.loads((src / "meta" / "tt0000001.json").read_text())[
        "title"] == "Mini"
    assert not list(src.rglob("poster*"))            # poster4M 不落地


def test_extract_movie_files_requires_core_zips(tmp_path):
    # annotation/subtitle/meta 缺一即报错;script 缺失仅告警(479/815 部有剧本)
    pkg = tmp_path / "MovieNet.tar.gz"
    _make_base_pkg(pkg, with_meta=False)
    with pytest.raises(RuntimeError):
        extract_movie_files(pkg, "tt0000001", tmp_path / "src")


def test_extract_movie_files_missing_script_inner_warns(tmp_path, capsys):
    # 剧本 zip 在而片内文件不在(479/815 部无剧本的常态):
    # 不报错、不落地,走告警路径;其余文件照常抽取
    pkg = tmp_path / "MovieNet.tar.gz"
    _make_base_pkg(pkg, script_movie="tt0000999")
    src = tmp_path / "src"
    extract_movie_files(pkg, "tt0000001", src)
    assert not (src / "script" / "tt0000001.script").exists()
    assert (src / "annotation" / "tt0000001.json").exists()
    assert (src / "subtitle" / "tt0000001.srt").exists()
    assert "警告" in capsys.readouterr().out


def test_extract_movie_files_missing_required_inner_errors(tmp_path):
    # 必需源文件缺失(zip 在而片内文件不在)→ 报错并指明具体文件
    pkg = tmp_path / "MovieNet.tar.gz"
    _make_base_pkg(pkg, subtitle_movie="tt0000999")
    with pytest.raises(RuntimeError, match=r"tt0000001\.srt"):
        extract_movie_files(pkg, "tt0000001", tmp_path / "src")


def test_extract_movie_files_skips_existing(tmp_path):
    pkg = tmp_path / "MovieNet.tar.gz"
    _make_base_pkg(pkg)
    src = tmp_path / "src"
    extract_movie_files(pkg, "tt0000001", src)
    (src / "annotation" / "tt0000001.json").write_text('{"imdb_id": "kept"}')
    extract_movie_files(pkg, "tt0000001", src)   # 幂等:已存在不覆盖
    assert "kept" in (src / "annotation" / "tt0000001.json").read_text()
