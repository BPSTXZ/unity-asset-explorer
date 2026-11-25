"""角色组装：Transform 层级 + SkinnedMeshRenderer + 材质贴图（含 UDIM）→ 完整 GLB。"""
from __future__ import annotations

import io
import re
import struct
import zlib

from UnityPy.helpers.MeshHelper import MeshHandler

from .gltf import (_BinBuilder, build_glb, _flip_x_matrix, _matrix_list, _min_max,
                   _quat_from_euler_deg)
from .anim_retarget import decode_tracks, bake_pose

_BASE_COLOR_KEYS = ("_BaseColorMap", "_MainTex", "_BaseColor")
_TILE_RE = re.compile(r"\.(\d{4})$")


def _key(reader) -> tuple:
    return (getattr(reader.assets_file, "name", ""), reader.path_id)


def _texenvs(mat):
    te = mat.m_SavedProperties.m_TexEnvs
    return te.items() if isinstance(te, dict) else te


def _go_transform(go):
    for c in go.m_Component:
        comp = c.component
        if comp and comp.type.name == "Transform":
            return comp.deref()
    return None


def _root_reader(tr_reader, cache: dict):
    chain = []
    r = tr_reader
    while True:
        k = _key(r)
        if k in cache:
            root = cache[k]
            break
        chain.append(k)
        d = r.read()
        if d.m_Father and d.m_Father.m_PathID:
            r = d.m_Father.deref()
        else:
            root = r
            break
    for k in chain:
        cache[k] = root
    return root


def find_characters(env) -> list[dict]:
    """按 SkinnedMeshRenderer 的根 Transform 分组，返回角色根信息。"""
    cache: dict = {}
    roots: dict = {}
    for reader in env.objects:
        if reader.type.name != "SkinnedMeshRenderer":
            continue
        try:
            d = reader.read()
            go = d.m_GameObject.read()
            tr = _go_transform(go)
            if not tr:
                continue
            root = _root_reader(tr, cache)
        except Exception:
            continue
        k = _key(root)
        info = roots.setdefault(k, {"root_pid": root.path_id, "cab": k[0],
                                    "parts": 0, "bones": 0, "vertices": 0})
        info["parts"] += 1
        info["bones"] = max(info["bones"], len(d.m_Bones))
        try:
            mesh_reader = d.m_Mesh.deref()
            mesh = mesh_reader.read()
            vd = getattr(mesh, "m_VertexData", None)
            info["vertices"] += getattr(vd, "m_VertexCount", 0)
            info["size"] = info.get("size", 0) + mesh_reader.byte_size
        except Exception:
            pass
    for info in roots.values():
        try:
            tr_reader = next(r for r in env.objects
                             if r.type.name == "Transform" and _key(r) == (info["cab"], info["root_pid"]))
            info["name"] = tr_reader.read().m_GameObject.read().m_Name or f"Character_{info['root_pid']}"
        except Exception:
            info["name"] = f"Character_{info['root_pid']}"
    return list(roots.values())


def _quat_rotate(q, v):
    x, y, z, w = q
    tx = 2 * (y * v[2] - z * v[1])
    ty = 2 * (z * v[0] - x * v[2])
    tz = 2 * (x * v[1] - y * v[0])
    return [v[0] + w * tx + (y * tz - z * ty),
            v[1] + w * ty + (z * tx - x * tz),
            v[2] + w * tz + (x * ty - y * tx)]


def _smr_offset(tr_reader, root_reader) -> list[float]:
    """SMR 相对角色根的位移（Unity 坐标，含中间节点旋转/缩放）。"""
    chain = []
    r = tr_reader
    while _key(r) != _key(root_reader):
        d = r.read()
        chain.append(d)
        if not d.m_Father or not d.m_Father.m_PathID:
            break
        r = d.m_Father.deref()
    v = [0.0, 0.0, 0.0]
    for d in chain:
        p, q, s = d.m_LocalPosition, d.m_LocalRotation, d.m_LocalScale
        v = _quat_rotate((q.x, q.y, q.z, q.w), [v[0] * s.x, v[1] * s.y, v[2] * s.z])
        v = [v[0] + p.x, v[1] + p.y, v[2] + p.z]
    return v


