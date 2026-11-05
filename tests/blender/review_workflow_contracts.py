# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Connect to an external trainer and explicitly return to the retained local job."""
from pathlib import Path
import importlib
import sys
import tempfile
import time
from types import SimpleNamespace

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
module = 'bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
bpy.ops.preferences.addon_enable(module=module)
ext = importlib.import_module(module)
sys.path.insert(0, str(Path(ext.__file__).parent))
supervisor = importlib.import_module(ext.__package__ + ".supervisor")
output = Path(sys.argv[sys.argv.index("--") + 1])
output.mkdir(parents=True, exist_ok=True)


def check_interrupted_capture_cleanup():
    class CancelledChild:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

    with tempfile.TemporaryDirectory(prefix="lf-render-cleanup-") as directory:
        job_root = Path(directory)
        geometry = job_root / "dataset" / "_geometry_passes"
        sparse = job_root / "dataset" / "sparse" / "0"
        geometry.mkdir(parents=True)
        sparse.mkdir(parents=True)
        (geometry / ("lf_" + "a" * 32 + "_Depth_000011_view.exr")).write_bytes(b"temp")
        (geometry / ("lf_" + "b" * 32 + "_Normal_000012_view.exr")).write_bytes(b"temp")
        (geometry / "unowned.exr").write_bytes(b"keep")
        (job_root / "dataset" / "images" / "000011.png").parent.mkdir(parents=True)
        (job_root / "dataset" / "images" / "000011.png").write_bytes(b"image")
        (job_root / "dataset" / "capture_points_000011.npz").write_bytes(b"points")
        (job_root / "dataset" / "journal.jsonl").write_text("journal", encoding="utf-8")
        for name in ("cameras", "images", "points3D"):
            (sparse / (name + ".bin.lf-" + "c" * 32 + ".tmp")).write_bytes(b"temp")
            (sparse / (name + ".bin")).write_bytes(b"final")
        child = CancelledChild()
        supervisor.stop_child(child)
        assert child.returncode == -15
        assert supervisor.cleanup_capture_temporary_files(job_root) == 5
        assert not list(geometry.glob("lf_*.exr")) and geometry.exists()
        assert not list(sparse.glob("*.lf-*.tmp"))
        assert (job_root / "dataset" / "images" / "000011.png").is_file()
        assert (job_root / "dataset" / "capture_points_000011.npz").is_file()
        assert (job_root / "dataset" / "journal.jsonl").is_file()
        assert all((sparse / (name + ".bin")).is_file() for name in ("cameras", "images", "points3D"))
        assert (geometry / "unowned.exr").is_file()


check_interrupted_capture_cleanup()


def check_cleanup_enumeration_failure():
    with tempfile.TemporaryDirectory(prefix="lf-render-cleanup-denied-") as directory:
        dataset = Path(directory) / "dataset"
        (dataset / "_geometry_passes").mkdir(parents=True)
        (dataset / "sparse" / "0").mkdir(parents=True)
        from unittest.mock import patch
        with patch.object(Path, "iterdir", side_effect=PermissionError("injected folder denial")):
            assert supervisor.cleanup_capture_temporary_files(directory) == 0


check_cleanup_enumeration_failure()
print("CLEANUP_ENUMERATION_FAILURE_PASS", flush=True)


def check_orphaned_color_view_route():
    source = bpy.data.scenes.new("Deleted color-view source")
    view = bpy.data.scenes.new("Retained trainer color view")
    view[ext.preview_scene.SOURCE] = source
    assert ext.preview_scene.is_preview(view)
    bpy.data.scenes.remove(source, do_unlink=True)
    assert ext.preview_scene.is_preview(view), "A deleted source hid the color-view marker"
    assert ext.preview_scene.source_scene(view) is None

    class Layout:
        def __init__(self):
            self.calls = []

        def box(self):
            return self

        def label(self, **kwargs):
            self.calls.append(("label", kwargs))

        def template_ID(self, data, property):
            self.calls.append(("template_ID", data, property))

    layout = Layout()
    ext.preview_scene.draw_orphaned(layout, SimpleNamespace(window=object()), lambda *_args: None)
    assert any(call[0] == "template_ID" and call[2] == "scene" for call in layout.calls)
    assert any(call[0] == "label" and "source scene removed" in call[1].get("text", "")
               for call in layout.calls)
    bpy.data.scenes.remove(view, do_unlink=True)


check_orphaned_color_view_route()
print("ORPHANED_COLOR_VIEW_ROUTE_PASS", flush=True)


