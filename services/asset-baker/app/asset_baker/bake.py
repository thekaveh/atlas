#!/usr/bin/env python3
"""bake.py — headless Blender HP→LP bake worker stage (Atlas asset-baker, #407).

Ported from DayDreams' battle-tested ``spikes/one-cell/bake_lp.py`` (itself derived
from mdj128/aeon-unity-tools' hp_to_lp_bake.py, then hardened over 9 assets). Turns
messy AI-generated high-poly meshes into clean game/web-ready low-poly assets with a
baked base-color + tangent normal map. Runs fully headless under ``blender -b -P``.
Cycles bakes on **CPU** by default: deterministic, runs anywhere (CI/Linux prod), and
GPU-contention-safe.

Pipeline per source (``--mode bake``, the proven algorithm, parameterized):
  1. import GLB → join all meshes → normalize to canonical max-dim → rest base on z=0
  2. voxel-remesh (fuses interpenetrating shells/flaps that cause tilt/floaters)
  3. drop tiny debris shells (< MIN_SHELL_FACES)
  4. decimate to target_tris (skipped if already under)
  5. uniform smooth shading (hard normals corrupt cage baking)
  6. Smart UV Project + pack (seam-based unwrap FAILS on remeshed surfaces)
  7. two-pass selected-to-active bake (color + tangent normal), numpy-composite misses
  8. mean-brightness QA gate, then export <name>_LP.glb + BaseColor.png + Normal.png

``--mode skip`` is the **foliage bypass**: thin leaf-shell meshes fragment into shards
under remesh+decimate, so foliage is normalized and re-exported WITHOUT remesh/bake.

Writes a machine-readable JSON summary (``--summary PATH``) of per-source records so the
Atlas worker can enforce the brightness gate and content-address the outputs. Exit 0 iff
every source succeeded; per-source errors are isolated.
"""
import bpy, time, math, json, os, sys, glob, traceback
import numpy as np

# ── fixed heuristics (tuned on real assets; not per-request) ────────────────
VOXEL_REL = 0.0015
MIN_SHELL_FACES = 500
BAKE_SAMPLES = 4
SMOOTH_ANGLE_UV = 66
EXT_TIGHT, RAY_TIGHT = 0.013, 0.029   # fractions of max dim
EXT_FAR, RAY_FAR = 0.020, 0.092

# ── per-request params (overridable via CLI) ────────────────────────────────
TARGET_TRIS = 39000
TEX_COLOR = 2048
TEX_NORMAL = 2048
CANONICAL = 4.0
BRIGHTNESS_MIN = 0.05
MODE = "bake"
TRY_GPU = False
OUTDIR = None
SUMMARY = None


def log(m): print(f"[asset-baker] {m}", flush=True)
def err(m): print(f"[asset-baker] ERROR: {m}", file=sys.stderr, flush=True)


class BlackBake(Exception):
    """The color bake came out near-black — refuse to emit (QA gate)."""


def parse_args():
    global TARGET_TRIS, TEX_COLOR, TEX_NORMAL, CANONICAL, BRIGHTNESS_MIN
    global MODE, TRY_GPU, OUTDIR, SUMMARY
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    inputs, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--target" and i + 1 < len(argv): TARGET_TRIS = int(argv[i+1]); i += 2
        elif a == "--tex" and i + 1 < len(argv): TEX_COLOR = TEX_NORMAL = int(argv[i+1]); i += 2
        elif a == "--canonical" and i + 1 < len(argv): CANONICAL = float(argv[i+1]); i += 2
        elif a == "--brightness-min" and i + 1 < len(argv): BRIGHTNESS_MIN = float(argv[i+1]); i += 2
        elif a == "--mode" and i + 1 < len(argv): MODE = argv[i+1]; i += 2
        elif a == "--outdir" and i + 1 < len(argv): OUTDIR = argv[i+1]; i += 2
        elif a == "--summary" and i + 1 < len(argv): SUMMARY = argv[i+1]; i += 2
        elif a == "--gpu": TRY_GPU = True; i += 1
        elif a.startswith("--"): err(f"unknown flag {a}"); i += 1
        else: inputs += glob.glob(a); i += 1
    return inputs


def enable_cycles(scene):
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = BAKE_SAMPLES
    if TRY_GPU:
        try:
            prefs = bpy.context.preferences.addons['cycles'].preferences
            for backend in ('OPTIX', 'CUDA', 'HIP', 'ONEAPI', 'METAL'):
                try:
                    prefs.compute_device_type = backend
                    prefs.get_devices()
                    if any(d.type != 'CPU' for d in prefs.devices):
                        for d in prefs.devices: d.use = True
                        scene.cycles.device = 'GPU'; log(f"GPU bake via {backend}"); return
                except Exception: continue
        except Exception: pass
    scene.cycles.device = 'CPU'; log("baking on CPU (deterministic, GPU-safe)")


