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

    # Keyframed rotation (location stays a plain value the tests compare and merge).
    cube.keyframe_insert("rotation_euler", frame=1)
    cube.rotation_euler.z = 0.5 if variant == "current" else 1.0
    cube.keyframe_insert("rotation_euler", frame=20)
    cube.rotation_euler.z = 0
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


def write_png(path, color):
    img = bpy.data.images.new("tmp_png", 8, 8)
    img.generated_color = color
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def build_session_state(path):
    """Data whose value in an open file depends on the session (current frame, Edit Mode) and images
    that read several files (UDIM tiles, a sequence). See run_tests.test_session_state."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, 50
    link = sc.collection.objects.link

    def keyed(struct, prop, a, b):
        setattr(struct, prop, a)
        struct.keyframe_insert(prop, frame=1)
        setattr(struct, prop, b)
        struct.keyframe_insert(prop, frame=50)

    me = cube_mesh("KeyedMesh")
    ob = bpy.data.objects.new("Animated", me)
    link(ob)
    ob.shape_key_add(name="Basis")
    keyed(ob.shape_key_add(name="Squash"), "value", 0.0, 1.0)
    mat = bpy.data.materials.new("KeyedMat")
    rough = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED").inputs["Roughness"]
    keyed(rough, "default_value", 0.1, 0.9)
    me.materials.append(mat)
    keyed(ob, "location", (0, 0, 0), (5, 0, 0))
    ob["wobble"] = 0.0
    ob.keyframe_insert('["wobble"]', frame=1)
    ob["wobble"] = 1.0
    ob.keyframe_insert('["wobble"]', frame=50)
    drv = bpy.data.objects.new("Driven", None)
    link(drv)
    d = drv.driver_add("scale", 0).driver
    d.type, d.expression = "SCRIPTED", "frame / 10"
    lamp = bpy.data.lights.new("KeyedLamp", "POINT")
    keyed(lamp, "energy", 10.0, 1000.0)
    link(bpy.data.objects.new("KeyedLamp", lamp))

    arm = bpy.data.armatures.new("RigData")
    rig = bpy.data.objects.new("Rig", arm)
    link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    arm.edit_bones.new("Bone").tail = (0, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    keyed(rig.pose.bones["Bone"], "location", (0, 0, 0), (0, 2, 0))

    link(bpy.data.objects.new("Plain", cube_mesh("PlainMesh")))

    tex = os.path.join(os.path.dirname(path), "textures")
    os.makedirs(tex, exist_ok=True)
    for t in (1001, 1002):
        write_png(os.path.join(tex, f"udim.{t}.png"), (1, 0, 0, 1))
    for f in (1, 2, 3):
        write_png(os.path.join(tex, f"seq.{f:04d}.png"), (0, 1, 0, 1))
    udim = bpy.data.images.load(os.path.join(tex, "udim.1001.png"))
    udim.name, udim.source = "Udim", "TILED"
    udim.tiles.new(1002)
    udim.filepath = "//textures/udim.<UDIM>.png"
    seq = bpy.data.images.load(os.path.join(tex, "seq.0001.png"))
    seq.name, seq.source = "Seq", "SEQUENCE"
    seq.filepath = "//textures/seq.0001.png"
    udim.use_fake_user = seq.use_fake_user = True

    sc.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=path)


def build_animation_review(variant, path):
    """Packed images, partly keyframed values and drivers outside objects (run_tests.test_animation_review)."""
    inc = variant == "incoming"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    tex = os.path.join(os.path.dirname(path), "textures")
    os.makedirs(tex, exist_ok=True)
    for name, color in (("Packed", (0, 0, 1, 1) if inc else (1, 0, 0, 1)), ("SamePacked", (0, 1, 0, 1))):
        p = os.path.join(tex, f"{name}.png")
        write_png(p, color)
        img = bpy.data.images.load(p)
        img.name = name
        img.pack()
        img.use_fake_user = True

    part = bpy.data.objects.new("PartKeyed", None)  # only location.x keyframed; y differs
    sc.collection.objects.link(part)
    part.keyframe_insert("location", index=0, frame=1)
    part.location = (3, 2 if inc else 1, 0)
    part.keyframe_insert("location", index=0, frame=10)

    lamp = bpy.data.lights.new("DrivenLamp", "POINT")
    sc.collection.objects.link(bpy.data.objects.new("DrivenLamp", lamp))
    lamp.driver_add("energy").driver.expression = "20" if inc else "10"

    muted = bpy.data.lights.new("MutedLamp", "POINT")  # same driver, muted in the incoming file
    sc.collection.objects.link(bpy.data.objects.new("MutedLamp", muted))
    fc = muted.driver_add("energy")
    fc.driver.expression = "10"
    fc.mute = inc
    muted.energy = 10

    mat = bpy.data.materials.new("DrivenMat")
    mat.use_fake_user = True
    rough = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED").inputs["Roughness"]
    d = rough.driver_add("default_value").driver
    d.type = "SCRIPTED"
    v = d.variables.new()
    v.targets[0].id = part
    v.targets[0].data_path = "location.y" if inc else "location.z"
    d.expression = v.name
    bpy.ops.wm.save_as_mainfile(filepath=path)


def set_gn_input(mod, name, value):
    """Set a Geometry Nodes modifier input by its name (5.3: properties.inputs; older: ID properties)."""
    from compare_merge import snapshot
    ident = next(i for label, i in snapshot.gn_inputs(mod) if label == name)
    st = snapshot.gn_input(mod, ident)
    if st is not None:
        st.value = value
    else:
        mod[ident] = value


def build_robustness(variant, path, shared_texture):
    """Cases found stress-testing a production file (run_tests.test_robustness): Geometry Nodes inputs
    and node settings, modifier toggles, UI-only modifier state, a shared texture reached by another
    relative path, and renames that every user of the renamed data-block refers to."""
    inc = variant == "incoming"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    link = sc.collection.objects.link

    smooth = bpy.data.node_groups.new("Smooth", "GeometryNodeTree")
    smooth.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    smooth.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    smooth.interface.new_socket("Angle", in_out="INPUT", socket_type="NodeSocketFloat")
    gi, go = smooth.nodes.new("NodeGroupInput"), smooth.nodes.new("NodeGroupOutput")
    smooth.links.new(gi.outputs[0], go.inputs[0])
    b = smooth.nodes.new("FunctionNodeBooleanMath")
    b.name = "Bool"
    b.operation = "OR" if inc else "AND"

    smoothed = bpy.data.objects.new("Smoothed", cube_mesh("SmoothedMesh"))
    link(smoothed)
    mod = smoothed.modifiers.new("Smooth", "NODES")
    mod.node_group = smooth
    set_gn_input(mod, "Angle", 1.2 if inc else 0.5)

    panel = bpy.data.objects.new("PanelOnly", cube_mesh("PanelMesh"))
    link(panel)
    pm = panel.modifiers.new("Smooth", "NODES")
    pm.node_group = smooth
    for p in ("open_manage_panel", "open_warnings_panel"):  # a panel opened in the modifier UI
        if hasattr(pm, p):
            setattr(pm, p, inc)

    beveled = bpy.data.objects.new("Beveled", cube_mesh("BeveledMesh"))
    link(beveled)
    bevel = beveled.modifiers.new("Bevel", "BEVEL")
    bevel.show_render = bevel.show_viewport = not inc
    smoothed.location.z = beveled.location.z = 1 if inc else 0  # a second unit: the picks stay Mixed

    folder = os.path.dirname(path)
    img = bpy.data.images.load(shared_texture)
    img.name = "Shared"
    img.filepath = "//" + os.path.relpath(shared_texture, folder)  # ../shared or ../../shared
    img.use_fake_user = True

    glass = bpy.data.materials.new("Glass")
    glass.diffuse_color = (0.2, 0.4, 1, 0.3)  # unlike ChainMat: identical data-blocks can't be paired
    for i in range(3):
        me = cube_mesh(f"Pane{i}Mesh", 0.5)
        me.materials.append(glass)
        link(bpy.data.objects.new(f"Pane{i}", me))
    chain_mat = bpy.data.materials.new("ChainMat")
    chain_me = cube_mesh("ChainMesh")
    chain_me.materials.append(chain_mat)
    link(bpy.data.objects.new("Chain", chain_me))
    crates = bpy.data.collections.new("Crates")
    sc.collection.children.link(crates)
    crate = bpy.data.objects.new("Crate", None)
    crates.objects.link(crate)
    # Six nested node groups (a material uses the outermost): each level only matches its renamed
    # version once the level inside it is mapped, so the renames take a round per level.
    child = None
    for i in reversed(range(6)):
        ng = bpy.data.node_groups.new(f"Nest{i}", "ShaderNodeTree")
        ng.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
        node = ng.nodes.new("ShaderNodeGroup") if child else ng.nodes.new("ShaderNodeRGB")
        if child:
            node.node_tree = child
        else:
            node.outputs[0].default_value = (0.1, 0.7, 0.3, 1)
        ng.links.new(node.outputs[0], ng.nodes.new("NodeGroupOutput").inputs[0])
        child = ng
    nested = bpy.data.materials.new("Nested")
    nested.diffuse_color = (0.5, 0.5, 0.1, 1)
    nested.node_tree.nodes.new("ShaderNodeGroup").node_tree = child
    nested.use_fake_user = True
    if inc:  # renamed, nothing else
        for i in range(6):
            bpy.data.node_groups[f"Nest{i}"].name = f"Nest{i}b"
        glass.name = "Glass2"
        chain_mat.name, chain_me.name = "ChainMat2", "ChainMesh2"
        crate.name = "Crate2"

    os.makedirs(folder, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path)
