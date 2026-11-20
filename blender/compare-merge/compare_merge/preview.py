"""The preview: a runtime-only scene that shows what the merge would produce.

The preview scene is a linked copy of the scene being compared (it shares its collections, so
untouched objects are the real ones and cost nothing). Every object whose chosen version differs
from what is in the file is hidden *in the preview's view layer only* and replaced by a preview
object: the incoming object itself (linked, read-only) or a runtime copy wired to the chosen
mesh, materials, action and parent. The file's own scene, objects and data are never edited.

It is derived, never patched: rebuild() throws every preview object away and builds them again
from the current choices, so the preview can't drift from the merge plan. A "plan" is a hashable
tuple describing one shown object; comparing plans tells which objects a choice affects, which is
how Ghost / Blink / Solo find what to show for the selected row.

A row with unit picks (Mixed) is shown as a runtime copy of the current data-block with the picked
units copied in from the incoming one, by the same apply.copy_unit Merge uses.
"""

import bpy

from . import apply, diff, snapshot
from . import session as ses
from .session import S

PREVIEW_COLL = "Compare Preview"
GHOST_COLL = "Compare Other Version"


class View:
    """Preview settings, mirrored from the sidebar so the timer and handlers can read them."""
    mode = "MERGE"  # MERGE (the choices) | CURRENT | INCOMING
    focus = "OFF"  # OFF | GHOST | BLINK
    solo = False
    active = -1  # selected row
    row_pick = None  # object the selected row made active (not a click of the user's)
    blink_on = False
    main_affected = []  # names of preview-scene objects showing the selected row's chosen version
    ghost_objects = []  # names of objects showing the other version
    choices = {}  # entry index -> "CURRENT" | "INCOMING" | ("MIXED", unit keys) (see session.state)


# ---------------------------------------------------------------------------------------------- planning

