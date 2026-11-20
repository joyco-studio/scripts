"""The Compare & Merge workspace: sidebar panels, the change list and the operators."""

import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, \
    PointerProperty, StringProperty

from . import api, diff, merge, preview, snapshot
from . import session as ses
from .preview import View
from .session import S

WS_NAME = "Compare & Merge"
WS_TAG = "cm_workspace"
CATEGORY = "Compare & Merge"

STATUS_ICON = {diff.CHANGED: "GREASEPENCIL", diff.RENAMED: "SORTALPHA", diff.ONLY_INCOMING: "ADD",
               diff.ONLY_CURRENT: "REMOVE", diff.SAME: "CHECKMARK"}
STATUS_TEXT = {
    diff.CHANGED: "Different in the two files",
    diff.RENAMED: "Same content, different name",
    diff.ONLY_INCOMING: "Only in the incoming file",
    diff.ONLY_CURRENT: "Only in this file",
    diff.SAME: "Identical in both files",
}

# Row choices. The two values are always CURRENT / INCOMING; what they mean depends on the row.
CHOICES = {
    diff.CHANGED: [("CURRENT", "Current", "Keep this file's version"),
                   ("INCOMING", "Incoming", "Take the incoming file's version")],
    diff.ONLY_INCOMING: [("CURRENT", "Skip", "Don't add it"), ("INCOMING", "Add", "Add it from the incoming file")],
    diff.ONLY_CURRENT: [("CURRENT", "Keep", "Keep it"), ("INCOMING", "Remove", "Remove it (it is not in the incoming file)")],
}
CHOICES[diff.RENAMED] = CHOICES[diff.CHANGED]
CHOICES[diff.SAME] = CHOICES[diff.CHANGED]

_syncing = False


def props(context=None):
    return (context or bpy.context).window_manager.compare_merge


def is_cm_workspace(ws):
    return ws is not None and ws.get(WS_TAG) is not None


def find_workspace():
    return next((ws for ws in bpy.data.workspaces if is_cm_workspace(ws)), None)


# ---------------------------------------------------------------------------------------------- state <-> rows

def sync_rows(p=None):
    """Mirror the comparison into the sidebar rows (and back to defaults when nothing is open)."""
    global _syncing
    p = p or props()
    _syncing = True
    try:
        if len(p.rows) != len(S.entries):
            p.rows.clear()
            for _ in S.entries:
                p.rows.add()
        for i, (row, e) in enumerate(zip(p.rows, S.entries)):
            row.index = i
            row.cat = e.cat
            row.status = e.status
            row.label = e.name
            row.n_changes = len(e.changes)
            row.choosable = ses.choosable(e)
            c = View.choices.get(i, "CURRENT")
            if row.choice != c:
                row.choice = c
        if p.active >= len(p.rows):
            p.active = len(p.rows) - 1
    finally:
        _syncing = False


def choice_items(self, _context):
    return CHOICES.get(self.status, CHOICES[diff.CHANGED])


def choice_update(self, _context):
    if _syncing or not S.active:
        return
    api.set_choice(self.index, self.choice, cascade=True)
    sync_rows()
    preview.rebuild()


def view_update(self, _context):
    if _syncing:
        return
    View.mode, View.focus, View.solo, View.active = self.view, self.focus, self.solo, self.active
    if S.active:
        preview.rebuild()


def active_update(self, _context):
    if _syncing:
        return
    View.active = self.active
    if S.active and (View.focus != "OFF" or View.solo):
        preview.rebuild()


class CM_Row(bpy.types.PropertyGroup):
    index: IntProperty()
    cat: StringProperty()
    status: StringProperty()
    label: StringProperty()
    n_changes: IntProperty()
    choosable: BoolProperty()
    choice: EnumProperty(name="Version", items=choice_items, update=choice_update)


