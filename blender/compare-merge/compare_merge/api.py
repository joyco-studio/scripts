"""One function per action, shared by the sidebar, the command line and the tests."""

import bpy
import numpy as np

from . import apply, diff, merge as merge_mod, preview, session as ses, snapshot
from .preview import View
from .session import S


def start(path, scene_name=None):
    """Compare the open file with `path` and build the preview. Returns the entries."""
    scene_name = scene_name or bpy.context.scene.name
    if S.active:
        preview.teardown()
    _before_merge.clear()
    ses.compare(path, scene_name)
    View.choices = {}
    View.active = -1
    preview.rebuild()
    return S.entries


def keyed_choices():
    return {(S.entries[i].cat, S.entries[i].current, S.entries[i].incoming): c for i, c in View.choices.items()
            if i < len(S.entries)}


def refresh(after_undo=False):
    """Diff again after the current file changed; choices stay with their rows."""
    keep = keyed_choices()
    if after_undo and not keep and _before_merge:
        keep = dict(_before_merge)  # the merge was undone: its picks come back (kept until the next
        # merge or the end of the comparison, so a Refresh in between doesn't lose them)
    ses.ensure_linked()
    ses.refresh_current()
    View.choices = {}
    for i, e in enumerate(S.entries):
        c = keep.get((e.cat, e.current, e.incoming))
        if c is not None and ses.choosable(e):
            View.choices[i] = apply.normalize(i, c)  # unit picks: only the units the new diff still has
    View.active = min(View.active, len(S.entries) - 1)
    preview.rebuild()


def row_state(idx):
    """CURRENT | INCOMING | MIXED."""
    return ses.state(View.choices.get(idx))


def takes(idx, unit_key):
    """Does the row take this unit from the incoming file?"""
    c = View.choices.get(idx)
    if ses.state(c) == ses.INCOMING:
        return any(u.key == unit_key for u in apply.units(idx))
    return unit_key in ses.taken(c)


def set_unit(idx, unit_key, choice):
    """Take one unit of a changed row from the incoming file (INCOMING) or keep this file's (CURRENT).
    The row becomes Mixed; it reads as Current / Incoming again when nothing / everything is taken.
    Locked units only come with the whole row: they are left alone. No cascade."""
    us = apply.units(idx)
    unit = next((u for u in us if u.key == unit_key), None)
    if unit is None or unit.locked:
        return False
    c = View.choices.get(idx)
    keys = {u.key for u in us if not u.locked} if ses.state(c) == ses.INCOMING else set(ses.taken(c))
    (keys.add if choice == ses.INCOMING else keys.discard)(unit_key)
    View.choices[idx] = apply.normalize(idx, ses.mixed(keys))
    return True


def set_choice(idx, choice, cascade=True):
    """Set a row (replacing its unit picks); cascading also sets the changed data it is made of
    (mesh, materials, images...)."""
    touched = [idx]
    View.choices[idx] = choice
    e = S.entries[idx]
    if cascade:
        for d in ses.dependents(e):
            View.choices[d] = choice
            touched.append(d)
        if e.cat == "SCENE":  # the scene's camera when it is new: shown as picked, merged with the scene
            cam = (S.incoming_scenes.get(e.incoming) or {}).get("camera")
            dep = S.index_inc.get(("OBJECT", cam))
            if dep is not None and S.entries[dep].status == diff.ONLY_INCOMING:
                View.choices[dep] = choice
                touched.append(dep)
        if e.cat == "OBJECT" and e.status == diff.ONLY_INCOMING:
            for c in new_object_closure(e.incoming, adding=choice == "INCOMING"):
                View.choices[c] = choice
                touched.append(c)
    return touched


def new_object_closure(name, adding):
    """New (only incoming) objects that go with a new object.

    Skipping one skips its new descendants. Adding one also adds its new descendants and, for every
    object added that way, the new objects it needs (parent, modifier and constraint targets), which
    Blender would append anyway, so nothing an added object relies on is left on Skip."""
    out, todo, seen = [], [name], {name}
    while todo:
        cur = todo.pop()
        linked = incoming_children(cur)
        if adding:
            linked += incoming_needs(cur)
        for idx in linked:
            n = S.entries[idx].incoming
            if n not in seen:
                seen.add(n)
                out.append(idx)
                todo.append(n)
    return out


