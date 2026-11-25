"""Unity 资源 → glTF/GLB 转换（模型、蒙皮、动画），供 three.js 预览与导出。"""
from __future__ import annotations

import json
import math
import struct

from UnityPy.helpers.MeshHelper import MeshHandler


# ---------- GLB 打包 ----------

def _pad4(data: bytes, fill: bytes = b"\x00") -> bytes:
    return data + fill * ((4 - len(data) % 4) % 4)


def build_glb(gltf: dict, bin_chunk: bytes) -> bytes:
    json_bytes = _pad4(json.dumps(gltf, separators=(",", ":")).encode(), b" ")
    bin_chunk = _pad4(bin_chunk)
    total = 12 + 8 + len(json_bytes) + (8 + len(bin_chunk) if bin_chunk else 0)
    out = struct.pack("<III", 0x46546C67, 2, total)
    out += struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    if bin_chunk:
        out += struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk
    return out


class _BinBuilder:
    def __init__(self):
        self.buf = bytearray()
        self.views: list[dict] = []
        self.accessors: list[dict] = []

    def add_view(self, data: bytes) -> int:
        offset = len(self.buf)
        self.buf += _pad4(data)
        self.views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data)})
        return len(self.views) - 1

    def add_accessor(self, data: bytes, comp_type: int, gltf_type: str, count: int,
                     min_max: tuple | None = None, target: int | None = None) -> int:
        offset = len(self.buf)
        self.buf += _pad4(data)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            view["target"] = target
        self.views.append(view)
        acc = {"bufferView": len(self.views) - 1, "componentType": comp_type,
               "count": count, "type": gltf_type}
        if min_max:
            acc["min"], acc["max"] = min_max
        self.accessors.append(acc)
        return len(self.accessors) - 1


def _min_max(values: list[tuple]) -> tuple:
    n = len(values[0])
    return ([min(v[i] for v in values) for i in range(n)],
            [max(v[i] for v in values) for i in range(n)])


def _matrix_list(m) -> list[float]:
    """UnityPy Matrix4x4 → 列主序 16 浮点，兼容不同属性布局。"""
    if hasattr(m, "e00"):
        vals = [[getattr(m, f"e{r}{c}") for c in range(4)] for r in range(4)]
    elif hasattr(m, "m00"):
        vals = [[getattr(m, f"m{r:02d}") if hasattr(m, "m00") else getattr(m, f"m{r}{c}")
                 for c in range(4)] for r in range(4)]
    else:
        vals = [[m[r][c] for c in range(4)] for r in range(4)]
    # 列主序
    return [vals[r][c] for c in range(4) for r in range(4)]


def _flip_x_matrix(m16: list[float]) -> list[float]:
    """左手系→右手系：M' = F·M·F，F = diag(-1,1,1,1)（列主序）。"""
    out = list(m16)
    for col in range(4):
        for row in range(4):
            i = col * 4 + row
            if (row == 0) != (col == 0):
                out[i] = -out[i]
    return out


# ---------- Mesh → glTF ----------

