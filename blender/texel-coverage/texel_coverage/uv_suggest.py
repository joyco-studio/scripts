"""Deterministic suggested UV layout, sized by camera need.

For every texture (objects sharing an image are packed together):
  1. islands come from the artist's UVs (their seams are kept as they are)
  2. an island is cut where camera need jumps by `split_ratio` or seen meets unseen; those cuts are
     only proposals, stored in the edge attribute `tc_proposed_cut`
  3. each piece is scaled to its P98 need (px/m); with Snap to Tiers, to the power of two above it
  4. pieces are turned to their tightest bounding box, landscape
  5. a shelf packer places them in a fixed order (tier, height, object, first face), so the same
     scene always gives the same layout, then the layout is grown uniformly to fill the square
Faces flagged `tc_lock` keep their place in the suggested map; the rest packs around them.
The result goes to `<uv>_tc_suggest`; the artist's UV map is never touched until Promote.
"""

import math

import bpy
import numpy as np
from mathutils import Vector, geometry

from . import core

SUFFIX = "_tc_suggest"
BACKUP = "_tc_backup"
LOCK_ATTR = "tc_lock"
CUT_ATTR = "tc_proposed_cut"
MIN_TEX, MAX_TEX = 64, 8192


def split_by_need(island, req, wA, ratio, min_share=0.03):
    """Cut an island where camera need jumps by `ratio` or more, or where seen meets unseen."""
    if ratio <= 1:
        return [island]
    step = math.log2(ratio)
    band = {f: (int(math.floor(math.log2(req[f.index]) / step)) if req[f.index] > 0 else None) for f in island}
    members = set(island)

    def components():
        seen, out = set(), []
        for f in island:
            if f in seen:
                continue
            comp, stack = [], [f]
            seen.add(f)
            while stack:
                g = stack.pop()
                comp.append(g)
                for e in g.edges:
                    for h in e.link_faces:
                        if h in members and h not in seen and band[h] == band[g]:
                            seen.add(h)
                            stack.append(h)
            out.append(comp)
        return out

    total = sum(wA[f.index] for f in island) or 1.0
    comps = components()
    if len(comps) == 1:
        return comps
    for comp in comps:  # fold slivers into their neighbours so packing doesn't drown in crumbs
        if sum(wA[f.index] for f in comp) / total >= min_share:
            continue
        votes = {}
        for g in comp:
            for e in g.edges:
                for h in e.link_faces:
                    if h in members and band[h] != band[g]:
                        votes[band[h]] = votes.get(band[h], 0) + wA[h.index]
        if votes:
            b = max(sorted(votes, key=lambda k: (k is None, k)), key=votes.get)
            for g in comp:
                band[g] = b
    return components()


def shelf_pack(sizes, obstacles, margin):
    """Next-fit shelf packing in the unit square, skipping locked rectangles. None if it doesn't fit."""
    pos = []
    x = y = margin
    row = 0.0
    for w, h in sizes:
        if w + 2 * margin > 1 or h + 2 * margin > 1:
            return None
        while True:
            if x + w + margin > 1:
                step = max(row, margin) + margin
                if step <= 1e-9:  # zero margin and nothing placed in this row: jump past what blocks it
                    tops = [o[3] for o in obstacles if o[1] < y + h + margin and o[3] + margin > y]
                    step = max((min(tops) - y) if tops else h, 1e-6)
                x, y, row = margin, y + step, 0.0
            if y + h + margin > 1:
                return None
            hit = next((o for o in obstacles if x < o[2] + margin and x + w + margin > o[0]
                        and y < o[3] + margin and y + h + margin > o[1]), None)
            if hit is None:
                break
            x = hit[2] + margin
        pos.append((x, y))
        x += w + margin
        row = max(row, h)
    return pos


def _rotation(points):
    """Angle that turns a point set to its tightest bounding box, landscape."""
    if len(points) < 3:
        return 0.0
    hull = geometry.convex_hull_2d(points)
    angle = geometry.box_fit_2d([points[i] for i in hull]) if len(hull) >= 3 else 0.0
    c, s = math.cos(angle), math.sin(angle)
    xs = [p[0] * c - p[1] * s for p in points]
    ys = [p[0] * s + p[1] * c for p in points]
    if (max(ys) - min(ys)) > (max(xs) - min(xs)) * 1.0001:
        angle += math.pi / 2
    return angle


