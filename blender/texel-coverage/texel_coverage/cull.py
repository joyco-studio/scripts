"""Geometry culling: keep faces any camera moment (plus margins) can see.

Results live in a boolean face attribute `tc_keep`; a Geometry Nodes modifier "TC Cull Unseen"
deletes the rest. The modifier is only added for previews and during export, so the .blend
keeps every face.

Per-object custom properties:
    tc_no_cull      never cull this object
    tc_cull_shift   camera shift margin for this object (metres), overrides the preset
    tc_cull_margin  surface margin for this object (metres), overrides the preset
Rigged objects (armature parent or modifier) are always left whole and never hide anything.
"""

import heapq
import os
import tempfile
import time

import bpy
import numpy as np
from mathutils import Vector

from . import core
from .core import CULL_ATTR, CULL_MOD


def margin_cameras(cm, shift, overscan):
    """The sampled camera plus copies nudged along its own axes, each with a wider frame."""
    Rw = cm["inv"][:3, :3].T
    offsets = [(0, 0, 0)]
    if shift > 0:
        offsets += [(a * shift, 0, 0) for a in (-1, 1)] + [(0, a * shift, 0) for a in (-1, 1)] \
            + [(0, 0, a * shift) for a in (-1, 1)]
    out = []
    for off in offsets:
        c = dict(cm)
        loc = np.array(cm["loc"]) + Rw @ np.array(off, float)
        inv = np.eye(4)
        inv[:3, :3] = Rw.T
        inv[:3, 3] = -Rw.T @ loc
        c["inv"], c["loc"] = inv, Vector(loc)
        cx, cy = (cm["xmin"] + cm["xmax"]) / 2, (cm["ymin"] + cm["ymax"]) / 2
        hw, hh = (cm["xmax"] - cm["xmin"]) / 2 * overscan, (cm["ymax"] - cm["ymin"]) / 2 * overscan
        c["xmin"], c["xmax"], c["ymin"], c["ymax"] = cx - hw, cx + hw, cy - hh, cy + hh
        out.append(c)
    return out


def rigged(o):
    return bool((o.parent and o.parent.type == "ARMATURE") or any(m.type == "ARMATURE" for m in o.modifiers))


def skipped(o, names):
    return rigged(o) or o.name in names or bool(o.get("tc_no_cull", False))


def skip_names(props):
    return {n.strip() for n in props.cull_skip.split(",") if n.strip()}


# ----------------------------------------------------------------------------- pixel-exact pass

