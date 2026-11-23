"""Fingerprint every data-block that can end up in a glTF export.

snapshot(ids) turns each ID into a flat {field: display string} dict. Two files are compared by
comparing those dicts (diff.py), so a field is "changed" exactly when its display string differs.
Heavy data (vertex positions, UVs, keyframes, image files) becomes a short content hash; the
details panel recomputes richer stats (how many vertices moved, by how much) only on demand.
"""

import hashlib
import math
import os
import re

import bpy
import numpy as np
from mathutils import Color, Euler, Matrix, Quaternion, Vector

# (key, bpy.data attribute, label, icon). Order is the order of the list in the sidebar.
CATEGORIES = (
    ("OBJECT", "objects", "Object", "OBJECT_DATA"),
    ("MESH", "meshes", "Mesh", "MESH_DATA"),
    ("MATERIAL", "materials", "Material", "MATERIAL"),
    ("IMAGE", "images", "Image", "IMAGE_DATA"),
    ("NODETREE", "node_groups", "Node Group", "NODETREE"),
    ("CAMERA", "cameras", "Camera", "CAMERA_DATA"),
    ("LIGHT", "lights", "Light", "LIGHT"),
    ("ACTION", "actions", "Action", "ACTION"),
    ("ARMATURE", "armatures", "Armature", "ARMATURE_DATA"),
    ("CURVE", "curves", "Curve", "CURVE_DATA"),
    ("WORLD", "worlds", "World", "WORLD"),
    ("COLLECTION", "collections", "Collection", "OUTLINER_COLLECTION"),
    ("SCENE", "scenes", "Scene", "SCENE_DATA"),
)
CAT_ATTR = {c[0]: c[1] for c in CATEGORIES}
CAT_LABEL = {c[0]: c[2] for c in CATEGORIES}
CAT_ICON = {c[0]: c[3] for c in CATEGORIES}
CAT_ORDER = {c[0]: i for i, c in enumerate(CATEGORIES)}
ATTR_CAT = {c[1]: c[0] for c in CATEGORIES}

# RNA properties that never matter for the exported result (UI state, bookkeeping, viewport looks).
SKIP = {
    "rna_type", "name", "name_full", "id_data", "use_fake_user", "use_extra_user", "is_runtime_data", "tag",
    "asset_data", "original", "library", "library_weak_reference", "override_library", "preview", "users",
    "session_uid", "is_evaluated", "is_embedded_data", "is_missing", "is_editable", "is_library_indirect",
    "id_type", "is_linked_packed", "deep_hash", "bl_idname", "bl_label", "bl_description", "bl_icon",
    "preview_render_type", "use_preview_world", "pass_index", "line_color", "line_priority", "is_dirty",
    "is_float", "has_data", "bindcode", "filepath_raw", "users_collection", "node_warnings",
}
# Node editor layout (where a node sits, how it looks); sockets and links are read separately.
NODE_SKIP = SKIP | {"location", "location_absolute", "width", "height", "select", "hide", "color", "use_custom_color",
                    "label", "parent", "warning_propagation", "dimensions", "inputs", "outputs", "internal_links"}
SKIP_PREFIX = ("bl_", "show_", "active_", "paint_", "lock_", "is_", "use_pin", "cycles", "texel_coverage",
               "compare_merge", "open_")  # open_*: a panel open in the modifier UI
KEEP = {"show_viewport", "show_render"}  # a modifier's toggles: they decide whether it is applied on export


def skipped(ident, skip=SKIP):
    return ident in skip or (ident.startswith(SKIP_PREFIX) and ident not in KEEP)


# ---------------------------------------------------------------------------------------------- formatting

def fnum(v):
    s = f"{v:.6g}"
    return "0" if s == "-0" else s


def fdeg(euler):
    """Euler rotation in degrees, the way the N panel shows it."""
    return "(" + ", ".join(f"{fnum(math.degrees(a))}°" for a in euler) + ")"


def fmt(v):
    """Stable, readable string for any RNA / ID property value."""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, float):
        return fnum(v)
    if isinstance(v, (int, str)):
        return str(v)
    if isinstance(v, bpy.types.ID):
        return ref(v)
    if isinstance(v, Matrix):
        return "(" + ", ".join(fnum(x) for row in v for x in row) + ")"
    if isinstance(v, (Vector, Euler, Quaternion, Color)):
        return "(" + ", ".join(fnum(x) for x in v) + ")"
    if isinstance(v, (set, frozenset)):
        return ", ".join(sorted(v)) or "—"
    if hasattr(v, "to_dict"):
        return str(v.to_dict())
    if hasattr(v, "to_list"):
        return str(v.to_list())
    try:
        return "(" + ", ".join(fmt(x) for x in v) + ")"
    except TypeError:
        return str(v)


