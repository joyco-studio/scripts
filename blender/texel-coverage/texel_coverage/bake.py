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


def plan(context):
    """Images to bake: {image name: dict(size, users, normal)} plus skipped {image name: reason}."""
    sizes = context.scene.get("tc_suggest_sizes")
    sizes = sizes.to_dict() if hasattr(sizes, "to_dict") else {}
    jobs, skipped = {}, {}
    for img_name, users in sorted(export.material_users(context).items()):
        img = bpy.data.images.get(img_name)
        if img is None or img.get(BAKED) or img.get(export.COPY_TAG):
            continue
        has = [u for u in users if u[2] + uv_suggest.SUFFIX in u[0].data.uv_layers]
        if not has:
            continue
        if len(has) != len(users):
            others = sorted({u[0].name for u in users if u not in has})
            skipped[img_name] = f"also used by objects without a suggested layout ({', '.join(others[:3])})"
            continue
        if any(not _image_nodes(u[3], img) for u in users):
            skipped[img_name] = "used inside a node group"
            continue
        w, h = img.size
        if w == 0 or h == 0:
            skipped[img_name] = "image has no pixels on this machine"
            continue
        target = 0
        for o, slot, uv, mat, _ in users:
            main = core.tex_info(mat)
            planned = int(sizes.get(main[2], 0)) if main else 0
            if not planned or not main[0]:
                continue
            target = max(target, _pow2(max(w, h) * planned / main[0]))
        if not target:
            skipped[img_name] = "no suggested size for its material: run Suggest UVs"
            continue
        f = target / max(w, h)
        normal = any(_is_normal(u[3].node_tree, n) for u in users for n in _image_nodes(u[3], img))
        jobs[img_name] = dict(size=[max(1, round(w * f)), max(1, round(h * f))], users=users, normal=normal)
    return jobs, skipped


