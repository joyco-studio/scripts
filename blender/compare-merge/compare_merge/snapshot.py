"""Fingerprint every data-block that can end up in a glTF export.

snapshot(ids) turns each ID into a flat {field: display string} dict. Two files are compared by
comparing those dicts (diff.py), so a field is "changed" exactly when its display string differs.
Heavy data (vertex positions, UVs, keyframes, image files) becomes a short content hash; the
details panel recomputes richer stats (how many vertices moved, by how much) only on demand.
"""

import hashlib
import math
import os

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
    "id_type", "is_linked_packed", "deep_hash", "location", "location_absolute", "width", "height", "select",
    "hide", "color", "use_custom_color", "label", "parent", "warning_propagation", "dimensions", "bl_idname",
    "bl_label", "bl_description", "bl_icon", "preview_render_type", "use_preview_world", "pass_index",
    "line_color", "line_priority", "is_dirty", "is_float", "has_data", "bindcode", "filepath_raw", "users_collection",
    "inputs", "outputs", "internal_links", "angle", "angle_x", "angle_y",  # sockets read separately; lens-derived
}
SKIP_PREFIX = ("bl_", "show_", "active_", "paint_", "lock_", "is_", "use_pin", "cycles", "texel_coverage",
               "compare_merge")


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
        return v.name
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


# ---------------------------------------------------------------------------------------------- generic RNA

def rna_fields(struct, prefix, out, depth=0):
    """Every editable property of `struct`; owned sub-structs recurse, nested collections hash."""
    for p in struct.bl_rna.properties:
        ident = p.identifier
        if ident in SKIP or ident.startswith(SKIP_PREFIX) or getattr(p, "is_runtime", False):
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
                rna_fields(v, key + ".", out, depth + 1)
            elif v is not None and hasattr(v, "name"):
                out[key] = str(v.name)
        elif p.type == "COLLECTION":
            if depth >= 1 and len(v) < 4096:
                items = []
                for item in v:
                    sub = {}
                    rna_fields(item, "", sub, depth + 1)
                    items.append(sorted(sub.items()))
                out[key] = f"{len(items)} items {digest(items)}"
        elif not p.is_readonly:
            out[key] = fmt(v)


def custom_props(idb, out):
    """Custom properties, exported as glTF extras."""
    for k in idb.keys():
        if k.startswith("_") or k in ("cycles", "cm_draft") or k.startswith("tc_"):
            continue
        out[f"custom › {k}"] = fmt(idb[k])


def node_tree_fields(nt, out, prefix=""):
    if nt is None:
        return
    for node in nt.nodes:
        n = f"{prefix}{node.name}"
        out[f"{n} · type"] = node.bl_idname
        if node.mute:
            out[f"{n} · muted"] = "on"
        rna_fields(node, f"{n} · ", out, depth=1)
        seen = {}
        for sock in node.inputs:
            if sock.is_linked or not hasattr(sock, "default_value") or not sock.enabled:
                continue
            label = sock.name
            seen[label] = seen.get(label, 0) + 1
            if seen[label] > 1:
                label = f"{label} {seen[label]}"
            out[f"{n} › {label}"] = fmt(sock.default_value)
    for link in nt.links:
        if link.is_muted:
            continue
        out[f"{prefix}link {link.from_node.name}.{link.from_socket.name} → {link.to_node.name}.{link.to_socket.name}"] = "linked"
    if hasattr(nt, "interface"):
        for item in nt.interface.items_tree:
            if item.item_type == "SOCKET":
                out[f"{prefix}interface {item.in_out.lower()} {item.name}"] = item.socket_type


# ---------------------------------------------------------------------------------------------- per type

