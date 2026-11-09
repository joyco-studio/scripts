"""Properties, operators and panels (3D View > Sidebar > Texel, UV Editor > Sidebar > Texel,
and a Texel Optimize section inside File > Export > glTF 2.0)."""

import os
import runpy
import shlex
import sys
import time

import bpy

from . import api, core, cull, export, overlay, uv_suggest

_LAST = {}


# ----------------------------------------------------------------------------- properties

class TC_Props(bpy.types.PropertyGroup):
    # cameras
    res_x: bpy.props.IntProperty(name="Width", default=1920, min=16,
                                 description="Output pixels for cameras without their own tc_res")
    res_y: bpy.props.IntProperty(name="Height", default=1080, min=16)
    camera_mode: bpy.props.EnumProperty(name="Cameras", items=[
        ("ALL", "All Cameras", ""), ("SELECTED", "Selected Cameras", ""), ("ACTIVE", "Scene Camera", "")])
    use_frames: bpy.props.BoolProperty(name="Sample Frame Range", default=False,
                                       description="Every sampled frame is a camera moment")
    frame_step: bpy.props.IntProperty(name="Step", default=1, min=1)
    collection: bpy.props.PointerProperty(
        name="Collection", type=bpy.types.Collection,
        description="Only the meshes in this collection (the export collection), including ones hidden "
                    "from render. Empty = every visible renderable mesh")
    rig_script: bpy.props.StringProperty(
        name="Rig Script", subtype="FILE_PATH",
        description="Python script that rebuilds the site's camera behaviour as cameras "
                    "(e.g. scripts/texel-coverage/hero_cameras.py in the website repo)")
    rig_args: bpy.props.StringProperty(name="Arguments", default="",
                                       description="Command-line arguments passed to the rig script")
    # analysis
    occlusion: bpy.props.BoolProperty(name="Occlusion", default=True,
                                      description="Faces hidden behind other geometry count as unseen")
    target_ratio: bpy.props.FloatProperty(
        name="Texels per Pixel", default=1.0, min=0.1, max=4.0,
        description="1.0 = one texel per screen pixel (full sharpness). 0.5 = half, softer and cheaper")
    time_quantile: bpy.props.FloatProperty(
        name="Moments Covered", default=1.0, min=0.5, max=1.0, subtype="FACTOR",
        description="Per face, how many of its on-screen camera moments must be sharp. 1.0 = every moment")
    quantile: bpy.props.FloatProperty(
        name="Pixels Covered", default=0.95, min=0.5, max=1.0, subtype="FACTOR",
        description="Texture sizes keep this share of a texture's screen pixels fully sharp")
    # uvs
    split_ratio: bpy.props.FloatProperty(
        name="Split Islands At", default=2.0, min=1.0, max=64.0,
        description="Propose a cut where camera need changes by this factor, or seen meets unseen. 1 = never")
    unseen_scale: bpy.props.FloatProperty(name="Unseen Island Scale", default=0.1, min=0.01, max=1.0,
                                          description="Density for islands no camera sees, relative to the median need")
    pack_margin_px: bpy.props.IntProperty(name="Island Gap (px)", default=4, min=0, max=64,
                                          description="Gap between islands, in texels of the final texture")
    suggest_tolerance: bpy.props.FloatProperty(
        name="Size Tolerance", default=0.85, min=0.5, max=1.0, subtype="FACTOR",
        description="Take the next smaller texture when the layout fits it at this share of the camera "
                    "need or more (0.85: up to 15% softer than 1 texel per pixel, a quarter of the memory). "
                    "1.0 never goes under the need")
    suggest_fill: bpy.props.BoolProperty(name="Fill Leftover Space", default=True,
                                         description="Grow the whole layout uniformly into the leftover space")
    suggest_snap: bpy.props.BoolProperty(
        name="Snap to Tiers", default=False,
        description="Size islands to power-of-two density tiers instead of their exact need. Steadier "
                    "layouts when cameras change a little, but textures can come out up to 2x bigger")
    uv_overlay: bpy.props.BoolProperty(name="Ratio Overlay", default=True,
                                       description="Colour faces by ratio in the UV editor while editing")
    view: bpy.props.StringProperty(default="TC_Ratio", options={"HIDDEN"})
    view_legend: bpy.props.BoolProperty(name="Legend in Viewport", default=True,
                                        description="Draw the colour legend in the viewport and UV editor "
                                                    "while a Texel view is shown")
    # cull
    cull_preset: bpy.props.EnumProperty(name="Margin", default="SAFE", items=[
        ("SAFE", "Safe", "Camera shift 0.2 m, overscan 1.1, surface margin 5 cm"),
        ("TIGHT", "Tight", "Camera shift 5 cm, overscan 1.03, surface margin 2 cm"),
        ("CUSTOM", "Custom", "Use the sliders below")])
    cull_shift: bpy.props.FloatProperty(name="Camera Shift", default=0.2, min=0.0, max=5.0, unit="LENGTH",
                                        description="Also test every camera moment moved this far along each axis")
    cull_overscan: bpy.props.FloatProperty(name="Frame Overscan", default=1.1, min=1.0, max=2.0,
                                           description="Widen every tested frame by this factor")
    cull_rings: bpy.props.IntProperty(name="Keep Rings", default=0, min=0, max=10,
                                      description="Keep this many rings of neighbouring faces around everything seen")
    cull_distance: bpy.props.FloatProperty(name="Surface Margin", default=0.05, min=0.0, max=2.0, unit="LENGTH",
                                           description="Keep faces within this distance of a seen face, along the surface")
    cull_pixel: bpy.props.BoolProperty(name="Pixel-Exact Pass", default=True,
                                       description="Also render every camera moment with face IDs so slivers are kept")
    cull_pixel_scale: bpy.props.FloatProperty(name="Pixel Pass Scale", default=1.0, min=0.1, max=1.0)
    cull_skip: bpy.props.StringProperty(name="Never Cull", default="",
                                        description="Comma-separated objects to leave whole (or set tc_no_cull on them)")
    # export
    export_enabled: bpy.props.BoolProperty(
        name="Optimize glTF Exports", default=False,
        description="Every glTF export of this file culls unseen faces and resizes textures to camera need. "
                    "The .blend is restored right after")
    export_cull: bpy.props.BoolProperty(name="Cull Unseen Faces", default=True)
    export_resize: bpy.props.BoolProperty(name="Resize Textures to Need", default=True)
    export_cameras: bpy.props.BoolProperty(
        name="Include Cameras", default=True,
        description="Add the analysed cameras to the GLB, so viewers can look through the render's point of view "
                    "(the panel's Export button and the CLI; File > Export uses its own Cameras checkbox)")
    export_triangulate: bpy.props.BoolProperty(
        name="Fixed Triangulation", default=True,
        description="Triangulate with a fixed rule during export, so the UI and the command line write "
                    "byte-identical GLBs")
    export_max_size: bpy.props.IntProperty(name="Max Size", default=4096, min=16, max=16384,
                                           description="No exported texture is larger than this")
    export_min_size: bpy.props.IntProperty(name="Min Size", default=64, min=1, max=4096,
                                           description="No exported texture is smaller than this")
    export_stale: bpy.props.EnumProperty(name="If Out of Date", default="REFRESH", items=[
        ("REFRESH", "Re-analyze", "Re-run the analysis and the cull before exporting (slow on big scenes)"),
        ("SKIP", "Export Unoptimized", "Export as-is and say so in the report")])
    export_report: bpy.props.BoolProperty(name="Write Report Next to GLB", default=True)
    export_compress: bpy.props.BoolProperty(
        name="Compress with Chopper", default=False,
        description="After each GLB export, also write <name>.compressed.glb with chopper, the same "
                    "compression the site pipeline runs, so you see the final size")
    chopper_path: bpy.props.StringProperty(
        name="Chopper Project", subtype="DIR_PATH",
        description="A project folder with chopper installed (node_modules/.bin/chopper), "
                    "e.g. the website repo")
    chopper_recipe: bpy.props.StringProperty(name="Recipe", subtype="FILE_PATH",
                                             description="Optional chopper recipe file")
    verify_nudge: bpy.props.BoolProperty(name="Also Test Nudged Cameras", default=True)