def check_failed_renderer_cleanup():
    class FailedChild:
        returncode = 7

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    with tempfile.TemporaryDirectory(prefix="lf-render-failure-") as directory:
        base = Path(directory)
        root = base / "job"
        source = base / "source.blend"
        source.write_bytes(b"saved source fixture")
        job_file = base / "job.json"
        job = {
            "id": "failed-render-contract", "root": str(root), "source_file": str(source),
            "blender": "blender-fixture", "capture_only": True, "cameras": [],
        }

        def fail_render(*_args, **_kwargs):
            geometry = root / "dataset" / "_geometry_passes"
            sparse = root / "dataset" / "sparse" / "0"
            geometry.mkdir(parents=True)
            sparse.mkdir(parents=True)
            (geometry / ("lf_" + "d" * 32 + "_Depth_000001_view.exr")).write_bytes(b"temp")
            (sparse / ("images.bin.lf-" + "e" * 32 + ".tmp")).write_bytes(b"temp")
            (root / "dataset" / "images").mkdir()
            (root / "dataset" / "images" / "000001.png").write_bytes(b"completed capture")
            (root / "dataset" / "capture_points_000001.npz").write_bytes(b"recovery points")
            return FailedChild()

        supervisor.atomic_json(job_file, job)
        from unittest.mock import patch
        with patch.object(supervisor.subprocess, "Popen", side_effect=fail_render):
            supervisor.supervise(job_file)

        assert supervisor.read_json(root / "state.json", {}).get("state") == "failed"
        assert not list((root / "dataset" / "_geometry_passes").glob("lf_*.exr"))
        assert not list((root / "dataset" / "sparse" / "0").glob("*.lf-*.tmp"))
        assert (root / "dataset" / "images" / "000001.png").read_bytes() == b"completed capture"
        assert (root / "dataset" / "capture_points_000001.npz").read_bytes() == b"recovery points"
        assert not (root / "supervisor.lock").exists()


check_failed_renderer_cleanup()
print("FAILED_RENDERER_CLEANUP_PASS", flush=True)

settings = bpy.context.scene.lichtfeld_training
settings.mode = "CONNECT"
settings.job_directory = ""
settings.previous_job = ""
settings.attached_session = ""

temporary = tempfile.TemporaryDirectory(prefix="lf-review-workflow-")
local_job = Path(temporary.name) / "owned-job"
local_job.mkdir()
(local_job / "job.json").write_text('{"id":"owned-local-job"}', encoding="utf-8")
settings.job_directory = ""

settings.result_object = None

def publish_session():
    transport = importlib.import_module('lichtfeld_sessions.transport')
    publisher = transport.SessionPublisher({
        "label": "Workflow contract trainer", "state": "training",
        "source": "contract", "project": "Return workflow",
    })
    arrays = {
        "means": np.zeros((16, 3), dtype=np.float32),
        "scale": np.full((16, 3), -4, dtype=np.float32),
        "rotation": np.tile(np.array([1, 0, 0, 0], dtype=np.float32), (16, 1)),
        "opacity": np.zeros((16, 1), dtype=np.float32),
        "sh": np.zeros((16, 1, 3), dtype=np.float32),
    }
    publisher.publish(arrays, 12)
    return publisher


def pump_until(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Trainer fixture timed out"
        ext.poll_jobs()
        time.sleep(0.05)


publisher = None
try:
    publisher = publish_session()
    assert bpy.ops.lichtfeld.scan_sessions() == {"FINISHED"}
    pump_until(lambda: any(item["id"] == publisher.id for item in ext._sessions))
    # A completed owned job is selected immediately before attach; do not tick a
    # synthetic local observer for this contract fixture.
    settings.job_directory = str(local_job)
    settings.discovered_session = publisher.id
    assert bpy.ops.lichtfeld.attach_session() == {"FINISHED"}
    assert settings.attached_session and settings.job_directory == ""
    assert settings.previous_job == str(local_job)
    pump_until(lambda: settings.result_object is not None and not settings.importing)
    assert not any(ext.allowed(bpy.context, action) for action in
                   ("pause", "resume", "stop", "import_best", "continue"))
    external_snapshot = settings.result_object

    assert bpy.ops.lichtfeld.detach_session() == {"FINISHED"}
    assert not settings.attached_session and not settings.job_directory
    assert settings.previous_job == str(local_job)
    assert settings.result_object == external_snapshot, "Disconnect discarded the external snapshot"
    assert ext.LF_OT_previous_job.poll(bpy.context), "Return action is unavailable after disconnect"

    settings.discovered_session = publisher.id
    assert bpy.ops.lichtfeld.attach_session() == {"FINISHED"}
    assert settings.previous_job == str(local_job), "Reconnecting discarded the retained local job"
    assert settings.job_directory == ""
    assert bpy.ops.lichtfeld.detach_session() == {"FINISHED"}
    assert settings.previous_job == str(local_job)

    assert bpy.ops.lichtfeld.previous_job() == {"FINISHED"}
    assert settings.job_directory == str(local_job) and not settings.previous_job
    assert settings.result_object == external_snapshot, "Returning replaced the retained snapshot eagerly"
    assert publisher.record["state"] == "training", "Disconnect or return changed the external trainer"
    print("REVIEW_WORKFLOW_ATTACH_DISCONNECT_RETURN_PASS", flush=True)
finally:
    if publisher is not None:
        publisher.finish("stopped")
        publisher.pool.close()
    temporary.cleanup()
