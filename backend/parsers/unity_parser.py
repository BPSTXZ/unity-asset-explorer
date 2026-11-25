"""UnityPy 资源扫描：建立可浏览的资源清单（只存元数据，预览/导出按需加载）。"""
from __future__ import annotations

import threading
from pathlib import Path

import UnityPy

from ..exporters.character import find_characters

# 可提取/预览的资源类型
EXTRACTABLE = {
    "Mesh", "Texture2D", "Sprite", "AnimationClip", "AudioClip",
    "Material", "TextAsset", "Font", "Cubemap", "VideoClip", "Shader",
}


def _extra_info(data, tname: str) -> dict:
    try:
        if tname in ("Texture2D", "Cubemap"):
            return {"width": data.m_Width, "height": data.m_Height,
                    "format": str(getattr(data, "m_TextureFormat", ""))}
        if tname == "Sprite":
            return {"rect": [data.m_Rect.width, data.m_Rect.height]}
        if tname == "Mesh":
            vd = getattr(data, "m_VertexData", None)
            return {"vertices": getattr(vd, "m_VertexCount", 0),
                    "submeshes": len(getattr(data, "m_SubMeshes", []) or [])}
        if tname == "AnimationClip":
            return {"sample_rate": getattr(data, "m_SampleRate", 0)}
        if tname == "AudioClip":
            return {"length": getattr(data, "m_Length", 0),
                    "channels": getattr(data, "m_Channels", 0),
                    "frequency": getattr(data, "m_Frequency", 0)}
        if tname == "Material":
            shader = getattr(data, "m_Shader", None)
            name = ""
            if shader is not None:
                try:
                    name = shader.read().m_Name
                except Exception:
                    pass
            return {"shader": name}
    except Exception:
        pass
    return {}


class ScanJob:
    def __init__(self, files: list[str]):
        self.files = files
        self.state = "running"  # running | done | error
        self.done_files = 0
        self.assets: list[dict] = []
        self.type_counts: dict[str, int] = {}
        self.error: str | None = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            for f in self.files:
                try:
                    env = UnityPy.load(f)
                except Exception:
                    with self._lock:
                        self.done_files += 1
                    continue
                fname = Path(f).name
                has_smr = False
                for reader in env.objects:
                    tname = reader.type.name
                    if tname == "SkinnedMeshRenderer":
                        has_smr = True
                    if tname not in EXTRACTABLE:
                        continue
                    try:
                        data = reader.read()
                    except Exception:
                        continue
                    name = getattr(data, "m_Name", "") or ""
                    container = ""
                    try:
                        container = reader.container or ""
                    except Exception:
                        pass
                    with self._lock:
                        self.assets.append({
                            "id": len(self.assets), "type": tname,
                            "name": name or f"<未命名 {reader.path_id}>",
                            "path_id": reader.path_id, "file": fname, "file_path": f,
                            "cab": getattr(reader.assets_file, "name", ""),
                            "container": container, "size": reader.byte_size,
                            "extra": _extra_info(data, tname),
                        })
                        self.type_counts[tname] = self.type_counts.get(tname, 0) + 1
                if has_smr:
                    try:
                        for ch in find_characters(env):
                            with self._lock:
                                self.assets.append({
                                    "id": len(self.assets), "type": "Character",
                                    "name": ch["name"], "path_id": ch["root_pid"],
                                    "file": fname, "file_path": f, "cab": ch["cab"],
                                    "container": "", "size": ch.get("size", 0),
                                    "extra": {"parts": ch["parts"], "bones": ch["bones"],
                                              "vertices": ch["vertices"]},
                                })
                                self.type_counts["Character"] = self.type_counts.get("Character", 0) + 1
                    except Exception:
                        pass
                # 释放本文件解析对象，仅保留元数据，控制内存
                del env
                with self._lock:
                    self.done_files += 1
            self.state = "done"
        except Exception as e:
            self.state = "error"
            self.error = str(e)

    def status(self) -> dict:
        with self._lock:
            return {"state": self.state, "done_files": self.done_files,
                    "total_files": len(self.files), "asset_count": len(self.assets),
                    "type_counts": dict(self.type_counts), "error": self.error}

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self.assets)