def mesh_to_glb(mesh) -> bytes:
    h = MeshHandler(mesh)
    h.process()
    if not h.m_Vertices or h.m_VertexCount <= 0:
        raise ValueError("网格无顶点数据")

    bb = _BinBuilder()
    # Unity 左手系 → glTF 右手系：翻转 X 并反转绕序
    pos = [(-v[0], v[1], v[2]) for v in h.m_Vertices]
    acc_pos = bb.add_accessor(
        struct.pack(f"<{len(pos)*3}f", *(c for v in pos for c in v)),
        5126, "VEC3", len(pos), _min_max(pos), 34962)
    attrib = {"POSITION": acc_pos}

    if h.m_Normals and len(h.m_Normals) == len(pos):
        nrm = [(-n[0], n[1], n[2]) for n in h.m_Normals]
        attrib["NORMAL"] = bb.add_accessor(
            struct.pack(f"<{len(nrm)*3}f", *(c for v in nrm for c in v)),
            5126, "VEC3", len(nrm), target=34962)
    if h.m_UV0 and len(h.m_UV0) == len(pos):
        uv = [(u[0], u[1]) for u in h.m_UV0]
        attrib["TEXCOORD_0"] = bb.add_accessor(
            struct.pack(f"<{len(uv)*2}f", *(c for v in uv for c in v)),
            5126, "VEC2", len(uv), target=34962)
    if h.m_Tangents and len(h.m_Tangents) == len(pos):
        tan = [(-t[0], t[1], t[2], t[3]) for t in h.m_Tangents]
        attrib["TANGENT"] = bb.add_accessor(
            struct.pack(f"<{len(tan)*4}f", *(c for v in tan for c in v)),
            5126, "VEC4", len(tan), target=34962)

    skinned = bool(getattr(h, "m_BoneWeights", None)) and bool(getattr(h, "m_BindPose", None))
    if skinned and len(h.m_BoneWeights) == len(pos):
        joints, weights = [], []
        for bi, bw in zip(h.m_BoneIndices, h.m_BoneWeights):
            w = [float(x) for x in bw]
            s = sum(w) or 1.0
            joints.append(tuple(int(j) for j in bi))
            weights.append(tuple(x / s for x in w))
        attrib["JOINTS_0"] = bb.add_accessor(
            struct.pack(f"<{len(joints)*4}B", *(c for v in joints for c in v)),
            5121, "VEC4", len(joints), target=34962)
        attrib["WEIGHTS_0"] = bb.add_accessor(
            struct.pack(f"<{len(weights)*4}f", *(c for v in weights for c in v)),
            5126, "VEC4", len(weights), target=34962)

    primitives = []
    for tris in h.get_triangles():
        if not tris:
            continue
        idx_flat = [i for tri in tris for i in (tri[0], tri[2], tri[1])]
        use32 = len(pos) > 65535
        acc_idx = bb.add_accessor(
            struct.pack(f"<{len(idx_flat)}{'I' if use32 else 'H'}", *idx_flat),
            5125 if use32 else 5123, "SCALAR", len(idx_flat), target=34963)
        primitives.append({"attributes": attrib, "indices": acc_idx, "mode": 4, "material": 0})
    if not primitives:
        raise ValueError("网格无三角形数据")

    gltf = {
        "asset": {"version": "2.0", "generator": "asset-explorer"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": mesh.m_Name or "mesh"}],
        "meshes": [{"primitives": primitives, "name": mesh.m_Name}],
        # glTF 默认 metallicFactor=1，无环境贴图时金属渲染为纯黑；显式给非金属默认材质
        "materials": [{
            "name": "preview",
            "pbrMetallicRoughness": {
                "baseColorFactor": [0.75, 0.78, 0.82, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.65,
            },
            "doubleSided": True,
        }],
        "buffers": [{"byteLength": len(bb.buf)}],
        "bufferViews": bb.views, "accessors": bb.accessors,
    }

    if skinned and "JOINTS_0" in attrib:
        bone_count = len(h.m_BindPose)
        ibm = []
        for m in h.m_BindPose:
            ibm.extend(_flip_x_matrix(_matrix_list(m)))
        acc_ibm = bb.add_accessor(
            struct.pack(f"<{len(ibm)}f", *ibm), 5126, "MAT4", bone_count)
        bone_nodes = [{"name": f"Bone_{i}"} for i in range(bone_count)]
        gltf["nodes"].extend(bone_nodes)
        gltf["nodes"][0]["skin"] = 0
        gltf["skins"] = [{
            "inverseBindMatrices": acc_ibm,
            "skeleton": 1,
            "joints": list(range(1, bone_count + 1)),
        }]
        gltf["scenes"][0]["nodes"] = list(range(bone_count + 1))
        gltf["buffers"][0]["byteLength"] = len(bb.buf)

    return build_glb(gltf, bytes(bb.buf))


# ---------- AnimationClip → glTF ----------

def _quat_from_euler_deg(x: float, y: float, z: float) -> tuple:
    """Unity 欧拉角（度，Z→X→Y 顺序）→ 四元数 (x,y,z,w)，再翻转到右手系。"""
    rx, ry, rz = math.radians(x), math.radians(y), math.radians(z)
    cx, sx = math.cos(rx / 2), math.sin(rx / 2)
    cy, sy = math.cos(ry / 2), math.sin(ry / 2)
    cz, sz = math.cos(rz / 2), math.sin(rz / 2)
    qx = (sx * cy * cz + cx * sy * sz)
    qy = (cx * sy * cz - sx * cy * sz)
    qz = (cx * cy * sz - sx * sy * cz)
    qw = (cx * cy * cz + sx * sy * sz)
    return (-qx, qy, qz, qw)


def _flip_quat(q) -> tuple:
    return (-q[0], q[1], q[2], q[3])


def animation_to_glb(clip) -> bytes | None:
    """通用（Generic）动画曲线 → 带骨架节点动画的 GLB。Humanoid 肌肉动画返回 None。"""
    pos_curves = list(getattr(clip, "m_PositionCurves", None) or [])
    rot_curves = list(getattr(clip, "m_RotationCurves", None) or [])
    scale_curves = list(getattr(clip, "m_ScaleCurves", None) or [])
    euler_curves = list(getattr(clip, "m_EulerCurves", None) or [])
    if not (pos_curves or rot_curves or scale_curves or euler_curves):
        return None

    # 由曲线路径构建节点层级
    node_index: dict[str, int] = {}
    nodes: list[dict] = []

    def ensure_node(path: str) -> int:
        if path in node_index:
            return node_index[path]
        parts = path.split("/") if path else ["root"]
        parent_idx = None
        cur_path = ""
        for part in parts:
            cur_path = f"{cur_path}/{part}" if cur_path else part
            if cur_path in node_index:
                parent_idx = node_index[cur_path]
                continue
            idx = len(nodes)
            nodes.append({"name": part, "children": []})
            node_index[cur_path] = idx
            if parent_idx is not None:
                nodes[parent_idx]["children"].append(idx)
            parent_idx = idx
        return node_index[path]

    bb = _BinBuilder()
    samplers, channels = [], []
    max_time = 0.0

    def add_channel(path: str, gltf_path: str, times: list[float], values: list[tuple], dim: int):
        nonlocal max_time
        if not times:
            return
        node = ensure_node(path)
        max_time = max(max_time, times[-1])
        acc_in = bb.add_accessor(
            struct.pack(f"<{len(times)}f", *times), 5126, "SCALAR", len(times),
            ([times[0]], [times[-1]]))
        acc_out = bb.add_accessor(
            struct.pack(f"<{len(values)*dim}f", *(c for v in values for c in v)),
            5126, "VEC3" if dim == 3 else "VEC4", len(values))
        samplers.append({"input": acc_in, "output": acc_out, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1,
                         "target": {"node": node, "path": gltf_path}})

    for c in pos_curves:
        keys = c.curve.m_Curve
        add_channel(c.path, "translation",
                    [k.time for k in keys],
                    [(-k.value[0], k.value[1], k.value[2]) for k in keys], 3)
    for c in scale_curves:
        keys = c.curve.m_Curve
        add_channel(c.path, "scale",
                    [k.time for k in keys],
                    [(k.value[0], k.value[1], k.value[2]) for k in keys], 3)
    for c in rot_curves:
        keys = c.curve.m_Curve
        add_channel(c.path, "rotation",
                    [k.time for k in keys],
                    [_flip_quat((k.value[0], k.value[1], k.value[2], k.value[3])) for k in keys], 4)
    for c in euler_curves:
        keys = c.curve.m_Curve
        add_channel(c.path, "rotation",
                    [k.time for k in keys],
                    [_quat_from_euler_deg(k.value[0], k.value[1], k.value[2]) for k in keys], 4)

    if not channels:
        return None
    for n in nodes:
        if not n["children"]:
            del n["children"]
    roots = [i for i, n in enumerate(nodes)
             if not any(i in other.get("children", []) for other in nodes)]
    gltf = {
        "asset": {"version": "2.0", "generator": "asset-explorer"},
        "scene": 0, "scenes": [{"nodes": roots}],
        "nodes": nodes,
        "animations": [{"name": clip.m_Name or "clip", "samplers": samplers, "channels": channels}],
        "buffers": [{"byteLength": len(bb.buf)}],
        "bufferViews": bb.views, "accessors": bb.accessors,
    }
    return build_glb(gltf, bytes(bb.buf))


def _unwrap(x):
    return x.data if type(x).__name__ == "OffsetPtr" else x


def humanoid_export(clip) -> dict:
    """Humanoid 肌肉动画无损数据导出（绑定/常量曲线/密集采样/编码流）。

    完整 Humanoid→骨骼重定向需要 Avatar 信息，列入后续版本；
    此处保留全部原始数据，可用外部工具重定向。
    """
    import base64

    mc = getattr(clip, "m_MuscleClip", None)
    out = {
        "name": clip.m_Name,
        "animation_type": "humanoid",
        "sample_rate": getattr(clip, "m_SampleRate", 60),
        "start_time": getattr(mc, "m_StartTime", 0) if mc else 0,
        "stop_time": getattr(mc, "m_StopTime", 0) if mc else 0,
        "loop_time": getattr(mc, "m_LoopTime", False) if mc else False,
        "events": [
            {"time": e.time, "function": e.functionName, "data": e.data}
            for e in (getattr(clip, "m_Events", None) or [])
        ],
    }
    cbc = getattr(clip, "m_ClipBindingConstant", None)
    if cbc:
        out["bindings"] = [
            {"path_hash": g.path, "attribute": g.attribute, "typeID": g.typeID,
             "classID": g.classID, "customType": g.customType,
             "isPPtrCurve": bool(g.isPPtrCurve)}
            for g in (cbc.genericBindings or [])
        ]
    if mc is None:
        return out
    c = _unwrap(mc.m_Clip)
    if c is None:
        return out
    cc = _unwrap(getattr(c, "m_ConstantClip", None))
    if cc is not None and getattr(cc, "data", None) is not None:
        out["constant_values"] = list(cc.data)
    dc = _unwrap(getattr(c, "m_DenseClip", None))
    if dc is not None and getattr(dc, "m_FrameCount", 0):
        out["dense_clip"] = {
            "frame_count": dc.m_FrameCount, "curve_count": dc.m_CurveCount,
            "sample_rate": dc.m_SampleRate, "begin_time": dc.m_BeginTime,
            "samples": list(dc.m_SampleArray),
        }
    sc = _unwrap(getattr(c, "m_StreamedClip", None))
    if sc is not None and getattr(sc, "data", None):
        words = list(sc.data)
        out["streamed_clip"] = {
            "curve_count": sc.curveCount,
            "data_base64": base64.b64encode(
                struct.pack(f"<{len(words)}I", *words)).decode(),
        }
    return out


def animation_info(clip) -> dict:
    def curve_summary(curves):
        out = []
        for c in curves or []:
            keys = c.curve.m_Curve
            out.append({"path": c.path, "keys": len(keys),
                        "duration": keys[-1].time if keys else 0})
        return out
    muscle = getattr(clip, "m_MuscleClip", None)
    info = {
        "name": clip.m_Name,
        "sample_rate": getattr(clip, "m_SampleRate", 60),
        "wrap_mode": getattr(clip, "m_WrapMode", 0),
        "position_curves": curve_summary(getattr(clip, "m_PositionCurves", None)),
        "rotation_curves": curve_summary(getattr(clip, "m_RotationCurves", None)),
        "scale_curves": curve_summary(getattr(clip, "m_ScaleCurves", None)),
        "euler_curves": curve_summary(getattr(clip, "m_EulerCurves", None)),
        "float_curves": len(getattr(clip, "m_FloatCurves", None) or []),
        "humanoid": bool(muscle is not None and getattr(muscle, "m_StopTime", 0)),
        "stop_time": getattr(muscle, "m_StopTime", None) if muscle else None,
    }
    if info["humanoid"]:
        c = _unwrap(muscle.m_Clip)
        sc = _unwrap(getattr(c, "m_StreamedClip", None)) if c else None
        dc = _unwrap(getattr(c, "m_DenseClip", None)) if c else None
        info["muscle_curves"] = (getattr(sc, "curveCount", 0) or 0) + (getattr(dc, "m_CurveCount", 0) or 0)
        cbc = getattr(clip, "m_ClipBindingConstant", None)
        info["bindings"] = len(cbc.genericBindings or []) if cbc else 0
        info["events"] = len(getattr(clip, "m_Events", None) or [])
    return info