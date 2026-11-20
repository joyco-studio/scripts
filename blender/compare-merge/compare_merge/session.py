"""One comparison: the incoming file linked into this session, the diff, and the choices.

Safety model: everything this add-on adds to bpy.data while comparing (the linked library and
every ID linked from it, the preview scene, preview objects and materials) is flagged
`is_runtime_data`, which Blender never writes to disk. Saving, autosaving or quitting mid-preview
therefore can't put preview data into the .blend. Only "Merge" (merge.py) changes real data.

State lives in this module (Python objects) plus the WindowManager rows the sidebar shows; IDs are
always looked up by name, because undo replaces every ID and invalidates Python references.
"""

import os

import bpy

from . import diff, snapshot

DRAFT_TAG = "cm_draft"
PREVIEW_SCENE = "Compare & Merge Preview"


class State:
    def __init__(self):
        self.path = ""  # absolute path of the incoming .blend
        self.lib_filepath = ""  # Library.filepath as Blender stores it (lookup key for linked IDs)
        self.scene = ""  # name of the scene being previewed
        self.entries = []  # [diff.Entry], same order as the sidebar rows
        self.incoming_snap = {}
        self.current_snap = {}
        self.index_cur = {}  # (cat, current name) -> entry index
        self.index_inc = {}  # (cat, incoming name) -> entry index
        self.incoming_scenes = {}  # scene name -> settings (linked scenes can't stay linked, see link())
        self.incoming_members = {}  # object name -> [(in a scene's top level, collection name)]
        self.incoming_colls = {}  # collection name -> parents and settings (to rebuild new nested collections)

    @property
    def active(self):
        return bool(self.path)


S = State()


def reset():
    """Clear the comparison in place: other modules hold this object (`from .session import S`)."""
    S.__init__()


# ---------------------------------------------------------------------------------------------- ID helpers

def id_collections():
    out = []
    for p in bpy.types.BlendData.bl_rna.properties:
        if p.type != "COLLECTION" or p.identifier in ("libraries", "window_managers", "screens", "workspaces", "all_ids"):
            continue
        coll = getattr(bpy.data, p.identifier, None)
        if isinstance(coll, bpy.types.bpy_prop_collection):
            out.append(coll)
    return out


def all_ids():
    for coll in id_collections():
        yield from coll


def norm(path):
    return os.path.normcase(os.path.normpath(bpy.path.abspath(path)))


def library():
    if not S.path:
        return None
    for lib in bpy.data.libraries:
        if norm(lib.filepath) == S.path:
            return lib
    return None


def is_current(idb):
    return idb.library is None and not idb.is_runtime_data


def is_incoming(idb, lib=None):
    lib = lib or library()
    return lib is not None and idb.library == lib


def cat_of(idb):
    return snapshot.ATTR_CAT.get(_attr_of(idb))


def _attr_of(idb):
    t = idb.id_type  # 'OBJECT', 'MESH', 'NODETREE', ...
    return {"OBJECT": "objects", "MESH": "meshes", "MATERIAL": "materials", "IMAGE": "images",
            "NODETREE": "node_groups", "CAMERA": "cameras", "LIGHT": "lights", "ACTION": "actions",
            "ARMATURE": "armatures", "CURVE": "curves", "FONT": "curves", "WORLD": "worlds",
            "COLLECTION": "collections", "SCENE": "scenes"}.get(t)


def get_current(cat, name):
    if not name:
        return None
    idb = getattr(bpy.data, snapshot.CAT_ATTR[cat]).get((name, None))
    return idb if idb is not None and not idb.is_runtime_data else None


def get_incoming(cat, name):
    if not name or not S.lib_filepath:
        return None
    return getattr(bpy.data, snapshot.CAT_ATTR[cat]).get((name, S.lib_filepath))


def entry_of(idb):
    """Index of the entry an ID belongs to (current or incoming side), or None."""
    cat = cat_of(idb)
    if cat is None:
        return None
    if is_current(idb):
        return S.index_cur.get((cat, idb.name))
    if S.lib_filepath and idb.library and idb.library.filepath == S.lib_filepath:
        return S.index_inc.get((cat, idb.name))
    return None


def mark_draft(idb):
    idb.is_runtime_data = True
    idb[DRAFT_TAG] = 1
    return idb


def is_draft(idb):
    return idb.get(DRAFT_TAG) is not None if hasattr(idb, "get") else False


# ---------------------------------------------------------------------------------------------- linking

def link(path, scenes=False):
    """Link every comparable data-block of `path` into this session as runtime-only data.

    Scenes only while reading them: Blender writes linked scenes into the file on save even when
    they are flagged as runtime data, so compare() reads what it needs and drops them again."""
    before_libs = {lib.as_pointer() for lib in bpy.data.libraries}
    before = {i.as_pointer() for i in all_ids()}
    with bpy.data.libraries.load(path, link=True) as (src, dst):
        for _cat, attr, _label, _icon in snapshot.CATEGORIES:
            if attr != "scenes" or scenes:
                setattr(dst, attr, list(getattr(src, attr)))
    for lib in bpy.data.libraries:
        if lib.as_pointer() not in before_libs:
            lib.is_runtime_data = True
    for idb in all_ids():
        if idb.library is not None and idb.as_pointer() not in before:
            idb.is_runtime_data = True
    lib = library()
    if lib is None:
        raise RuntimeError(f"Blender did not load {path}")
    S.lib_filepath = lib.filepath


