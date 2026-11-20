"""Field-level picks: the parts of a changed data-block that can be taken from the incoming file on
their own (units), and one copy function per kind of unit.

The preview and Merge call the same copy_unit, so the preview never shows something Merge won't do:
  preview  dst = a runtime copy of the current data-block, src = the linked incoming one,
           resolve = the version the preview shows of whatever src points at
  merge    dst = the real current data-block, src = the appended incoming copy, resolve = identity
           (by then every reference of the appended copy points at what stays in the file)
"""

import re
from contextlib import contextmanager
from dataclasses import dataclass

import bpy

from . import diff, snapshot
from . import session as ses
from .session import S


@dataclass(frozen=True)
class Unit:
    key: str  # stable name ("uv:UVMap", "transform", "node:Principled BSDF › Roughness")
    label: str
    fields: tuple  # ((field, current value, incoming value), ...) it covers
    locked: str = ""  # why it only comes with the whole row ("" = it can be picked on its own)


LOCK_TOPOLOGY = "Topology differs: UVs and positions only fit their own geometry. Take the whole mesh"
LOCK_TYPE = "Object type differs: take the whole object"
LOCK_WIRING = "Node wiring changed (links, sockets, added or removed nodes): take the whole row"
LOCK_WHOLE = "Only comes with the whole row"
LOCK_STACK = "{} changed (added, removed, reordered or retyped): take the whole object"
ITEMS = re.compile(r"^\d+ items #")


# ---------------------------------------------------------------------------------------------- units

def units(idx):
    """[Unit] of a changed row, in the order its fields are listed (cached until the next diff)."""
    if idx not in S.units:
        e = S.entries[idx]
        S.units[idx] = derive(e) if e.status == diff.CHANGED and e.cat in snapshot.UNIT_CATS else []
    return S.units[idx]


def derive(e):
    cur = ses.get_current(e.cat, e.current)
    nodes = set()
    if e.cat in ("MATERIAL", "WORLD"):
        nodes = snapshot.node_names(S.current_snap.get((e.cat, e.current), {})) | \
            snapshot.node_names(S.incoming_snap.get((e.cat, e.incoming), {}))
    ref_keys, uv_names = set(), set()
    if e.cat == "MESH":
        for me in (cur, ses.get_incoming(e.cat, e.incoming)):
            if me is not None and me.shape_keys:  # the Basis is the rest positions: it goes with them
                ref_keys.add(f"key:{me.shape_keys.reference_key.name}")
        uv_names = {f[len("uv map "):] for f, _a, _b in e.changes if f.startswith("uv map ")}
    groups = {}
    for f, a, b in e.changes:
        key, label = snapshot.unit_key(e.cat, f, nodes)
        if key in ref_keys:
            key, label = "positions", snapshot.UNIT_LABEL["positions"]
        elif key.startswith("attr:") and key[5:] in uv_names:  # one layer, a UV map on one side only
            key, label = f"uv:{key[5:]}", f"UV map {key[5:]}"
        groups.setdefault(key, [label, []])[1].append((f, a, b))
    keys = set(groups)
    return [Unit(k, label, tuple(fields), lock(e.cat, k, fields, keys, cur)) for k, (label, fields) in groups.items()]


