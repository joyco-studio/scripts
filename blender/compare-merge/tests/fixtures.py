"""Builds two versions of a small scene for the tests (run inside Blender)."""

import os

import bmesh
import bpy


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