def _collect_nodes(root_reader):
    """Transform 子树 → glTF 节点（左手→右手：位置/旋转镜像）。

    返回 (nodes, index, pathmap)；pathmap 为 {CRC32(相对根骨骼路径): 节点索引}，
    路径不含根节点名，与动画绑定路径一致，用于重定向。
    """
    nodes = []
    index: dict = {}
    pathmap: dict = {}

    def walk(reader, parent_path):
        d = reader.read()
        idx = len(nodes)
        index[_key(reader)] = idx
        name = d.m_GameObject.read().m_Name if d.m_GameObject else f"node_{idx}"
        if parent_path is None:
            path = ""  # 根节点：动画路径相对根，不含根名
        else:
            path = f"{parent_path}/{name}" if parent_path else name
        if path:
            pathmap[zlib.crc32(path.encode())] = idx
        p, r, s = d.m_LocalPosition, d.m_LocalRotation, d.m_LocalScale
        node = {
            "name": name,
            "translation": [-p.x, p.y, p.z],
            "rotation": [r.x, -r.y, -r.z, r.w],
            "scale": [s.x, s.y, s.z],
        }
        nodes.append(node)
        kids = []
        for c in d.m_Children:
            if c and c.m_PathID:
                kids.append(walk(c.deref(), path))
        if kids:
            node["children"] = kids
        return idx

    walk(root_reader, None)
    return nodes, index, pathmap


def character_bone_crc(root_reader) -> set:
    """角色骨骼路径 CRC 集合（用于动画兼容性匹配）。"""
    return set(_collect_nodes(root_reader)[2].keys())


def _mesh_primitives(bb, h, material_of_sub):
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
        # Unity UV 原点在左下，glTF 在左上 → 翻转 V
        uv = [(u[0], 1.0 - u[1]) for u in h.m_UV0]
        attrib["TEXCOORD_0"] = bb.add_accessor(
            struct.pack(f"<{len(uv)*2}f", *(c for v in uv for c in v)),
            5126, "VEC2", len(uv), target=34962)
    if h.m_Tangents and len(h.m_Tangents) == len(pos):
        tan = [(-t[0], t[1], t[2], -t[3]) for t in h.m_Tangents]
        attrib["TANGENT"] = bb.add_accessor(
            struct.pack(f"<{len(tan)*4}f", *(c for v in tan for c in v)),
            5126, "VEC4", len(tan), target=34962)
    if getattr(h, "m_BoneWeights", None) and len(h.m_BoneWeights) == len(pos):
        bone_idx = h.m_BoneIndices
        joints, weights = [], []
        for bi, bw in zip(bone_idx, h.m_BoneWeights):
            w = [float(x) for x in bw]
            ssum = sum(w) or 1.0
            joints.append(tuple(int(j) for j in bi))
            weights.append(tuple(x / ssum for x in w))
        attrib["JOINTS_0"] = bb.add_accessor(
            struct.pack(f"<{len(joints)*4}B", *(c for v in joints for c in v)),
            5121, "VEC4", len(joints), target=34962)
        attrib["WEIGHTS_0"] = bb.add_accessor(
            struct.pack(f"<{len(weights)*4}f", *(c for v in weights for c in v)),
            5126, "VEC4", len(weights), target=34962)

    primitives = []
    for si, tris in enumerate(h.get_triangles()):
        if not tris:
            continue
        idx_flat = [i for tri in tris for i in (tri[0], tri[2], tri[1])]
        use32 = len(pos) > 65535
        acc_idx = bb.add_accessor(
            struct.pack(f"<{len(idx_flat)}{'I' if use32 else 'H'}", *idx_flat),
            5125 if use32 else 5123, "SCALAR", len(idx_flat), target=34963)
        primitives.append({"attributes": attrib, "indices": acc_idx, "mode": 4,
                           "material": material_of_sub(si)})
    return primitives