def incoming_needs(name, seen=None):
    """New (only incoming) objects an incoming object depends on, recursively."""
    seen = set() if seen is None else seen
    ob = ses.get_incoming("OBJECT", name)
    out = []
    if ob is None:
        return out
    deps = [ob.parent] + [getattr(item, p.identifier, None) for kind in (ob.modifiers, ob.constraints)
                          for item in kind for p in item.bl_rna.properties if p.type == "POINTER"]
    for dep in deps:
        if not isinstance(dep, bpy.types.Object) or dep.name in seen:
            continue
        seen.add(dep.name)
        idx = S.index_inc.get(("OBJECT", dep.name))
        if idx is not None and S.entries[idx].status == diff.ONLY_INCOMING:
            out += [idx] + incoming_needs(dep.name, seen)
    return out


def incoming_children(name):
    ob = ses.get_incoming("OBJECT", name)
    out = []
    for child in (ob.children_recursive if ob else ()):
        idx = S.index_inc.get(("OBJECT", child.name))
        if idx is not None and S.entries[idx].status == diff.ONLY_INCOMING:
            out.append(idx)
    return out


_before_merge = {}  # picks before the last merge, so undoing the merge brings them back


def merge():
    _before_merge.clear()
    _before_merge.update(keyed_choices())
    report = merge_mod.apply(dict(View.choices), S.scene)
    ses.link(S.path)
    View.choices = {}
    ses.refresh_current()
    preview.rebuild()
    return report


def recover_after_failed_merge():
    """merge.apply rolled back what it could; bring the comparison and the preview back."""
    _before_merge.clear()
    ses.ensure_linked()
    ses.refresh_current()
    preview.rebuild()


def close():
    _before_merge.clear()
    preview.teardown()
    ses.unlink()
    ses.reset()
    View.choices = {}
    View.active = -1


# ---------------------------------------------------------------------------------------------- details

def mesh_stats(e):
    """How the geometry of a changed mesh differs (computed on demand)."""
    a, b = ses.get_current("MESH", e.current), ses.get_incoming("MESH", e.incoming)
    if a is None or b is None:
        return None
    if len(a.vertices) != len(b.vertices) or len(a.polygons) != len(b.polygons):
        return (f"{len(a.vertices):,} → {len(b.vertices):,} vertices, "
                f"{len(a.polygons):,} → {len(b.polygons):,} faces")
    pa = snapshot.read(a.vertices, "co", len(a.vertices), 3).reshape(-1, 3)
    pb = snapshot.read(b.vertices, "co", len(b.vertices), 3).reshape(-1, 3)
    d = np.linalg.norm(pa - pb, axis=1) if len(pa) else np.zeros(0)
    moved = int((d > 1e-6).sum())
    if not moved:
        return "same vertex positions"
    return f"{moved:,} of {len(d):,} vertices moved (max {d.max():.4g}, mean {d[d > 1e-6].mean():.4g})"


def used_by(e):
    """Names of current/incoming objects that use this data-block (for non-object rows)."""
    out = set()
    for idb in (ses.get_current(e.cat, e.current), ses.get_incoming(e.cat, e.incoming)):
        if idb is None:
            continue
        for user in bpy.data.user_map(subset=[idb]).get(idb, ()):
            if isinstance(user, bpy.types.Object) and not ses.is_draft(user):
                out.add(user.name)
            elif isinstance(user, (bpy.types.Mesh, bpy.types.Material, bpy.types.Curve)):
                for u2 in bpy.data.user_map(subset=[user]).get(user, ()):
                    if isinstance(u2, bpy.types.Object) and not ses.is_draft(u2):
                        out.add(u2.name)
    return sorted(out)


def text_report(entries, show_same=False, limit=12):
    """Readable `git diff`-style summary of the comparison."""
    lines = []
    st = diff.stat(entries)
    lines.append(f"{st[diff.CHANGED]} changed, {st[diff.RENAMED]} renamed, {st[diff.ONLY_INCOMING]} only incoming, "
                 f"{st[diff.ONLY_CURRENT]} only current, {st[diff.SAME]} identical")
    for e in entries:
        if e.status == diff.SAME and not show_same:
            continue
        lines.append(f"{diff.STATUS_LETTER[e.status]} {snapshot.CAT_LABEL[e.cat]}: {e.name}")
        for field, a, b in e.changes[:limit]:
            lines.append(f"    {field}: {short(a)} → {short(b)}")
        if len(e.changes) > limit:
            lines.append(f"    … {len(e.changes) - limit} more")
    return "\n".join(lines)


def short(v, n=60):
    return v if len(v) <= n else v[: n - 1] + "…"
