"""Camera-driven texel density: measurement, per-face attributes, report and cache.

For every face, from every camera moment that actually sees it (inside the frame, not hidden):

    required px/m = focal length in pixels / depth

and what the UVs + texture deliver:

    current texels/m = texture size * sqrt(uv area / world area)

Face attributes written (Spreadsheet): tc_required, tc_current, tc_ratio, tc_screen_px, tc_driver.
Colour attributes (Solid > Attribute): TC_Required, TC_Ratio.
"""

import hashlib
import json
import math
import time

import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

def to_srgb(rgb):
    return tuple(c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055 for c in rgb)


def _srgb(h):
    """'#rrggbb' -> linear RGB (what colour attributes and the GPU module expect)."""
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return tuple(round(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4, 5) for v in c)


# Ratio: diverging. Warm = less detail than the cameras need, green = right, cool = more than needed.
RATIO_BANDS = [  # (upper bound ratio, colour, legend label)
    (0.5, _srgb("#d7301f"), ("under 0.5x", "blurry")),
    (0.8, _srgb("#f4a236"), ("0.5 - 0.8x", "a bit soft")),
    (1.5, _srgb("#3cb44b"), ("0.8 - 1.5x", "right")),
    (3.0, _srgb("#5fa8e8"), ("1.5 - 3x", "more than needed")),
    (float("inf"), _srgb("#3343b8"), ("over 3x", "wasted memory")),
]
# Required: one ordered ramp (viridis), dark = little detail needed, bright = a lot.
REQ_BANDS = [  # (upper bound px/m, colour, legend label)
    (64, _srgb("#440154"), ("under 64", "")),
    (128, _srgb("#46327e"), ("64 - 128", "")),
    (256, _srgb("#365c8d"), ("128 - 256", "")),
    (512, _srgb("#277f8e"), ("256 - 512", "")),
    (1024, _srgb("#1fa187"), ("512 - 1k", "")),
    (2048, _srgb("#4ac16d"), ("1k - 2k", "")),
    (4096, _srgb("#a0da39"), ("2k - 4k", "")),
    (float("inf"), _srgb("#fde725"), ("over 4k", "")),
]
UNSEEN = _srgb("#4a4a4a")
NO_TEX = _srgb("#ff2bd6")
LEGENDS = {  # view -> (title, explanation, [(colour, (range, meaning))])
    "TC_Ratio": ("Texture detail vs camera need",
                 "texels per screen pixel with today's UVs",
                 [(c, l) for _, c, l in RATIO_BANDS] + [(NO_TEX, ("no texture", "")), (UNSEEN, ("never seen", ""))]),
    "TC_Required": ("Camera need, px per meter",
                    "1 m of this surface needs this many texels",
                    [(c, l) for _, c, l in reversed(REQ_BANDS)] + [(UNSEEN, ("never seen", ""))]),
}
CACHE_KEY = "tc_cache"
CULL_ATTR = "tc_keep"
CULL_MOD = "TC Cull Unseen"
IMPORTER_HELPERS = {"glTF_not_exported"}  # bone display shapes the glTF importer creates


# ----------------------------------------------------------------------------- helpers

def pow2_ceil(x):
    if not x or not math.isfinite(x) or x <= 0:
        return 0
    return int(2 ** math.ceil(math.log2(max(x, 1))))


def fmt(n):
    return ">8k" if n > 8192 else str(n)


def wquantile(v, w, q):
    v = np.asarray(v, np.float64)
    w = np.asarray(w, np.float64)
    m = np.isfinite(v) & (w > 0)
    if not m.any():
        return float("nan")
    v, w = v[m], w[m]
    o = np.argsort(v, kind="stable")
    c = np.cumsum(w[o])
    i = min(int(np.searchsorted(c, q * c[-1])), len(v) - 1)
    return float(v[o][i])


def tri_area(a, b, c):
    cr = np.cross(b - a, c - a)
    return 0.5 * (np.linalg.norm(cr, axis=1) if cr.ndim == 2 else np.abs(cr))


def uv_face_selected(face, uvl):
    """UV-editor selection of a BMFace (Blender 5.x stores it on the face, older on each loop)."""
    if hasattr(face, "uv_select"):
        return face.uv_select
    return uvl is not None and all(l[uvl].select for l in face.loops)


def write_text(name, body):
    t = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    t.clear()
    t.write(body)
    return t


def action_fcurves(action):
    """F-curves of an action, for both layered (4.4+) and legacy actions."""
    if action is None:
        return
    layers = getattr(action, "layers", None)
    if layers:
        for layer in layers:
            for strip in layer.strips:
                for bag in getattr(strip, "channelbags", []):
                    yield from bag.fcurves
    elif hasattr(action, "fcurves"):
        yield from action.fcurves


def object_mode(context):
    """Leave Edit (or any other) mode: mesh face data is only readable and writable in Object Mode.
    Returns True if the mode changed."""
    if context.mode == "OBJECT" or context.active_object is None:
        return False
    bpy.ops.object.mode_set(mode="OBJECT")
    return True


def frames_of(scene, props):
    if props.use_frames:
        return list(range(scene.frame_start, scene.frame_end + 1, max(1, props.frame_step)))
    return [scene.frame_current]


# ----------------------------------------------------------------------------- materials / images