class CM_Props(bpy.types.PropertyGroup):
    path: StringProperty(name="Incoming", subtype="FILE_PATH",
                         description="Another version of this .blend (an artist's copy, an older or newer save)")
    rows: CollectionProperty(type=CM_Row)
    active: IntProperty(update=active_update)
    view: EnumProperty(
        name="Show", update=view_update, default="MERGE",
        items=[("MERGE", "Merge Result", "What the file will look like after Merge, with the versions you picked"),
               ("CURRENT", "Current", "This file as it is"),
               ("INCOMING", "Incoming", "Everything from the incoming file")])
    focus: EnumProperty(
        name="Other Version", update=view_update, default="OFF",
        items=[("OFF", "Off", "Only show the picked version"),
               ("GHOST", "Ghost", "Overlay the other version of the selected row as a wireframe"),
               ("BLINK", "Blink", "Alternate between the two versions of the selected row")])
    solo: BoolProperty(name="Solo", update=view_update,
                       description="Hide everything the selected row doesn't affect")
    blink_rate: FloatProperty(name="Blink Rate", default=0.6, min=0.15, max=3.0, subtype="TIME_ABSOLUTE",
                              unit="TIME_ABSOLUTE", description="Seconds each version stays on screen")
    show_same: BoolProperty(name="Show Identical", description="Also list data-blocks that are the same in both files")
    category: EnumProperty(
        name="Type", items=[("ALL", "All Types", "", "NONE", 0)] +
        [(c, f"{label}s" if not label.endswith("h") else f"{label}es", "", icon, i + 1)
         for i, (c, _a, label, icon) in enumerate(snapshot.CATEGORIES)])
    message: StringProperty()


def row_visible(p, row):
    if not p.show_same and row.status == diff.SAME:
        return False
    return p.category == "ALL" or row.cat == p.category


# ---------------------------------------------------------------------------------------------- list

class CM_UL_rows(bpy.types.UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_propname, _index):
        split = layout.split(factor=0.5, align=True)
        row = split.row(align=True)
        row.label(text="", icon=STATUS_ICON.get(item.status, "DOT"))
        row.label(text=item.label, icon=snapshot.CAT_ICON.get(item.cat, "DOT"))
        row = split.row(align=True)
        if item.choosable:
            row.prop(item, "choice", expand=True)
        elif item.status == diff.SAME:
            row.label(text="identical")
        elif item.cat == "COLLECTION":
            row.label(text="follows its objects")
        else:
            row.label(text="with its objects")

    def filter_items(self, context, data, propname):
        p = props(context)
        rows = getattr(data, propname)
        flags = [self.bitflag_filter_item if row_visible(p, r) else 0 for r in rows]
        if self.filter_name:
            by_name = bpy.types.UI_UL_list.filter_items_by_name(self.filter_name, self.bitflag_filter_item, rows,
                                                                "label")
            flags = [a & b for a, b in zip(flags, by_name)]
        return flags, []


# ---------------------------------------------------------------------------------------------- operators

def preview_windows(context):
    for win in context.window_manager.windows:
        if is_cm_workspace(win.workspace):
            preview.show_preview(win)


def create_workspace(window, activate):
    """Duplicate the window's workspace into a 'Compare & Merge' tab with the sidebar on our panels."""
    prev = window.workspace
    before = set(bpy.data.workspaces[:])
    with bpy.context.temp_override(window=window, screen=window.screen):
        bpy.ops.workspace.duplicate()
    ws = next((w for w in bpy.data.workspaces if w not in before), None)
    if ws is None:
        return None
    ws.name = WS_NAME
    ws[WS_TAG] = 1
    for screen in ws.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.show_region_ui = True
                for region in area.regions:
                    if region.type == "UI":
                        try:
                            region.active_panel_category = CATEGORY
                        except (AttributeError, TypeError):
                            pass
    if not activate:
        window.workspace = prev
    return ws


class CM_OT_open_workspace(bpy.types.Operator):
    bl_idname = "compare_merge.open_workspace"
    bl_label = "Compare & Merge"
    bl_description = "Open the Compare & Merge tab to diff this file against another version and merge changes"

    def execute(self, context):
        ws = find_workspace() or create_workspace(context.window, activate=True)
        if ws is None:
            self.report({"ERROR"}, "Could not create the Compare & Merge tab")
            return {"CANCELLED"}
        context.window.workspace = ws
        return {"FINISHED"}


