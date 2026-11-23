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
from compare_merge import api, apply, diff, preview, session as ses, snapshot  # noqa: E402
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


def test_image_snapshot_ignores_loaded_pixels():
    """An image snapshots the same whether or not Blender has loaded its pixels (the viewport drew it)."""
    print("image snapshot")
    from compare_merge import snapshot
    img = bpy.data.images["Paint", None]
    img.buffers_free()
    before = snapshot.snap_image(img)
    img.pixels[0]  # loads the pixels, like the viewport drawing it
    check(img.has_data, "pixels loaded")
    check(snapshot.snap_image(img) == before, "snapshot unchanged by loading pixels")


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
    check(child.matrix_parent_inverse == bpy.data.objects["Child", None].matrix_parent_inverse,
          "Child keeps its parent inverse under the incoming Cube")
    with bpy.context.temp_override(scene=sc, view_layer=vl):
        bpy.context.evaluated_depsgraph_get().update()
    check((child.matrix_world.translation - Vector((1, 3, 2))).length < 1e-5,
          f"Child sits where the incoming file has it ({tuple(child.matrix_world.translation)})")
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

    def ghost_child(row):
        View.active = row
        preview.rebuild()
        with bpy.context.temp_override(scene=sc, view_layer=vl):
            bpy.context.evaluated_depsgraph_get().update()
        return next((bpy.data.objects[n, None] for n in View.ghost_objects if n.startswith("Child")), None)

    real_mpi = bpy.data.objects["Child", None].matrix_parent_inverse
    # Its parent is another version of the Cube (the incoming one with the current Wood).
    g = ghost_child(entry("MATERIAL", "Wood"))
    check(g is not None and g.parent is not None and ses.is_draft(g.parent) and g.matrix_parent_inverse == real_mpi
          and (g.matrix_world.translation - Vector((1, 3, 2))).length < 1e-5,
          f"ghost Child keeps its offset under a ghost Cube ({g and tuple(g.matrix_world.translation)})")
    # Unchanged itself, it hangs from the parent the main preview shows (here the current Cube).
    cube_parts = ses.dependents(ses.S.entries[entry("OBJECT", "Cube")])  # mesh, Wood, action
    for i in cube_parts:
        View.choices[i] = "CURRENT"
    g = ghost_child(entry("OBJECT", "Cube"))
    check(g is not None and g.parent == cube and g.matrix_parent_inverse == real_mpi
          and (g.matrix_world.translation - Vector((0, 3, 2))).length < 1e-5,
          f"ghost Child keeps its offset under the current Cube ({g and tuple(g.matrix_world.translation)})")
    for i in cube_parts:
        View.choices[i] = "INCOMING"

    View.active = entry("MATERIAL", "Wood")
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
          {"resolution", "unit scale", "camera"} <= set(changed_fields("SCENE", "Scene")), "scene fields")
    # The incoming file no longer uses WorldA, so Blender didn't save it: its identical Sky reads as a
    # rename, and the scene's world, read under its current name, as unchanged. Merge still brings Sky.
    check(status("WORLD", "WorldA") == diff.RENAMED and "world" not in changed_fields("SCENE", "Scene"),
          "a renamed world shows on its own row, not on the scene")

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
        groups = ui.merge_overview()
        names = {(ses.S.entries[i].cat, ses.S.entries[i].name) for i in groups[diff.CHANGED]}
        check({("OBJECT", "Cube"), ("MESH", "CubeMesh"), ("MATERIAL", "Wood")} <= names and
              not groups[diff.ONLY_INCOMING] and not groups[diff.ONLY_CURRENT], f"merge overview lists the picks ({names})")
        check("location" in ui.change_summary(ses.S.entries[cube_i]), "overview says what changed on the Cube")
        p = ui.props()
        p.search = "nothing matches this"
        check(bpy.ops.compare_merge.show_row(index=cube_i) == {"FINISHED"} and p.active == cube_i and not p.search,
              "clicking an overview row selects it and clears a search that hid it")
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


UCUR = os.path.join(TMP, "units", "final.blend")
UINC = os.path.join(TMP, "units", "artist.blend")


def unit_keys(cat, name, locked=None):
    from compare_merge import apply
    return {u.key for u in apply.units(entry(cat, name)) if locked is None or bool(u.locked) == locked}