_rename = None  # set by snapshot(): how this snapshot names the IDs it refers to


def ref(idb):
    """Name of an ID another one refers to. The incoming side is re-read with the IDs it renamed under
    their current names (session.rediff), so a renamed material doesn't change every slot using it."""
    return _rename(idb) if _rename is not None else idb.name


def digest(*parts):
    h = hashlib.blake2b(digest_size=5)
    for p in parts:
        if isinstance(p, np.ndarray):
            h.update(np.ascontiguousarray(p).tobytes())
        elif isinstance(p, bytes):
            h.update(p)
        else:
            h.update(str(p).encode())
    return "#" + h.hexdigest()


def read(coll, key, n, comps=1, dtype=np.float32):
    a = np.empty(n * comps, dtype=dtype)
    if n:
        coll.foreach_get(key, a)
    return a


# ---------------------------------------------------------------------------------------------- animated values
# Blender writes the value of the current frame onto every keyframed or driven property of the open
# file, so its value says where the playhead is, not what the file holds; the linked copy keeps the
# value it was saved with. Such fields read ANIMATED instead and the action's hash catches real changes.

ANIMATED = "animated"
_anim = {}  # id_data pointer -> {data path: array indices keyframed or driven}; cleared for every snapshot
_UNSET = object()


def anim_paths(idb):
    key = idb.as_pointer()
    if key not in _anim:
        paths = {}
        ad = getattr(idb, "animation_data", None)
        if ad:
            curves = list(ad.drivers)
            acts = [(ad.action, getattr(ad, "action_slot", None))]
            acts += [(st.action, getattr(st, "action_slot", None)) for tr in ad.nla_tracks if not tr.mute
                     for st in tr.strips if not st.mute]
            for act, slot in acts:
                if act:
                    sid = slot.identifier if slot else None
                    curves += [fc for s, fc in iter_fcurves(act) if not (s and sid and s != sid)]
            for fc in curves:
                if not fc.mute:  # a muted curve or driver leaves the saved value alone: compare it
                    paths.setdefault(fc.data_path, set()).add(fc.array_index)
        _anim[key] = paths
    return _anim[key]


def animated(struct, prop):
    """Array indices of `prop` of `struct` (any struct inside an ID) that are keyframed or driven."""
    paths = anim_paths(struct.id_data)
    if not paths:
        return set()
    try:
        return paths.get(struct.path_from_id(prop), set())
    except (ValueError, TypeError):
        return set()


def masked(v, idx, whole=fmt, comp=fmt):
    """whole(v), with the components at array indices `idx` reading ANIMATED: when only location.x
    is keyframed, a change to y still shows."""
    if not idx:
        return whole(v)
    if isinstance(v, str) or not hasattr(v, "__len__") or len(v) < 2 or len(idx) >= len(v):
        return ANIMATED
    return "(" + ", ".join(ANIMATED if i in idx else comp(c) for i, c in enumerate(v)) + ")"


def value(struct, prop, v=_UNSET, whole=fmt, comp=fmt):
    """fmt() of struct.prop, its keyframed or driven components reading ANIMATED."""
    return masked(getattr(struct, prop) if v is _UNSET else v, animated(struct, prop), whole, comp)


def deg(a):
    return f"{fnum(math.degrees(a))}°"


def fcurve_digest(fc):
    """Keyframes, handles, interpolation, extrapolation and modifiers of an F-curve."""
    kp = fc.keyframe_points
    n = len(kp)
    mods = []
    for m in fc.modifiers:  # Noise, Cycles, ...: their settings change the motion too
        sub = {}
        rna_fields(m, "", sub, depth=1)
        mods.append((m.type, sorted(sub.items())))
    return digest(read(kp, "co", n, 2), read(kp, "handle_left", n, 2), read(kp, "handle_right", n, 2),
                  [(k.interpolation, k.easing) for k in kp], fc.extrapolation, fc.mute, mods)