def props_of(context):
    return context.scene.texel_coverage


def run_api(op, fn, *args, **kw):
    """Call an api function from an operator: same code path as the command line."""
    if core.object_mode(bpy.context):
        op.report({"INFO"}, "Switched to Object Mode (mesh data can't be read in Edit Mode)")
    try:
        res = fn(*args, **kw)
    except api.TexelError as e:
        op.report({"ERROR"}, str(e))
        return None
    for w in res["warnings"]:
        op.report({"WARNING"}, w)
    op.report({"INFO"} if res["ok"] else {"WARNING"}, res["message"][0].upper() + res["message"][1:])
    return res


# ----------------------------------------------------------------------------- operators

class TC_OT_sync_cameras(bpy.types.Operator):
    bl_idname = "texel_coverage.sync_cameras"
    bl_label = "Sync Site Cameras"
    bl_description = "Run the rig script to rebuild the site's camera behaviour as cameras in this file"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return {"FINISHED"} if run_api(self, api.sync_cameras, context) else {"CANCELLED"}

class TC_OT_analyze(bpy.types.Operator):
    bl_idname = "texel_coverage.analyze"
    bl_label = "Analyze"
    bl_description = "Measure how much texture resolution every face needs from the cameras"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        res = run_api(self, api.analyze, context)
        if res is None:
            return {"CANCELLED"}
        _LAST["summary"] = res["data"]["textures"]
        core.show_attribute(context, "TC_Ratio")
        props_of(context).view = "TC_Ratio"
        return {"FINISHED"}

