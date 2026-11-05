"""Texel Coverage command line. Runs inside Blender; use the `texel` wrapper next to this file:

    texel scene.blend status
    texel scene.blend analyze cull --save
    texel scene.blend export --out build/model.glb --json

Commands run in the order given, in one Blender session. Each one calls the same function as the
matching sidebar button (texel_coverage/api.py), so results match what an artist sees.
"""

import argparse
import json
import os
import sys
import traceback

COMMANDS = ("status", "settings", "sync-cameras", "analyze", "cull", "update", "suggest", "promote",
            "plan", "verify", "export", "restore")

HELP = """commands (run in order):
  status         is the stored analysis / cull fresh? scene summary
  settings       print the Texel settings stored in the file
  sync-cameras   run the site camera rig script (--script, --args)
  analyze        measure camera need per face, write the coverage report
  cull           work out which faces no camera sees (--force to recompute)
  update         analyze and cull only if they are out of date
  suggest        write <uv>_tc_suggest maps sized by camera need (--objects)
  promote        copy suggested maps into the render UV maps (--objects)
  plan           texture sizes an export would use, without exporting
  verify         render every camera moment with and without the cull (--no-nudge, --max-diff-px)
  export         optimized GLB with the file's glTF settings (--out, --collection, --plain, --chopper)
  restore        clean up after an interrupted export

exit codes: 0 ok, 1 error, 2 a check failed (verify over --max-diff-px, status --check not fresh)"""


def parser():
    ap = argparse.ArgumentParser(prog="texel", description="Texel Coverage from the command line",
                                 epilog=HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("commands", nargs="+", choices=COMMANDS, metavar="command")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a stored setting for this run (repeatable), e.g. --set cull_preset=TIGHT")
    ap.add_argument("--save", action="store_true", help="save the .blend afterwards (settings, caches, UVs)")
    ap.add_argument("--save-as", metavar="PATH", help="save to another .blend instead")
    ap.add_argument("--json", action="store_true", help="print only JSON on stdout")
    ap.add_argument("--out-json", metavar="PATH", help="also write the JSON result to this file")
    ap.add_argument("--quiet", action="store_true", help="one line per command, no reports")
    g = ap.add_argument_group("command options")
    g.add_argument("--script", help="sync-cameras: rig script path")
    g.add_argument("--args", help="sync-cameras: arguments for the rig script")
    g.add_argument("--force", action="store_true", help="cull: recompute even if fresh")
    g.add_argument("--objects", help="suggest/promote: comma-separated object names")
    g.add_argument("--no-nudge", action="store_true", help="verify: skip the nudged-camera tests")
    g.add_argument("--max-diff-px", type=int, default=None, help="verify: fail above this many changed pixels")
    g.add_argument("--out", help="export: GLB path")
    g.add_argument("--collection", help="export: collection to export (default: the Texel collection setting)")
    g.add_argument("--plain", action="store_true", help="export: no optimization, just the stock export")
    g.add_argument("--chopper", metavar="REPO", help="export: also run chopper from this repo")
    g.add_argument("--recipe", help="export: chopper recipe JSON")
    g.add_argument("--check", action="store_true", help="status: exit 2 unless analysis and cull are fresh")
    return ap


def load_package():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import bpy
    import texel_coverage
    if not hasattr(bpy.types.Scene, "texel_coverage"):
        texel_coverage.register()
    from texel_coverage import api
    return api


def run(a, api):
    import bpy
    context = bpy.context
    results, code = [], 0
    for kv in a.set:
        key, _, val = kv.partition("=")
        api.set_setting(context, key.strip(), val.strip())
    objects = [n.strip() for n in a.objects.split(",")] if a.objects else None
    for cmd in a.commands:
        if cmd == "status":
            r = api.status(context)
            if a.check and (r["data"]["analysis"] != "fresh" or r["data"]["cull"] != "fresh"):
                r["ok"], code = False, max(code, 2)
        elif cmd == "settings":
            s = api.settings(context)
            r = dict(command="settings", ok=True, message=f"{len(s)} settings", data=s, warnings=[],
                     meta=dict(version=api.VERSION, file=bpy.data.filepath))
        elif cmd == "sync-cameras":
            r = api.sync_cameras(context, a.script, a.args)
        elif cmd == "analyze":
            r = api.analyze(context)
        elif cmd == "cull":
            r = api.cull_compute(context, force=a.force)
        elif cmd == "update":
            r = api.prepare(context)
        elif cmd == "suggest":
            r = api.suggest(context, objects)
        elif cmd == "promote":
            r = api.promote(context, objects)
        elif cmd == "plan":
            r = api.plan(context)
        elif cmd == "verify":
            r = api.verify(context, nudge=not a.no_nudge,
                           max_diff_px=a.max_diff_px if a.max_diff_px is not None else 0)
            if not r["ok"] and a.max_diff_px is not None:
                code = max(code, 2)
            elif a.max_diff_px is None:
                r["ok"] = True  # report only; pass --max-diff-px to make it a check
        elif cmd == "export":
            if not a.out:
                raise api.TexelError("export needs --out PATH")
            context.scene.texel_coverage.export_enabled = not a.plain
            col = None
            if a.collection:
                col = bpy.data.collections.get(a.collection)
                if col is None:
                    raise api.TexelError(f"no collection named {a.collection!r}")
            r = api.export_glb(context, a.out, col, a.chopper, a.recipe)
        else:
            r = api.restore(context)
        results.append(r)
    if a.save or a.save_as:
        api.restore(context)  # never save temporary export state
        if a.save_as:
            bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.save_as), copy=True)
        else:
            bpy.ops.wm.save_mainfile()
    return results, code


def human(results, quiet):
    out = []
    for r in results:
        mark = "ok  " if r["ok"] else "FAIL"
        secs = r.get("meta", {}).get("seconds")
        out.append(f"[{mark}] {r['command']}: {r['message']}" + (f"  ({secs}s)" if secs is not None else ""))
        for w in r.get("warnings", []):
            out.append(f"       warning: {w}")
        if quiet:
            continue
        d = r.get("data", {})
        if r["command"] in ("status", "settings"):
            out += [f"       {k}: {v}" for k, v in d.items()]
        elif d.get("report"):
            out += ["", d["report"], ""]
    return "\n".join(out)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = parser()
    if not argv or "-h" in argv or "--help" in argv:
        emit(ap.format_help())
        os._exit(0)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:  # argparse already printed the error to stderr
        emit("")
        os._exit(e.code or 1)
    api = load_package()
    try:
        results, code = run(a, api)
    except api.TexelError as e:
        results, code = [dict(command="error", ok=False, message=str(e), data={}, warnings=[], meta={})], 1
    except Exception as e:
        traceback.print_exc()
        results, code = [dict(command="error", ok=False, message=f"{type(e).__name__}: {e}", data={},
                              warnings=[], meta={})], 1
    doc = dict(ok=code == 0 and all(r["ok"] for r in results), exit_code=code, results=results)
    text = json.dumps(doc, indent=2, sort_keys=True, default=str)
    if a.out_json:
        with open(a.out_json, "w") as f:
            f.write(text + "\n")
    emit(text if a.json else human(results, a.quiet))
    os._exit(code)  # Blender ignores SystemExit codes from --python scripts


def emit(rendered):
    target = os.environ.get("TEXEL_RESULT")
    if target:
        with open(target, "w") as f:
            f.write(rendered + "\n")
    else:
        print(rendered)
    sys.stdout.flush()  # Blender ignores SystemExit codes from --python scripts


if __name__ == "__main__":
    main()
