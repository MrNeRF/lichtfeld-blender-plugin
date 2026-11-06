# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with Blender --background --factory-startup --python this_file -- area backend.

Creates a real scene and launches the same operators as the installed extension.
Waits are implemented as a separate process polling the actual job state.
"""

from pathlib import Path
import sys
import time
import importlib
import argparse
import bpy
import numpy as np
from mathutils import Vector

root = Path(__file__).resolve().parents[2]
arguments = sys.argv[sys.argv.index("--") + 1:]
parser = argparse.ArgumentParser()
parser.add_argument("area")
parser.add_argument("backend")
parser.add_argument("--installed", action="store_true")
parser.add_argument("--jobs", default="")
parser.add_argument("--geometry", action="store_true")
parser.add_argument("--stop-capture", action="store_true")
parser.add_argument("--resolution", type=int, default=64)
parser.add_argument("--strategy", default="mrnf", choices=["mrnf","mcmc","igs+"])
parser.add_argument("--steps", type=int, default=16)
parser.add_argument("--points", type=int, default=2000)
options = parser.parse_args(arguments)
area, backend = options.area, options.backend
Path(area).mkdir(parents=True,exist_ok=True)
if options.installed:
    extension = importlib.import_module("bl_ext.user_default.lichtfeld_training")
    assert "bl_ext.user_default.lichtfeld_training" in bpy.context.preferences.addons
else:
    sys.path.insert(0, str(root))
    import lichtfeld_training as extension
    bpy.ops.preferences.addon_enable(module="lichtfeld_training")
prefs = extension.preferences()
prefs.backend_path = backend
prefs.jobs_directory = options.jobs or str(Path(area) / "jobs")
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2)
source = bpy.context.object
source.location = (2, -1, 0.5)
source.name = "Training test source"
material = bpy.data.materials.new("Terracotta")
material.diffuse_color = (0.7, 0.15, 0.04, 1)
source.data.materials.append(material)
bpy.ops.object.light_add(type="AREA", location=(5, -5, 5.5))
bpy.context.object.data.energy = 600
bpy.context.object.data.shape = "DISK"
bpy.context.object.data.size = 5
bpy.ops.object.select_all(action="DESELECT")
source.select_set(True)
bpy.context.view_layer.objects.active = source
settings = bpy.context.scene.lichtfeld_training
settings.views = 8
settings.resolution = options.resolution
settings.samples = 1
settings.engine = "BLENDER_EEVEE"
settings.iterations = options.steps
settings.eval_interval = max(4, options.steps // 4)
settings.max_points = options.points
settings.preview_points = 1000
settings.preview_seconds = 1
settings.sh_degree = 1
settings.strategy = options.strategy
if options.geometry:
    settings.engine = "CYCLES"
    settings.samples = 4
    settings.use_depth_loss = True
    settings.use_normal_loss = True
extension.capture_settings.apply_format(bpy.context.scene, extension.capture_settings.preset_format(settings))
assert bpy.ops.lichtfeld.create_rig() == {"FINISHED"}
assert sum(o.type == "CAMERA" for o in settings.camera_collection.objects) == 8
bpy.ops.wm.save_as_mainfile(filepath=str(Path(area) / "smoke-source.blend"))
assert bpy.ops.lichtfeld.start_training() == {"FINISHED"}
job = Path(settings.job_directory)
(Path(area) / "smoke-job.txt").write_text(str(job))
print("SMOKE_JOB", job)
deadline = time.monotonic() + 600
previous = None
last_debug = 0
transports = set()
stop_requested = False
while time.monotonic() < deadline:
    state = extension.read_json(job / "state.json", {})
    if state.get("state") != previous:
        previous = state.get("state")
        print("SMOKE_STATE", state, flush=True)
    extension.poll_jobs()
    if options.stop_capture and state.get("state") == "rendering" and not stop_requested and extension.allowed(bpy.context, "stop"):
        assert bpy.ops.lichtfeld.training_command(action="stop") == {"FINISHED"}
        stop_requested = True
    if time.monotonic() - last_debug > 10:
        binding = extension.ui_runtime.peek(bpy.context.scene)
        print("SMOKE_VIEWER", settings.status, settings.detail, "reader", binding.reader.error if binding else None, flush=True)
        last_debug = time.monotonic()
    transports.update(obj.get("lichtfeld_transport") for obj in bpy.context.scene.objects
                      if obj.get("lichtfeld_job_id") == job.name)
    if state.get("state") in {"completed", "failed", "stopped"}:
        if options.stop_capture:
            assert stop_requested and state["state"] == "stopped", state
            assert not (job / "training.json").exists()
            source_job = extension.read_json(job / "job.json")
            assert not Path(source_job["capture_staging_file"]).exists()
            print("CANCEL_CAPTURE_WITH_VALIDATED_CONTROL_PASS", flush=True)
            break
        assert state["state"] == "completed", state
        visible = [obj for obj in bpy.context.scene.objects if obj.get("lichtfeld_job_id") == job.name
                   and obj.get("lichtfeld_artifact") == state["result"]["id"]]
        if not visible or settings.importing:
            time.sleep(0.02)
            continue
        assert state["result"]["score"] is not None, state
        manifest = extension.read_json(job / "dataset/manifest.json")
        captured = extension.read_json(job / "dataset" / manifest.get("transforms_file","transforms.json"))
        runtime = extension.read_json(job / 'runtime.json')
        assert runtime['hook_api_checked'] is True, runtime
        assert runtime['terminal_state'] == 'completed', runtime
        session_root = Path(runtime['session_directory'])
        session = extension.read_json(session_root / 'session.json')
        saved_path, saved_presentation = importlib.import_module("lichtfeld_sessions.transport").resolve_result(session)
        assert saved_path == (job / state['result']['path']).resolve(), (saved_path, state['result'])
        assert session['result']['count'] == state['result']['count']
        assert session['result']['sh_degree'] == state['result']['sh_degree']
        assert saved_presentation['source_frame'] == 'blender-world'
        assert not session.get('geometry_error'), session
        geometry = importlib.import_module("lichtfeld_sessions.geometry").load(session_root, runtime['geometry'])
        assert geometry['source_frame'] == 'blender-world', geometry
        assert geometry['cameras'], geometry
        original_geometry = importlib.import_module(extension.__package__ + ".session_geometry").from_capture(captured['frames'])
        for camera in geometry['cameras']:
            expected = min(original_geometry['cameras'], key=lambda item:
                np.max(np.abs(np.asarray(item['camera_to_data']) - camera['camera_to_data'])))
            np.testing.assert_allclose(camera['camera_to_data'], expected['camera_to_data'], atol=1e-5)
            for field in ('fx', 'fy', 'cx', 'cy', 'width', 'height'):
                np.testing.assert_allclose(camera[field], expected[field], atol=1e-4)
        assert all(frame["mask_foreground_fraction"] > 0 for frame in captured["frames"])
        for frame in captured["frames"]:
            mask = bpy.data.images.load(str(job / "dataset" / frame["mask_path"]), check_existing=False)
            try:
                pixels = np.empty(len(mask.pixels), dtype=np.float32)
                mask.pixels.foreach_get(pixels)
                assert pixels.reshape(-1, 4)[:, :3].max() > 0.9, frame
            finally:
                bpy.data.images.remove(mask)
        result = next(obj for obj in bpy.context.scene.objects if obj.get("lichtfeld_job_id") == job.name)
        assert result.type == "POINTCLOUD"
        assert result.data.type == "GAUSSIAN_SPLAT"
        assert not list((job / "artifacts").glob("preview-*.ply")), "Preview unexpectedly used disk PLY"
        if options.steps >= 2000:
            assert "win32_mapping" in transports, transports
        positions = np.empty(len(result.data.points) * 3, dtype=np.float32)
        result.data.attributes["position"].data.foreach_get("vector", positions)
        center = result.matrix_world @ Vector(positions.reshape(-1, 3).mean(axis=0))
        assert (center - source.location).length < 0.5, (center, source.location)
        print("SMOKE_RESULT", len(result.data.points), result["lichtfeld_iteration"], flush=True)
        print("OBSERVED_TRANSPORTS", sorted(transports), flush=True)
        print("MAX_UI_OPERATION_MS", settings.ui_max_ms, flush=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(Path(area) / "smoke-result.blend"))
        break
    time.sleep(0.02)
else:
    raise AssertionError("Training smoke timed out; inspect job logs")