class TC_OT_show(bpy.types.Operator):
    bl_idname = "texel_coverage.show"
    bl_label = "Show"
    bl_description = ("Show this view in the viewport (Solid shading, attribute colour). "
                      "Click again to turn it off and get your previous shading back")
    attr: bpy.props.StringProperty()

    def execute(self, context):
        if core.view_shown(context) == self.attr:  # clicking the view that is on turns it off
            core.hide_attribute(context)
            return {"FINISHED"}
        core.object_mode(context)
        for o in core.target_objects(context):
            if self.attr == "TC_Ratio":
                core.refresh_ratio(o, props_of(context))
            core.recolor(o)
        core.show_attribute(context, self.attr)
        props_of(context).view = self.attr
        return {"FINISHED"}


class TC_OT_use_scene_res(bpy.types.Operator):
    bl_idname = "texel_coverage.use_scene_res"
    bl_label = "From Scene"
    bl_description = "Use the scene's render resolution"

    def execute(self, context):
        p, r = props_of(context), context.scene.render
        p.res_x = int(r.resolution_x * r.resolution_percentage / 100)
        p.res_y = int(r.resolution_y * r.resolution_percentage / 100)
        return {"FINISHED"}


class TC_OT_suggest(bpy.types.Operator):
    bl_idname = "texel_coverage.suggest_uvs"
    bl_label = "Suggest UVs"
    bl_description = ("Write a <uv>_tc_suggest map where every island is sized by camera need, packed "
                      "deterministically. Locked islands stay. Your UV map is not touched")
    bl_options = {"REGISTER", "UNDO"}
    selected: bpy.props.BoolProperty(name="Selected Objects' Textures Only", default=True)

    def execute(self, context):
        only = [o.name for o in context.selected_objects] if self.selected and context.selected_objects else None
        res = run_api(self, api.suggest, context, only)
        if res is None:
            return {"CANCELLED"}
        for o in core.target_objects(context):
            name = next((l.name for l in o.data.uv_layers if l.name.endswith(uv_suggest.SUFFIX)), None)
            if name and (only is None or o.name in only):
                o.data.uv_layers.active = o.data.uv_layers[name]
        return {"FINISHED"}

