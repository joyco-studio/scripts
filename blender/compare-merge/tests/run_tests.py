"""Headless tests. Run: ./tests/run (or blender -b --factory-startup --python tests/run_tests.py)."""

import os
import subprocess
import sys
import tempfile
import traceback

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fixtures  # noqa: E402
from compare_merge import api, diff, preview, session as ses  # noqa: E402
from compare_merge.preview import View  # noqa: E402

TMP = tempfile.mkdtemp(prefix="cm_test_")
CUR = os.path.join(TMP, "final", "scene.blend")
INC = os.path.join(TMP, "artist", "scene_v2.blend")
FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def entry(cat, name):
    return next((i for i, e in enumerate(ses.S.entries) if e.cat == cat and name in (e.current, e.incoming)), None)


def status(cat, name):
    i = entry(cat, name)
    return ses.S.entries[i].status if i is not None else None


def runtime_leftovers():
    return [i.name_full for i in ses.all_ids() if i.is_runtime_data] + [l.name for l in bpy.data.libraries]


def reopen_check(path, code):
    """Open `path` in a fresh Blender and evaluate `code` (prints RESULT ...)."""
    out = subprocess.run([bpy.app.binary_path, "-b", path, "--factory-startup", "--python-expr", code],
                         capture_output=True, text=True).stdout
    return next((l[7:] for l in out.splitlines() if l.startswith("RESULT ")), "")


def test_diff():
    print("diff")
    api.start(INC)
    check(status("OBJECT", "Cube") == diff.CHANGED, "Cube changed")
    e = ses.S.entries[entry("OBJECT", "Cube")]
    check(any(f == "location" for f, _a, _b in e.changes), "Cube location listed")
    check(status("MESH", "CubeMesh") == diff.CHANGED, "CubeMesh changed (vertex moved)")
    check("1 of 8 vertices moved" in api.mesh_stats(ses.S.entries[entry("MESH", "CubeMesh")]), "mesh stats")
    check(status("MATERIAL", "Wood") == diff.CHANGED, "Wood changed (roughness)")
    w = ses.S.entries[entry("MATERIAL", "Wood")]
    check(any("Roughness" in f for f, _a, _b in w.changes), "roughness field")
    check(status("CAMERA", "Cam") == diff.CHANGED, "camera lens changed")
    check(status("IMAGE", "Paint") == diff.CHANGED, "image content changed")
    check(status("MATERIAL", "Paint") == diff.SAME, "Paint material same")
    check(status("OBJECT", "Cone") == diff.ONLY_INCOMING, "Cone only incoming")
    check(status("OBJECT", "OnlyCurrent") == diff.ONLY_CURRENT, "OnlyCurrent only current")
    check(status("OBJECT", "Rock0") == diff.SAME, "Rock0 same")
    check(status("SCENE", "Scene") == diff.CHANGED, "scene frame range changed")
    check(status("OBJECT", "Rock1") == diff.RENAMED and status("OBJECT", "Boulder") == diff.RENAMED, "Rock1 → Boulder rename")
    cam = ses.S.entries[entry("CAMERA", "Cam")]
    check([f for f, _a, _b in cam.changes] == ["lens"], f"camera: only lens listed ({[c[0] for c in cam.changes]})")
    check(status("ACTION", "CubeAction") == diff.CHANGED, "action changed")
    print(api.text_report(ses.S.entries, limit=3))


