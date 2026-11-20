"""Headless tests. Run: ./tests/run (or blender -b --factory-startup --python tests/run_tests.py)."""

import os
import subprocess
import sys
import tempfile
import traceback

import bpy
from mathutils import Vector

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


RCUR = os.path.join(TMP, "review", "final.blend")
RINC = os.path.join(TMP, "review", "artist.blend")


def changed_fields(cat, name):
    i = entry(cat, name)
    return [f for f, _a, _b in ses.S.entries[i].changes] if i is not None else []


def test_review():
    """Regression tests for the code-review findings on the first version."""
    print("review findings")
    bpy.ops.wm.open_mainfile(filepath=RCUR)
    api.start(RINC)
    check(status("LIGHT", "Lamp") == diff.CHANGED and "color" in changed_fields("LIGHT", "Lamp"), "light color change")
    check(status("MESH", "SkinMesh") == diff.CHANGED and "vertex weights" in changed_fields("MESH", "SkinMesh"),
          "weight moved to another vertex")
    check(status("CURVE", "Path") == diff.CHANGED, "curve handle moved")
    check(status("ACTION", "WigglerAction") == diff.CHANGED, "F-curve modifier settings")
    check(status("MATERIAL", "Mathy") == diff.CHANGED, "swapped links into same-named sockets")
    check(status("NODETREE", "Tint") == diff.CHANGED and status("MATERIAL", "Tinted") == diff.SAME, "node group change")
    check(status("SCENE", "Scene") == diff.CHANGED and
          {"resolution", "unit scale", "camera", "world"} <= set(changed_fields("SCENE", "Scene")), "scene fields")

    # Close + compare again: every module must see the new comparison (reset clears in place).
    api.close()
    api.start(RINC)
    check(preview.S is ses.S and preview.preview_scene() is not None and ses.S.entries, "compare again after close")

    user = bpy.data.collections["Compare Preview", None]
    check(not user.is_runtime_data and "UserThing" in user.objects, "user collection named like the preview")

    # Node group picked on its own: the preview shows it through a runtime copy of the material.
    api.set_choice(entry("NODETREE", "Tint"), "INCOMING")
    preview.rebuild()
    sc = preview.preview_scene()
    vl = sc.view_layers[0]
    ball = next(o for o in sc.objects if o.name.startswith("TintBall") and o.visible_get(view_layer=vl))
    grp = ball.material_slots[0].material.node_tree.nodes["Group"]
    check(ball.material_slots[0].material.is_runtime_data and grp.node_tree.library is not None,
          "preview shows the picked node group")

    # Adding a new parent adds its new children and what they need (the child's Boolean cutter).
    api.set_choice(entry("OBJECT", "Holder"), "INCOMING")
    check(View.choices.get(entry("OBJECT", "Drilled")) == "INCOMING" and
          View.choices.get(entry("OBJECT", "Cutter")) == "INCOMING", "new children bring what they need")
    for n in ("Holder", "Drilled", "Cutter"):
        View.choices.pop(entry("OBJECT", n), None)

    # A bone-parented new child whose new armature is skipped keeps its place in the world.
    kid_world = Vector((0, 5, 2))  # NewRig at (0, 5, 0), its bone's tail 2 up
    View.choices[entry("OBJECT", "BoneKid")] = "INCOMING"

    # A new child needs its new parent; Skip on the parent is honoured by preview and merge alike.
    child_i, parent_i = entry("OBJECT", "NewChild"), entry("OBJECT", "NewParent")
    api.set_choice(child_i, "INCOMING")
    check(View.choices.get(parent_i) == "INCOMING", "adding a new child adds its new parent")
    View.choices[parent_i] = "CURRENT"
    preview.rebuild()
    vl.update()
    shown = next(o for o in sc.objects if o.get("cm_src", o.name) == "NewChild" and o.visible_get(view_layer=vl))
    check((shown.matrix_world.translation - Vector((5, 0, 1))).length < 1e-5 and
          not any(o.name == "NewParent" and o.visible_get(view_layer=vl) for o in sc.objects),
          f"preview: skipped parent hidden, child in place ({tuple(shown.matrix_world.translation)})")

    api.set_choice(entry("OBJECT", "Nested"), "INCOMING")
    api.set_choice(entry("SCENE", "Scene"), "INCOMING")
    print("  ", api.merge())

    check("UserThing" in bpy.data.collections["Compare Preview", None].objects, "user collection untouched by the preview")
    child = bpy.data.objects.get(("NewChild", None))
    check(child is not None and child.parent is None and (child.matrix_world.translation - Vector((5, 0, 1))).length < 1e-5,
          "merge: child added, skipped parent left out, world position kept")
    check(bpy.data.objects.get(("NewParent", None)) is None, "merge: skipped parent not added")
    kid = bpy.data.objects.get(("BoneKid", None))
    check(kid is not None and kid.parent is None and bpy.data.objects.get(("NewRig", None)) is None and
          (kid.matrix_world.translation - kid_world).length < 1e-5,
          f"bone-parented child keeps its world position ({kid and tuple(kid.matrix_world.translation)} vs {tuple(kid_world)})")
    outer, inner = bpy.data.collections.get(("Outer", None)), bpy.data.collections.get(("Inner", None))
    check(outer is not None and inner is not None and inner.name in outer.children and outer.name in
          bpy.context.scene.collection.children and inner.hide_render, "new nested collections keep their tree")
    scene = bpy.context.scene
    check(scene.camera is not None and scene.camera.name == "Cam2", f"scene merge brings its new camera ({scene.camera})")
    check(scene.world is not None and scene.world.name == "Sky", "scene merge brings its new world")
    check(scene.render.resolution_percentage == 50 and abs(scene.unit_settings.scale_length - 0.01) < 1e-9,
          "scene merge applies resolution % and unit scale")
    check(status("SCENE", "Scene") == diff.SAME, "scene identical after merge")

    check(bool(api._before_merge), "picks saved for undo")
    api.refresh()
    check(bool(api._before_merge), "a Refresh keeps the picks an undo would bring back")
    api.close()