class TC_OT_lock(bpy.types.Operator):
    bl_idname = "texel_coverage.lock"
    bl_label = "Lock Islands"
    bl_description = "Keep the selected faces where they are in the suggested map when you suggest again"
    bl_options = {"REGISTER", "UNDO"}
    lock: bpy.props.BoolProperty(default=True)

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH"

    def execute(self, context):
        n = uv_suggest.set_lock(context, self.lock)
        self.report({"INFO"}, f"{'Locked' if self.lock else 'Unlocked'} {n} faces")
        return {"FINISHED"}


class TC_OT_select_cuts(bpy.types.Operator):
    bl_idname = "texel_coverage.select_cuts"
    bl_label = "Select Proposed Cuts"
    bl_description = ("Select the edges where Suggest UVs split an island by camera need. "
                      "Mark Seam on the ones you agree with")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH"

    def execute(self, context):
        n = uv_suggest.select_cuts(context)
        self.report({"INFO"}, f"Selected {n} proposed cut edges")
        return {"FINISHED"}


class TC_OT_promote(bpy.types.Operator):
    bl_idname = "texel_coverage.promote"
    bl_label = "Promote Suggested UVs"
    bl_description = ("Copy the suggested map into the UV map your materials use (a *_tc_backup copy of "
                      "the old one is kept). Re-bake your textures into it afterwards")
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        names = [o.name for o in context.selected_objects if o.type == "MESH"] or None
        return {"FINISHED"} if run_api(self, api.promote, context, names) else {"CANCELLED"}

class TC_OT_bake(bpy.types.Operator):
    bl_idname = "texel_coverage.bake"
    bl_label = "Bake to Suggested UVs"
    bl_description = ("Bake every texture from the current UVs into the suggested layout (new *_tc images "
                      "saved next to the originals), promote the layout and switch the materials to the "
                      "baked images. Originals are untouched: Ctrl+Z or Revert Bake goes back")
    # No "UNDO" option: Blender 5.1 crashes on Ctrl+Z when an operator's automatic undo step
    # adds images to materials shown in Material Preview. A manual push undoes the same way.
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        ok = run_api(self, api.bake_textures, context)
        bpy.ops.ed.undo_push(message="Bake to Suggested UVs")
        return {"FINISHED"} if ok else {"CANCELLED"}


class TC_OT_revert_bake(bpy.types.Operator):
    bl_idname = "texel_coverage.revert_bake"
    bl_label = "Revert Bake"
    bl_description = "Point materials back at the original images and restore the UVs from before Promote"
    bl_options = {"REGISTER"}  # manual undo push, same reason as Bake

    def execute(self, context):
        ok = run_api(self, api.revert_bake, context)
        bpy.ops.ed.undo_push(message="Revert Bake")
        return {"FINISHED"} if ok else {"CANCELLED"}


class TC_OT_checker(bpy.types.Operator):
    bl_idname = "texel_coverage.checker"
    bl_label = "Target Checker"
    bl_description = ("Show a colour grid through the suggested UVs (or the render UVs). Even-sized cells "
                      "on screen = density follows camera need")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if overlay.checker_active(context.scene):
            overlay.checker_off(context)
        else:
            overlay.checker_on(context)
        return {"FINISHED"}


class TC_OT_cull_preview(bpy.types.Operator):
    bl_idname = "texel_coverage.cull_preview"
    bl_label = "Preview Cull"
    bl_description = "Work out the cull (if out of date) and show it in the viewport. Nothing is deleted"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return {"FINISHED"} if run_api(self, api.cull_compute, context, preview=True) else {"CANCELLED"}

class TC_OT_cull_clear(bpy.types.Operator):
    bl_idname = "texel_coverage.cull_clear"
    bl_label = "Show Everything"
    bl_description = "Remove the cull preview modifiers (the stored cull stays for export)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        cull.remove_modifiers(context)
        return {"FINISHED"}


class TC_OT_prepare(bpy.types.Operator):
    bl_idname = "texel_coverage.prepare"
    bl_label = "Update Analysis + Cull"
    bl_description = "Bring the stored analysis and cull up to date now, so exports are fast"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return {"FINISHED"} if run_api(self, api.prepare, context) else {"CANCELLED"}

