"""离线单机 modding 分析：识别货币/存档机制，给出可行改法。

仅面向离线单机游戏的个人 modding；不适用于联机/竞技/反作弊游戏。
分析只读取游戏二进制与存档目录的公开字符串/元数据，不做运行时注入、不绕过 DRM/反作弊。
"""
from __future__ import annotations

import os
import re
import struct
import time
from pathlib import Path

# 货币/经济关键词（按 token 匹配，降低子串噪声）
CURRENCY_GROUPS: dict[str, list[str]] = {
    "货币 / 数值": ["money", "gold", "coin", "cash", "currency", "gem", "diamond",
                 "credit", "wallet", "funds", "balance", "silver", "treasure", "rupee"],
    "经济行为": ["addmoney", "increasemoney", "spend", "purchase", "afford", "earn",
              "reward", "deduct", "addcoin", "gaincoin", "pay"],
    "商店 / 价格": ["shop", "price", "cost", "buy", "sell", "eshopping", "shopping"],
}
# 存档框架（ distinctive 长名，按子串匹配）
SAVE_FRAMEWORKS: dict[str, list[str]] = {
    "Fungus（视觉小说框架，JSON 存档）": ["fungus.savemanager", "savedatajson", "savedataitem", "fungus.save"],
    "EasySave / ES3": ["easysave", "es3file", "es3cloud", "es3spreadsheet"],
    "PlayerPrefs": ["playerprefs"],
    "JSON 序列化": ["jsonutility", "newtonsoft", "jsonserialize", "savedatajson"],
    "BinaryFormatter": ["binaryformatter"],
    "SQLite / LiteDB": ["sqlite", "litedb"],
    "Lua 脚本（MoonSharp / XLua）": ["moonsharp", "xlua", "nlua", "luaenv"],
}
# 内置作弊 / 调试提示
CHEAT_HINTS = ["cheat", "godmode", "infinite", "addmoney", "unlockall", "freemoney",
               "givemoney", "addgold", "devmenu", "devconsole", "debugmenu"]

_TOKEN_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|\d+")
_ASCII_RE = re.compile(rb"[ -~]{4,}")
_UTF16_RE = re.compile(rb"(?:[ -~]\x00){4,}")
_MAX_BYTES = 96 * 1024 * 1024


def _mine_strings(data: bytes) -> set[str]:
    out = {m.decode("latin1") for m in _ASCII_RE.findall(data)}
    out |= {m.decode("utf-16le", "ignore") for m in _UTF16_RE.findall(data)}
    return out


def _tokens(s: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(s)]


def _singular(tok: str) -> str:
    return tok[:-1] if tok.endswith("s") and len(tok) > 3 else tok


def _match_currency(strings: set[str]) -> dict[str, list[str]]:
    terms = {t for ts in CURRENCY_GROUPS.values() for t in ts}
    grouped: dict[str, set[str]] = {g: set() for g in CURRENCY_GROUPS}
    for s in strings:
        if len(s) > 48:
            continue
        toks = {_singular(t) for t in _tokens(s)}
        hit = toks & terms
        if not hit:
            continue
        for g, ts in CURRENCY_GROUPS.items():
            if hit & set(ts):
                grouped[g].add(s)
    return {g: sorted(v)[:40] for g, v in grouped.items() if v}


def _match_save(strings: set[str]) -> list[str]:
    low = {s.lower() for s in strings if len(s) <= 64}
    found = []
    for name, keys in SAVE_FRAMEWORKS.items():
        if any(k in s for s in low for k in keys):
            found.append(name)
    return found


def _match_cheat(strings: set[str]) -> list[str]:
    low = {s.lower() for s in strings if len(s) <= 48}
    return sorted({s for s in low if any(k in s for k in CHEAT_HINTS)})[:30]


