"""Live feedback while laying out UVs.

UV editor overlay: every face drawn in its ratio colour for the UV map being edited (so moving or
scaling an island updates the colour), plus a readout for the selected faces.

Target checker: shows a colour grid through the UV map in the viewport (Solid > Texture). When
islands are sized by camera need, every grid cell covers about the same number of screen pixels
from the cameras, so an even-looking grid means an even layout.
"""

import math
import time

import blf
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import core, uv_suggest

_HANDLE = {"uv": None, "view3d": None, "uv_legend": None}
_CACHE = {"key": None, "t": 0.0, "tris": None, "cols": None, "text": []}
CHECKER = "TC Target Checker"
CHECKER_NODE = "TC Checker"


def _tex_sizes(context, obj, uvname):
    """Texture size per material slot for the UV map being edited."""
    sizes = []
    planned = core.suggest_sizes(context.scene)
    for g in core.face_groups(obj):
        if g is None:
            sizes.append(0)
        elif uvname.endswith(uv_suggest.SUFFIX) and planned and g[2] in planned:
            sizes.append(int(planned[g[2]]))
        else:
            sizes.append(g[0])
    return sizes


def compute(context):
    import bmesh
    obj = context.edit_object
    props = context.scene.texel_coverage
    if obj is None or obj.type != "MESH":
        return dict(tris=None, cols=None, text=[])
    bm = bmesh.from_edit_mesh(obj.data)
    uvl = bm.loops.layers.uv.active
    req_l = bm.faces.layers.float.get("tc_required")
    drv_l = bm.faces.layers.int.get("tc_driver")
    if uvl is None or req_l is None:
        return dict(tris=None, cols=None, text=["Texel: run Analyze to see camera need here"])
    uvname = obj.data.uv_layers.active.name
    sizes = _tex_sizes(context, obj, uvname)
    s2 = abs(obj.matrix_world.to_3x3().determinant()) ** (2 / 3)
    sync = context.scene.tool_settings.use_uv_select_sync
    edges = np.array([b[0] for b in core.RATIO_BANDS[:-1]])
    cols = np.array([core.to_srgb(b[1]) for b in core.RATIO_BANDS], np.float32)
    tris, tcol = [], []
    sel_need, sel_cur, sel_w, sel_drv = [], [], [], {}
    for f in bm.faces:
        if f.hide or (not sync and not f.select):
            continue
        uv = [l[uvl].uv for l in f.loops]
        n = len(uv)
        ua = 0.5 * abs(sum(uv[i].x * uv[(i + 1) % n].y - uv[(i + 1) % n].x * uv[i].y for i in range(n)))
        wa = f.calc_area() * s2
        size = sizes[min(f.material_index, len(sizes) - 1)] if sizes else 0
        need = f[req_l] * props.target_ratio
        cur = size * math.sqrt(ua / wa) if wa > 0 else 0.0
        if need <= 0:
            c = core.to_srgb(core.UNSEEN)
        elif size <= 0:
            c = core.to_srgb(core.NO_TEX)
        else:
            c = cols[np.searchsorted(edges, cur / need, side="right")]
        for i in range(1, n - 1):
            tris += [(uv[0].x, uv[0].y), (uv[i].x, uv[i].y), (uv[i + 1].x, uv[i + 1].y)]
            tcol += [(c[0], c[1], c[2], 0.45)] * 3
        picked = core.uv_face_selected(f, uvl) if not sync else f.select
        if picked and need > 0:
            sel_need.append(need)
            sel_cur.append(cur)
            sel_w.append(wa)
            if drv_l is not None:
                d = f[drv_l]
                sel_drv[d] = sel_drv.get(d, 0.0) + wa
    text = []
    if sel_w:
        need = core.wquantile(sel_need, sel_w, 0.98)
        cur = core.wquantile(sel_cur, sel_w, 0.5)
        tier = core.pow2_ceil(need)
        labels = core.load_cache(context.scene).get("analysis", {}).get("labels", [])
        d = max(sel_drv, key=sel_drv.get) if sel_drv else -1
        text = [f"selected: {len(sel_w)} faces seen by the cameras",
                f"need {need:,.0f} px/m (P98)  ->  tier {tier} px/m",
                f"now {cur:,.0f} texels/m  ->  ratio {cur / need:.2f}x" if need > 0 else "",
                f"set by: {labels[d]}" if 0 <= d < len(labels) else ""]
    return dict(tris=np.array(tris, np.float32) if tris else None,
                cols=np.array(tcol, np.float32) if tcol else None, text=[t for t in text if t])