class TC_OT_plan(bpy.types.Operator):
    bl_idname = "texel_coverage.plan"
    bl_label = "Preview Export Sizes"
    bl_description = "List the size every texture would be exported at, without exporting"

    def execute(self, context):
        res = run_api(self, api.plan, context)
        if res is None:
            return {"CANCELLED"}
        _LAST["plan"] = res["data"]["textures"]
        return {"FINISHED"}

class TC_OT_export(bpy.types.Operator):
    bl_idname = "texel_coverage.export"
    bl_label = "Export GLB..."
    bl_description = "Open the glTF exporter with Texel optimization on"

    def execute(self, context):
        p = props_of(context)
        p.export_enabled = True
        bpy.ops.export_scene.gltf("INVOKE_DEFAULT", export_cameras=p.export_cameras)
        return {"FINISHED"}


class TC_OT_restore(bpy.types.Operator):
    bl_idname = "texel_coverage.restore"
    bl_label = "Restore"
    bl_description = "Undo anything an interrupted optimized export left behind (images, modifiers)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return {"FINISHED"} if run_api(self, api.restore, context) else {"CANCELLED"}

class TC_OT_verify(bpy.types.Operator):
    bl_idname = "texel_coverage.verify"
    bl_label = "Verify Cull"
    bl_description = ("Render every camera moment with and without the cull and count changed pixels "
                      "(slow: two renders per moment)")
    bl_options = {"REGISTER"}

    def execute(self, context):
        p = props_of(context)
        return {"FINISHED"} if run_api(self, api.verify, context, nudge=p.verify_nudge) else {"CANCELLED"}

def _status_row(layout, label, state):
    icon = {"fresh": "CHECKMARK", "stale": "ERROR", "none": "RADIOBUT_OFF"}[state]
    text = {"fresh": "ok", "stale": "outdated", "none": "not run"}[state]
    layout.label(text=f"{label}: {text}", icon=icon)


class TC_PT_main(bpy.types.Panel):
    bl_label = "Texel Coverage"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Texel"

    def draw(self, context):
        p = props_of(context)
        layout = self.layout
        st = core.cache_status(context, p)
        row = layout.row()
        _status_row(row, "Analysis", st["analysis"])
        _status_row(row, "Cull", st["cull"])
        if "stale" in st.values() or "none" in st.values():
            layout.operator("texel_coverage.prepare", icon="FILE_REFRESH")


class TC_PT_cameras(bpy.types.Panel):
    bl_label = "1. Cameras"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Texel"
    bl_parent_id = "TC_PT_main"

    def draw(self, context):
        p = props_of(context)
        col = self.layout.column()
        col.prop(p, "camera_mode")
        col.prop(p, "collection")
        row = col.row(align=True)
        row.prop(p, "use_frames", text="Frame Range")
        sub = row.row()
        sub.enabled = p.use_frames
        sub.prop(p, "frame_step")
        box = col.box()
        box.label(text="Output px incl. DPR (no tc_res)")
        row = box.row(align=True)
        row.prop(p, "res_x")
        row.prop(p, "res_y")
        row.operator("texel_coverage.use_scene_res", text="", icon="SCENE")
        box = col.box()
        box.label(text="Site camera rig", icon="SCRIPT")
        box.prop(p, "rig_script", text="")
        box.prop(p, "rig_args", text="Args")
        box.operator("texel_coverage.sync_cameras", icon="FILE_REFRESH")


