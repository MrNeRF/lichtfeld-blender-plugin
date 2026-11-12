# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Installed extension: additive import, full SH, cancellation and preview guard."""
import importlib
import tempfile
import threading
import hashlib
import shutil
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import bpy
import numpy as np
import ssl
from lichtfeld_portal import http

context = http._system_ssl_context()
assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED

NAME = "bl_ext.user_default.lichtfeld_training"
bpy.ops.preferences.addon_enable(module=NAME)
extension = importlib.import_module(NAME)
portal = extension.portal_ui
scene = bpy.context.scene
source = scene.objects.get("Cube")
source["preserve"] = "original"
frames = []
parameters = []


def fields_on_worker():
    from importlib import import_module
    reader = import_module(NAME + ".session_reader")
    arrays = {"means": np.array([[1, 2, 3], [4, 5, 6]], dtype=np.float32),
              "scale": np.full((2, 3), -4, dtype=np.float32),
              "rotation": np.array([[1, 0, 0, 0], [1, 0, 0, 0]], dtype=np.float32),
              "opacity": np.zeros((2, 1), dtype=np.float32),
              "sh": np.full((2, 16, 3), 0.25, dtype=np.float32)}
    frames.append(reader.blender_fields(arrays))
    parameters.append(arrays)


worker = threading.Thread(target=fields_on_worker)
worker.start()
worker.join()  # Test setup; production only queues immutable worker results.

# A malformed but valid JSON checkpoint must not bypass download/export/decode.
with tempfile.TemporaryDirectory() as directory:
    runtime = portal.Runtime(directory)
    identifier = "ef4cb85d-b868-4e51-a0de-bac3bfa269ed"
    identity = ("owner@example.test", "session")
    account = SimpleNamespace(base_url="https://portal.lichtfeld.io",
        snapshot=lambda: SimpleNamespace(email=identity[0], connected_since=identity[1]))
    owner = hashlib.sha256(repr((account.base_url, *identity)).encode("utf-8")).hexdigest()
    cache = runtime.cache_directory / "assets" / owner / identifier
    cache.mkdir(parents=True)
    (cache / "download.json").write_text("[]")
    executable = Path(directory) / "test-backend"
    executable.touch()
    from importlib import import_module
    bridge = import_module(NAME + ".bridge_core")
    record = bridge.write_splat(directory, parameters[0], 1, "test")

    def download(identifier, project, **options):
        assert options["checkpoint"] is None and options["reuse_completed"]
        project.write_bytes(b"fake download; exporter is the boundary under test")

    def backend(arguments, log, cancel, timeout):
        if arguments[-1] == "--help":
            log.write(b"--export-ply")
        else:
            shutil.copyfile(Path(directory) / record["path"], arguments[-1])
        return 0

    try:
        with patch.object(runtime, "_client", return_value=SimpleNamespace(expected_session=identity, download=download)), \
             patch.object(runtime, "_backend", side_effect=backend):
            runtime._download(account, identifier, str(executable), cancel=runtime.cancel)
        kind, payload = runtime.events.get_nowait()
        assert kind == "import" and payload[3] == 2
        assert runtime.snapshot()["phase"] == "ready"
    finally:
        runtime.close()

with tempfile.TemporaryDirectory() as directory:
    portal._runtime = portal.Runtime(directory)
    identifier = "ef4cb85d-b868-4e51-a0de-bac3bfa269ed"
    portal._runtime.update(rows=({"id": identifier, "title": "Portal test", "description": ""},))
    clouds = len(bpy.data.pointclouds)
    incomplete = extension.ui_runtime.pointcloud_steps(frames[0], 2)
    next(incomplete)
    incomplete.close()
    assert len(bpy.data.pointclouds) == clouds
    operation = portal.import_steps(scene, identifier, 2, frames[0])
    for _ in operation:
        pass
    imported = bpy.context.view_layer.objects.active
    assert imported.type == "POINTCLOUD"
    assert imported.data.type == "GAUSSIAN_SPLAT"
    assert len(imported.data.points) == 2
    assert imported.get("lichtfeld_portal_scene") == identifier
    assert "lichtfeld_job_id" not in imported
    assert imported.data.attributes.get("radiance:sh_14")
    assert imported.data.attributes.get("lichtfeld_rgb_tail_5")
    assert source["preserve"] == "original" and source.name in scene.objects
    assert scene.lichtfeld_training.result_object is None
    assert imported.matrix_world[1][1] == -1 and imported.matrix_world[2][2] == -1
    portal._destination = scene.session_uid
    portal._import_cancel = threading.Event()
    portal._frame = (identifier, 2, frames[0])
    portal._stage = portal.import_steps(scene, *portal._frame)
    cloud_count = len(bpy.data.pointclouds)
    next(portal._stage)
    portal.before_history()
    assert portal._stage is None and portal._pending is portal._frame
    assert len(bpy.data.pointclouds) == cloud_count
    assert not portal._import_cancel.is_set()  # Undo releases RNA, not the download.
    portal.cancel_import()
    portal._runtime.cancel = threading.Event()  # A later action must not revive the old import.
    assert portal._pending is None and portal._destination is None
    canceled = portal._import_cancel
    portal._destination = scene.session_uid
    portal._import_cancel = threading.Event()
    portal._runtime.events.put(("import", (None, canceled, identifier, 2, frames[0])))
    portal._last_online = False
    portal.tick_steps()
    assert portal._pending is None and portal._stage is None
    portal.invalidate()
    assert imported.name in scene.objects
    portal._runtime.close()
    portal._runtime = None
bpy.ops.preferences.addon_disable(module=NAME)
assert not hasattr(bpy.types.WindowManager, "lichtfeld_portal")
print("PORTAL_GALLERY_NATIVE_OK", flush=True)