def solo(*objs, active=None):
    bpy.ops.object.select_all(action='DESELECT')
    for o in objs: o.select_set(True)
    bpy.context.view_layer.objects.active = active or objs[0]


def new_img(name, size, cs):
    old = bpy.data.images.get(name)
    if old: bpy.data.images.remove(old)
    img = bpy.data.images.new(name, size, size, alpha=False)
    img.colorspace_settings.name = cs
    return img


def make_mat(base, col, nrm):
    m = bpy.data.materials.new(base + "_LP_Mat"); m.use_nodes = True
    nt = m.node_tree; bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs['Roughness'].default_value = 0.9
    tc = nt.nodes.new('ShaderNodeTexImage'); tc.image = col; tc.location = (-500, 300)
    nt.links.new(tc.outputs['Color'], bsdf.inputs['Base Color'])
    tn = nt.nodes.new('ShaderNodeTexImage'); tn.image = nrm; tn.location = (-700, -100)
    nm = nt.nodes.new('ShaderNodeNormalMap'); nm.location = (-350, -100)
    nt.links.new(tn.outputs['Color'], nm.inputs['Color'])
    nt.links.new(nm.outputs['Normal'], bsdf.inputs['Normal'])
    return m, tc, tn


def bake_into(nt, node, btype, ext, ray):
    for n in nt.nodes: n.select = False
    node.select = True; nt.nodes.active = node   # shader nodes use .select (bool)
    kw = dict(use_selected_to_active=True, cage_extrusion=ext, max_ray_distance=ray, use_clear=True)
    if btype == 'DIFFUSE': bpy.ops.object.bake(type='DIFFUSE', pass_filter={'COLOR'}, **kw)
    else: bpy.ops.object.bake(type='NORMAL', normal_space='TANGENT', **kw)


def composite(main, fb, is_normal):
    w, h = main.size
    a = np.empty(w*h*4, dtype=np.float32); b = np.empty(w*h*4, dtype=np.float32)
    main.pixels.foreach_get(a); fb.pixels.foreach_get(b)
    a4, b4 = a.reshape(-1, 4), b.reshape(-1, 4)
    if is_normal:
        miss = a4[:, 2] < 0.1; a4[miss] = b4[miss]
        still = miss & (b4[:, 2] < 0.1); a4[still] = [0.5, 0.5, 1.0, 1.0]
    else:
        miss = np.all(a4[:, :3] < 0.004, axis=1); a4[miss] = b4[miss]
    main.pixels.foreach_set(a)
    return int(miss.sum())


def neutralize_metallic(obj):
    """Force metallic=0 on all of obj's materials (disconnect metallic textures too).

    glTF img2mesh sources ship metallic=1.0; a fully-metallic Principled BSDF has NO
    diffuse component, so the DIFFUSE/COLOR bake returns pure black. Only the in-memory
    copy is touched; the source GLB on disk is unchanged.
    """
    for slot in obj.material_slots:
        m = slot.material
        if not m or not m.use_nodes:
            continue
        for n in m.node_tree.nodes:
            if n.type == 'BSDF_PRINCIPLED':
                met = n.inputs['Metallic']
                for link in list(met.links):
                    m.node_tree.links.remove(link)
                met.default_value = 0.0


def mean_brightness(img):
    w, h = img.size
    a = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    return float(a.reshape(-1, 4)[:, :3].mean())


def import_glb(path):
    """Import a plain GLB, join meshes, normalize to canonical max-dim, rest on z=0."""
    before = set(bpy.data.objects)
    try:
        bpy.ops.import_scene.gltf(filepath=path)
    except Exception as e:
        err(f"import failed for {path}: {e}"); return None
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == 'MESH']
    for o in new:
        if o.type != 'MESH': bpy.data.objects.remove(o, do_unlink=True)
    if not meshes: err(f"no meshes in {path}"); return None
    solo(*meshes, active=meshes[0])                  # glTF import may leave active None
    if len(meshes) > 1: bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active or meshes[0]
    obj.name = os.path.splitext(os.path.basename(path))[0].replace('.plain', '')
    bpy.context.view_layer.update()
    md = max(obj.dimensions) or 1.0
    s = CANONICAL / md
    obj.scale = (s, s, s); bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
    import mathutils
    obj.location.z -= min((obj.matrix_world @ mathutils.Vector(c)).z for c in obj.bound_box)
    return obj


