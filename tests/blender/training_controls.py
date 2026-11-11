# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise pause, resume and graceful stop on an actual headless trainer."""

from pathlib import Path
import sys
import time
import bpy

import importlib
if '--installed' in sys.argv:
 extension=importlib.import_module('bl_ext.user_default.lichtfeld_training')
else:
 sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
 import lichtfeld_training as extension
 bpy.ops.preferences.addon_enable(module='lichtfeld_training')

area = Path(sys.argv[sys.argv.index("--") + 1])
bpy.ops.wm.open_mainfile(filepath=str(area / "smoke-result.blend"))
root = Path((area / "smoke-job.txt").read_text())
if "--backend" in sys.argv:
    extension.preferences().backend_path = sys.argv[sys.argv.index("--backend") + 1]
extension.preferences().jobs_directory = str(area / 'continued-jobs')
original_result = next(obj for obj in bpy.context.scene.objects if obj.get("lichtfeld_job_id") == root.name)
original_best = extension.read_json(root / "best.json")
original_job = extension.read_json(root / 'job.json')
settings = bpy.context.scene.lichtfeld_training
settings.job_directory = str(root)
saved_step = extension.read_json(root / 'runtime.json')['iteration']
expected_total = max(original_job['iterations'], saved_step + 1000)
settings.iterations = saved_step + 50000
settings.eval_interval = 7  # Unrelated scene settings must not change a continuation.
settings.full_preview = not original_job.get('full_preview', True)
ready_deadline=time.monotonic()+10
while not extension.allowed(bpy.context,'continue') and time.monotonic()<ready_deadline:
 extension.poll_jobs();time.sleep(.02)
assert bpy.ops.lichtfeld.resume_job() == {"FINISHED"}
resume_deadline = time.monotonic() + 30
while settings.operation_busy and time.monotonic() < resume_deadline:
    extension.poll_jobs()
    time.sleep(0.02)
assert not settings.operation_busy, settings.detail
root = Path(settings.job_directory)
assert root.parent == area / 'continued-jobs'
continued_job = extension.read_json(root / 'job.json')
assert continued_job['iterations'] == expected_total
assert continued_job['eval_interval'] == original_job['eval_interval']
assert continued_job['full_preview'] == original_job.get('full_preview', True)
inherited = extension.read_json(root / "best.json")
assert inherited["sha256"] == original_best["sha256"]
deadline = time.monotonic() + 180


def until(predicate):
    while time.monotonic() < deadline:
        state = extension.read_json(root / "state.json", {})
        assert state.get("state") != "failed", state
        runtime = extension.read_json(root / "runtime.json", {})
        extension.poll_jobs()
        if predicate(state, runtime):
            return state, runtime
        time.sleep(0.02)
    raise AssertionError("Control test timed out")


until(lambda state, runtime: runtime.get("state") == "training" and extension.allowed(bpy.context, "pause"))
bpy.ops.lichtfeld.training_command(action="pause")
_, runtime = until(lambda state, runtime: runtime.get("state") == "paused")
iteration = runtime["iteration"]
assert iteration >= saved_step, runtime
time.sleep(0.7)
assert extension.read_json(root / "runtime.json")["iteration"] == iteration
print("PAUSE_VERIFIED", iteration, flush=True)
bpy.ops.lichtfeld.training_command(action="resume")
until(lambda state, runtime: runtime.get("state") == "training" and runtime.get('iteration', 0) > saved_step)
bpy.ops.lichtfeld.training_command(action="stop")
state, _ = until(lambda state, runtime: state.get("state") == "stopped")
assert state.get("result"), state
assert (root / "output/project.licht").is_file()
assert next(obj for obj in bpy.context.scene.objects if obj.get("lichtfeld_job_id") == root.name) == original_result
assert state["result"]["score"]["psnr"] >= original_best["score"]["psnr"]
print("RESUME_AND_STOP_VERIFIED", state, flush=True)