class Planner:
    def __init__(self, choice):
        self.choice = choice  # entry index -> a pick (session.state reads it)
        self.memo = {}

    def side(self, idx):
        """CURRENT | INCOMING | MIXED."""
        e = S.entries[idx]
        if e.status == diff.ONLY_INCOMING:
            return ses.INCOMING
        if e.status in (diff.ONLY_CURRENT, diff.SAME):
            return ses.CURRENT
        return ses.state(self.choice(idx))

    def version(self, idb):
        """The chosen version of a data-block (identical content counts as no change). A mixed row
        gives a plan ("MIX", ...) for a copy of the current one with the picked units; a mixed
        object gives the current object (obj() adds the units)."""
        if idb is None:
            return None
        idx = ses.entry_of(idb)
        if idx is None:
            return idb
        e = S.entries[idx]
        if e.status == diff.SAME:
            return idb
        side = self.side(idx)
        if side == ses.INCOMING:
            return ses.get_incoming(e.cat, e.incoming) or idb
        cur = ses.get_current(e.cat, e.current) or idb
        if side == ses.MIXED and e.cat != "OBJECT":
            inc = ses.get_incoming(e.cat, e.incoming)
            if inc is not None:
                keys = ses.taken(self.choice(idx))
                deps = tuple(self.version(r) for r in snapshot.references(cur) + snapshot.references(inc))
                return ("MIX", idx, cur, inc, keys, deps)
        return cur

    def mix(self, ob):
        """(incoming object, unit keys) when `ob` is the current side of a mixed object row."""
        idx = ses.entry_of(ob)
        if idx is None or not ses.is_current(ob) or self.side(idx) != ses.MIXED:
            return None
        inc = ses.get_incoming("OBJECT", S.entries[idx].incoming)
        return (inc, ses.taken(self.choice(idx))) if inc is not None else None

    @staticmethod
    def materials_of(data):
        """The data-block whose material list a (planned) data-block shows."""
        if isinstance(data, tuple) and data[0] == "MIX":
            _, _idx, cur, inc, keys, _deps = data
            return inc if "materials" in keys else cur
        return data

    def material(self, m):
        """Plan for a material: the chosen version, or a runtime copy whose image / node-group nodes
        point at the chosen versions of those (picked independently of the material)."""
        t = self.version(m)
        if isinstance(t, tuple):  # mixed: the builder points its nodes at the chosen images / groups
            return t
        swaps = []
        if t is not None and t.node_tree:
            for n in t.node_tree.nodes:
                for attr in ("image", "node_tree"):
                    ref = getattr(n, attr, None)
                    if isinstance(ref, bpy.types.ID):
                        v = self.version(ref)
                        if v != ref:
                            swaps.append((n.name, attr, v))
        return ("MA", t, tuple(swaps)) if swaps else t

    def skipped(self, ob):
        """A new object whose row says Skip (Merge won't bring it even if something uses it)."""
        idx = ses.entry_of(ob)
        return idx is not None and S.entries[idx].status == diff.ONLY_INCOMING and \
            ses.state(self.choice(idx)) != ses.INCOMING

    def obj(self, ob):
        """Plan for showing `ob` (already the chosen version): ob itself, or ("OB", ob, edits...)."""
        if ob in self.memo:
            return self.memo[ob]
        self.memo[ob] = ob  # cycle guard (objects that target each other in modifiers)
        mix = self.mix(ob)

        def own(key):
            """The object a reference is read from: the incoming one for a unit a mixed row takes."""
            return mix[0] if mix is not None and key in mix[1] else ob
        data = own("data").data
        new_data = self.version(data) if isinstance(data, bpy.types.ID) else None
        if new_data == ob.data:
            new_data = None
        src = self.materials_of(new_data or ob.data)
        mats = []
        n_slots = len(src.materials) if hasattr(src, "materials") else len(ob.material_slots)
        for i in range(n_slots):
            owner = own(f"slot:{i + 1}")
            slot = owner.material_slots[i] if i < len(owner.material_slots) else None
            if slot is not None and slot.link == "OBJECT":
                m = slot.material
            else:
                m = src.materials[i] if hasattr(src, "materials") and i < len(src.materials) else None
            if m is not None:
                p = self.material(m)
                if p != m:
                    mats.append((i, p))
        action = None
        ad = own("animation").animation_data
        if ad and ad.action:
            a = self.version(ad.action)
            action = a if a != (ob.animation_data.action if ob.animation_data else None) else None
        parent = None
        par = own("parent").parent
        if par:
            if self.skipped(par):
                # Its new parent stays out (Skip): keep pointing at it without showing it. Blender
                # still evaluates hidden parents (bones and poses too), so the child sits exactly
                # where Merge will leave it once it detaches it in place.
                parent = None
            else:
                pp = self.obj(self.version(par))
                parent = pp if pp != ob.parent else None
        targets = []  # (modifiers | constraints, item name, property, "OBJ" plan | "ID" data-block | "NONE")
        for kind, prefix in (("modifiers", "mod"), ("constraints", "con")):
            for item in getattr(ob, kind):
                item = getattr(own(f"{prefix}:{item.name}"), kind).get(item.name) or item
                for p in item.bl_rna.properties:
                    if p.type != "POINTER" or p.is_readonly:
                        continue
                    t = getattr(item, p.identifier, None)
                    if isinstance(t, bpy.types.Object) and t != ob:
                        if self.skipped(t):
                            targets.append((kind, item.name, p.identifier, "NONE", None))
                            continue
                        tp = self.obj(self.version(t))
                        if tp != t:
                            targets.append((kind, item.name, p.identifier, "OBJ", tp))
                    elif isinstance(t, bpy.types.NodeTree):  # Geometry Nodes modifiers
                        v = self.version(t)
                        if v != t:
                            targets.append((kind, item.name, p.identifier, "ID", v))
        if new_data is None and not mats and action is None and parent is None and not targets and mix is None:
            plan = ob
        else:
            plan = ("OB", ob, new_data, tuple(mats), action, parent, tuple(targets), mix)
        self.memo[ob] = plan
        return plan


def slots(planner, scene):
    """[(key, plan or None, current object or None)] for every object the preview can show."""
    out = []
    for ob in scene.objects:
        if ses.is_draft(ob) or not ses.is_current(ob):
            continue
        idx = ses.entry_of(ob)
        e = S.entries[idx] if idx is not None else None
        if e is not None and e.status == diff.ONLY_CURRENT and ses.state(planner.choice(idx)) == ses.INCOMING:
            out.append((("C", ob.name), None, ob))  # removal chosen
            continue
        out.append((("C", ob.name), planner.obj(planner.version(ob)), ob))
    for idx, e in enumerate(S.entries):
        if e.cat == "OBJECT" and e.status == diff.ONLY_INCOMING:
            inc = ses.get_incoming("OBJECT", e.incoming)
            plan = planner.obj(inc) if inc is not None and ses.state(planner.choice(idx)) == ses.INCOMING else None
            out.append((("I", e.incoming), plan, None))
    return out


# ---------------------------------------------------------------------------------------------- building