def driver_digest(ad):
    """Every driver of an animation_data: what it drives, its expression, its variables and their
    targets, and its own F-curve. Driven values read ANIMATED, so this is where their changes show."""
    out = []
    for fc in ad.drivers:
        d = fc.driver
        variables = [(v.name, v.type, [(fmt(t.id), t.id_type, t.data_path, t.bone_target, t.transform_type,
                                        t.transform_space, t.rotation_mode) for t in v.targets])
                     for v in d.variables]
        out.append((fc.data_path, fc.array_index, d.type, d.expression, d.use_self, variables, fcurve_digest(fc)))
    return digest(sorted(out, key=lambda x: (x[0], x[1])))


# Keyframes and drivers of an ID and of the IDs embedded in it (a material's node tree, a mesh's
# shape keys): (field prefix, the ID or None). Values they animate read ANIMATED; these fields show
# what animates them.
def anim_owners(idb):
    return (("", idb), ("node tree ", getattr(idb, "node_tree", None)), ("shape keys ", getattr(idb, "shape_keys", None)))


ANIM_FIELDS = {f"{p}{k}" for p, _o in anim_owners(None) for k in ("drivers", "action")} - {"action"}


def anim_fields(idb, out):
    for prefix, owner in anim_owners(idb):
        ad = getattr(owner, "animation_data", None) if owner is not None else None
        if not ad:
            continue
        if ad.drivers:
            out[f"{prefix}drivers"] = driver_digest(ad)
        if prefix and ad.action:  # an ID's own action is listed already (object: "action", others: rna)
            slot = getattr(ad, "action_slot", None)
            out[f"{prefix}action"] = ref(ad.action) + (f" ({slot.identifier})" if slot else "")


# ---------------------------------------------------------------------------------------------- generic RNA

def rna_fields(struct, prefix, out, depth=0, skip=SKIP, runtime=False):
    """Every editable property of `struct`; owned sub-structs recurse, nested collections hash.
    runtime: also read properties defined at runtime. Add-ons register theirs that way (skipped), but so
    does Blender for Geometry Nodes nodes (a Boolean Math's operation) and modifier inputs."""
    for p in struct.bl_rna.properties:
        ident = p.identifier
        if skipped(ident, skip) or (getattr(p, "is_runtime", False) and not runtime):
            continue
        try:
            v = getattr(struct, ident)
        except Exception:
            continue
        key = prefix + ident
        if p.type == "POINTER":
            if isinstance(v, bpy.types.ID) or (v is None and not p.is_readonly):
                out[key] = fmt(v)
            elif v is not None and p.is_readonly and depth < 2:
                rna_fields(v, key + ".", out, depth + 1, skip, runtime)
            elif v is not None and hasattr(v, "name"):
                out[key] = str(v.name)
        elif p.type == "COLLECTION":
            if depth >= 1 and len(v) < 4096:
                items = []
                for item in v:
                    sub = {}
                    rna_fields(item, "", sub, depth + 1, skip, runtime)
                    items.append(sorted(sub.items()))
                out[key] = f"{len(items)} items {digest(items)}"
        elif not p.is_readonly:
            out[key] = value(struct, ident, v)


def custom_props(idb, out):
    """Custom properties, exported as glTF extras."""
    for k in idb.keys():
        if k.startswith("_") or k in ("cycles", "cm_draft") or k.startswith("tc_"):
            continue
        out[f"custom › {k}"] = masked(idb[k], anim_paths(idb).get(f'["{k}"]'))


def node_values(node):
    """{label: socket} for the values a node holds itself: unlinked inputs (a repeated name gets
    " 2", " 3"...) and the output of RGB / Value nodes, which keep their value there."""
    out, seen = {}, {}
    for sock in node.inputs:
        if sock.is_linked or not hasattr(sock, "default_value") or not sock.enabled:
            continue
        label = sock.name
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:
            label = f"{label} {seen[label]}"
        out[label] = sock
    if node.type in ("RGB", "VALUE"):
        out["value"] = node.outputs[0]
    return out


def node_tree_fields(nt, out, prefix=""):
    if nt is None:
        return
    for node in nt.nodes:
        n = f"{prefix}{node.name}"
        out[f"{n} · type"] = node.bl_idname
        if node.mute:
            out[f"{n} · muted"] = "on"
        rna_fields(node, f"{n} · ", out, depth=1, skip=NODE_SKIP, runtime=True)
        for label, sock in node_values(node).items():
            out[f"{n} › {label}"] = value(sock, "default_value")
    for link in nt.links:
        if link.is_muted:
            continue
        # Socket identifiers, not names: a Math node has two inputs called "Value".
        to = f"{link.to_node.name}.{link.to_socket.identifier}"
        if link.to_socket.is_multi_input:  # order matters (Join Geometry, ...)
            to += f" #{getattr(link, 'multi_input_sort_id', 0)}"
        out[f"{prefix}link {link.from_node.name}.{link.from_socket.identifier} → {to}"] = "linked"
    if hasattr(nt, "interface"):
        for item in nt.interface.items_tree:
            if item.item_type == "SOCKET":
                out[f"{prefix}interface {item.in_out.lower()} {item.name}"] = item.socket_type