def tier_of(need):
    return core.pow2_ceil(need) if need > 0 else 0


def suggest(context, props, only=None):
    """Write <uv>_tc_suggest maps. `only`: object names whose textures to lay out (None = all).
    Returns a list of per-texture summaries."""
    import bmesh
    from bpy_extras.bmesh_utils import bmesh_linked_uv_islands

    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    objs, meshes = [], set()
    for o in sorted(core.target_objects(context), key=lambda o: o.name):
        if "tc_required" in o.data.attributes and o.data.name not in meshes:  # linked duplicates: once per mesh
            meshes.add(o.data.name)
            objs.append(o)
    by_key = {}  # image -> {(object, uv map): material slots}
    for o in objs:
        for slot, g in enumerate(core.face_groups(o)):
            if g:
                by_key.setdefault(g[2], {}).setdefault((o.name, g[1]), set()).add(slot)
    if only is not None:
        only = set(only)
        by_key = {k: v for k, v in by_key.items() if only & {m[0] for m in v}}
    summaries, cuts = [], {}
    for key in sorted(by_key):
        summaries.append(_suggest_key(context, props, key, by_key[key], bmesh, bmesh_linked_uv_islands, cuts))
    for name, cut in cuts.items():  # proposed cuts of every texture on the mesh, written once
        me = bpy.data.objects[name].data
        core.set_attr(me, CUT_ATTR, cut, "BOOLEAN", "EDGE")
        me.update()
    return summaries