class MaterialBuilder:
    """材质/贴图 → glTF（含 UDIM 平移与透明模式），角色组装与单网格贴图共用。"""

    def __init__(self, bb, gltf):
        self.bb = bb
        self.gltf = gltf
        self.tex_cache: dict = {}
        self.mat_cache: dict = {}

    def add_texture(self, tex_pptr):
        if not tex_pptr or not tex_pptr.m_PathID:
            return None
        pid = tex_pptr.m_PathID
        if pid in self.tex_cache:
            return self.tex_cache[pid]
        try:
            tex = tex_pptr.read()
            img = tex.image
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            view = self.bb.add_view(buf.getvalue())
            self.gltf["images"].append({"bufferView": view, "mimeType": "image/png",
                                        "name": tex.m_Name})
            self.gltf["textures"].append({"sampler": 0, "source": len(self.gltf["images"]) - 1})
            self.tex_cache[pid] = len(self.gltf["textures"]) - 1
            return self.tex_cache[pid]
        except Exception:
            return None

    def add_material(self, mat_pptr):
        if not mat_pptr or not mat_pptr.m_PathID:
            return None
        pid = mat_pptr.m_PathID
        if pid in self.mat_cache:
            return self.mat_cache[pid]
        try:
            md = mat_pptr.read()
        except Exception:
            return None
        pbr = {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.0, "roughnessFactor": 0.8}
        mat = {"name": md.m_Name, "pbrMetallicRoughness": pbr, "doubleSided": True}
        texs = dict(_texenvs(md))
        for key in _BASE_COLOR_KEYS:
            val = texs.get(key)
            if val is None:
                continue
            ti = self.add_texture(val.m_Texture)
            if ti is None:
                continue
            info = {"index": ti}
            try:
                tname = val.m_Texture.read().m_Name
                m = _TILE_RE.search(tname or "")
                if m and m.group(1) != "1001":
                    # UDIM：子网格 UV 处于全局 tile 坐标，平移回 0-1
                    info["extensions"] = {"KHR_texture_transform":
                                          {"offset": [1001 - int(m.group(1)), 0]}}
                    self.gltf.setdefault("extensionsUsed", [])
                    if "KHR_texture_transform" not in self.gltf["extensionsUsed"]:
                        self.gltf["extensionsUsed"].append("KHR_texture_transform")
            except Exception:
                pass
            pbr["baseColorTexture"] = info
            break
        kw = getattr(md, "m_ShaderKeywords", "") or ""
        if isinstance(kw, (list, tuple)):
            kw = " ".join(kw)
        if "_ALPHATEST_ON" in kw:
            mat["alphaMode"] = "MASK"
            mat["alphaCutoff"] = 0.5
        elif "_ALPHABLEND_ON" in kw:
            mat["alphaMode"] = "BLEND"
        self.gltf["materials"].append(mat)
        self.mat_cache[pid] = len(self.gltf["materials"]) - 1
        return self.mat_cache[pid]


def _apply_clip(gltf, nodes, pathmap, bb, clip, pose_time):
    """把动画曲线重定向到角色骨骼节点：pose_time 为 None 时嵌入动画，否则烘焙该帧姿态。"""
    if pose_time is not None:
        for path_hash, attrs in bake_pose(clip, pose_time).items():
            ni = pathmap.get(path_hash)
            if ni is None:
                continue
            if 1 in attrs:
                p = attrs[1]
                nodes[ni]["translation"] = [-p[0], p[1], p[2]]
            if 2 in attrs:
                q = attrs[2]
                nodes[ni]["rotation"] = [q[0], -q[1], -q[2], q[3]]
            elif 4 in attrs:
                e = attrs[4]
                nodes[ni]["rotation"] = list(_quat_from_euler_deg(e[0], e[1], e[2]))
            if 3 in attrs:
                nodes[ni]["scale"] = list(attrs[3])
        return
    decoded = decode_tracks(clip)
    if not decoded:
        return
    _, tracks = decoded
    samplers, channels = [], []
    for tr in tracks:
        ni = pathmap.get(tr["path"])
        if ni is None:
            continue
        times, vals, attr = tr["times"], tr["values"], tr["attribute"]
        if attr == 1:
            out = [(-vals[i], vals[i + 1], vals[i + 2]) for i in range(0, len(vals), 3)]
            gpath, dim = "translation", 3
        elif attr == 2:
            out = [(vals[i], -vals[i + 1], -vals[i + 2], vals[i + 3])
                   for i in range(0, len(vals), 4)]
            gpath, dim = "rotation", 4
        elif attr == 3:
            out = [(vals[i], vals[i + 1], vals[i + 2]) for i in range(0, len(vals), 3)]
            gpath, dim = "scale", 3
        else:  # 欧拉角
            out = [_quat_from_euler_deg(vals[i], vals[i + 1], vals[i + 2])
                   for i in range(0, len(vals), 3)]
            gpath, dim = "rotation", 4
        if not out:
            continue
        acc_in = bb.add_accessor(
            struct.pack(f"<{len(times)}f", *times), 5126, "SCALAR", len(times),
            ([times[0]], [times[-1]]))
        acc_out = bb.add_accessor(
            struct.pack(f"<{len(out)*dim}f", *(c for v in out for c in v)),
            5126, "VEC3" if dim == 3 else "VEC4", len(out))
        samplers.append({"input": acc_in, "output": acc_out, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1,
                         "target": {"node": ni, "path": gpath}})
    if channels:
        gltf["animations"] = [{"name": clip.m_Name or "clip",
                               "samplers": samplers, "channels": channels}]


