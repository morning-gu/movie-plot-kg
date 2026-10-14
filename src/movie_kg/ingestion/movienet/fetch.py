"""MovieNet 取数: OpenDataLab 匿名直链 + HTTP Range 按片下载(DESIGN §3.4)。

网络层(urlopen/Range/重试)不进单测,试点时人工验收;
解析层(parse_central_dir / extract_movie_files)为纯函数,离线可测。
"""
import io
import json
import struct
import tarfile
import time
import urllib.request
import zipfile
import zlib
from pathlib import Path

BASE_URL = "https://openxlab.org.cn/datasets/resolve/12/main"
PKG_NAME = "MovieNet.tar.gz"
PKG_SHA256 = "3fb250aa665b8cda46dd1f5291fa637f9a9083e860a0f191946e6d45c4b3e76e"
KF_ZIP_NAME = "movie1K.keyframes.240p.v1.zip"
RETRIES = 6
PAUSE = 3.0


def _open(url: str, headers: dict | None = None, timeout: int = 60):
    req = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout)


def _resolve(path: str) -> str:
    """resolve 端点 302 → 签名直链(匿名可用)。"""
    last: Exception | None = None
    for _ in range(RETRIES):
        try:
            with _open(f"{BASE_URL}/{path}") as resp:
                return resp.url
        except Exception as e:  # noqa: BLE001 - 网络抖动统一重试
            last = e
            time.sleep(PAUSE)
    raise RuntimeError(f"解析下载地址失败({path}): {last}")


def _range_get(url: str, start: int, end: int) -> bytes:
    last: Exception | None = None
    for _ in range(RETRIES):
        try:
            with _open(url, headers={"Range": f"bytes={start}-{end}"}) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(PAUSE)
    raise RuntimeError(f"Range 下载失败({start}-{end}): {last}")


def _remote_size(url: str) -> int:
    """1 字节 Range 探测总长(Content-Range: bytes 0-0/TOTAL)。"""
    with _open(url, headers={"Range": "bytes=0-0"}) as resp:
        cr = resp.headers.get("Content-Range", "")
        return int(cr.rsplit("/", 1)[1])


