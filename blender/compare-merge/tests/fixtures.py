"""Builds two versions of a small scene for the tests (run inside Blender)."""

import os

import bmesh
import bpy
from mathutils import Matrix


def cube_mesh(name, size=2.0):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bm.to_mesh(me)
    bm.free()
    return me


def build(variant, path):
    """variant 'current' (the final file) or 'incoming' (an artist's version of it)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    props = bpy.data.collections.new("Props")
    sc.collection.children.link(props)

    me = cube_mesh("CubeMesh")
    wood = bpy.data.materials.new("Wood")
    bsdf = next(n for n in wood.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = 0.5 if variant == "current" else 0.2
    me.materials.append(wood)
    cube = bpy.data.objects.new("Cube", me)
    props.objects.link(cube)
    child = bpy.data.objects.new("Child", None)
    props.objects.link(child)
    child.parent = cube
    child.matrix_parent_inverse = Matrix.Translation((0, 3, 0))  # parented with an offset (Ctrl+P)
    child.location = (0, 0, 2)

    shared = cube_mesh("SharedMesh", 0.5)
    for i in range(2):
        o = bpy.data.objects.new(f"Rock{i}", shared)
        o.location = (-3, i * 2, 0)
        props.objects.link(o)

    cam_data = bpy.data.cameras.new("Cam")
    cam_data.lens = 50 if variant == "current" else 35
    cam = bpy.data.objects.new("Camera", cam_data)
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.location = (0, -10, 2)
    cam.rotation_euler = (1.45, 0, 0)

    img = bpy.data.images.new("Paint", 8, 8)
    img.generated_color = (1, 0, 0, 1) if variant == "current" else (0, 0, 1, 1)
    tex_dir = os.path.join(os.path.dirname(path), "textures")
    os.makedirs(tex_dir, exist_ok=True)
    img.filepath_raw = os.path.join(tex_dir, "paint.png")
    img.file_format = "PNG"
    img.save()
    img.source = "FILE"
    img.filepath = "//textures/paint.png"
    paint = bpy.data.materials.new("Paint")
    tex = paint.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = img
    pb = next(n for n in paint.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    paint.node_tree.links.new(tex.outputs["Color"], pb.inputs["Base Color"])
    shared.materials.append(paint)

    if variant == "incoming":
        cube.location = (1, 0, 0)
        me.vertices[0].co.x += 0.5
        cone = bpy.data.meshes.new("ConeMesh")
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=1, radius2=0, depth=2)
        bm.to_mesh(cone)
        bm.free()
        cone.materials.append(wood)
        c = bpy.data.objects.new("Cone", cone)
        c.location = (3, 0, 0)
        props.objects.link(c)
        tip = bpy.data.objects.new("ConeTip", None)
        tip.parent = c
        props.objects.link(tip)
        sc.frame_end = 120
        bpy.data.objects["Rock1"].name = "Boulder"
    else:
        only = bpy.data.objects.new("OnlyCurrent", None)
        sc.collection.objects.link(only)
        sc.frame_end = 250

    cube.keyframe_insert("location", frame=1)
    cube.location.z += 1
    cube.keyframe_insert("location", frame=20)
    cube.location.z -= 1
    bpy.ops.wm.save_as_mainfile(filepath=path)


def build_review(variant, path):
    """One pair of files with a case for every code-review finding (see run_tests.test_review)."""
    inc = variant == "incoming"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    link = sc.collection.objects.link

    # A user collection that happens to share the preview's collection name.
    user = bpy.data.collections.new("Compare Preview")
    sc.collection.children.link(user)
    user.objects.link(bpy.data.objects.new("UserThing", None))

    light = bpy.data.lights.new("Lamp", "POINT")
    light.color = (1, 0.5, 0.2) if inc else (1, 1, 1)
    link(bpy.data.objects.new("LampObj", light))

    skin = cube_mesh("SkinMesh")
    skin_ob = bpy.data.objects.new("SkinObj", skin)
    link(skin_ob)
    g = skin_ob.vertex_groups.new(name="G")
    g.add([1 if inc else 0], 1.0, "REPLACE")

    cu = bpy.data.curves.new("Path", "CURVE")
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(1)
    sp.bezier_points[1].co = (2, 0, 0)
    sp.bezier_points[0].handle_left = (-1, 1 if inc else 0, 0)
    link(bpy.data.objects.new("PathObj", cu))

    wig = bpy.data.objects.new("Wiggler", None)
    link(wig)
    wig.keyframe_insert("location", frame=1, index=0)
    wig.location.x = 1
    wig.keyframe_insert("location", frame=10, index=0)
    from compare_merge.snapshot import iter_fcurves
    fc = next(fc for _slot, fc in iter_fcurves(wig.animation_data.action))
    fc.modifiers.new("NOISE").strength = 2.0 if inc else 1.0

    mathy = bpy.data.materials.new("Mathy")
    mathy.use_fake_user = True
    nt = mathy.node_tree
    a, b = nt.nodes.new("ShaderNodeValue"), nt.nodes.new("ShaderNodeValue")
    a.name, b.name = "A", "B"
    a.outputs[0].default_value, b.outputs[0].default_value = 1, 2
    m = nt.nodes.new("ShaderNodeMath")
    m.name, m.operation = "Sub", "SUBTRACT"
    first, second = (b, a) if inc else (a, b)
    nt.links.new(first.outputs[0], m.inputs[0])
    nt.links.new(second.outputs[0], m.inputs[1])

    tint = bpy.data.node_groups.new("Tint", "ShaderNodeTree")
    tint.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    out = tint.nodes.new("NodeGroupOutput")
    rgb = tint.nodes.new("ShaderNodeRGB")
    rgb.outputs[0].default_value = (0, 0, 1, 1) if inc else (1, 0, 0, 1)
    tint.links.new(rgb.outputs[0], out.inputs[0])
    tinted = bpy.data.materials.new("Tinted")
    grp = tinted.node_tree.nodes.new("ShaderNodeGroup")
    grp.name = "Group"
    grp.node_tree = tint
    bsdf = next(n for n in tinted.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    tinted.node_tree.links.new(grp.outputs[0], bsdf.inputs["Base Color"])
    ball = cube_mesh("BallMesh")
    ball.materials.append(tinted)
    link(bpy.data.objects.new("TintBall", ball))

    cam1 = bpy.data.objects.new("Cam1", bpy.data.cameras.new("Cam1Data"))
    link(cam1)
    sc.camera = cam1
    sc.world = bpy.data.worlds.new("WorldA")
    sc.render.resolution_percentage = 50 if inc else 100
    sc.unit_settings.scale_length = 0.01 if inc else 1.0
    if inc:
        cam2 = bpy.data.objects.new("Cam2", bpy.data.cameras.new("Cam2Data"))
        link(cam2)
        sc.camera = cam2
        sc.world = bpy.data.worlds.new("Sky")

        parent = bpy.data.objects.new("NewParent", None)
        parent.location = (5, 0, 0)
        link(parent)
        child = bpy.data.objects.new("NewChild", None)
        child.parent = parent
        child.location = (0, 0, 1)
        link(child)

        outer = bpy.data.collections.new("Outer")
        sc.collection.children.link(outer)
        inner = bpy.data.collections.new("Inner")
        outer.children.link(inner)
        inner.hide_render = True
        inner.objects.link(bpy.data.objects.new("Nested", None))

        # A new bone-parented child of a new armature.
        rig = bpy.data.objects.new("NewRig", bpy.data.armatures.new("NewRigData"))
        rig.location = (0, 5, 0)
        link(rig)
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="EDIT")
        bone = rig.data.edit_bones.new("Bone")
        bone.head, bone.tail = (0, 0, 0), (0, 0, 2)
        bpy.ops.object.mode_set(mode="OBJECT")
        kid = bpy.data.objects.new("BoneKid", None)
        link(kid)
        kid.parent, kid.parent_type, kid.parent_bone = rig, "BONE", "Bone"

        # A new parent whose new child needs another new object (a Boolean cutter).
        holder = bpy.data.objects.new("Holder", None)
        link(holder)
        drilled = bpy.data.objects.new("Drilled", cube_mesh("DrilledMesh"))
        link(drilled)
        drilled.parent = holder
        cutter = bpy.data.objects.new("Cutter", cube_mesh("CutterMesh", 0.5))
        link(cutter)
        drilled.modifiers.new("Hole", "BOOLEAN").object = cutter
    bpy.context.view_layer.update()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path)


def build_units(variant, path):
    """Field-level picks (run_tests.test_units): each data-block differs in two independent ways."""
    inc = variant == "incoming"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    link = sc.collection.objects.link

    # Same topology: current moved the vertices, incoming changed the UVs and added an attribute.
    split = bpy.data.meshes.new("SplitMesh")
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    bmesh.ops.create_grid(bm, x_segments=3, y_segments=3, size=1.0, calc_uvs=True)
    bm.to_mesh(split)
    bm.free()
    split_ob = bpy.data.objects.new("SplitObj", split)
    link(split_ob)
    if not inc:
        for v in split.vertices:
            v.co.z += 0.25 * v.index
    else:
        uv = split.uv_layers["UVMap"].data
        for d in uv:
            d.uv = (d.uv[0] * 0.5, d.uv[1] * 0.5 + 0.25)
        w = split.attributes.new("Weight", "FLOAT", "POINT")
        w.data.foreach_set("value", [0.1 * i for i in range(len(split.vertices))])
    split_ob.shape_key_add(name="Basis", from_mix=False)
    k1 = split_ob.shape_key_add(name="Key 1", from_mix=False)
    k1.data[0].co.z += 1.0 if inc else 2.0
    if inc:  # a new key, listed after Key 1 in the diff, that Key 1 is now relative to
        a = split_ob.shape_key_add(name="A", from_mix=False)
        a.data[1].co.z += 1.0
        k1.relative_key = a

    # Different topology: incoming is subdivided once more.
    sub = bpy.data.meshes.new("SubdivMesh")
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    bmesh.ops.create_grid(bm, x_segments=4 if inc else 2, y_segments=4 if inc else 2, size=1.0, calc_uvs=True)
    bm.to_mesh(sub)
    bm.free()
    link(bpy.data.objects.new("SubdivObj", sub))

    # An object that moved on one side and got another material in slot 1 on the other.
    mat_a, mat_b = bpy.data.materials.new("MatA"), bpy.data.materials.new("MatB")
    mat_b.use_fake_user = True
    mover_me = cube_mesh("MoverMesh")
    mover_me.materials.append(mat_a)
    mover = bpy.data.objects.new("Mover", mover_me)
    link(mover)
    mover.location = (0, 0, 3) if inc else (0, 0, 0)
    mover.material_slots[0].link = "OBJECT"
    mover.material_slots[0].material = mat_b if inc else mat_a

    # A material with two independent input changes.
    painted = bpy.data.materials.new("Painted")
    painted.use_fake_user = True
    bsdf = next(n for n in painted.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = 0.9 if inc else 0.3
    bsdf.inputs["Base Color"].default_value = (0, 0, 1, 1) if inc else (1, 0, 0, 1)
    math = painted.node_tree.nodes.new("ShaderNodeMath")  # a third input only with MULTIPLY_ADD
    math.name = "Math"
    math.operation = "MULTIPLY_ADD" if inc else "ADD"
    painted.node_tree.links.new(math.outputs[0], bsdf.inputs["Metallic"])

    # A light with two property changes.
    bulb = bpy.data.lights.new("Bulb", "POINT")
    bulb.energy = 500.0 if inc else 100.0
    bulb.color = (1, 0.5, 0.2) if inc else (1, 1, 1)
    link(bpy.data.objects.new("BulbObj", bulb))

    # Re-parented with an offset (Ctrl+P) on one side, moved on the other.
    hook, hook2 = bpy.data.objects.new("Hook", None), bpy.data.objects.new("Hook2", None)
    hook.location, hook2.location = (10, 0, 0), (0, 10, 0)
    link(hook)
    link(hook2)
    hanger = bpy.data.objects.new("Hanger", None)
    link(hanger)
    hanger.parent = hook2 if inc else hook
    hanger.matrix_parent_inverse = Matrix.Translation((0, 0, 5) if inc else (0, 0, 0))
    hanger.location = (0, 0, 0) if inc else (1, 1, 1)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path)
