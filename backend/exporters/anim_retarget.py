"""Generic 动画解码与重定向：streamed/dense/constant 曲线 → 每骨骼关键帧。

Unity 2018.3+ 的 AnimationClip 即便挂在 m_MuscleClip 下，也可能是 Generic
Transform 曲线（按骨骼路径绑定）。曲线全局索引顺序为
[streamed][dense][constant]，绑定按顺序累加维度（Transform: 位移3/旋转4/缩放3/欧拉3，
其余1）。坐标转换与角色节点一致：位移(-x,y,z)、旋转(x,-y,-z,w)、缩放(x,y,z)。
"""
from __future__ import annotations

import struct


def _unwrap(x):
    return x.data if type(x).__name__ == "OffsetPtr" else x


def _f(x) -> float:
    return struct.unpack("<f", struct.pack("<I", x & 0xFFFFFFFF))[0]


def _dim(g) -> int:
    if g.typeID == 4:  # Transform
        return {1: 3, 2: 4, 3: 3, 4: 3}.get(g.attribute, 1)
    return 1


def _eval(samples, t: float) -> float:
    if not samples:
        return 0.0
    if t <= samples[0][0]:
        return samples[0][1]
    if t >= samples[-1][0]:
        return samples[-1][1]
    for a, b in zip(samples, samples[1:]):
        if a[0] <= t <= b[0]:
            span = (b[0] - a[0]) or 1.0
            return a[1] + (b[1] - a[1]) * ((t - a[0]) / span)
    return samples[-1][1]


def _curve_reader(clip):
    """构建按全局曲线索引取样的函数，返回 (stop, streamCount, denseCount, samples_fn)。"""
    mc = _unwrap(getattr(clip, "m_MuscleClip", None))
    if mc is None:
        return None
    cl = _unwrap(getattr(mc, "m_Clip", None))
    if cl is None:
        return None
    sc = _unwrap(getattr(cl, "m_StreamedClip", None))
    dc = _unwrap(getattr(cl, "m_DenseClip", None))
    cc = _unwrap(getattr(cl, "m_ConstantClip", None))
    stream_count = getattr(sc, "curveCount", 0) or 0
    dense_count = getattr(dc, "m_CurveCount", 0) or 0
    const = list(cc.data) if cc is not None and getattr(cc, "data", None) else []
    stop = getattr(mc, "m_StopTime", 0) or 0

    skeys: dict[int, list] = {}
    if sc is not None and getattr(sc, "data", None):
        w = list(sc.data)
        i, n = 0, len(w)
        while i + 1 < n:
            t = _f(w[i])
            cnt = w[i + 1]
            i += 2
            for _ in range(cnt):
                if i + 4 >= n:
                    break
                idx = w[i]
                val = _f(w[i + 4])
                i += 5
                if abs(t) < 1e30:
                    skeys.setdefault(idx, []).append((t, val))

    def samples(gi: int):
        if gi < stream_count:
            return sorted(skeys.get(gi, []))
        if gi < stream_count + dense_count:
            j = gi - stream_count
            fc = dc.m_FrameCount
            rate = dc.m_SampleRate or 30.0
            arr = dc.m_SampleArray
            return [(dc.m_BeginTime + k / rate, arr[k * dense_count + j])
                    for k in range(fc)]
        j = gi - stream_count - dense_count
        if j < len(const):
            return [(0.0, const[j]), (stop, const[j])]
        return []

    return stop, samples


def decode_tracks(clip):
    """→ (duration, tracks)。track = {path, attribute, dim, times, values( Unity 坐标平铺)}。"""
    cr = _curve_reader(clip)
    if cr is None:
        return None
    stop, samples = cr
    gb = getattr(clip.m_ClipBindingConstant, "genericBindings", None) or []
    tracks = []
    off = 0
    for g in gb:
        d = _dim(g)
        idxs = list(range(off, off + d))
        off += d
        if g.typeID != 4 or g.attribute not in (1, 2, 3, 4):
            continue
        comp = [samples(gi) for gi in idxs]
        times = sorted({t for s in comp for t, _ in s})
        if not times:
            continue
        if times[0] > 0:
            times = [0.0] + times
        if stop and times[-1] < stop:
            times = times + [stop]
        values = [_eval(s, t) for t in times for s in comp]
        tracks.append({"path": g.path, "attribute": g.attribute,
                       "dim": d, "times": times, "values": values})
    return stop, tracks


def bake_pose(clip, t: float) -> dict:
    """在时间 t 求值所有 Transform 曲线 → {path_hash: {attribute: [分量]}}（Unity 坐标）。"""
    cr = _curve_reader(clip)
    if cr is None:
        return {}
    _, samples = cr
    gb = getattr(clip.m_ClipBindingConstant, "genericBindings", None) or []
    pose: dict = {}
    off = 0
    for g in gb:
        d = _dim(g)
        idxs = list(range(off, off + d))
        off += d
        if g.typeID != 4 or g.attribute not in (1, 2, 3, 4):
            continue
        vals = [_eval(samples(gi), t) for gi in idxs]
        pose.setdefault(g.path, {})[g.attribute] = vals
    return pose


def clip_duration(clip) -> float:
    mc = getattr(clip, "m_MuscleClip", None)
    if mc is not None and getattr(mc, "m_StopTime", 0):
        return mc.m_StopTime
    dur = 0.0
    for fld in ("m_PositionCurves", "m_RotationCurves", "m_ScaleCurves", "m_EulerCurves"):
        for c in getattr(clip, fld, None) or []:
            keys = c.curve.m_Curve
            if keys:
                dur = max(dur, keys[-1].time)
    return dur