def arrays(me):
    """(positions, UVMap, Basis key) of a mesh as numpy arrays."""
    import numpy as np
    from compare_merge import snapshot
    n, c = len(me.vertices), len(me.loops)
    pos = snapshot.read(me.vertices, "co", n, 3)
    uv = snapshot.read(me.uv_layers["UVMap"].data, "uv", c, 2)
    basis = snapshot.read(me.shape_keys.reference_key.data, "co", n, 3) if me.shape_keys else None
    return pos, uv, basis


def close(a, b):
    import numpy as np
    return a is not None and b is not None and a.shape == b.shape and np.allclose(a, b, atol=1e-6)


def test_units():
    """Field-level picks: units, preview, normalisation, refresh."""
    print("units")
    bpy.ops.wm.open_mainfile(filepath=UCUR)
    cur_pos, cur_uv, cur_basis = arrays(bpy.data.meshes["SplitMesh", None])
    api.start(UINC)
    inc_pos, inc_uv, _b = arrays(ses.get_incoming("MESH", "SplitMesh"))
    check(not close(cur_pos, inc_pos) and not close(cur_uv, inc_uv), "fixture: positions and UVs differ")

    keys = unit_keys("MESH", "SplitMesh", locked=False)
    check({"positions", "uv:UVMap", "attr:Weight", "key:Key 1", "key:A"} <= keys, f"SplitMesh units ({sorted(keys)})")
    check(not unit_keys("MESH", "SplitMesh", locked=True) - {"weights"}, "SplitMesh: nothing else locked")
    from compare_merge import apply
    sub = apply.units(entry("MESH", "SubdivMesh"))
    check(sub and all(u.locked and "topology" in u.locked.lower() for u in sub),
          f"SubdivMesh: every unit locked by topology ({[(u.key, u.locked) for u in sub]})")
    check({"transform", "slot:1"} <= unit_keys("OBJECT", "Mover", locked=False), "Mover units")
    pk = unit_keys("MATERIAL", "Painted", locked=False)
    check(len([k for k in pk if "Roughness" in k]) == 1 and len([k for k in pk if "Base Color" in k]) == 1,
          f"Painted: Roughness and Base Color pick separately ({sorted(pk)})")
    check({"rna:energy", "rna:color"} <= unit_keys("LIGHT", "Bulb", locked=False), "Bulb units")
    third = [u for u in apply.units(entry("MATERIAL", "Painted")) if u.key.startswith("node:Math › ")]
    check(third and all(u.locked for u in third) and "node:Math · operation" in unit_keys("MATERIAL", "Painted", False),
          f"an input only one side's node settings show is locked ({[(u.key, u.locked) for u in third]})")

    # Preview: their UVs, our positions.
    mi = entry("MESH", "SplitMesh")
    api.set_unit(mi, "uv:UVMap", "INCOMING")
    check(api.row_state(mi) == "MIXED" and api.takes(mi, "uv:UVMap") and not api.takes(mi, "positions"),
          "row is Mixed and takes only the UVs")
    preview.rebuild()
    sc = preview.preview_scene()
    vl = sc.view_layers[0]
    shown = next(o for o in sc.objects if o.get("cm_src", o.name) == "SplitObj" and o.visible_get(view_layer=vl))
    pos, uv, basis = arrays(shown.data)
    check(shown.data.is_runtime_data and close(pos, cur_pos) and close(uv, inc_uv) and close(basis, cur_basis),
          "preview: current positions, incoming UVs")
    real = bpy.data.meshes["SplitMesh", None]
    rp, ru, _rb = arrays(real)
    check(close(rp, cur_pos) and close(ru, cur_uv), "preview leaves the real mesh untouched")
    check(not real.is_runtime_data and shown.data != real, "preview shows a runtime copy")

    # A mixed object: their transform, our slot material; their parent (and its offset), our location.
    api.set_unit(entry("OBJECT", "Mover"), "transform", "INCOMING")
    hi = entry("OBJECT", "Hanger")
    check({"parent", "transform"} <= unit_keys("OBJECT", "Hanger", locked=False), "Hanger units")
    api.set_unit(hi, "parent", "INCOMING")
    preview.rebuild()
    with bpy.context.temp_override(scene=sc, view_layer=vl):
        bpy.context.evaluated_depsgraph_get().update()

    def shown_ob(name):
        return next(o for o in sc.objects if o.get("cm_src", o.name) == name and o.visible_get(view_layer=vl))
    mover = shown_ob("Mover")
    check(tuple(mover.location) == (0, 0, 3) and mover.material_slots[0].material.name == "MatA",
          f"preview: Mover at the incoming location with the current slot material ({tuple(mover.location)})")
    hanger = shown_ob("Hanger")
    check(hanger.parent is not None and hanger.parent.name == "Hook2" and
          (hanger.matrix_world.translation - Vector((1, 11, 6))).length < 1e-5,
          f"preview: Hanger under Hook2 with its offset, at its own location ({tuple(hanger.matrix_world.translation)})")
    check(bpy.data.objects["Hanger", None].parent.name == "Hook", "real Hanger untouched")

    # Normalisation: every unit is Incoming, none is Current.
    for k in keys:
        api.set_unit(mi, k, "INCOMING")
    check(View.choices[mi] == "INCOMING", "taking every unit reads as Incoming")
    api.set_unit(mi, "positions", "CURRENT")
    check(api.row_state(mi) == "MIXED" and not api.takes(mi, "positions"), "dropping one unit from Incoming mixes")
    for k in keys:
        api.set_unit(mi, k, "CURRENT")
    check(View.choices[mi] == "CURRENT", "no unit taken reads as Current")
    api.set_unit(mi, "uv:UVMap", "INCOMING")
    api.set_choice(mi, "INCOMING")
    check(View.choices[mi] == "INCOMING", "setting the row replaces its unit picks")
    api.set_unit(mi, "uv:UVMap", "INCOMING")
    api.set_unit(mi, "positions", "CURRENT")
    sub_i = entry("MESH", "SubdivMesh")
    api.set_unit(sub_i, sub[0].key, "INCOMING")
    check(View.choices.get(sub_i, "CURRENT") == "CURRENT", "locked units can't be picked")

    # Picks survive a refresh.
    before = dict(View.choices)
    api.refresh()
    check(View.choices.get(entry("MESH", "SplitMesh")) == before[mi], "unit picks survive a refresh")

    # Ghost compares the mix with the current file; mixed copies go away with the preview.
    View.active, View.focus = entry("MESH", "SplitMesh"), "GHOST"
    preview.rebuild()
    check(View.ghost_objects, "ghost for a mixed row")
    View.focus, View.active = "OFF", -1
    for cat, name in (("MESH", "SplitMesh"), ("OBJECT", "Mover"), ("OBJECT", "Hanger")):
        api.set_choice(entry(cat, name), "CURRENT")
    preview.rebuild()
    drafts = [i.name_full for i in ses.all_ids() if ses.is_draft(i) and not isinstance(i, (bpy.types.Scene, bpy.types.Collection))]
    check(not drafts, f"no mixed copies left in the preview ({drafts})")
    api.close()
    check(not runtime_leftovers(), f"nothing left after closing ({runtime_leftovers()})")