def _find_mine_targets(data_dir: Path, backend: str) -> list[Path]:
    targets: list[Path] = []
    managed = data_dir / "Managed"
    if managed.is_dir():
        for pat in ("Assembly-CSharp.dll", "Assembly-CSharp-firstpass.dll"):
            f = managed / pat
            if f.exists():
                targets.append(f)
    meta = data_dir / "il2cpp_data" / "Metadata" / "global-metadata.dat"
    if meta.exists():
        targets.append(meta)
    if not targets and backend == "IL2CPP":
        for f in data_dir.rglob("global-metadata.dat"):
            targets.append(f)
            break
    return targets


def _read_app_info(data_dir: Path) -> tuple[str, str]:
    f = data_dir / "app.info"
    if f.exists():
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            lines = [x.strip() for x in lines if x.strip()]
            if len(lines) >= 2:
                return lines[0], lines[1]
            if lines:
                return lines[0], lines[0]
        except OSError:
            pass
    return "", ""


def _save_dir(company: str, product: str, android: bool) -> tuple[str, bool, list[str]]:
    if android:
        return "安卓设备内（/data/data/<包名>/files 或 persistentDataPath），PC 侧不可直接访问", False, []
    if not company or not product:
        return "", False, []
    base = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "AppData" / "LocalLow" / company / product
    exists = base.is_dir()
    files = []
    if exists:
        try:
            files = sorted(p.name for p in base.iterdir() if p.is_file())[:30]
        except OSError:
            pass
    return str(base), exists, files


_CHEAT_RES = re.compile(
    r"(?:Add|Give)\w*(?:Money|Coin|Gold|Cash|Gem|Diamond|Energy|Corruption|Stamina|Salary)"
    r"|(?:Add|Give)\w*Time\d+|Cheat|GodMode|Infinite|UnlockAll|FreeMoney", re.I)


def _il_call_names(pe, raw: bytes, rva: int) -> list[str]:
    if not rva:
        return []
    try:
        off = pe.get_offset_from_rva(rva)
    except Exception:
        return []
    b0 = raw[off]
    if (b0 & 3) == 2:
        size, s = b0 >> 2, off + 1
    elif (b0 & 3) == 3:
        size, s = struct.unpack("<I", raw[off + 4:off + 8])[0], off + 12
    else:
        return []
    body = raw[s:s + size]
    md = pe.net.mdtables
    names = []
    i = 0
    while i < len(body):
        op = body[i]
        if op in (0x28, 0x6F) and i + 4 < len(body):
            tok = struct.unpack("<I", body[i + 1:i + 5])[0]
            tbl, rid = tok >> 24, tok & 0xFFFFFF
            try:
                if tbl == 0x0A:
                    names.append(str(md.MemberRef.rows[rid - 1].Name))
                elif tbl == 0x06:
                    names.append(str(md.MethodDef.rows[rid - 1].Name))
            except Exception:
                pass
            i += 5
            continue
        i += 1
    return names


def _find_hosts(data_dir: Path, cls: str) -> list[dict]:
    """扫描场景，找挂载指定脚本的 GameObject（名称/场景/激活态）。"""
    import UnityPy
    scripts = set()
    for fn in ("globalgamemanagers.assets", "resources.assets"):
        f = data_dir / fn
        if not f.exists():
            continue
        try:
            env = UnityPy.load(str(f))
        except Exception:
            continue
        for o in env.objects:
            if o.type.name == "MonoScript":
                try:
                    if o.read().m_ClassName == cls:
                        scripts.add((fn, o.path_id))
                except Exception:
                    pass
    if not scripts:
        return []
    hosts = []
    scenes = sorted(data_dir.glob("level*")) + sorted(data_dir.glob("sharedassets*.assets"))
    for sc in scenes:
        try:
            env = UnityPy.load(str(sc))
        except Exception:
            continue
        if not env.objects:
            continue
        ext = getattr(env.objects[0].assets_file, "externals", []) or []
        fidmap = {0: sc.name}
        for i, e in enumerate(ext):
            fidmap[i + 1] = Path(getattr(e, "path", "")).name
        objs = {o.path_id: o for o in env.objects}
        for o in env.objects:
            if o.type.name != "MonoBehaviour":
                continue
            try:
                raw = o.get_raw_data()
            except Exception:
                continue
            if len(raw) < 28:
                continue
            sfid = struct.unpack("<i", raw[16:20])[0]
            spid = struct.unpack("<q", raw[20:28])[0]
            if (fidmap.get(sfid, ""), spid) not in scripts:
                continue
            gpid = struct.unpack("<q", raw[4:12])[0]
            g = objs.get(gpid)
            if g and g.type.name == "GameObject":
                try:
                    gd = g.read()
                    hosts.append({"gameobject": gd.m_Name, "scene": sc.name, "active": bool(gd.m_IsActive)})
                except Exception:
                    pass
    return hosts