def snap_object(o):
    f = {"type": o.type, "data": fmt(o.data), "parent": fmt(o.parent)}
    if o.parent:
        f["parent type"] = o.parent_type
        if o.parent_type == "BONE":
            f["parent bone"] = o.parent_bone
        m = o.matrix_parent_inverse
        f["parent inverse"] = "identity" if m == Matrix.Identity(4) else fmt(m)
    f["location"] = fmt(o.location)
    mode = o.rotation_mode
    if mode == "QUATERNION":
        f["rotation"] = fmt(o.rotation_quaternion)
    elif mode == "AXIS_ANGLE":
        f["rotation"] = fmt(tuple(o.rotation_axis_angle))
    else:
        f["rotation"] = fdeg(o.rotation_euler)
    f["rotation mode"] = mode
    f["scale"] = fmt(o.scale)
    f["delta location"] = fmt(o.delta_location)
    f["delta rotation"] = fdeg(o.delta_rotation_euler)
    f["delta scale"] = fmt(o.delta_scale)
    f["hide in renders"] = fmt(o.hide_render)
    f["disabled in viewports"] = fmt(o.hide_viewport)
    f["collections"] = ", ".join(sorted(c.name for c in o.users_collection
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
    for i, con in enumerate(o.constraints):
        f[f"constraint {i + 1}"] = f"{con.name} ({con.type})"
        rna_fields(con, f"constraint {con.name} · ", f, depth=1)
    ad = o.animation_data
    if ad:
        f["action"] = fmt(ad.action)
        slot = getattr(ad, "action_slot", None)
        if slot:
            f["action slot"] = slot.identifier
        if ad.drivers:
            f["drivers"] = digest(sorted((d.data_path, d.array_index, d.driver.expression) for d in ad.drivers))
    if o.pose:
        f["pose"] = digest([(b.name, tuple(b.location), tuple(b.rotation_quaternion), tuple(b.rotation_euler),
                             tuple(b.scale)) for b in o.pose.bones])
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
            f[f"shape key {kb.name}"] = f"{digest(read(kb.data, 'co', len(kb.data), 3))} value {fnum(kb.value)} " \
                                         f"relative to {kb.relative_key.name}"
    if len(me.vertices) <= 100_000:  # no bulk API for deform weights; skip the Python loop on huge meshes
        weights = [(g.group, round(g.weight, 5)) for v in me.vertices for g in v.groups]
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
    return os.path.normpath(bpy.path.abspath(img.filepath, library=img.library)) if img.filepath else ""


def snap_image(img):
    f = {"source": img.source, "file": img.filepath or "—", "color space": img.colorspace_settings.name,
         "alpha": img.alpha_mode, "packed": fmt(bool(img.packed_file))}
    if img.packed_file:
        f["content"] = digest(bytes(img.packed_file.data))
    elif img.source in ("FILE", "SEQUENCE", "MOVIE") and img.filepath:
        f["content"] = file_digest(image_path(img))
    elif img.source == "GENERATED":
        f["generated"] = f"{img.generated_type} {img.generated_width}×{img.generated_height} {fmt(img.generated_color)}"
    if img.has_data:
        f["size"] = f"{img.size[0]}×{img.size[1]}"
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
        kp = fc.keyframe_points
        n = len(kp)
        h = digest(read(kp, "co", n, 2), read(kp, "handle_left", n, 2), read(kp, "handle_right", n, 2),
                   [k.interpolation for k in kp], [m.type for m in fc.modifiers])
        f[f"curve {slot + ' ' if slot else ''}{fc.data_path}[{fc.array_index}]"] = f"{n} keys {h}"
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
    pts = []
    for s in getattr(cu, "splines", ()):
        pts.append((s.type, s.use_cyclic_u, read(s.bezier_points, "co", len(s.bezier_points), 3),
                    read(s.points, "co", len(s.points), 4)))
    f["splines"] = f"{len(pts)} {digest(pts)}"
    if hasattr(cu, "body"):
        f["text"] = cu.body
    custom_props(cu, f)
    return f


def snap_generic(idb):
    f = {}
    rna_fields(idb, "", f)
    custom_props(idb, f)
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
    return {"objects": ", ".join(sorted(o.name for o in c.objects)) or "—",
            "children": ", ".join(sorted(ch.name for ch in c.children)) or "—",
            "hide in renders": fmt(c.hide_render), "disabled in viewports": fmt(c.hide_viewport),
            "instance offset": fmt(c.instance_offset)}


def snap_scene(sc):
    r = sc.render
    return {"camera": fmt(sc.camera), "world": fmt(sc.world), "frame start": str(sc.frame_start),
            "frame end": str(sc.frame_end), "fps": fnum(r.fps / r.fps_base),
            "resolution": f"{r.resolution_x}×{r.resolution_y} @ {r.resolution_percentage}%",
            "unit scale": fnum(sc.unit_settings.scale_length),
            "top-level objects": ", ".join(sorted(o.name for o in sc.collection.objects)) or "—",
            "top-level collections": ", ".join(sorted(c.name for c in sc.collection.children)) or "—"}


SNAPPERS = {
    "OBJECT": snap_object, "MESH": snap_mesh, "MATERIAL": snap_material, "IMAGE": snap_image,
    "NODETREE": snap_node_group, "CAMERA": snap_generic, "LIGHT": snap_generic, "ACTION": snap_action,
    "ARMATURE": snap_armature, "CURVE": snap_curve, "WORLD": snap_world, "COLLECTION": snap_collection,
    "SCENE": snap_scene,
}


_preview_masters = set()  # top-level collections of runtime (preview) scenes: not part of the file


def snapshot(owned):
    """{(cat, name): fields} for every ID for which owned(id) is true."""
    _preview_masters.clear()
    _preview_masters.update(s.collection.as_pointer() for s in bpy.data.scenes
                            if s.is_runtime_data and s.library is None)
    out = {}
    for cat, attr, _label, _icon in CATEGORIES:
        for idb in getattr(bpy.data, attr):
            if not owned(idb):
                continue
            try:
                out[(cat, idb.name)] = SNAPPERS[cat](idb)
            except Exception as e:  # never let one odd data-block hide the rest of the diff
                out[(cat, idb.name)] = {"error": f"could not read: {e}"}
    return out


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