class CM_OT_compare(bpy.types.Operator):
    bl_idname = "compare_merge.compare"
    bl_label = "Compare"
    bl_description = "Diff this file against the incoming file and preview it. Nothing in this file changes"

    filepath: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    filter_glob: StringProperty(default="*.blend", options={"HIDDEN"})
    browse: BoolProperty(options={"SKIP_SAVE", "HIDDEN"})

    def invoke(self, context, _event):
        if self.browse or not props(context).path:
            if props(context).path:
                self.filepath = bpy.path.abspath(props(context).path)
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}
        return self.execute(context)

    def execute(self, context):
        p = props(context)
        if self.filepath:
            p.path = self.filepath
        if not p.path:
            self.report({"ERROR"}, "Pick the incoming .blend first")
            return {"CANCELLED"}
        scene = preview.original_scene() if S.active else context.scene
        if scene is not None and scene.is_runtime_data:
            scene = preview.original_scene()
        try:
            entries = api.start(bpy.path.abspath(p.path), scene.name if scene else None)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        p.active = -1
        sync_rows(p)
        View.mode, View.focus, View.solo = p.view, p.focus, p.solo
        preview_windows(context)
        st = diff.stat(entries)
        n = st[diff.CHANGED] + st[diff.RENAMED] + st[diff.ONLY_INCOMING] + st[diff.ONLY_CURRENT]
        self.report({"INFO"}, f"{n} differences ({st[diff.SAME]} identical)")
        return {"FINISHED"}


class CM_OT_refresh(bpy.types.Operator):
    bl_idname = "compare_merge.refresh"
    bl_label = "Refresh"
    bl_description = "Read this file again (after you edited it) and update the diff. Your picks are kept"

    @classmethod
    def poll(cls, _context):
        return S.active

    def execute(self, context):
        api.refresh()
        sync_rows()
        return {"FINISHED"}


class CM_OT_close(bpy.types.Operator):
    bl_idname = "compare_merge.close"
    bl_label = "Close"
    bl_description = "Stop comparing: remove the preview and unload the incoming file. This file is not changed"

    @classmethod
    def poll(cls, _context):
        return S.active

    def execute(self, context):
        api.close()
        props(context).rows.clear()
        return {"FINISHED"}


class CM_OT_set_all(bpy.types.Operator):
    bl_idname = "compare_merge.set_all"
    bl_label = "Set All"
    bl_description = "Pick this version for every row currently listed"

    choice: EnumProperty(items=[("CURRENT", "Current", ""), ("INCOMING", "Incoming", "")])

    @classmethod
    def poll(cls, _context):
        return S.active

    def execute(self, context):
        p = props(context)
        for row in p.rows:
            if row.choosable and row_visible(p, row):
                View.choices[row.index] = self.choice
        sync_rows(p)
        preview.rebuild()
        return {"FINISHED"}


class CM_OT_flip(bpy.types.Operator):
    bl_idname = "compare_merge.flip"
    bl_label = "Flip"
    bl_description = "Switch the selected row between its two versions"

    @classmethod
    def poll(cls, context):
        p = props(context)
        return S.active and 0 <= p.active < len(p.rows) and p.rows[p.active].choosable

    def execute(self, context):
        row = props(context).rows[props(context).active]
        row.choice = "CURRENT" if row.choice == "INCOMING" else "INCOMING"
        return {"FINISHED"}


def row_objects(context, idx):
    """Preview-scene objects that show the given row."""
    sc = preview.preview_scene()
    if sc is None or not (0 <= idx < len(S.entries)):
        return []
    e = S.entries[idx]
    names = {e.current, e.incoming} - {""} if e.cat == "OBJECT" else set(api.used_by(e))
    vl = sc.view_layers[0]
    out = []
    for ob in sc.objects:
        if ob.get("cm_src", ob.name) in names and ob.visible_get(view_layer=vl):
            out.append(ob)
    return out


class CM_OT_frame(bpy.types.Operator):
    bl_idname = "compare_merge.frame"
    bl_label = "Frame"
    bl_description = "Select and frame what the selected row affects in the preview"

    @classmethod
    def poll(cls, context):
        return S.active and context.area is not None and context.area.type == "VIEW_3D"

    def execute(self, context):
        obs = row_objects(context, props(context).active)
        if not obs:
            self.report({"INFO"}, "Nothing visible to frame for this row")
            return {"CANCELLED"}
        vl = context.view_layer
        for ob in vl.objects:
            if ob.select_get():
                ob.select_set(False)
        for ob in obs:
            try:
                ob.select_set(True)
            except RuntimeError:
                pass
        vl.objects.active = obs[0]
        region = next((r for r in context.area.regions if r.type == "WINDOW"), None)
        with context.temp_override(region=region):
            bpy.ops.view3d.view_selected()
        return {"FINISHED"}