# ---------------------------------------------------------------------------------------------- modifier inputs

def gn_inputs(mod):
    """[(label, identifier)] of the inputs a Geometry Nodes modifier shows (geometry sockets hold no value).
    A repeated name gets " 2", " 3"..."""
    ng = getattr(mod, "node_group", None) if mod.type == "NODES" else None
    if ng is None:
        return []
    out, seen = [], {}
    for item in ng.interface.items_tree:
        if item.item_type != "SOCKET" or item.in_out != "INPUT" or item.socket_type == "NodeSocketGeometry":
            continue
        seen[item.name] = seen.get(item.name, 0) + 1
        out.append((item.name if seen[item.name] == 1 else f"{item.name} {seen[item.name]}", item.identifier))
    return out


def gn_input(mod, ident):
    """The struct holding one input of a Geometry Nodes modifier (properties.inputs, Blender 5.3), or
    None (older versions keep them as ID properties of the modifier)."""
    inputs = getattr(getattr(mod, "properties", None), "inputs", None)
    return getattr(inputs, ident, None) if inputs is not None else None


def gn_input_fields(mod, prefix, out):
    """One field per modifier input: its value, or the attribute / layer it reads instead."""
    for label, ident in gn_inputs(mod):
        st = gn_input(mod, ident)
        if st is not None:
            sub = {}
            rna_fields(st, "", sub, depth=1, runtime=True)
            v = sub.get("value", "")
            kind = sub.get("type", "VALUE")
        else:
            try:
                raw = mod[ident]
            except (KeyError, TypeError):
                continue
            v = masked(raw, anim_paths(mod.id_data).get(f'modifiers["{mod.name}"]["{ident}"]'))
            kind = "ATTRIBUTE" if mod.get(f"{ident}_use_attribute") else "VALUE"
            sub = {"attribute_name": str(mod.get(f"{ident}_attribute_name", ""))}
        if kind != "VALUE":
            v = f"{kind.lower()} {sub.get('attribute_name') or sub.get('layer_name') or ''}".rstrip()
        out[f"{prefix}input {label}"] = v


# ---------------------------------------------------------------------------------------------- per type

def snap_object(o):
    f = {"type": o.type, "data": fmt(o.data), "parent": fmt(o.parent)}
    if o.parent:
        f["parent type"] = o.parent_type
        if o.parent_type == "BONE":
            f["parent bone"] = o.parent_bone
        m = o.matrix_parent_inverse
        f["parent inverse"] = "identity" if m == Matrix.Identity(4) else fmt(m)
    f["location"] = value(o, "location")
    mode = o.rotation_mode
    if mode == "QUATERNION":
        f["rotation"] = value(o, "rotation_quaternion")
    elif mode == "AXIS_ANGLE":
        f["rotation"] = value(o, "rotation_axis_angle", tuple(o.rotation_axis_angle))
    else:
        f["rotation"] = value(o, "rotation_euler", whole=fdeg, comp=deg)
    f["rotation mode"] = mode
    f["scale"] = value(o, "scale")
    f["delta location"] = value(o, "delta_location")
    f["delta rotation"] = value(o, "delta_rotation_euler", whole=fdeg, comp=deg)
    f["delta scale"] = value(o, "delta_scale")
    f["hide in renders"] = value(o, "hide_render")
    f["disabled in viewports"] = value(o, "hide_viewport")
    f["collections"] = ", ".join(sorted(ref(c) for c in o.users_collection
                                        if c.as_pointer() not in _preview_masters)) or "—"
    if o.instance_type != "NONE":
        f["instancing"] = o.instance_type
        f["instance collection"] = fmt(o.instance_collection)
    for i, slot in enumerate(o.material_slots):
        f[f"material slot {i + 1}"] = f"{fmt(slot.material)} ({slot.link.lower()})"
    if o.vertex_groups:
        f["vertex groups"] = ", ".join(g.name for g in o.vertex_groups)
    for i, mod in enumerate(o.modifiers):
        f[f"modifier {i + 1}"] = f"{mod.name} ({mod.type})"
        rna_fields(mod, f"modifier {mod.name} · ", f, depth=1)
        gn_input_fields(mod, f"modifier {mod.name} · ", f)
    for i, con in enumerate(o.constraints):
        f[f"constraint {i + 1}"] = f"{con.name} ({con.type})"
        rna_fields(con, f"constraint {con.name} · ", f, depth=1)
    ad = o.animation_data
    if ad:
        f["action"] = fmt(ad.action)
        slot = getattr(ad, "action_slot", None)
        if slot:
            f["action slot"] = slot.identifier
    if o.pose:
        f["pose"] = digest([(b.name, *(value(b, p) for p in ("location", "rotation_quaternion", "rotation_euler",
                                                               "scale")))
                            for b in o.pose.bones])
    custom_props(o, f)
    return f