def test_units_merge():
    print("units merge")
    bpy.ops.wm.open_mainfile(filepath=UCUR)
    real = bpy.data.meshes["SplitMesh", None]
    cur_pos, cur_uv, cur_basis = arrays(real)
    cur_painted = bpy.data.materials["Painted", None].node_tree.nodes["Principled BSDF"]
    cur_color = tuple(cur_painted.inputs["Base Color"].default_value)
    api.start(UINC)
    inc_pos, inc_uv, inc_basis = arrays(ses.get_incoming("MESH", "SplitMesh"))
    from compare_merge import apply
    mi = entry("MESH", "SplitMesh")
    api.set_unit(mi, "uv:UVMap", "INCOMING")
    api.set_unit(mi, "attr:Weight", "INCOMING")
    api.set_unit(mi, "key:Key 1", "INCOMING")
    api.set_unit(mi, "key:A", "INCOMING")
    api.set_unit(entry("OBJECT", "Mover"), "transform", "INCOMING")
    rough = next(u.key for u in apply.units(entry("MATERIAL", "Painted")) if "Roughness" in u.key)
    api.set_unit(entry("MATERIAL", "Painted"), rough, "INCOMING")
    api.set_unit(entry("LIGHT", "Bulb"), "rna:energy", "INCOMING")
    api.set_unit(entry("OBJECT", "Hanger"), "parent", "INCOMING")
    report = api.merge()
    print("  ", report)
    check("partially" in report, "report counts partial merges")

    me = bpy.data.meshes["SplitMesh", None]
    pos, uv, basis = arrays(me)
    check(me == real and bpy.data.objects["SplitObj", None].data == me, "SplitMesh keeps its name and its object")
    check(close(pos, cur_pos) and close(uv, inc_uv) and close(basis, cur_basis),
          "merge: current positions and Basis, incoming UVs")
    check("Weight" in me.attributes and abs(me.attributes["Weight"].data[3].value - 0.3) < 1e-6, "attribute added")
    kb = me.shape_keys.key_blocks
    check("A" in kb and kb["Key 1"].relative_key == kb["A"],
          f"picked key relative to another picked key added after it ({kb['Key 1'].relative_key.name})")
    mover = bpy.data.objects["Mover", None]
    check(tuple(mover.location) == (0, 0, 3) and mover.material_slots[0].material.name == "MatA",
          "Mover: incoming location, current slot material")
    hanger = bpy.data.objects["Hanger", None]
    bpy.context.view_layer.update()
    check(hanger.parent == bpy.data.objects["Hook2", None] and
          (hanger.matrix_world.translation - Vector((1, 11, 6))).length < 1e-5,
          f"Hanger: incoming parent and offset, current location ({tuple(hanger.matrix_world.translation)})")
    bsdf = bpy.data.materials["Painted", None].node_tree.nodes["Principled BSDF"]
    check(abs(bsdf.inputs["Roughness"].default_value - 0.9) < 1e-6 and
          tuple(bsdf.inputs["Base Color"].default_value) == cur_color, "Painted: incoming roughness, current color")
    bulb = bpy.data.lights["Bulb", None]
    check(bulb.energy == 500 and tuple(bulb.color) == (1, 1, 1), "Bulb: incoming energy, current color")
    names = [i.name for i in ses.all_ids() if i.library is None and not i.is_runtime_data]
    check(not [n for n in names if n.endswith(".001") or n.startswith("cm~") or "(incoming)" in n],
          f"no duplicate names ({[n for n in names if '.0' in n or 'cm~' in n]})")
    check(status("MESH", "SplitMesh") == diff.CHANGED and changed_fields("MESH", "SplitMesh") and
          not {"uv map UVMap", "attribute (point) Weight"} & set(changed_fields("MESH", "SplitMesh")),
          f"only the kept fields still differ ({changed_fields('MESH', 'SplitMesh')})")
    api.close()
    out = os.path.join(TMP, "units_merged.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True)
    res = reopen_check(out, "import bpy, numpy as np; me = bpy.data.meshes['SplitMesh']; "
                            "uv = np.empty(len(me.loops) * 2, dtype=np.float32); "
                            "me.uv_layers['UVMap'].data.foreach_get('uv', uv); "
                            "print('RESULT', sorted(m.name for m in bpy.data.meshes), "
                            "round(float(uv.sum()), 4), round(me.vertices[8].co.z, 4), "
                            "bpy.data.objects['Mover'].location.z)")
    print("   reopened:", res)
    want = (f"['MoverMesh', 'SplitMesh', 'SubdivMesh'] {round(float(inc_uv.sum()), 4)} "
            f"{round(float(cur_pos.reshape(-1, 3)[8][2]), 4)} 3.0")
    check(res == want, f"merged file reopens with the mix ({res} vs {want})")

    # A copy that fails after editing real data: the merge says so (Ctrl+Z), not "nothing changed".
    from compare_merge import merge
    bpy.ops.wm.open_mainfile(filepath=UCUR)
    api.start(UINC)
    api.set_unit(entry("MESH", "SplitMesh"), "uv:UVMap", "INCOMING")
    api.set_unit(entry("MESH", "SplitMesh"), "attr:Weight", "INCOMING")
    real_copy, calls = apply.copy_unit, {"n": 0}

    def flaky(*args):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure after the first unit")
        return real_copy(*args)
    apply.copy_unit = flaky
    try:
        api.merge()
        check(False, "merge should have raised")
    except RuntimeError:
        check(merge.LAST_TOUCHED, "a mixed copy that fails halfway counts as touched")
    finally:
        apply.copy_unit = real_copy
    api.recover_after_failed_merge()
    api.close()

    # Vice versa: their positions, our UVs.
    bpy.ops.wm.open_mainfile(filepath=UCUR)
    api.start(UINC)
    api.set_unit(entry("MESH", "SplitMesh"), "positions", "INCOMING")
    api.merge()
    pos, uv, basis = arrays(bpy.data.meshes["SplitMesh", None])
    check(close(pos, inc_pos) and close(uv, cur_uv) and close(basis, inc_basis),
          "merge: incoming positions and Basis, current UVs")
    api.close()


def test_units_undo():
    """Unit picks come back after undoing a merge."""
    print("units undo")
    import compare_merge
    compare_merge.register()
    try:
        bpy.ops.wm.open_mainfile(filepath=UCUR)
        api.start(UINC)
        mi = entry("MESH", "SplitMesh")
        api.set_unit(mi, "uv:UVMap", "INCOMING")
        picked = View.choices[mi]
        bpy.ops.ed.undo_push(message="picked")
        check(bpy.ops.compare_merge.merge("EXEC_DEFAULT") == {"FINISHED"}, "operator merged a mixed row")
        bpy.ops.ed.undo_push(message="Merge")
        bpy.ops.ed.undo()
        compare_merge._after_undo()
        check(View.choices.get(entry("MESH", "SplitMesh")) == picked, "undo brings the unit picks back")
        api.close()
    finally:
        compare_merge.unregister()


SCUR = os.path.join(TMP, "session", "final", "scene.blend")
SINC = os.path.join(TMP, "session", "artist", "scene.blend")


def test_session_state():
    """Only what the files hold is compared, not what this session did to the open one."""
    print("session state")
    import bmesh
    import shutil
    from compare_merge import snapshot
    fixtures.build_session_state(SCUR)
    shutil.copytree(os.path.dirname(SCUR), os.path.dirname(SINC))  # the same file, byte for byte
    bpy.ops.wm.open_mainfile(filepath=SCUR)
    bpy.context.scene.frame_set(25)  # writes frame 25's values onto everything keyframed or driven
    api.start(SINC)
    drift = [(e.cat, e.current, e.changes) for e in ses.S.entries if e.status != diff.SAME]
    check(not drift, f"an identical copy diffs clean after scrubbing the timeline ({drift})")
    cur = ses.S.current_snap
    check(cur[("OBJECT", "Animated")]["location"] == snapshot.ANIMATED, "keyframed location reads animated")
    check(cur[("OBJECT", "Driven")]["scale"] == "(animated, 1, 1)", "driven scale.x reads animated")
    check(cur[("OBJECT", "Animated")]["custom › wobble"] == snapshot.ANIMATED, "keyframed custom property")
    check(cur[("MATERIAL", "KeyedMat")]["Principled BSDF › Roughness"] == snapshot.ANIMATED, "keyframed node input")
    check(cur[("LIGHT", "KeyedLamp")]["energy"] == snapshot.ANIMATED, "keyframed light energy")
    check("value animated" in cur[("MESH", "KeyedMesh")]["shape key Squash"], "keyframed shape key value")
    check("2 files" in cur[("IMAGE", "Udim")].get("content", ""), "every UDIM tile hashed")
    check("3 files" in cur[("IMAGE", "Seq")].get("content", ""), "every sequence frame hashed")

    # The artist repainted one UDIM tile and one frame of the sequence (not the first ones).
    tex = os.path.join(os.path.dirname(SINC), "textures")
    fixtures.write_png(os.path.join(tex, "udim.1002.png"), (0, 0, 1, 1))
    fixtures.write_png(os.path.join(tex, "seq.0002.png"), (0, 0, 1, 1))
    api.start(SINC)
    check(status("IMAGE", "Udim") == diff.CHANGED, "repainted UDIM tile found")
    check(status("IMAGE", "Seq") == diff.CHANGED, "repainted sequence frame found")

    # A vertex moved in Edit Mode, still in Edit Mode.
    plain = bpy.data.objects["Plain", None]
    bpy.context.view_layer.objects.active = plain
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(plain.data)
    bm.verts.ensure_lookup_table()
    bm.verts[0].co.x += 1
    bmesh.update_edit_mesh(plain.data)
    api.refresh()
    fields = changed_fields("MESH", "PlainMesh")
    check(fields == ["positions"], f"edit-mode vertex move found, nothing else ({fields})")
    bpy.ops.object.mode_set(mode="OBJECT")
    api.close()


ACUR = os.path.join(TMP, "animation", "final", "scene.blend")
AINC = os.path.join(TMP, "animation", "artist", "scene.blend")


def test_animation_review():
    """Packed images hash their bytes; a keyframe hides only its own component; drivers show anywhere."""
    print("animation review")
    fixtures.build_animation_review("incoming", AINC)
    fixtures.build_animation_review("current", ACUR)
    bpy.ops.wm.open_mainfile(filepath=ACUR)
    api.start(AINC)
    cur, inc = ses.S.current_snap, ses.S.incoming_snap
    content = cur[("IMAGE", "Packed")].get("content", "")
    check(content.startswith("#"), f"packed image hashed ({cur[('IMAGE', 'Packed')]})")
    check(changed_fields("IMAGE", "Packed") == ["content"], "different packed images differ in content")
    check(status("IMAGE", "SamePacked") == diff.SAME, "identical packed images match")
    check((cur[("OBJECT", "PartKeyed")]["location"], inc[("OBJECT", "PartKeyed")]["location"]) ==
          ("(animated, 1, 0)", "(animated, 2, 0)"), f"only the keyed component reads animated "
          f"({cur[('OBJECT', 'PartKeyed')]['location']})")
    check(changed_fields("OBJECT", "PartKeyed") == ["location"], "a change to an unkeyed component shows")
    check(cur[("LIGHT", "DrivenLamp")]["energy"] == snapshot.ANIMATED, "driven light energy reads animated")
    check("drivers" in changed_fields("LIGHT", "DrivenLamp"), "a light driver's expression change shows")
    check("node tree drivers" in changed_fields("MATERIAL", "DrivenMat"), "a node driver's target change shows")
    check((cur[("LIGHT", "MutedLamp")]["energy"], inc[("LIGHT", "MutedLamp")]["energy"]) == (snapshot.ANIMATED, "10"),
          "a muted driver leaves its value compared")
    check("drivers" in changed_fields("LIGHT", "MutedLamp"), "muting a driver shows as a driver change")
    for cat, name, field in (("LIGHT", "DrivenLamp", "drivers"), ("MATERIAL", "DrivenMat", "node tree drivers")):
        u = next((u for u in apply.units(entry(cat, name)) if field in [f for f, _a, _b in u.fields]), None)
        check(u is not None and u.locked, f"{name}: drivers come with the whole row")
    api.close()


BCUR = os.path.join(TMP, "robust", "final", "scene.blend")
BINC = os.path.join(TMP, "robust", "artist", "v2", "scene.blend")  # another folder, one level deeper


def test_robustness():
    """Changes a production file showed the diff missed or over-reported (see KNOWN-ISSUES.md)."""
    print("robustness")
    shared = os.path.join(TMP, "robust", "shared", "tex.png")
    os.makedirs(os.path.dirname(shared), exist_ok=True)
    fixtures.write_png(shared, (1, 0, 1, 1))
    fixtures.build_robustness("incoming", BINC, shared)
    fixtures.build_robustness("current", BCUR, shared)
    bpy.ops.wm.open_mainfile(filepath=BCUR)
    api.start(BINC)

    e = ses.S.entries[entry("OBJECT", "Smoothed")]
    angle = [(a, b) for f, a, b in e.changes if f == "modifier Smooth · input Angle"]
    check(angle == [("0.5", "1.2")], f"Geometry Nodes modifier input compared ({e.changes})")
    check("Bool · operation" in changed_fields("NODETREE", "Smooth"), "Geometry Nodes node setting compared")
    check({"modifier Bevel · show_render", "modifier Bevel · show_viewport"} <= set(changed_fields("OBJECT", "Beveled")),
          "modifier render / viewport toggles compared")
    check(status("OBJECT", "PanelOnly") == diff.SAME, f"a panel opened in the modifier UI is not a change "
          f"({changed_fields('OBJECT', 'PanelOnly')})")
    check(status("IMAGE", "Shared") == diff.SAME, f"same texture by another relative path "
          f"({changed_fields('IMAGE', 'Shared')})")
    check(status("IMAGE", "WinPath") == diff.SAME and "missing" not in ses.S.incoming_snap[("IMAGE", "WinPath")]["content"],
          f"a path saved on Windows (backslashes) is read ({changed_fields('IMAGE', 'WinPath')}, "
          f"{ses.S.incoming_snap[('IMAGE', 'WinPath')]['content']})")
    renamed = sorted((e.cat, e.current, e.incoming) for e in ses.S.entries if e.status == diff.RENAMED)
    renamed = [r for r in renamed if r[0] != "NODETREE"]
    check(renamed == [("MATERIAL", "ChainMat", "ChainMat2"),
                      ("MATERIAL", "Glass", "Glass2"), ("MESH", "ChainMesh", "ChainMesh2"),
                      ("OBJECT", "Crate", "Crate2")], f"renames found, a chain of them too ({renamed})")
    nest = sorted(e.current for e in ses.S.entries if e.cat == "NODETREE" and e.status == diff.RENAMED)
    check(nest == [f"Nest{i}" for i in range(6)] and status("MATERIAL", "Nested") == diff.SAME,
          f"six nested renamed node groups all pair up, their material unchanged ({nest})")
    users = [n for cat, n in [("OBJECT", f"Pane{i}") for i in range(3)] + [("MESH", f"Pane{i}Mesh") for i in range(3)]
             + [("OBJECT", "Chain"), ("COLLECTION", "Crates")] if status(cat, n) != diff.SAME]
    check(not users, f"what uses a renamed data-block is unchanged ({users})")

    # Field-level picks of the new fields, then everything else.
    check(api.set_unit(entry("OBJECT", "Smoothed"), "mod:Smooth", "INCOMING"), "modifier input unit pickable")
    check(api.set_unit(entry("OBJECT", "Beveled"), "mod:Bevel", "INCOMING"), "modifier toggles unit pickable")
    print("  ", api.merge())
    smoothed, beveled = bpy.data.objects["Smoothed", None], bpy.data.objects["Beveled", None]
    from compare_merge import snapshot
    st = snapshot.gn_input(smoothed.modifiers["Smooth"], snapshot.gn_inputs(smoothed.modifiers["Smooth"])[0][1])
    check(st is None or abs(st.value - 1.2) < 1e-6, "merge: modifier input taken")
    check(not beveled.modifiers["Bevel"].show_render, "merge: modifier render toggle taken")
    check(changed_fields("OBJECT", "Smoothed") == ["location"] and changed_fields("OBJECT", "Beveled") == ["location"],
          f"only the units not picked still differ ({changed_fields('OBJECT', 'Smoothed')}, "
          f"{changed_fields('OBJECT', 'Beveled')})")
    for i, e in enumerate(ses.S.entries):
        if e.status != diff.SAME and ses.choosable(e):
            api.set_choice(i, "INCOMING", cascade=False)
    print("  ", api.merge())
    left = [(e.cat, e.name, e.status) for e in ses.S.entries if e.status != diff.SAME]
    check(not left, f"merging everything leaves no difference ({left})")
    api.close()


def main():
    fixtures.build("incoming", INC)
    fixtures.build("current", CUR)
    fixtures.build_review("incoming", RINC)
    fixtures.build_review("current", RCUR)
    fixtures.build_units("incoming", UINC)
    fixtures.build_units("current", UCUR)
    bpy.ops.wm.open_mainfile(filepath=CUR)
    for t in (test_image_snapshot_ignores_loaded_pixels, test_diff, test_preview_is_safe, test_views, test_merge, test_close, test_review,
              test_failed_merge_rolls_back, test_scene_rename, test_operator_undo, test_units, test_units_merge,
              test_units_undo, test_session_state, test_animation_review, test_robustness):
        try:
            t()
        except Exception:
            traceback.print_exc()
            FAILS.append(t.__name__ + " raised")
    print(f"\n{len(FAILS)} failure(s)" if FAILS else "\nall tests passed")
    sys.exit(1 if FAILS else 0)


main()
