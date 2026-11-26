"""在线翻译兜底：百度 / 有道翻译 API（英/日 → 中），按需调用 + 本地缓存。

离线词典优先；本模块用于日语、词典未收录词或更自然的短语翻译。
配置（provider/appid/key）与译文缓存均存于 .cache，密钥不外泄、不入库。
百度支持把多行文本合并为一次请求（免费档 QPS=1，故批量+限速）。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .paths import CACHE_DIR

CONFIG_FILE = CACHE_DIR / "translate_config.json"
CACHE_FILE = CACHE_DIR / "online_translations.json"

_lock = threading.Lock()
_config: dict = {"provider": "baidu", "appid": "", "key": ""}
_cache: dict[str, str] = {}
_last_call = 0.0
_MIN_INTERVAL = 1.1  # 百度免费档 QPS=1


def _load():
    global _config, _cache
    try:
        _config.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    try:
        _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _cache = {}


_load()


def get_config() -> dict:
    with _lock:
        return {"provider": _config.get("provider", "baidu"),
                "appid": _config.get("appid", ""),
                "has_key": bool(_config.get("key")),
                "configured": bool(_config.get("appid") and _config.get("key")),
                "cached": len(_cache)}


def save_config(provider: str, appid: str, key: str):
    with _lock:
        if key == "__KEEP__":
            key = _config.get("key", "")
        _config.update(provider=provider or "baidu", appid=appid.strip(), key=key.strip())
        try:
            CONFIG_FILE.write_text(json.dumps(_config, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def _save_cache():
    try:
        CACHE_FILE.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def normalize(text: str) -> str:
    """把标识符拆成空格分隔的词，便于机器翻译理解（resourceImage → resource image）。"""
    t = re.sub(r"^m_", "", text.strip())
    t = t.replace("_", " ")
    t = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", t)
    t = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", t)
    return re.sub(r"\s+", " ", t).strip()


def _throttle():
    global _last_call
    wait = _MIN_INTERVAL - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def _http_get(url: str, params: dict) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": "AssetExplorer/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _baidu_batch(texts: list[str], src: str, dst: str) -> list[str]:
    """百度：多行合并一次请求，返回每行译文。"""
    appid, key = _config["appid"], _config["key"]
    q = "\n".join(texts)
    salt = str(int(time.time() * 1000) % 100000000)
    sign = hashlib.md5((appid + q + salt + key).encode("utf-8")).hexdigest()
    _throttle()
    data = _http_get("https://fanyi-api.baidu.com/api/trans/vip/translate",
                     {"q": q, "from": src, "to": dst, "appid": appid, "salt": salt, "sign": sign})
    if "error_code" in data:
        raise RuntimeError(f"百度翻译错误 {data['error_code']}: {data.get('error_msg', '')}")
    res = data.get("trans_result") or []
    return [r.get("dst", "") for r in res]


def _youdao_one(text: str, src: str, dst: str) -> str:
    appid, key = _config["appid"], _config["key"]
    salt = str(int(time.time() * 1000) % 100000000)
    curtime = str(int(time.time()))
    n = len(text)
    trunc = text if n <= 20 else text[:10] + str(n) + text[-10:]
    sign = hashlib.sha256((appid + trunc + salt + curtime + key).encode("utf-8")).hexdigest()
    _throttle()
    data = _http_get("https://openapi.youdao.com/api",
                     {"q": text, "from": src, "to": dst, "appKey": appid, "salt": salt,
                      "sign": sign, "signType": "v3", "curtime": curtime})
    if data.get("errorCode") != "0":
        raise RuntimeError(f"有道翻译错误 {data.get('errorCode')}")
    return "; ".join(data.get("translation") or [])


def _lang_map(provider: str, src: str, dst: str):
    if provider == "youdao":
        m = {"auto": "auto", "en": "en", "jp": "ja", "ja": "ja", "zh": "zh-CHS"}
        return m.get(src, "auto"), m.get(dst, "zh-CHS")
    m = {"auto": "auto", "en": "en", "jp": "jp", "ja": "jp", "zh": "zh"}
    return m.get(src, "auto"), m.get(dst, "zh")


def translate_batch(texts: list[str], src: str = "auto") -> dict:
    """翻译一批文本，返回 {原文: 译文}。命中缓存优先；未配置则抛错。"""
    with _lock:
        if not (_config.get("appid") and _config.get("key")):
            raise RuntimeError("未配置翻译 API（请先在设置中填写 appid 与密钥）")
        provider = _config.get("provider", "baidu")
    out: dict[str, str] = {}
    todo: list[str] = []
    for t in texts:
        if not t:
            continue
        if t in _cache:
            out[t] = _cache[t]
        elif t not in todo:
            todo.append(t)
    if not todo:
        return out
    s, d = _lang_map(provider, src, "zh")
    norm = {t: normalize(t) or t for t in todo}
    if provider == "baidu":
        # 按字节长度分块（百度单次 q 上限约 6000 字节）
        chunk: list[str] = []
        size = 0
        chunks: list[list[str]] = []
        for t in todo:
            ln = len(norm[t].encode("utf-8")) + 1
            if size + ln > 5000 and chunk:
                chunks.append(chunk); chunk = []; size = 0
            chunk.append(t); size += ln
        if chunk:
            chunks.append(chunk)
        for ch in chunks:
            dsts = _baidu_batch([norm[t] for t in ch], s, d)
            for i, t in enumerate(ch):
                zh = dsts[i] if i < len(dsts) else ""
                if zh:
                    out[t] = zh
                    _cache[t] = zh
    else:
        for t in todo:
            try:
                zh = _youdao_one(norm[t], s, d)
            except RuntimeError:
                raise
            except Exception as e:
                raise RuntimeError(f"有道翻译请求失败: {e}")
            if zh:
                out[t] = zh
                _cache[t] = zh
    with _lock:
        _save_cache()
    return out