def _suggest_key(context, props, key, members, bmesh, linked_islands, cuts):
    pieces, obstacles, writes = [], [], []
    prev = context.scene.get("tc_suggest_sizes", {})
    prev_size = int(prev.get(key, 0)) if hasattr(prev, "get") else 0
    for (oname, uvname), slots in sorted(members.items()):
        o = bpy.data.objects[oname]
        me = o.data
        dst = uvname + SUFFIX
        if dst not in me.uv_layers:
            layer = me.uv_layers.new(name=dst, do_init=False)
            src = np.empty(len(me.loops) * 2, np.float32)
            me.uv_layers[uvname].data.foreach_get("uv", src)
            layer.data.foreach_set("uv", src)
        with core.cull_paused(context):
            geo = core.read_mesh(o, context.evaluated_depsgraph_get())
        wA = core.uv_density(geo, uvname)[0] if geo["npoly"] == len(me.polygons) else None
        if wA is None:
            continue
        req = core.face_attr(me, "tc_required") * props.target_ratio
        lock = core.face_attr(me, LOCK_ATTR, bool, False)
        mi = np.empty(len(me.polygons), np.int32)
        me.polygons.foreach_get("material_index", mi)
        in_key = np.isin(mi, list(slots))
        dst_uv = np.empty(len(me.loops) * 2, np.float64)
        me.uv_layers[dst].data.foreach_get("uv", dst_uv)
        dst_uv = dst_uv.reshape(-1, 2)
        ls = geo["ls"]
        lt = geo["lt"]

        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        src_layer = bm.loops.layers.uv[uvname]
        cut = cuts.setdefault(oname, np.zeros(len(me.edges), bool))
        for isl in linked_islands(bm, src_layer):
            faces = [f for f in isl if in_key[f.index]]
            if not faces:
                continue
            locked = [f for f in faces if lock[f.index]]
            free = [f for f in faces if not lock[f.index]]
            if locked:  # locked faces keep their place: their bounds become obstacles
                loops = np.concatenate([np.arange(ls[f.index], ls[f.index] + lt[f.index]) for f in locked])
                p = dst_uv[loops]
                obstacles.append((float(p[:, 0].min()), float(p[:, 1].min()),
                                  float(p[:, 0].max()), float(p[:, 1].max())))
            if not free:
                continue
            parts = split_by_need(free, req, wA, props.split_ratio)
            if len(parts) > 1:
                owner = {f: i for i, part in enumerate(parts) for f in part}
                for part in parts:
                    for f in part:
                        for e in f.edges:
                            if any(h in owner and owner[h] != owner[f] for h in e.link_faces):
                                cut[e.index] = True
            for part in parts:
                idx = np.array(sorted(f.index for f in part))
                loops = np.concatenate([np.arange(ls[i], ls[i] + lt[i]) for i in idx])
                uv = np.array([[l[src_layer].uv.x, l[src_layer].uv.y] for f in sorted(part, key=lambda f: f.index)
                               for l in f.loops])
                r, w = req[idx], wA[idx]
                seen = r > 0
                need = core.wquantile(r[seen], w[seen], 0.98) if seen.any() else 0.0
                uvA = abs(_poly_area(uv, lt[idx]))
                cur = math.sqrt(uvA / max(w.sum(), 1e-12))  # uv units per metre
                pieces.append(dict(obj=oname, dst=dst, first=int(idx[0]), loops=loops, uv=uv, need=need,
                                   area=float(w.sum()), cur=cur))
        bm.free()
        writes.append((o, dst, dst_uv))

    if not pieces:
        return dict(texture=key, size=prev_size, pieces=0, locked=len(obstacles),
                    note="every island is locked" if obstacles else "nothing to lay out")
    seen_needs = [p["need"] for p in pieces if p["need"] > 0]
    floor = max(16.0, (float(np.median(seen_needs)) if seen_needs else 64.0) * props.unseen_scale)
    for p in pieces:
        density = max(p["need"], floor)
        p["tier"] = tier_of(density)
        if props.suggest_snap:
            density = p["tier"]
        p["scale"] = density / p["cur"] if p["cur"] > 0 else 0.0  # texels per source uv unit
        pts = [Vector((u * p["scale"], v * p["scale"])) for u, v in p["uv"]]
        a = _rotation(pts)
        c, s = math.cos(a), math.sin(a)
        rot = np.array([[c, -s], [s, c]])
        q = (p["uv"] * p["scale"]) @ rot.T
        q -= q.min(axis=0)
        p["texels"] = q
        p["w"], p["h"] = float(q[:, 0].max()), float(q[:, 1].max())
    pieces.sort(key=lambda p: (-p["tier"], -round(p["h"], 3), p["obj"], p["first"]))
    def attempt(size, grow):
        sizes = [(p["w"] * grow / size, p["h"] * grow / size) for p in pieces]
        return shelf_pack(sizes, obstacles, props.pack_margin_px / size)

    if obstacles and prev_size:
        size = prev_size  # locked islands were placed for this size
    else:
        total = sum((p["w"] + props.pack_margin_px) * (p["h"] + props.pack_margin_px) for p in pieces)
        size = max(MIN_TEX, core.pow2_ceil(math.sqrt(total / 0.75)))
    grow = 1.0
    while attempt(size, 1.0) is None:
        if obstacles and prev_size:
            grow *= 0.9  # can't change the size under locked islands: shrink the free ones
            if grow < 0.05 or attempt(size, grow) is not None:
                break
        elif size >= MAX_TEX:
            break
        else:
            size *= 2
    if grow == 1.0 and props.suggest_fill:
        lo, hi = 1.0, 2.0
        for _ in range(10):  # grow uniformly into the leftover space
            mid = (lo + hi) / 2
            if attempt(size, mid) is not None:
                lo = mid
            else:
                hi = mid
        grow = lo
    pos = attempt(size, grow)
    if pos is None:
        return dict(texture=key, size=size, pieces=len(pieces), note="does not fit; unlock islands or lower the margin")
    by_map = {(o.name, dst): uv for o, dst, uv in writes}
    for p, (x, y) in zip(pieces, pos):
        uv = by_map[(p["obj"], p["dst"])]
        uv[p["loops"]] = p["texels"] * grow / size + np.array([x, y])
    for o, dst, uv in writes:
        o.data.uv_layers[dst].data.foreach_set("uv", uv.astype(np.float32).ravel())
        o.data.update()
    old = context.scene.get("tc_suggest_sizes")
    sizes = old.to_dict() if hasattr(old, "to_dict") else {}
    sizes[key] = size
    context.scene["tc_suggest_sizes"] = sizes
    tiers = {}
    for p in pieces:
        tiers[p["tier"]] = tiers.get(p["tier"], 0) + 1
    under = grow < 1.0
    return dict(texture=key, size=size, pieces=len(pieces), locked=len(obstacles), fill=round(grow, 3),
                tiers=dict(sorted(tiers.items(), reverse=True)),
                note=("free islands shrunk to fit around locked ones: under target" if under else ""))