ATTR_COMPONENTS = {"FLOAT": 1, "INT": 1, "BOOLEAN": 1, "INT8": 1, "FLOAT2": 2, "INT32_2D": 2, "INT16_2D": 2,
                   "FLOAT_VECTOR": 3, "FLOAT_COLOR": 4, "BYTE_COLOR": 4, "QUATERNION": 4, "FLOAT4X4": 16}
ATTR_KEY = {"FLOAT2": "vector", "FLOAT_VECTOR": "vector", "FLOAT_COLOR": "color", "BYTE_COLOR": "color"}
ATTR_DTYPE = {"INT": np.int32, "INT8": np.int32, "INT32_2D": np.int32, "INT16_2D": np.int32, "BOOLEAN": bool}
KEEP_INTERNAL = {".corner_vert", ".edge_verts"}


def attribute_array(a):
    comps = ATTR_COMPONENTS.get(a.data_type)
    if comps is None:
        return None
    try:
        return read(a.data, ATTR_KEY.get(a.data_type, "value"), len(a.data), comps, ATTR_DTYPE.get(a.data_type, np.float32))
    except Exception:
        return None


def snap_mesh(me):
    if me.is_editmode:  # attributes would read the edit-mode data, laid out differently: read a
        tmp = me.copy()  # copy, which has the mesh as session.refresh_current synced it
        try:
            return snap_mesh(tmp)
        finally:
            for idb in (tmp, tmp.shape_keys):  # freed: its address may come back as another ID
                if idb:
                    _anim.pop(idb.as_pointer(), None)
            bpy.data.meshes.remove(tmp)
    f = {"vertices": str(len(me.vertices)), "edges": str(len(me.edges)), "faces": str(len(me.polygons))}
    f["positions"] = digest(read(me.vertices, "co", len(me.vertices), 3))
    f["topology"] = digest(read(me.polygons, "loop_total", len(me.polygons), 1, np.int32),
                           *(attribute_array(me.attributes[n]) for n in KEEP_INTERNAL if n in me.attributes))
    for a in me.attributes:
        if a.name == "position" or (a.name.startswith(".") and a.name not in KEEP_INTERNAL):
            continue
        if a.name in KEEP_INTERNAL:
            continue
        kind = "uv map" if a.domain == "CORNER" and a.data_type == "FLOAT2" else f"attribute ({a.domain.lower()})"
        arr = attribute_array(a)
        f[f"{kind} {a.name}"] = f"{a.data_type.lower()} {digest(arr) if arr is not None else '?'}"
    f["materials"] = ", ".join(fmt(m) for m in me.materials) or "—"
    if me.shape_keys:
        for kb in me.shape_keys.key_blocks:
            f[f"shape key {kb.name}"] = f"{digest(read(kb.data, 'co', len(kb.data), 3))} value {value(kb, 'value')} " \
                                         f"relative to {kb.relative_key.name}"
    if len(me.vertices) <= 100_000:  # no bulk API for deform weights; skip the Python loop on huge meshes
        weights = [(v.index, g.group, round(g.weight, 5)) for v in me.vertices for g in v.groups]
        if weights:
            f["vertex weights"] = digest(weights)
    else:
        f["vertex weights"] = "not compared (over 100k vertices)"
    custom_props(me, f)
    return f


def snap_material(m):
    f = {}
    rna_fields(m, "", f)
    f.pop("use_nodes", None)
    node_tree_fields(m.node_tree, f)
    custom_props(m, f)
    return f


_file_hash_cache = {}


