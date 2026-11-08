"""Bake textures from the current UV layout into the suggested one, then switch to it.

For every image used by meshes that have a suggested map (<uv>_tc_suggest):
  * a new image <name>_tc is baked with Cycles: colour/data images as Emit from the original image
    read through the original UVs; normal maps as Normal, with the Normal Map node kept on the
    original UVs so tangent space is converted for rotated islands;
  * its size follows the suggestion: the material's main image gets the size Suggest UVs planned,
    the other images of that material keep their ratio to it;
  * the suggested layout is promoted (old UVs kept as <uv>_tc_backup) and the materials are switched
    to the baked images. Original images are never modified.

`revert` undoes it at any time: image nodes go back to their original image (tagged on the node) and
the backed-up UVs are copied back.
"""

import os

import bpy
import numpy as np

from . import core, export, uv_suggest

TAG = "tc_prebake_image"   # on image nodes: the original image's name
BAKED = "tc_baked_from"    # on baked images: the original image's name
SUFFIX = "_tc"


def _is_normal(tree, node):
    return any(l.to_node.type == "NORMAL_MAP" for o in node.outputs for l in o.links)


def _image_nodes(mat, img):
    """Image Texture nodes in the material's own tree showing `img` (node groups are not baked)."""
    return [n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.image == img]


def _pow2(x):
    return max(16, int(2 ** round(np.log2(max(x, 1)))))


def _plain_uv(node):
    """True when the image reads plain UVs (direct, UV Map / Texture Coordinate UV, reroutes, identity
    Mapping). A transformed lookup would be applied again on top of the baked layout."""
    sock, depth = node.inputs.get("Vector"), 0
    while sock is not None and sock.is_linked and depth < 8:
        link = sock.links[0]
        n = link.from_node
        if n.type == "UVMAP" or (n.type == "TEX_COORD" and link.from_socket.name == "UV"):
            return True
        if n.type == "REROUTE":
            sock = n.inputs[0]
        elif n.type == "MAPPING":
            ins = {i.name: i for i in n.inputs}
            ident = all(not ins[k].is_linked for k in ("Location", "Rotation", "Scale") if k in ins)
            ident = ident and all(abs(v) < 1e-6 for k in ("Location", "Rotation") if k in ins
                                  for v in ins[k].default_value)
            ident = ident and ("Scale" not in ins or all(abs(v - 1) < 1e-6 for v in ins["Scale"].default_value))
            if not ident:
                return False
            sock = n.inputs.get("Vector")
        else:
            return False
        depth += 1
    return True


def plan(context):
    """Images to bake: {image name: dict(size, users, normal)} plus skipped {image name: reason}.

    A suggested layout is only switched when every texture on it can be baked, so a texture is also
    skipped when it shares a layout with one that can't."""
    sizes = context.scene.get("tc_suggest_sizes")
    sizes = sizes.to_dict() if hasattr(sizes, "to_dict") else {}
    jobs, skipped = {}, {}
    for img_name, users in sorted(export.material_users(context).items()):
        img = bpy.data.images.get(img_name)
        if img is None or img.get(BAKED) or img.get(export.COPY_TAG):
            continue
        has = [u for u in users if u[2] and u[2] + uv_suggest.SUFFIX in u[0].data.uv_layers]
        if not has:
            continue

        def skip(reason):
            skipped[img_name] = dict(reason=reason, users=users)

        nodes = [n for u in users for n in _image_nodes(u[3], img)]
        per_mesh = {}
        for u in users:
            per_mesh.setdefault(u[0].data.name, set()).add(u[2])
        w, h = img.size
        if len(has) != len(users):
            others = sorted({u[0].name for u in users if u not in has})
            skip(f"also used by objects without a suggested layout ({', '.join(others[:3])})")
        elif any(not _image_nodes(u[3], img) for u in users):
            skip("used inside a node group")
        elif any(len(v) > 1 for v in per_mesh.values()):
            skip("read through different UV maps on the same mesh")
        elif not all(_plain_uv(n) for n in nodes):
            skip("its texture coordinates are transformed (Mapping or other nodes)")
        elif any(n.outputs["Alpha"].is_linked for n in nodes):
            skip("its alpha channel is used (alpha isn't baked yet)")
        elif len({_is_normal(None, n) for n in nodes}) > 1:
            skip("used both as a normal map and as a colour")
        elif w == 0 or h == 0:
            skip("image has no pixels on this machine")
        else:
            target = 0
            for o, slot, uv, mat, _ in users:
                main = core.tex_info(mat)
                planned = int(sizes.get(main[2], 0)) if main else 0
                if planned and main[0]:
                    target = max(target, _pow2(max(w, h) * planned / main[0]))
            if not target:
                skip("no suggested size for its material: run Suggest UVs")
            else:
                target = min(target, max(w, h))  # baking can't add detail the source doesn't have
                f = target / max(w, h)
                jobs[img_name] = dict(size=[max(1, round(w * f)), max(1, round(h * f))], users=users,
                                      normal=_is_normal(None, nodes[0]))
    layout = lambda u: (u[0].data.name, u[2])
    blocked = {layout(u) for v in skipped.values() for u in v["users"] if u[2]}
    changed = True
    while changed:  # a layout switches only if every texture on it converts
        changed = False
        for name in sorted(jobs):
            if any(layout(u) in blocked for u in jobs[name]["users"]):
                skipped[name] = dict(reason="shares a UV layout with a texture that can't be baked",
                                     users=jobs[name]["users"])
                blocked |= {layout(u) for u in jobs.pop(name)["users"]}
                changed = True
    return jobs, {k: v["reason"] for k, v in skipped.items()}


