"""Deterministic suggested UV layout, sized by camera need.

For every texture (objects sharing an image are packed together):
  1. islands come from the artist's UVs (their seams are kept as they are)
  2. an island is cut where camera need jumps by `split_ratio` or seen meets unseen; those cuts are
     only proposals, stored in the edge attribute `tc_proposed_cut`
  3. each piece is scaled to its P98 need (px/m); with Snap to Tiers, to the power of two above it
  4. Blender's packer places them by their real outline (any shape, quarter turns), which also gives
     the natural size: the side at which everything fits at exactly the camera need
  5. the texture is the smallest power of two at or above Size Tolerance x natural size (0.85: take
     2048 for a layout that needs 2300, not 4096), then the layout is grown uniformly to fill it.
     Locked islands keep the old size and use the deterministic shelf packer around them
With Cull Unseen Faces on, faces the export will delete are shrunk to a point and get no texels.
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
CHUNKS_PER_SIDE = 6  # grid step for cutting islands = side the texture needs / this
PACK_FILL = 0.8      # share of the square an outline pack fills, for the size estimate
PACK_SAFETY = 1.15
MIN_CHUNK = 0.04     # chunks under this share of a grid cell are merged into a neighbour   # only try the smaller size when the pack leaves at least this much room
SPLIT_PER_SIDE = 3   # faces longer than side / this set the texture size: split them


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
    copies = {}  # linked duplicates share one mesh (and one UV map): lay it out once
    for o in core.target_objects(context):
        if "tc_required" in o.data.attributes:
            copies.setdefault(o.data.name, []).append(o)
    objs, alias = [], {}
    for group in copies.values():
        # the largest copy sets the size: tc_required is the highest need of any copy in px/m,
        # and the biggest copy needs the most texels for the same px/m
        rep = min(group, key=lambda o: (-abs(o.matrix_world.to_3x3().determinant()), o.name))
        objs.append(rep)
        alias[rep.name] = {o.name for o in group}
    objs.sort(key=lambda o: o.name)
    by_key = {}  # image -> {(object, uv map): material slots}
    for o in objs:
        for slot, g in enumerate(core.face_groups(o)):
            if g:
                by_key.setdefault(g[2], {}).setdefault((o.name, g[1]), set()).add(slot)
    if only is not None:
        only = set(only)
        by_key = {k: v for k, v in by_key.items() if only & set().union(*(alias[m[0]] for m in v))}
    summaries, cuts = [], {}
    drop = _culled_faces(context, props, objs)
    if _split_long_faces(context, props, by_key, drop):
        drop = _culled_faces(context, props, objs)  # face indices moved; tc_keep came along
    for key in sorted(by_key):
        summaries.append(_suggest_key(context, props, key, by_key[key], bmesh, bmesh_linked_uv_islands, cuts,
                                      drop))
    for name, cut in cuts.items():  # proposed cuts of every texture on the mesh, written once
        me = bpy.data.objects[name].data
        core.set_attr(me, CUT_ATTR, cut, "BOOLEAN", "EDGE")
        me.update()
    return summaries


def _split_long_faces(context, props, by_key, drop):
    """Cut faces too long to chunk (a 6 m plinth strip, a long cornice) into equal segments along their
    long edges, so chunking can tile them. The shape doesn't change; the new faces inherit every face
    attribute and UV map. Inherited need and cull flags are conservative (a segment is never nearer
    than its whole face), so a fresh analysis and cull stay valid and are re-stamped, not re-run.
    Returns how many edges were split."""
    import bmesh
    status = core.cache_status(context, props, force=True)
    limit = {}  # object -> longest allowed edge per face, in metres
    for members in by_key.values():
        rows, seen = [], []
        for (oname, _uv), slots in members.items():
            o = bpy.data.objects[oname]
            me = o.data
            req = core.face_attr(me, "tc_required") * props.target_ratio
            mi = np.empty(len(me.polygons), np.int32)
            me.polygons.foreach_get("material_index", mi)
            area = np.empty(len(me.polygons))
            me.polygons.foreach_get("area", area)
            area *= abs(o.matrix_world.to_3x3().determinant()) ** (2 / 3)
            m = np.isin(mi, list(slots)) & ~drop.get(oname, np.zeros(len(mi), bool))
            m &= ~core.face_attr(me, LOCK_ATTR, bool, False)
            rows.append((oname, m, req, area))
            seen.append(req[m & (req > 0)])
        seen = np.concatenate(seen) if seen else np.zeros(0)
        floor = max(16.0, (float(np.median(seen)) if len(seen) else 64.0) * props.unseen_scale)
        total = sum(float((np.maximum(req, floor) ** 2 * area)[m].sum()) for _, m, req, area in rows)
        step = max(128.0, math.sqrt(total) / SPLIT_PER_SIDE)  # texels: only faces that set the size
        for oname, m, req, _ in rows:
            lim = limit.setdefault(oname, np.full(len(m), np.inf))
            lim[m] = np.minimum(lim[m], step / np.maximum(req[m], floor))
    split = 0
    for oname, lim in limit.items():
        if not np.isfinite(lim).any():
            continue
        o = bpy.data.objects[oname]
        if not core._static_mesh(o):   # modifiers (displace, ...) or animation: splitting could change the shape
            continue
        me = o.data
        mw = o.matrix_world
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        cuts = {}
        for f in bm.faces:
            if not np.isfinite(lim[f.index]):
                continue
            for e in f.edges:
                n = math.ceil((mw @ e.verts[0].co - mw @ e.verts[1].co).length / lim[f.index]) - 1
                if n > 0:
                    cuts[e] = max(cuts.get(e, 0), min(n, 64))
        for n in sorted(set(cuts.values())):
            bmesh.ops.subdivide_edges(bm, edges=[e for e, c in cuts.items() if c == n and e.is_valid],
                                      cuts=n, use_grid_fill=True)
        if cuts:
            bm.to_mesh(me)
            me.update()
            split += len(cuts)
        bm.free()
    if split:
        core.restamp(context, props, status, f"Suggest UVs: split {split} long edges")
        print(f"Texel Coverage: split {split} long edge(s) so their islands can be chunked")
    return split


def _culled_faces(context, props, objs):
    """{object name: faces the export cull will delete}. Those get no texture space: they are shrunk
    to a point in the suggested map, so the atlas only pays for what reaches the GLB. Computes the
    cull if it is stale (export then reuses it). Empty when Cull Unseen Faces is off."""
    if not (props.export_enabled and props.export_cull):   # a plain export keeps every face: keep its texels
        return {}
    from . import cull
    if core.cache_status(context, props, force=True)["cull"] != "fresh":
        try:
            cull.compute(context, props)
        except RuntimeError as e:  # no cameras: nothing will be culled either
            print("Texel Coverage: suggest without cull:", e)
            return {}
    skip = cull.skip_names(props)
    return {o.name: ~core.face_attr(o.data, core.CULL_ATTR, bool, True) for o in objs
            if not cull.skipped(o, skip) and core.CULL_ATTR in o.data.attributes}


def _suggest_key(context, props, key, members, bmesh, linked_islands, cuts, drop):
    pieces, obstacles, writes, dropped = [], [], [], 0
    prev_size = int(core.suggest_sizes(context.scene).get(key, 0))
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
        gone = drop.get(oname, np.zeros(len(me.polygons), bool)) & ~lock
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
            if gone.any():
                for f in faces:
                    if gone[f.index]:  # culled at export: a point, no texels
                        dst_uv[ls[f.index]:ls[f.index] + lt[f.index]] = 0.0
                        dropped += 1
                faces = [f for f in faces if not gone[f.index]]
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
                                   area=float(w.sum()), cur=cur, faces=idx, lt=lt[idx], r=r, wa=w))
        bm.free()
        writes.append((o, dst, dst_uv))

    def write():
        for o, dst, uv in writes:
            o.data.uv_layers[dst].data.foreach_set("uv", uv.astype(np.float32).ravel())
            o.data.update()

    if not pieces:
        write()
        return dict(texture=key, size=prev_size, pieces=0, locked=len(obstacles), culled=dropped,
                    note="every island is locked" if obstacles else "nothing to lay out")
    seen_needs = [p["need"] for p in pieces if p["need"] > 0]
    floor = max(16.0, (float(np.median(seen_needs)) if seen_needs else 64.0) * props.unseen_scale)
    for p in pieces:
        density = max(p["need"], floor)
        p["tier"] = tier_of(density)
        if props.suggest_snap:
            density = p["tier"]
        p["density"] = density
        p["scale"] = density / p["cur"] if p["cur"] > 0 else 0.0  # texels per source uv unit
        pts = [Vector((u * p["scale"], v * p["scale"])) for u, v in p["uv"]]
        a = _rotation(pts)
        c, s = math.cos(a), math.sin(a)
        rot = np.array([[c, -s], [s, c]])
        q = (p["uv"] * p["scale"]) @ rot.T
        q -= q.min(axis=0)
        p["texels"] = q
        p["w"], p["h"] = float(q[:, 0].max()), float(q[:, 1].max())
    pieces = _chunk(pieces, cuts, floor, props.suggest_snap)
    pieces.sort(key=lambda p: (-p["tier"], -round(p["h"], 3), p["obj"], p["first"]))
    by_map = {(o.name, dst): uv for o, dst, uv in writes}
    # texels the camera asks for, face by face: what a perfect layout would spend
    ideal = sum(float((np.maximum(p["r"], floor) ** 2 * p["wa"]).sum()) for p in pieces)
    method = "outline"
    total = sum((p["w"] + props.pack_margin_px) * (p["h"] + props.pack_margin_px) for p in pieces)
    estimate = max(MIN_TEX, core.pow2_ceil(math.sqrt(total / 0.75)))
    objs_maps = [o.name for o, _, _ in writes]
    # Blender's packer works on one active UV map per object, and knows nothing of locked islands:
    # shelves handle both cases (locks keep their size; a first run with locks starts from the estimate)
    if obstacles or len(objs_maps) != len(set(objs_maps)):
        method = "shelf"
        size, grow = _shelf(props, pieces, obstacles, prev_size or estimate, by_map, bool(obstacles and prev_size))
    else:
        try:
            size, grow = _outline_size(context, props, pieces, writes, by_map)
        except RuntimeError as e:  # objects that can't enter Edit Mode (hidden, excluded): fall back
            print("Texel Coverage: outline packing unavailable, using shelves:", e)
            method = "shelf"
            size, grow = _shelf(props, pieces, obstacles, estimate, by_map, False)
    if grow is None:
        return dict(texture=key, size=size, pieces=len(pieces), note="does not fit; unlock islands or lower the margin")
    write()
    sizes = core.suggest_sizes(context.scene)
    sizes[key] = size
    core.set_suggest_sizes(context.scene, sizes)
    tiers = {}
    for p in pieces:
        tiers[p["tier"]] = tiers.get(p["tier"], 0) + 1
    under = grow < 1.0
    return dict(texture=key, size=size, pieces=len(pieces), locked=len(obstacles), culled=dropped,
                fill=round(grow, 3), use=round(ideal / size ** 2, 3), packer=method,
                tiers=dict(sorted(tiers.items(), reverse=True)),
                note=("under target: within Size Tolerance" if under else ""))


def _chunk(pieces, cuts, floor, snap, per_side=CHUNKS_PER_SIDE):
    """Cut every island bigger than a grid step into grid chunks, whatever its shape. Two wins: the
    packer gets near-square chunks it fits tightly (no wasted corners of wedges and staircases), and
    each chunk gets the density its own faces need instead of the island's sharpest face. More seams
    in exchange; the bake fills them, so they don't show. The step is a fraction of the side the whole
    layout needs; faces go to the cell holding their centre, so cuts follow edges."""
    total = sum(_poly_area(p["texels"], p["lt"]) for p in pieces)
    step = max(64.0, math.sqrt(total) / per_side)  # texels
    out = []
    for p in pieces:
        q, lt = p["texels"], p["lt"]
        if max(p["w"], p["h"]) <= step:
            out.append(p)
            continue
        starts = np.concatenate([[0], np.cumsum(lt)[:-1]])
        cent = np.array([q[a:a + n].mean(axis=0) for a, n in zip(starts, lt)])
        cell = [tuple(c) for c in np.floor(cent / step).astype(int)]
        groups = {}
        for i, c in enumerate(cell):
            groups.setdefault(c, []).append(i)
        small = [c for c, ix in groups.items() if len(groups) > 1 and
                 _poly_area(q[np.concatenate([np.arange(starts[i], starts[i] + lt[i]) for i in ix])],
                            lt[ix]) < MIN_CHUNK * step * step]
        for c in small:   # a sliver costs a whole island (margin, packer time): join the nearest big cell
            if len(groups) <= 1:
                break
            others = [k for k in groups if k != c and k not in small] or [k for k in groups if k != c]
            to = min(others, key=lambda k: (k[0] - c[0]) ** 2 + (k[1] - c[1]) ** 2)
            for i in groups.pop(c):
                cell[i] = to
                groups[to].append(i)
        if len(groups) == 1:
            out.append(p)
            continue
        me = bpy.data.objects[p["obj"]].data
        owner = dict(zip(p["faces"].tolist(), cell))
        ek = {k: i for i, k in enumerate(me.edge_keys)}
        cut = cuts.setdefault(p["obj"], np.zeros(len(me.edges), bool))
        seen_edge = {}
        for f in p["faces"].tolist():
            for k in me.polygons[f].edge_keys:
                if k in seen_edge and seen_edge[k] != owner[f]:
                    cut[ek[k]] = True
                seen_edge.setdefault(k, owner[f])
        for c in sorted(groups):
            fi = np.array(groups[c])
            sub_loops = np.concatenate([np.arange(starts[i], starts[i] + lt[i]) for i in fi])
            r, wa = p["r"][fi], p["wa"][fi]
            seen = r > 0
            density = max(core.wquantile(r[seen], wa[seen], 0.98) if seen.any() else 0.0, floor)
            if snap:
                density = tier_of(density)
            t = q[sub_loops] * (density / p["density"] if p["density"] > 0 else 1.0)
            t = t - t.min(axis=0)
            out.append(dict(p, faces=p["faces"][fi], lt=lt[fi], loops=p["loops"][sub_loops], texels=t,
                            w=float(t[:, 0].max()), h=float(t[:, 1].max()), r=r, wa=wa, density=density,
                            tier=tier_of(density), first=int(p["faces"][fi].min())))
    return out


def _shelf(props, pieces, obstacles, size, by_map, fixed):
    """Rectangle shelf packing (the fallback, and the locked-islands path). Writes positions; returns
    (size, scale): the size may have doubled to fit unless `fixed`; scale is None if nothing fits."""
    def attempt(sz, g):
        return shelf_pack([(p["w"] * g / sz, p["h"] * g / sz) for p in pieces], obstacles,
                          props.pack_margin_px / sz)

    grow = 1.0
    while attempt(size, grow) is None:
        if not fixed and size < MAX_TEX:
            size *= 2  # only when the caller allows it; fixed sizes shrink the pieces instead
            continue
        grow *= 0.9
        if grow < 0.05:
            return size, None
    if grow == 1.0 and props.suggest_fill:
        lo, hi = 1.0, 2.0
        for _ in range(10):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if attempt(size, mid) is not None else (lo, mid)
        grow = lo
    for p, (x, y) in zip(pieces, attempt(size, grow)):
        by_map[(p["obj"], p["dst"])][p["loops"]] = p["texels"] * grow / size + np.array([x, y])
    return size, grow


def _outline_size(context, props, pieces, writes, by_map):
    """Pack by outline, find the natural size and pick the power of two. Returns (size, grow)."""
    tol = max(0.5, min(1.0, getattr(props, "suggest_tolerance", 1.0)))
    # judge a size like the export does: by the screen pixels, not by each island's sharpest face.
    # At scale g a face gets g * island density / its need; the export's quantile of that, weighted
    # by screen pixels (need^2 x area), must reach the tolerance. It is g * q, with q found once.
    ratio = np.concatenate([p["density"] / p["r"][p["r"] > 0] for p in pieces if (p["r"] > 0).any()] or [np.ones(1)])
    weight = np.concatenate([(p["r"] ** 2 * p["wa"])[p["r"] > 0] for p in pieces if (p["r"] > 0).any()] or [np.ones(1)])
    q = max(1e-6, float(core.wquantile(ratio, weight, 1.0 - props.quantile)))
    tol = min(tol / q, tol)  # never stricter than the plain tolerance
    m = props.pack_margin_px
    clamp = lambda sz: min(MAX_TEX, max(MIN_TEX, sz))
    # 1. estimate the natural size (where every piece fits at its need) from the pieces' real area: outline
    #    packing fills ~80% of the square, so one concave pack usually lands on the final size at once
    area = sum(_poly_area(p["texels"], p["lt"]) + m * (p["w"] + p["h"]) for p in pieces)
    size = clamp(core.pow2_ceil(tol * math.sqrt(area / PACK_FILL)))
    tried = {}

    def run(sz):
        tried[sz] = _outline_pack(context, props, pieces, writes, by_map, sz)
        return tried[sz]

    g = run(size)
    # 2. one correction at most: the pack tells the true natural size (size / g); margins are fixed pixels,
    #    so the scale isn't exactly linear, which is why the result is checked rather than assumed
    if g < tol and size < MAX_TEX:
        size = clamp(max(size * 2, core.pow2_ceil(tol * size / g)))
        g = run(size)
    elif g >= tol and size > MIN_TEX and g / 2 >= tol * PACK_SAFETY:
        smaller = clamp(core.pow2_ceil(tol * size / g))
        if smaller < size:
            g2 = run(smaller)
            if g2 >= tol:
                size, g = smaller, g2
            else:
                g = run(size)   # the uvs hold the last pack: put the good one back
    grow = g
    if grow > 1.0 and not props.suggest_fill:
        for o, dst, uv in writes:  # back to exactly the need, packed from the corner
            for p in pieces:
                if (p["obj"], p["dst"]) == (o.name, dst):
                    uv[p["loops"]] /= grow
        grow = 1.0
    return size, grow


def _outline_pack(context, props, pieces, writes, by_map, size):
    """One run of Blender's packer (concave shapes, any rotation, scaled to fill the square) over the
    pieces drawn at their need for a `size` texture. Reads the result back into by_map and returns
    how much it scaled them: above 1 they fit with room to spare, below 1 they don't fit at need."""
    import bmesh
    for p in pieces:
        by_map[(p["obj"], p["dst"])][p["loops"]] = p["texels"] / size
    objs = [o for o, _, _ in writes]
    for o in objs:
        if o.name not in context.view_layer.objects:
            raise RuntimeError(f"{o.name} is not in the view layer")
    faces = {}
    for p in pieces:
        faces.setdefault(p["obj"], []).append(p["faces"])
    vl = context.view_layer
    ts = context.scene.tool_settings
    saved = dict(active=vl.objects.active, selected=[o for o in vl.objects if o.select_get()],
                 sync=ts.use_uv_select_sync, mode=tuple(ts.mesh_select_mode),
                 hidden={o.name: (o.hide_get(), o.hide_viewport) for o in objs})
    state = {}
    try:
        for o, dst, uv in writes:
            me = o.data
            sel = [np.empty(len(c), bool) for c in (me.vertices, me.edges, me.polygons)]
            for arr, coll in zip(sel, (me.vertices, me.edges, me.polygons)):
                coll.foreach_get("select", arr)
            hid = np.empty(len(me.polygons), bool)
            me.polygons.foreach_get("hide", hid)
            state[o.name] = (me.uv_layers.active.name, sel, hid)
            me.uv_layers[dst].data.foreach_set("uv", uv.astype(np.float32).ravel())
            me.uv_layers.active = me.uv_layers[dst]
            o.hide_set(False)
            o.hide_viewport = False
            if not o.visible_get():
                raise RuntimeError(f"{o.name} is in a hidden or excluded collection")
        for o in vl.objects:
            o.select_set(o in objs)
        vl.objects.active = objs[0]
        ts.use_uv_select_sync = True
        ts.mesh_select_mode = (False, False, True)
        bpy.ops.object.mode_set(mode="EDIT")
        for o in objs:
            bm = bmesh.from_edit_mesh(o.data)
            bm.faces.ensure_lookup_table()
            want = np.zeros(len(bm.faces), bool)
            for idx in faces.get(o.name, []):
                want[idx] = True
            for f in bm.faces:
                f.hide_set(False)
                f.select_set(bool(want[f.index]))
            bm.select_flush_mode()
            bmesh.update_edit_mesh(o.data)
        bpy.ops.uv.pack_islands(udim_source="CLOSEST_UDIM", rotate=True, rotate_method="CARDINAL", scale=True,
                                merge_overlap=False, margin_method="ADD",
                                margin=props.pack_margin_px / size, pin=False, shape_method="CONCAVE")
        bpy.ops.object.mode_set(mode="OBJECT")
        before = after = 0.0
        for o, dst, uv in writes:
            me = o.data
            out = np.empty(len(me.loops) * 2, np.float32)
            me.uv_layers[dst].data.foreach_get("uv", out)
            out = out.reshape(-1, 2).astype(np.float64)
            for p in pieces:
                if (p["obj"], p["dst"]) == (o.name, dst):
                    before += _poly_area(uv[p["loops"]], p["lt"])
                    uv[p["loops"]] = out[p["loops"]]
                    after += _poly_area(uv[p["loops"]], p["lt"])
        return math.sqrt(after / before) if before > 0 else 1.0
    finally:
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for o in objs:
            if o.name in state:
                active, sel, hid = state[o.name]
                me = o.data
                for arr, coll in zip(sel, (me.vertices, me.edges, me.polygons)):
                    coll.foreach_set("select", arr)
                me.polygons.foreach_set("hide", hid)
                me.uv_layers.active = me.uv_layers[active]
            h, hv = saved["hidden"][o.name]
            o.hide_viewport = hv
            o.hide_set(h)
        for o in vl.objects:
            o.select_set(o in saved["selected"])
        vl.objects.active = saved["active"]
        ts.use_uv_select_sync = saved["sync"]
        ts.mesh_select_mode = saved["mode"]


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
             f"{'texture':42} {'size':>5} {'pieces':>6} {'locked':>6} {'culled':>6} {'fill':>5} {'use':>5}  tiers px/m (pieces)"]
    for s in summaries:
        tiers = " ".join(f"{k}:{v}" for k, v in s.get("tiers", {}).items())
        lines.append(f"{s['texture'][:42]:42} {s['size']:>5} {s['pieces']:>6} {s.get('locked', 0):>6} "
                     f"{s.get('culled', 0):>6} {s.get('fill', 0):>5} {s.get('use', 0):>5.0%}  {tiers}  {s.get('note', '')}")
    lines.append("")
    lines.append("size = smallest power-of-two texture that holds every piece at its camera need "
                 "(1 texel per screen pixel). fill = how much the layout was then grown uniformly into the "
                 "leftover space (above 1 = sharper than needed). use = texels the camera asks for, face by face, over the texture's texels (the rest is "
                 "packing gaps, islands held at their sharpest face, and the power-of-two step). "
                 "tiers = pieces per density band. culled = faces the export "
                 "cull deletes: shrunk to a point, no texels (with Cull Unseen Faces off they keep their space).")
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


def promote(context, objs, layouts=None):
    """Copy each suggested map into the UV map it was made from. A backup is kept.
    `layouts`: only these (mesh name, uv map) pairs (None = every suggested map)."""
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
            if src not in me.uv_layers or (layouts is not None and (me.name, src) not in layouts):
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