class TC_PT_analyze(bpy.types.Panel):
    bl_label = "2. Analyze"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Texel"
    bl_parent_id = "TC_PT_main"

    def draw(self, context):
        p = props_of(context)
        col = self.layout.column()
        col.prop(p, "target_ratio")
        col.prop(p, "quantile")
        col.prop(p, "time_quantile")
        col.prop(p, "occlusion")
        col.operator("texel_coverage.analyze", icon="CAMERA_DATA")
        row = col.row(align=True)
        shown = core.view_shown(context)
        for attr, text in (("TC_Ratio", "Ratio"), ("TC_Required", "Required")):
            row.operator("texel_coverage.show", text=text, icon="HIDE_OFF" if shown == attr else "SHADING_SOLID",
                         depress=shown == attr).attr = attr
        draw_legend(col.box(), p.view)
        col.prop(p, "view_legend")
        if _LAST.get("summary"):
            box = col.box()
            box.label(text="Texture: now -> needs (current UVs / ideal UVs)")
            for row in _LAST["summary"]:
                k, size, rc, ri = row["texture"], row["size"], row["need_current_uv"], row["need_ideal_uv"]
                icon = "ERROR" if rc > size else ("CHECKMARK" if rc == size else "TRIA_DOWN")
                box.label(text=f"{k[:22]}  {size} -> {core.fmt(rc)} / {core.fmt(ri)}", icon=icon)


class TC_PT_uvs(bpy.types.Panel):
    bl_label = "3. UVs"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Texel"
    bl_parent_id = "TC_PT_main"

    def draw(self, context):
        draw_uv_tools(self.layout, context)


_SWATCH = {"pcoll": None, "ids": {}}


def swatch(rgb):
    """Icon id of a flat colour square (rgb is linear, like the face colours)."""
    key = tuple(rgb)
    if key not in _SWATCH["ids"]:
        import bpy.utils.previews
        if _SWATCH["pcoll"] is None:
            _SWATCH["pcoll"] = bpy.utils.previews.new()
        n = 32
        srgb = core.to_srgb(rgb)
        px = []
        for yy in range(n):
            for xx in range(n):
                edge = xx in (0, n - 1) or yy in (0, n - 1)
                px += [0.0, 0.0, 0.0, 0.0] if edge else [*srgb, 1.0]
        pv = _SWATCH["pcoll"].new(f"tc_swatch_{len(_SWATCH['ids'])}")
        pv.icon_size = (n, n)
        pv.icon_pixels_float = px
        _SWATCH["ids"][key] = pv.icon_id
    return _SWATCH["ids"][key]


def free_swatches():
    if _SWATCH["pcoll"] is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(_SWATCH["pcoll"])
    _SWATCH.update(pcoll=None, ids={})


def draw_legend(layout, view):
    title, note, rows = core.LEGENDS.get(view, core.LEGENDS["TC_Ratio"])
    col = layout.column(align=True)
    col.label(text=title)
    sub = col.column(align=True)
    sub.scale_y = 0.75
    for c, (rng, meaning) in rows:
        row = sub.split(factor=0.45)
        row.label(text=rng, icon_value=swatch(c))
        row.label(text=meaning)
    note_row = col.row()
    note_row.enabled = False
    note_row.label(text=note)


def draw_uv_tools(layout, context):
    p = props_of(context)
    col = layout.column()
    col.prop(p, "split_ratio")
    col.prop(p, "unseen_scale")
    col.prop(p, "pack_margin_px")
    col.prop(p, "suggest_tolerance")
    col.prop(p, "suggest_fill")
    col.prop(p, "suggest_snap")
    col.operator("texel_coverage.suggest_uvs", icon="UV")
    row = col.row(align=True)
    row.operator("texel_coverage.lock", text="Lock", icon="LOCKED").lock = True
    row.operator("texel_coverage.lock", text="Unlock", icon="UNLOCKED").lock = False
    col.operator("texel_coverage.select_cuts", icon="EDGESEL")
    col.operator("texel_coverage.checker", icon="TEXTURE",
                 depress=overlay.checker_active(context.scene))
    col.prop(p, "uv_overlay")
    if p.uv_overlay and context.space_data.type == "IMAGE_EDITOR":
        draw_legend(col.box(), "TC_Ratio")
    row = col.row(align=True)
    row.operator("texel_coverage.bake", icon="RENDER_STILL")
    row.operator("texel_coverage.revert_bake", text="", icon="LOOP_BACK")
    col.operator("texel_coverage.promote", text="Promote Only (no bake)", icon="EXPORT")