def _bake_one(context, img, job, margin):
    """Bake `img` into a new image on the suggested UVs (the active UV map). Returns the new image.
    Every temporary node, link and setting is undone, also when something fails."""
    w, h = job["size"]
    new = bpy.data.images.new(img.name + SUFFIX, w, h, alpha=False, float_buffer=img.is_float)
    try:  # a normal bake writes raw vectors: store them as data, whatever the original was tagged
        new.colorspace_settings.name = "Non-Color" if job["normal"] else img.colorspace_settings.name
    except TypeError:
        pass
    new[BAKED] = img.name
    temp, relinks, actives, normal_uvs = [], [], [], []
    mats = {u[3] for u in job["users"]}
    objs = sorted({u[0] for u in job["users"]}, key=lambda o: o.name)
    dummy = None
    ok = False

    def unlink(nt, sock):
        for l in list(sock.links):
            nt.links.remove(l)

    try:
        for mat in mats:
            nt = mat.node_tree
            src = _image_nodes(mat, img)[0]
            uv = next(u[2] for u in job["users"] if u[3] == mat)
            actives.append((nt, nt.nodes.active))
            tgt = nt.nodes.new("ShaderNodeTexImage")
            temp.append((nt, tgt))
            tgt.image = new
            for n in nt.nodes:
                n.select = False
            tgt.select = True
            nt.nodes.active = tgt
            if not src.inputs["Vector"].is_linked:  # read the original layout explicitly
                uvn = nt.nodes.new("ShaderNodeUVMap")
                temp.append((nt, uvn))
                uvn.uv_map = uv
                relinks.append((nt, src.inputs["Vector"], None))
                nt.links.new(uvn.outputs["UV"], src.inputs["Vector"])
            out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None) \
                or next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
            surf = out.inputs["Surface"]
            relinks.append((nt, surf, surf.links[0].from_socket if surf.is_linked else None))
            if job["normal"]:
                nm = next(l.to_node for o in src.outputs for l in o.links if l.to_node.type == "NORMAL_MAP")
                normal_uvs.append((nm, nm.uv_map))
                nm.uv_map = uv  # tangent space of the layout the normal map was made for
                sh = nt.nodes.new("ShaderNodeBsdfDiffuse")
                temp.append((nt, sh))
                nt.links.new(nm.outputs["Normal"], sh.inputs["Normal"])
            else:
                sh = nt.nodes.new("ShaderNodeEmission")
                temp.append((nt, sh))
                nt.links.new(src.outputs["Color"], sh.inputs["Color"])
            nt.links.new(sh.outputs[0], surf)
        # Cycles bakes every material on the selected objects into its active image node: give the
        # other materials a throwaway target so no original image is ever written to.
        others = {ms.material for o in objs for ms in o.material_slots
                  if ms.material and ms.material.use_nodes and ms.material not in mats}
        if others:
            dummy = bpy.data.images.new("TC bake scratch", 16, 16)
            for mat in others:
                nt = mat.node_tree
                actives.append((nt, nt.nodes.active))
                d = nt.nodes.new("ShaderNodeTexImage")
                temp.append((nt, d))
                d.image = dummy
                nt.nodes.active = d
        for o in context.view_layer.objects:
            o.select_set(o in objs)
        context.view_layer.objects.active = objs[0]
        bpy.ops.object.bake(type="NORMAL" if job["normal"] else "EMIT", margin=margin, use_clear=True,
                            normal_space="TANGENT")
        ok = True
    finally:
        for nt, sock, prev in reversed(relinks):
            unlink(nt, sock)
            if prev is not None:
                nt.links.new(prev, sock)
        for nm, uv in normal_uvs:
            nm.uv_map = uv
        for nt, n in temp:
            nt.nodes.remove(n)
        for nt, n in actives:
            if n is not None:
                nt.nodes.active = n
        if dummy is not None:
            bpy.data.images.remove(dummy)
        if not ok:
            bpy.data.images.remove(new)
    return new