class CM_OT_merge(bpy.types.Operator):
    bl_idname = "compare_merge.merge"
    bl_label = "Merge"
    bl_description = "Bring every row set to Incoming / Add / Remove into this file. Ctrl+Z undoes it"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, _context):
        return S.active and any(c == "INCOMING" for c in View.choices.values())

    def invoke(self, context, event):
        n = sum(1 for c in View.choices.values() if c == "INCOMING")
        return context.window_manager.invoke_confirm(
            self, event, title="Merge into this file?", confirm_text="Merge",
            message=f"{n} rows take the incoming version. You can undo with Ctrl+Z.")

    def execute(self, context):
        # Never push undo steps from in here (a nested ed.undo_push corrupts the undo stack and the
        # next Ctrl+Z crashes). If a merge fails after it already swapped real data, finish anyway:
        # Blender records this operator's undo step and one Ctrl+Z restores the file.
        try:
            report = api.merge()
        except Exception as e:
            import traceback
            traceback.print_exc()
            touched = merge.LAST_TOUCHED
            api.recover_after_failed_merge()
            sync_rows()
            preview_windows(context)
            if touched:
                self.report({"ERROR"}, f"Merge failed partway: {e}. Press Ctrl+Z to restore the file")
                return {"FINISHED"}
            self.report({"ERROR"}, f"Merge failed: {e}. Nothing in the file was changed")
            return {"CANCELLED"}
        sync_rows()
        preview_windows(context)
        props(context).message = report
        self.report({"INFO"}, report)
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------- panels

class CM_Panel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    @classmethod
    def poll(cls, context):
        return is_cm_workspace(context.workspace)


class CM_PT_compare(CM_Panel, bpy.types.Panel):
    bl_label = "Compare & Merge"

    def draw(self, context):
        p = props(context)
        layout = self.layout
        if not S.active:
            col = layout.column()
            col.label(text="Diff this file against another version of it.")
            col.label(text="Nothing here changes until you press Merge.")
            row = layout.row(align=True)
            row.prop(p, "path", text="")
            row = layout.row()
            row.scale_y = 1.4
            row.operator(CM_OT_compare.bl_idname, text="Compare", icon="ARROW_LEFTRIGHT")
            return
        box = layout.box()
        col = box.column(align=True)
        col.label(text=f"Current:  {os.path.basename(bpy.data.filepath) or 'unsaved file'}", icon="FILE_BLEND")
        r = col.row(align=True)
        r.label(text=f"Incoming: {os.path.basename(S.path)}", icon="IMPORT")
        r.operator(CM_OT_compare.bl_idname, text="", icon="FILEBROWSER").browse = True
        st = diff.stat(S.entries)
        box.label(text=f"{st[diff.CHANGED] + st[diff.RENAMED]} changed · {st[diff.ONLY_INCOMING]} new · "
                       f"{st[diff.ONLY_CURRENT]} missing · {st[diff.SAME]} identical")
        row = layout.row(align=True)
        row.operator(CM_OT_refresh.bl_idname, icon="FILE_REFRESH")
        row.operator(CM_OT_close.bl_idname, icon="X")


class CM_PT_preview(CM_Panel, bpy.types.Panel):
    bl_label = "Preview"

    @classmethod
    def poll(cls, context):
        return super().poll(context) and S.active

    def draw(self, context):
        p = props(context)
        layout = self.layout
        layout.row().prop(p, "view", expand=True)
        col = layout.column(align=True)
        col.label(text="Selected row's other version:")
        row = col.row(align=True)
        row.prop(p, "focus", expand=True)
        row = layout.row(align=True)
        row.prop(p, "solo", toggle=True, icon="HIDE_OFF" if not p.solo else "HIDE_ON")
        row.operator(CM_OT_flip.bl_idname, icon="ARROW_LEFTRIGHT")
        row.operator(CM_OT_frame.bl_idname, icon="ZOOM_SELECTED")
        if p.focus == "BLINK":
            layout.prop(p, "blink_rate")
        layout.label(text="Preview only: saving never stores it.", icon="LOCKED")