class TC_OT_cull_override(bpy.types.Operator):
    bl_idname = "texel_coverage.cull_override"
    bl_label = "Cull Override"
    bl_description = ("Override this cull setting for the active object (click again or X to go back to the "
                      "preset). Stored as a custom property, so the command line and exports use it too")
    bl_options = {"REGISTER", "UNDO"}
    prop: bpy.props.StringProperty()

    _DESCR = {"tc_cull_shift": "How far the cameras may drift from the sampled positions, for this object",
              "tc_cull_margin": "Extra surface kept around visible faces, for this object"}

    def execute(self, context):
        o = context.active_object
        if o is None:
            return {"CANCELLED"}
        if self.prop == "tc_no_cull":
            if o.get("tc_no_cull"):
                del o["tc_no_cull"]
            else:
                o["tc_no_cull"] = True
        elif self.prop in o:
            del o[self.prop]
        else:
            param = dict((k, v) for k, v, _ in _OVERRIDES)[self.prop]
            o[self.prop] = float(core.cull_params(props_of(context))[param])
            o.id_properties_ui(self.prop).update(min=0.0, soft_max=1.0, step=1, precision=3,
                                                 subtype="DISTANCE", description=self._DESCR[self.prop])
        return {"FINISHED"}


class TC_PT_cull(bpy.types.Panel):
    bl_label = "4. Cull"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Texel"
    bl_parent_id = "TC_PT_main"

    def draw(self, context):
        p = props_of(context)
        col = self.layout.column()
        col.prop(p, "cull_preset", expand=True)
        sub = col.column(align=True)
        sub.enabled = p.cull_preset == "CUSTOM"
        sub.prop(p, "cull_shift")
        sub.prop(p, "cull_overscan")
        sub.prop(p, "cull_distance")
        sub.prop(p, "cull_rings")
        row = col.row(align=True)
        row.prop(p, "cull_pixel")
        r2 = row.row()
        r2.enabled = p.cull_pixel
        r2.prop(p, "cull_pixel_scale", text="Scale")
        col.label(text="Never cull these objects:")
        col.prop(p, "cull_skip", text="")
        draw_object_cull(col.box(), context, p)
        row = col.row(align=True)
        row.operator("texel_coverage.cull_preview", icon="MOD_MASK")
        row.operator("texel_coverage.cull_clear", text="", icon="X")
        row = col.row(align=True)
        row.operator("texel_coverage.verify", icon="VIEWZOOM")
        row.prop(p, "verify_nudge", text="", icon="ORIENTATION_VIEW")


_OVERRIDES = (("tc_cull_shift", "shift", "Camera Shift"), ("tc_cull_margin", "distance", "Surface Margin"))


def draw_object_cull(layout, context, p):
    """Cull status and per-object overrides of the active object."""
    o = context.active_object
    if o is None or o.type != "MESH":
        layout.label(text="Select a mesh to see its cull settings", icon="INFO")
        return
    layout.label(text=o.name, icon="OBJECT_DATA")
    if cull.rigged(o):
        layout.label(text="Never culled: rigged (armature)", icon="ARMATURE_DATA")
        return
    if o.name in cull.skip_names(p):
        layout.label(text="Never culled: in the Never Cull list", icon="CHECKBOX_HLT")
        return
    no_cull = bool(o.get("tc_no_cull", False))
    layout.operator("texel_coverage.cull_override", text="Never Cull This Object", icon="CHECKBOX_HLT"
                    if no_cull else "CHECKBOX_DEHLT", depress=no_cull).prop = "tc_no_cull"
    if no_cull:
        return
    a = o.data.attributes.get(core.CULL_ATTR)
    if a is not None and a.domain == "FACE" and len(a.data) == len(o.data.polygons) and len(a.data):
        import numpy as np
        keep = np.zeros(len(a.data), bool)
        a.data.foreach_get("value", keep)
        layout.label(text=f"Keeps {keep.sum():,} / {len(keep):,} faces", icon="MOD_MASK")
    preset = core.cull_params(p)
    col = layout.column(align=True)
    for key, param, label in _OVERRIDES:
        row = col.row(align=True)
        if key in o:
            row.prop(o, f'["{key}"]', text=label)
            row.operator("texel_coverage.cull_override", text="", icon="X").prop = key
        else:
            split = row.split(factor=0.55, align=True)
            split.label(text=label)
            split.operator("texel_coverage.cull_override", text=f"{preset[param] * 100:g} cm",
                           icon="DECORATE_OVERRIDE").prop = key


