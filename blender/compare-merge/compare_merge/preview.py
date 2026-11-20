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
"""

import bpy

from . import diff
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
    blink_on = False
    main_affected = []  # names of preview-scene objects showing the selected row's chosen version
    ghost_objects = []  # names of objects showing the other version
    choices = {}  # entry index -> "CURRENT" | "INCOMING"


# ---------------------------------------------------------------------------------------------- planning

class Planner:
    def __init__(self, choice):
        self.choice = choice  # entry index -> "CURRENT" | "INCOMING"
        self.memo = {}

    def side(self, idx):
        e = S.entries[idx]
        if e.status == diff.ONLY_INCOMING:
            return "INCOMING"
        if e.status in (diff.ONLY_CURRENT, diff.SAME):
            return "CURRENT"
        return self.choice(idx)

    def version(self, idb):
        """The chosen version of a data-block (identical content counts as no change)."""
        if idb is None:
            return None
        idx = ses.entry_of(idb)
        if idx is None:
            return idb
        e = S.entries[idx]
        if e.status == diff.SAME:
            return idb
        if self.side(idx) == "INCOMING":
            return ses.get_incoming(e.cat, e.incoming) or idb
        return ses.get_current(e.cat, e.current) or idb

    def material(self, m):
        t = self.version(m)
        swaps = []
        if t is not None and t.node_tree:
            for n in t.node_tree.nodes:
                img = getattr(n, "image", None)
                if img is not None:
                    v = self.version(img)
                    if v != img:
                        swaps.append((n.name, v))
        return ("MA", t, tuple(swaps)) if swaps else t

    def obj(self, ob):
        """Plan for showing `ob` (already the chosen version): ob itself, or ("OB", ob, edits...)."""
        if ob in self.memo:
            return self.memo[ob]
        self.memo[ob] = ob  # cycle guard (objects that target each other in modifiers)
        data = ob.data
        new_data = self.version(data) if isinstance(data, bpy.types.ID) else None
        if new_data == data:
            new_data = None
        src = new_data or data
        mats = []
        n_slots = len(src.materials) if hasattr(src, "materials") else len(ob.material_slots)
        for i in range(n_slots):
            slot = ob.material_slots[i] if i < len(ob.material_slots) else None
            if slot is not None and slot.link == "OBJECT":
                m = slot.material
            else:
                m = src.materials[i] if hasattr(src, "materials") and i < len(src.materials) else None
            if m is not None:
                p = self.material(m)
                if p != m:
                    mats.append((i, p))
        action = None
        if ob.animation_data and ob.animation_data.action:
            a = self.version(ob.animation_data.action)
            action = a if a != ob.animation_data.action else None
        parent = None
        if ob.parent:
            pp = self.obj(self.version(ob.parent))
            parent = pp if pp != ob.parent else None
        targets = []
        for kind in ("modifiers", "constraints"):
            for item in getattr(ob, kind):
                for p in item.bl_rna.properties:
                    if p.type != "POINTER" or p.is_readonly:
                        continue
                    t = getattr(item, p.identifier, None)
                    if isinstance(t, bpy.types.Object) and t != ob:
                        tp = self.obj(self.version(t))
                        if tp != t:
                            targets.append((kind, item.name, p.identifier, tp))
        if new_data is None and not mats and action is None and parent is None and not targets:
            plan = ob
        else:
            plan = ("OB", ob, new_data, tuple(mats), action, parent, tuple(targets))
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
        if e is not None and e.status == diff.ONLY_CURRENT and planner.choice(idx) == "INCOMING":
            out.append((("C", ob.name), None, ob))  # removal chosen
            continue
        out.append((("C", ob.name), planner.obj(planner.version(ob)), ob))
    for idx, e in enumerate(S.entries):
        if e.cat == "OBJECT" and e.status == diff.ONLY_INCOMING:
            inc = ses.get_incoming("OBJECT", e.incoming)
            plan = planner.obj(inc) if inc is not None and planner.choice(idx) == "INCOMING" else None
            out.append((("I", e.incoming), plan, None))
    return out


# ---------------------------------------------------------------------------------------------- building

class Builder:
    def __init__(self, coll, ghost=False, main=None):
        self.coll = coll
        self.ghost = ghost
        self.main = main  # the main builder, for a ghost's unaffected parents
        self.made = {}
        self.mats = {}

    def link(self, ob):
        try:
            self.coll.objects.link(ob)
        except RuntimeError:  # already in the preview collection
            pass

    def material(self, plan):
        if not isinstance(plan, tuple):
            return plan
        if plan not in self.mats:
            _, m, swaps = plan
            d = ses.mark_draft(m.copy())
            d.name = f"{m.name} (preview)"
            for node_name, img in swaps:
                node = d.node_tree.nodes.get(node_name)
                if node is not None:
                    node.image = img
            self.mats[plan] = d
        return self.mats[plan]

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
            ob, data, mats, action, parent, targets = plan, None, (), None, None, ()
        else:
            _, ob, data, mats, action, parent, targets = plan
        d = ses.mark_draft(ob.copy())
        d.name = f"{ob.name} ({'other' if self.ghost else 'preview'})"
        d["cm_src"] = ob.name
        self.made[plan] = d
        self.coll.objects.link(d)
        if data is not None:
            d.data = data
        for i, mp in mats:
            if i < len(d.material_slots):
                d.material_slots[i].link = "OBJECT"
                d.material_slots[i].material = self.material(mp)
        if action is not None:
            if d.animation_data is None:
                d.animation_data_create()
            d.animation_data.action = action
        if parent is not None:
            d.parent = self.nested(parent)
        elif self.ghost and ob.parent is not None and self.main is not None:
            d.parent = self.main.made.get(ob.parent, ob.parent)
        for kind, item_name, prop, target in targets:
            item = getattr(d, kind).get(item_name)
            if item is not None:
                setattr(item, prop, self.nested(target))
        for axis in ("lock_location", "lock_rotation", "lock_scale"):
            setattr(d, axis, (True, True, True))
        if self.ghost:
            d.display_type = "WIRE" if View.focus == "GHOST" else d.display_type
            d.show_in_front = View.focus == "GHOST"
            d.hide_select = True
        return d


# ---------------------------------------------------------------------------------------------- scene

def preview_scene():
    sc = bpy.data.scenes.get(ses.PREVIEW_SCENE)
    return sc if sc is not None and sc.is_runtime_data else None


def original_scene():
    return bpy.data.scenes.get((S.scene, None)) if S.scene else None


def ensure_scene():
    orig = original_scene()
    if orig is None:
        raise RuntimeError(f"Scene '{S.scene}' no longer exists")
    sc = preview_scene()
    if sc is None:
        sc = ses.mark_draft(orig.copy())
        sc.name = ses.PREVIEW_SCENE
    for name in (PREVIEW_COLL, GHOST_COLL):
        c = sc.collection.children.get(name)
        if c is None:
            c = ses.mark_draft(bpy.data.collections.new(name))
            sc.collection.children.link(c)
    return sc


def purge():
    """Remove every preview object and material; empty the preview collections."""
    sc = preview_scene()
    for name in (PREVIEW_COLL, GHOST_COLL):
        c = sc.collection.children.get(name) if sc is not None else None
        for ob in list(c.objects) if c is not None else ():
            c.objects.unlink(ob)
    doomed = [i for coll in (bpy.data.objects, bpy.data.materials) for i in coll
              if i.is_runtime_data and ses.is_draft(i)]
    if doomed:
        bpy.data.batch_remove(doomed)


def choice_fn(rows_choice, override=None):
    def choice(idx):
        if override is not None and idx in override:
            return override[idx]
        if View.mode == "CURRENT":
            return "CURRENT"
        if View.mode == "INCOMING":
            return "INCOMING"
        return rows_choice.get(idx, "CURRENT")
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
    main = Builder(sc.collection.children[PREVIEW_COLL])
    planner = Planner(choice_fn(View.choices))
    main_slots = slots(planner, orig)
    shown = {}
    for key, plan, cur in main_slots:
        shown[key] = main.make(plan) if plan is not None else None

    # The selected row's other version, for Ghost / Blink / Solo.
    affected = []
    ghost_objs = []
    idx = View.active
    if 0 <= idx < len(S.entries) and ses.choosable(S.entries[idx]) and (View.focus != "OFF" or View.solo):
        flipped = "CURRENT" if choice_fn(View.choices)(idx) == "INCOMING" else "INCOMING"
        alt_planner = Planner(choice_fn(View.choices, {idx: flipped}))
        alt = {key: plan for key, plan, _ in slots(alt_planner, orig)}
        ghost = Builder(sc.collection.children[GHOST_COLL], ghost=True, main=main)
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
    r = orig.render
    st = dict(camera=orig.camera, world=orig.world, frame_start=orig.frame_start, frame_end=orig.frame_end,
              fps=r.fps, fps_base=r.fps_base)
    sidx = S.index_cur.get(("SCENE", orig.name))
    if sidx is not None and planner.side(sidx) == "INCOMING" and S.entries[sidx].status != diff.SAME:
        inc = S.incoming_scenes.get(S.entries[sidx].incoming)
        if inc:
            st = dict(inc, camera=ses.get_incoming("OBJECT", inc["camera"]),
                      world=ses.get_incoming("WORLD", inc["world"]))
    cam = st["camera"]
    sc.camera = main.make(planner.obj(planner.version(cam))) if cam is not None else None
    sc.world = planner.version(st["world"])
    sc.frame_start, sc.frame_end = st["frame_start"], st["frame_end"]
    sc.render.fps, sc.render.fps_base = st["fps"], st["fps_base"]


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