class Builder:
    def __init__(self, coll, planner, ghost=False, main=None):
        self.coll = coll
        self.planner = planner  # resolves what mixed units point at
        self.ghost = ghost
        self.main = main  # the main builder, for a ghost's unaffected parents
        self.made = {}
        self.mats = {}  # plan -> runtime copy (materials, mixed data-blocks)

    def link(self, ob):
        try:
            self.coll.objects.link(ob)
        except RuntimeError:  # already in the preview collection
            pass

    def material(self, plan):
        if not isinstance(plan, tuple):
            return plan
        if plan[0] == "MIX":
            return self.mixed(plan)
        if plan not in self.mats:
            _, m, swaps = plan
            d = ses.mark_draft(m.copy())
            d.name = f"{m.name} (preview)"
            for node_name, attr, value in swaps:
                node = d.node_tree.nodes.get(node_name)
                if node is not None:
                    setattr(node, attr, value)
            self.mats[plan] = d
        return self.mats[plan]

    def datablock(self, plan):
        """The data-block a plan from Planner.version stands for."""
        return self.material(plan) if isinstance(plan, tuple) else plan

    def mixed(self, plan):
        """A runtime copy of the current data-block with the picked units taken from the incoming one."""
        if plan not in self.mats:
            _, idx, cur, inc, keys, _deps = plan
            d = ses.mark_draft(cur.copy())
            d.name = f"{cur.name} (preview)"
            self.mats[plan] = d
            apply.copy_units(S.entries[idx].cat, [u for u in apply.units(idx) if u.key in keys], d, inc,
                             self.resolve)
            if getattr(d, "shape_keys", None) is not None:
                ses.mark_draft(d.shape_keys)
            nt = getattr(d, "node_tree", None)
            for n in nt.nodes if nt is not None else ():  # images / node groups picked on their own rows
                for attr in ("image", "node_tree"):
                    ref = getattr(n, attr, None)
                    if isinstance(ref, bpy.types.ID):
                        v = self.planner.version(ref)
                        if v != ref and isinstance(v, bpy.types.ID):
                            setattr(n, attr, v)
        return self.mats[plan]

    def resolve(self, idb):
        """What the preview shows for a data-block an incoming one points at (apply.copy_unit's
        resolve): the chosen version, as Merge would leave it."""
        if not isinstance(idb, bpy.types.ID):
            return idb
        idx = ses.entry_of(idb)
        if idx is not None and S.entries[idx].status == diff.SAME and S.entries[idx].current:
            idb = ses.get_current(S.entries[idx].cat, S.entries[idx].current) or idb
        p = self.planner
        if isinstance(idb, bpy.types.Object):
            return idb if p.skipped(idb) else self.nested(p.obj(p.version(idb)))
        if isinstance(idb, bpy.types.Material):
            return self.material(p.material(idb))
        return self.datablock(p.version(idb))

    def nested(self, plan):
        """A parent / modifier target: reuse the main preview's object when the ghost can."""
        if self.main is not None:
            if plan in self.main.made:
                return self.main.made[plan]
            if not isinstance(plan, tuple):
                return plan
        return self.make(plan)

    def make(self, plan):
        if plan in self.made:
            return self.made[plan]
        if not isinstance(plan, tuple):
            if not self.ghost:
                if plan.library is not None:
                    self.link(plan)
                self.made[plan] = plan
                return plan
            ob, data, mats, action, parent, targets, mix = plan, None, (), None, None, (), None
        else:
            _, ob, data, mats, action, parent, targets, mix = plan
        d = ses.mark_draft(ob.copy())
        d.name = f"{ob.name} ({'other' if self.ghost else 'preview'})"
        d["cm_src"] = ob.name
        self.made[plan] = d
        self.coll.objects.link(d)
        if mix is not None:  # the picked object units; references are wired below, the same way
            idx = ses.entry_of(ob)
            apply.copy_units("OBJECT", [u for u in apply.units(idx) if u.key in mix[1]], d, mix[0], self.resolve)
        if data is not None:
            d.data = self.datablock(data)
        for i, mp in mats:
            if i < len(d.material_slots):
                d.material_slots[i].link = "OBJECT"
                d.material_slots[i].material = self.material(mp)
        if action is not None:
            if d.animation_data is None:
                d.animation_data_create()
            d.animation_data.action = action
        if parent is not None:
            reparent(d, self.nested(parent))
        elif self.ghost and d.parent is not None and self.main is not None:  # d.parent: a picked parent unit counts
            reparent(d, self.main.made.get(d.parent, d.parent))
        for kind, item_name, prop, mode, target in targets:
            item = getattr(d, kind).get(item_name)
            if item is not None:
                setattr(item, prop, self.nested(target) if mode == "OBJ" else target)
        for axis in ("lock_location", "lock_rotation", "lock_scale"):
            setattr(d, axis, (True, True, True))
        if self.ghost:
            d.display_type = "WIRE" if View.focus == "GHOST" else d.display_type
            d.show_in_front = View.focus == "GHOST"
            d.hide_select = True
        return d


