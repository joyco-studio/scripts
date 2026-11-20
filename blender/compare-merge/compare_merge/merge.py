"""Apply the chosen incoming versions to the real file. The only part of the add-on that edits data.

1. The preview and the linked library go away (so nothing below can touch them).
2. Local data-blocks whose names clash with the incoming file are renamed out of the way, so the
   appended copies keep their exact incoming names and can be matched to their rows.
3. The chosen incoming objects / data-blocks are appended. Blender brings their dependencies too.
4. Every appended data-block is reconciled with its row:
     chosen incoming  -> the current one is replaced everywhere (user_remap) and removed
     only incoming    -> kept (a new object, or new data an incoming object needs)
     anything else    -> the appended duplicate is dropped and its users point at the current one
5. Names are restored, new objects join the collections they are in in the incoming file, removals
   are applied and leftovers of the append are cleaned up.
If anything raises, Job.rollback() drops what was appended and restores every name. If real data
was already swapped, the operator still finishes, so its undo step exists and Ctrl+Z restores it.

The operator runs with UNDO, so Ctrl+Z takes the whole merge back.
"""

import bpy

from . import diff, preview, snapshot
from . import session as ses
from .session import S


def plan(choices):
    """(entries to bring, objects to remove, scene entries) for {entry index: choice}."""
    bring, remove, scenes = [], [], []
    for idx, c in choices.items():
        e = S.entries[idx]
        if c != "INCOMING" or not ses.choosable(e):
            continue
        if e.cat == "SCENE":
            scenes.append(e)
            # A camera or world that only exists in the incoming file has to come in with the scene.
            st = S.incoming_scenes.get(e.incoming) or {}
            for cat, name in (("OBJECT", st.get("camera")), ("WORLD", st.get("world"))):
                dep = S.index_inc.get((cat, name))
                if dep is not None and S.entries[dep].status in (diff.ONLY_INCOMING, diff.RENAMED) \
                        and S.entries[dep] not in bring:
                    bring.append(S.entries[dep])
        elif e.status == diff.ONLY_CURRENT:
            if e.cat == "OBJECT":
                remove.append(e)
        elif e not in bring:
            bring.append(e)
    return bring, remove, scenes


def alive(idb):
    try:
        idb.name
        return True
    except ReferenceError:
        return False


def attr_of(idb):
    return ses._attr_of(idb)


def local(cat, name):
    return getattr(bpy.data, snapshot.CAT_ATTR[cat]).get((name, None)) if name else None


def apply(choices, scene_name):
    """Merge; returns a short report. `choices`: {entry index: "CURRENT" | "INCOMING"}."""
    bring, remove, scene_entries = plan(choices)
    if not (bring or remove or scene_entries):
        return "Nothing to merge: every row is set to Current."
    path = S.path
    chosen = {(e.cat, e.incoming) for e in bring}
    member, inc_scenes = S.incoming_members, S.incoming_scenes

    preview.teardown()
    ses.unlink()

    with bpy.data.libraries.load(path) as (src, _dst):
        names = {a: set(getattr(src, a)) for a in dir(src) if isinstance(getattr(src, a, None), list)}

    global LAST_TOUCHED
    job = Job()
    try:
        return run(job, path, names, bring, remove, scene_entries, chosen, member, inc_scenes, scene_name)
    except Exception:
        job.rollback()
        raise
    finally:
        LAST_TOUCHED = job.touched


LAST_TOUCHED = False  # did the last merge change real data (even if it then failed)?


class Job:
    """What a merge has done so far, so a failure can be cleaned up."""

    def __init__(self):
        self.renamed = []  # [(local id, original name)]
        self.new = []  # appended data-blocks
        self.before_libs = set()
        self.touched = False  # real data was remapped or removed (only undo can take that back)

    def restore_names(self, gone=()):
        taken = {}
        for n in self.new:
            if alive(n) and n.as_pointer() not in gone:
                taken.setdefault((attr_of(n), n.name), n)
        for idb, name in self.renamed:
            if not alive(idb) or idb.as_pointer() in gone:
                continue
            clash = taken.get((attr_of(idb), name))
            if clash is not None and alive(clash) and clash != idb:
                clash.name = f"{name} (incoming)"
            idb.name = name
        self.renamed = []

    def rollback(self):
        """Best effort after an error: drop what was appended and give everything its name back.
        If real data was already swapped, the operator tells the artist to press Ctrl+Z."""
        unused = [n for n in self.new if alive(n) and (not self.touched or n.users == 0)]
        if unused:
            bpy.data.batch_remove(unused)
        self.restore_names()
        libs = [l for l in bpy.data.libraries if l.as_pointer() not in self.before_libs and not l.is_runtime_data]
        if libs:
            bpy.data.batch_remove(libs)