def pixel_visibility(context, props, objs, frames, cams, movers=(), scale=1.0):
    """Render every camera moment with each face's index as a flat colour (Workbench, no AA,
    Raw view, 32-bit EXR) and collect the faces that land on at least one pixel."""
    scene = context.scene
    sh, r, vs = scene.display.shading, scene.render, scene.view_settings
    saved = dict(engine=r.engine, res=(r.resolution_x, r.resolution_y, r.resolution_percentage),
                 film=r.film_transparent, dither=r.dither_intensity, cam=scene.camera,
                 aa=scene.display.render_aa, vt=vs.view_transform, look=vs.look, exp=vs.exposure,
                 gamma=vs.gamma, shading=(sh.light, sh.color_type, sh.show_cavity, sh.show_object_outline,
                                          sh.show_shadows, sh.show_specular_highlight, sh.show_xray,
                                          sh.show_backface_culling),
                 frame=scene.frame_current, fmt=(r.image_settings.file_format, r.image_settings.color_mode,
                                                 r.image_settings.color_depth))
    seen = {o.name: np.zeros(len(o.data.polygons), bool) for o in objs}
    owners, base, touched = [], 1, []
    for o in scene.objects:
        if o.type != "MESH":
            continue
        me = o.data
        if any(t[0] == me for t in touched):
            continue
        ids = np.zeros(len(me.polygons), np.int64)
        if o.name in seen:
            ids = np.arange(base, base + len(me.polygons))
            owners.append((o.name, base))
            base += len(me.polygons)
        rgb = np.stack([(ids >> 16) & 255, (ids >> 8) & 255, ids & 255], 1) / 255.0
        a = me.color_attributes.get("tc_id") or me.color_attributes.new("tc_id", "FLOAT_COLOR", "CORNER")
        lt = np.empty(len(me.polygons), np.int32)
        me.polygons.foreach_get("loop_total", lt)
        rgba = np.ones((len(me.polygons), 4), np.float32)
        rgba[:, :3] = rgb
        a.data.foreach_set("color", np.repeat(rgba, lt, axis=0).ravel())
        ca = me.color_attributes
        touched.append((me, ca.active_color_name, ca.render_color_index))
        ca.active_color = a
        ca.render_color_index = ca.find("tc_id")
    tmpdir = tempfile.mkdtemp(prefix="texel_")  # private per run: parallel sessions can't clash
    path = os.path.join(tmpdir, "tc_id.exr")
    try:
        try:
            r.engine = "BLENDER_WORKBENCH"
        except TypeError:
            pass
        items = {i.identifier for i in sh.bl_rna.properties["color_type"].enum_items}
        sh.light, sh.color_type = "FLAT", ("ATTRIBUTE" if "ATTRIBUTE" in items else "VERTEX")
        sh.show_cavity = sh.show_object_outline = sh.show_shadows = sh.show_specular_highlight = False
        sh.show_xray = sh.show_backface_culling = False
        scene.display.render_aa = "OFF"
        r.film_transparent, r.dither_intensity = True, 0.0
        vs.view_transform, vs.exposure, vs.gamma = "Raw", 0.0, 1.0
        r.image_settings.file_format = "OPEN_EXR"
        r.image_settings.color_mode, r.image_settings.color_depth = "RGBA", "32"
        bounds = np.array([b for _, b in owners] + [base])
        dedupe = all(core._static_mesh(scene.objects[n]) for n in seen)   # same moment, same pixels
        done = set()
        for f in frames:
            scene.frame_set(f)
            for cam in cams:
                cs = core.camera_settings(cam, props)
                if not cs["active"]:
                    continue
                if dedupe:
                    key = core.moment_key(cam, cs)
                    if key in done:
                        continue
                    done.add(key)
                hide = [scene.objects[n] for n in set(cs["hide"]) | set(movers) if n in scene.objects]
                prev = [o.hide_render for o in hide]
                scene.camera = cam
                w, h = (max(1, round(v * scale)) for v in cs["res"])
                r.resolution_x, r.resolution_y, r.resolution_percentage = w, h, 100
                try:
                    for o in hide:
                        o.hide_render = True
                    bpy.ops.render.render()
                    bpy.data.images["Render Result"].save_render(path)
                finally:
                    for o, p in zip(hide, prev):
                        o.hide_render = p
                img = bpy.data.images.load(path, check_existing=False)
                px = np.empty(w * h * 4, np.float32)
                img.pixels.foreach_get(px)
                bpy.data.images.remove(img)
                px = px.reshape(h, w, 4)
                q = np.rint(px * 255).astype(np.int64)
                ids = np.unique(((q[..., 0] << 16) | (q[..., 1] << 8) | q[..., 2])[q[..., 3] > 0])
                ids = ids[ids > 0]
                k = np.searchsorted(bounds, ids, side="right") - 1
                for j, (name, b0) in enumerate(owners):
                    sel = ids[(k == j)] - b0
                    seen[name][sel[sel < len(seen[name])]] = True
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        r.engine = saved["engine"]
        r.resolution_x, r.resolution_y, r.resolution_percentage = saved["res"]
        r.film_transparent, r.dither_intensity, scene.camera = saved["film"], saved["dither"], saved["cam"]
        scene.display.render_aa = saved["aa"]
        vs.view_transform, vs.look, vs.exposure, vs.gamma = saved["vt"], saved["look"], saved["exp"], saved["gamma"]
        (sh.light, sh.color_type, sh.show_cavity, sh.show_object_outline, sh.show_shadows,
         sh.show_specular_highlight, sh.show_xray, sh.show_backface_culling) = saved["shading"]
        (r.image_settings.file_format, r.image_settings.color_mode, r.image_settings.color_depth) = saved["fmt"]
        for me, name, render_index in touched:
            ca = me.color_attributes
            if "tc_id" in ca:
                ca.remove(ca["tc_id"])
            if name and name in ca:
                ca.active_color = ca[name]
            if 0 <= render_index < len(ca):
                ca.render_color_index = render_index
        scene.frame_set(saved["frame"])
    return seen


# ----------------------------------------------------------------------------- margins