def lock(cat, key, fields, keys, cur):
    """Why a unit can't be taken on its own, or ""."""
    if key.startswith("field:"):
        return LOCK_WHOLE
    if cat == "OBJECT":
        if "type" in keys:
            return LOCK_TYPE
        for stack, prefix, label in (("modifiers", "mod:", "Modifier stack"), ("constraints", "con:", "Constraint stack")):
            if key == stack or (key.startswith(prefix) and stack in keys):
                return LOCK_STACK.format(label)
        if key == "drivers":
            return "Drivers only come with the whole object"
        if key == "vertex groups":
            return "Vertex groups only come with the whole object (their weights are on the mesh)"
        if key.startswith("slot:"):
            _f, a, b = fields[0]
            if "—" in (a, b):
                return "The number of slots follows the data's materials: take the whole object"
            if a.endswith("(data)") and b.endswith("(data)"):
                return "This slot shows the mesh's material: pick it on the mesh row"
    elif cat == "MESH":
        if "topology" in keys:
            return LOCK_TOPOLOGY
        if key == "weights":
            return "Vertex weights only come with the whole mesh"
        if key.startswith(("uv:", "attr:")) and any(v.endswith(" ?") for _f, a, b in fields for v in (a, b)):
            return "This attribute type can't be copied on its own: take the whole mesh"
    if key == "wiring" or (key.startswith("node:") and "wiring" in keys):
        return LOCK_WIRING
    if key.startswith(("node:", "rna:")):
        if any(ITEMS.match(v) for _f, a, b in fields for v in (a, b)):
            return "A list of values: take the whole row"
        if key.startswith("rna:") and cur is not None and not settable(cur, key[4:]):
            return "Read-only here: take the whole row"
        if key.startswith("node:") and cur is not None:
            node, sep, rest = split_node_field(key[5:])
            nt = getattr(cur, "node_tree", None)
            n = nt.nodes.get(node) if nt and node else None
            if sep == " · " and rest != "muted" and (n is None or not settable(n, rest)):
                return "Read-only here: take the whole row"
    return ""


def normalize(idx, choice):
    """A mixed pick with nothing taken is Current; one taking every unit (none locked) is Incoming."""
    if ses.state(choice) != ses.MIXED:
        return choice
    us = units(idx)
    pickable = {u.key for u in us if not u.locked}
    keys = ses.taken(choice) & pickable
    if not keys:
        return ses.CURRENT
    if keys == pickable and len(pickable) == len(us):
        return ses.INCOMING
    return ses.mixed(keys)


def taken_units(idx, choice):
    keys = ses.taken(choice)
    return [u for u in units(idx) if u.key in keys]


# ---------------------------------------------------------------------------------------------- RNA paths

def settable(struct, path):
    *parents, last = path.split(".")
    for p in parents:
        struct = getattr(struct, p, None)
        if struct is None:
            return False
    prop = struct.bl_rna.properties.get(last) if hasattr(struct, "bl_rna") else None
    return prop is not None and not prop.is_readonly


def get_path(struct, path):
    for p in path.split("."):
        struct = getattr(struct, p)
    return struct


def set_path(struct, path, value, resolve):
    *parents, last = path.split(".")
    for p in parents:
        struct = getattr(struct, p)
    setattr(struct, last, resolve(value) if isinstance(value, bpy.types.ID) else value)


def split_node_field(field):
    """"<node> › <socket>" or "<node> · <property>" -> (node, separator, rest)."""
    for sep in (" › ", " · "):
        if sep in field:
            node, rest = field.split(sep, 1)
            return node, sep, rest
    return "", "", field


def copy_struct(dst, src, resolve, depth=0):
    """Every setting snapshot.rna_fields compares (modifier / constraint settings). Twice, so a value
    whose valid range depends on another setting (an enum, a mode) lands after it."""
    for _ in range(2):
        for p in src.bl_rna.properties:
            ident = p.identifier
            if ident in snapshot.SKIP or ident.startswith(snapshot.SKIP_PREFIX) or p.type == "COLLECTION":
                continue
            try:
                v = getattr(src, ident)
            except AttributeError:
                continue
            if p.type == "POINTER":
                if p.is_readonly:
                    if v is not None and not isinstance(v, bpy.types.ID) and depth < 2:
                        copy_struct(getattr(dst, ident), v, resolve, depth + 1)
                    continue
                if v is not None and not isinstance(v, bpy.types.ID):
                    continue
                v = resolve(v)
            elif p.is_readonly:
                continue
            try:
                setattr(dst, ident, v)
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass  # not settable in this state (the dst's other settings): left as it is


def id_value(v, resolve):
    if isinstance(v, bpy.types.ID):
        return resolve(v)
    if hasattr(v, "to_dict"):
        return v.to_dict()
    if hasattr(v, "to_list"):
        return v.to_list()
    return v


def copy_custom(dst, src, key, resolve):
    if key in src.keys():
        dst[key] = id_value(src[key], resolve)
    elif key in dst.keys():
        del dst[key]


# ---------------------------------------------------------------------------------------------- objects