def uv_of_image_node(node):
    """UV map wired into an Image Texture node through a UV Map node (None = default render UV)."""
    n, depth = node, 0
    while n and n.inputs and n.inputs[0].links and depth < 4:
        n = n.inputs[0].links[0].from_node
        if n.type == "UVMAP":
            return n.uv_map or None
        depth += 1
    return None


def coord_scale(node):
    """How many times the texture repeats per UV unit along its coarsest axis (1 = plain UVs).

    Follows the Vector input through reroutes and Mapping nodes. None when the chain holds
    anything else (object/generated coordinates, math, linked scales): it can't be measured,
    so the export leaves that image at full size."""
    scale, sock, depth = 1.0, node.inputs.get("Vector"), 0
    while sock is not None and sock.is_linked and depth < 8:
        link = sock.links[0]
        n = link.from_node
        if n.type == "UVMAP" or (n.type == "TEX_COORD" and link.from_socket.name == "UV"):
            return scale
        if n.type == "REROUTE":
            sock = n.inputs[0]
        elif n.type == "MAPPING" and n.vector_type in {"POINT", "TEXTURE"}:
            sc = n.inputs.get("Scale")
            if sc is None or sc.is_linked:
                return None
            sx, sy = abs(sc.default_value[0]), abs(sc.default_value[1])
            scale *= min(sx, sy) if n.vector_type == "POINT" else 1 / max(sx, sy, 1e-9)
            sock = n.inputs.get("Vector")
        else:
            return None
        depth += 1
    return scale


def image_nodes(tree, seen=None):
    """Every Image Texture node in a node tree, recursing into node groups."""
    seen = seen if seen is not None else set()
    if tree is None or tree.name in seen:
        return
    seen.add(tree.name)
    for n in tree.nodes:
        if n.type == "TEX_IMAGE" and n.image:
            yield n
        elif n.type == "GROUP" and n.node_tree:
            yield from image_nodes(n.node_tree, seen)


def tex_info(mat):
    """(texture size, uv map name or None, image name) of the material's main image.

    Prefers the image wired into Base Color, then the largest. Material custom property
    `tc_tex_size` overrides the size (for images missing on this machine).
    """
    if mat is None:
        return None
    best, best_rank = None, None
    if mat.use_nodes and mat.node_tree:
        for n in image_nodes(mat.node_tree):
            size = max(n.image.size)
            base = any(l.to_socket.name == "Base Color" for o in n.outputs for l in o.links)
            rank = (base, size)
            if best_rank and rank <= best_rank:
                continue
            best, best_rank = (size, uv_of_image_node(n), n.image.name), rank
    override = mat.get("tc_tex_size")
    if override:
        best = (int(override), best[1] if best else None, best[2] if best else mat.name)
    if best and best[0] <= 0:
        return None
    return best


def render_uv(me):
    for uv in me.uv_layers:
        if uv.active_render:
            return uv.name
    return me.uv_layers[0].name if len(me.uv_layers) else None


def face_groups(obj):
    """Per material slot: (tex size, uv layer name, texture key) or None."""
    me = obj.data
    default_uv = render_uv(me)
    out = []
    for slot in range(max(1, len(me.materials))):
        mat = me.materials[slot] if slot < len(me.materials) else None
        info = tex_info(mat)
        if info is None or default_uv is None:
            out.append(None)
            continue
        size, uv, img = info
        uv = uv if uv and uv in me.uv_layers else default_uv
        out.append((size, uv, img))
    return out


# ----------------------------------------------------------------------------- scene / meshes

def renderable(o, collection=None):
    if o.type != "MESH" or not len(o.data.polygons):
        return False
    if any(c.name in IMPORTER_HELPERS for c in o.users_collection):
        return False
    if collection is not None:  # export-collection mode: what ships, regardless of render toggles
        return o.name in collection.all_objects and o.visible_get()
    return o.visible_get() and not o.hide_render


def target_objects(context):
    col = context.scene.texel_coverage.collection
    return [o for o in context.scene.objects if renderable(o, col)]


def cameras_of(context, props):
    scene = context.scene
    if props.camera_mode == "ACTIVE":
        return [scene.camera] if scene.camera else []
    return [o for o in scene.objects if o.type == "CAMERA" and
            (props.camera_mode == "ALL" or o.select_get())]


def camera_model(cam, scene, res_x, res_y):
    """Camera matrix inverse and frustum description at the target resolution."""
    r = scene.render
    old = (r.resolution_x, r.resolution_y, r.resolution_percentage)
    r.resolution_x, r.resolution_y, r.resolution_percentage = res_x, res_y, 100
    frame = cam.data.view_frame(scene=scene)
    r.resolution_x, r.resolution_y, r.resolution_percentage = old
    xs = [v.x for v in frame]
    ys = [v.y for v in frame]
    loc, rot, _ = cam.matrix_world.decompose()
    m = (Matrix.Translation(loc) @ rot.to_matrix().to_4x4()).inverted()
    return dict(
        inv=np.array(m), loc=loc, ortho=cam.data.type == "ORTHO",
        d=-frame[0].z, xmin=min(xs), xmax=max(xs), ymin=min(ys), ymax=max(ys),
        px_per_unit=res_x / (max(xs) - min(xs)),
        near=cam.data.clip_start, far=cam.data.clip_end,
    )