def run(job, path, names, bring, remove, scene_entries, chosen, member, inc_scenes, scene_name):
    renamed = job.renamed
    for attr in names:
        coll = getattr(bpy.data, attr, None)
        if attr in ("libraries", "workspaces", "screens", "window_managers") or \
                not isinstance(coll, bpy.types.bpy_prop_collection):
            continue
        for idb in list(coll):
            if idb.library is None and idb.name in names[attr]:
                renamed.append((idb, idb.name))
                idb.name = f"cm~{len(renamed)}"

    def current_of(e):
        """The local ID of an entry, wherever its name went during the merge."""
        for idb, n in renamed:
            if n == e.current and alive(idb) and attr_of(idb) == snapshot.CAT_ATTR[e.cat]:
                return idb
        return local(e.cat, e.current)

    before = {i.as_pointer() for i in ses.all_ids()}
    job.before_libs = {l.as_pointer() for l in bpy.data.libraries}
    by_attr = {}
    for e in bring:
        by_attr.setdefault(snapshot.CAT_ATTR[e.cat], []).append(e.incoming)
    with bpy.data.libraries.load(path, link=False) as (_src, dst):
        for attr, wanted in by_attr.items():
            setattr(dst, attr, wanted)
    new = job.new = [i for i in ses.all_ids() if i.as_pointer() not in before and i.library is None]

    def local_for(cat, incoming_name):
        """Local data-block that stands for an incoming name after the merge (renames included)."""
        idx = S.index_inc.get((cat, incoming_name))
        if idx is None:
            return local(cat, incoming_name)
        e = S.entries[idx]
        if (cat, e.incoming) in chosen:
            return local(cat, e.incoming)
        return local(cat, e.current) or local(cat, e.incoming)

    replaced, kept, dropped, skipped = [], [], [], []
    for n in new:
        cat = snapshot.ATTR_CAT.get(attr_of(n))
        idx = S.index_inc.get((cat, n.name)) if cat else None
        if idx is None:
            continue  # untracked (shape keys, textures...): stays with whoever uses it
        e = S.entries[idx]
        if e.status == diff.ONLY_INCOMING:
            if e.cat == "OBJECT" and (e.cat, e.incoming) not in chosen:
                # Appended only because a picked object uses it (parent, modifier target), but its
                # row says Skip: it goes, and its children keep their place in the world (below).
                skipped.append(n)
            else:
                kept.append(n)
            continue
        cur = current_of(e)
        if cur is None:
            kept.append(n)
        elif (e.cat, e.incoming) in chosen:
            cur.user_remap(n)
            job.touched = True
            replaced.append((cur, n, e))
        else:
            n.user_remap(cur)
            dropped.append(n)

    doomed = [cur for cur, _n, _e in replaced] + dropped
    gone = {i.as_pointer() for i in doomed}
    if doomed:
        bpy.data.batch_remove(doomed)

    # Names: incoming names for what came in, original names for everything else.
    job.restore_names(gone)

    # New and replaced objects sit in the collections they are in in the incoming file.
    scene = bpy.data.scenes.get((scene_name, None)) or bpy.context.scene

    def ensure_collection(name, seen=()):
        """Local collection `name`; a new one is created under its incoming parents, with its settings."""
        c = bpy.data.collections.get((name, None))
        if c is not None:
            return c
        info = S.incoming_colls.get(name) or {}
        c = bpy.data.collections.new(name)
        c.hide_render = info.get("hide_render", False)
        c.hide_viewport = info.get("hide_viewport", False)
        c.instance_offset = info.get("instance_offset", (0, 0, 0))
        for is_master, parent_name in info.get("parents") or [(True, "")]:
            parent = scene.collection if is_master or parent_name in seen else \
                ensure_collection(parent_name, seen + (name,))
            if c.name not in parent.children:
                parent.children.link(c)
        return c
    objects = [n for _c, n, e in replaced if e.cat == "OBJECT"] + \
              [n for n in kept if isinstance(n, bpy.types.Object)]
    for ob in objects:
        want = member.get(ob.name)
        if want is None:
            continue
        targets = []
        for is_master, cname in want or [(True, "")]:
            if is_master:
                targets.append(scene.collection)
                continue
            targets.append(ensure_collection(cname))
        if not targets:
            targets = [scene.collection]
        for c in targets:
            if ob.name not in c.objects:
                c.objects.link(ob)
        for c in list(ob.users_collection):
            if c not in targets and (c == scene.collection or c.library is None):
                c.objects.unlink(ob)

    if skipped:
        # Let Blender evaluate the scene with the skipped parents in it, so bone / vertex parents,
        # constraints and poses all count, then detach the children where they are and drop the rest.
        for ob in skipped:
            scene.collection.objects.link(ob)
        scene.view_layers[0].update()
        worlds = [(child, child.matrix_world.copy()) for ob in skipped for child in ob.children]
        for child, mw in worlds:
            child.parent = None
            child.matrix_world = mw
        gone |= {o.as_pointer() for o in skipped}
        bpy.data.batch_remove(skipped)

    removed = 0
    for e in remove:
        ob = local("OBJECT", e.current)
        if ob is None:
            continue
        for child in ob.children:
            mw = child.matrix_world.copy()
            child.parent = None
            child.matrix_world = mw
        bpy.data.objects.remove(ob)
        job.touched = True
        removed += 1

    for e in scene_entries:
        sc = local("SCENE", e.current)
        st = inc_scenes.get(e.incoming)
        if sc is None or st is None:
            continue
        sc.camera = local_for("OBJECT", st["camera"]) if st["camera"] else None
        sc.world = local_for("WORLD", st["world"]) if st["world"] else None
        preview.apply_scene_settings(sc, st)
        if e.status == diff.RENAMED:
            sc.name = e.incoming
        job.touched = True

    # Leftovers of the append nobody uses (shape keys of dropped meshes, ...).
    for _ in range(8):
        orphans = [n for n in new if alive(n) and n.as_pointer() not in gone and n.users == 0]
        if not orphans:
            break
        gone |= {o.as_pointer() for o in orphans}
        bpy.data.batch_remove(orphans)
    used = {i.library.as_pointer() for i in ses.all_ids() if i.library is not None}
    libs = [l for l in bpy.data.libraries if l.as_pointer() not in job.before_libs and l.as_pointer() not in used]
    if libs:
        bpy.data.batch_remove(libs)

    parts = []
    if replaced:
        parts.append(f"{len(replaced)} updated")
    added = sum(1 for n in kept if alive(n) and isinstance(n, bpy.types.Object))
    if added:
        parts.append(f"{added} objects added")
    if removed:
        parts.append(f"{removed} objects removed")
    if scene_entries:
        parts.append("scene settings")
    return "Merged: " + (", ".join(parts) or "no changes")