TRANSFORM = ("rotation_mode", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "location", "scale",
             "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale")


def copy_attrs(dst, src, attrs):
    for a in attrs:  # rotation_mode first: it decides which rotation Blender uses
        setattr(dst, a, getattr(src, a))


def copy_parent(dst, src, resolve):
    dst.parent = resolve(src.parent)
    if src.parent is not None:
        dst.parent_type = src.parent_type
        if src.parent_type == "BONE":
            dst.parent_bone = src.parent_bone
        elif src.parent_type in ("VERTEX", "VERTEX_3"):
            dst.parent_vertices = src.parent_vertices[:]
    # Assigning Object.parent resets the parent inverse (it holds the offset of a Ctrl+P): set it last.
    dst.matrix_parent_inverse = src.matrix_parent_inverse.copy()


def copy_slot(dst, src, n, resolve):
    i = int(n) - 1
    if i < len(dst.material_slots) and i < len(src.material_slots):
        s, d = src.material_slots[i], dst.material_slots[i]
        d.link = s.link
        if s.link == "OBJECT":
            d.material = resolve(s.material)


def copy_stack_item(dst, src, kind, name, resolve):
    s, d = getattr(src, kind).get(name), getattr(dst, kind).get(name)
    if s is None or d is None:
        return
    copy_struct(d, s, resolve)
    for k in s.keys():  # Geometry Nodes inputs are ID properties of the modifier
        try:
            d[k] = id_value(s[k], resolve)
        except (TypeError, KeyError):
            pass


def copy_animation(dst, src, resolve):
    sad = src.animation_data
    act = resolve(sad.action) if sad and sad.action else None
    if dst.animation_data is None:
        if act is None:
            return
        dst.animation_data_create()
    ad = dst.animation_data
    ad.action = act
    slot = getattr(sad, "action_slot", None) if sad else None
    if act is not None and slot is not None and hasattr(act, "slots"):
        match = next((s for s in act.slots if s.identifier == slot.identifier), None)
        if match is not None:
            ad.action_slot = match


def copy_pose(dst, src):
    if dst.pose is None or src.pose is None:
        return
    for sb in src.pose.bones:
        db = dst.pose.bones.get(sb.name)
        if db is not None:
            copy_attrs(db, sb, ("rotation_mode", "location", "rotation_quaternion", "rotation_euler",
                                "rotation_axis_angle", "scale"))


def copy_instancing(dst, src, resolve):
    dst.instance_type = src.instance_type
    dst.instance_collection = resolve(src.instance_collection)


OBJECT_COPY = {
    "transform": lambda d, s, _n, _r: copy_attrs(d, s, TRANSFORM),
    "visibility": lambda d, s, _n, _r: copy_attrs(d, s, ("hide_render", "hide_viewport")),
    "parent": lambda d, s, _n, r: copy_parent(d, s, r),
    "collections": lambda d, s, _n, _r: None,  # Merge links the object where it is in the incoming file
    "slot": copy_slot,
    "mod": lambda d, s, n, r: copy_stack_item(d, s, "modifiers", n, r),
    "con": lambda d, s, n, r: copy_stack_item(d, s, "constraints", n, r),
    "animation": lambda d, s, _n, r: copy_animation(d, s, r),
    "pose": lambda d, s, _n, _r: copy_pose(d, s),
    "instancing": lambda d, s, _n, r: copy_instancing(d, s, r),
    "data": lambda d, s, _n, r: setattr(d, "data", r(s.data)),
}


# ---------------------------------------------------------------------------------------------- meshes

def copy_positions(dst, src):
    n = len(dst.vertices)
    co = snapshot.read(src.vertices, "co", n, 3)
    dst.vertices.foreach_set("co", co)
    if dst.shape_keys:
        # Edit mode rebuilds the mesh from the Basis key: without this the old positions come back.
        basis = src.shape_keys.reference_key if src.shape_keys else None
        dst.shape_keys.reference_key.data.foreach_set("co", snapshot.read(basis.data, "co", n, 3) if basis else co)
    dst.update()


