"""One function per action. The sidebar buttons and the `texel` command line both call these, so
people and agents get the same numbers, the same text reports and the same files.

Every function returns a plain dict:

    {"command": ..., "ok": bool, "message": one line for people,
     "data": {...}  (deterministic: no times, sorted keys, rounded floats),
     "meta": {...}  (version, file, seconds, effective settings; changes run to run),
     "warnings": [...]}

Errors raise TexelError with a message meant for the person or agent that called it.
"""

import os
import runpy
import shlex
import sys
import time

import bpy

from . import bake, core, cull, export, uv_suggest



def _manifest_version():
    import os
    import tomllib
    with open(os.path.join(os.path.dirname(__file__), "blender_manifest.toml"), "rb") as f:
        return tomllib.load(f)["version"]


VERSION = _manifest_version()  # single source: blender_manifest.toml (synced from package.json)


class TexelError(RuntimeError):
    pass


SETTING_KEYS = (
    "res_x", "res_y", "camera_mode", "use_frames", "frame_step", "rig_script", "rig_args", "occlusion",
    "target_ratio", "time_quantile", "quantile", "split_ratio", "unseen_scale", "pack_margin_px",
    "suggest_fill", "suggest_snap", "cull_preset", "cull_shift", "cull_overscan", "cull_rings",
    "cull_distance", "cull_pixel", "cull_pixel_scale", "cull_skip", "export_enabled", "export_cull",
    "export_resize", "export_triangulate", "export_max_size", "export_min_size", "export_stale", "export_report",
    "export_compress", "chopper_path", "chopper_recipe",
)


def settings(context):
    p = context.scene.texel_coverage
    out = {k: getattr(p, k) for k in SETTING_KEYS}
    out["collection"] = p.collection.name if p.collection else None
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in out.items()}


def set_setting(context, key, raw):
    """Set one setting from a string (command line --set key=value)."""
    p = context.scene.texel_coverage
    if key == "collection":
        if raw in ("", "none", "None"):
            p.collection = None
            return
        col = bpy.data.collections.get(raw)
        if col is None:
            raise TexelError(f"no collection named {raw!r}")
        p.collection = col
        return
    if key not in SETTING_KEYS:
        raise TexelError(f"unknown setting {key!r}. Known: collection, {', '.join(SETTING_KEYS)}")
    cur = getattr(p, key)
    if isinstance(cur, bool):
        val = raw.lower() in ("1", "true", "yes", "on")
    elif isinstance(cur, int):
        val = int(raw)
    elif isinstance(cur, float):
        val = float(raw)
    else:
        val = raw
    try:
        setattr(p, key, val)
    except TypeError as e:
        raise TexelError(f"bad value for {key}: {e}")


def _result(command, ok, message, data, t0, context, warnings=()):
    return dict(command=command, ok=bool(ok), message=message, data=data, warnings=list(warnings),
                meta=dict(version=VERSION, blender=bpy.app.version_string, file=bpy.data.filepath,
                          seconds=round(time.time() - t0, 1), settings=settings(context)))


def _require_analysis(context):
    if core.cache_status(context, context.scene.texel_coverage, force=True)["analysis"] == "none":
        raise TexelError("no analysis yet: run `analyze` first")


# ----------------------------------------------------------------------------- actions

def status(context):
    t0 = time.time()
    p = context.scene.texel_coverage
    st = core.cache_status(context, p, force=True)
    cache = core.load_cache(context.scene)
    objs = core.target_objects(context)
    cams = core.cameras_of(context, p)
    data = dict(analysis=st["analysis"], cull=st["cull"], meshes=len(objs), cameras=len(cams),
                frames=len(core.frames_of(context.scene, p)),
                moments=len(cache.get("analysis", {}).get("labels", [])),
                faces_kept=sum(v[0] for v in cache.get("cull", {}).get("stats", {}).values()),
                faces_total=sum(v[1] for v in cache.get("cull", {}).get("stats", {}).values()),
                leftovers=bool(context.scene.get(export.STATE_KEY))
                or any(i.get(export.COPY_TAG) for i in bpy.data.images))
    msg = f"analysis {st['analysis']}, cull {st['cull']}, {len(objs)} meshes, {len(cams)} cameras"
    return _result("status", True, msg, data, t0, context)


