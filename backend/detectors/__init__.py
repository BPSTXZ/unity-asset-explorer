"""游戏引擎自动识别。返回结构化报告，供前端展示与解析器路由。"""
from __future__ import annotations

import os
import struct
from pathlib import Path

UNITY_MAGIC = (b"UnityFS", b"UnityWeb", b"UnityRaw", b"UnityArchive")

# 候选 bundle 扩展名：直接纳入清单，扫描阶段 UnityPy 会自动跳过非 bundle 文件，
# 避免识别期对海量文件逐个打开读魔数（曾导致识别耗时 80s+）
BUNDLE_EXTS = {".assets", ".unity3d", ".bundle", ".unityweb", ".dat", ".bytes", ".bin"}

SUPPORTED_ENGINES = {"unity": "Unity", "unreal": "Unreal Engine", "godot": "Godot"}


def _read_magic(path: Path, n: int = 16) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _is_unity_bundle(path: Path) -> bool:
    return _read_magic(path).startswith(UNITY_MAGIC)


def _find_data_dir(root: Path) -> Path | None:
    for d in root.iterdir():
        if d.is_dir() and d.name.endswith("_Data"):
            return d
    # 游戏目录本身可能就是 *_Data
    if root.name.endswith("_Data"):
        return root
    return None


def _unity_version_from_file(path: Path) -> str | None:
    """从 SerializedFile / Bundle 头部提取 Unity 版本字符串。"""
    try:
        data = path.read_bytes()[:4096]
    except OSError:
        return None
    # 版本字符串形如 2021.3.16f1，直接在整个头部里找可打印模式
    import re
    m = re.search(rb"\d{4,5}\.\d\.\d{1,2}[fabp]\d{1,3}", data)
    if m:
        return m.group().decode()
    m = re.search(rb"\d\.\d\.\d[fabp]\d{1,3}", data)
    return m.group().decode() if m else None


def detect_unity(root: Path) -> dict | None:
    exe_dir = root
    data_dir = _find_data_dir(root)
    has_player = (root / "UnityPlayer.dll").exists()
    if not has_player and data_dir is None:
        # 也许用户直接选了 *_Data 或资源文件夹
        if not any(root.glob("*.assets")) and not any(root.glob("*.unity3d")):
            return None

    il2cpp = (root / "GameAssembly.dll").exists() or (
        data_dir is not None and (data_dir / "il2cpp_data").exists()
    )
    mono = data_dir is not None and (data_dir / "Managed").exists()

    version = None
    asset_files: list[str] = []
    search_dirs = [d for d in (data_dir, root) if d is not None]
    seen: set[Path] = set()
    for d in search_dirs:
        for f in sorted(d.iterdir()):
            if not f.is_file() or f in seen:
                continue
            name = f.name.lower()
            if name in ("globalgamemanagers", "globalgamemanagers.build") or name.startswith("level"):
                asset_files.append(str(f)); seen.add(f)
            elif f.suffix in BUNDLE_EXTS:
                asset_files.append(str(f)); seen.add(f)
            elif f.suffix == "" and _is_unity_bundle(f):
                asset_files.append(str(f)); seen.add(f)
            if version is None and name in ("globalgamemanagers", "data.unity3d"):
                version = _unity_version_from_file(f)
        # StreamingAssets 里常藏 bundle
        sa = d / "StreamingAssets"
        if sa.is_dir():
            for f in sorted(sa.rglob("*")):
                if f.is_file() and f not in seen and (
                    f.suffix in BUNDLE_EXTS or (f.suffix == "" and _is_unity_bundle(f))
                ):
                    asset_files.append(str(f)); seen.add(f)

    if not asset_files and not has_player:
        return None
    return {
        "engine": "unity",
        "engine_label": "Unity",
        "confidence": "high" if has_player else "medium",
        "unity_version": version,
        "scripting_backend": "IL2CPP" if il2cpp else ("Mono" if mono else "unknown"),
        "data_dir": str(data_dir) if data_dir else str(root),
        "asset_files": asset_files,
        "notes": (
            ["IL2CPP 游戏：MonoBehaviour 脚本字段可能缺少类型信息，模型/贴图/动画不受影响"]
            if il2cpp else []
        ),
    }


def detect_unreal(root: Path) -> dict | None:
    paks = list(root.rglob("*.pak")) + list(root.rglob("*.utoc"))
    if not paks:
        return None
    return {
        "engine": "unreal",
        "engine_label": "Unreal Engine",
        "confidence": "medium",
        "asset_files": [str(p) for p in paks[:200]],
        "notes": ["Unreal 解析适配器尚未接入（规划中：CUE4Parse CLI）"],
    }


def detect_godot(root: Path) -> dict | None:
    pcks = list(root.glob("*.pck"))
    if not pcks:
        return None
    return {
        "engine": "godot",
        "engine_label": "Godot",
        "confidence": "medium",
        "asset_files": [str(p) for p in pcks],
        "notes": ["Godot 解析适配器尚未接入"],
    }


def detect_container(root: Path) -> dict | None:
    """APK/OBB（安卓 Unity 容器）：解包/直读内部 bundle，复用 Unity 管线。"""
    from .container import find_containers, prepare_containers
    from ..paths import CACHE_DIR
    containers = find_containers(root)
    if not containers:
        return None
    cache_root = CACHE_DIR / "containers"
    prep = prepare_containers(containers, cache_root)
    if not prep["asset_files"]:
        return None
    return {
        "engine": "unity",
        "engine_label": "Unity · Android",
        "confidence": "high",
        "unity_version": prep["version"],
        "scripting_backend": "IL2CPP",
        "data_dir": str(cache_root),
        "asset_files": prep["asset_files"],
        "container_files": [str(c) for c in containers],
        "notes": [f"Android 容器：{', '.join(prep['containers'])}"] + prep["notes"],
    }


def detect(path: str) -> dict:
    root = Path(path)
    if not root.exists():
        return {"engine": None, "error": f"路径不存在: {path}"}
    if root.is_file():
        if root.suffix.lower() in (".apk", ".obb"):
            r = detect_container(root)
            if r:
                r["root"] = str(root)
                r["supported"] = True
                return r
            return {"engine": None, "error": "容器内未发现可解析的 Unity 资源"}
        if _is_unity_bundle(root) or root.suffix in (".assets", ".unity3d", ".bundle"):
            return {
                "engine": "unity", "engine_label": "Unity", "confidence": "high",
                "root": str(root),
                "unity_version": _unity_version_from_file(root),
                "scripting_backend": "unknown",
                "data_dir": str(root.parent),
                "asset_files": [str(root)],
                "notes": ["单文件模式"],
            }
        return {"engine": None, "error": "无法识别该文件类型"}
    for fn in (detect_container, detect_unity, detect_unreal, detect_godot):
        r = fn(root)
        if r:
            r["root"] = str(root)
            r["supported"] = r["engine"] == "unity"
            return r
    return {"engine": None, "error": "未能识别引擎（未发现 Unity/Unreal/Godot 特征文件）"}