def file_digest(path):
    try:
        st = os.stat(path)
    except OSError:
        return "missing file"
    key = (path, st.st_size, st.st_mtime_ns)
    if key not in _file_hash_cache:
        h = hashlib.blake2b(digest_size=5)
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        _file_hash_cache[key] = f"#{h.hexdigest()} ({st.st_size / 1e6:.2f} MB)"
    return _file_hash_cache[key]


def image_path(img):
    """Absolute path of an image's file. A .blend saved on Windows keeps backslashes (`//..\\textures\\a.png`,
    `C:\\Users\\...`), which only Windows reads as separators: elsewhere they would make the whole path one
    file name, and the file unreadable."""
    path = img.filepath
    if not path:
        return ""
    if os.sep == "/":
        path = path.replace("\\", "/")
    return os.path.normpath(bpy.path.abspath(path, library=img.library))


def image_files(img):
    """Every file an image reads: each UDIM tile, each frame of a sequence, else its one file."""
    path = image_path(img)
    if img.source == "TILED":
        return [path.replace("<UDIM>", str(t.number))
                .replace("<UVTILE>", f"u{(t.number - 1001) % 10 + 1}_v{(t.number - 1001) // 10 + 1}")
                for t in img.tiles]
    if img.source == "SEQUENCE":
        folder, name = os.path.split(path)
        m = re.match(r"(.*?)(\d+)(\D*)$", name)
        if m:
            head, tail = m.group(1), m.group(3)
            frame = re.compile(re.escape(head) + r"\d+" + re.escape(tail) + "$")
            try:
                return sorted(os.path.join(folder, f) for f in os.listdir(folder) if frame.match(f)) or [path]
            except OSError:
                pass
    return [path]


def file_name(img):
    """The name of the file an image reads (its folder isn't compared). Two versions of a .blend often
    sit in different folders, so the same file is reached by different relative paths, or each has its
    own copy of the textures; the content hash reads the bytes wherever they are. The name is what a
    glTF export calls the texture, so a relink to another file shows."""
    return os.path.basename(image_path(img)) or "—"


def files_digest(paths):
    if len(paths) == 1:
        return file_digest(paths[0])
    parts = [file_digest(p) for p in paths]
    missing = sum(p == "missing file" for p in parts)
    h = digest(*((os.path.basename(p), d) for p, d in zip(paths, parts)))  # names, not folders: the
    # two versions usually sit in different folders
    return f"{h} ({len(paths)} files{f', {missing} missing' if missing else ''})"


def snap_image(img):
    f = {"source": img.source, "file": file_name(img), "color space": img.colorspace_settings.name,
         "alpha": img.alpha_mode, "packed": fmt(bool(img.packed_file))}
    if img.packed_file:
        f["content"] = digest(*(bytes(pf.packed_file.data) for pf in img.packed_files))
    elif img.source in ("FILE", "SEQUENCE", "MOVIE", "TILED") and img.filepath:
        f["content"] = files_digest(image_files(img))
    elif img.source == "GENERATED":
        f["generated"] = f"{img.generated_type} {img.generated_width}×{img.generated_height} {fmt(img.generated_color)}"
    # No pixel size: img.size is only known once Blender has loaded the pixels, which depends on what the
    # viewport drew this session, so it showed as "1024×1024 → —" on untouched images. The content hash
    # (or the generated size) already catches a resize.
    custom_props(img, f)
    return f


def iter_fcurves(act):
    if getattr(act, "layers", None):
        for layer in act.layers:
            for strip in layer.strips:
                for cb in getattr(strip, "channelbags", ()):
                    for fc in cb.fcurves:
                        yield cb.slot.identifier if cb.slot else "", fc
    elif hasattr(act, "fcurves"):  # legacy actions (Blender < 5.0)
        for fc in act.fcurves:
            yield "", fc


def snap_action(act):
    f = {"frame range": fmt(tuple(act.frame_range))}
    for slot, fc in iter_fcurves(act):
        f[f"curve {slot + ' ' if slot else ''}{fc.data_path}[{fc.array_index}]"] = \
            f"{len(fc.keyframe_points)} keys {fcurve_digest(fc)}"
    custom_props(act, f)
    return f


def snap_armature(arm):
    f = {"bones": str(len(arm.bones))}
    for b in arm.bones:
        f[f"bone {b.name}"] = digest(tuple(b.matrix_local), tuple(b.head_local), tuple(b.tail_local),
                                     b.parent.name if b.parent else "", b.use_deform, b.use_connect)
    custom_props(arm, f)
    return f