def sync_cameras(context, script=None, args=None):
    t0 = time.time()
    p = context.scene.texel_coverage
    if script is not None:
        p.rig_script = script
    if args is not None:
        p.rig_args = args
    path = bpy.path.abspath(p.rig_script)
    if not os.path.isfile(path):
        raise TexelError("rig script not found: set rig_script (sidebar or --set rig_script=PATH)")
    argv = sys.argv
    sys.argv = [argv[0], "--"] + shlex.split(p.rig_args)
    try:
        runpy.run_path(path, run_name="__main__")
    except SystemExit:
        pass
    except Exception as e:
        raise TexelError(f"rig script failed: {e}")
    finally:
        sys.argv = argv
    p.use_frames, p.frame_step, p.camera_mode = True, 1, "ALL"
    core.mark_dirty()
    cams = core.cameras_of(context, p)
    data = dict(cameras=sorted(c.name for c in cams), frames=len(core.frames_of(context.scene, p)))
    return _result("sync-cameras", True, f"rebuilt site cameras: {len(cams)} cameras, {data['frames']} frames. "
                   "Analysis and cull are now out of date", data, t0, context)


def analyze(context):
    t0 = time.time()
    p = context.scene.texel_coverage
    try:
        results, cams, frames, labels = core.analyze(context, p)
    except RuntimeError as e:
        raise TexelError(str(e))
    body, summary = core.build_report(results, cams, frames, p, labels)
    core.write_text("Texel Coverage Report", body)
    data = dict(meshes=len(results), moments=len(labels), textures=summary, report=body)
    return _result("analyze", True, f"analyzed {len(results)} meshes over {len(labels)} camera moments",
                   data, t0, context)


def cull_compute(context, force=False, preview=False):
    t0 = time.time()
    p = context.scene.texel_coverage
    st = core.cache_status(context, p, force=True)
    reused = st["cull"] == "fresh" and not force
    if reused:
        c = core.load_cache(context.scene)["cull"]
        stats, samples = c["stats"], c["samples"]
    else:
        stats, samples = cull.compute(context, p)
    if preview:
        cull.add_modifiers(context, p)
    body = cull.stats_text(stats, samples, p)
    core.write_text("Texel Coverage Cull", body)
    kept = sum(k for k, _ in stats.values())
    total = sum(n for _, n in stats.values())
    data = dict(reused_cache=reused, camera_tests=samples, faces_kept=kept, faces_total=total,
                objects={k: dict(kept=v[0], total=v[1]) for k, v in sorted(stats.items())},
                params=core.cull_params(p), report=body)
    return _result("cull", True, f"{kept}/{total} faces kept ({kept / max(total, 1):.0%})"
                   + (" (cached)" if reused else ""), data, t0, context)


def prepare(context):
    """Bring analysis and cull up to date."""
    t0 = time.time()
    p = context.scene.texel_coverage
    st = core.cache_status(context, p, force=True)
    done = []
    if st["analysis"] != "fresh":
        analyze(context)
        done.append("analysis")
    if st["cull"] != "fresh":
        cull.compute(context, p)
        done.append("cull")
    return _result("update", True, f"updated {' + '.join(done)}" if done else "already up to date",
                   dict(updated=done), t0, context)


def suggest(context, objects=None):
    t0 = time.time()
    _require_analysis(context)
    p = context.scene.texel_coverage
    if objects:
        missing = [n for n in objects if n not in bpy.data.objects]
        if missing:
            raise TexelError(f"no objects named {', '.join(missing)}")
    summaries = uv_suggest.suggest(context, p, objects or None)
    body = uv_suggest.summary_text(summaries)
    core.write_text("Texel UV Suggestion", body)
    for s in summaries:
        s["tiers"] = {str(k): v for k, v in s.get("tiers", {}).items()}
    return _result("suggest", True, f"suggested layouts for {len(summaries)} texture(s)",
                   dict(textures=summaries, report=body), t0, context)


def promote(context, objects=None):
    t0 = time.time()
    objs = [bpy.data.objects[n] for n in objects] if objects else core.target_objects(context)
    done = uv_suggest.promote(context, [o for o in objs if o.type == "MESH"])
    return _result("promote", True, f"promoted {len(done)} UV map(s); re-bake textures into them",
                   dict(promoted=sorted(done)), t0, context)


