"""Blender fixture builder: emit a small metallic=1 colored-cube GLB.

Redistributable test fixture generator (run under `blender -b -P`). Produces the
img2mesh failure case the bake worker must survive: a Principled BSDF with
metallic=1.0 and a non-black base color — which bakes pure-black DIFFUSE unless the
worker neutralizes metallic. Usage:

    blender -b -P build_metallic_cube.py -- <out.glb>
"""
import sys

import bpy

out = sys.argv[sys.argv.index("--") + 1:][0]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=1.0)
cube = bpy.context.active_object

mat = bpy.data.materials.new("metallic_red")
mat.use_nodes = True
bsdf = mat.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.8, 0.1, 0.1, 1.0)  # clearly non-black
bsdf.inputs["Metallic"].default_value = 1.0                     # the failure trigger
cube.data.materials.append(mat)

bpy.ops.object.select_all(action="DESELECT")
cube.select_set(True)
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True)
print(f"[fixture] wrote {out}", flush=True)