def reparent(ob, parent):
    """Point `ob` at another version of its parent, keeping it where it sits under that parent.
    Assigning Object.parent resets the parent inverse matrix, which moves every child that was
    parented with an offset (Ctrl+P), so it is put back."""
    mpi = ob.matrix_parent_inverse.copy()
    ob.parent = parent
    ob.matrix_parent_inverse = mpi


# ---------------------------------------------------------------------------------------------- scene

def preview_scene():
    """Found by its tag, not its name: a scene of the user's may already be called that."""
    return next((s for s in bpy.data.scenes if s.library is None and s.is_runtime_data and ses.is_draft(s)), None)


def preview_coll(sc, role):
    """The preview's own collections, by tag. The preview scene also shows the real scene's collections,
    so a user collection with the same name must never be picked up (purge empties these)."""
    if sc is None:
        return None
    return next((c for c in sc.collection.children if c.is_runtime_data and c.get(ses.DRAFT_TAG) == role), None)


def original_scene():
    """The real scene being compared. Merging a scene rename changes its name and undo changes it
    back, so every name it has had is tried; the one that exists becomes current again."""
    for name in [S.scene] + sorted(S.scene_names - {S.scene}):
        sc = bpy.data.scenes.get((name, None)) if name else None
        if sc is not None and not sc.is_runtime_data:
            S.scene = name
            return sc
    return None


def ensure_scene():
    orig = original_scene()
    if orig is None:
        raise RuntimeError(f"Scene '{S.scene}' no longer exists")
    sc = preview_scene()
    if sc is None:
        sc = ses.mark_draft(orig.copy())
        sc.name = ses.PREVIEW_SCENE
    for name, role in ((PREVIEW_COLL, "preview"), (GHOST_COLL, "ghost")):
        if preview_coll(sc, role) is None:
            c = ses.mark_draft(bpy.data.collections.new(name))
            c[ses.DRAFT_TAG] = role
            sc.collection.children.link(c)
    return sc


DRAFT_TYPES = ("objects", "materials", "meshes", "lights", "cameras", "worlds", "curves", "shape_keys")


def purge():
    """Remove every preview object and runtime copy of data; empty the preview collections."""
    sc = preview_scene()
    for role in ("preview", "ghost"):
        c = preview_coll(sc, role)
        for ob in list(c.objects) if c is not None else ():
            c.objects.unlink(ob)
    doomed = [i for attr in DRAFT_TYPES for i in getattr(bpy.data, attr)
              if i.is_runtime_data and ses.is_draft(i)]
    if doomed:
        bpy.data.batch_remove(doomed)


def choice_fn(rows_choice, override=None):
    def choice(idx):
        if override is not None and idx in override:
            return override[idx]
        if View.mode == "CURRENT":
            return ses.CURRENT
        if View.mode == "INCOMING":
            return ses.INCOMING
        return rows_choice.get(idx, ses.CURRENT)
    return choice