def _export_glb(obj, glb):
    solo(obj)
    bpy.ops.export_scene.gltf(filepath=glb, export_format='GLB', use_selection=True,
                              export_apply=True, export_yup=True)
    assert os.path.exists(glb) and os.path.getsize(glb) > 0, f"GLB not written: {glb}"


def passthrough(src, outdir):
    """Foliage bypass: normalize + export, no remesh/decimate/bake."""
    t0 = time.time(); base = src.name[:40]
    tris = sum(len(p.vertices) - 2 for p in src.data.polygons)
    os.makedirs(outdir, exist_ok=True)
    glb = os.path.join(outdir, f"{base}_LP.glb")
    _export_glb(src, glb)
    log(f"  foliage bypass → {glb} ({os.path.getsize(glb)//1024} KB)")
    return {
        "glb": glb, "mode": "skip", "faces_in": len(src.data.polygons),
        "tris_out": tris, "shells_kept": 1, "color_mean": None,
        "duration_s": round(time.time() - t0, 2),
    }


def process(src, outdir):
    t0 = time.time(); scene = bpy.context.scene
    base = src.name[:40]
    max_dim = max(src.dimensions) or 1.0
    faces_in = len(src.data.polygons)
    log(f"=== {src.name}: {faces_in} faces, max_dim {max_dim:.2f} ===")

    # 1. copy + voxel remesh
    lp = src.copy(); lp.data = src.data.copy(); lp.name = base + "_LP"; lp.data.name = base + "_LP"
    lp.hide_render = False; lp.hide_set(False)
    scene.collection.objects.link(lp); solo(lp)
    mod = lp.modifiers.new("Remesh", 'REMESH'); mod.mode = 'VOXEL'
    mod.voxel_size = max(0.0008, VOXEL_REL * max_dim)
    bpy.ops.object.modifier_apply(modifier=mod.name)
    log(f"  remeshed → {len(lp.data.polygons)} faces")

    # 2. drop debris shells
    bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.separate(type='LOOSE'); bpy.ops.object.mode_set(mode='OBJECT')
    shells = [o for o in scene.objects if o.name.startswith(lp.name)]
    keep = [o for o in shells if len(o.data.polygons) >= MIN_SHELL_FACES]
    if not keep:
        # Everything remeshed to sub-MIN_SHELL_FACES debris — fail with a clear
        # message rather than an opaque `max() arg is an empty sequence`.
        for o in shells:
            bpy.data.objects.remove(o, do_unlink=True)
        raise RuntimeError(
            f"all remeshed shells were debris (< {MIN_SHELL_FACES} faces) — "
            f"nothing to bake (try mode=skip for thin/foliage meshes)"
        )
    for o in shells:
        if o not in keep: bpy.data.objects.remove(o, do_unlink=True)
    lp = bpy.data.objects[base + "_LP"] if (base + "_LP") in bpy.data.objects else max(keep, key=lambda o: len(o.data.polygons))
    if len(keep) > 1: solo(*keep, active=lp); bpy.ops.object.join()
    shells_kept = len(keep)
    log(f"  kept {shells_kept} shell(s), {len(lp.data.polygons)} faces")

    # 3. decimate to budget (skip if already small)
    tris = sum(len(p.vertices) - 2 for p in lp.data.polygons)
    if tris > TARGET_TRIS:
        solo(lp); mod = lp.modifiers.new("Decimate", 'DECIMATE'); mod.ratio = TARGET_TRIS / tris
        bpy.ops.object.modifier_apply(modifier=mod.name)
    tris_out = sum(len(p.vertices) - 2 for p in lp.data.polygons)
    log(f"  decimated → {tris_out} tris")

    # 4. smooth shading
    for p in lp.data.polygons: p.use_smooth = True
    for e in lp.data.edges: e.use_edge_sharp = False; e.use_seam = False

    # 5. fresh UVs (Smart-UV by angle — seam-based unwrap fails on remeshed surfaces)
    solo(lp); bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(SMOOTH_ANGLE_UV), island_margin=0.0003)
    bpy.ops.uv.select_all(action='SELECT'); bpy.ops.uv.pack_islands(rotate=True, margin=0.001)
    bpy.ops.object.mode_set(mode='OBJECT')

    # 6. bake color + normal (tight + fallback composite)
    col = new_img(base + "_BaseColor", TEX_COLOR, 'sRGB')
    nrm = new_img(base + "_Normal", TEX_NORMAL, 'Non-Color')
    mat, tc, tn = make_mat(base, col, nrm)
    lp.data.materials.clear(); lp.data.materials.append(mat); nt = mat.node_tree
    scene.render.bake.margin = max(16, TEX_COLOR // 256)
    try: scene.render.bake.margin_type = 'ADJACENT_FACES'
    except Exception: pass
    sh, shr = src.hide_get(), src.hide_render
    src.hide_set(False); src.hide_render = False
    neutralize_metallic(src)   # metallic=1 sources bake pure-black diffuse
    solo(src, lp, active=lp)
    et, rt = EXT_TIGHT * max_dim, RAY_TIGHT * max_dim
    ef, rf = EXT_FAR * max_dim, RAY_FAR * max_dim
    bake_into(nt, tc, 'DIFFUSE', et, rt); bake_into(nt, tn, 'NORMAL', et, rt)
    fc, fn = new_img("_fb_c", TEX_COLOR, 'sRGB'), new_img("_fb_n", TEX_NORMAL, 'Non-Color')
    sc = nt.nodes.new('ShaderNodeTexImage')
    sc.image = fc; bake_into(nt, sc, 'DIFFUSE', ef, rf)
    sc.image = fn; bake_into(nt, sc, 'NORMAL', ef, rf)
    cc = composite(col, fc, False); cn = composite(nrm, fn, True)
    nt.nodes.remove(sc); bpy.data.images.remove(fc); bpy.data.images.remove(fn)
    log(f"  composite filled {cc} col / {cn} nrm texels")

    # QA gate: a healthy albedo bake has real colour. A near-black mean means the
    # bake silently failed (metallic sources, dead UVs) — fail LOUDLY.
    mb = mean_brightness(col)
    log(f"  color-bake mean brightness {mb:.3f}")
    if mb < BRIGHTNESS_MIN:
        raise BlackBake(f"color bake is black (mean {mb:.3f} < {BRIGHTNESS_MIN}) — refusing to export")
    col.pack(); nrm.pack()

    # 7. export GLB + textures
    os.makedirs(outdir, exist_ok=True)
    glb = os.path.join(outdir, f"{base}_LP.glb")
    _export_glb(lp, glb)
    basecolor = os.path.join(outdir, f"{base}_BaseColor.png")
    normal = os.path.join(outdir, f"{base}_Normal.png")
    col.save_render(basecolor)
    nrm.save_render(normal)
    src.hide_render = shr; src.hide_set(sh)
    log(f"  → {glb} ({os.path.getsize(glb)//1024} KB) in {time.time()-t0:.0f}s")
    bpy.data.objects.remove(lp, do_unlink=True)
    return {
        "glb": glb, "basecolor": basecolor, "normal": normal, "mode": "bake",
        "faces_in": faces_in, "tris_out": tris_out, "shells_kept": shells_kept,
        "color_mean": round(mb, 4), "duration_s": round(time.time() - t0, 2),
    }


def main():
    inputs = parse_args()
    if not inputs:
        err("no inputs (pass one or more plain .glb paths)")
        _write_summary([]); sys.exit(2)
    outdir = OUTDIR or os.path.join(os.path.dirname(inputs[0]) or '.', 'baked')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    enable_cycles(bpy.context.scene)
    summary = []
    for path in inputs:
        rec = {"src": os.path.basename(path), "ok": False, "mode": MODE}
        try:
            src = import_glb(path)
            if src is None:
                raise RuntimeError("import yielded no mesh")
            rec.update(passthrough(src, outdir) if MODE == "skip" else process(src, outdir))
            rec["ok"] = True
        except BlackBake as e:
            err(f"{os.path.basename(path)} black bake: {e}")
            rec["error"] = str(e); rec["black_bake"] = True
        except Exception as e:
            err(f"{os.path.basename(path)} failed: {e}")
            rec["error"] = str(e); traceback.print_exc()
        summary.append(rec)
    _write_summary(summary)
    log("SUMMARY " + json.dumps(summary))
    sys.exit(0 if summary and all(r["ok"] for r in summary) else 1)


def _write_summary(summary):
    if not SUMMARY:
        return
    try:
        os.makedirs(os.path.dirname(SUMMARY) or '.', exist_ok=True)
        with open(SUMMARY, "w", encoding="utf-8") as fh:
            json.dump(summary, fh)
    except OSError as e:
        err(f"could not write summary {SUMMARY}: {e}")


# Runs only under `blender -b -P bake.py` (the top-level `import bpy` makes a plain
# Python import impossible), so invoke unconditionally like the DayDreams reference.
main()