def test_failed_merge_rolls_back():
    """An error halfway through a merge leaves no temporary names and nothing appended behind."""
    print("failed merge")
    from compare_merge import merge
    bpy.ops.wm.open_mainfile(filepath=CUR)
    api.start(INC)
    api.set_choice(entry("OBJECT", "Cube"), "INCOMING")
    api.set_choice(entry("OBJECT", "Cone"), "INCOMING")
    before = sorted(i.name_full for i in ses.all_ids() if i.library is None and not i.is_runtime_data)
    real_attr_of, calls = merge.attr_of, {"n": 0}

    def boom(idb):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated failure after append")
        return real_attr_of(idb)
    merge.attr_of = boom
    try:
        api.merge()
        check(False, "merge should have raised")
    except RuntimeError:
        pass
    finally:
        merge.attr_of = real_attr_of
    after = sorted(i.name_full for i in ses.all_ids() if i.library is None and not i.is_runtime_data)
    check(after == before and not any(n.startswith("cm~") for n in after),
          f"rolled back: same data-blocks and names ({set(after) ^ set(before)})")
    api.recover_after_failed_merge()
    check(ses.library() is not None and preview.preview_scene() is not None and View.choices,
          "comparison and picks survive a failed merge")
    api.close()


def test_scene_rename():
    """Merging a rename of the compared scene keeps the preview alive, before and after undo."""
    print("scene rename")
    renamed = os.path.join(TMP, "renamed", "shot.blend")
    fixtures.build("current", renamed)
    bpy.context.scene.name = "Shot"
    bpy.ops.wm.save_as_mainfile(filepath=renamed)
    bpy.ops.wm.open_mainfile(filepath=CUR)
    api.start(renamed)
    i = entry("SCENE", "Scene")
    check(ses.S.entries[i].status == diff.RENAMED, "scene rename detected")
    api.set_choice(i, "INCOMING")
    api.merge()
    check(bpy.context.scene.name == "Shot" and ses.S.scene == "Shot", "scene takes the incoming name")
    check(preview.preview_scene() is not None and preview.original_scene() is not None, "preview follows the rename")
    bpy.data.scenes["Shot", None].name = "Scene"  # what undoing the merge does
    api.refresh(after_undo=True)
    check(ses.S.scene == "Scene" and preview.preview_scene() is not None, "preview survives undoing the rename")
    api.close()


def test_operator_undo():
    """The real Merge operator, a real undo and redo: data and picks follow."""
    print("operator undo / redo")
    import compare_merge
    from compare_merge import ui
    compare_merge.register()
    try:
        bpy.ops.wm.open_mainfile(filepath=CUR)
        api.start(INC)
        cube_i = entry("OBJECT", "Cube")
        api.set_choice(cube_i, "INCOMING")
        ui.sync_rows()
        bpy.ops.ed.undo_push(message="picked")
        check(bpy.ops.compare_merge.merge("EXEC_DEFAULT") == {"FINISHED"}, "operator merged")
        bpy.ops.ed.undo_push(message="Merge")  # the UI records this for UNDO operators; background doesn't
        x = lambda: round(bpy.data.objects["Cube", None].location.x, 4)
        check(x() == 1.0, "merged Cube location")
        bpy.ops.ed.undo()
        compare_merge._after_undo()  # what the timer runs after undo_post in the UI
        check(x() == 0.0, "undo restores the file's Cube")
        check(View.choices.get(entry("OBJECT", "Cube")) == "INCOMING", "undo brings the picks back")
        check(ses.library() is not None and preview.preview_scene() is not None, "undo: preview rebuilt")
        bpy.ops.ed.redo()
        compare_merge._after_undo()
        check(x() == 1.0 and status("OBJECT", "Cube") == diff.SAME, "redo merges again")
        api.close()
    finally:
        compare_merge.unregister()


def main():
    fixtures.build("incoming", INC)
    fixtures.build("current", CUR)
    fixtures.build_review("incoming", RINC)
    fixtures.build_review("current", RCUR)
    bpy.ops.wm.open_mainfile(filepath=CUR)
    for t in (test_diff, test_preview_is_safe, test_views, test_merge, test_close, test_review,
              test_failed_merge_rolls_back, test_scene_rename, test_operator_undo):
        try:
            t()
        except Exception:
            traceback.print_exc()
            FAILS.append(t.__name__ + " raised")
    print(f"\n{len(FAILS)} failure(s)" if FAILS else "\nall tests passed")
    sys.exit(1 if FAILS else 0)


main()