def snap_curve(cu):
    f = {}
    rna_fields(cu, "", f)
    parts = []
    splines = list(getattr(cu, "splines", ()))
    for s in splines:
        bp, pt = s.bezier_points, s.points
        nb, np_ = len(bp), len(pt)
        parts += [f"{s.type} {s.use_cyclic_u} {s.order_u} {s.resolution_u} {s.use_smooth}",
                  read(bp, "co", nb, 3), read(bp, "handle_left", nb, 3), read(bp, "handle_right", nb, 3),
                  read(bp, "radius", nb), read(bp, "tilt", nb),
                  read(pt, "co", np_, 4), read(pt, "radius", np_), read(pt, "tilt", np_), read(pt, "weight", np_)]
    f["splines"] = f"{len(splines)} {digest(*parts)}"
    if hasattr(cu, "body"):
        f["text"] = cu.body
    custom_props(cu, f)
    return f


def snap_generic(idb):
    f = {}
    rna_fields(idb, "", f)
    custom_props(idb, f)
    return f


def snap_camera(cam):
    f = snap_generic(cam)
    for k in ("angle", "angle_x", "angle_y"):  # derived from lens and sensor, which are listed already
        f.pop(k, None)
    return f


def snap_world(w):
    f = snap_generic(w)
    f.pop("use_nodes", None)
    node_tree_fields(w.node_tree, f)
    return f


def snap_node_group(nt):
    f = {"type": nt.bl_idname}
    node_tree_fields(nt, f)
    return f


def snap_collection(c):
    return {"objects": ", ".join(sorted(ref(o) for o in c.objects)) or "—",
            "children": ", ".join(sorted(ref(ch) for ch in c.children)) or "—",
            "hide in renders": fmt(c.hide_render), "disabled in viewports": fmt(c.hide_viewport),
            "instance offset": fmt(c.instance_offset)}


def snap_scene(sc):
    """Only the settings Merge can take (session.scene_settings); objects and collections have their own rows."""
    r = sc.render
    return {"camera": fmt(sc.camera), "world": fmt(sc.world), "frame start": str(sc.frame_start),
            "frame end": str(sc.frame_end), "fps": fnum(r.fps / r.fps_base),
            "resolution": f"{r.resolution_x}×{r.resolution_y} @ {r.resolution_percentage}%",
            "unit scale": fnum(sc.unit_settings.scale_length)}


SNAPPERS = {
    "OBJECT": snap_object, "MESH": snap_mesh, "MATERIAL": snap_material, "IMAGE": snap_image,
    "NODETREE": snap_node_group, "CAMERA": snap_camera, "LIGHT": snap_generic, "ACTION": snap_action,
    "ARMATURE": snap_armature, "CURVE": snap_curve, "WORLD": snap_world, "COLLECTION": snap_collection,
    "SCENE": snap_scene,
}


_preview_masters = set()  # top-level collections of runtime (preview) scenes: not part of the file


def snapshot(owned, rename=None):
    """{(cat, name): fields} for every ID for which owned(id) is true. rename(id) -> the name used for
    the IDs it refers to (default: their own)."""
    global _rename
    _rename = rename
    try:
        return _snapshot(owned)
    finally:
        _rename = None


def _snapshot(owned):
    _preview_masters.clear()
    _anim.clear()
    _preview_masters.update(s.collection.as_pointer() for s in bpy.data.scenes
                            if s.is_runtime_data and s.library is None)
    out = {}
    for cat, attr, _label, _icon in CATEGORIES:
        for idb in list(getattr(bpy.data, attr)):  # a list: snap_mesh adds and removes a copy
            if not owned(idb):
                continue
            try:
                out[(cat, idb.name)] = fields = SNAPPERS[cat](idb)
                anim_fields(idb, fields)
            except Exception as e:  # never let one odd data-block hide the rest of the diff
                out[(cat, idb.name)] = {"error": f"could not read: {e}"}
    return out


# ---------------------------------------------------------------------------------------------- merge units
# A unit is the smallest group of fields that can be taken from the incoming file on its own and
# still give a valid data-block (apply.units adds the locks). unit_key() is the grouping, next to
# the snappers that name the fields. Keys are names, never indices, so picks survive a refresh.