def draw_uv():
    context = bpy.context
    props = context.scene.texel_coverage
    if not props.uv_overlay or context.mode != "EDIT_MESH":
        return
    obj = context.edit_object
    key = (obj.name if obj else "", obj.data.uv_layers.active.name if obj and obj.data.uv_layers.active else "")
    now = time.time()
    if _CACHE["key"] != key or now - _CACHE["t"] > 0.3:
        _CACHE.update(compute(context), key=key, t=now)
    region = context.region
    v2d = region.view2d
    x0, y0 = v2d.view_to_region(0, 0, clip=False)
    x1, y1 = v2d.view_to_region(1, 1, clip=False)
    if _CACHE["tris"] is not None:
        pts = _CACHE["tris"] * np.array([x1 - x0, y1 - y0], np.float32) + np.array([x0, y0], np.float32)
        shader = gpu.shader.from_builtin("SMOOTH_COLOR")
        batch = batch_for_shader(shader, "TRIS", {"pos": pts, "color": _CACHE["cols"]})
        gpu.state.blend_set("ALPHA")
        batch.draw(shader)
        gpu.state.blend_set("NONE")
    if _CACHE["text"]:
        font, sc = 0, context.preferences.system.ui_scale
        blf.size(font, 12 * sc)
        step = round(17 * sc)
        y = 20 + step * (len(_CACHE["text"]) - 1)
        for line in _CACHE["text"]:
            blf.color(font, 0, 0, 0, 0.8)
            blf.position(font, 21, y - 1, 0)
            blf.draw(font, line)
            blf.color(font, 1, 1, 1, 1)
            blf.position(font, 20, y, 0)
            blf.draw(font, line)
            y -= step


# ----------------------------------------------------------------------------- legends

def _shown_view(context):
    """The Texel view the viewport is showing (or None): Solid shading, attribute colour, TC_* active."""
    sh = context.space_data.shading
    if sh.type != "SOLID" or sh.color_type not in {"ATTRIBUTE", "VERTEX"}:
        return None
    obj = context.active_object
    if obj is None or obj.type != "MESH" or not obj.data.color_attributes.get("TC_Ratio"):
        obj = next((o for o in context.visible_objects
                    if o.type == "MESH" and o.data.color_attributes.get("TC_Ratio")), None)
    act = obj.data.color_attributes.active_color if obj else None
    return act.name if act is not None and act.name in core.LEGENDS else None


def _side_width(context, kind):
    """Width of an overlapping side region, so the legend is not drawn under it."""
    if not context.preferences.system.use_region_overlap:
        return 0
    return sum(r.width for r in context.area.regions if r.type == kind and r.width > 1)