def _poly_area(uv, lt):
    """Sum of polygon areas (shoelace) for consecutive polygons with loop totals `lt`."""
    total, i = 0.0, 0
    for n in lt:
        p = uv[i:i + n]
        x, y = p[:, 0], p[:, 1]
        total += 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
        i += n
    return total


def summary_text(summaries):
    lines = ["SUGGESTED UV LAYOUT (uv maps *" + SUFFIX + ")",
             f"{'texture':42} {'size':>5} {'pieces':>6} {'locked':>6} {'fill':>5}  tiers px/m (pieces)"]
    for s in summaries:
        tiers = " ".join(f"{k}:{v}" for k, v in s.get("tiers", {}).items())
        lines.append(f"{s['texture'][:42]:42} {s['size']:>5} {s['pieces']:>6} {s.get('locked', 0):>6} "
                     f"{s.get('fill', 0):>5}  {tiers}  {s.get('note', '')}")
    lines.append("")
    lines.append("size = smallest power-of-two texture that holds every piece at its camera need "
                 "(1 texel per screen pixel). fill = how much the layout was then grown uniformly into the "
                 "leftover space (above 1 = sharper than needed). tiers = pieces per density band.")
    return "\n".join(lines)


def set_lock(context, value):
    """Lock or unlock the selected faces (edit mode selection; UV editor selection when sync is off).
    Works on the edit mesh directly, so no mode switch and no undo surprises."""
    import bmesh
    count = 0
    sync = context.scene.tool_settings.use_uv_select_sync
    for o in [o for o in context.objects_in_mode if o.type == "MESH"]:
        bm = bmesh.from_edit_mesh(o.data)
        layer = bm.faces.layers.bool.get(LOCK_ATTR) or bm.faces.layers.bool.new(LOCK_ATTR)
        uvl = bm.loops.layers.uv.active
        for f in bm.faces:
            if not f.select or f.hide:
                continue
            if not sync and not core.uv_face_selected(f, uvl):
                continue
            f[layer] = value
            count += 1
        bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)
    return count


def select_cuts(context):
    """Select the proposed cut edges in edit mode, ready for Mark Seam."""
    import bmesh
    n = 0
    bpy.ops.mesh.select_mode(type="EDGE")
    for o in [o for o in context.objects_in_mode if o.type == "MESH"]:
        bm = bmesh.from_edit_mesh(o.data)
        layer = bm.edges.layers.bool.get(CUT_ATTR)
        if layer is None:
            continue
        for f in bm.faces:
            f.select = False
        for e in bm.edges:
            e.select = bool(e[layer])
            n += e.select
        for v in bm.verts:
            v.select = any(e.select for e in v.link_edges)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)
    return n


def promote(context, objs):
    """Copy each suggested map into the UV map it was made from. A backup is kept."""
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    done, meshes = [], set()
    for o in objs:
        me = o.data
        if me.name in meshes:  # linked duplicate: a second pass would back up the promoted UVs
            continue
        meshes.add(me.name)
        for layer in list(me.uv_layers):
            if not layer.name.endswith(SUFFIX):
                continue
            src = layer.name[: -len(SUFFIX)]
            if src not in me.uv_layers:
                continue
            a = np.empty(len(me.loops) * 2, np.float32)
            me.uv_layers[src].data.foreach_get("uv", a)
            backup = me.uv_layers.get(src + BACKUP) or me.uv_layers.new(name=src + BACKUP, do_init=False)
            backup.data.foreach_set("uv", a)
            layer.data.foreach_get("uv", a)
            me.uv_layers[src].data.foreach_set("uv", a)
            me.update()
            done.append(f"{o.name}:{src}")
    return done