def _save(img, new):
    """Write the baked image next to the original (or pack it when the original is packed)."""
    path = bpy.path.abspath(img.filepath) if img.filepath else ""
    if img.packed_file or not path:
        new.pack()
        return "packed"
    stem, _ = os.path.splitext(path)
    out = stem + SUFFIX + ".png"
    new.filepath_raw = out
    new.file_format = "PNG"
    new.save()
    try:
        new.filepath = bpy.path.relpath(out)
    except ValueError:
        new.filepath = out
    return out


def bake(context, props):
    """Bake every image into the suggested layout, promote it and switch the materials."""
    core.object_mode(context)
    jobs, skipped = plan(context)
    if not jobs:
        return [], skipped
    scene = context.scene
    bk = scene.render.bake
    saved_bake = (bk.use_selected_to_active, bk.target)
    saved = dict(engine=scene.render.engine, samples=scene.cycles.samples,
                 adaptive=scene.cycles.use_adaptive_sampling, sel=list(context.selected_objects),
                 active=context.view_layer.objects.active)
    meshes = {u[0].data for job in jobs.values() for u in job["users"]}
    uv_state = [(me, me.uv_layers.active.name if me.uv_layers.active else None) for me in meshes]
    baked = {}
    hidden = {o.name: o.hide_render for o in scene.objects}
    try:
        bk.use_selected_to_active, bk.target = False, "IMAGE_TEXTURES"
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 8
        scene.cycles.use_adaptive_sampling = False
        margin = max(2, int(props.pack_margin_px))
        where = {}
        for name, job in jobs.items():
            objs = {u[0] for u in job["users"]}
            for o in scene.objects:  # Cycles loads every visible object and texture: only these
                o.hide_render = o not in objs  # baked objects must render, even if hidden in the file
            for o, slot, uv, mat, _ in job["users"]:  # bakes are written into the active map
                o.data.uv_layers.active = o.data.uv_layers[uv + uv_suggest.SUFFIX]
            new = baked[name] = _bake_one(context, bpy.data.images[name], job, margin)
            where[name] = _save(bpy.data.images[name], new)  # saved before any material switches
            new.buffers_free()  # reloads from disk / packed data when shown
    except Exception:
        for new in baked.values():
            bpy.data.images.remove(new)
        raise
    finally:
        for o in scene.objects:
            if o.name in hidden:
                o.hide_render = hidden[o.name]
        for me, act in uv_state:
            if act and act in me.uv_layers:
                me.uv_layers.active = me.uv_layers[act]
        bk.use_selected_to_active, bk.target = saved_bake
        scene.render.engine = saved["engine"]
        scene.cycles.samples, scene.cycles.use_adaptive_sampling = saved["samples"], saved["adaptive"]
        for o in context.view_layer.objects:
            o.select_set(o in saved["sel"])
        context.view_layer.objects.active = saved["active"]
    switched = []
    try:
        for name, new in baked.items():
            img = bpy.data.images[name]
            for mat in {u[3] for u in jobs[name]["users"]}:
                for n in _image_nodes(mat, img):
                    n[TAG] = img.name
                    n.image = new
                    switched.append((n, img))
        layouts = {(u[0].data.name, u[2]) for job in jobs.values() for u in job["users"]}
        objs = sorted({u[0] for job in jobs.values() for u in job["users"]}, key=lambda o: o.name)
        uv_suggest.promote(context, objs, layouts)
    except Exception:
        for n, img in switched:  # never leave baked images on the old layout
            n.image = img
            if TAG in n:
                del n[TAG]
        raise
    done = [dict(image=name, baked=new.name, size=list(new.size), normal=jobs[name]["normal"], saved=where[name])
            for name, new in baked.items()]
    return done, skipped


def revert(context):
    """Point materials back at the original images and restore the UVs saved by Promote."""
    core.object_mode(context)
    images = 0
    for mat in bpy.data.materials:
        if not (mat.use_nodes and mat.node_tree):
            continue
        for n in mat.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.get(TAG):
                orig = bpy.data.images.get(n[TAG])
                if orig is not None:
                    n.image = orig
                    del n[TAG]
                    images += 1
    uvs = 0
    for me in {o.data for o in context.scene.objects if o.type == "MESH"}:
        for layer in list(me.uv_layers):
            if not layer.name.endswith(uv_suggest.BACKUP):
                continue
            src = layer.name[: -len(uv_suggest.BACKUP)]
            if src in me.uv_layers:
                a = np.empty(len(me.loops) * 2, np.float32)
                layer.data.foreach_get("uv", a)
                me.uv_layers[src].data.foreach_set("uv", a)
                me.update()
                uvs += 1
    return images, uvs
