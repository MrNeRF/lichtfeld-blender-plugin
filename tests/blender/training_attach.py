# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Attach, detach, reconnect and reload Blender while another process trains."""
from pathlib import Path
import importlib
import sys
import time
import bpy
import numpy as np
from mathutils import Matrix

arguments = sys.argv[sys.argv.index("--") + 1:]
directory, output = map(Path, arguments[:2])
if "--installed" in arguments:
    extension = importlib.import_module("bl_ext.user_default.lichtfeld_training")
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import lichtfeld_training as extension
    bpy.ops.preferences.addon_enable(module="lichtfeld_training")

settings = bpy.context.scene.lichtfeld_training
settings.job_directory = ""
assert bpy.ops.lichtfeld.scan_sessions() == {"FINISHED"}
record = extension.read_json(directory / "session.json")
scan_deadline = time.monotonic() + 30
while not any(item["id"] == record["id"] for item in extension._sessions) and time.monotonic() < scan_deadline:
    extension.poll_jobs()
    time.sleep(0.02)
settings.discovered_session = record["id"]
assert bpy.ops.lichtfeld.attach_session() == {"FINISHED"}
identity = "session:" + record["id"]
deadline = time.monotonic() + 120


def until(predicate):
    while time.monotonic() < deadline:
        extension.poll_jobs()
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(settings.detail or "Viewer timed out")


until(lambda: any(obj.get("lichtfeld_job_id") == identity for obj in bpy.context.scene.objects))
result = next(obj for obj in bpy.context.scene.objects if obj.get("lichtfeld_job_id") == identity)
generation = result["lichtfeld_artifact"]
result.matrix_world = Matrix.Translation((3, 2, 1)) @ result.matrix_world
expected = result.matrix_world.copy()
until(lambda: result["lichtfeld_artifact"] != generation)
np.testing.assert_allclose(result.matrix_world, expected, atol=1e-6)
assert result["lichtfeld_transport"] == "win32_mapping"
assert extension.process_alive(record["pid"])
assert bpy.ops.lichtfeld.detach_session() == {"FINISHED"}
assert extension.process_alive(record["pid"])
assert bpy.ops.lichtfeld.attach_session() == {"FINISHED"}
generation = result["lichtfeld_artifact"]
until(lambda: result["lichtfeld_artifact"] != generation)
bpy.ops.wm.save_as_mainfile(filepath=str(output))
bpy.ops.wm.open_mainfile(filepath=str(output))
settings = bpy.context.scene.lichtfeld_training
assert Path(settings.attached_session) == directory
extension.poll_jobs()
assert any(obj.get("lichtfeld_job_id") == identity for obj in bpy.context.scene.objects)
assert bpy.ops.lichtfeld.detach_session() == {"FINISHED"}
assert extension.process_alive(record["pid"])
print("ATTACH_DETACH_RECONNECT_RELOAD_OK", identity, flush=True)