def copy_attribute(dst, src, name):
    sa, da = src.attributes.get(name), dst.attributes.get(name)
    if da is not None and (sa is None or da.domain != sa.domain or da.data_type != sa.data_type):
        dst.attributes.remove(da)
        da = None
    if sa is None:
        return
    if da is None:
        da = dst.attributes.new(name, sa.data_type, sa.domain)
    arr = snapshot.attribute_array(sa)
    if arr is not None:
        da.data.foreach_set(snapshot.ATTR_KEY.get(sa.data_type, "value"), arr)
    dst.update()


@contextmanager
def holder(me):
    """Shape keys are added and removed through an object: a temporary one, never in a scene."""
    ob = bpy.data.objects.new("cm~shape keys", me)
    try:
        yield ob
    finally:
        bpy.data.objects.remove(ob)


def copy_shape_key(dst, src, name):
    skb = src.shape_keys.key_blocks.get(name) if src.shape_keys else None
    dkb = dst.shape_keys.key_blocks.get(name) if dst.shape_keys else None
    if skb is None:
        if dkb is not None:
            with holder(dst) as ob:
                ob.shape_key_remove(dkb)
        return
    if dkb is None:
        with holder(dst) as ob:
            if dst.shape_keys is None:  # the first key becomes the Basis: add one from this mesh first
                ob.shape_key_add(name=src.shape_keys.reference_key.name, from_mix=False)
            dkb = ob.shape_key_add(name=name, from_mix=False)
    keys = dst.shape_keys.key_blocks
    dkb.data.foreach_set("co", snapshot.read(skb.data, "co", len(dst.vertices), 3))
    dkb.slider_min, dkb.slider_max = skb.slider_min, skb.slider_max
    dkb.value = skb.value
    dkb.relative_key = keys.get(skb.relative_key.name) or dst.shape_keys.reference_key
    dkb.vertex_group = skb.vertex_group
    dkb.interpolation = skb.interpolation
    dkb.mute = skb.mute


def copy_mesh_materials(dst, src, resolve):
    dst.materials.clear()
    for m in src.materials:
        dst.materials.append(resolve(m))


MESH_COPY = {
    "positions": lambda d, s, _n, _r: copy_positions(d, s),
    "uv": lambda d, s, n, _r: copy_attribute(d, s, n),
    "attr": lambda d, s, n, _r: copy_attribute(d, s, n),
    "key": lambda d, s, n, _r: copy_shape_key(d, s, n),
    "materials": lambda d, s, _n, r: copy_mesh_materials(d, s, r),
}


# ---------------------------------------------------------------------------------------------- node trees

def copy_node_field(dst_nt, src_nt, field, resolve):
    node, sep, rest = split_node_field(field)
    sn, dn = src_nt.nodes.get(node), dst_nt.nodes.get(node)
    if sn is None or dn is None:
        return
    if sep == " › ":
        s, d = snapshot.node_values(sn).get(rest), snapshot.node_values(dn).get(rest)
        if s is not None and d is not None:
            d.default_value = id_value(s.default_value, resolve) if isinstance(s.default_value, bpy.types.ID) \
                else s.default_value
    elif rest == "muted":
        dn.mute = sn.mute
    else:
        set_path(dn, rest, get_path(sn, rest), resolve)


# ---------------------------------------------------------------------------------------------- dispatch

def copy_unit(cat, unit, dst, src, resolve):
    """Make `unit` of `dst` what it is in `src`. References go through resolve()."""
    if unit.locked:
        raise ValueError(f"{unit.label} can't be taken on its own")
    kind, _, name = unit.key.partition(":")
    if kind == "custom":
        copy_custom(dst, src, name, resolve)
    elif cat == "OBJECT" and kind in OBJECT_COPY:
        OBJECT_COPY[kind](dst, src, name, resolve)
    elif cat == "MESH" and kind in MESH_COPY:
        MESH_COPY[kind](dst, src, name, resolve)
    elif kind == "node":
        copy_node_field(dst.node_tree, src.node_tree, name, resolve)
    elif kind == "rna":
        set_path(dst, name, get_path(src, name), resolve)
    else:
        raise ValueError(f"{unit.label} can't be taken on its own")


def copy_units(cat, us, dst, src, resolve):
    for u in us:
        copy_unit(cat, u, dst, src, resolve)
