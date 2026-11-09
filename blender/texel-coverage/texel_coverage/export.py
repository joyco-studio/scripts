"""Optimize-on-export: temporary culling and texture downsizing around the stock glTF exporter.

prepare()  refreshes the analysis/cull cache if needed, enables the cull modifiers and swaps every
           texture that can shrink for a smaller copy under the same name.
restore()  puts everything back. It is idempotent and also recovers from an interrupted export,
           because temporary images and swapped originals are tagged with custom properties.

The glTF exporter calls these through glTF2_pre_export_callback / glTF2_post_export_callback
(see __init__.py), so artists keep using File > Export > glTF 2.0.
"""

import json
import math
import os
import time

import bpy
import numpy as np

from . import core, cull

STATE_KEY = "tc_export_state"
COPY_TAG = "tc_export_copy"      # on a temporary image: name of the original
ORIG_TAG = "tc_export_original"  # on a swapped original: its real name
FULL_SUFFIX = ".tc_full"
_ACTIVE = {}                      # the in-flight export, if any
_MANAGED = {"on": False}          # set by run() so the exporter callbacks don't run twice


# ----------------------------------------------------------------------------- texture plan

def material_users(context):
    """image name -> list of (object, slot index, uv map, material, coordinate scale or None)."""
    out = {}
    for o in context.scene.objects:
        if o.type != "MESH":
            continue
        default_uv = core.render_uv(o.data)
        for slot, ms in enumerate(o.material_slots):
            mat = ms.material
            if not (mat and mat.use_nodes and mat.node_tree):
                continue
            for n in core.image_nodes(mat.node_tree):
                uv = core.uv_of_image_node(n)
                uv = uv if uv and uv in o.data.uv_layers else default_uv
                entry = (o, slot, uv, mat, core.coord_scale(n))
                lst = out.setdefault(n.image.name, [])
                if all((e[0], e[1], e[2]) != entry[:3] for e in lst):
                    lst.append(entry)
    return out


def _limit(mat, key):
    v = mat.get(key) if mat else None
    return int(v) if v else None