def character_to_glb(env, root_reader, textured: bool = True,
                     clip=None, pose_time: float | None = None) -> bytes:
    nodes, node_index, pathmap = _collect_nodes(root_reader)
    root_key = _key(root_reader)

    bb = _BinBuilder()
    gltf = {
        "asset": {"version": "2.0", "generator": "asset-explorer"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": nodes, "meshes": [], "skins": [],
        "materials": [], "textures": [], "images": [],
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
        "buffers": [{"byteLength": 0}],
        "bufferViews": bb.views, "accessors": bb.accessors,
    }
    mb = MaterialBuilder(bb, gltf)
    plain_idx = None
    if not textured:
        gltf["materials"].append({"name": "plain", "doubleSided": True,
                                  "pbrMetallicRoughness": {
                                      "baseColorFactor": [0.75, 0.78, 0.82, 1],
                                      "metallicFactor": 0.0, "roughnessFactor": 0.65}})
        plain_idx = 0

    cache: dict = {}
    recentered = False
    for reader in env.objects:
        if reader.type.name != "SkinnedMeshRenderer":
            continue
        try:
            d = reader.read()
            go = d.m_GameObject.read()
            tr = _go_transform(go)
            if not tr or _key(_root_reader(tr, cache)) != root_key:
                continue
            if not recentered:
                # 场景世界偏移可能挂在中间节点上 → 平移根节点使角色回到原点
                off = _smr_offset(tr, root_reader)
                mir = [-off[0], off[1], off[2]]
                rd = root_reader.read()
                rq, rs = rd.m_LocalRotation, rd.m_LocalScale
                mir = _quat_rotate((rq.x, -rq.y, -rq.z, rq.w),
                                   [mir[0] * rs.x, mir[1] * rs.y, mir[2] * rs.z])
                nodes[0]["translation"] = [-mir[0], -mir[1], -mir[2]]
                recentered = True
            mesh = d.m_Mesh.read()
            h = MeshHandler(mesh)
            h.process()
            if not h.m_Vertices:
                continue
            if textured:
                mats = [mb.add_material(m) for m in d.m_Materials]
                if not any(m is not None for m in mats):
                    gltf["materials"].append({"name": "default", "doubleSided": True,
                                              "pbrMetallicRoughness": {
                                                  "baseColorFactor": [0.75, 0.78, 0.82, 1],
                                                  "metallicFactor": 0.0, "roughnessFactor": 0.65}})
                    mats = [len(gltf["materials"]) - 1]
            else:
                mats = [plain_idx]

            def mat_of(si, mats=mats):
                m = mats[si] if si < len(mats) else mats[0]
                return m if m is not None else 0

            prims = _mesh_primitives(bb, h, mat_of)
            if not prims:
                continue
            gltf["meshes"].append({"name": mesh.m_Name, "primitives": prims})
            mesh_idx = len(gltf["meshes"]) - 1

            node_idx = node_index.get(_key(tr))
            if node_idx is None:
                continue
            has_weights = "JOINTS_0" in prims[0]["attributes"]
            if has_weights:
                joints = [node_index.get(_key(b.deref())) for b in d.m_Bones]
                if joints and not any(j is None for j in joints):
                    ibm = []
                    for m in mesh.m_BindPose:
                        ibm.extend(_flip_x_matrix(_matrix_list(m)))
                    acc_ibm = bb.add_accessor(
                        struct.pack(f"<{len(ibm)}f", *ibm), 5126, "MAT4", len(joints))
                    gltf["skins"].append(
                        {"inverseBindMatrices": acc_ibm, "skeleton": 0, "joints": joints})
                    skin_node = {"name": mesh.m_Name, "mesh": mesh_idx,
                                 "skin": len(gltf["skins"]) - 1}
                    nodes.append(skin_node)
                    nodes[node_idx].setdefault("children", []).append(len(nodes) - 1)
                    continue
            # 无蒙皮权重的小部件（眼睛/头发等）：刚性绑定到根骨骼，随骨架姿态一起运动，
            # 否则停留在绑定位姿而与已蒙皮的身体错位
            rb = getattr(d, "m_RootBone", None)
            bind = getattr(mesh, "m_BindPose", None)
            if rb and rb.m_PathID and bind and d.m_Bones:
                bone_keys = [_key(b.deref()) for b in d.m_Bones]
                rb_key = _key(rb.deref())
                joint_node = node_index.get(rb_key)
                if rb_key in bone_keys and joint_node is not None:
                    ri = bone_keys.index(rb_key)
                    n = len(h.m_Vertices)
                    acc_j = bb.add_accessor(
                        struct.pack(f"<{n*4}B", *([0, 0, 0, 0] * n)), 5121, "VEC4", n, target=34962)
                    acc_w = bb.add_accessor(
                        struct.pack(f"<{n*4}f", *([1.0, 0.0, 0.0, 0.0] * n)), 5126, "VEC4", n, target=34962)
                    for p in prims:
                        p["attributes"]["JOINTS_0"] = acc_j
                        p["attributes"]["WEIGHTS_0"] = acc_w
                    ibm = _flip_x_matrix(_matrix_list(bind[ri]))
                    acc_ibm = bb.add_accessor(struct.pack("<16f", *ibm), 5126, "MAT4", 1)
                    gltf["skins"].append(
                        {"inverseBindMatrices": acc_ibm, "skeleton": 0, "joints": [joint_node]})
                    skin_node = {"name": mesh.m_Name, "mesh": mesh_idx,
                                 "skin": len(gltf["skins"]) - 1}
                    nodes.append(skin_node)
                    nodes[node_idx].setdefault("children", []).append(len(nodes) - 1)
                    continue
            nodes[node_idx]["mesh"] = mesh_idx
        except Exception:
            continue

    if not gltf["meshes"]:
        raise ValueError("该角色无可组装的蒙皮网格")
    if clip is not None:
        _apply_clip(gltf, nodes, pathmap, bb, clip, pose_time)
    for key in ("materials", "textures", "images", "skins"):
        if not gltf[key]:
            del gltf[key]
    gltf["buffers"][0]["byteLength"] = len(bb.buf)
    return build_glb(gltf, bytes(bb.buf))


def _mesh_materials(env, mesh_reader) -> list:
    """网格材质：蒙皮网格直接挂在 SkinnedMeshRenderer；静态网格经
    MeshFilter→同 GameObject 的 MeshRenderer 解析（Mesh 本身不存材质）。"""
    mk = _key(mesh_reader)

    def _is_mesh(pptr) -> bool:
        try:
            return bool(pptr and pptr.m_PathID and _key(pptr.deref()) == mk)
        except Exception:
            return False

    for r in env.objects:
        if r.type.name != "SkinnedMeshRenderer":
            continue
        try:
            d = r.read()
            if _is_mesh(d.m_Mesh):
                return list(d.m_Materials)
        except Exception:
            continue
    go_keys = set()
    for r in env.objects:
        if r.type.name != "MeshFilter":
            continue
        try:
            d = r.read()
            if _is_mesh(d.m_Mesh):
                go_keys.add(_key(d.m_GameObject.deref()))
        except Exception:
            continue
    if not go_keys:
        return []
    for r in env.objects:
        if r.type.name != "MeshRenderer":
            continue
        try:
            d = r.read()
            if _key(d.m_GameObject.deref()) in go_keys:
                return list(d.m_Materials)
        except Exception:
            continue
    return []


def mesh_to_glb_textured(env, mesh_reader) -> bytes:
    """单网格按需贴图：解析关联材质生成带贴图 GLB（静态，不含蒙皮）。"""
    mesh = mesh_reader.read()
    h = MeshHandler(mesh)
    h.process()
    if not h.m_Vertices:
        raise ValueError("网格无顶点数据")
    mat_pptrs = _mesh_materials(env, mesh_reader)
    if not mat_pptrs:
        raise ValueError("未找到关联材质（该网格可能未被场景使用）")

    bb = _BinBuilder()
    gltf = {
        "asset": {"version": "2.0", "generator": "asset-explorer"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": mesh.m_Name or "mesh"}],
        "meshes": [], "materials": [], "textures": [], "images": [],
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
        "buffers": [{"byteLength": 0}],
        "bufferViews": bb.views, "accessors": bb.accessors,
    }
    mb = MaterialBuilder(bb, gltf)
    mats = [mb.add_material(m) for m in mat_pptrs]
    if not any(m is not None for m in mats):
        raise ValueError("关联材质无可读取贴图")

    def mat_of(si):
        m = mats[si] if si < len(mats) else mats[0]
        return m if m is not None else 0

    prims = _mesh_primitives(bb, h, mat_of)
    if not prims:
        raise ValueError("网格无三角形数据")
    gltf["meshes"].append({"name": mesh.m_Name, "primitives": prims})
    for key in ("materials", "textures", "images"):
        if not gltf[key]:
            del gltf[key]
    gltf["buffers"][0]["byteLength"] = len(bb.buf)
    return build_glb(gltf, bytes(bb.buf))