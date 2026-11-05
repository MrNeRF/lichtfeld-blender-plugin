# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render an immutable capture, evaluated cameras and seed for a local job."""

from pathlib import Path
import sys
import math
import json

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_core import atomic_json, read_json, read_job_json, read_dataset_json, digest


def intrinsics(scene, camera, width, height):
    """Recover pinhole intrinsics from Blender's actual projection matrix."""
    projection = camera.calc_matrix_camera(
        bpy.context.evaluated_depsgraph_get(), x=width, y=height,
        scale_x=scene.render.pixel_aspect_x, scale_y=scene.render.pixel_aspect_y)
    return {"fl_x": float(projection[0][0] * width / 2),
            "fl_y": float(projection[1][1] * height / 2),
            "cx": float((1 - projection[0][2]) * width / 2),
            "cy": float((1 + projection[1][2]) * height / 2)}


def write_seed(path, sources, limit=30000):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    source_set = set(sources)
    # Dependency-graph instance objects are transient RNA views. Never retain
    # their evaluated object references across iterator steps / mesh conversion.
    def eligible(item):
        return (item.object.type in {"MESH", "CURVE", "SURFACE", "FONT", "META"}
                and not item.object.hide_render and getattr(item, "show_self", True)
                and (item.object.original in source_set or
                     item.parent and item.parent.original in source_set)
                and (item.object.type != 'MESH' or any(polygon.area > 0 for polygon in item.object.data.polygons)))
    instance_count = sum(1 for item in depsgraph.object_instances if eligible(item))
    vertices = []
    colors = []
    rng = np.random.default_rng(142857)
    from capture_seed import instance_sample_plan
    plan = iter(instance_sample_plan(limit, instance_count)) if instance_count else iter(())
    next_plan = next(plan, None)
    rank = 0
    for item in depsgraph.object_instances:
        if not eligible(item):
            continue
        if next_plan is None or rank != next_plan[0]:
            rank += 1
            continue
        take = next_plan[1]
        next_plan = next(plan, None)
        rank += 1
        evaluated = item.object
        world_matrix = item.matrix_world.copy()
        source = evaluated.original
        if source.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
            continue
        temporary = evaluated.type != "MESH"
        mesh = evaluated.to_mesh() if temporary else evaluated.data
        if mesh is None:
            continue
        try:
            mesh.calc_loop_triangles()
            if not mesh.loop_triangles:
                continue
            local = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
            mesh.vertices.foreach_get("co", local)
            matrix = np.array(world_matrix, dtype=np.float64)
            points = local.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
            triangles = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
            mesh.loop_triangles.foreach_get("vertices", triangles)
            triangles = triangles.reshape(-1, 3)
            xyz = points[triangles]
            area = np.linalg.norm(np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]), axis=1)
            total_area = area.sum()
            if not np.isfinite(area).all() or not np.isfinite(total_area) or total_area <= 0:
                continue
            chosen = rng.choice(len(xyz), take, p=area / area.sum())
            uv = rng.random((take, 2))
            uv[uv.sum(axis=1) > 1] = 1 - uv[uv.sum(axis=1) > 1]
            samples = xyz[chosen, 0] + uv[:, :1] * (xyz[chosen, 1] - xyz[chosen, 0])
            samples += uv[:, 1:] * (xyz[chosen, 2] - xyz[chosen, 0])
            vertices.append(samples)
            base = np.array(source.color[:3])
            if source.active_material:
                base = np.array(source.active_material.diffuse_color[:3])
            colors.append(np.tile(np.clip(base, 0, 1) * 255, (take, 1)))
        finally:
            if temporary:
                evaluated.to_mesh_clear()
    if not vertices:
        raise ValueError("The source needs renderable surface geometry for initialization")
    positions = np.concatenate(vertices).astype("<f4")
    rgb = np.concatenate(colors).astype("u1")
    if len(positions) > limit:
        raise RuntimeError('Surface initialization exceeded its global sample budget')
    structured = np.empty(len(positions), dtype=[("xyz", "<f4", (3,)), ("rgb", "u1", (3,))])
    structured["xyz"], structured["rgb"] = positions, rgb
    with Path(path).open("wb") as stream:
        stream.write(("ply\nformat binary_little_endian 1.0\nelement vertex " + str(len(positions)) +
                      "\nproperty float x\nproperty float y\nproperty float z\n"
                      "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n").encode())
        structured.tofile(stream)


def capture_color_profile(scene):
    """Capture Standard sRGB without baking scene display modifiers."""
    view=scene.view_settings
    fields=('view_transform','look','exposure','gamma','use_curve_mapping','use_white_balance',
            'white_balance_temperature','white_balance_tint')
    original={name:getattr(view,name) for name in fields}
    original["display_device"]=scene.display_settings.display_device
    scene.display_settings.display_device="sRGB"
    view.view_transform='Standard'
    view.look='None'
    view.exposure=0
    view.gamma=1
    view.use_curve_mapping=False
    view.use_white_balance=False
    return original