def download_file(path: str, target: Path, chunk_mb: int = 8) -> None:
    """断点续传下载 repo 文件(按 Content-Range 校验总长)。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    url = _resolve(path)
    expected = _remote_size(url)
    got = target.stat().st_size if target.exists() else 0
    if got > expected:
        raise RuntimeError(f"{target} 比远端大({got}>{expected}),请删除后重试")
    while got < expected:
        end = min(got + (chunk_mb << 20), expected) - 1
        data = _range_get(url, got, end)
        if not data:
            url = _resolve(path)  # 直链可能过期
            continue
        with open(target, "ab") as f:
            f.write(data)
        got += len(data)


def fetch_base(src: Path) -> Path:
    """下载基础包(887MB,一次性),sha256 校验。"""
    import hashlib
    target = src / PKG_NAME
    if target.exists() and target.stat().st_size > 0:
        return target
    download_file(f"raw/{PKG_NAME}", target)
    h = hashlib.sha256(target.read_bytes()).hexdigest()
    if h != PKG_SHA256:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"{PKG_NAME} sha256 不符(远端可能已更新): {h}")
    return target


def parse_central_dir(buf: bytes) -> dict[str, dict]:
    """zip 中央目录 → {tar 名: {usize, csize, lho}}(含 zip64 extra 解析)。"""
    entries: dict[str, dict] = {}
    pos = 0
    while pos < len(buf) - 46 and buf[pos:pos + 4] == b"PK\x01\x02":
        nlen, elen, clen = struct.unpack("<HHH", buf[pos + 28:pos + 34])
        csize = struct.unpack("<I", buf[pos + 20:pos + 24])[0]
        usize = struct.unpack("<I", buf[pos + 24:pos + 28])[0]
        lho = struct.unpack("<I", buf[pos + 42:pos + 46])[0]
        name = buf[pos + 46:pos + 46 + nlen].decode("utf-8")
        extra = buf[pos + 46 + nlen:pos + 46 + nlen + elen]
        e = 0
        while e + 4 <= len(extra):
            hid, hsz = struct.unpack("<HH", extra[e:e + 4])
            if hid == 1:  # zip64: 按占位符顺序 usize → csize → lho
                f = e + 4
                if usize == 0xFFFFFFFF:
                    usize = struct.unpack("<Q", extra[f:f + 8])[0]
                    f += 8
                if csize == 0xFFFFFFFF:
                    csize = struct.unpack("<Q", extra[f:f + 8])[0]
                    f += 8
                if lho == 0xFFFFFFFF:
                    lho = struct.unpack("<Q", extra[f:f + 8])[0]
            e += 4 + hsz
        if name.endswith(".tar"):
            entries[name] = {"usize": usize, "csize": csize, "lho": lho}
        pos += 46 + nlen + elen + clen
    return entries


def read_zip_index(cache: Path) -> dict[str, dict]:
    """读关键帧 zip 的中央目录(110KB)并缓存到本地。"""
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    cache.parent.mkdir(parents=True, exist_ok=True)
    url = _resolve(f"raw/{KF_ZIP_NAME}")
    size = _remote_size(url)
    tail = _range_get(url, size - 2048, size - 1)
    i = tail.rfind(b"PK\x06\x07")  # ZIP64 EOCD locator
    if i < 0:
        raise RuntimeError("关键帧包缺少 ZIP64 EOCD locator")
    eocd_off = struct.unpack("<Q", tail[i + 8:i + 16])[0]
    head = _range_get(url, eocd_off, eocd_off + 55)
    if head[:4] != b"PK\x06\x06":
        raise RuntimeError("关键帧包缺少 ZIP64 EOCD")
    cd_off = struct.unpack("<Q", head[48:56])[0]
    cd_size = struct.unpack("<Q", head[40:48])[0]
    entries = parse_central_dir(_range_get(url, cd_off, cd_off + cd_size - 1))
    if not entries:
        raise RuntimeError("关键帧包索引为空")
    cache.write_text(json.dumps(entries), encoding="utf-8")
    return entries


def fetch_keyframes(movie: str, src: Path) -> Path:
    """按片下载关键帧 tar(Range 拉取 deflate 流 → 流式解压,原子落盘)。"""
    target = src / "keyframes" / f"{movie}.tar"
    if target.exists() and target.stat().st_size > 0:
        return target
    index = read_zip_index(src / "kf_zip_index.json")
    key = f"240P/{movie}.tar"
    if key not in index:
        raise RuntimeError(f"关键帧包中无 {movie}")
    e = index[key]
    url = _resolve(f"raw/{KF_ZIP_NAME}")
    hdr = _range_get(url, e["lho"], e["lho"] + 29)
    if hdr[:4] != b"PK\x03\x04":
        raise RuntimeError(f"{movie}: zip 本地文件头异常")
    nlen, elen = struct.unpack("<HH", hdr[26:30])
    data_off = e["lho"] + 30 + nlen + elen

    # 先写临时文件,完成并校验大小后原子改名——中断不会留下"看似完整"的半成品
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    got = 0
    d = zlib.decompressobj(-15)
    with open(tmp, "wb") as out:
        while got < e["csize"]:
            end = min(got + (8 << 20), e["csize"]) - 1
            chunk = _range_get(url, data_off + got, data_off + end)
            if not chunk:
                url = _resolve(f"raw/{KF_ZIP_NAME}")
                continue
            out.write(d.decompress(chunk))
            got += len(chunk)
        out.write(d.flush())
    if tmp.stat().st_size != e["usize"]:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"{movie}: 解压后大小 {tmp.stat().st_size if tmp.exists() else 0}"
            f" != {e['usize']}")
    tmp.replace(target)
    return target


_ZIP_MEMBERS = {  # 基础包内 zip → (解出的目录, 文件名格式)
    "files/annotation.v1.zip": ("annotation", "{m}.json"),
    "files/subtitle1K.v1.zip": ("subtitle", "{m}.srt"),
    "files/script1K.v1.zip": ("script", "{m}.script"),
    "files/meta.v1.zip": ("meta", "{m}.json"),
}
_REQUIRED = {"annotation", "subtitle", "meta"}


def extract_movie_files(pkg: Path, movie: str, src: Path) -> None:
    """从基础包选择性抽取该片四类源文件(跳过 poster4M;已存在不覆盖)。"""
    wanted = {member: (d, fmt.format(m=movie)) for member, (d, fmt)
              in _ZIP_MEMBERS.items()}
    found: set[str] = set()
    with tarfile.open(pkg, "r:gz") as tf:
        for member in tf:  # 顺序扫描一次
            if member.name not in wanted:
                continue
            dest_dir, dest_name = wanted[member.name]
            dest = src / dest_dir / dest_name
            found.add(dest_dir)
            if dest.exists():
                continue
            fobj = tf.extractfile(member)
            if fobj is None:
                continue
            with zipfile.ZipFile(io.BytesIO(fobj.read())) as zf:
                inner = next((n for n in zf.namelist() if n.endswith(dest_name)),
                             None)
                if inner is None:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(inner))
    missing = _REQUIRED - found
    if missing:
        raise RuntimeError(f"基础包缺少 {sorted(missing)} 的源 zip")
    if "script" not in found:
        print(f"警告: 基础包中无 {movie} 的剧本,台词将全部走「剧本未匹配」丢弃路径")
