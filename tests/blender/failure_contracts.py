# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run in real Blender: cancellation at every yield and timer isolation."""
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch
import bpy
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
if "--installed" in sys.argv:
    import importlib
    ext = importlib.import_module("bl_ext.user_default.lichtfeld_training")
    assert ext.__package__ in bpy.context.preferences.addons
else:
    bpy.ops.preferences.addon_enable(module="lichtfeld_training")
    import lichtfeld_training as ext
ui = ext.ui_runtime


def clear_and_wait():
    readers = [binding.reader for binding in ui._bindings.values()]
    ui.clear()
    for reader in readers:
        if hasattr(reader, "thread"):
            reader.thread.join(5)  # Test teardown only; production never joins UI.


scene = bpy.context.scene
frame = {"count": 1, "record": {"id": "a", "iteration": 12},
    "job": {"id": "same-job", "sources": ["Cube"]}, "transport": "test",
    "data": [("position", "FLOAT_VECTOR", "vector", np.zeros(3, dtype=np.float32))]}
for _ in ui.build(scene, frame, ui.scene_key(scene)):
    pass
steps = sum(1 for _ in ui.build(scene, frame, ui.scene_key(scene)))
for cutoff in range(1, steps + 2):
    work = ui.build(scene, frame, ui.scene_key(scene))
    for _ in range(cutoff):
        try:
            next(work)
        except StopIteration:
            break
    work.close()
    assert len(bpy.data.pointclouds) == 1, (cutoff, list(bpy.data.pointclouds))
    assert scene.lichtfeld_training.result_object.data.users == 1
print("CANCEL_EVERY_YIELD_PASS", steps, flush=True)

# A deleted scene makes both the success callback and the old error callback fail.
# Their failure must not unregister the common timer or skip the next task.
removed = bpy.data.scenes.new("Removed while loading")
gate = threading.Event()
with patch.object(ext.jobs, "load_job", side_effect=lambda path: (gate.wait(5), "/loaded")[1]):
    with bpy.context.temp_override(scene=removed):
        assert bpy.ops.lichtfeld.load_job(filepath="fixture/job.json") == {"FINISHED"}
    future = ui._tasks[-1][0]
    bpy.data.scenes.remove(removed)
    gate.set()
    future.result(timeout=5)
    assert ui.tick() == .02
assert not ui._tasks
seen = []
def broken():
    raise ValueError("worker failure")
def broken_failure(error):
    raise RuntimeError("failure callback also failed")
f = ui.submit(broken, failure=broken_failure)
try:
    f.result(timeout=5)
except ValueError:
    pass
f = ui.submit(lambda: 1, callback=seen.append)
f.result(timeout=5)
assert ui.tick() == .02 and seen == [1]
def action():
    raise ValueError("staging failure")
    yield
ui.schedule(action(), broken_failure)
assert ui.tick() == .02
print("DELETED_SCENE_AND_CALLBACK_ISOLATION_PASS", flush=True)

class FakeObserver:
    def __init__(self, *args, **kw):
        self.status = {"state": "training", "iteration": 3, "iterations": 10}
        self.external = False
        self.error = ""
        self.closed = False
    def take(self):
        return None
    def close(self):
        self.closed = True

second = bpy.data.scenes.new("Healthy independent binding")
with patch.object(ui, "Observer", FakeObserver):
    scene.lichtfeld_training.job_directory = "/bad-fixture"
    second.lichtfeld_training.job_directory = "/good-fixture"
    bad, good = ui.binding(scene), ui.binding(second)
    # Rendering may begin before the first capture-progress file exists.
    bad.reader.status = {"state": "rendering"}
    assert ui.tick() == .02
    assert scene.lichtfeld_training.status == "Rendering"
    bad.reader.status["iteration"] = "corrupt"
    assert ui.tick() == .02
    assert bad.reader.closed
    assert scene.lichtfeld_training.status == "Failed"
    assert "Invalid training counter" in scene.lichtfeld_training.detail
    assert second.lichtfeld_training.status == "Training"
    assert "Training step 3 / 10" in second.lichtfeld_training.detail
    assert ui.tick() == .02
    recovered = ui.reconnect(scene)
    assert recovered is not bad
    assert ui.tick() == .02
    assert scene.lichtfeld_training.status == "Training"
    clear_and_wait()
scene.lichtfeld_training.job_directory = ""
bpy.data.scenes.remove(second)
print("CORRUPT_BINDING_ISOLATION_PASS", flush=True)

# Imported metadata never chooses the executable or index, including after reopen.
import tempfile
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory) / "previous"
    (root / "output").mkdir(parents=True)
    (root / "output/project.licht").write_bytes(b"checkpoint fixture")
    ext.atomic_json(root / "job.json", {"schema": 1, "id": "previous", "root": str(root),
        "backend": "UNTRUSTED_EXECUTABLE", "connection_directory": "UNTRUSTED_INDEX",
        "dataset_root": str(root / "dataset")})
    prefs = ext.preferences()
    prefs.backend_path = str(Path(directory) / "configured-lichtfeld.exe")
    prefs.jobs_directory = str(Path(directory) / "configured-jobs")
    scene.lichtfeld_training.job_directory = str(root)
    launched = []
    with patch.object(ext, "allowed", return_value=True), patch.object(ext, "launch_supervisor", side_effect=launched.append), patch.object(ui, "binding", return_value=None):
        assert bpy.ops.lichtfeld.resume_job() == {"FINISHED"}
        ui._tasks[-1][0].result(timeout=5)
        # Avoid a reader for this synthetic checkpoint; test callback directly.
        future, done, failure = ui._tasks.pop()
        done(future.result())
    assert len(launched) == 1
    assert launched[0]["backend"] == prefs.backend_path
    assert launched[0]["connection_directory"] == ext.preflight.connection_folder(prefs)
    prefs.backend_path = ""
    with patch.object(ext, "allowed", return_value=True), patch.object(ext, "launch_supervisor") as spawn:
        try:
            result = bpy.ops.lichtfeld.resume_job()
            assert result == {"CANCELLED"}
        except RuntimeError as error:
            assert "Choose the LichtFeld executable" in str(error)
        spawn.assert_not_called()
    scene.lichtfeld_training.job_directory = ""
    scene.lichtfeld_training.running = False