UNIT_CATS = {"OBJECT", "MESH", "MATERIAL", "WORLD", "LIGHT", "CAMERA"}
OBJECT_UNITS = {
    "location": "transform", "rotation": "transform", "rotation mode": "transform", "scale": "transform",
    "delta location": "transform", "delta rotation": "transform", "delta scale": "transform",
    "hide in renders": "visibility", "disabled in viewports": "visibility",
    "parent": "parent", "parent type": "parent", "parent bone": "parent", "parent inverse": "parent",
    "collections": "collections", "action": "animation", "action slot": "animation", "drivers": "drivers",
    "pose": "pose", "instancing": "instancing", "instance collection": "instancing", "data": "data",
    "type": "type", "vertex groups": "vertex groups",
}
UNIT_LABEL = {"transform": "Transform", "visibility": "Visibility", "parent": "Parent", "collections": "Collections",
              "animation": "Animation", "drivers": "Drivers", "pose": "Pose", "instancing": "Instancing",
              "data": "Data", "type": "Type", "vertex groups": "Vertex groups", "modifiers": "Modifier stack",
              "constraints": "Constraint stack", "topology": "Topology", "positions": "Positions",
              "materials": "Materials", "weights": "Vertex weights", "wiring": "Node wiring"}
MESH_TOPOLOGY = {"vertices", "edges", "faces", "topology"}


def unit_key(cat, field, nodes=()):
    """(unit key, label) of a changed field. `nodes`: node names of a material / world (its fields
    start with one). Fields nothing knows how to copy on their own get a "field:" unit (locked)."""
    if cat != "OBJECT" and field in ANIM_FIELDS:  # drivers and embedded actions: the whole row
        return f"field:{field}", field
    if field.startswith("custom › "):
        k = field[len("custom › "):]
        return f"custom:{k}", f"Custom property {k}"
    if cat == "OBJECT":
        if field in OBJECT_UNITS:
            key = OBJECT_UNITS[field]
            return key, UNIT_LABEL[key]
        if field.startswith("material slot "):
            n = field[len("material slot "):]
            return f"slot:{n}", f"Material slot {n}"
        for kind, prefix in (("modifier", "mod"), ("constraint", "con")):
            if field.startswith(kind + " "):
                rest = field[len(kind) + 1:]
                if " · " in rest:
                    name = rest.split(" · ", 1)[0]
                    return f"{prefix}:{name}", f"{kind.title()} {name}"
                if rest.isdigit():
                    key = kind + "s"
                    return key, UNIT_LABEL[key]
    elif cat == "MESH":
        if field in MESH_TOPOLOGY:
            return "topology", UNIT_LABEL["topology"]
        if field in ("positions", "materials"):
            return field, UNIT_LABEL[field]
        if field == "vertex weights":
            return "weights", UNIT_LABEL["weights"]
        if field.startswith("uv map "):
            n = field[len("uv map "):]
            return f"uv:{n}", f"UV map {n}"
        if field.startswith("attribute ("):
            n = field.split(") ", 1)[1]
            return f"attr:{n}", f"Attribute {n}"
        if field.startswith("shape key "):
            n = field[len("shape key "):]
            return f"key:{n}", f"Shape key {n}"
    elif cat in ("MATERIAL", "WORLD"):
        if field.startswith(("link ", "interface ")) or field.endswith(" · type"):
            return "wiring", UNIT_LABEL["wiring"]
        for sep in (" · ", " › "):
            if sep in field and field.split(sep, 1)[0] in nodes:
                return f"node:{field}", field
        return f"rna:{field}", field
    elif cat in ("LIGHT", "CAMERA"):
        return f"rna:{field}", field
    return f"field:{field}", field


def node_names(fields):
    return {f[:-len(" · type")] for f in fields if f.endswith(" · type")}


# ---------------------------------------------------------------------------------------------- references

def references(idb):
    """IDs this one uses directly (for cascading a choice to what an object is made of)."""
    refs = []
    if isinstance(idb, bpy.types.Object):
        refs += [idb.data] + [s.material for s in idb.material_slots]
        if idb.animation_data:
            refs.append(idb.animation_data.action)
    elif isinstance(idb, bpy.types.Mesh) or isinstance(idb, bpy.types.Curve):
        refs += list(idb.materials)
    elif isinstance(idb, (bpy.types.Material, bpy.types.World, bpy.types.NodeTree)):
        nt = idb if isinstance(idb, bpy.types.NodeTree) else idb.node_tree
        if nt:
            for n in nt.nodes:
                refs += [getattr(n, "image", None), getattr(n, "node_tree", None)]
    if getattr(idb, "animation_data", None) and not isinstance(idb, bpy.types.Object):
        refs.append(idb.animation_data.action)
    return [r for r in refs if isinstance(r, bpy.types.ID)]
