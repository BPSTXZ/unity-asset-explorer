"""Asset Explorer — 游戏资源自动识别/提取/预览/导出 本地服务。"""
from __future__ import annotations

import io
import json
import os
import re
import string
import threading
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

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"
CACHE_DIR.mkdir(exist_ok=True)
DEFAULT_EXPORT_DIR = ROOT / "exports"

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


# ---------- 目录浏览（文件夹选择） ----------

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
    except PermissionError:
        pass
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None,
            "entries": entries}


# ---------- 识别 / 扫描 ----------

@app.post("/api/detect")
def api_detect(req: PathReq):
    result = detect(req.path.strip().strip('"'))
    _state["detect"] = result
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
    if st["asset_count"] and (st["state"] == "done" or len(_state["assets"]) < st["asset_count"]):
        _state["assets"] = job.snapshot()
    if st["state"] == "done" and not getattr(job, "_session_saved", False):
        job._session_saved = True
        _save_session()
    return st


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


@app.get("/api/assets")
def api_assets(type: str | None = None, search: str = "", offset: int = 0, limit: int = 200):
    items = _state["assets"]
    if type:
        items = [a for a in items if a["type"] == type]
    if search:
        s = search.lower()
        items = [a for a in items if s in a["name"].lower() or s in a["container"].lower()]
    return {"total": len(items), "items": items[offset:offset + limit]}


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


class ClipMatchJob:
    """后台扫描与角色骨骼兼容的动画 clips（按骨骼路径 CRC 匹配打分）。"""

    def __init__(self, char_crc: set, boost_dir: str):
        self.char_crc = char_crc
        self.boost_dir = boost_dir
        self.state = "running"
        self.done = 0
        self.total = 0
        self.results: list[dict] = []
        self.error: str | None = None
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            clips = [a for a in _state["assets"] if a["type"] == "AnimationClip"]
            byfile: dict[str, list] = {}
            for a in clips:
                byfile.setdefault(a["file_path"], []).append(a)
            self.total = len(byfile)
            matched = []
            for fp, items in byfile.items():
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
                            "ratio": round(ratio, 3), "bones": hit,
                            "duration": round(dur, 3), "boost": boost,
                        })
                del env
                with self._lock:
                    self.done += 1
            matched.sort(key=lambda x: (not x["boost"], -x["ratio"], x["name"]))
            with self._lock:
                self.results = matched
            self.state = "done"
        except Exception as e:
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
    job = _clip_jobs.get(aid)
    if job is None or refresh:
        crc = character_bone_crc(reader)
        job = ClipMatchJob(crc, _creature_dir(meta["file_path"]))
        _clip_jobs[aid] = job
    return job.status()


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
        threading.Thread(target=self._run, daemon=True).start()

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
            self.state = "done"
        except Exception as e:
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


app.mount("/", StaticFiles(directory=str(ROOT / "frontend"), html=True), name="frontend")