def draw_export_options(layout, p):
    col = layout.column()
    col.prop(p, "export_enabled")
    sub = col.column()
    sub.enabled = p.export_enabled
    sub.prop(p, "export_cull")
    sub.prop(p, "export_resize")
    sub.prop(p, "export_cameras")
    sub.prop(p, "export_triangulate")
    row = sub.row(align=True)
    row.enabled = p.export_resize
    row.prop(p, "export_min_size", text="Min")
    row.prop(p, "export_max_size", text="Max")
    sub.prop(p, "export_stale", text="If Stale")
    sub.prop(p, "export_report", text="Write Report")
    draw_compress(col, p)


def draw_compress(layout, p):
    layout.prop(p, "export_compress")
    if not p.export_compress:
        return
    c = layout.column(align=True)
    c.prop(p, "chopper_path", text="Chopper")
    c.prop(p, "chopper_recipe", text="Recipe")
    if not p.chopper_path:
        layout.label(text="Pick a folder with chopper installed", icon="ERROR")
    st = export.COMPRESS
    if st["state"]:
        icon = {"ok": "CHECKMARK", "running": "SORTTIME", "error": "ERROR"}[st["state"]]
        layout.label(text=st["text"], icon=icon)


class TC_PT_export(bpy.types.Panel):
    bl_label = "5. Export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Texel"
    bl_parent_id = "TC_PT_main"

    def draw(self, context):
        p = props_of(context)
        draw_export_options(self.layout, p)
        col = self.layout.column()
        col.operator("texel_coverage.plan", icon="IMAGE_DATA")
        col.operator("texel_coverage.export", icon="EXPORT")
        col.operator("texel_coverage.restore", icon="LOOP_BACK")


class TC_PT_uv_editor(bpy.types.Panel):
    bl_label = "Texel"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Texel"

    def draw(self, context):
        draw_uv_tools(self.layout, context)


def draw_gltf_export(context, layout):
    """Section inside File > Export > glTF 2.0."""
    p = props_of(context)
    header, body = layout.panel("TC_gltf_export", default_closed=False)
    header.use_property_split = False
    header.prop(p, "export_enabled", text="")
    header.label(text="Texel Optimize")
    if body:
        body.use_property_split = False
        st = core.cache_status(context, p)
        sub = body.column()
        sub.enabled = p.export_enabled
        sub.prop(p, "export_cull")
        sub.prop(p, "export_resize")
        sub.prop(p, "export_triangulate")
        row = sub.row(align=True)
        row.enabled = p.export_resize
        row.prop(p, "export_min_size", text="Min")
        row.prop(p, "export_max_size", text="Max")
        sub.prop(p, "export_stale", text="")
        sub.prop(p, "export_report", text="Write report")
        draw_compress(body, p)
        _status_row(sub, "Analysis", st["analysis"])
        _status_row(sub, "Cull", st["cull"])
        if p.export_enabled and (p.export_cull or p.export_triangulate):
            sub.label(text="Apply Modifiers is forced on", icon="INFO")


classes = (TC_Props, TC_OT_sync_cameras, TC_OT_analyze, TC_OT_show, TC_OT_use_scene_res, TC_OT_suggest,
           TC_OT_lock, TC_OT_select_cuts, TC_OT_promote, TC_OT_bake, TC_OT_revert_bake, TC_OT_checker, TC_OT_cull_preview, TC_OT_cull_clear, TC_OT_cull_override,
           TC_OT_prepare, TC_OT_plan, TC_OT_export, TC_OT_restore, TC_OT_verify,
           TC_PT_main, TC_PT_cameras, TC_PT_analyze, TC_PT_uvs, TC_PT_cull, TC_PT_export, TC_PT_uv_editor)