def grow(g, mask, rings, distance):
    """Keep faces within `distance` of a seen face measured ALONG the surface (never through thin
    walls), plus `rings` extra rings of neighbours."""
    lv, ls, lt, W = g["lv"], g["ls"], g["lt"], g["W"]
    keep = mask.copy()
    if distance > 0 and keep.any() and not keep.all():
        nxt = np.roll(np.arange(len(lv)), -1)
        nxt[ls + lt - 1] = ls
        a, b = lv, lv[nxt]
        L = np.linalg.norm(W[a] - W[b], axis=1)
        adj = [[] for _ in range(len(W))]
        for u, v, w in zip(a.tolist(), b.tolist(), L.tolist()):
            adj[u].append((v, w))
            adj[v].append((u, w))
        dist = np.full(len(W), np.inf)
        seeds = np.unique(lv[np.repeat(keep, lt)])
        dist[seeds] = 0.0
        heap = [(0.0, int(v)) for v in seeds]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist[u] or d > distance:
                continue
            for v, w in adj[u]:
                nd = d + w
                if nd < dist[v] and nd <= distance:
                    dist[v] = nd
                    heapq.heappush(heap, (nd, v))
        reach = dist[lv] + np.linalg.norm(W[lv] - np.repeat(g["centers"], lt, axis=0), axis=1)
        keep |= np.minimum.reduceat(reach, ls) <= distance
    for _ in range(rings):
        vk = np.zeros(len(W), np.int8)
        vk[lv[np.repeat(keep, lt)]] = 1
        keep |= np.maximum.reduceat(vk[lv], ls) > 0
    return keep


# ----------------------------------------------------------------------------- compute

def compute(context, props):
    """Work out tc_keep for every cullable object and store it. No modifiers are added."""
    t0 = time.time()
    with core.cull_paused(context):
        keep, samples = _compute(context, props)
    by_mesh = {}  # linked duplicates share one mesh: keep a face if any of its objects needs it
    for name, mask in keep.items():
        me = bpy.data.objects[name].data
        by_mesh[me.name] = by_mesh[me.name] | mask if me.name in by_mesh else mask.copy()
    stats = {}
    for name in keep:
        me = bpy.data.objects[name].data
        mask = by_mesh[me.name]
        core.set_attr(me, CULL_ATTR, mask, "BOOLEAN")
        me.update()
        stats[name] = [int(mask.sum()), len(mask)]
    cache = core.load_cache(context.scene)
    cache["cull"] = dict(fp=core.cull_fingerprint(context, props), time=time.strftime("%Y-%m-%d %H:%M"),
                         stats=stats, samples=samples, seconds=round(time.time() - t0),
                         params=core.cull_params(props))
    core.save_cache(context.scene, cache)
    core.mark_dirty()
    return stats, samples


def _compute(context, props):
    scene = context.scene
    cp = core.cull_params(props)
    skip = skip_names(props)
    everything = core.target_objects(context)
    objs = [o for o in everything if not skipped(o, skip)]
    occ = [o for o in everything if not rigged(o) and not core.transparent(o)]  # nor do rigs or glass
    cams = [c for c in core.cameras_of(context, props)]
    if not cams:
        raise RuntimeError("No cameras to cull from (check the Cameras setting); refusing to cull everything")
    frames = core.frames_of(scene, props)
    frame0 = scene.frame_current
    moments = 0
    seen = {o.name: np.zeros(len(o.data.polygons), bool) for o in objs}
    shift_of = {o.name: float(o.get("tc_cull_shift", cp["shift"])) for o in objs}
    shifts = sorted(set(shift_of.values()))
    samples = 0
    dedupe = all(core._static_mesh(o) for o in {*occ, *objs})   # visibility is an OR: duplicates add nothing
    done = set()
    for f in frames:
        scene.frame_set(f)
        dg = context.evaluated_depsgraph_get()
        geos = None
        bvhs = {}
        for cam in cams:
            cs = core.camera_settings(cam, props)
            if not cs["active"]:
                continue
            if dedupe:
                key = core.moment_key(cam, cs)
                if key in done:
                    continue
                done.add(key)
            if geos is None:   # read the meshes only for frames that have a new moment
                geos = {o.name: core.read_mesh(o, dg) for o in {*occ, *objs}}
            cm = core.framed_camera(cam, scene, cs)
            moments += 1
            if cs["hide"] not in bvhs:
                names = [o.name for o in occ if o.name not in cs["hide"]]
                bvhs[cs["hide"]] = core.build_bvh(geos, names) if names else None
            for shift in shifts:
                members = [o for o in objs if shift_of[o.name] == shift and o.name not in cs["hide"]]
                for c in margin_cameras(cm, shift, cp["overscan"]):
                    samples += 1
                    for o in members:
                        seen[o.name] |= core.face_visibility(geos[o.name], c, bvhs[cs["hide"]], o.name)[0]
    scene.frame_set(frame0)
    if not moments:
        raise RuntimeError("No active camera moments (every camera has tc_active off); refusing to cull everything")
    if cp["pixel"]:
        movers = [o.name for o in scene.objects if o.type == "MESH" and (rigged(o) or core.transparent(o))]
        px_seen = pixel_visibility(context, props, objs, frames, cams, movers, cp["pixel_scale"])
        for n in seen:
            seen[n] |= px_seen[n]
    dg = context.evaluated_depsgraph_get()
    keep = {}
    for o in objs:
        g = core.read_mesh(o, dg)
        keep[o.name] = grow(g, seen[o.name], cp["rings"], float(o.get("tc_cull_margin", cp["distance"])))
    return keep, samples