def texture_plan(context, props):
    """For every image: its size now, the size the cameras need with the current UVs, the size to
    export, and which faces hold it at that size."""
    objs = {o.name for o in core.target_objects(context)}
    cache = core.load_cache(context.scene)
    labels = cache.get("analysis", {}).get("labels", [])
    q = props.quantile
    geos = {}
    with core.cull_paused(context):
        dg = context.evaluated_depsgraph_get()
        for name in objs:
            o = bpy.data.objects[name]
            if "tc_required" in o.data.attributes:
                geos[name] = core.read_mesh(o, dg)
    plan = []
    for img_name, users in sorted(material_users(context).items()):
        img = bpy.data.images.get(img_name)
        if img is None or img.get(COPY_TAG):
            continue
        w, h = img.size
        entry = dict(image=img_name, size=[w, h], target=[w, h], need=None, reason="", blockers=[],
                     objects=sorted({u[0].name for u in users}))
        plan.append(entry)
        if w == 0 or h == 0:
            entry["reason"] = "image has no pixels on this machine"
            continue
        if img.get("tc_keep_size"):
            entry["reason"] = "tc_keep_size on the image"
            continue
        users = [u for u in users if u[0].name in objs]  # objects outside the export never see it
        entry["objects"] = sorted({u[0].name for u in users})
        if not users:
            entry["reason"] = "not used by exported objects"
            continue
        if any(u[4] is None for u in users):
            entry["reason"] = "texture coordinates can't be measured (not plain UVs / Mapping)"
            continue
        missing = sorted({u[0].name for u in users if u[0].name not in geos})
        if missing:
            entry["reason"] = f"used by objects without analysis ({', '.join(missing[:3])})"
            continue
        need, wts, objn, drv = [], [], [], []
        mins, maxs = [], []
        for o, slot, uv, mat, cscale in users:
            g = geos[o.name]
            me = o.data
            if g["npoly"] != len(me.polygons):
                continue
            sel = g["mi"] == slot
            if not sel.any():
                continue
            req = core.face_attr(me, "tc_required")[sel] * props.target_ratio
            scr = core.face_attr(me, "tc_screen_px")[sel]
            dr = core.face_attr(me, "tc_driver", np.int32, -1)[sel]
            unit = core.uv_density(g, uv)[2][sel] * cscale  # a Mapping scale of 0.5 halves the texels per metre
            with np.errstate(divide="ignore", invalid="ignore"):
                nr = np.where((req > 0) & (unit > 0), req / unit, np.nan)
            need.append(nr)
            wts.append(scr)
            objn.append(np.full(len(nr), o.name, dtype=object))
            drv.append(dr)
            if _limit(mat, "tc_min_size"):
                mins.append(_limit(mat, "tc_min_size"))
            if _limit(mat, "tc_max_size"):
                maxs.append(_limit(mat, "tc_max_size"))
        if not need:
            entry["reason"] = "no faces use it"
            continue
        need, wts, objn, drv = map(np.concatenate, (need, wts, objn, drv))
        side = max(w, h)
        # need is in square-texture terms; the short side of a rectangular image must deliver it
        aspect = side / min(w, h)
        exact = core.wquantile(need, wts, q) * aspect
        seen = np.isfinite(need) & (wts > 0)
        target = core.pow2_ceil(exact) if seen.any() else props.export_min_size
        target = max(target, props.export_min_size, *mins) if mins else max(target, props.export_min_size)
        target = min(target, side, props.export_max_size, *maxs) if maxs else min(target, side, props.export_max_size)
        if target >= side:
            entry["need"] = round(exact) if seen.any() else 0
            entry["reason"] = "needs full size" if seen.any() and core.pow2_ceil(exact) >= side else "limits keep it"
            continue
        f = side / target
        entry["target"] = [max(1, round(w / f)), max(1, round(h / f))]
        entry["need"] = round(exact) if seen.any() else 0
        entry["reason"] = "never seen" if not seen.any() else f"P{round(q * 100)} need {round(exact)} px"
        # what stops it from halving again: faces needing more than target/2
        block = seen & (need * aspect > target / 2)
        if block.any():
            tot = wts[block].sum()
            keys = {}
            for o_, d_, w_ in zip(objn[block], drv[block], wts[block]):
                keys[(o_, int(d_))] = keys.get((o_, int(d_)), 0.0) + float(w_)
            for (o_, d_), w_ in sorted(keys.items(), key=lambda kv: -kv[1])[:2]:
                entry["blockers"].append(dict(object=o_, moment=labels[d_] if 0 <= d_ < len(labels) else "",
                                              share=round(100 * w_ / max(tot, 1e-9))))
    return plan


# ----------------------------------------------------------------------------- image swap