def _cheat_triggers(data_dir: Path, backend: str) -> list[dict]:
    """识别内置作弊/调试指令的触发方法（聚焦 MonoBehaviour 按钮面板类 GM 菜单）。"""
    if backend != "Mono":
        return []
    try:
        import dnfile
    except ImportError:
        return []
    dll = data_dir / "Managed" / "Assembly-CSharp.dll"
    if not dll.exists():
        return []
    try:
        pe = dnfile.dnPE(str(dll))
        raw = dll.read_bytes()
        md = pe.net.mdtables
        tds, mds = md.TypeDef.rows, md.MethodDef.rows
    except Exception:
        return []
    owner = {}
    for ti, t in enumerate(tds):
        for m in t.MethodList:
            owner[m.row_index] = ti
    groups: dict[int, list[str]] = {}
    for mi, m in enumerate(mds):
        nm = str(m.Name)
        if _CHEAT_RES.search(nm):
            ti = owner.get(mi + 1)
            if ti is not None:
                groups.setdefault(ti, []).append(nm)
    results = []
    for ti, methods in groups.items():
        t = tds[ti]
        cls = str(t.TypeName)
        try:
            base = str(t.Extends.row.TypeName)
        except Exception:
            base = ""
        if base != "MonoBehaviour":
            continue
        wired = False
        for m in t.MethodList:
            if str(m.row.Name) in ("Awake", "Start", "OnEnable"):
                names = _il_call_names(pe, raw, m.row.Rva)
                if "AddListener" in names and "get_onClick" in names:
                    wired = True
                    break
        if not wired:
            continue
        hosts = _find_hosts(data_dir, cls)
        uniq = sorted(set(methods))
        desc = f"{cls} 是 UI 按钮面板（Awake 把按钮 onClick 绑定到这些方法），属按钮触发类 GM 菜单。"
        if hosts:
            h = hosts[0]
            if h["active"]:
                desc += f"挂在 {h['scene']} 的 GameObject「{h['gameobject']}」（当前显示）：进游戏到该场景即可点按钮触发。"
            else:
                desc += (f"挂在 {h['scene']} 的 GameObject「{h['gameobject']}」（当前隐藏）："
                         "用「GameObject 激活态编辑」把它设为激活，进游戏即可点按钮触发。")
        else:
            desc += "未在场景中找到挂载物体（可能在预制体或需运行时实例化）。"
        results.append({"class": cls, "mechanism": "UI 按钮面板", "methods": uniq[:12],
                        "hosts": hosts, "description": desc})
    return results


