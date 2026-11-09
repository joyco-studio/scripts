"""Texel Coverage: camera-driven texture sizes, UV layout and geometry culling.

Panels: 3D View > Sidebar > Texel, UV Editor > Sidebar > Texel,
and a "Texel Optimize" section in File > Export > glTF 2.0.
"""

if "bpy" in locals():
    import importlib
    for _m in (core, cull, export, uv_suggest, overlay, api, ui):  # noqa: F821
        importlib.reload(_m)
else:
    from . import core, cull, export, uv_suggest, overlay, api, ui

import bpy
from bpy.app.handlers import persistent


# The stock glTF exporter looks for these on every enabled add-on.
def glTF2_pre_export_callback(export_settings):
    export.pre_export(export_settings)


def glTF2_post_export_callback(export_settings):
    export.post_export(export_settings)


@persistent
def _on_load(_):
    core.mark_dirty()
    try:
        export.restore(bpy.context)  # an export that never finished before the file was saved
    except Exception as e:
        print("Texel Coverage: restore on load failed:", e)


def _gltf_ui(register):
    try:
        import io_scene_gltf2
    except ImportError:
        return
    draws = getattr(io_scene_gltf2, "exporter_extension_layout_draw", None)
    if draws is None:
        return
    if register:
        draws["Texel Optimize"] = ui.draw_gltf_export
    else:
        draws.pop("Texel Optimize", None)


def register():
    for c in ui.classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.texel_coverage = bpy.props.PointerProperty(type=ui.TC_Props)
    bpy.app.handlers.depsgraph_update_post.append(core.mark_dirty)
    bpy.app.handlers.load_post.append(_on_load)
    overlay.register()
    _gltf_ui(True)


def unregister():
    _gltf_ui(False)
    overlay.unregister()
    ui.free_swatches()
    for h, f in ((bpy.app.handlers.depsgraph_update_post, core.mark_dirty),
                 (bpy.app.handlers.load_post, _on_load)):
        if f in h:
            h.remove(f)
    del bpy.types.Scene.texel_coverage
    for c in reversed(ui.classes):
        bpy.utils.unregister_class(c)