def _downsample(px, f):
    h, w, c = px.shape
    if f > 1 and h % f == 0 and w % f == 0:
        return px.reshape(h // f, f, w // f, f, c).mean(axis=(1, 3))
    return None


def swap_image(name, target):
    """Replace image `name` with a smaller copy under the same name. Returns the copy."""
    orig = bpy.data.images[name]
    w, h = orig.size
    tw, th = target
    px = np.empty(w * h * orig.channels, np.float32)
    orig.pixels.foreach_get(px)
    px = px.reshape(h, w, orig.channels)
    small = _downsample(px, w // tw if w % tw == 0 and w // tw == h // th else 0)
    copy = bpy.data.images.new(name + ".tc_tmp", tw, th, alpha=orig.channels == 4,
                               float_buffer=orig.is_float)
    try:
        copy.colorspace_settings.name = orig.colorspace_settings.name
    except TypeError:
        pass
    copy.alpha_mode = orig.alpha_mode
    if small is None:  # odd sizes: let Blender resample
        copy.scale(w, h)
        copy.pixels.foreach_set(px.ravel())
        copy.scale(tw, th)
    else:
        if small.shape[2] != copy.channels:
            pad = np.ones(small.shape[:2] + (copy.channels,), np.float32)
            pad[..., :min(copy.channels, small.shape[2])] = small[..., :copy.channels]
            small = pad
        copy.pixels.foreach_set(small.astype(np.float32).ravel())
    copy[COPY_TAG] = name
    orig[ORIG_TAG] = name
    orig.name = name + FULL_SUFFIX
    copy.name = name
    for tree in _node_trees():
        for n in tree.nodes:
            if n.type == "TEX_IMAGE" and n.image == orig:
                n.image = copy
    return copy


def _node_trees():
    for m in bpy.data.materials:
        if m.node_tree:
            yield m.node_tree
    yield from bpy.data.node_groups


def restore_images():
    """Put every swapped original back. Safe to call any time."""
    restored = 0
    copies = [i for i in bpy.data.images if i.get(COPY_TAG)]
    for copy in copies:
        name = copy[COPY_TAG]
        orig = next((i for i in bpy.data.images if i.get(ORIG_TAG) == name), None)
        if orig is not None:
            for tree in _node_trees():
                for n in tree.nodes:
                    if n.type == "TEX_IMAGE" and n.image == copy:
                        n.image = orig
        bpy.data.images.remove(copy)
        if orig is not None:
            orig.name = name
            del orig[ORIG_TAG]
            restored += 1
    for orig in [i for i in bpy.data.images if i.get(ORIG_TAG)]:
        orig.name = orig[ORIG_TAG]
        del orig[ORIG_TAG]
    return restored


# ----------------------------------------------------------------------------- helper uv maps

STASH = ".tc_stash."  # hidden corner attribute holding a removed helper UV map (".tc_stash.A." = was active)
HELPER_SUFFIXES = ("_tc_suggest", "_tc_backup", "_tc_opt")


def stash_helper_uvs(context):
    """The glTF exporter writes every UV map. Park helper maps in hidden attributes during export."""
    n = 0
    for me in {o.data for o in core.target_objects(context)}:
        for layer in [l for l in me.uv_layers if l.name.endswith(HELPER_SUFFIXES)]:
            uv = np.empty(len(me.loops) * 2, np.float32)
            layer.data.foreach_get("uv", uv)
            active = me.uv_layers.active is not None and me.uv_layers.active.name == layer.name
            name = STASH + ("A." if active else "") + layer.name
            me.uv_layers.remove(layer)
            a = me.attributes.new(name, "FLOAT_VECTOR", "CORNER")
            v = np.zeros((len(me.loops), 3), np.float32)
            v[:, :2] = uv.reshape(-1, 2)
            a.data.foreach_set("vector", v.ravel())
            n += 1
    return n


def unstash_helper_uvs():
    n = 0
    for me in bpy.data.meshes:
        for a in [a for a in me.attributes if a.name.startswith(STASH)]:
            name = a.name[len(STASH):]
            active = name.startswith("A.")
            name = name[2:] if active else name
            v = np.empty(len(me.loops) * 3, np.float32)
            a.data.foreach_get("vector", v)
            me.attributes.remove(a)
            layer = me.uv_layers.get(name) or me.uv_layers.new(name=name, do_init=False)
            layer.data.foreach_set("uv", v.reshape(-1, 3)[:, :2].ravel())
            if active:
                me.uv_layers.active = layer
            n += 1
    return n


# ----------------------------------------------------------------------------- triangulation

TRI_MOD = "TC Triangulate"


def add_triangulate(context):
    """GLB is triangles anyway. Splitting quads with a fixed rule makes the export identical whether
    it runs in the Blender UI or headless (their own triangulation can break near-ties differently)."""
    n = 0
    for o in core.target_objects(context):
        if cull.rigged(o) or o.modifiers.get(TRI_MOD):
            continue
        m = o.modifiers.new(TRI_MOD, "TRIANGULATE")
        m.quad_method, m.ngon_method, m.min_vertices = "FIXED", "CLIP", 4
        if hasattr(m, "keep_custom_normals"):
            m.keep_custom_normals = True
        n += 1
    return n


def remove_triangulate():
    n = 0
    for o in bpy.data.objects:
        m = o.modifiers.get(TRI_MOD) if o.type == "MESH" else None
        if m is not None:
            o.modifiers.remove(m)
            n += 1
    return n


# ----------------------------------------------------------------------------- prepare / restore

def ensure_fresh(context, props, log):
    """Re-run the analysis and the cull if anything they depend on changed."""
    status = core.cache_status(context, props, force=True)
    need_analysis = props.export_resize and status["analysis"] != "fresh"
    need_cull = props.export_cull and status["cull"] != "fresh"
    if not (need_analysis or need_cull):
        log.append("cache: fresh, reused the stored analysis and cull")
        return True
    if props.export_stale == "SKIP":
        log.append(f"cache: analysis {status['analysis']}, cull {status['cull']} -> exported WITHOUT optimization")
        return False
    if need_analysis:
        t = time.time()
        core.analyze(context, props)
        log.append(f"analysis refreshed in {time.time() - t:.0f}s")
    if need_cull:
        t = time.time()
        cull.compute(context, props)
        log.append(f"cull refreshed in {time.time() - t:.0f}s")
    return True


def prepare(context, props, export_settings=None):
    core.object_mode(context)
    restore(context)  # leftovers from an interrupted export
    state = dict(started=time.time(), log=[], modifiers=[], plan=[], cull={}, ok=False,
                 filepath=(export_settings or {}).get("gltf_filepath", ""))
    _ACTIVE.clear()
    _ACTIVE.update(state)
    try:
        if not ensure_fresh(context, props, state["log"]):
            return state
        if props.export_resize:
            state["plan"] = texture_plan(context, props)
            for e in state["plan"]:
                if e["target"] != e["size"]:
                    swap_image(e["image"], e["target"])
        hidden = stash_helper_uvs(context)
        if hidden:
            state["log"].append(f"left {hidden} helper UV map(s) (_tc_suggest/_tc_backup) out of the GLB")
        if props.export_cull:
            state["modifiers"] = cull.add_modifiers(context, props)
            context.scene[STATE_KEY] = json.dumps(dict(modifiers=state["modifiers"]))
            if export_settings is not None and not export_settings.get("gltf_apply"):
                export_settings["gltf_apply"] = True
                state["log"].append("turned on Apply Modifiers so the cull reaches the GLB")
            state["cull"] = core.load_cache(context.scene).get("cull", {}).get("stats", {})
        if props.export_triangulate:
            add_triangulate(context)  # after the cull modifier, so it is last
            if export_settings is not None:
                export_settings["gltf_apply"] = True
        state["ok"] = True
    except Exception:
        restore(context)
        raise
    finally:
        _ACTIVE.update(state)
    return state


def restore(context=None):
    context = context or bpy.context
    scene = context.scene
    images = restore_images()
    images += unstash_helper_uvs()
    mods = remove_triangulate()
    raw = scene.get(STATE_KEY)
    if raw:
        try:
            changes = json.loads(raw).get("modifiers", [])
        except ValueError:
            changes = []
        cull.undo_modifiers(changes)
        mods += len(changes)
        del scene[STATE_KEY]
    return images, mods


# ----------------------------------------------------------------------------- report

def vram_mb(w, h):
    return w * h * 4 * 4 / 3 / 2 ** 20  # RGBA8 with mipmaps


def report(state, props):
    plan, stats = state.get("plan", []), state.get("cull", {})
    lines = [f"# Texel export report", "",
             f"- file: {bpy.data.filepath or '(unsaved)'}",
             f"- glb: {state.get('filepath') or '(none)'}",
             f"- date: {time.strftime('%Y-%m-%d %H:%M')}"]
    lines += [f"- {l}" for l in state.get("log", [])]
    if not state.get("ok"):
        lines += ["", "**Not optimized.**"]
        return "\n".join(lines)
    unused = [e["image"] for e in plan if e["reason"] == "not used by exported objects"]
    plan = [e for e in plan if e["reason"] != "not used by exported objects"]
    before = sum(vram_mb(*e["size"]) for e in plan)
    after = sum(vram_mb(*e["target"]) for e in plan)
    if plan:
        lines += ["", f"## Textures ({before:.0f} MB -> {after:.0f} MB of GPU memory, RGBA8 + mips)", "",
                  "| image | now | exported | need | why | held at this size by |",
                  "|---|---|---|---|---|---|"]
        for e in sorted(plan, key=lambda e: -vram_mb(*e["size"])):
            held = "; ".join(f"{b['object']} {b['share']}%" + (f" ({b['moment']})" if b["moment"] else "")
                             for b in e["blockers"])
            lines.append(f"| {e['image']} | {e['size'][0]}x{e['size'][1]} | {e['target'][0]}x{e['target'][1]} | "
                         f"{e['need'] if e['need'] is not None else ''} | {e['reason']} | {held} |")
        lines += ["", "need = texture size the cameras need with the current UVs (P"
                  f"{round(props.quantile * 100)} of screen pixels). When it is far above the current size, "
                  "the UV layout is the problem: see Suggest UVs."]
        if unused:
            lines += ["", f"{len(unused)} images in the file are not used by exported objects and were ignored."]
    if stats:
        kept = sum(k for k, _ in stats.values())
        total = sum(n for _, n in stats.values())
        lines += ["", f"## Culling ({kept}/{total} faces kept, {kept / max(total, 1):.0%})", "",
                  "| object | kept | total | % |", "|---|---|---|---|"]
        for name, (k, n) in sorted(stats.items(), key=lambda kv: kv[1][0] - kv[1][1]):
            lines.append(f"| {name} | {k} | {n} | {k / max(n, 1):.0%} |")
        skipped = [o.name for o in core.target_objects(bpy.context)
                   if cull.skipped(o, cull.skip_names(props))]
        if skipped:
            lines += ["", "Left whole (rigged, tc_no_cull or Never Cull): " + ", ".join(sorted(skipped))]
    return "\n".join(lines)


def write_report(state, props):
    body = report(state, props)
    core.write_text("Texel Export Report", body)
    path = state.get("filepath")
    if props.export_report and path:
        base = os.path.splitext(path)[0]
        try:
            with open(base + ".texel.md", "w") as f:
                f.write(body + "\n")
            with open(base + ".texel.json", "w") as f:
                json.dump({k: state.get(k) for k in ("log", "plan", "cull", "ok", "filepath")}, f, indent=1)
        except OSError as e:
            print("Texel Coverage: could not write report:", e)
    return body


# ----------------------------------------------------------------------------- compression (chopper)

COMPRESS = {"state": "", "text": ""}  # last compression, shown in the sidebar
_PENDING = {}


def _chopper_cmd(glb, folder, recipe):
    folder = os.path.abspath(bpy.path.abspath(folder))
    exe = os.path.join(folder, "node_modules", ".bin", "chopper")
    if not os.path.exists(exe):
        raise RuntimeError(f"chopper not found: {exe} (run your package manager's install in {folder})")
    target = os.path.splitext(glb)[0] + ".compressed.glb"
    cmd = [exe, "optimize", glb, target]
    if recipe:
        cmd += ["--recipe", os.path.abspath(bpy.path.abspath(recipe))]
    return cmd, folder, target


def _finish(glb, target, returncode, output):
    if returncode:
        raise RuntimeError("chopper failed: " + output.strip()[-400:])
    out = dict(compressed=target, compressed_bytes=os.path.getsize(target), bytes=os.path.getsize(glb))
    mb = lambda b: f"{b / 2**20:.2f} MB"
    COMPRESS.update(state="ok", text=f"{os.path.basename(glb)}: {mb(out['bytes'])} -> {mb(out['compressed_bytes'])}")
    md = os.path.splitext(glb)[0] + ".texel.md"
    if os.path.exists(md):
        with open(md, "a") as f:
            f.write(f"\nCompressed with chopper: {mb(out['bytes'])} -> {mb(out['compressed_bytes'])} ({target})\n")
    return out


def compress(glb, folder, recipe=""):
    """Run chopper (the studio's GLB compressor) on an exported GLB, writing <name>.compressed.glb next
    to it. `folder` is a project with chopper installed (node_modules/.bin/chopper). Blocking."""
    import subprocess
    cmd, cwd, target = _chopper_cmd(glb, folder, recipe)
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return _finish(glb, target, r.returncode, r.stderr or r.stdout)


def _redraw():
    for w in getattr(bpy.context.window_manager, "windows", []):
        for a in w.screen.areas:
            if a.type == "VIEW_3D":
                a.tag_redraw()


def _compress_when_written():
    """File > Export: the exporter writes the GLB after our last hook. Wait until it is on disk, then
    run chopper in the background and poll it, so Blender stays usable while it compresses."""
    job = _PENDING
    if not job:
        return None
    if time.time() - job["t0"] > 1800:
        if job.get("proc"):
            job["proc"].kill()
        COMPRESS.update(state="error", text="compression timed out")
        _PENDING.clear()
        return None
    try:
        if job.get("proc") is None:
            try:
                st = os.stat(job["path"])
            except OSError:
                st = None
            if st is None or st.st_mtime <= job["since"] or st.st_size != job["size"]:
                job["size"] = st.st_size if st else None  # not written yet, or still growing
                return 0.5
            import subprocess
            import tempfile
            cmd, cwd, job["target"] = _chopper_cmd(job["path"], job["folder"], job["recipe"])
            job["log"] = tempfile.TemporaryFile(mode="w+")
            job["proc"] = subprocess.Popen(cmd, cwd=cwd, stdout=job["log"], stderr=subprocess.STDOUT, text=True)
            return 1.0
        if job["proc"].poll() is None:
            return 1.0
        job["log"].seek(0)
        _finish(job["path"], job["target"], job["proc"].returncode, job["log"].read())
    except Exception as e:
        COMPRESS.update(state="error", text=str(e)[:200])
        print("Texel Coverage:", e)
    if job.get("log"):
        job["log"].close()
    _PENDING.clear()
    _redraw()
    return None


def schedule_compress(path, props):
    try:
        since = os.stat(path).st_mtime
    except OSError:
        since = 0
    if _PENDING.get("proc") and _PENDING["proc"].poll() is None:
        _PENDING["proc"].kill()  # a newer export replaces the one still compressing
    _PENDING.clear()
    _PENDING.update(path=path, since=since, size=-1, t0=time.time(), folder=props.chopper_path,
                    recipe=props.chopper_recipe, proc=None)
    COMPRESS.update(state="running", text=f"compressing {os.path.basename(path)}…")
    if not bpy.app.timers.is_registered(_compress_when_written):
        bpy.app.timers.register(_compress_when_written, first_interval=0.5)


# ----------------------------------------------------------------------------- exporter callbacks

def _fallback_restore():
    if _ACTIVE.get("pending"):
        _ACTIVE["pending"] = False
        restore(bpy.context)
        print("Texel Coverage: export did not finish, restored the scene")
    return None


def pre_export(export_settings):
    props = bpy.context.scene.texel_coverage
    if _MANAGED["on"] or not props.export_enabled:
        return
    state = prepare(bpy.context, props, export_settings)
    _ACTIVE["pending"] = True
    if not bpy.app.background:
        bpy.app.timers.register(_fallback_restore, first_interval=1.0)
    return state


def post_export(export_settings):
    props = bpy.context.scene.texel_coverage
    if not _MANAGED["on"] and props.export_compress and props.chopper_path and not bpy.app.background:
        path = export_settings.get("gltf_filepath", "")
        if path.lower().endswith(".glb"):
            schedule_compress(path, props)
    if _MANAGED["on"] or not props.export_enabled or not _ACTIVE.get("pending"):
        return
    _ACTIVE["pending"] = False
    state = dict(_ACTIVE)
    restore(bpy.context)
    write_report(state, props)


# ----------------------------------------------------------------------------- headless / scripted

def gltf_kwargs(scene, collection=None):
    """The file's saved glTF export settings (collection exporter first, then the scene's)."""
    keys = bpy.ops.export_scene.gltf.get_rna_type().properties.keys()
    kw = {}
    if collection is not None:
        for ex in getattr(collection, "exporters", []):
            if "gltf" in ex.name.lower() or "gltf" in str(getattr(ex, "export_properties", "")).lower():
                ep = ex.export_properties
                kw = {k: getattr(ep, k) for k in keys if hasattr(ep, k) and k != "filepath"}
                break
    if not kw and "glTF2ExportSettings" in scene:
        kw = {k: v for k, v in scene["glTF2ExportSettings"].to_dict().items() if k in keys}
    return kw


def run(context, filepath, collection=None, **overrides):
    """Optimize, export with the file's glTF settings, restore. Returns (report text, state)."""
    props = context.scene.texel_coverage
    kw = gltf_kwargs(context.scene, collection)
    kw.update(filepath=filepath, export_format="GLB")
    cams = core.cameras_of(context, props) if props.export_cameras else []
    kw["export_cameras"] = bool(cams)   # the switch decides, whatever the file's saved glTF settings say
    if collection is not None:
        names = set(collection.all_objects.keys()) | {c.name for c in cams}  # before selection changes
        for o in context.view_layer.objects:
            o.select_set(o.name in names)
        kw["use_selection"] = True
    kw.update(overrides)
    _MANAGED["on"] = True
    state = None
    stored = props.collection
    if collection is not None and collection != stored:
        props.collection = collection  # measure and optimize what is actually exported
    try:
        if props.export_enabled:
            state = prepare(context, props, dict(gltf_filepath=filepath))
            if props.export_cull or props.export_triangulate:
                kw["export_apply"] = True
        bpy.ops.export_scene.gltf(**kw)
    finally:
        _MANAGED["on"] = False
        restore(context)
        if props.collection != stored:
            props.collection = stored
    body = write_report(state, props) if state else "optimization disabled"
    return body, state