# ----------------------------------------------------------------------------- modifiers

def node_group():
    ng = bpy.data.node_groups.get(CULL_MOD)
    if ng:
        return ng
    ng = bpy.data.node_groups.new(CULL_MOD, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n = ng.nodes
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    attr = n.new("GeometryNodeInputNamedAttribute")
    attr.data_type = "BOOLEAN"
    attr.inputs["Name"].default_value = CULL_ATTR
    exists = attr.outputs.get("Exists")
    inv = n.new("FunctionNodeBooleanMath")
    inv.operation = "NOT"
    delete = n.new("GeometryNodeDeleteGeometry")
    delete.domain = "FACE"
    delete.mode = "ALL"
    ng.links.new(attr.outputs["Attribute"], inv.inputs[0])
    if exists is not None:  # no attribute (new object): delete nothing
        both = n.new("FunctionNodeBooleanMath")
        both.operation = "AND"
        ng.links.new(inv.outputs[0], both.inputs[0])
        ng.links.new(exists, both.inputs[1])
        sel = both.outputs[0]
    else:
        sel = inv.outputs[0]
    ng.links.new(gi.outputs[0], delete.inputs["Geometry"])
    ng.links.new(sel, delete.inputs["Selection"])
    ng.links.new(delete.outputs[0], go.inputs[0])
    for x, node in enumerate(n):
        node.location = (x * 200, 0)
    return ng


def cullable(context, props):
    skip = skip_names(props)
    return [o for o in core.target_objects(context)
            if not skipped(o, skip) and CULL_ATTR in o.data.attributes]


def add_modifiers(context, props):
    """Make sure every object with a cull mask has an enabled cull modifier. Returns what changed."""
    ng = node_group()
    changes = []
    for o in cullable(context, props):
        m = o.modifiers.get(CULL_MOD)
        if m is None:
            m = o.modifiers.new(CULL_MOD, "NODES")
            m.node_group = ng
            changes.append([o.name, "added"])
        else:
            idx = o.modifiers.find(CULL_MOD)
            if not (m.show_render and m.show_viewport) or idx != len(o.modifiers) - 1:
                changes.append([o.name, "enabled", m.show_viewport, m.show_render, idx])
        m.show_render = m.show_viewport = True
        o.modifiers.move(o.modifiers.find(CULL_MOD), len(o.modifiers) - 1)  # always last
    return changes


def undo_modifiers(changes):
    for entry in changes:
        o = bpy.data.objects.get(entry[0])
        m = o and o.modifiers.get(CULL_MOD)
        if m is None:
            continue
        if entry[1] == "added":
            o.modifiers.remove(m)
        else:
            m.show_viewport, m.show_render = entry[2], entry[3]
            if len(entry) > 4:  # back to the artist's stack position
                o.modifiers.move(o.modifiers.find(CULL_MOD), min(entry[4], len(o.modifiers) - 1))


def remove_modifiers(context):
    for o in context.scene.objects:
        if o.type == "MESH" and CULL_MOD in o.modifiers:
            o.modifiers.remove(o.modifiers[CULL_MOD])


def stats_text(stats, samples, props, seconds=None):
    cp = core.cull_params(props)
    kept = sum(k for k, _ in stats.values())
    total = sum(n for _, n in stats.values())
    head = f"CULL: {samples} camera tests" + (f" in {seconds:.0f}s" if seconds is not None else "")
    lines = [f"{head} | preset {props.cull_preset.lower()} | shift {cp['shift']:g} m, overscan "
             f"{cp['overscan']:g}, surface margin {cp['distance']:g} m, pixel pass {'on' if cp['pixel'] else 'off'}",
             f"kept {kept}/{total} faces ({kept / max(total, 1):.0%})"]
    for name, (k, n) in sorted(stats.items(), key=lambda kv: kv[1][0] - kv[1][1]):
        lines.append(f"  {name:24} keep {k:6}/{n:6}  ({k / max(n, 1):.0%})")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- verify

def verify(context, props, nudge=True, max_w=480):
    """Render every camera moment with the cull on and off and count pixels that change.
    With `nudge`, also tests each moment with the camera moved by half the shift margin."""
    scene = context.scene
    cp = core.cull_params(props)
    cams = core.cameras_of(context, props)
    frames = core.frames_of(scene, props)
    changes = add_modifiers(context, props)
    sh, r = scene.display.shading, scene.render
    saved = (r.engine, scene.camera, r.resolution_x, r.resolution_y, r.resolution_percentage, scene.frame_current,
             sh.light, sh.color_type, sh.show_cavity, sh.show_object_outline, scene.display.render_aa,
             scene.view_settings.view_transform)
    tmpdir = tempfile.mkdtemp(prefix="texel_")
    path = os.path.join(tmpdir, "tc_verify.png")
    mods = [o.modifiers[CULL_MOD] for o in scene.objects if o.type == "MESH" and CULL_MOD in o.modifiers]

    def render(cam, cs):
        scale = min(1.0, max_w / cs["res"][0])
        scene.camera = cam
        r.resolution_x, r.resolution_y = round(cs["res"][0] * scale), round(cs["res"][1] * scale)
        r.resolution_percentage = 100
        bpy.ops.render.render()
        bpy.data.images["Render Result"].save_render(path)
        img = bpy.data.images.load(path, check_existing=False)
        a = np.array(img.pixels[:], np.float32).reshape(img.size[1], img.size[0], 4)[:, :, :3]
        bpy.data.images.remove(img)
        return a

    results = []
    tmp = None
    try:
        try:
            r.engine = "BLENDER_WORKBENCH"
        except TypeError:
            pass
        sh.light, sh.color_type, sh.show_cavity, sh.show_object_outline = "FLAT", "RANDOM", False, False
        scene.display.render_aa = "OFF"
        scene.view_settings.view_transform = "Standard"
        tmp = bpy.data.objects.new("TC verify camera", None)
        for f in frames:
            scene.frame_set(f)
            for cam in cams:
                cs = core.camera_settings(cam, props)
                if not cs["active"]:
                    continue
                label = core.sample_label(cam, f)
                poses = [("", None)]
                if nudge and cp["shift"] > 0:
                    R = cam.matrix_world.to_3x3()
                    d = cp["shift"] / 2
                    poses += [(f" nudged {n}", R @ Vector(v) * d) for n, v in
                              (("left", (-1, 0, 0)), ("right", (1, 0, 0)), ("down", (0, -1, 0)), ("up", (0, 1, 0)))]
                for suffix, off in poses:
                    test = cam
                    if off is not None:
                        if tmp.type != "CAMERA":
                            bpy.data.objects.remove(tmp)
                            tmp = bpy.data.objects.new("TC verify camera", cam.data)
                            scene.collection.objects.link(tmp)
                        tmp.data = cam.data
                        tmp.matrix_world = cam.matrix_world.copy()
                        tmp.matrix_world.translation += off
                        for k in cam.keys():
                            if k.startswith("tc_"):
                                tmp[k] = cam[k]
                        test = tmp
                    hide = [scene.objects[n] for n in cs["hide"] if n in scene.objects]
                    prev = [o.hide_render for o in hide]
                    try:
                        for o in hide:
                            o.hide_render = True
                        for m in mods:
                            m.show_render = False
                        a = render(test, cs)
                        for m in mods:
                            m.show_render = True
                        b = render(test, cs)
                    finally:
                        for m in mods:
                            m.show_render = True
                        for o, p in zip(hide, prev):
                            o.hide_render = p
                    diff = int((np.abs(a - b).max(axis=2) > 1e-3).sum())
                    results.append((label + suffix, diff, a.shape[1]))
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        if tmp is not None:
            bpy.data.objects.remove(tmp)
        undo_modifiers(changes)
        (r.engine, scene.camera, r.resolution_x, r.resolution_y, r.resolution_percentage, f0,
         sh.light, sh.color_type, sh.show_cavity, sh.show_object_outline, scene.display.render_aa,
         scene.view_settings.view_transform) = saved
        scene.frame_set(f0)
    return results