def rebuild():
    """Derive the whole preview scene from the comparison and the current choices."""
    if not S.active:
        return
    ses.ensure_linked()
    purge()
    orig = original_scene()
    if orig is None:
        return
    sc = ensure_scene()
    vl = sc.view_layers[0]
    orig_vl = orig.view_layers[0]
    planner = Planner(choice_fn(View.choices))
    main = Builder(preview_coll(sc, "preview"), planner)
    main_slots = slots(planner, orig)
    shown = {}
    for key, plan, cur in main_slots:
        shown[key] = main.make(plan) if plan is not None else None

    # The selected row's other version, for Ghost / Blink / Solo.
    affected = []
    ghost_objs = []
    idx = View.active
    if 0 <= idx < len(S.entries) and ses.choosable(S.entries[idx]) and (View.focus != "OFF" or View.solo):
        flipped = ses.other_side(choice_fn(View.choices)(idx))
        alt_planner = Planner(choice_fn(View.choices, {idx: flipped}))
        alt = {key: plan for key, plan, _ in slots(alt_planner, orig)}
        ghost = Builder(preview_coll(sc, "ghost"), alt_planner, ghost=True, main=main)
        for key, plan, cur in main_slots:
            if alt.get(key) != plan:
                affected.append(key)
                if alt.get(key) is not None and View.focus != "OFF":
                    ghost_objs.append(ghost.make(alt[key]))
        for key, plan in alt.items():
            if key not in shown and plan is not None:
                affected.append(key)
                if View.focus != "OFF":
                    ghost_objs.append(ghost.make(plan))

    def hidden_in_file(ob):
        try:
            return ob.hide_get(view_layer=orig_vl)
        except RuntimeError:
            return False

    def set_hidden(ob, state):
        try:
            ob.hide_set(state, view_layer=vl)
        except RuntimeError:
            pass

    affected_set = set(affected)
    for key, plan, cur in main_slots:
        obj = shown.get(key)
        base_hidden = hidden_in_file(cur) if cur is not None else False
        if cur is not None:
            set_hidden(cur, base_hidden or obj is not cur)
        if obj is not None and obj is not cur:
            set_hidden(obj, base_hidden)
        if View.solo and key not in affected_set:
            for o in (cur, obj):
                if o is not None:
                    set_hidden(o, True)

    View.main_affected = [shown[k].name_full for k in affected if shown.get(k) is not None]
    View.ghost_objects = [o.name_full for o in ghost_objs]
    View.blink_on = False
    if View.focus == "BLINK":
        for o in ghost_objs:
            set_hidden(o, True)

    # Scene settings: the chosen scene's camera, world and frame range.
    st = ses.scene_settings(orig)
    cam, world = orig.camera, orig.world
    sidx = S.index_cur.get(("SCENE", orig.name))
    if sidx is not None and planner.side(sidx) == ses.INCOMING and S.entries[sidx].status != diff.SAME:
        inc = S.incoming_scenes.get(S.entries[sidx].incoming)
        if inc:
            st = inc
            cam = ses.get_incoming("OBJECT", inc["camera"])
            world = ses.get_incoming("WORLD", inc["world"])
    sc.camera = main.make(planner.obj(planner.version(cam))) if cam is not None else None
    if View.solo and sc.camera is not None and sc.camera.name_full not in View.main_affected:
        set_hidden(sc.camera, True)  # the incoming scene's camera, added after Solo hid the rest
    sc.world = main.datablock(planner.version(world))
    apply_scene_settings(sc, st)


def apply_scene_settings(sc, st):
    """Frame range, fps, resolution and units (camera and world are resolved by the caller)."""
    r = sc.render
    sc.frame_start, sc.frame_end = st["frame_start"], st["frame_end"]
    r.fps, r.fps_base = st["fps"], st["fps_base"]
    r.resolution_x, r.resolution_y = st["resolution_x"], st["resolution_y"]
    r.resolution_percentage = st["resolution_percentage"]
    sc.unit_settings.scale_length = st["scale_length"]


def blink_tick():
    """Swap the selected row's two versions (called by the timer)."""
    sc = preview_scene()
    if sc is None or View.focus != "BLINK":
        return
    View.blink_on = not View.blink_on
    vl = sc.view_layers[0]
    main, other = set(View.main_affected), set(View.ghost_objects)
    for ob in sc.objects:
        name = ob.name_full
        if name in main or name in other:
            try:
                ob.hide_set(View.blink_on if name in main else not View.blink_on, view_layer=vl)
            except RuntimeError:
                pass


def teardown():
    """Remove the preview scene and everything in it; windows go back to the real scene."""
    show_original()
    purge()
    sc = preview_scene()
    colls = [c for c in bpy.data.collections if c.is_runtime_data and ses.is_draft(c)]
    if colls:
        bpy.data.batch_remove(colls)
    if sc is not None:
        bpy.data.scenes.remove(sc)


# ---------------------------------------------------------------------------------------------- windows

def show_preview(window):
    sc = preview_scene()
    if sc is not None and window.scene != sc:
        window.scene = sc


def show_original(window=None):
    """Point windows showing the preview back at the real scene."""
    orig = original_scene() or (bpy.data.scenes[0] if bpy.data.scenes else None)
    sc = preview_scene()
    wm = bpy.context.window_manager
    if wm is None or orig is None:
        return []
    moved = []
    for win in ([window] if window else wm.windows):
        if sc is not None and win.scene == sc:
            win.scene = orig
            moved.append(win)
    return moved
