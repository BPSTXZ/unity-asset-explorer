"""Asset Explorer — 游戏资源自动识别/提取/预览/导出 本地服务。"""
from __future__ import annotations

import io
import gc
import hashlib
import json
import math
import os
import re
import shutil
import string
import tempfile
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import UnityPy
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .detectors import detect
from .parsers.unity_parser import ScanJob
from .exporters.gltf import mesh_to_glb, animation_to_glb, animation_info, humanoid_export
from .exporters.character import character_to_glb, mesh_to_glb_textured, character_bone_crc
from .paths import CACHE_DIR, EXPORT_DIR, FRONTEND_DIR
from . import glossary
from . import translate
from . import modguide

CACHE_DIR.mkdir(exist_ok=True, parents=True)
DEFAULT_EXPORT_DIR = EXPORT_DIR

app = FastAPI(title="Asset Explorer")


@app.middleware("http")
async def no_cache_frontend(request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.endswith((".html", ".js", ".css")):
        # 前端更新后避免浏览器沿用旧缓存（仍可用 ETag 协商 304）
        resp.headers["Cache-Control"] = "no-cache"
    return resp

_state = {
    "detect": None,
    "scan": None,          # ScanJob
    "scan_path": None,     # 当前资源项目路径
    "assets": [],          # 扫描清单快照（仅元数据）
    "export": None,        # ExportJob
    "keyparam_job": None,  # KeyParamJob
    "keyparams": [],       # 关键参数识别结果
}

SESSION_FILE = CACHE_DIR / "session.json"


def _save_session():
    try:
        SESSION_FILE.write_text(json.dumps(
            {"detect": _state["detect"], "scan_path": _state["scan_path"],
             "assets": _state["assets"]}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _restore_session():
    try:
        d = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        _state["detect"] = d.get("detect")
        _state["scan_path"] = d.get("scan_path")
        _state["assets"] = d.get("assets") or []
    except (OSError, ValueError):
        pass


_restore_session()

# ---------- 项目历史（项目中心初始界面：已打开过的项目 + 识别状态） ----------
PROJECTS_FILE = CACHE_DIR / "projects.json"
_projects: list[dict] = []
_proj_lock = threading.Lock()


def _norm_path(p: str) -> str:
    return os.path.normpath((p or "").strip().strip('"')).lower()


def _load_projects():
    global _projects
    try:
        _projects = json.loads(PROJECTS_FILE.read_text(encoding="utf-8")) or []
    except (OSError, ValueError):
        _projects = []


def _save_projects():
    try:
        PROJECTS_FILE.write_text(json.dumps(_projects, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _find_project(path: str) -> dict | None:
    key = _norm_path(path)
    for p in _projects:
        if _norm_path(p.get("path", "")) == key:
            return p
    return None


def _upsert_project(path: str, **fields):
    if not path:
        return
    with _proj_lock:
        p = _find_project(path)
        if p is None:
            p = {"path": path, "name": Path(path).name or path,
                 "scan_state": "idle", "asset_count": 0}
            _projects.append(p)
        p.update(fields)
        p["last_opened"] = time.time()
        _save_projects()


def _set_project_scan(path: str, scan_state: str, asset_count: int | None = None):
    with _proj_lock:
        p = _find_project(path)
        if p is None:
            return
        p["scan_state"] = scan_state
        if asset_count is not None:
            p["asset_count"] = asset_count
        _save_projects()


def _forget_project(path: str):
    with _proj_lock:
        key = _norm_path(path)
        _projects[:] = [p for p in _projects if _norm_path(p.get("path", "")) != key]
        _save_projects()


_load_projects()

# 启动时若已有恢复的会话但无历史记录，补一条，保证项目中心能看到当前项目
if _state["scan_path"] and _find_project(_state["scan_path"]) is None:
    _det = _state["detect"] or {}
    _upsert_project(_state["scan_path"],
                    engine=_det.get("engine"), engine_label=_det.get("engine_label"),
                    unity_version=_det.get("unity_version"),
                    scripting_backend=_det.get("scripting_backend"),
                    supported=_det.get("supported", False),
                    asset_file_count=len(_det.get("asset_files") or []),
                    scan_state="done" if _state["assets"] else "idle",
                    asset_count=len(_state["assets"]))

# ---------- 按项目清单缓存（切换项目免重扫，除非用户移除） ----------
MANIFEST_DIR = CACHE_DIR / "manifests"
MANIFEST_DIR.mkdir(exist_ok=True)


def _manifest_file(path: str) -> Path:
    return MANIFEST_DIR / (hashlib.md5(_norm_path(path).encode("utf-8")).hexdigest()[:16] + ".json")


def _save_manifest():
    if not _state["scan_path"] or not _state["assets"]:
        return
    try:
        counts: dict[str, int] = {}
        for a in _state["assets"]:
            counts[a["type"]] = counts.get(a["type"], 0) + 1
        _manifest_file(_state["scan_path"]).write_text(json.dumps({
            "detect": _state["detect"], "scan_path": _state["scan_path"],
            "assets": _state["assets"], "type_counts": counts,
            "saved_at": time.time()}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _load_manifest(path: str) -> dict | None:
    try:
        return json.loads(_manifest_file(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------- 收藏（别名仅用于个人辨识，与原命名分离，不参与任何解析/导出） ----------
FAVORITES_FILE = CACHE_DIR / "favorites.json"
_favorites: list[dict] = []
_fav_lock = threading.Lock()


def _asset_key(meta: dict) -> str:
    return f'{meta["file_path"]}|{meta.get("cab", "")}|{meta["path_id"]}|{meta["type"]}'


def _load_favorites():
    global _favorites
    try:
        _favorites = json.loads(FAVORITES_FILE.read_text(encoding="utf-8")) or []
    except (OSError, ValueError):
        _favorites = []


def _save_favorites():
    try:
        FAVORITES_FILE.write_text(json.dumps(_favorites, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


_load_favorites()


def _resolve_favorites() -> list[dict]:
    """把收藏按稳定键映射回当前扫描清单，附上当前 aid 与完整元数据。"""
    index = {_asset_key(a): a for a in _state["assets"]}
    out = []
    for f in _favorites:
        a = index.get(f["key"])
        if a is None:
            continue
        item = dict(a)
        item["alias"] = f.get("alias") or a["name"]
        item["fav_key"] = f["key"]
        item["added"] = f.get("added", "")
        out.append(item)
    return out

# 按需加载的 UnityPy 环境 LRU 缓存，避免常驻全部文件解析对象
_env_cache: OrderedDict[str, object] = OrderedDict()
_env_lock = threading.Lock()
_ENV_CACHE_MAX = 6


def _load_env(file_path: str):
    with _env_lock:
        if file_path in _env_cache:
            _env_cache.move_to_end(file_path)
            return _env_cache[file_path]
    env = UnityPy.load(file_path)
    with _env_lock:
        _env_cache[file_path] = env
        while len(_env_cache) > _ENV_CACHE_MAX:
            _env_cache.popitem(last=False)
    return env


# 贴图材质常分布在依赖 bundle 中（外部 PPtr），需递归加载依赖才能解析。
_env_deps_cache: OrderedDict[str, object] = OrderedDict()
_ENV_DEPS_MAX = 3


def _streaming_base(file_path: str) -> Path:
    p = Path(file_path).parent
    for anc in (p, *p.parents):
        if anc.name.lower() == "streamingassets":
            return anc
    return p


def _load_env_deps(file_path: str):
    key = os.path.normpath(file_path)
    with _env_lock:
        if key in _env_deps_cache:
            _env_deps_cache.move_to_end(key)
            return _env_deps_cache[key]
    env = UnityPy.load(file_path)
    base = _streaming_base(file_path)
    seen: set = set()
    loaded = {key}
    for _ in range(8):
        new = []
        for r in env.objects:
            if r.type.name != "AssetBundle":
                continue
            try:
                deps = r.read().m_Dependencies or []
            except Exception:
                continue
            for d in deps:
                if d in seen:
                    continue
                seen.add(d)
                p = os.path.normpath(str(base / (d + ".dat")))
                if p not in loaded and os.path.exists(p):
                    loaded.add(p)
                    new.append(p)
        if not new or len(loaded) > 80:
            break
        for p in new:
            try:
                env.load_file(p)
            except Exception:
                pass
    with _env_lock:
        _env_deps_cache[key] = env
        while len(_env_deps_cache) > _ENV_DEPS_MAX:
            _env_deps_cache.popitem(last=False)
    return env


def _safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "unnamed"


def _get_reader(aid: int, deps: bool = False):
    if aid < 0 or aid >= len(_state["assets"]):
        raise HTTPException(404, "资源不存在，请先扫描")
    meta = _state["assets"][aid]
    env = _load_env_deps(meta["file_path"]) if deps else _load_env(meta["file_path"])
    for reader in env.objects:
        # path_id 仅在单个 CAB 子文件内唯一，需联合 CAB 名定位
        if reader.path_id == meta["path_id"] and \
                getattr(reader.assets_file, "name", "") == meta.get("cab", ""):
            return meta, reader
    raise HTTPException(404, "资源定位失败")


# ---------- 请求模型 ----------

class PathReq(BaseModel):
    path: str


class ScanReq(BaseModel):
    path: str | None = None
    files: list[str] | None = None


class ExportReq(BaseModel):
    ids: list[int]
    out_dir: str | None = None
    mesh_formats: list[str] = ["glb", "obj"]


# ---------- 目录浏览（文件夹 / 文件选择） ----------

# 选择器中可直接选中的资源文件类型（安卓容器 + Unity bundle）
SELECTABLE_FILE_EXTS = {
    ".apk", ".obb", ".assets", ".unity3d", ".bundle", ".unityweb",
    ".dat", ".bytes", ".bin", ".resource", ".ress",
    ".split0", ".split1", ".split2", ".split3",
}


@app.get("/api/fs/list")
def api_fs_list(path: str | None = None):
    if not path:
        drives = []
        for letter in string.ascii_uppercase:
            d = Path(f"{letter}:\\")
            if d.exists():
                drives.append({"name": f"{letter}:", "path": str(d), "type": "drive"})
        return {"path": None, "parent": None, "entries": drives}
    p = Path(path)
    if not p.exists() or not p.is_dir():
        raise HTTPException(404, "目录不存在")
    entries = []
    try:
        for child in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            if child.name.startswith("$") or child.name in ("System Volume Information",):
                continue
            if child.is_dir():
                entries.append({"name": child.name, "path": str(child), "type": "dir"})
            elif child.suffix.lower() in SELECTABLE_FILE_EXTS:
                entries.append({"name": child.name, "path": str(child), "type": "file"})
    except PermissionError:
        pass
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None,
            "entries": entries}


# ---------- 识别 / 扫描 ----------

@app.post("/api/detect")
def api_detect(req: PathReq):
    result = detect(req.path.strip().strip('"'))
    _state["detect"] = result
    if result.get("engine"):
        _upsert_project(result.get("root") or req.path,
                        engine=result.get("engine"), engine_label=result.get("engine_label"),
                        unity_version=result.get("unity_version"),
                        scripting_backend=result.get("scripting_backend"),
                        supported=result.get("supported", False),
                        asset_file_count=len(result.get("asset_files") or []))
    return result


@app.post("/api/scan")
def api_scan(req: ScanReq):
    files = req.files
    if not files:
        det = _state["detect"]
        if req.path:
            det = detect(req.path.strip().strip('"'))
            _state["detect"] = det
        if not det or not det.get("asset_files"):
            raise HTTPException(400, "请先识别引擎或指定资源文件")
        if det.get("engine") != "unity":
            raise HTTPException(400, f"暂不支持 {det.get('engine_label')} 的解析")
        files = det["asset_files"]
    _state["scan"] = ScanJob(files)
    _state["assets"] = []
    if req.files:
        _state["scan_path"] = req.path or str(Path(files[0]).parent)
    else:
        _state["scan_path"] = (_state["detect"] or {}).get("root") or req.path
    _upsert_project(_state["scan_path"], scan_state="scanning", asset_count=0)
    with _env_lock:
        _env_cache.clear()
    return {"started": True, "total_files": len(files)}


@app.get("/api/scan/status")
def api_scan_status():
    job: ScanJob | None = _state["scan"]
    if job is None:
        if _state["assets"]:
            counts: dict[str, int] = {}
            for a in _state["assets"]:
                counts[a["type"]] = counts.get(a["type"], 0) + 1
            return {"state": "done", "restored": True, "done_files": 0, "total_files": 0,
                    "asset_count": len(_state["assets"]), "type_counts": counts, "error": None}
        return {"state": "idle"}
    st = job.status()
    if st["state"] != getattr(job, "_proj_state", None):
        job._proj_state = st["state"]
        if st["state"] in ("done", "paused"):
            _set_project_scan(_state["scan_path"], st["state"], st["asset_count"])
        elif st["state"] == "error":
            _set_project_scan(_state["scan_path"], "idle")
    if st["asset_count"] and (st["state"] == "done" or len(_state["assets"]) < st["asset_count"]):
        _state["assets"] = job.snapshot()
    if st["state"] == "done" and not getattr(job, "_session_saved", False):
        job._session_saved = True
        _save_session()
        _save_manifest()
    return st


@app.post("/api/scan/pause")
def api_scan_pause():
    job: ScanJob | None = _state["scan"]
    if job is None:
        raise HTTPException(400, "无扫描任务")
    job.pause()
    return {"state": job.state}


@app.post("/api/scan/resume")
def api_scan_resume():
    job: ScanJob | None = _state["scan"]
    if job is None:
        raise HTTPException(400, "无扫描任务")
    job.resume()
    return {"state": job.state}


def _clear_preview_caches():
    """预览 PNG/GLB/缩略图按 aid 命名，跨项目会串，切换/移除时清理。"""
    try:
        for p in CACHE_DIR.glob("*.png"):
            p.unlink(missing_ok=True)
        for p in CACHE_DIR.glob("*.glb"):
            p.unlink(missing_ok=True)
    except OSError:
        pass
    shutil.rmtree(CACHE_DIR / "thumbs", ignore_errors=True)
    (CACHE_DIR / "thumbs").mkdir(exist_ok=True)


def _release_current():
    """停止当前项目后台任务并释放内存/编辑态，但保留各项目磁盘缓存与历史。"""
    for key in ("scan", "export", "keyparam_job"):
        job = _state.get(key)
        if job is not None:
            job.cancel()
    for job in _clip_jobs.values():
        job.cancel()
    _clip_jobs.clear()
    with _env_lock:
        _env_cache.clear()
        _env_deps_cache.clear()
    with _edit_lock:
        _edit_envs.clear()
        _edit_pending.clear()
    _state.update(scan=None, export=None, keyparam_job=None, keyparams=[])
    try:
        KEYPARAMS_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    _clear_preview_caches()
    gc.collect()


@app.post("/api/project/remove")
def api_project_remove():
    """移除当前项目：停止任务、清清单与该项目缓存（manifest/容器），保留其它项目与收藏。"""
    path = _state["scan_path"] or (_state["detect"] or {}).get("root")
    det = _state["detect"]
    _release_current()
    _state.update(detect=None, scan_path=None, assets=[])
    _clear_disk_caches(det)
    if path:
        _manifest_file(path).unlink(missing_ok=True)
    _forget_project(path)
    return {"removed": True}


def _clear_disk_caches(det=None):
    """删除可再生缓存：预览/缩略图、解包容器（按项目 scoped）、会话；保留收藏与其它项目。"""
    _clear_preview_caches()
    containers = CACHE_DIR / "containers"
    if det and det.get("container_files"):
        for c in det["container_files"]:
            shutil.rmtree(containers / Path(c).stem, ignore_errors=True)
    else:
        shutil.rmtree(containers, ignore_errors=True)
        if containers.exists():  # 句柄延迟释放时重试
            gc.collect()
            shutil.rmtree(containers, ignore_errors=True)
    try:
        SESSION_FILE.unlink(missing_ok=True)
        KEYPARAMS_FILE.unlink(missing_ok=True)
    except OSError:
        pass


@app.post("/api/project/open")
def api_project_open(req: PathReq):
    """打开项目：有清单缓存则即时恢复（免重扫），否则 restored=False 走识别+扫描。"""
    path = req.path.strip().strip('"')
    key = _norm_path(path)
    cur = _state["scan_path"]
    if cur and _norm_path(cur) == key and _state["assets"]:
        return {"restored": True, "already": True, "asset_count": len(_state["assets"])}
    mf = _load_manifest(path)
    if mf and mf.get("assets"):
        _release_current()
        _state["detect"] = mf.get("detect")
        _state["scan_path"] = mf.get("scan_path") or path
        _state["assets"] = mf.get("assets") or []
        _upsert_project(_state["scan_path"], scan_state="done", asset_count=len(_state["assets"]))
        _save_session()
        return {"restored": True, "asset_count": len(_state["assets"])}
    if cur and _norm_path(cur) != key:
        _release_current()
        _state.update(detect=None, scan_path=None, assets=[])
    return {"restored": False}


@app.get("/api/session")
def api_session():
    det = _state["detect"] or {}
    path = _state["scan_path"] or det.get("root")
    if not path:
        return {"active": False}
    return {"active": True, "path": path,
            "engine_label": det.get("engine_label"), "unity_version": det.get("unity_version"),
            "scripting_backend": det.get("scripting_backend"),
            "asset_file_count": len(det.get("asset_files") or []),
            "supported": det.get("supported", False),
            "asset_count": len(_state["assets"])}


@app.get("/api/projects")
def api_projects():
    """项目中心：历史项目 + 识别状态（当前项目附带实时扫描进度）。"""
    cur = _state["scan_path"] or (_state["detect"] or {}).get("root")
    curkey = _norm_path(cur) if cur else None
    job: ScanJob | None = _state["scan"]
    with _proj_lock:
        items = sorted(_projects, key=lambda p: p.get("last_opened", 0), reverse=True)
    out = []
    for p in items:
        d = dict(p)
        d["current"] = curkey is not None and _norm_path(p.get("path", "")) == curkey
        if d["current"]:
            if job is not None:
                st = job.status()
                d["scan_state"] = {"running": "scanning", "paused": "paused",
                                   "done": "done"}.get(st["state"], d.get("scan_state"))
                d["asset_count"] = st["asset_count"] or d.get("asset_count", 0)
                d["done_files"] = st["done_files"]
                d["total_files"] = st["total_files"]
            elif _state["assets"]:
                d["scan_state"] = "done"
                d["asset_count"] = len(_state["assets"])
        out.append(d)
    return {"projects": out}


@app.post("/api/projects/remove")
def api_projects_remove(req: PathReq):
    """从项目中心移除一条历史记录（不清理当前会话）。"""
    _forget_project(req.path)
    return {"removed": True}


@app.get("/api/assets")
def api_assets(type: str | None = None, search: str = "", offset: int = 0, limit: int = 200):
    items = _state["assets"]
    if type:
        items = [a for a in items if a["type"] == type]
    if search:
        s = search.lower()
        items = [a for a in items if s in a["name"].lower() or s in a["container"].lower()]
    return {"total": len(items), "items": items[offset:offset + limit]}


# ---------- CG 画廊（大图候选，复用元数据，无需解码） ----------

def _image_dims(meta: dict) -> tuple[int, int]:
    e = meta.get("extra") or {}
    if meta["type"] == "Sprite":
        r = e.get("rect") or [0, 0]
        return int(r[0]), int(r[1])
    return int(e.get("width", 0)), int(e.get("height", 0))


# 游戏通常把 CG 放进命名含 photos/cg/gallery 等的 bundle（Addressables 常见约定）。
# 按来源 bundle 名识别“游戏判定的 CG”，纯命名启发式，跨游戏通用。
_CG_SOURCE_RE = re.compile(r"(?:^|[_\-./])(photos?|cg|gallery|illust|albums?)(?:[_\-./]|$)", re.I)


def _is_cg_source(meta: dict) -> bool:
    return bool(_CG_SOURCE_RE.search(meta.get("file", "")))


@app.get("/api/gallery")
def api_gallery(min_side: int = 400, search: str = "", offset: int = 0, limit: int = 60,
                cg_only: bool = False):
    cands = []
    s = search.lower()
    cg_available = False
    for a in _state["assets"]:
        if a["type"] not in ("Texture2D", "Sprite"):
            continue
        is_cg = _is_cg_source(a)
        cg_available = cg_available or is_cg
        if cg_only and not is_cg:
            continue
        w, h = _image_dims(a)
        if min_side and min(w, h) < min_side:
            continue
        if s and s not in a["name"].lower() and s not in a.get("container", "").lower():
            continue
        cands.append((w * h, a))
    cands.sort(key=lambda x: -x[0])  # 面积降序：大幅 CG 优先
    return {"total": len(cands), "cg_available": cg_available,
            "items": [a for _, a in cands[offset:offset + limit]]}


# ---------- 收藏 ----------

class FavReq(BaseModel):
    aid: int
    alias: str = ""


class FavKeyReq(BaseModel):
    key: str
    alias: str | None = None


@app.get("/api/favorites")
def api_favorites():
    with _fav_lock:
        items = _resolve_favorites()
    return {"total": len(items), "items": items}


@app.post("/api/favorites")
def api_favorite_add(req: FavReq):
    if req.aid < 0 or req.aid >= len(_state["assets"]):
        raise HTTPException(404, "资源不存在")
    meta = _state["assets"][req.aid]
    alias = req.alias.strip()
    if not alias:
        raise HTTPException(400, "请填写收藏名称")
    if len(alias) > 60:
        raise HTTPException(400, "收藏名称过长（≤60 字）")
    key = _asset_key(meta)
    with _fav_lock:
        for f in _favorites:
            if f["key"] == key:
                f["alias"] = alias  # 已收藏则更新别名
                _save_favorites()
                return {"ok": True, "key": key, "updated": True}
        _favorites.append({
            "key": key, "type": meta["type"], "name": meta["name"],
            "alias": alias, "file_path": meta["file_path"],
            "cab": meta.get("cab", ""), "path_id": meta["path_id"],
            "added": datetime.now().isoformat(timespec="seconds"),
        })
        _save_favorites()
    return {"ok": True, "key": key}


@app.post("/api/favorites/remove")
def api_favorite_remove(req: FavKeyReq):
    with _fav_lock:
        before = len(_favorites)
        _favorites[:] = [f for f in _favorites if f["key"] != req.key]
        _save_favorites()
    return {"ok": True, "removed": before - len(_favorites)}


@app.post("/api/favorites/rename")
def api_favorite_rename(req: FavKeyReq):
    alias = (req.alias or "").strip()
    if not alias:
        raise HTTPException(400, "请填写收藏名称")
    with _fav_lock:
        for f in _favorites:
            if f["key"] == req.key:
                f["alias"] = alias
                _save_favorites()
                return {"ok": True}
    raise HTTPException(404, "收藏不存在")


# ---------- 预览 ----------

def _texture_png(data) -> bytes:
    img = data.image
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------- 列表缩略图（图片类资源，小尺寸 + 磁盘缓存） ----------

IMAGE_TYPES = ("Texture2D", "Sprite", "Cubemap")
THUMB_DIR = CACHE_DIR / "thumbs"
THUMB_DIR.mkdir(exist_ok=True)
_thumb_sem = threading.Semaphore(3)  # 限制并发解码，避免滚动时内存尖峰


@app.get("/api/thumbnail/{aid}")
def api_thumbnail(aid: int):
    if aid < 0 or aid >= len(_state["assets"]):
        raise HTTPException(404, "资源不存在，请先扫描")
    meta = _state["assets"][aid]
    if meta["type"] not in IMAGE_TYPES:
        raise HTTPException(400, "非图片资源")
    key = hashlib.md5(_asset_key(meta).encode("utf-8")).hexdigest()[:16]
    png = THUMB_DIR / f"{key}.png"
    if not png.exists():
        with _thumb_sem:
            if not png.exists():
                _, reader = _get_reader(aid)
                try:
                    img = reader.read().image
                except Exception as e:
                    raise HTTPException(500, f"缩略图生成失败: {e}")
                img.thumbnail((64, 64))
                if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                    img = img.convert("RGBA")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                png.write_bytes(buf.getvalue())
    return FileResponse(png, media_type="image/png")


@app.get("/api/preview/{aid}")
def api_preview(aid: int, refresh: bool = False, textured: bool = False):
    if aid < 0 or aid >= len(_state["assets"]):
        raise HTTPException(404, "资源不存在，请先扫描")
    tname = _state["assets"][aid]["type"]
    deps = textured and tname in ("Mesh", "Character")
    meta, reader = _get_reader(aid, deps=deps)
    cache_key = CACHE_DIR / f"{aid}_{reader.path_id}"
    try:
        data = reader.read()
    except Exception as e:
        raise HTTPException(500, f"读取失败: {e}")

    if tname in ("Texture2D", "Sprite", "Cubemap"):
        png = cache_key.with_suffix(".png")
        if not png.exists():
            png.write_bytes(_texture_png(data))
        return FileResponse(png, media_type="image/png")

    if tname == "Mesh":
        if textured:
            tex = CACHE_DIR / f"{aid}_{reader.path_id}_tex.glb"
            if refresh or not tex.exists():
                try:
                    tex.write_bytes(mesh_to_glb_textured(_load_env_deps(meta["file_path"]), reader))
                except ValueError as e:
                    raise HTTPException(404, str(e))
            return FileResponse(tex, media_type="model/gltf-binary")
        glb = cache_key.with_suffix(".glb")
        if not glb.exists():
            glb.write_bytes(mesh_to_glb(data))
        return FileResponse(glb, media_type="model/gltf-binary")

    if tname == "Character":
        suffix = "_tex.glb" if textured else "_plain.glb"
        glb = CACHE_DIR / f"{aid}_{reader.path_id}{suffix}"
        if refresh or not glb.exists():
            env = _load_env_deps(meta["file_path"]) if textured else _load_env(meta["file_path"])
            glb.write_bytes(character_to_glb(env, reader, textured=textured))
        return FileResponse(glb, media_type="model/gltf-binary")

    if tname == "AnimationClip":
        glb_bytes = animation_to_glb(data)
        if glb_bytes:
            glb = cache_key.with_suffix(".glb")
            if not glb.exists():
                glb.write_bytes(glb_bytes)
            return FileResponse(glb, media_type="model/gltf-binary")
        return {"humanoid": True, "info": animation_info(data),
                "note": "Humanoid 肌肉动画，暂以数据形式导出"}

    if tname == "AudioClip":
        samples = data.samples
        if not samples:
            raise HTTPException(404, "无音频数据")
        name, audio = next(iter(samples.items()))
        ext = Path(name).suffix.lower()
        mime = {".wav": "audio/wav", ".ogg": "audio/ogg", ".mp3": "audio/mpeg",
                ".fsb": "audio/x-fsb"}.get(ext, "application/octet-stream")
        return Response(audio, media_type=mime)

    if tname == "TextAsset":
        script = data.m_Script
        if isinstance(script, bytes):
            return Response(script, media_type="text/plain; charset=utf-8")
        return Response(str(script), media_type="text/plain; charset=utf-8")

    try:
        tree = reader.read_typetree()
        return Response(json.dumps(tree, ensure_ascii=False, indent=2, default=str),
                        media_type="application/json")
    except Exception as e:
        raise HTTPException(500, f"该类型暂不支持预览: {e}")


@app.get("/api/asset/{aid}/info")
def api_asset_info(aid: int):
    meta, reader = _get_reader(aid)
    data = reader.read()
    if meta["type"] == "AnimationClip":
        return animation_info(data)
    return meta


# ---------- 角色动画重定向 ----------

def _creature_dir(file_path: str) -> str:
    parts = Path(file_path).parts
    for i, p in enumerate(parts):
        if p == "Creature" and i + 1 < len(parts):
            return parts[i + 1]
    return ""


# ---------- 动画手动绑定 + 匹配结果缓存（避免每次重扫匹配） ----------
CLIPMATCH_DIR = CACHE_DIR / "clipmatch"
CLIPMATCH_DIR.mkdir(exist_ok=True)
BINDS_FILE = CACHE_DIR / "char_binds.json"
_binds: dict[str, list] = {}
_binds_lock = threading.Lock()


def _load_binds():
    global _binds
    try:
        _binds = json.loads(BINDS_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        _binds = {}


def _save_binds():
    try:
        BINDS_FILE.write_text(json.dumps(_binds, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


_load_binds()


def _clipmatch_file(key: str) -> Path:
    return CLIPMATCH_DIR / (hashlib.md5(key.encode("utf-8")).hexdigest()[:16] + ".json")


def _resolve_clips(entries: list) -> list:
    """把稳定键解析回当前会话 aid（重扫/切换后 aid 会变）。"""
    index = {_asset_key(a): a for a in _state["assets"]}
    out = []
    for e in entries:
        a = index.get(e.get("key", ""))
        if a is None:
            continue
        d = dict(e)
        d["id"] = a["id"]
        out.append(d)
    return out


class ClipMatchJob:
    """后台扫描与角色骨骼兼容的动画 clips（按骨骼路径 CRC 匹配打分）。"""

    def __init__(self, char_crc: set, boost_dir: str, char_key: str = ""):
        self.char_crc = char_crc
        self.boost_dir = boost_dir
        self.char_key = char_key
        self.state = "running"
        self.done = 0
        self.total = 0
        self.results: list[dict] = []
        self.error: str | None = None
        self._lock = threading.Lock()
        self._cancelled = False
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        with self._lock:
            self._cancelled = True
            self.state = "cancelled"

    def _run(self):
        try:
            clips = [a for a in _state["assets"] if a["type"] == "AnimationClip"]
            byfile: dict[str, list] = {}
            for a in clips:
                byfile.setdefault(a["file_path"], []).append(a)
            self.total = len(byfile)
            matched = []
            for fp, items in byfile.items():
                if self._cancelled:
                    break
                try:
                    env = UnityPy.load(fp)
                except Exception:
                    with self._lock:
                        self.done += 1
                    continue
                cmap = {(getattr(r.assets_file, "name", ""), r.path_id): r
                        for r in env.objects if r.type.name == "AnimationClip"}
                boost = bool(self.boost_dir) and self.boost_dir in Path(fp).parts
                for a in items:
                    r = cmap.get((a.get("cab", ""), a["path_id"]))
                    if not r:
                        continue
                    try:
                        c = r.read()
                        gb = c.m_ClipBindingConstant.genericBindings or []
                    except Exception:
                        continue
                    trb = [g for g in gb if g.typeID == 4]
                    if not trb:
                        continue
                    hit = sum(1 for g in trb if g.path in self.char_crc)
                    ratio = hit / len(trb)
                    if ratio > 0.5:
                        mc = getattr(c, "m_MuscleClip", None)
                        dur = getattr(mc, "m_StopTime", 0) if mc else 0
                        matched.append({
                            "id": a["id"], "name": a["name"], "file": a["file"],
                            "key": _asset_key(a),
                            "ratio": round(ratio, 3), "bones": hit,
                            "duration": round(dur, 3), "boost": boost,
                        })
                del env
                with self._lock:
                    self.done += 1
            matched.sort(key=lambda x: (not x["boost"], -x["ratio"], x["name"]))
            with self._lock:
                self.results = matched
            if not self._cancelled:
                self.state = "done"
                if self.char_key:
                    try:
                        _clipmatch_file(self.char_key).write_text(
                            json.dumps({"results": matched}, ensure_ascii=False), encoding="utf-8")
                    except OSError:
                        pass
        except Exception as e:
            if not self._cancelled:
                self.state = "error"
                self.error = str(e)

    def status(self):
        with self._lock:
            return {"state": self.state, "done": self.done, "total": self.total,
                    "count": len(self.results), "error": self.error,
                    "results": self.results if self.state == "done" else []}


_clip_jobs: dict[int, ClipMatchJob] = {}


@app.get("/api/character_clips/{aid}")
def api_character_clips(aid: int, refresh: bool = False):
    meta, reader = _get_reader(aid)
    if meta["type"] != "Character":
        raise HTTPException(400, "非角色资源")
    ckey = _asset_key(meta)
    if not refresh:
        try:
            cached = json.loads(_clipmatch_file(ckey).read_text(encoding="utf-8")).get("results")
        except (OSError, ValueError):
            cached = None
        if cached is not None:
            res = _resolve_clips(cached)
            return {"state": "done", "done": 0, "total": 0, "count": len(res),
                    "error": None, "results": res, "cached": True}
    job = _clip_jobs.get(aid)
    if job is None or refresh:
        crc = character_bone_crc(reader)
        job = ClipMatchJob(crc, _creature_dir(meta["file_path"]), ckey)
        _clip_jobs[aid] = job
    return job.status()


class BindReq(BaseModel):
    clip: int
    remove: bool = False


@app.get("/api/character_binds/{aid}")
def api_character_binds(aid: int):
    meta, _ = _get_reader(aid)
    with _binds_lock:
        entries = list(_binds.get(_asset_key(meta), []))
    return {"binds": _resolve_clips(entries)}


@app.post("/api/character_binds/{aid}")
def api_character_bind(aid: int, req: BindReq):
    """手动绑定/解绑动画到角色（持久化，跨会话按稳定键解析）。"""
    meta, _ = _get_reader(aid)
    cmeta, _ = _get_reader(req.clip)
    ck, ek = _asset_key(meta), _asset_key(cmeta)
    with _binds_lock:
        lst = _binds.setdefault(ck, [])
        if req.remove:
            lst[:] = [e for e in lst if e.get("key") != ek]
        elif not any(e.get("key") == ek for e in lst):
            lst.append({"key": ek, "name": cmeta["name"], "file": cmeta["file"]})
        _save_binds()
        entries = list(lst)
    return {"binds": _resolve_clips(entries)}


@app.get("/api/character_anim/{aid}")
def api_character_anim(aid: int, clip: int, textured: bool = False,
                       pose: float | None = None, refresh: bool = False):
    meta, reader = _get_reader(aid, deps=textured)
    if meta["type"] != "Character":
        raise HTTPException(400, "非角色资源")
    cmeta, creader = _get_reader(clip)
    if cmeta["type"] != "AnimationClip":
        raise HTTPException(400, "非动画资源")
    try:
        clip_data = creader.read()
    except Exception as e:
        raise HTTPException(500, f"动画读取失败: {e}")
    env = _load_env_deps(meta["file_path"]) if textured else _load_env(meta["file_path"])
    try:
        if pose is not None:
            glb = character_to_glb(env, reader, textured=textured,
                                   clip=clip_data, pose_time=pose)
            return Response(glb, media_type="model/gltf-binary")
        suffix = "_tex" if textured else "_plain"
        cache = CACHE_DIR / f"{aid}_{reader.path_id}_anim{clip}{suffix}.glb"
        if refresh or not cache.exists():
            cache.write_bytes(character_to_glb(env, reader, textured=textured, clip=clip_data))
        return FileResponse(cache, media_type="model/gltf-binary")
    except ValueError as e:
        raise HTTPException(404, str(e))


# ---------- 参数编辑（离线单机 modding，非破坏性：导出到 exports，不覆盖原文件） ----------

_edit_envs: dict[str, object] = {}   # file_path -> 已修改待导出的 UnityPy 环境
_edit_pending: dict[int, dict] = {}  # aid -> 修改记录
_edit_lock = threading.Lock()


def _get_edit_env(file_path: str):
    key = os.path.normpath(file_path)
    with _edit_lock:
        if key not in _edit_envs:
            _edit_envs[key] = UnityPy.load(file_path)
        return _edit_envs[key]


def _find_reader(env, meta: dict):
    for r in env.objects:
        if r.path_id == meta["path_id"] and \
                getattr(r.assets_file, "name", "") == meta.get("cab", ""):
            return r
    return None


def _text_format(text: str) -> str:
    s = text.lstrip()
    if s[:1] in ("{", "["):
        return "JSON"
    if s[:1] == "<":
        return "XML"
    if re.search(r"^\s*\S+\.(png|jpg)\s*$", text, re.M) and "size:" in text:
        return "Spine 图集"
    return "纯文本"


def _flatten_fields(node, prefix=""):
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.extend(_flatten_fields(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_flatten_fields(v, f"{prefix}[{i}]"))
    else:
        out.append({"path": prefix, "type": type(node).__name__, "value": node})
    return out


def _coerce(value, old):
    if isinstance(old, bool):
        return str(value).lower() in ("true", "1", "yes")
    if isinstance(old, int):
        return int(value)
    if isinstance(old, float):
        return float(value)
    return str(value)


def _set_by_path(root, path: str, value):
    tokens = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    cur = root
    for i, tok in enumerate(tokens):
        last = i == len(tokens) - 1
        if tok.startswith("["):
            idx = int(tok[1:-1])
            if last:
                cur[idx] = _coerce(value, cur[idx])
            else:
                cur = cur[idx]
        else:
            if last:
                cur[tok] = _coerce(value, cur[tok])
            else:
                cur = cur[tok]


@app.get("/api/asset/{aid}/editable")
def api_asset_editable(aid: int):
    if aid < 0 or aid >= len(_state["assets"]):
        raise HTTPException(404, "资源不存在，请先扫描")
    meta = _state["assets"][aid]
    t = meta["type"]
    if t == "TextAsset":
        _, reader = _get_reader(aid)
        s = reader.read().m_Script
        text = s if isinstance(s, str) else s.decode("utf-8", "replace")
        return {"editable": True, "kind": "text", "name": meta["name"], "text": text,
                "format": _text_format(text)}
    if t == "GameObject":
        _, reader = _get_reader(aid)
        go = reader.read()
        val = bool(go.m_IsActive)
        with _edit_lock:
            pend = (_edit_pending.get(aid) or {}).get("fields") or {}
        if "m_IsActive" in pend:
            val = _coerce(pend["m_IsActive"], val)
        fields = [{"path": "m_IsActive", "type": "bool", "value": val,
                   "zh": "激活状态", "exact": True,
                   "changed": _field_changed(_asset_key(meta), "m_IsActive", val)}]
        return {"editable": True, "kind": "fields", "name": meta["name"],
                "classname": "", "purpose": "游戏对象：勾选=显示/启用，取消=隐藏（如内置 GM 面板）",
                "fields": fields}
    if t in ("MonoBehaviour", "ScriptableObject"):
        _, reader = _get_reader(aid)
        try:
            tt = reader.read_typetree()
        except Exception:
            return {"editable": False, "kind": "fields", "name": meta["name"],
                    "reason": "无类型信息（IL2CPP），无法编辑字段"}
        # UnityPy 重读 typetree 不反映 save_typetree，故用待导出记录里的改动值覆盖磁盘值
        with _edit_lock:
            pend = (_edit_pending.get(aid) or {}).get("fields") or {}
        akey = _asset_key(meta)
        fields = []
        for f in _flatten_fields(tt):
            val = _coerce(pend[f["path"]], f["value"]) if f["path"] in pend else f["value"]
            fields.append({**f, **glossary.explain_field(f["path"], f["type"]),
                           "value": val, "changed": _field_changed(akey, f["path"], val)})
        cls = (meta.get("extra") or {}).get("class", "")
        return {"editable": True, "kind": "fields", "name": meta["name"],
                "classname": cls, "purpose": glossary.explain_class(cls), "fields": fields}
    return {"editable": False, "name": meta["name"], "reason": "该类型暂不支持编辑"}


class EditReq(BaseModel):
    text: str | None = None
    fields: dict | None = None


@app.post("/api/asset/{aid}/edit")
def api_asset_edit(aid: int, req: EditReq):
    if aid < 0 or aid >= len(_state["assets"]):
        raise HTTPException(404, "资源不存在，请先扫描")
    meta = _state["assets"][aid]
    t = meta["type"]
    _ensure_baseline(meta["file_path"])
    env = _get_edit_env(meta["file_path"])
    reader = _find_reader(env, meta)
    if reader is None:
        raise HTTPException(404, "资源定位失败")
    try:
        if t == "TextAsset":
            if req.text is None:
                raise HTTPException(400, "缺少文本内容")
            data = reader.read()
            data.m_Script = req.text if isinstance(data.m_Script, str) else req.text.encode("utf-8")
            data.save()
        elif t in ("MonoBehaviour", "ScriptableObject"):
            if not req.fields:
                raise HTTPException(400, "缺少字段修改")
            tt = reader.read_typetree()
            _capture_initial(_asset_key(meta), tt, list(req.fields))
            # read_typetree 不反映既往 save_typetree，重放全部累计改动，避免分次保存丢失先前字段
            with _edit_lock:
                prev = (_edit_pending.get(aid) or {}).get("fields") or {}
            for path, val in {**prev, **req.fields}.items():
                _set_by_path(tt, path, val)
            reader.save_typetree(tt)
        elif t == "GameObject":
            if not req.fields or "m_IsActive" not in req.fields:
                raise HTTPException(400, "缺少激活状态修改")
            go = reader.read()
            _capture_initial(_asset_key(meta), {"m_IsActive": bool(go.m_IsActive)}, ["m_IsActive"])
            with _edit_lock:
                prev = (_edit_pending.get(aid) or {}).get("fields") or {}
            go.m_IsActive = _coerce({**prev, **req.fields}["m_IsActive"], bool(go.m_IsActive))
            go.save()
        else:
            raise HTTPException(400, "该类型暂不支持编辑")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"修改失败: {e}")
    with _edit_lock:
        rec = _edit_pending.get(aid) or {"aid": aid, "name": meta["name"], "type": t, "file": meta["file"]}
        if t in ("MonoBehaviour", "ScriptableObject", "GameObject") and req.fields:
            flds = rec.get("fields") or {}
            flds.update(req.fields)
            rec["fields"] = flds
        _edit_pending[aid] = rec
    return {"ok": True, "pending": len(_edit_pending)}


@app.get("/api/edit/pending")
def api_edit_pending():
    with _edit_lock:
        return {"total": len(_edit_pending), "items": list(_edit_pending.values())}


@app.post("/api/edit/export")
def api_edit_export():
    with _edit_lock:
        if not _edit_envs:
            raise HTTPException(400, "无待导出的修改")
        game = "game"
        if _state["detect"] and _state["detect"].get("root"):
            game = Path(_state["detect"]["root"]).name
        out_root = DEFAULT_EXPORT_DIR / "_modified" / _safe_name(game)
        out_root.mkdir(parents=True, exist_ok=True)
        written = []
        for key, env in _edit_envs.items():
            env.save(pack="none", out_path=str(out_root))
            written.append(str(out_root / Path(key).name))
        _edit_envs.clear()
        _edit_pending.clear()
    return {"ok": True, "out_dir": str(out_root), "written": written}


# ---------- 一键生效（备份原文件后覆盖）与脚本还原（从备份恢复） ----------
BACKUP_DIR = CACHE_DIR / "orig_backup"
BACKUP_DIR.mkdir(exist_ok=True)
APPLY_FILE = CACHE_DIR / "apply_log.json"
_apply_log: list[dict] = []
_apply_lock = threading.Lock()


def _load_apply():
    global _apply_log
    try:
        _apply_log = json.loads(APPLY_FILE.read_text(encoding="utf-8")) or []
    except (OSError, ValueError):
        _apply_log = []


def _save_apply():
    try:
        APPLY_FILE.write_text(json.dumps(_apply_log, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


_load_apply()


def _md5_file(p) -> str:
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


# 初始基线：首次改动前记录原文件备份 + md5，跨会话保留；每次进入对比当前与基线，不匹配即可还原
BASELINE_FILE = CACHE_DIR / "baseline_log.json"
_baseline: list[dict] = []
_base_lock = threading.Lock()


def _load_baseline():
    global _baseline
    try:
        _baseline = json.loads(BASELINE_FILE.read_text(encoding="utf-8")) or []
    except (OSError, ValueError):
        _baseline = []


def _save_baseline():
    try:
        BASELINE_FILE.write_text(json.dumps(_baseline, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


_load_baseline()


def _ensure_baseline(path: str):
    p = Path(path)
    if not p.exists() or CACHE_DIR in p.parents:
        return
    with _base_lock:
        if any(e["path"] == path for e in _baseline):
            return
        backup = BACKUP_DIR / (hashlib.md5(_norm_path(path).encode("utf-8")).hexdigest()[:16] + ".init.bak")
        try:
            shutil.copy2(p, backup)
            _baseline.append({"path": path, "name": p.name, "backup": str(backup),
                              "init_md5": _md5_file(p), "recorded_at": time.time()})
            _save_baseline()
        except OSError:
            pass


def _diverged_entries() -> list[dict]:
    with _base_lock:
        entries = list(_baseline)
    out = []
    for e in entries:
        p = Path(e["path"])
        try:
            if p.exists() and _md5_file(p) != e["init_md5"]:
                out.append(e)
        except OSError:
            continue
    return out


# 字段级初始值基线：用于标记"哪些参数改动过"（当前值 != 初始值）
PARAM_BASELINE_FILE = CACHE_DIR / "param_baseline.json"
_pb: dict[str, dict] = {}
_pb_lock = threading.Lock()


def _load_pb():
    global _pb
    try:
        _pb = json.loads(PARAM_BASELINE_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        _pb = {}


def _save_pb():
    try:
        PARAM_BASELINE_FILE.write_text(json.dumps(_pb, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


_load_pb()


def _capture_initial(asset_key: str, tt, paths):
    vals = {f["path"]: f["value"] for f in _flatten_fields(tt)}
    with _pb_lock:
        d = _pb.setdefault(asset_key, {})
        dirty = False
        for p in paths:
            if p not in d and p in vals:
                d[p] = vals[p]
                dirty = True
        if dirty:
            _save_pb()


def _field_changed(asset_key: str, path: str, value) -> bool:
    with _pb_lock:
        d = _pb.get(asset_key)
    if not d or path not in d:
        return False
    return d[path] != value


@app.get("/api/edit/diverged")
def api_edit_diverged():
    """当前游戏文件与初始基线不匹配的项（含历史会话/已生效的改动），均可还原。"""
    out = _diverged_entries()
    return {"total": len(out), "items": out}


@app.get("/api/edit/applied")
def api_edit_applied():
    with _apply_lock:
        return {"total": len(_apply_log), "items": list(_apply_log)}


@app.post("/api/edit/apply")
def api_edit_apply():
    """一键生效：备份原文件 → 覆盖修改后的 bundle。容器（APK/OBB 解包）不支持直接覆盖。"""
    with _edit_lock:
        if not _edit_envs:
            raise HTTPException(400, "无待生效的修改")
        envs = dict(_edit_envs)
    applied, skipped = [], []
    tmp = Path(tempfile.mkdtemp(prefix="apply_", dir=str(CACHE_DIR)))
    try:
        for key, env in envs.items():
            orig = Path(key)
            if not orig.exists():
                skipped.append({"name": orig.name, "reason": "原文件不存在"})
                continue
            if CACHE_DIR in orig.parents:
                skipped.append({"name": orig.name, "reason": "APK/OBB 解包容器不支持直接覆盖，请用导出后自行重打包"})
                continue
            backup = BACKUP_DIR / (hashlib.md5(_norm_path(key).encode("utf-8")).hexdigest()[:16] + ".bak")
            try:
                shutil.copy2(orig, backup)
                orig_md5 = _md5_file(orig)
                sub = tmp / hashlib.md5(key.encode("utf-8")).hexdigest()[:8]
                sub.mkdir(parents=True, exist_ok=True)
                env.save(pack="none", out_path=str(sub))
                prod = sub / orig.name
                if not prod.exists():
                    cands = [p for p in sub.iterdir() if p.is_file()]
                    prod = cands[0] if len(cands) == 1 else None
                if prod is None:
                    skipped.append({"name": orig.name, "reason": "导出产物无法定位"})
                    continue
                shutil.copyfile(prod, orig)
                entry = {"path": key, "name": orig.name, "backup": str(backup),
                         "orig_md5": orig_md5, "applied_at": time.time()}
                with _apply_lock:
                    _apply_log[:] = [e for e in _apply_log if e["path"] != key]
                    _apply_log.append(entry)
                    _save_apply()
                applied.append(orig.name)
            except OSError as e:
                skipped.append({"name": orig.name, "reason": str(e)})
        with _edit_lock:
            for key in envs:
                _edit_envs.pop(key, None)
            _edit_pending.clear()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"applied": applied, "skipped": skipped}


class RestoreReq(BaseModel):
    paths: list[str] = []


@app.post("/api/edit/restore")
def api_edit_restore(req: RestoreReq):
    """脚本还原：把与初始基线不匹配的文件恢复为初始参数（md5 校验）；paths 为空还原全部不匹配项。"""
    div = _diverged_entries()
    targets = [e for e in div if not req.paths or e["path"] in req.paths]
    if not targets:
        raise HTTPException(400, "当前参数与初始一致，无需还原")
    restored, failed = [], []
    for e in targets:
        b, orig = Path(e["backup"]), Path(e["path"])
        try:
            if not b.exists():
                raise OSError("初始备份缺失")
            shutil.copy2(b, orig)
            if _md5_file(orig) != e["init_md5"]:
                raise OSError("还原后校验不一致")
            restored.append(e["name"])
            with _apply_lock:
                _apply_log[:] = [x for x in _apply_log if x["path"] != e["path"]]
                _save_apply()
            for it in _state.get("keyparams") or []:
                a = it.get("aid")
                if isinstance(a, int) and 0 <= a < len(_state["assets"]) \
                        and _state["assets"][a]["file_path"] == e["path"]:
                    init = _pb.get(_asset_key(_state["assets"][a]), {}).get(it["path"])
                    if init is not None:
                        it["value"] = init
            _save_keyparams(_state.get("keyparams") or [])
            with _edit_lock:
                for a, m in enumerate(_state["assets"]):
                    if os.path.normpath(m["file_path"]) == e["path"]:
                        _edit_pending.pop(a, None)
                _edit_envs.pop(e["path"], None)
        except OSError as ex:
            failed.append({"name": e["name"], "reason": str(ex)})
    return {"restored": restored, "failed": failed}


# ---------- 关键参数一键识别 ----------

KEYPARAMS_FILE = CACHE_DIR / "keyparams.json"


def _kp_counts(items: list[dict]):
    cc: dict[str, int] = {}
    pc: dict[str, int] = {}
    for it in items:
        cc[it["category"]] = cc.get(it["category"], 0) + 1
        if it.get("priority"):
            pc[it["priority"]] = pc.get(it["priority"], 0) + 1
    return cc, pc


def _kp_visible(items: list[dict]) -> list[dict]:
    """查询时按最新引擎类黑名单过滤，使黑名单细化即时生效（无需重扫）。"""
    return [it for it in items if not glossary.is_engine_class(it.get("class", ""))]


def _save_keyparams(items: list[dict]):
    try:
        KEYPARAMS_FILE.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _restore_keyparams():
    try:
        d = json.loads(KEYPARAMS_FILE.read_text(encoding="utf-8"))
        _state["keyparams"] = d.get("items") or []
    except (OSError, ValueError):
        _state["keyparams"] = []


class KeyParamJob:
    """按 bundle 分组加载脚本组件，提取分门别类的游戏关键参数（跳过引擎/UI 类）。"""

    def __init__(self, assets: list[dict]):
        self.assets = assets
        self.state = "running"
        self.done_files = 0
        self.total_files = 0
        self.items: list[dict] = []
        self.cat_counts: dict[str, int] = {}
        self.concept_counts: dict[str, int] = {}
        self.error: str | None = None
        self._saved = False
        self._lock = threading.Lock()
        self._cancelled = False
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        with self._lock:
            self._cancelled = True
            self.state = "cancelled"

    def status(self):
        with self._lock:
            return {"state": self.state, "done_files": self.done_files,
                    "total_files": self.total_files, "kept": len(self.items),
                    "cat_counts": dict(self.cat_counts),
                    "concept_counts": dict(self.concept_counts), "error": self.error}

    def _run(self):
        try:
            by_file: dict[str, list[dict]] = {}
            for a in self.assets:
                by_file.setdefault(a["file_path"], []).append(a)
            with self._lock:
                self.total_files = len(by_file)
            for fp, metas in by_file.items():
                if self._cancelled:
                    break
                try:
                    env = UnityPy.load(fp)
                except Exception:
                    with self._lock:
                        self.done_files += 1
                    continue
                readers = {}
                for r in env.objects:
                    if r.type.name in ("MonoBehaviour", "ScriptableObject"):
                        readers[(r.path_id, getattr(r.assets_file, "name", ""))] = r
                local: list[dict] = []
                for meta in metas:
                    if self._cancelled:
                        break
                    cls = (meta.get("extra") or {}).get("class", "")
                    if glossary.is_engine_class(cls):
                        continue
                    r = readers.get((meta["path_id"], meta.get("cab", "")))
                    if r is None:
                        continue
                    try:
                        tt = r.read_typetree()
                    except Exception:
                        continue
                    for f in _flatten_fields(tt):
                        if f["type"] not in ("int", "float", "bool", "str"):
                            continue
                        cat = glossary.categorize_field(f["path"], f["type"])
                        if not cat:
                            continue
                        v = f["value"]
                        if isinstance(v, float) and not math.isfinite(v):
                            v = 0.0
                        ex = glossary.explain_field(f["path"], f["type"])
                        local.append({
                            "aid": meta["id"], "name": meta["name"], "class": cls,
                            "path": f["path"], "type": f["type"], "value": v,
                            "zh": ex["zh"], "exact": ex["exact"],
                            "category": cat, "priority": glossary.priority_concept(f["path"], f["type"]),
                        })
                with self._lock:
                    self.items.extend(local)
                    for it in local:
                        self.cat_counts[it["category"]] = self.cat_counts.get(it["category"], 0) + 1
                        if it["priority"]:
                            self.concept_counts[it["priority"]] = self.concept_counts.get(it["priority"], 0) + 1
                    self.done_files += 1
            with self._lock:
                if not self._cancelled:
                    self.state = "done"
        except Exception as e:
            with self._lock:
                self.state = "error"
                self.error = str(e)


_restore_keyparams()


@app.post("/api/keyparams/scan")
def api_keyparams_scan():
    assets = [a for a in _state["assets"] if a["type"] in ("MonoBehaviour", "ScriptableObject")]
    if not assets:
        raise HTTPException(400, "请先扫描资源（未发现脚本组件）")
    old = _state.get("keyparam_job")
    if old is not None:
        old.cancel()
    _state["keyparam_job"] = KeyParamJob(assets)
    _state["keyparams"] = []
    return {"started": True, "total_scripts": len(assets)}


@app.get("/api/keyparams/status")
def api_keyparams_status():
    job: KeyParamJob | None = _state.get("keyparam_job")
    if job is None:
        items = _kp_visible(_state.get("keyparams") or [])
        if items:
            cc, pc = _kp_counts(items)
            return {"state": "done", "restored": True, "kept": len(items), "cat_counts": cc,
                    "concept_counts": pc, "done_files": 0, "total_files": 0, "error": None}
        return {"state": "idle"}
    st = job.status()
    vis = _kp_visible(job.items)
    cc, pc = _kp_counts(vis)
    st["kept"] = len(vis); st["cat_counts"] = cc; st["concept_counts"] = pc
    if st["state"] == "done" and not job._saved:
        job._saved = True
        _state["keyparams"] = vis
        _save_keyparams(vis)
    return st


@app.get("/api/keyparams")
def api_keyparams(category: str | None = None, priority: str | None = None,
                  search: str = "", offset: int = 0, limit: int = 200):
    items = _kp_visible(_state.get("keyparams") or [])
    for it in items:
        if "purpose" not in it:
            it["purpose"] = glossary.explain_class(it.get("class", ""))
        aid = it.get("aid")
        if isinstance(aid, int) and 0 <= aid < len(_state["assets"]):
            it["changed"] = _field_changed(_asset_key(_state["assets"][aid]), it["path"], it["value"])
    cc, pc = _kp_counts(items)
    sel = items
    if priority == "any":
        sel = [it for it in sel if it.get("priority")]
    elif priority:
        sel = [it for it in sel if it.get("priority") == priority]
    if category:
        sel = [it for it in sel if it["category"] == category]
    if search:
        s = search.lower()
        sel = [it for it in sel if s in it["path"].lower() or s in (it["zh"] or "").lower()
               or s in it["name"].lower() or s in (it["class"] or "").lower()
               or s in (it.get("purpose") or "").lower()
               or s in (it.get("priority") or "").lower()]
    return {"total": len(sel), "items": sel[offset:offset + limit], "cat_counts": cc,
            "concept_counts": pc, "category_zh": glossary.CATEGORY_ZH,
            "concepts": [n for n, _ in glossary.PRIORITY_CONCEPTS]}


class KeyParamChange(BaseModel):
    aid: int
    path: str
    value: str | int | float | bool


class KeyParamSaveReq(BaseModel):
    changes: list[KeyParamChange]


@app.post("/api/keyparams/save")
def api_keyparams_save(req: KeyParamSaveReq):
    if not req.changes:
        raise HTTPException(400, "无修改")
    by_aid: dict[int, dict] = {}
    for ch in req.changes:
        by_aid.setdefault(ch.aid, {})[ch.path] = ch.value
    applied = 0
    for aid, fields in by_aid.items():
        if aid < 0 or aid >= len(_state["assets"]):
            continue
        meta = _state["assets"][aid]
        if meta["type"] not in ("MonoBehaviour", "ScriptableObject"):
            continue
        _ensure_baseline(meta["file_path"])
        env = _get_edit_env(meta["file_path"])
        reader = _find_reader(env, meta)
        if reader is None:
            continue
        try:
            tt = reader.read_typetree()
            _capture_initial(_asset_key(meta), tt, list(fields))
            # read_typetree 不反映既往 save_typetree，重放全部累计改动，避免分次保存丢失先前字段
            with _edit_lock:
                prev = (_edit_pending.get(aid) or {}).get("fields") or {}
            for path, val in {**prev, **fields}.items():
                _set_by_path(tt, path, val)
            reader.save_typetree(tt)
        except Exception as e:
            raise HTTPException(500, f"修改失败（{meta['name']}）: {e}")
        with _edit_lock:
            rec = _edit_pending.get(aid) or {"aid": aid, "name": meta["name"], "type": meta["type"], "file": meta["file"]}
            flds = rec.get("fields") or {}
            flds.update(fields)
            rec["fields"] = flds
            _edit_pending[aid] = rec
        applied += 1
    changed = {(ch.aid, ch.path): ch.value for ch in req.changes}
    items = _state.get("keyparams") or []
    for it in items:
        k = (it["aid"], it["path"])
        if k in changed:
            it["value"] = _coerce(changed[k], it["value"])
    if applied:
        _save_keyparams(items)
    return {"ok": True, "applied": applied, "pending": len(_edit_pending)}


# ---------- 在线翻译（百度/有道，按需+缓存） ----------

class TranslateConfigReq(BaseModel):
    provider: str = "baidu"
    appid: str = ""
    key: str = ""


@app.get("/api/translate/config")
def api_translate_config():
    return translate.get_config()


@app.post("/api/translate/config")
def api_translate_config_save(req: TranslateConfigReq):
    translate.save_config(req.provider, req.appid, req.key)
    return translate.get_config()


class TranslateReq(BaseModel):
    texts: list[str]
    src: str = "auto"


@app.post("/api/translate")
def api_translate(req: TranslateReq):
    if not req.texts:
        return {"results": {}}
    try:
        res = translate.translate_batch(req.texts[:500], req.src)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"翻译请求失败: {e}")
    return {"results": res, "provider": translate.get_config()["provider"]}


# ---------- 导出 ----------

class ExportJob:
    def __init__(self, ids: list[int], out_dir: Path, mesh_formats: list[str]):
        self.ids = ids
        self.out_dir = out_dir
        self.mesh_formats = mesh_formats
        self.state = "running"
        self.done = 0
        self.current = ""
        self.errors: list[str] = []
        self.written: list[str] = []
        self._lock = threading.Lock()
        self._cancelled = False
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        with self._lock:
            self._cancelled = True
            self.state = "cancelled"

    def _write(self, sub: str, name: str, ext: str, content: bytes | str):
        d = self.out_dir / sub
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{_safe_name(name)}_{self.done}{ext}"
        if isinstance(content, str):
            p.write_text(content, encoding="utf-8")
        else:
            p.write_bytes(content)
        with self._lock:
            self.written.append(str(p))

    def _run(self):
        try:
            for aid in self.ids:
                if self._cancelled:
                    break
                if aid >= len(_state["assets"]):
                    continue
                meta = _state["assets"][aid]
                tname, name = meta["type"], meta["name"]
                with self._lock:
                    self.current = f"{tname}: {name}"
                try:
                    _, reader = _get_reader(aid)
                    data = reader.read()
                    if tname in ("Texture2D", "Sprite", "Cubemap"):
                        self._write(tname, name, ".png", _texture_png(data))
                    elif tname == "Mesh":
                        if "glb" in self.mesh_formats:
                            self._write(tname, name, ".glb", mesh_to_glb(data))
                        if "obj" in self.mesh_formats:
                            obj = data.export()
                            if obj:
                                self._write(tname, name, ".obj", obj)
                    elif tname == "Character":
                        _, reader = _get_reader(aid, deps=True)
                        self._write(tname, name, ".glb",
                                    character_to_glb(_load_env_deps(meta["file_path"]), reader))
                    elif tname == "AnimationClip":
                        glb = animation_to_glb(data)
                        if glb:
                            self._write(tname, name, ".glb", glb)
                        else:
                            self._write(tname, name, ".json",
                                        json.dumps(humanoid_export(data), ensure_ascii=False,
                                                   indent=2, default=str))
                    elif tname == "AudioClip":
                        for fname, audio in (data.samples or {}).items():
                            self._write(tname, name, Path(fname).suffix or ".wav", audio)
                    elif tname == "TextAsset":
                        s = data.m_Script
                        self._write(tname, name, ".txt", s if isinstance(s, (bytes, str)) else str(s))
                    elif tname == "Font":
                        self._write(tname, name, ".bin", bytes(data.m_FontData))
                    else:
                        tree = reader.read_typetree()
                        self._write(tname, name, ".json",
                                    json.dumps(tree, ensure_ascii=False, indent=2, default=str))
                except Exception as e:
                    with self._lock:
                        self.errors.append(f"{name}: {e}")
                with self._lock:
                    self.done += 1
            if not self._cancelled:
                self.state = "done"
        except Exception as e:
            if not self._cancelled:
                self.state = "error"
                self.errors.append(str(e))

    def status(self):
        with self._lock:
            return {"state": self.state, "done": self.done, "total": len(self.ids),
                    "current": self.current, "errors": self.errors[:50],
                    "out_dir": str(self.out_dir), "written_count": len(self.written)}


@app.post("/api/export")
def api_export(req: ExportReq):
    if not _state["assets"]:
        raise HTTPException(400, "请先扫描资源")
    if _state["export"] and _state["export"].state == "running":
        raise HTTPException(409, "已有导出任务进行中")
    game = "game"
    if _state["detect"] and _state["detect"].get("root"):
        game = Path(_state["detect"]["root"]).name
    out = Path(req.out_dir) if req.out_dir else DEFAULT_EXPORT_DIR / _safe_name(game)
    _state["export"] = ExportJob(req.ids, out, req.mesh_formats)
    return {"started": True, "out_dir": str(out)}


@app.get("/api/export/status")
def api_export_status():
    job: ExportJob | None = _state["export"]
    return job.status() if job else {"state": "idle"}


@app.post("/api/open_folder")
def api_open_folder(req: PathReq):
    p = Path(req.path)
    if not p.exists():
        raise HTTPException(404, "目录不存在")
    os.startfile(str(p))  # noqa: S606 — 本地工具，打开资源管理器
    return {"ok": True}


# ---------- 修改指南（离线单机 modding 分析 + 结论笔记） ----------
MODGUIDE_DIR = CACHE_DIR / "modguide"
MODGUIDE_DIR.mkdir(exist_ok=True)


def _modguide_key() -> str:
    path = (_state["detect"] or {}).get("root") or _state["scan_path"] or ""
    return hashlib.md5(_norm_path(path).encode("utf-8")).hexdigest()[:16] if path else ""


def _modguide_file(key: str) -> Path:
    return MODGUIDE_DIR / (key + ".json")


def _load_modguide(key: str) -> dict:
    if not key:
        return {}
    try:
        return json.loads(_modguide_file(key).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _save_modguide(key: str, data: dict):
    if not key:
        return
    try:
        _modguide_file(key).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


@app.get("/api/modguide")
def api_modguide_get():
    key = _modguide_key()
    data = _load_modguide(key)
    return {"has_project": bool(key), "report": data.get("report"), "notes": data.get("notes", "")}


@app.post("/api/modguide/analyze")
def api_modguide_analyze():
    det = _state["detect"]
    if not det or not det.get("engine"):
        raise HTTPException(400, "请先识别游戏引擎")
    if det.get("engine") != "unity":
        raise HTTPException(400, "修改指南分析目前仅支持 Unity 游戏")
    key = _modguide_key()
    report = modguide.analyze(det)
    data = _load_modguide(key)
    data["report"] = report
    _save_modguide(key, data)
    return {"report": report, "notes": data.get("notes", "")}


class ModguideNotesReq(BaseModel):
    notes: str = ""


@app.post("/api/modguide/notes")
def api_modguide_notes(req: ModguideNotesReq):
    key = _modguide_key()
    if not key:
        raise HTTPException(400, "请先识别 / 打开游戏")
    data = _load_modguide(key)
    data["notes"] = req.notes
    _save_modguide(key, data)
    return {"ok": True}


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")