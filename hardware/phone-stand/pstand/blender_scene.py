"""Blender 4.0 scene for the stand stills (run by pstand/render.py):
    blender -b --factory-startup --python blender_scene.py -- job.json
Imports a posed stand GLB per job (mm -> m), optionally the 3018 twin model (mirrored like the twin), a bench or a
studio floor, area lights, a camera; renders with Cycles (CUDA if present, else CPU) and OIDN.
"""
import json
import math
import sys

import bpy
from mathutils import Vector

job = json.load(open(sys.argv[sys.argv.index("--") + 1]))


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras):
        for b in list(coll):
            if b.users == 0:
                coll.remove(b)


def import_glb(path, scale=(0.001, 0.001, 0.001), loc=(0, 0, 0), name="root"):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    for o in new:
        if o.parent is None:
            o.parent = root
    root.scale = scale
    root.location = loc
    return root, new


def mat(name, color, rough=0.5, metal=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1.0)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    return m


def floor(bench):
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, 0))
    p = bpy.context.object
    if bench:
        p.scale = (1.8, 0.9, 1)
        p.location = (0, -0.245, 0)            # twin bench: centre three Z = +20 -> S y = 245
        p.data.materials.append(mat("bench_top", (0.86, 0.86, 0.84), 0.45))
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -0.245, -0.0151))
        e = bpy.context.object
        e.scale = (1.8, 0.9, 0.03)
        e.data.materials.append(mat("bench_edge", (0.6, 0.62, 0.64), 0.5))
        bpy.ops.mesh.primitive_plane_add(size=1, location=(0, -0.785, 0.9))
        wall = bpy.context.object
        wall.scale = (6, 2, 1)
        wall.rotation_euler = (math.radians(90), 0, 0)
        wall.data.materials.append(mat("wall", (0.82, 0.81, 0.78), 0.9))
        bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, -0.9))
        fl = bpy.context.object
        fl.scale = (8, 8, 1)
        fl.data.materials.append(mat("lab_floor", (0.55, 0.54, 0.52), 0.8))
    else:
        p.scale = (8, 8, 1)
        p.data.materials.append(mat("studio_floor", (0.62, 0.63, 0.64), 0.6))


def lights(bench):
    def area(name, loc, target, size, power, color=(1, 1, 1)):
        d = bpy.data.lights.new(name, "AREA")
        d.size = size
        d.energy = power
        d.color = color
        o = bpy.data.objects.new(name, d)
        bpy.context.scene.collection.objects.link(o)
        o.location = loc
        o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    c = (0, 0, 0.3)
    k = 0.11
    area("key", (-1.1, -1.3, 1.5), c, 1.2, k * (900 if bench else 650), (1.0, 0.96, 0.9))
    area("fill", (1.4, -1.0, 0.7), c, 1.6, k * (300 if bench else 240), (0.9, 0.95, 1.0))
    area("rim", (0.4, 1.4, 1.2), c, 1.0, k * (420 if bench else 380))
    area("top", (0, -0.2, 2.4), c, 2.0, k * (500 if bench else 300))
    w = bpy.context.scene.world or bpy.data.worlds.new("w")
    bpy.context.scene.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.78, 0.8, 0.83, 1) if bench else (0.55, 0.57, 0.6, 1)
    bg.inputs[1].default_value = 0.35 if bench else 0.25


def camera(eye, target, lens):
    d = bpy.data.cameras.new("cam")
    d.lens = lens
    d.clip_start = 0.01
    d.clip_end = 50
    o = bpy.data.objects.new("cam", d)
    bpy.context.scene.collection.objects.link(o)
    o.location = Vector(eye) * 0.001
    o.rotation_euler = (Vector(target) * 0.001 - o.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = o


def setup(samples, res):
    s = bpy.context.scene
    s.render.engine = "CYCLES"
    try:
        if job.get("cpu"):
            raise RuntimeError("cpu requested")
        pr = bpy.context.preferences.addons["cycles"].preferences
        pr.compute_device_type = "CUDA"
        pr.get_devices()
        for dv in pr.devices:
            dv.use = True
        s.cycles.device = "GPU"
    except Exception as e:  # noqa: BLE001
        print("GPU unavailable, CPU render:", e)
    if s.cycles.device != "GPU":
        s.render.threads_mode = "AUTO"
    s.cycles.samples = samples
    s.cycles.use_adaptive_sampling = True
    s.cycles.adaptive_threshold = 0.02
    s.cycles.use_denoising = bpy.app.build_options.openimagedenoise if hasattr(bpy.app.build_options,
                                                                              "openimagedenoise") else False
    s.cycles.filter_width = 1.2
    s.render.resolution_x, s.render.resolution_y = res
    s.render.resolution_percentage = 100
    s.view_settings.view_transform = "AgX"
    try:
        s.view_settings.look = "AgX - Medium High Contrast"
    except Exception:  # noqa: BLE001
        pass
    s.render.image_settings.file_format = "PNG"


for j in job["jobs"]:
    clear()
    import_glb(j["glb"], name="stand")
    if j["bench"] and job.get("cnc_glb"):
        import_glb(job["cnc_glb"], scale=(-0.001, 0.001, 0.001), loc=(0, job["stand_z"] * 0.001, job["bed_h"] * 0.001),
                   name="cnc")
    floor(j["bench"])
    lights(j["bench"])
    camera(j["eye"], j["target"], j["lens"])
    setup(j["samples"], j["res"])
    bpy.context.scene.render.filepath = j["out"]
    bpy.ops.render.render(write_still=True)
    print("wrote", j["out"])