def read_mesh(obj, depsgraph):
    oe = obj.evaluated_get(depsgraph)
    me = oe.to_mesh()
    try:
        nv, npoly, nl = len(me.vertices), len(me.polygons), len(me.loops)
        co = np.empty(nv * 3, np.float64)
        me.vertices.foreach_get("co", co)
        M = np.array(oe.matrix_world)
        W = co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]
        ls = np.empty(npoly, np.int32)
        lt = np.empty(npoly, np.int32)
        me.polygons.foreach_get("loop_start", ls)
        me.polygons.foreach_get("loop_total", lt)
        lv = np.empty(nl, np.int32)
        me.loops.foreach_get("vertex_index", lv)
        mi = np.empty(npoly, np.int32)
        me.polygons.foreach_get("material_index", mi)
        nt = len(me.loop_triangles)
        tl = np.empty(nt * 3, np.int32)
        tp = np.empty(nt, np.int32)
        me.loop_triangles.foreach_get("loops", tl)
        me.loop_triangles.foreach_get("polygon_index", tp)
        uvs = {}
        for uvl in me.uv_layers:
            a = np.empty(nl * 2, np.float64)
            uvl.data.foreach_get("uv", a)
            uvs[uvl.name] = a.reshape(-1, 2)
    finally:
        oe.to_mesh_clear()
    centers = np.add.reduceat(W[lv], ls) / lt[:, None] if npoly else np.zeros((0, 3))
    return dict(W=W, ls=ls, lt=lt, lv=lv, mi=mi, tl=tl.reshape(-1, 3), tp=tp, uvs=uvs,
                centers=centers, npoly=npoly)


def face_attr(me, name, dtype=np.float32, default=0):
    a = me.attributes.get(name)
    out = np.full(len(me.polygons), default, dtype)
    if a is not None and a.domain == "FACE" and len(a.data) == len(out):
        key = "value"
        a.data.foreach_get(key, out)
    return out


def uv_density(geo, uvname):
    """Per face: world area, uv area and sqrt(uv/world) (texels per metre per texel of size)."""
    P = geo["npoly"]
    W, tl, tp, lv = geo["W"], geo["tl"], geo["tp"], geo["lv"]
    wA = np.bincount(tp, tri_area(W[lv[tl[:, 0]]], W[lv[tl[:, 1]]], W[lv[tl[:, 2]]]), P)
    UV = geo["uvs"].get(uvname)
    if UV is None:
        return wA, np.zeros(P), np.zeros(P)
    uvA = np.bincount(tp, tri_area(UV[tl[:, 0]], UV[tl[:, 1]], UV[tl[:, 2]]), P)
    with np.errstate(divide="ignore", invalid="ignore"):
        unit = np.sqrt(uvA / wA)
    unit[~np.isfinite(unit)] = 0
    return wA, uvA, unit


def current_density(obj, geo):
    """texels/m per face for each material's main texture."""
    groups = face_groups(obj)
    P = geo["npoly"]
    wA = uvA = unit = None
    out_unit, out_uvA = np.zeros(P), np.zeros(P)
    size = np.zeros(P)
    key = np.full(P, "", dtype=object)
    mi = np.clip(geo["mi"], 0, len(groups) - 1)
    cache = {}
    for slot, g in enumerate(groups):
        if g is None:
            continue
        fsel = mi == slot
        if not fsel.any():
            continue
        if g[1] not in cache:
            cache[g[1]] = uv_density(geo, g[1])
        wA, uvA, unit = cache[g[1]]
        out_unit[fsel], out_uvA[fsel] = unit[fsel], uvA[fsel]
        size[fsel] = g[0]
        key[fsel] = g[2]
    if wA is None:
        wA = uv_density(geo, None)[0]
    return dict(cur=out_unit * size, unit=out_unit, wA=wA, uvA=out_uvA, size=size, key=key)


# ----------------------------------------------------------------------------- cameras

def camera_settings(cam, props):
    """Per-camera overrides from custom properties (tc_active can be animated).

    tc_res        (w, h) output pixels for this camera, overrides the panel resolution
    tc_hide       comma-separated object names this camera never renders
    tc_active     0 skips this camera on the current frame
    tc_labels     JSON {frame: label} used in the report's "driven by" lines
    """
    res = cam.get("tc_res")
    res = (int(res[0]), int(res[1])) if res is not None else (props.res_x, props.res_y)
    hide = frozenset(n.strip() for n in str(cam.get("tc_hide", "")).split(",") if n.strip())
    return dict(res=res, hide=hide, active=float(cam.get("tc_active", 1.0)) > 0.5)


def sample_label(cam, frame):
    try:
        labels = json.loads(cam.get("tc_labels", "{}"))
    except ValueError:
        labels = {}
    return labels.get(str(frame), f"{cam.name} @ frame {frame}")


def framed_camera(cam, scene, cs):
    """camera_model at the camera's own resolution."""
    return camera_model(cam, scene, *cs["res"])


def build_bvh(geos_all, names):
    verts, tris, owner, off = [], [], [], 0
    for name in names:
        g = geos_all[name]
        verts.append(g["W"])
        tris.append(g["lv"][g["tl"]] + off)
        owner.append((name, g["tp"]))
        off += len(g["W"])
    bvh = BVHTree.FromPolygons(np.concatenate(verts).tolist(), np.concatenate(tris).tolist(),
                               all_triangles=True)
    tri_obj = np.concatenate([np.full(len(tp), i) for i, (_, tp) in enumerate(owner)])
    tri_poly = np.concatenate([tp for _, tp in owner])
    return bvh, tri_obj, tri_poly, {name: i for i, (name, _) in enumerate(owner)}