def read_capture_rgba(path, width, height):
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        image.colorspace_settings.name = 'Non-Color'
        if tuple(image.size) != (width, height):
            raise ValueError('Incomplete capture image: ' + str(path))
        values = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(values)
        np.clip(values, 0, 1, out=values)
        values *= 255
        np.rint(values, out=values)
        return values.reshape(height, width, 4).astype(np.uint8)
    finally:
        bpy.data.images.remove(image)


def render_dataset(job_path, reuse_images=False):
    job = read_job_json(job_path)
    # Blender's file-output nodes resolve plain relative paths differently from
    # Python. Resolve once before passing any path to either API.
    root = Path(job["root"]).resolve()
    requested_render = job.get('render_settings')
    if requested_render and not reuse_images:
        atomic_json(root / 'capture-render.json', {'requested': requested_render, 'state': 'initializing devices'})
    depth_supervision = bool(job.get('use_depth_loss') and job.get('depth_loss_weight', 2) > 0)
    visible_seed = job.get('seed_method', 'SURFACE') == 'VISIBLE' or depth_supervision and 'seed_method' in job
    if not reuse_images and job.get("source_sha256") and digest(job.get("capture_staging_file",job["source_file"])) != job["source_sha256"]:
        raise ValueError("The saved scene changed while opening capture. Save it and retry.")
    if reuse_images:
        if Path(bpy.data.filepath).resolve() != (root / "capture.blend").resolve():
            raise ValueError("Recovery must open this job's frozen capture.blend")
        if (job.get("use_depth_loss") or job.get("use_normal_loss")) and not visible_seed:
            raise ValueError("Legacy map-supervised recovery requires a capture with saved visible-point samples")
    else:
        bpy.ops.wm.save_as_mainfile(filepath=str(root / "capture.blend"), copy=True,
                                   relative_remap=True, check_existing=False)
    output = root / "dataset"
    (output / "images").mkdir(parents=True, exist_ok=True)
    scene = bpy.data.scenes.get(job.get("scene", "")) or bpy.context.scene
    bpy.context.window.scene = scene
    bpy.context.window.view_layer = scene.view_layers.get(job.get("view_layer", "")) or scene.view_layers[0]
    scene.frame_set(job["frame"])
    from capture_settings import job_format, validate_camera, copy_camera, apply_format
    capture_format = job_format(job, scene)
    camera_overrides = validate_camera(job.get('capture_camera', {}))
    missing = []
    for library in bpy.data.libraries:
        if not getattr(library, "packed_file", None) and not Path(bpy.path.abspath(library.filepath)).is_file():
            missing.append(library.filepath)
    for image in bpy.data.images:
        if image.source == "FILE" and image.filepath and not image.packed_file:
            path = Path(bpy.path.abspath(image.filepath, library=image.library))
            if not path.is_file():
                missing.append(str(path))
    if missing:
        raise ValueError("Missing capture assets: " + "; ".join(missing[:8]))
    sources = [bpy.data.objects[name] for name in job["sources"] if name in bpy.data.objects]
    if len(sources) != len(job["sources"]):
        raise ValueError("A source object is missing from the capture")
    cameras = [bpy.data.objects[name] for name in job["cameras"] if name in bpy.data.objects]
    if len(cameras) != len(job["cameras"]) or len(cameras) < 4:
        raise ValueError("Choose at least four perspective cameras")
    if visible_seed:
        from capture_seed import view_budget
        view_budget(job['seed_points'], len(cameras))  # Validate before any render.
    # Freeze every camera independently, including whole-scene captures and
    # cameras sharing animated data. Constraints are resolved before isolation.
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    cameras = [copy_camera(camera, graph, camera_overrides) for camera in cameras]
    if job["scope"] != "SCENE":
        original = scene
        scene = original.copy()
        for child in list(scene.collection.children):
            scene.collection.children.unlink(child)
        for obj in list(scene.collection.objects):
            scene.collection.objects.unlink(obj)
        lights = set()
        layer = original.view_layers.get(job.get("view_layer", "")) or original.view_layers[0]
        def collect(collection):
            if collection.exclude or collection.collection.hide_render:
                return
            lights.update(obj for obj in collection.collection.objects if obj.type == "LIGHT" and not obj.hide_render)
            for child in collection.children:
                collect(child)
        collect(layer.layer_collection)
        for obj in set(sources + cameras) | lights:
            scene.collection.objects.link(obj)
        bpy.context.window.scene = scene
        bpy.context.window.view_layer = scene.view_layers.get(job.get("view_layer", "")) or scene.view_layers[0]
        scene.frame_set(job["frame"])
        for source in sources:
            source.hide_render = False
    else:
        for camera in cameras:
            scene.collection.objects.link(camera)
    for obj in scene.objects:
        if obj.get("lichtfeld_result"):
            obj.hide_render = True
    for name in job.get("capture_helpers", []):
        if name in scene.objects:
            scene.objects[name].hide_render = True
    if requested_render:
        try:
            from render_devices import configure
            from scene_render_worker import verify_saved_settings
            verify_saved_settings(scene, requested_render)
            if not reuse_images:
                effective_render = configure(requested_render['request'])
                atomic_json(root / 'capture-render.json', {'requested': requested_render, 'effective': effective_render, 'state': 'ready'})
                print('LICHTFELD_CAPTURE_RENDER ' + json.dumps(effective_render), flush=True)
        except Exception as error:
            atomic_json(root / 'capture-render.json', {'requested': requested_render, 'state': 'failed', 'error': str(error)})
            raise
    else:
        # Existing 0.1.0 jobs retain their original capture recipe.
        scene.render.engine = job["engine"]
    if not requested_render and scene.render.engine == "CYCLES":
        scene.render.use_persistent_data = job.get('reuse_render_data', True)
        scene.cycles.samples = job["samples"]
        scene.cycles.device = job.get("cycles_device", "CPU")
        if scene.cycles.device == 'GPU':
            # Background --factory-startup does not inherit interactive preferences.
            preferences = bpy.context.preferences.addons['cycles'].preferences
            available = False
            for backend in ('OPTIX','CUDA','HIP','METAL','ONEAPI'):
                try:
                    preferences.compute_device_type = backend
                    preferences.get_devices()
                    for device in preferences.devices:
                        device.use = device.type != 'CPU'
                        available = available or device.use
                    if available:
                        break
                except (TypeError, RuntimeError):
                    continue
            if not available:
                raise ValueError('No supported Cycles GPU is available. Select CPU capture.')
        scene.cycles.use_denoising = True
    elif not requested_render:
        scene.eevee.taa_render_samples = job["samples"]
    apply_format(scene, capture_format)
    scene.render.use_motion_blur = False
    scene.render.image_settings.file_format = "PNG"
    isolated = job["scope"] != "SCENE"
    scene.render.image_settings.color_mode = "RGBA" if isolated else "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.film_transparent = isolated
    scene.render.use_compositing = False
    scene.render.use_sequencer = False
    source_display = capture_color_profile(scene)
    width, height = capture_format['width'], capture_format['height']
    saved_frames = read_dataset_json(root / 'capture-frames.json', {}).get('frames', []) if reuse_images else []
    if reuse_images and (job.get('use_depth_loss') or job.get('use_normal_loss')) and len(saved_frames) != len(cameras):
        raise ValueError('Saved map capture is incomplete. Existing images are preserved.')
    frames = []
    geometry = None
    if not reuse_images and (visible_seed or job.get('use_depth_loss') or job.get('use_normal_loss')):
        from capture_passes import GeometryPasses
        geometry = GeometryPasses(scene, bpy.context.view_layer, output, observations=depth_supervision and not visible_seed,
                                  seed_limit=job['seed_points'] if visible_seed else None, views=len(cameras),
                                  normals=bool(job.get('use_normal_loss')))
    loop_finished = False
    try:
        for index, camera in enumerate(cameras):
            if camera.data.type != "PERSP":
                raise ValueError(f"Camera {camera.name} is not perspective")
            camera.data.dof.use_dof = False
            scene.camera = camera
            depsgraph = bpy.context.evaluated_depsgraph_get()
            evaluated = camera.evaluated_get(depsgraph)
            location, rotation, scale = evaluated.matrix_world.decompose()
            if any(abs(component - 1) > 1e-4 for component in scale):
                raise ValueError(f"Apply scale on camera {camera.name} and its parents before capture")
            matrix = rotation.to_matrix().to_4x4()
            matrix.translation = location
            relative = f"images/view_{index:06d}.png"
            scene.render.filepath = str(output / relative)
            if geometry:
                geometry.prepare(index)
            if not reuse_images:
                bpy.ops.render.render(write_still=True)
            rgba = read_capture_rgba(output / relative, width, height) if geometry or isolated or reuse_images else None
            frame = {"file_path": relative, "transform_matrix": [list(row) for row in matrix],
                     "w": width, "h": height, **intrinsics(scene, evaluated, width, height)}
            if reuse_images and len(saved_frames) == len(cameras):
                saved = saved_frames[index]
                if (saved.get('file_path') != relative or saved.get('w') != width or saved.get('h') != height or
                        not np.allclose(saved.get('transform_matrix'), frame['transform_matrix'], rtol=0, atol=1e-6) or
                        any(not math.isclose(saved[key], frame[key], rel_tol=1e-6, abs_tol=1e-5) for key in ('fl_x','fl_y','cx','cy'))):
                    raise ValueError('Saved camera calibration differs from the frozen capture')
                # Preserve the exact calibration that rendered these images, after
                # checking it against the frozen scene. Do not introduce roundoff.
                frame = dict(saved)
                for kind, enabled in (('depths', job.get('use_depth_loss')), ('normals', job.get('use_normal_loss'))):
                    path = output / kind / f'view_{index:06d}.tiff'
                    if enabled and (not path.is_file() or path.is_symlink()):
                        raise ValueError(f'Saved {kind} for view {index+1} are missing. Existing images are preserved.')
            if geometry:
                frame.update(geometry.export(np.array(matrix), job.get('use_depth_loss'), job.get('use_normal_loss'), frame, rgba))
            if isolated:
                mask = bpy.data.images.new("LichtFeld alpha", width=width, height=height, alpha=False)
                try:
                    pixels = np.empty(width * height * 4, dtype=np.float32)
                    alpha = rgba[:, :, 3].reshape(-1).astype(np.float32) / 255
                    pixels.reshape(-1, 4)[:, :3] = alpha[:, None]
                    pixels.reshape(-1, 4)[:, 3] = 1
                    # Changing a generated image's color space invalidates its pixel
                    # storage in Blender. Configure it before writing the mask.
                    mask.colorspace_settings.name = "Non-Color"
                    mask.pixels.foreach_set(pixels)
                    mask.update()
                    relative_mask = f"masks/view_{index:06d}.png"
                    (output / "masks").mkdir(exist_ok=True)
                    mask.filepath_raw = str(output / relative_mask)
                    mask.file_format = "PNG"
                    mask.save()
                    frame["mask_path"] = relative_mask
                    frame["mask_foreground_fraction"] = float(np.mean(alpha > 0.5))
                finally:
                    bpy.data.images.remove(mask)
            frames.append(frame)
            atomic_json(root / "capture-frames.json", {"job_id": job["id"], "frames": frames})
            atomic_json(root / "capture-progress.json", {"done": index + 1, "total": len(cameras)})
        loop_finished = True
    finally:
        if geometry:
            geometry.close(commit=loop_finished)
    atomic_json(root / "capture-progress.json", {"done": len(cameras), "total": len(cameras), "stage": "initializing points"})
    if visible_seed:
        from capture_seed import finalize
        initialization, points, colors = finalize(output, job['seed_points'], len(cameras), return_cloud=True)
        if depth_supervision:
            from capture_observations import write_visible
            initialization['observations'] = write_visible(output, frames, points, colors)
    else:
        write_seed(output / "pointcloud.ply", sources, job["seed_points"])
        initialization = {'method': 'surface_geometry', 'budget': job['seed_points']}
    from lichtfeld_sessions.geometry import from_capture
    atomic_json(output / "view_geometry.json", from_capture(frames))
    if isolated and not any(frame.get("mask_foreground_fraction", 0) > 0 for frame in frames):
        raise ValueError("None of the capture cameras sees the source. Review rig coverage.")
    transforms = {"camera_model": "PINHOLE", "w": width, "h": height, "frames": frames}
    transforms_file = "capture_transforms.json" if depth_supervision else "transforms.json"
    atomic_json(output / transforms_file, transforms)
    atomic_json(output / "manifest.json", {
        "job_id": job["id"], "views": len(frames), "frame": job["frame"],
        "color_space": "sRGB Standard", "world_basis": "Blender",
        "source_display": source_display, "display_overrides_baked": False,
        "capture_format": capture_format, "capture_camera": camera_overrides,
        "training_world_basis": "LichtFeld COLMAP: Blender X, -Y, -Z",
        "view_geometry_file": "view_geometry.json",
        "initialization": initialization,
        "transforms_file": transforms_file,
        "dataset_format": "COLMAP" if depth_supervision else "Blender transforms",
        "transforms_sha256": digest(output / transforms_file),
        "validation": [frame["file_path"] for i, frame in enumerate(frames) if i % job["test_every"] == 0],
    })


if __name__ == "__main__":
    job_path = Path(sys.argv[sys.argv.index("--") + 1])
    try:
        render_dataset(job_path, reuse_images="--finalize-capture" in sys.argv)
    except Exception as error:
        try:
            failure_root = Path(read_job_json(job_path)['root'])
            report = read_json(failure_root/'capture-render.json', {})
            atomic_json(failure_root/'capture-render.json', {**report, 'state':'failed', 'error':str(error)})
        except Exception:
            pass  # Preserve the actual render exception if diagnostics cannot be saved.
        raise