def analyze(det: dict) -> dict:
    """对已识别的游戏做通用 modding 分析，返回结构化报告（不含人工结论笔记）。"""
    root = Path(det.get("root") or "")
    data_dir = Path(det.get("data_dir") or str(root))
    backend = det.get("scripting_backend") or "unknown"
    android = bool(det.get("container_files")) or "Android" in (det.get("engine_label") or "")
    platform = "Android（APK / OBB 容器）" if android else (
        "Windows PC" if (root / "UnityPlayer.dll").exists() or any(root.glob("*.exe")) else "未知平台")

    steps = [
        f"识别平台 / 引擎 / 后端：{platform} · {det.get('engine_label')} · {backend}"
        + (f" · Unity {det.get('unity_version')}" if det.get("unity_version") else ""),
    ]

    targets = _find_mine_targets(data_dir, backend)
    strings: set[str] = set()
    mined = []
    for f in targets:
        try:
            if f.stat().st_size > _MAX_BYTES:
                continue
            strings |= _mine_strings(f.read_bytes())
            mined.append(f.name)
        except OSError:
            continue
    if mined:
        steps.append(f"挖掘代码字符串（{', '.join(mined)}）：货币 / 经济 / 商店 / 存档框架 / 作弊调试关键词")
    else:
        steps.append("未找到托管 DLL / global-metadata，关键词分析受限（IL2CPP 或容器解包不含代码）")

    currency = _match_currency(strings)
    save_fw = _match_save(strings)
    cheat = _match_cheat(strings)
    cheat_triggers = _cheat_triggers(data_dir, backend)
    if cheat_triggers:
        steps.append("逆向作弊指令触发方法：定位指令所属组件、判断按钮/按键机制、扫描场景挂载物体与显隐")
    company, product = _read_app_info(data_dir)
    save_path, save_exists, save_files = _save_dir(company, product, android)
    steps.append(f"定位存档：{save_path or '未能确定'}" + ("（已存在）" if save_exists else "（不存在 / 未运行过）" if save_path else ""))

    # 运行时货币信号（属性 / 缓存）
    runtime_money = sorted({s for s in strings if len(s) <= 40 and re.search(
        r"get_Money|set_Money|cachedMoney|get_coin|set_coin|Money\b", s)})[:12]

    # 可行性评估（启发式，证据导向，结论可在笔记中细化）
    limits = ["仅离线单机 modding；联机 / 竞技 / 反作弊游戏不适用。"]
    if backend == "IL2CPP":
        limits.append("IL2CPP 无托管 DLL，关键词来自 global-metadata（若可用），精度较低。")
    if android:
        limits.append("安卓容器存档在设备内，PC 侧无法直接改存档；资源编辑需导出后自行重打包。")
    if not mined:
        limits.append("未能读取代码字符串，货币 / 存档判断主要依赖关键参数扫描与存档探查。")

    recommend = []
    if currency:
        if save_fw:
            verdict = ("货币很可能为运行时 / 存档变量（检测到 " + "、".join(save_fw) + "）。"
                       "资源参数编辑改的是 bundle 内序列化字段（默认值 / 配置），改不到已有存档的实际余额；"
                       "实际余额需编辑存档。")
        else:
            verdict = ("发现货币 / 经济关键词，但未识别出明确存档框架。"
                       "货币可能序列化在资源里（可用关键参数编辑），也可能在存档 / 运行时；需进一步确认。")
        recommend.append("用「关键参数」一键识别扫描资源侧货币 / 价格字段（影响新游戏或经济配置）。")
        if save_fw:
            recommend.append(f"实际余额：定位并编辑存档（{save_path or '存档路径'}）。")
        if runtime_money:
            recommend.append("检测到 Money 属性 / cachedMoney 等运行时信号，存档值会覆盖资源默认值。")
        if cheat:
            recommend.append("留意游戏内置作弊 / 调试指令（见下方提示），可能可直接触发。")
    else:
        verdict = "未在代码字符串中发现明显货币关键词；可能用其它命名或纯存档驱动。"
        recommend.append("用「关键参数」扫描资源侧字段，并探查存档文件确认货币存储位置。")

    return {
        "game": product or root.name,
        "root": str(root),
        "platform": platform,
        "engine": det.get("engine_label"),
        "unity_version": det.get("unity_version"),
        "backend": backend,
        "steps": steps,
        "mined_files": mined,
        "currency": currency,
        "runtime_money": runtime_money,
        "save": {"frameworks": save_fw, "path": save_path, "exists": save_exists,
                 "files": save_files, "company": company, "product": product},
        "cheat_hints": cheat,
        "cheat_triggers": cheat_triggers,
        "feasibility": {"verdict": verdict, "recommend": recommend, "limits": limits},
        "analyzed_at": time.time(),
    }