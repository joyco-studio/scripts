"""Compare & Merge: diff this .blend against another version of it, preview either version of
anything that differs, and merge the versions you pick. Git-style, for the data a glTF export is
made of (objects, meshes, materials, images, cameras, lights, actions, armatures, ...).

The "Compare & Merge" workspace tab holds everything (3D View > Sidebar > Compare & Merge).
"""

if "bpy" in locals():
    import importlib
    for _m in (snapshot, diff, session, preview, merge, api, ui):  # noqa: F821
        importlib.reload(_m)
else:
    from . import snapshot, diff, session, preview, merge, api, ui

import time

import bpy
from bpy.app.handlers import persistent

from .preview import View
from .session import S

_saved_windows = []
_last = {"blink": 0.0, "active": {}, "workspace": {}, "tabbed": set()}
_stale = {"flag": False}


def _reset_rows():
    wm = bpy.context.window_manager
    if wm is not None and hasattr(wm, "compare_merge"):
        wm.compare_merge.rows.clear()
        wm.compare_merge.message = ""


@persistent
def _save_pre(*_):
    # The preview scene is runtime data and never written, but windows must not point at it.
    _saved_windows[:] = preview.show_original() if S.active else []


@persistent
def _save_post(*_):
    for win in _saved_windows:
        preview.show_preview(win)
    _saved_windows.clear()


@persistent
def _load_pre(*_):
    session.reset()
    View.choices, View.active = {}, -1


@persistent
def _load_post(*_):
    _reset_rows()
    bpy.app.timers.register(_ensure_tab, first_interval=0.05)


@persistent
def _undo_post(*_):
    # Undo restores the real data and drops runtime data (the linked file, the preview): rebuild.
    if S.active:
        bpy.app.timers.register(_after_undo, first_interval=0.0)


def _after_undo():
    if S.active:
        try:
            api.refresh(after_undo=True)
            bpy.context.window_manager.compare_merge.message = ""
            ui.sync_rows()
            for win in bpy.context.window_manager.windows:
                if ui.is_cm_workspace(win.workspace):
                    preview.show_preview(win)
        except Exception as e:
            print("Compare & Merge: could not rebuild the preview after undo:", e)
    return None


@persistent
def _depsgraph_post(scene, depsgraph):
    if S.active and not scene.is_runtime_data:
        _stale["flag"] = True  # the real scene was edited (in another tab): refresh on return


def _ensure_tab():
    p = ui.prefs()
    wm = bpy.context.window_manager
    if p is None or not p.auto_tab or wm is None or not wm.windows or ui.find_workspace() is not None:
        return None
    try:
        ui.create_workspace(wm.windows[0], activate=False)
    except Exception as e:
        print("Compare & Merge: could not add the tab:", e)
    return None


def _show_our_sidebar(wm):
    """Open the sidebar of the tab's 3D views on our category, once per view (then it's the user's)."""
    for win in wm.windows:
        if not ui.is_cm_workspace(win.workspace):
            continue
        for area in win.screen.areas:
            if area.type != "VIEW_3D" or area.as_pointer() in _last["tabbed"]:
                continue
            region = next((r for r in area.regions if r.type == "UI"), None)
            if region is None or region.width <= 1:
                continue
            try:
                region.active_panel_category = ui.CATEGORY
            except (AttributeError, TypeError, ValueError):
                pass
            if getattr(region, "active_panel_category", ui.CATEGORY) == ui.CATEGORY:
                _last["tabbed"].add(area.as_pointer())
                area.tag_redraw()


def _tick():
    """Keep each window on the right scene, run Blink, and select the row of the clicked object."""
    wm = bpy.context.window_manager
    if wm is None:
        return 0.25
    _show_our_sidebar(wm)
    if not S.active:
        return 0.25
    p = wm.compare_merge
    for win in wm.windows:
        in_cm = ui.is_cm_workspace(win.workspace)
        was = _last["workspace"].get(win.as_pointer())
        _last["workspace"][win.as_pointer()] = in_cm
        if in_cm:
            if was is False and _stale["flag"]:
                _stale["flag"] = False
                try:
                    api.refresh()
                    ui.sync_rows()
                except Exception as e:
                    print("Compare & Merge: refresh failed:", e)
            preview.show_preview(win)
            ob = win.view_layer.objects.active if win.view_layer else None
            key = ob.name_full if ob else None
            if key == View.row_pick:
                pass  # made active by selecting a row: keep that row
            elif key and key != _last["active"].get(win.as_pointer()):
                View.row_pick = None
                src = ob.get("cm_src", ob.name)
                side = session.S.index_inc if ob.library else session.S.index_cur
                idx = side.get(("OBJECT", src))
                if idx is None:
                    idx = session.S.index_cur.get(("OBJECT", src), session.S.index_inc.get(("OBJECT", src)))
                if idx is not None and idx != p.active:
                    ui.set_active_from_viewport(p, idx)
            _last["active"][win.as_pointer()] = key
        else:
            preview.show_original(win)
    if View.focus == "BLINK" and time.monotonic() - _last["blink"] >= p.blink_rate:
        _last["blink"] = time.monotonic()
        preview.blink_tick()
        for win in wm.windows:
            for area in win.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    return 0.05 if View.focus == "BLINK" else 0.2


HANDLERS = (
    (bpy.app.handlers.save_pre, _save_pre), (bpy.app.handlers.save_post, _save_post),
    (bpy.app.handlers.load_pre, _load_pre), (bpy.app.handlers.load_post, _load_post),
    (bpy.app.handlers.undo_post, _undo_post), (bpy.app.handlers.redo_post, _undo_post),
    (bpy.app.handlers.depsgraph_update_post, _depsgraph_post),
)


def register():
    for c in ui.classes:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.compare_merge = bpy.props.PointerProperty(type=ui.CM_Props)
    bpy.types.TOPBAR_MT_file.append(ui.draw_file_menu)
    for h, f in HANDLERS:
        h.append(f)
    if not bpy.app.background:
        bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)
        bpy.app.timers.register(_ensure_tab, first_interval=0.2)


def unregister():
    if S.active:
        try:
            api.close()
        except Exception as e:
            print("Compare & Merge: close failed:", e)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for h, f in HANDLERS:
        if f in h:
            h.remove(f)
    bpy.types.TOPBAR_MT_file.remove(ui.draw_file_menu)
    del bpy.types.WindowManager.compare_merge
    for c in reversed(ui.classes):
        bpy.utils.unregister_class(c)