def face_visibility(g, cm, bvh_pack, name):
    """Faces in the frustum and not occluded (centre, then corners). Returns vis, depth, projection."""
    R, t = cm["inv"][:3, :3], cm["inv"][:3, 3]
    origin = cm["loc"]
    pv = g["W"] @ R.T + t
    pc = g["centers"] @ R.T + t
    dv, dc = -pv[:, 2], -pc[:, 2]

    def proj(p, d):
        if cm["ortho"]:
            return p[:, 0], p[:, 1]
        dd = np.maximum(d, 1e-9)
        return p[:, 0] / dd * cm["d"], p[:, 1] / dd * cm["d"]

    def inside(p, d):
        x, y = proj(p, d)
        return ((d > cm["near"]) & (d < cm["far"]) & (x >= cm["xmin"]) & (x <= cm["xmax"])
                & (y >= cm["ymin"]) & (y <= cm["ymax"]))

    iv, ic = inside(pv, dv), inside(pc, dc)
    lv, ls = g["lv"], g["ls"]
    any_v = np.maximum.reduceat(iv[lv].astype(np.int8), ls) > 0 if g["npoly"] else ic
    cand = ic | any_v
    dmin = np.minimum.reduceat(np.where(iv, dv, np.inf)[lv], ls) if g["npoly"] else dc
    dmin = np.maximum(np.where(np.isfinite(dmin), dmin, dc), cm["near"])
    if g["npoly"]:  # big faces that cross the frame with no corner or centre inside it (floors, walls)
        x, y = proj(pv, dv)
        front = np.minimum.reduceat((dv > cm["near"])[lv].astype(np.int8), ls) > 0
        red = lambda f, v: f.reduceat(v[lv], ls)
        span = (front & (red(np.minimum, x) <= cm["xmax"]) & (red(np.maximum, x) >= cm["xmin"])
                & (red(np.minimum, y) <= cm["ymax"]) & (red(np.maximum, y) >= cm["ymin"])
                & (red(np.minimum, dv) < cm["far"]))
        extra = span & ~cand
        cand = cand | extra
        dmin[extra] = np.maximum(red(np.minimum, dv)[extra], cm["near"])  # nearest corner: errs on more detail
    vis = cand.copy()
    if bvh_pack is not None:
        bvh, tri_obj, tri_poly, occ_index = bvh_pack
        oi = occ_index.get(name, -1)

        def sees(i, p):
            dvec = Vector(p) - origin
            dist = dvec.length
            if dist < 1e-9:
                return True
            hit = bvh.ray_cast(origin, dvec / dist, dist)
            if hit[0] is None:
                return True
            ti = hit[2]
            if tri_obj[ti] == oi and tri_poly[ti] == i:
                return True
            return hit[3] >= dist - max(1e-3, 0.002 * dist)

        W_, C_ = g["W"], g["centers"]
        for i in np.nonzero(cand)[0]:
            if sees(i, C_[i]):
                continue
            s, n = ls[i], g["lt"][i]  # centre hidden: try corners pulled 10% toward the centre
            corners = W_[lv[s:s + n]]
            if n > 8:
                corners = corners[:: max(1, n // 8)]
            if not any(sees(i, c * 0.9 + C_[i] * 0.1) for c in corners):
                vis[i] = False
    return vis, dmin, pv, dv, proj


class cull_paused:
    """Measure whole meshes: switch the cull modifiers off while analysing, restore afterwards."""

    def __init__(self, context):
        self.mods = [m for o in context.scene.objects if o.type == "MESH"
                     for m in o.modifiers if m.name == CULL_MOD]

    def __enter__(self):
        self.state = [(m, m.show_viewport, m.show_render) for m in self.mods]
        for m in self.mods:
            m.show_viewport = m.show_render = False
        return self

    def __exit__(self, *exc):
        for m, v, r in self.state:
            m.show_viewport, m.show_render = v, r


# ----------------------------------------------------------------------------- fingerprints / cache

ANALYSIS_PROPS = ("res_x", "res_y", "camera_mode", "use_frames", "frame_step", "occlusion", "time_quantile")


def _hash_floats(h, values):
    h.update(np.asarray(values, np.float32).tobytes())


def _hash_anim(h, idblock):
    ad = getattr(idblock, "animation_data", None)
    if ad is None or ad.action is None:
        return
    for fc in action_fcurves(ad.action):
        h.update(f"{fc.data_path}[{fc.array_index}]".encode())
        co = np.empty(len(fc.keyframe_points) * 2, np.float32)
        fc.keyframe_points.foreach_get("co", co)
        h.update(co.tobytes())
        h.update("".join(k.interpolation[0] for k in fc.keyframe_points).encode())


def _hash_props(h, idblock, prefix="tc_"):
    for k in sorted(idblock.keys()):
        if k.startswith(prefix) and k not in (CACHE_KEY, "tc_export_state"):
            v = idblock[k]
            h.update(f"{k}={v.to_list() if hasattr(v, 'to_list') else v}".encode())


def _hash_modifiers(h, o):
    for m in o.modifiers:
        if m.name == CULL_MOD:
            continue
        h.update(f"{m.name}:{m.type}:{m.show_render}:{m.show_viewport}".encode())
        for p in m.bl_rna.properties:
            if p.type in {"BOOLEAN", "INT", "FLOAT", "ENUM", "STRING"} and not p.is_readonly:
                try:
                    v = getattr(m, p.identifier)
                    h.update(f"{p.identifier}={v if not hasattr(v, '__len__') or isinstance(v, str) else tuple(v)}".encode())
                except Exception:
                    pass


def geometry_fingerprint(context, props):
    """Everything visibility depends on: meshes, modifiers, transforms, animation, cameras, settings."""
    h = hashlib.blake2b(digest_size=12)
    scene = context.scene
    h.update(f"{scene.frame_start}-{scene.frame_end}".encode())
    h.update((props.collection.name if props.collection else "").encode())
    h.update(("cams:" + ",".join(sorted(c.name for c in cameras_of(context, props)))).encode())  # which ones count
    for k in ANALYSIS_PROPS:
        h.update(f"{k}={getattr(props, k)}".encode())
    for o in sorted(scene.objects, key=lambda o: o.name):
        if o.type == "MESH" and renderable(o, props.collection):
            me = o.data
            h.update(f"M{o.name}:{len(me.vertices)}:{len(me.polygons)}".encode())
            co = np.empty(len(me.vertices) * 3, np.float32)
            me.vertices.foreach_get("co", co)
            h.update(np.round(co, 5).tobytes())
            lt = np.empty(len(me.polygons), np.int32)
            me.polygons.foreach_get("loop_total", lt)
            h.update(lt.tobytes())
            _hash_floats(h, [v for row in o.matrix_world for v in row])
            _hash_modifiers(h, o)
            _hash_anim(h, o)
            if o.parent:
                h.update(f"P{o.parent.name}".encode())
                _hash_anim(h, o.parent)
        elif o.type == "CAMERA":
            c = o.data
            h.update(f"C{o.name}:{c.type}:{c.lens}:{c.sensor_width}:{c.sensor_height}:{c.sensor_fit}:"
                     f"{c.shift_x}:{c.shift_y}:{c.clip_start}:{c.clip_end}:{c.ortho_scale}".encode())
            _hash_floats(h, [v for row in o.matrix_world for v in row])
            _hash_anim(h, o)
            _hash_anim(h, c)
            _hash_props(h, o)
    return h.hexdigest()


def cull_fingerprint(context, props, base=None):
    h = hashlib.blake2b(digest_size=12)
    h.update((base or geometry_fingerprint(context, props)).encode())
    h.update(json.dumps(cull_params(props), sort_keys=True).encode())
    h.update(props.cull_skip.encode())
    for o in sorted(target_objects(context), key=lambda o: o.name):
        for k in ("tc_no_cull", "tc_cull_shift", "tc_cull_margin"):
            if k in o:
                h.update(f"{o.name}.{k}={o[k]}".encode())
    return h.hexdigest()


def load_cache(scene):
    try:
        return json.loads(scene.get(CACHE_KEY, "{}"))
    except ValueError:
        return {}


def save_cache(scene, cache):
    scene[CACHE_KEY] = json.dumps(cache)


_STATUS = {"dirty": True, "t": 0.0, "value": None}


@bpy.app.handlers.persistent
def mark_dirty(*_):
    _STATUS["dirty"] = True


def cache_status(context, props, force=False):
    """{'analysis': 'fresh'|'stale'|'none', 'cull': ...}. Recomputed at most once a second."""
    now = time.time()
    if not force and _STATUS["value"] is not None and (not _STATUS["dirty"] or now - _STATUS["t"] < 1.0):
        return _STATUS["value"]
    cache = load_cache(context.scene)
    geo = geometry_fingerprint(context, props)
    out = {}
    a = cache.get("analysis")
    out["analysis"] = "none" if not a else ("fresh" if a.get("fp") == geo else "stale")
    c = cache.get("cull")
    out["cull"] = "none" if not c else ("fresh" if c.get("fp") == cull_fingerprint(context, props, geo) else "stale")
    _STATUS.update(dirty=False, t=now, value=out)
    return out


# ----------------------------------------------------------------------------- analysis

def analyze(context, props):
    with cull_paused(context):
        out = _analyze(context, props)
    cache = load_cache(context.scene)
    results, cams, frames, labels = out
    cache["analysis"] = dict(fp=geometry_fingerprint(context, props), time=time.strftime("%Y-%m-%d %H:%M"),
                             labels=labels, cams=cams, frames=[frames[0], frames[-1], len(frames)],
                             objects=sorted(results))
    save_cache(context.scene, cache)
    mark_dirty()
    return out


def _analyze(context, props):
    scene = context.scene
    depsgraph = context.evaluated_depsgraph_get()
    objs = target_objects(context)
    occ = objs
    cams = cameras_of(context, props)
    if not cams:
        raise RuntimeError("No cameras to analyze")
    frames = frames_of(scene, props)
    frame0 = scene.frame_current

    keep_samples = props.time_quantile < 1.0
    req = {o.name: np.zeros(len(o.data.polygons)) for o in objs}
    drv = {o.name: np.full(len(o.data.polygons), -1, np.int32) for o in objs}
    per_sample = {o.name: [] for o in objs}
    scr = {o.name: np.zeros(len(o.data.polygons)) for o in objs}
    cam_px = {o.name: {c.name: 0.0 for c in cams} for o in objs}
    labels = []

    for f in frames:
        if f != scene.frame_current:
            scene.frame_set(f)
            depsgraph = context.evaluated_depsgraph_get()
        geos = {o.name: read_mesh(o, depsgraph) for o in occ}
        bad = [n for n, g in geos.items() if g["npoly"] != len(req[n])]
        if bad:
            raise RuntimeError(f"Modifiers change the face count of {', '.join(bad[:4])}; "
                               "apply them or move them after export")
        bvhs = {}
        for cam in cams:
            cs = camera_settings(cam, props)
            if not cs["active"]:
                continue
            cm = framed_camera(cam, scene, cs)
            sample = len(labels)
            labels.append(sample_label(cam, f))
            if props.occlusion and cs["hide"] not in bvhs:
                names = [o.name for o in occ if o.name not in cs["hide"]]
                bvhs[cs["hide"]] = build_bvh(geos, names) if names else None
            bvh_pack = bvhs.get(cs["hide"]) if props.occlusion else None
            for o in objs:
                if o.name in cs["hide"]:
                    if keep_samples:
                        per_sample[o.name].append(None)
                    continue
                g = geos[o.name]
                vis, dmin, pv, dv, proj = face_visibility(g, cm, bvh_pack, o.name)
                lv = g["lv"]
                dens = np.full(g["npoly"], cm["px_per_unit"]) if cm["ortho"] else cm["px_per_unit"] * cm["d"] / dmin
                r = req[o.name]
                up = vis & (dens > r)
                r[up] = dens[up]
                drv[o.name][up] = sample
                if keep_samples:
                    per_sample[o.name].append(np.where(vis, dens, np.nan).astype(np.float32))
                x, y = proj(pv, dv)  # screen pixel coverage, clamped to the frame
                x = np.clip(x, cm["xmin"], cm["xmax"]) * cm["px_per_unit"]
                y = np.clip(y, cm["ymin"], cm["ymax"]) * cm["px_per_unit"]
                tv = lv[g["tl"]]
                ok = (dv[tv] > cm["near"]).all(axis=1)
                a = np.stack([x, y], 1)
                ta = np.where(ok, tri_area(a[tv[:, 0]], a[tv[:, 1]], a[tv[:, 2]]), 0)
                fpx = np.bincount(g["tp"], ta, g["npoly"]) * vis
                scr[o.name] = np.maximum(scr[o.name], fpx)
                cam_px[o.name][cam.name] = max(cam_px[o.name][cam.name], float(fpx.sum()))

    if scene.frame_current != frame0:
        scene.frame_set(frame0)
        depsgraph = context.evaluated_depsgraph_get()

    results, attrs = {}, {}
    for o in objs:
        geo = read_mesh(o, depsgraph)
        cd = current_density(o, geo)
        need = req[o.name]
        stack = [s for s in per_sample[o.name] if s is not None]
        if keep_samples and stack:
            import warnings
            with np.errstate(all="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore")
                need = np.nan_to_num(np.nanquantile(np.stack(stack), props.time_quantile, axis=0))
        r = need * props.target_ratio
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where((r > 0) & (cd["cur"] > 0), cd["cur"] / r, 0)
        attrs.setdefault(o.data.name, []).append((o, need, cd["cur"], ratio, scr[o.name], cd["size"], drv[o.name]))
        results[o.name] = dict(req=r, scr=scr[o.name], cam_px=cam_px[o.name], drv=drv[o.name], **cd)
    for users in attrs.values():
        write_attributes(*_worst_user(users))
    return results, [c.name for c in cams], frames, labels


def _worst_user(users):
    """Linked duplicates share one mesh: per face, store what the most demanding object needs
    (highest need, its driver; lowest ratio) so textures and UVs cover every copy."""
    if len(users) == 1:
        return users[0]
    o = users[0][0]
    need = np.stack([u[1] for u in users])
    pick = need.argmax(axis=0)
    cols = np.arange(need.shape[1])
    ratio = np.stack([np.where(u[1] > 0, u[3], np.inf) for u in users]).min(axis=0)
    ratio[np.isinf(ratio)] = 0
    take = lambda i: np.stack([u[i] for u in users])[pick, cols]
    return o, need.max(axis=0), take(2), ratio, np.stack([u[4] for u in users]).max(axis=0), users[0][5], take(6)


# ----------------------------------------------------------------------------- attributes / viz

def set_attr(me, name, values, data_type="FLOAT", domain="FACE"):
    a = me.attributes.get(name)
    if a and (a.domain != domain or a.data_type != data_type):
        me.attributes.remove(a)
        a = None
    if a is None:
        a = me.attributes.new(name, data_type, domain)
    dt = {"FLOAT": np.float32, "INT": np.int32, "BOOLEAN": bool}[data_type]
    a.data.foreach_set("value", np.asarray(values, dt))


def set_face_colors(me, name, face_rgb):
    a = me.color_attributes.get(name)
    if a is None:
        a = me.color_attributes.new(name, "BYTE_COLOR", "CORNER")
    lt = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_total", lt)
    rgba = np.ones((len(me.polygons), 4), np.float32)
    rgba[:, :3] = face_rgb
    a.data.foreach_set("color", np.repeat(rgba, lt, axis=0).ravel())


def band_colors(values, bands, mask_unseen, mask_notex=None):
    edges = np.array([b[0] for b in bands[:-1]])
    cols = np.array([b[1] for b in bands], np.float32)
    rgb = cols[np.searchsorted(edges, values, side="right")]
    if mask_notex is not None:
        rgb[mask_notex] = NO_TEX
    rgb[mask_unseen] = UNSEEN
    return rgb


def write_attributes(obj, req, cur, ratio, scr, size, drv):
    me = obj.data
    set_attr(me, "tc_required", req)
    set_attr(me, "tc_current", cur)
    set_attr(me, "tc_ratio", ratio)
    set_attr(me, "tc_screen_px", scr)
    set_attr(me, "tc_driver", drv, "INT")
    unseen = req <= 0
    set_face_colors(me, "TC_Required", band_colors(req, REQ_BANDS, unseen))
    set_face_colors(me, "TC_Ratio", band_colors(ratio, RATIO_BANDS, unseen, size <= 0))
    me.update()


def refresh_ratio(obj, props):
    """Recolour TC_Ratio from the stored need and the current UVs (after UV edits)."""
    with cull_paused(bpy.context):
        geo = read_mesh(obj, bpy.context.evaluated_depsgraph_get())
    me = obj.data
    if geo["npoly"] != len(me.polygons) or "tc_required" not in me.attributes:
        return
    need = face_attr(me, "tc_required")
    cd = current_density(obj, geo)
    r = need * props.target_ratio
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where((r > 0) & (cd["cur"] > 0), cd["cur"] / r, 0)
    set_attr(me, "tc_current", cd["cur"])
    set_attr(me, "tc_ratio", ratio)
    set_face_colors(me, "TC_Ratio", band_colors(ratio, RATIO_BANDS, r <= 0, cd["size"] <= 0))
    me.update()


def recolor(obj):
    """Repaint both views from the stored attributes, so files analyzed with older colours match the legend."""
    me = obj.data
    if "tc_required" not in me.attributes or "tc_ratio" not in me.attributes:
        return
    req, ratio = face_attr(me, "tc_required"), face_attr(me, "tc_ratio")
    unseen = req <= 0
    set_face_colors(me, "TC_Required", band_colors(req, REQ_BANDS, unseen))
    set_face_colors(me, "TC_Ratio", band_colors(ratio, RATIO_BANDS, unseen, ratio <= 0))
    me.update()


VIEW_KEY = "tc_view_restore"  # shading and active colours from before a Texel view, to turn it off


def view_shown(context):
    """The Texel view a 3D viewport shows right now (TC_Ratio / TC_Required), or None."""
    if not context.screen:
        return None
    for area in context.screen.areas:
        if area.type != "VIEW_3D":
            continue
        sh = area.spaces.active.shading
        if sh.type == "SOLID" and sh.color_type in {"ATTRIBUTE", "VERTEX"}:
            o = next((o for o in context.scene.objects
                      if o.type == "MESH" and o.data.color_attributes.get("TC_Ratio")), None)
            act = o.data.color_attributes.active_color if o else None
            if act is not None and act.name in LEGENDS:
                return act.name
    return None


def show_attribute(context, name):
    scene = context.scene
    if view_shown(context) is None:  # entering a Texel view: remember how to leave it
        saved = dict(shading=[], colors={})
        for o in scene.objects:
            if o.type == "MESH" and o.data.color_attributes.active_color:
                saved["colors"][o.data.name] = o.data.color_attributes.active_color.name
        for area in context.screen.areas if context.screen else []:
            if area.type == "VIEW_3D":
                sh = area.spaces.active.shading
                saved["shading"].append([sh.type, sh.color_type, sh.light])
        scene[VIEW_KEY] = saved
    for o in scene.objects:
        if o.type == "MESH" and name in o.data.color_attributes:
            o.data.color_attributes.active_color = o.data.color_attributes[name]
    for area in context.screen.areas if context.screen else []:
        if area.type == "VIEW_3D":
            sh = area.spaces.active.shading
            sh.type = "SOLID"
            items = {i.identifier for i in sh.bl_rna.properties["color_type"].enum_items}
            sh.color_type = "ATTRIBUTE" if "ATTRIBUTE" in items else "VERTEX"
            sh.light = "FLAT"


def hide_attribute(context):
    """Leave the Texel view: shading and active colour attributes go back to what they were."""
    scene = context.scene
    saved = scene.get(VIEW_KEY)
    saved = saved.to_dict() if hasattr(saved, "to_dict") else {}
    colors = saved.get("colors", {})
    for o in scene.objects:
        if o.type != "MESH":
            continue
        ca = o.data.color_attributes
        prev = colors.get(o.data.name)
        if prev and prev in ca:
            ca.active_color = ca[prev]
        elif ca.active_color and ca.active_color.name in LEGENDS:
            other = next((a for a in ca if a.name not in LEGENDS), None)
            if other is not None:
                ca.active_color = other
    shading = list(saved.get("shading", []))
    for area in context.screen.areas if context.screen else []:
        if area.type == "VIEW_3D":
            sh = area.spaces.active.shading
            t, c, l = shading.pop(0) if shading else ("SOLID", "MATERIAL", "STUDIO")
            sh.type, sh.color_type, sh.light = t, c, l
    if VIEW_KEY in scene:
        del scene[VIEW_KEY]


# ----------------------------------------------------------------------------- report

def build_report(results, cams, frames, props, labels=()):
    q = props.quantile
    groups = {}
    for oname, r in results.items():
        for k in set(r["key"].tolist()) - {""}:
            groups.setdefault(k, []).append((oname, r, r["key"] == k))
    lines = ["TEXEL COVERAGE REPORT"]
    tq = "worst moment" if props.time_quantile >= 1 else f"P{round(props.time_quantile * 100)} of on-screen moments"
    lines.append(f"cameras: {', '.join(cams)} | frames: {frames[0]}-{frames[-1]} ({len(frames)}) | "
                 f"{len(labels)} camera samples | target {props.target_ratio:g} texel/px | "
                 f"per face: {tq} | per texture: P{round(q * 100)} weighted by screen pixels")
    lines.append("")
    lines.append("Per texture (an image can be shared by several objects):")
    hdr = (f"{'texture':42} {'now':>5} {'curUV':>6} {'idealUV':>7} {'scrMpx':>7} {'unseenUV%':>9} "
           f"{'need px/m P50/P' + str(round(q * 100)):>18}  verdict")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    summary = []
    for k, items in sorted(groups.items()):
        req = np.concatenate([r["req"][m] for _, r, m in items])
        unit = np.concatenate([r["unit"][m] for _, r, m in items])
        wA = np.concatenate([r["wA"][m] for _, r, m in items])
        uvA = np.concatenate([r["uvA"][m] for _, r, m in items])
        scr = np.concatenate([r["scr"][m] for _, r, m in items])
        size = int(np.max(np.concatenate([r["size"][m] for _, r, m in items])))
        seen = req > 0
        with np.errstate(divide="ignore", invalid="ignore"):
            need_res = np.where(seen & (unit > 0), req / unit, np.nan)
        cur_uv = wquantile(need_res, scr, q)
        reqc = np.minimum(req, wquantile(req, scr, 0.99)) if seen.any() else req
        ideal = math.sqrt(float((wA[seen] * reqc[seen] ** 2).sum()) / 0.7) if seen.any() else 0
        unseen_uv = 100 * float(uvA[~seen].sum() / uvA.sum()) if uvA.sum() > 0 else 0
        screen_mpx = float(scr.sum()) / 1e6
        p50, pq = wquantile(req, scr, 0.5), wquantile(req, scr, q)
        rc, ri = min(pow2_ceil(cur_uv), 16384), min(pow2_ceil(ideal), 16384)
        if not seen.any():
            verdict = "never seen -> smallest possible / remove"
        elif rc > size:
            verdict = f"UPSCALE to {fmt(rc)}" + (f" or fix UVs ({fmt(ri)})" if ri < rc else "")
        elif rc < size:
            verdict = f"downsize to {rc}"
        else:
            verdict = "ok"
        if seen.any() and ri < rc and "UPSCALE" not in verdict:
            verdict += f" | UVs waste space: {ri} possible"
        objs = ",".join(sorted({o for o, _, _ in items}))
        lines.append(f"{k[:42]:42} {size:>5} {fmt(rc):>6} {fmt(ri):>7} {screen_mpx:>7.2f} {unseen_uv:>8.1f}% "
                     f"{p50:>8.0f}/{pq:<8.0f}  {verdict}")
        lines.append(f"    objects: {objs}")
        dr = np.concatenate([r["drv"][m] for _, r, m in items])
        drivers = []
        if labels and (dr >= 0).any():
            share = np.bincount(dr[dr >= 0], scr[dr >= 0], len(labels))
            tot = share.sum()
            if tot > 0:
                top = np.argsort(-share, kind="stable")[:2]
                drivers = [dict(moment=labels[i], share=round(100 * float(share[i]) / tot))
                           for i in top if share[i] > 0]
                lines.append("    worst case from: " + " | ".join(
                    f"{d['moment']} ({d['share']}%)" for d in drivers))
        summary.append(dict(texture=k, size=size, need_current_uv=rc, need_ideal_uv=ri,
                            screen_mpx=round(screen_mpx, 3), unseen_uv_pct=round(unseen_uv, 1),
                            need_px_per_m_p50=round(p50) if math.isfinite(p50) else 0,
                            need_px_per_m_pq=round(pq) if math.isfinite(pq) else 0,
                            verdict=verdict, objects=objs.split(","), worst_case_from=drivers))
    lines.append("")
    lines.append("Per object screen coverage (megapixels, max over frames):")
    for oname, r in sorted(results.items(), key=lambda kv: -kv[1]["scr"].sum()):
        px = " | ".join(f"{c}: {v / 1e6:5.2f}" for c, v in r["cam_px"].items() if v > 0)
        lines.append(f"  {oname:24} {px}")
    lines.append("")
    lines.append("Columns: now = current texture size | curUV = size needed with the CURRENT UV layout | "
                 "idealUV = size needed if UV space were allocated by camera need (Suggest UVs) | "
                 "unseenUV = UV space spent on faces no camera sees | scrMpx = screen megapixels the "
                 "texture covers at its biggest | worst case from = which camera moment sets the size")
    return "\n".join(lines), summary


def cull_params(props):
    """Effective cull settings: a preset or the custom sliders."""
    presets = {
        "SAFE": dict(shift=0.2, overscan=1.1, distance=0.05, rings=0),
        "TIGHT": dict(shift=0.05, overscan=1.03, distance=0.02, rings=0),
    }
    p = presets.get(props.cull_preset) or dict(shift=props.cull_shift, overscan=props.cull_overscan,
                                                distance=props.cull_distance, rings=props.cull_rings)
    return dict(p, pixel=bool(props.cull_pixel), pixel_scale=props.cull_pixel_scale)