def draw_legend(view, x, y, anchor="left"):
    """Swatch legend card with its bottom corner at (x, y), in region pixels."""
    title, note, rows = core.LEGENDS[view]
    s = bpy.context.preferences.system.ui_scale
    font, fs = 0, 11 * s
    blf.size(font, fs)
    line, pad, sw = round(17 * s), round(8 * s), round(12 * s)
    dim = lambda t: blf.dimensions(font, t)[0]
    col2 = pad * 2 + sw + max(dim(r[1][0]) for r in rows) + pad * 1.5
    lines = len(rows) + 2
    w = max(col2 + max(dim(r[1][1]) for r in rows), dim(title), dim(note)) + pad * 2
    h = pad * 2 + line * lines
    if anchor == "right":
        x -= w
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")

    def rect(x0, y0, x1, y1, rgba):
        b = batch_for_shader(shader, "TRIS", {"pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)]})
        shader.uniform_float("color", rgba)
        b.draw(shader)

    rect(x, y, x + w, y + h, (0.09, 0.09, 0.09, 0.94))
    ty = y + h - pad - line + round(4 * s)
    blf.color(font, 1, 1, 1, 1)
    blf.position(font, x + pad, ty, 0)
    blf.draw(font, title)
    for c, (rng, meaning) in rows:
        ty -= line
        rect(x + pad, ty - 2 * s, x + pad + sw, ty - 2 * s + sw, (*core.to_srgb(c), 1.0))  # UI draws in display space
        blf.color(font, 0.92, 0.92, 0.92, 1)
        blf.position(font, x + pad * 2 + sw, ty, 0)
        blf.draw(font, rng)
        blf.color(font, 0.7, 0.7, 0.7, 1)
        blf.position(font, x + col2, ty, 0)
        blf.draw(font, meaning)
    ty -= line
    blf.color(font, 0.65, 0.65, 0.65, 1)
    blf.position(font, x + pad, ty, 0)
    blf.draw(font, note)
    gpu.state.blend_set("NONE")


def draw_view3d():
    context = bpy.context
    props = getattr(context.scene, "texel_coverage", None)
    if props is None or not props.view_legend or not context.space_data.overlay.show_overlays:
        return
    view = _shown_view(context)
    if view:
        draw_legend(view, _side_width(context, "TOOLS") + 12, 12)


def draw_uv_legend():
    context = bpy.context
    props = context.scene.texel_coverage
    if not (props.uv_overlay and props.view_legend and context.mode == "EDIT_MESH" and _CACHE["tris"] is not None):
        return
    draw_legend("TC_Ratio", context.region.width - _side_width(context, "UI") - 12, 12, anchor="right")


def register():
    if _HANDLE["uv"] is None:
        _HANDLE["uv"] = bpy.types.SpaceImageEditor.draw_handler_add(draw_uv, (), "WINDOW", "POST_PIXEL")
        _HANDLE["uv_legend"] = bpy.types.SpaceImageEditor.draw_handler_add(draw_uv_legend, (), "WINDOW", "POST_PIXEL")
        _HANDLE["view3d"] = bpy.types.SpaceView3D.draw_handler_add(draw_view3d, (), "WINDOW", "POST_PIXEL")


def unregister():
    for key, space in (("uv", bpy.types.SpaceImageEditor), ("uv_legend", bpy.types.SpaceImageEditor),
                       ("view3d", bpy.types.SpaceView3D)):
        if _HANDLE[key] is not None:
            space.draw_handler_remove(_HANDLE[key], "WINDOW")
            _HANDLE[key] = None


# ----------------------------------------------------------------------------- target checker

def checker_active(scene):
    return bool(scene.get("tc_checker"))


def checker_on(context, use_suggested=True):
    img = bpy.data.images.get(CHECKER)
    if img is None:
        img = bpy.data.images.new(CHECKER, 1024, 1024)
        img.generated_type = "COLOR_GRID"
    state = {"mats": {}, "uv": {}, "shading": []}
    for o in core.target_objects(context):
        me = o.data
        if me.name not in state["uv"]:
            active = me.uv_layers.active.name if me.uv_layers.active else ""
            want = next((l.name for l in me.uv_layers if l.name.endswith(uv_suggest.SUFFIX)), None) \
                if use_suggested else None
            want = want or core.render_uv(me)
            state["uv"][me.name] = active
            if want:
                me.uv_layers.active = me.uv_layers[want]
        for ms in o.material_slots:
            m = ms.material
            if not m or not m.use_nodes or m.name in state["mats"]:
                continue
            nodes = m.node_tree.nodes
            state["mats"][m.name] = nodes.active.name if nodes.active else ""
            n = nodes.get(CHECKER_NODE) or nodes.new("ShaderNodeTexImage")
            n.name = n.label = CHECKER_NODE
            n.image = img
            n.location = (-1200, 600)
            nodes.active = n
    for area in context.screen.areas if context.screen else []:
        if area.type == "VIEW_3D":
            sh = area.spaces.active.shading
            state["shading"].append([sh.type, sh.color_type, sh.light])
            sh.type, sh.color_type = "SOLID", "TEXTURE"
    import json
    context.scene["tc_checker"] = json.dumps(state)


def checker_off(context):
    import json
    raw = context.scene.get("tc_checker")
    if not raw:
        return
    state = json.loads(raw)
    for mname, prev in state["mats"].items():
        m = bpy.data.materials.get(mname)
        if not (m and m.node_tree):
            continue
        n = m.node_tree.nodes.get(CHECKER_NODE)
        if n:
            m.node_tree.nodes.remove(n)
        if prev and prev in m.node_tree.nodes:
            m.node_tree.nodes.active = m.node_tree.nodes[prev]
    for mename, prev in state["uv"].items():
        me = bpy.data.meshes.get(mename)
        if me and prev in me.uv_layers:
            me.uv_layers.active = me.uv_layers[prev]
    areas = [a for a in (context.screen.areas if context.screen else []) if a.type == "VIEW_3D"]
    for area, (t, c, l) in zip(areas, state["shading"]):
        sh = area.spaces.active.shading
        sh.type, sh.color_type, sh.light = t, c, l
    del context.scene["tc_checker"]