def _bake_one(context, img, job, margin):
    """Bake `img` into a new image on the suggested UVs. Returns the new image."""
    w, h = job["size"]
    new = bpy.data.images.new(img.name + SUFFIX, w, h, alpha=False, float_buffer=img.is_float)
    try:  # a normal bake writes raw vectors: store them as data, whatever the original was tagged
        new.colorspace_settings.name = "Non-Color" if job["normal"] else img.colorspace_settings.name
    except TypeError:
        pass
    new[BAKED] = img.name
    temp, saved_links, saved_active, saved_normal_uv = [], [], [], []
    mats = {u[3] for u in job["users"]}
    for mat in mats:
        nt = mat.node_tree
        src_nodes = _image_nodes(mat, img)
        uv = next(u[2] for u in job["users"] if u[3] == mat)
        tgt = nt.nodes.new("ShaderNodeTexImage")
        tgt.image = new
        temp.append((nt, tgt))
        saved_active.append((nt, nt.nodes.active))
        for n in nt.nodes:
            n.select = False
        tgt.select = True
        nt.nodes.active = tgt
        src = src_nodes[0]
        if not src.inputs["Vector"].is_linked:  # read the original layout explicitly
            uvn = nt.nodes.new("ShaderNodeUVMap")
            uvn.uv_map = uv
            temp.append((nt, uvn))
            nt.links.new(uvn.outputs["UV"], src.inputs["Vector"])
            saved_links.append((nt, src.inputs["Vector"]))
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None) \
            or next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
        prev = out.inputs["Surface"].links[0].from_socket if out.inputs["Surface"].is_linked else None
        saved_links.append((nt, out.inputs["Surface"], prev))
        if job["normal"]:
            nm = next(l.to_node for o in src.outputs for l in o.links if l.to_node.type == "NORMAL_MAP")
            saved_normal_uv.append((nm, nm.uv_map))
            nm.uv_map = uv  # tangent space of the layout the normal map was made for
            sh = nt.nodes.new("ShaderNodeBsdfDiffuse")
            nt.links.new(nm.outputs["Normal"], sh.inputs["Normal"])
        else:
            sh = nt.nodes.new("ShaderNodeEmission")
            nt.links.new(src.outputs["Color"], sh.inputs["Color"])
        temp.append((nt, sh))
        nt.links.new(sh.outputs[0], out.inputs["Surface"])
    objs = sorted({u[0] for u in job["users"]}, key=lambda o: o.name)
    # Cycles bakes every material on the selected objects into its active image node: give the
    # other materials a throwaway target so no original image is ever written to.
    others = {ms.material for o in objs for ms in o.material_slots
              if ms.material and ms.material.use_nodes and ms.material not in mats}
    dummy = None
    if others:
        dummy = bpy.data.images.new("TC bake scratch", 16, 16)
        for mat in others:
            nt = mat.node_tree
            d = nt.nodes.new("ShaderNodeTexImage")
            d.image = dummy
            temp.append((nt, d))
            saved_active.append((nt, nt.nodes.active))
            nt.nodes.active = d
    try:
        for o in context.view_layer.objects:
            o.select_set(o in objs)
        context.view_layer.objects.active = objs[0]
        bpy.ops.object.bake(type="NORMAL" if job["normal"] else "EMIT", margin=margin, use_clear=True,
                            normal_space="TANGENT")
    finally:
        for item in saved_links:
            if len(item) == 2:
                nt, sock = item
                for l in list(sock.links):
                    nt.links.remove(l)
            else:
                nt, sock, prev = item
                for l in list(sock.links):
                    nt.links.remove(l)
                if prev is not None:
                    nt.links.new(prev, sock)
        for nm, uv in saved_normal_uv:
            nm.uv_map = uv
        for nt, n in temp:
            nt.nodes.remove(n)
        for nt, n in saved_active:
            if n is not None:
                nt.nodes.active = n
        if dummy is not None:
            bpy.data.images.remove(dummy)
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
    bk.use_selected_to_active, bk.target = False, "IMAGE_TEXTURES"
    saved = dict(engine=scene.render.engine, samples=scene.cycles.samples,
                 adaptive=scene.cycles.use_adaptive_sampling, sel=[o for o in context.selected_objects],
                 active=context.view_layer.objects.active)
    uv_state = []  # active / render UV per mesh, so baking writes into the suggested map
    meshes = {}
    for job in jobs.values():
        for o, slot, uv, mat, _ in job["users"]:
            meshes.setdefault(o.data.name, (o.data, uv))
    for me, uv in meshes.values():
        uv_state.append((me, me.uv_layers.active.name if me.uv_layers.active else None))
        me.uv_layers.active = me.uv_layers[uv + uv_suggest.SUFFIX]
    done = []
    try:
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 8
        scene.cycles.use_adaptive_sampling = False
        margin = max(2, int(props.pack_margin_px))
        new_images = {}
        for name, job in jobs.items():
            img = bpy.data.images[name]
            new_images[name] = _bake_one(context, img, job, margin)
        for name, new in new_images.items():
            img = bpy.data.images[name]
            where = _save(img, new)
            for mat in {u[3] for u in jobs[name]["users"]}:
                for n in _image_nodes(mat, img):
                    n[TAG] = img.name
                    n.image = new
            done.append(dict(image=name, baked=new.name, size=list(new.size), normal=jobs[name]["normal"],
                             saved=where))
    finally:
        for me, act in uv_state:
            if act and act in me.uv_layers:
                me.uv_layers.active = me.uv_layers[act]
        bk.use_selected_to_active, bk.target = saved_bake
        scene.render.engine = saved["engine"]
        scene.cycles.samples, scene.cycles.use_adaptive_sampling = saved["samples"], saved["adaptive"]
        for o in context.view_layer.objects:
            o.select_set(o in saved["sel"])
        context.view_layer.objects.active = saved["active"]
    if done:
        objs = {bpy.data.objects[o.name] for job in jobs.values() for o, *_ in job["users"]}
        uv_suggest.promote(context, sorted(objs, key=lambda o: o.name))
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