# Full button path: explicitly restore an already acknowledged saved generation.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    ext.atomic_json(root / "job.json", {"schema": 1, "id": "restore", "root": str(root)})
    arrays = {"means": np.zeros((1, 3)), "scale": np.zeros((1, 3)),
        "rotation": np.array([[1, 0, 0, 0]]), "opacity": np.zeros((1, 1)),
        "sh": np.zeros((1, 1, 3))}
    import importlib
    core = importlib.import_module(ext.__package__ + ".bridge_core")
    record = core.write_splat(root, arrays, 4, "best", {"psnr": 30})
    ext.atomic_json(root / "best.json", record)
    ext.atomic_json(root / "state.json", {"state": "completed", "result": record})
    scene.lichtfeld_training.job_directory = str(root)
    def wait_import():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            ui.tick()
            binding = ui.peek(scene)
            if (scene.lichtfeld_training.result_object and binding.reader.last == record["id"]
                    and not scene.lichtfeld_training.importing):
                return
            time.sleep(.01)
        raise AssertionError(scene.lichtfeld_training.detail)
    wait_import()
    result = scene.lichtfeld_training.result_object
    cloud = result.data
    bpy.data.objects.remove(result, do_unlink=True)
    bpy.data.pointclouds.remove(cloud)
    assert bpy.ops.lichtfeld.import_result() == {"FINISHED"}
    wait_import()
    assert scene.lichtfeld_training.result_object["lichtfeld_artifact"] == record["id"]
    old_reader = ui.peek(scene).reader
    ui.fail(scene, "Injected connection failure")
    assert old_reader.stop.is_set()
    assert bpy.ops.lichtfeld.reconnect_job() == {"FINISHED"}
    assert ui.peek(scene).reader is not old_reader
    old_reader.thread.join(5)
    wait_import()
    assert scene.lichtfeld_training.status == "Completed"
    clear_and_wait()
    scene.lichtfeld_training.job_directory = ""
print("RESTORE_ACKNOWLEDGED_BEST_BUTTON_PASS", flush=True)
print("TRUSTED_RESUME_OPERATOR_PASS", flush=True)
# Import staging must preserve the layout and leave commands usable.
from types import SimpleNamespace
class PanelLayout:
    def __init__(self): self.operators = []
    def box(self): return self
    def row(self, **kwargs): return self
    def column(self, **kwargs): return self
    def label(self, **kwargs): pass
    def progress(self, **kwargs): pass
    def prop(self, *args, **kwargs): pass
    def operator(self, name, **kwargs):
        self.operators.append(name)
        return SimpleNamespace()
settings = scene.lichtfeld_training
settings.job_directory = "panel-fixture"
settings.running = True
settings.job_details = True
settings.status = "Training"
status = {"state": "training", "best": {"score": {"psnr": 30}}, "control_available": True}
context = SimpleNamespace(scene=scene, region=SimpleNamespace(width=300))
orders = []
with patch.object(ui, "peek", return_value=SimpleNamespace(status=status, reader=SimpleNamespace(error=""))), patch.object(ext, "preferences", return_value=None):
    for busy in (False, True, False):
        settings.importing = busy
        layout = PanelLayout()
        ext.LF_PT_training.draw(SimpleNamespace(layout=layout), context)
        orders.append(layout.operators)
assert orders[0] == orders[1] == orders[2], orders
assert "lichtfeld.import_result" in orders[0] and "lichtfeld.compare_result" in orders[0]
settings.job_directory = ""
settings.running = False
settings.importing = False
print("STABLE_PANEL_DURING_PREVIEW_IMPORT_PASS", flush=True)

bpy.ops.preferences.addon_disable(module=ext.__package__)
print("FAILURE_CONTRACTS_PASS", flush=True)

# Polling an unchanged value must not trigger RNA writes or dependency updates.
class AssignmentProbe:
    value = 5
    writes = 0
    def __setattr__(self, name, value):
        if name == "value":
            self.writes += 1
        object.__setattr__(self, name, value)
probe = AssignmentProbe()
for _ in range(100):
    ui.assign(probe, "value", 5)
assert probe.writes == 0
ui.assign(probe, "value", 6)
assert probe.writes == 1
print("UNCHANGED_RNA_POLLING_PASS", flush=True)

probe.value = float(np.float32(0.123456789))
previous_writes = probe.writes
for _ in range(100):
    ui.assign(probe, "value", 0.123456789)
assert probe.writes == previous_writes
print("RNA_FLOAT_ROUNDING_PASS", flush=True)