def unlink():
    """Remove everything `link` added (only runtime data, so nothing the user owns)."""
    doomed = [i for i in all_ids() if i.library is not None and i.is_runtime_data]
    if doomed:
        bpy.data.batch_remove(doomed)
    libs = [l for l in bpy.data.libraries if l.is_runtime_data]
    if libs:
        bpy.data.batch_remove(libs)
    if library() is None:
        S.lib_filepath = ""


def scene_settings(s):
    """The scene settings Compare & Merge diffs, previews and merges (snapshot.snap_scene shows them)."""
    r = s.render
    return dict(camera=s.camera.name if s.camera else None, world=s.world.name if s.world else None,
                frame_start=s.frame_start, frame_end=s.frame_end, fps=r.fps, fps_base=r.fps_base,
                resolution_x=r.resolution_x, resolution_y=r.resolution_y,
                resolution_percentage=r.resolution_percentage, scale_length=s.unit_settings.scale_length)


def drop_linked_scenes():
    """Read what the merge and the preview need from the incoming scenes, then unlink them."""
    lib = library()
    scenes = [s for s in bpy.data.scenes if s.library == lib and s.is_runtime_data]
    masters = {s.collection.as_pointer() for s in scenes}
    S.incoming_members = {
        ob.name: [(c.as_pointer() in masters, c.name) for c in ob.users_collection]
        for ob in bpy.data.objects if ob.library == lib}
    S.incoming_scenes = {s.name: scene_settings(s) for s in scenes}
    colls = [c for c in bpy.data.collections if c.library == lib]
    S.incoming_colls = {c.name: dict(parents=[], hide_render=c.hide_render, hide_viewport=c.hide_viewport,
                                     instance_offset=tuple(c.instance_offset)) for c in colls}
    for parent in colls + [s.collection for s in scenes]:
        for child in parent.children:
            if child.name in S.incoming_colls:
                S.incoming_colls[child.name]["parents"].append(
                    (parent.as_pointer() in masters, "" if parent.as_pointer() in masters else parent.name))
    if scenes:
        bpy.data.batch_remove(scenes)


def ensure_linked():
    """Undo / redo can drop the runtime library or leave a second copy of it: end with exactly one."""
    if not S.active:
        return False
    libs = [l for l in bpy.data.libraries if norm(l.filepath) == S.path]
    if len(libs) == 1:
        S.lib_filepath = libs[0].filepath
        return False
    if libs:
        unlink()
    link(S.path)
    return True


# ---------------------------------------------------------------------------------------------- comparing

def compare(path, scene_name):
    path = norm(path)
    if not os.path.isfile(path):
        raise RuntimeError(f"No such file: {path}")
    if bpy.data.filepath and norm(bpy.data.filepath) == path:
        raise RuntimeError("That is the file you have open. Pick another version to compare with.")
    if S.active and S.path != path:
        close_library()
    S.path = path
    S.scene = scene_name
    if library() is not None:
        unlink()
    link(path, scenes=True)
    S.incoming_snap = snapshot.snapshot(lambda i: is_incoming(i))
    drop_linked_scenes()
    refresh_current()


def refresh_current():
    """Re-read the current file (after edits or a merge) and diff again; the incoming side is cached."""
    S.current_snap = snapshot.snapshot(lambda i: is_current(i) and not is_draft(i))
    S.entries = diff.diff(S.current_snap, S.incoming_snap, snapshot.CAT_ORDER)
    S.index_cur = {(e.cat, e.current): i for i, e in enumerate(S.entries) if e.current}
    S.index_inc = {(e.cat, e.incoming): i for i, e in enumerate(S.entries) if e.incoming}


def close_library():
    unlink()


def choosable(e):
    """Rows where Current / Incoming is a real choice. Types other than objects that exist on one
    side only come along with (or stay with) the objects that use them."""
    if e.cat == "COLLECTION":
        return False
    if e.cat == "OBJECT":
        return e.status != diff.SAME
    return e.status in (diff.CHANGED, diff.RENAMED)


def dependents(e):
    """Changed entries an object/material is made of (data, materials, images, action), recursively."""
    out = []
    seen = set()
    stack = [i for i in (get_incoming(e.cat, e.incoming), get_current(e.cat, e.current)) if i is not None]
    while stack:
        idb = stack.pop()
        for ref in snapshot.references(idb):
            k = ref.as_pointer()
            if k in seen:
                continue
            seen.add(k)
            stack.append(ref)
            idx = entry_of(ref)
            if idx is not None and choosable(S.entries[idx]) and S.entries[idx].cat != "OBJECT":
                out.append(idx)
    return sorted(set(out))
