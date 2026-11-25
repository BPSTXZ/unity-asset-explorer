"""APK/OBB 容器解包：提取内部 Unity bundle，交给现有 UnityPy 管线解析。

- APK 是 ZIP：解出 assets/bin/Data 与 StreamingAssets/aa 里的资源 bundle。
- OBB 有两种：本身即 UnityFS bundle（魔数 UnityFS，UnityPy 直读，无需解包），
  或 ZIP 容器（解包）。按魔数区分。
解包结果缓存到 .cache/containers/<容器名>/，按文件大小跳过重复解包。
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

UNITY_MAGIC = (b"UnityFS", b"UnityWeb", b"UnityRaw", b"UnityArchive")
CONTAINER_EXTS = {".apk", ".obb"}
BUNDLE_EXTS = {".assets", ".unity3d", ".bundle", ".resource", ".ress",
               ".split0", ".split1", ".split2", ".split3"}
DATA_NAMES = {"unity default resources", "globalgamemanagers",
              "globalgamemanagers.build", "resources.resource"}


def _magic(path, n: int = 16) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def is_unity_bundle_file(path) -> bool:
    return _magic(path).startswith(UNITY_MAGIC)


def is_zip(path) -> bool:
    return _magic(path, 4)[:2] == b"PK"


def find_containers(root: Path) -> list[Path]:
    """浅层查找 apk/obb（顶层 + 两级子目录），避免对大型 PC 游戏目录全量遍历。"""
    if root.is_file():
        return [root] if root.suffix.lower() in CONTAINER_EXTS else []
    found: list[Path] = []
    for pat in ("*.apk", "*.obb", "*/*.apk", "*/*.obb", "*/*/*.apk", "*/*/*.obb"):
        found.extend(root.glob(pat))
    return sorted(set(found))


def _version(path) -> str | None:
    try:
        data = Path(path).read_bytes()[:4096]
    except OSError:
        return None
    m = re.search(rb"\d{4,5}\.\d\.\d{1,2}[fabp]\d{1,3}", data)
    if m:
        return m.group().decode()
    m = re.search(rb"\d\.\d\.\d[fabp]\d{1,3}", data)
    return m.group().decode() if m else None


def _keep_entry(z: zipfile.ZipFile, info: zipfile.ZipInfo) -> bool:
    n = info.filename.replace("\\", "/")
    if n.startswith("assets/bin/Data/"):
        rel = n[len("assets/bin/Data/"):]
        if rel.startswith("Managed/"):  # dll/IL2CPP 元数据，资源解析不需要
            return False
        base = rel.split("/")[-1]
        if "/" not in rel:  # Data 顶层：核心 bundle / 流式资源
            return (Path(rel).suffix.lower() in BUNDLE_EXTS
                    or base in DATA_NAMES or base.startswith("level")
                    or (Path(rel).suffix == "" and _entry_magic(z, info)))
        return Path(rel).suffix.lower() in BUNDLE_EXTS
    if n.startswith(("assets/StreamingAssets/", "assets/aa/")):
        return (Path(n).suffix.lower() in BUNDLE_EXTS
                or (Path(n).suffix == "" and _entry_magic(z, info)))
    return False


def _entry_magic(z: zipfile.ZipFile, info: zipfile.ZipInfo) -> bool:
    try:
        with z.open(info) as f:
            return f.read(16).startswith(UNITY_MAGIC)
    except Exception:
        return False


def extract_bundles(zippath: Path, dest: Path) -> list[str]:
    out: list[str] = []
    with zipfile.ZipFile(zippath) as z:
        for info in z.infolist():
            if info.is_dir() or not _keep_entry(z, info):
                continue
            n = info.filename.replace("\\", "/")
            rel = n[len("assets/"):] if n.startswith("assets/") else n
            dst = dest / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or dst.stat().st_size != info.file_size:
                with z.open(info) as src, open(dst, "wb") as f:
                    f.write(src.read())
            out.append(str(dst))
    return out


def prepare_containers(containers: list[Path], cache_root: Path) -> dict:
    asset_files: list[str] = []
    used: list[str] = []
    notes: list[str] = []
    for c in containers:
        if c.suffix.lower() == ".obb" and is_unity_bundle_file(c):
            asset_files.append(str(c))  # 本身就是 bundle，原地直读
            used.append(c.name)
            continue
        if is_zip(c):
            files = extract_bundles(c, cache_root / c.stem)
            if files:
                asset_files.extend(files)
                used.append(c.name)
            else:
                notes.append(f"{c.name} 内未发现 Unity 资源")
            continue
        notes.append(f"无法解析容器 {c.name}（非 ZIP / Unity bundle）")
    version = next((v for f in asset_files if (v := _version(f))), None)
    return {"asset_files": asset_files, "version": version,
            "containers": used, "notes": notes}