def bake_textures(context):
    t0 = time.time()
    done, skipped = bake.bake(context, context.scene.texel_coverage)
    if not done and not skipped:
        raise TexelError("nothing to bake: run Suggest UVs first (no *_tc_suggest maps)")
    lines = [f"  {d['image']} -> {d['baked']} {d['size'][0]}x{d['size'][1]}"
             + (" (normal)" if d["normal"] else "") for d in done]
    lines += [f"  skipped {k}: {v}" for k, v in sorted(skipped.items())]
    core.write_text("Texel Bake", "BAKE TO SUGGESTED UVS\n" + "\n".join(lines))
    msg = f"baked {len(done)} texture(s) into the suggested UVs and switched to them"
    if skipped:
        msg += f"; skipped {len(skipped)} (see warnings)"
    return _result("bake", bool(done), msg,
                   dict(baked=done, skipped=[dict(image=k, reason=v) for k, v in sorted(skipped.items())]),
                   t0, context, [f"{k}: {v}" for k, v in sorted(skipped.items())])


def revert_bake(context):
    t0 = time.time()
    images, uvs = bake.revert(context)
    return _result("revert-bake", True, f"restored {images} image node(s) and {uvs} UV map(s)",
                   dict(images=images, uv_maps=uvs), t0, context)


def plan(context):
    t0 = time.time()
    _require_analysis(context)
    p = context.scene.texel_coverage
    entries = export.texture_plan(context, p)
    state = dict(ok=True, plan=entries, cull={}, log=["preview only, nothing exported"])
    body = export.report(state, p)
    core.write_text("Texel Export Report", body)
    shipped = [e for e in entries if e["reason"] != "not used by exported objects"]
    shrunk = [e for e in shipped if e["target"] != e["size"]]
    return _result("plan", True, f"{len(shrunk)} of {len(shipped)} exported textures would shrink",
                   dict(textures=shipped, report=body), t0, context)


def verify(context, nudge=True, max_diff_px=0):
    t0 = time.time()
    p = context.scene.texel_coverage
    if core.cache_status(context, p, force=True)["cull"] == "none":
        raise TexelError("nothing to verify: run `cull` first")
    res = cull.verify(context, p, nudge=nudge)
    if not res:
        raise TexelError("no active camera moments to verify against")
    worst = max((r[1] for r in res), default=0)
    bad = [r for r in res if r[1] > 0]
    lines = [f"VERIFY CULL: {len(res)} renders compared, {len(bad)} with differences", ""]
    lines += [f"  {d:6d} px changed  ({w}px wide)  {label}" for label, d, w in sorted(res, key=lambda r: (-r[1], r[0]))[:40] if d]
    if not bad:
        lines.append("  every render is identical with and without the cull")
    body = "\n".join(lines)
    core.write_text("Texel Verify", body)
    data = dict(renders=len(res), with_differences=len(bad), worst_px=worst, max_allowed_px=max_diff_px,
                differences=[dict(moment=l, px=d, width=w) for l, d, w in sorted(bad, key=lambda r: (-r[1], r[0]))],
                report=body)
    ok = worst <= max_diff_px
    return _result("verify", ok, f"{len(bad)}/{len(res)} renders differ, worst {worst} px"
                   + ("" if ok else f" (over the {max_diff_px} px limit)"), data, t0, context)


def export_glb(context, out, collection=None, chopper=None, recipe=None):
    t0 = time.time()
    p = context.scene.texel_coverage
    col = collection if collection is not None else p.collection
    out = os.path.abspath(bpy.path.abspath(out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    body, state = export.run(context, out, col)
    data = dict(glb=out, bytes=os.path.getsize(out), optimized=bool(state and state.get("ok")),
                log=(state or {}).get("log", []),
                textures=[e for e in (state or {}).get("plan", []) if e["reason"] != "not used by exported objects"],
                cull={k: dict(kept=v[0], total=v[1]) for k, v in sorted((state or {}).get("cull", {}).items())},
                report=body)
    warnings = []
    if chopper is None and p.export_compress and p.chopper_path:
        chopper, recipe = p.chopper_path, recipe or p.chopper_recipe  # the file's own setting
    if chopper:
        try:
            data.update(export.compress(out, chopper, recipe or ""))
        except RuntimeError as e:
            warnings.append(str(e))
    plain = p.export_enabled and not data["optimized"]
    msg = ("exported WITHOUT optimizing (see log) " if plain else "exported ") + f"{out} ({data['bytes'] / 2**20:.1f} MB" + (
        f", compressed {data['compressed_bytes'] / 2**20:.2f} MB" if "compressed" in data else "") + ")"
    return _result("export", data["optimized"] or not p.export_enabled, msg, data, t0, context, warnings)


def restore(context):
    t0 = time.time()
    images, mods = export.restore(context)
    return _result("restore", True, f"restored {images} image/uv item(s) and {mods} modifier change(s)",
                   dict(images=images, modifiers=mods), t0, context)