class CM_PT_changes(CM_Panel, bpy.types.Panel):
    bl_label = "Changes"

    @classmethod
    def poll(cls, context):
        return super().poll(context) and S.active

    def draw(self, context):
        p = props(context)
        layout = self.layout
        row = layout.row(align=True)
        row.prop(p, "category", text="")
        row.prop(p, "show_same", text="", icon="CHECKMARK")
        layout.template_list("CM_UL_rows", "", p, "rows", p, "active", rows=12)
        row = layout.row(align=True)
        row.label(text="Listed rows:")
        row.operator(CM_OT_set_all.bl_idname, text="All Current").choice = "CURRENT"
        row.operator(CM_OT_set_all.bl_idname, text="All Incoming").choice = "INCOMING"


class CM_PT_details(CM_Panel, bpy.types.Panel):
    bl_label = "Details"

    @classmethod
    def poll(cls, context):
        p = props(context)
        return super().poll(context) and S.active and 0 <= p.active < len(S.entries)

    def draw(self, context):
        p = props(context)
        e = S.entries[p.active]
        layout = self.layout
        layout.label(text=f"{snapshot.CAT_LABEL[e.cat]}: {e.name}", icon=snapshot.CAT_ICON[e.cat])
        layout.label(text=STATUS_TEXT[e.status], icon=STATUS_ICON[e.status])
        if e.cat == "MESH" and e.status == diff.CHANGED:
            stats = api.mesh_stats(e)
            if stats:
                layout.label(text=stats, icon="VERTEXSEL")
        if e.cat != "OBJECT":
            users = api.used_by(e)
            if users:
                layout.label(text="Used by: " + ", ".join(users[:6]) + (" …" if len(users) > 6 else ""),
                             icon="OBJECT_DATA")
        if e.cat == "OBJECT" and ses.choosable(e):
            deps = ses.dependents(e)
            if deps:
                layout.label(text="Also changed: " + ", ".join(S.entries[d].name for d in deps[:5]), icon="LINKED")
        if not e.changes:
            return
        # Field on its own line, then current | incoming: readable in a narrow sidebar.
        col = layout.column(align=True)
        head = col.split(factor=0.5)
        head.label(text="Current", icon="FILE_BLEND")
        head.label(text="Incoming", icon="IMPORT")
        for field, a, b in e.changes[:60]:
            box = col.box()
            sub = box.column(align=True)
            sub.label(text=field)
            split = sub.split(factor=0.5)
            split.label(text=a)
            split.label(text=b)
        if len(e.changes) > 60:
            col.label(text=f"… {len(e.changes) - 60} more")


class CM_PT_merge(CM_Panel, bpy.types.Panel):
    bl_label = "Merge"

    @classmethod
    def poll(cls, context):
        return super().poll(context) and S.active

    def draw(self, context):
        p = props(context)
        layout = self.layout
        picked = [S.entries[i] for i, c in View.choices.items() if c == "INCOMING" and i < len(S.entries)
                  and ses.choosable(S.entries[i])]
        upd = sum(1 for e in picked if e.status in (diff.CHANGED, diff.RENAMED))
        add = sum(1 for e in picked if e.status == diff.ONLY_INCOMING)
        rem = sum(1 for e in picked if e.status == diff.ONLY_CURRENT)
        layout.label(text=f"{upd} to update · {add} to add · {rem} to remove")
        row = layout.row()
        row.scale_y = 1.5
        row.operator(CM_OT_merge.bl_idname, text="Merge into This File", icon="CHECKMARK")
        if p.message:
            layout.label(text=p.message, icon="INFO")


# ---------------------------------------------------------------------------------------------- menus, prefs

def draw_file_menu(self, _context):
    self.layout.separator()
    self.layout.operator(CM_OT_open_workspace.bl_idname, text="Compare & Merge…", icon="ARROW_LEFTRIGHT")


class CM_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    auto_tab: BoolProperty(
        name="Add the Compare & Merge tab to every file", default=True,
        description="Add the tab when a file opens. Off: use File > Compare & Merge… to add it when needed")

    def draw(self, _context):
        self.layout.prop(self, "auto_tab")


def prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


classes = (CM_Row, CM_Props, CM_UL_rows, CM_OT_open_workspace, CM_OT_compare, CM_OT_refresh, CM_OT_close,
           CM_OT_set_all, CM_OT_flip, CM_OT_frame, CM_OT_merge, CM_PT_compare, CM_PT_preview, CM_PT_changes,
           CM_PT_details, CM_PT_merge, CM_Preferences)