def test_preview_is_safe():
    print("preview")
    sc = preview.preview_scene()
    check(sc is not None and sc.is_runtime_data, "preview scene is runtime data")
    check(not [o for o in bpy.data.objects if ses.is_draft(o)], "all-current: no preview objects")
    cube = bpy.data.objects["Cube", None]
    api.set_choice(entry("OBJECT", "Cube"), "INCOMING")
    check(View.choices.get(entry("MESH", "CubeMesh")) == "INCOMING", "cascade to mesh")
    check(View.choices.get(entry("MATERIAL", "Wood")) == "INCOMING", "cascade to material")
    api.set_choice(entry("OBJECT", "Cone"), "INCOMING")
    check(View.choices.get(entry("OBJECT", "ConeTip")) == "INCOMING", "new children come along")
    api.set_choice(entry("IMAGE", "Paint"), "INCOMING")
    api.set_choice(entry("OBJECT", "OnlyCurrent"), "INCOMING")
    preview.rebuild()
    vl = sc.view_layers[0]
    check(cube.hide_get(view_layer=vl), "current Cube hidden in preview")
    check(not cube.hide_get(view_layer=bpy.context.scene.view_layers[0]), "current Cube visible in the file's scene")
    shown = [o for o in sc.objects if o.visible_get(view_layer=vl)]
    names = sorted(o.name_full for o in shown)
    print("   shown:", names)
    check(any(o.name == "Cube" and o.library is not None for o in shown), "incoming Cube shown")
    check(any(o.name.startswith("Child") and ses.is_draft(o) for o in shown), "Child re-parented preview copy")
    child = next(o for o in shown if o.name.startswith("Child"))
    check(child.parent is not None and child.parent.library is not None, "Child follows the incoming Cube")
    check(any(o.name == "Cone" for o in shown), "Cone shown")
    check(not any(o.name == "OnlyCurrent" for o in shown), "removed object hidden")
    rock = next(o for o in shown if o.name.startswith("Rock0"))
    img = rock.material_slots[0].material.node_tree.nodes["Image Texture"].image
    check(img.library is not None, "Rock shows the incoming image through a preview material")
    check(tuple(cube.location) == (0, 0, 0), "real Cube untouched")
    check(bpy.data.objects["Child", None].parent == cube, "real Child parent untouched")
    check(bpy.data.materials["Wood", None].node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value == 0.5,
          "real material untouched")

    # Focus: ghost of the other version for the selected row.
    View.active, View.focus = entry("MATERIAL", "Wood"), "GHOST"
    preview.rebuild()
    check(len(View.ghost_objects) >= 1, f"ghost for Wood users ({View.ghost_objects})")
    View.focus = "BLINK"
    preview.rebuild()
    preview.blink_tick()
    check(View.blink_on, "blink toggles")
    View.focus, View.active = "OFF", -1
    preview.rebuild()

    # Saving mid-preview writes only the real data.
    saved = os.path.join(TMP, "saved_mid_preview.blend")
    bpy.ops.wm.save_as_mainfile(filepath=saved, copy=True)
    res = reopen_check(saved, "import bpy; print('RESULT', [s.name for s in bpy.data.scenes], len(bpy.data.libraries), "
                              "sorted(o.name for o in bpy.data.objects), tuple(bpy.data.objects['Cube'].location))")
    print("   reopened:", res)
    check(res.startswith("['Scene'] 0 ['Camera', 'Child', 'Cube', 'OnlyCurrent', 'Rock0', 'Rock1'] (0.0, 0.0, 0.0)"),
          "file saved mid-preview has no preview data and no incoming changes")


def test_views():
    print("views")
    View.mode = "INCOMING"
    preview.rebuild()
    sc = preview.preview_scene()
    vl = sc.view_layers[0]
    shown = sorted(o.name for o in sc.objects if o.visible_get(view_layer=vl))
    check("OnlyCurrent" not in shown and "Cone" in shown, f"all-incoming view {shown}")
    check(sc.frame_end == 120, "incoming frame range in preview")
    View.mode = "CURRENT"
    preview.rebuild()
    shown = sorted(o.name for o in sc.objects if o.visible_get(view_layer=vl))
    check("OnlyCurrent" in shown and "Cone" not in shown, f"all-current view {shown}")
    check(not [o for o in bpy.data.objects if ses.is_draft(o)], "all-current view has no copies")
    View.mode = "MERGE"
    preview.rebuild()


def test_merge():
    print("merge")
    api.set_choice(entry("SCENE", "Scene"), "INCOMING")
    api.set_choice(entry("OBJECT", "Boulder"), "INCOMING")
    report = api.merge()
    print("  ", report)
    cube = bpy.data.objects["Cube", None]
    check(cube.library is None and abs(cube.matrix_basis.translation.x - 1) < 1e-6 or
          abs(cube.location.x - 1) < 1e-6, "Cube has the incoming location")
    check(abs(cube.data.vertices[0].co.x - (-0.5)) < 1e-6 or abs(cube.data.vertices[0].co.x - 1.5) < 1e-6,
          "CubeMesh vertex moved")
    rough = bpy.data.materials["Wood", None].node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value
    check(abs(rough - 0.2) < 1e-6, "Wood roughness merged")
    check(bpy.data.cameras["Cam", None].lens == 50, "camera not chosen: kept")
    check("Cone" in bpy.data.objects and "ConeTip" in bpy.data.objects, "Cone and child added")
    check(bpy.data.objects["Cone", None].name in bpy.data.collections["Props"].objects, "Cone in Props")
    check("OnlyCurrent" not in [o.name for o in bpy.data.objects if o.library is None], "OnlyCurrent removed")
    check(bpy.data.objects["Child", None].parent == cube, "Child parented to the merged Cube")
    names = [i.name for i in ses.all_ids() if i.library is None and not i.is_runtime_data]
    check(not [n for n in names if n.endswith(".001") or n.startswith("cm~") or "(incoming)" in n],
          f"no duplicate names ({[n for n in names if '.0' in n or 'cm~' in n]})")
    img = bpy.data.images["Paint", None]
    path = bpy.path.abspath(img.filepath)
    check(os.path.normpath(path).startswith(os.path.join(TMP, "artist")), f"merged image points at the artist's file ({img.filepath})")
    check(bpy.context.scene.frame_end == 120, "scene settings merged")
    check(status("OBJECT", "Cube") == diff.SAME and status("MATERIAL", "Wood") == diff.SAME, "merged rows are identical now")
    check(status("CAMERA", "Cam") == diff.CHANGED, "unchosen rows still differ")
    check(status("OBJECT", "Camera") == diff.SAME, "preview scene doesn't make top-level objects look changed")
    check("Boulder" in bpy.data.objects and "Rock1" not in bpy.data.objects, "rename merged")
    check(bpy.data.objects["Boulder", None].data.name == "SharedMesh", "renamed object keeps the shared mesh")

    # Undo drops runtime data (the linked file, the preview); the add-on relinks and rebuilds.
    preview.teardown()
    ses.unlink()
    api.refresh()
    check(ses.library() is not None and preview.preview_scene() is not None, "rebuilds after runtime data is dropped")
    meshes = [m.name for m in bpy.data.meshes if m.library is None]
    check(sorted(meshes) == ["ConeMesh", "CubeMesh", "SharedMesh"], f"no leftover meshes {meshes}")
    bpy.ops.ed.undo_push(message="test") if bpy.ops.ed.undo_push.poll() else None


def test_close():
    print("close")
    api.close()
    check(not runtime_leftovers(), f"nothing left after closing ({runtime_leftovers()})")
    out = os.path.join(TMP, "merged.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True)
    res = reopen_check(out, "import bpy; print('RESULT', len(bpy.data.libraries), sorted(o.name for o in bpy.data.objects))")
    check(res == "0 ['Boulder', 'Camera', 'Child', 'Cone', 'ConeTip', 'Cube', 'Rock0']", f"merged file reopens clean ({res})")


def main():
    fixtures.build("incoming", INC)
    fixtures.build("current", CUR)
    bpy.ops.wm.open_mainfile(filepath=CUR)
    for t in (test_diff, test_preview_is_safe, test_views, test_merge, test_close):
        try:
            t()
        except Exception:
            traceback.print_exc()
            FAILS.append(t.__name__ + " raised")
    print(f"\n{len(FAILS)} failure(s)" if FAILS else "\nall tests passed")
    sys.exit(1 if FAILS else 0)


main()
