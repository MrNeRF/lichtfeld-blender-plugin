# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Installed extension: additive import, full SH, cancellation and preview guard."""
import importlib
import tempfile
import threading
import shutil
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import bpy
import numpy as np
import ssl
from lichtfeld_portal import http
from lichtfeld_portal.path_io import Path as IOPath, TemporaryDirectory
from lichtfeld_portal.private_directory import locked_sidecar

context = http._system_ssl_context()
assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED

NAME = "bl_ext.user_default.lichtfeld_training"
bpy.ops.preferences.addon_enable(module=NAME)
extension = importlib.import_module(NAME)
portal = extension.portal_ui
brand = portal._icons["lichtfeld"]
portal.clear_thumbnails()
assert portal._icons["lichtfeld"] is brand
browser = bpy.context.window_manager.lichtfeld_portal
assert bpy.context.window_manager.lichtfeld_workspace == "PORTAL"
bpy.context.window_manager.lichtfeld_workspace = "TRAINING"
bpy.context.window_manager.lichtfeld_workspace = "PORTAL"
settings = bpy.context.scene.lichtfeld_training
settings.job_directory = "remembered-job"
bpy.context.window_manager.property_unset("lichtfeld_workspace")
portal.select_workspace(bpy.context)
assert bpy.context.window_manager.lichtfeld_workspace == "TRAINING"
# Explicit Portal navigation must not be stolen by a delayed job recovery.
bpy.context.window_manager.lichtfeld_workspace = "PORTAL"
portal.select_workspace(bpy.context)
assert bpy.context.window_manager.lichtfeld_workspace == "PORTAL"
settings.job_directory = ""
bpy.context.window_manager.property_unset("lichtfeld_workspace")
portal.select_workspace(bpy.context)
assert bpy.context.window_manager.lichtfeld_workspace == "PORTAL"
with TemporaryDirectory() as directory:
    filename = str(IOPath(directory) / "workspace.blend")
    bpy.context.scene.lichtfeld_training.camera_collection = bpy.data.collections.new("Saved capture")
    bpy.context.window_manager.lichtfeld_workspace = "TRAINING"
    bpy.ops.wm.save_as_mainfile(filepath=filename)
    bpy.context.window_manager.lichtfeld_workspace = "PORTAL"
    bpy.ops.wm.open_mainfile(filepath=filename)
    assert bpy.context.window_manager.lichtfeld_workspace == "TRAINING"
bpy.context.scene.lichtfeld_training.camera_collection = None
bpy.context.window_manager.lichtfeld_workspace = "PORTAL"
browser = bpy.context.window_manager.lichtfeld_portal
for number in range(13):
    item = browser.rows.add()
    item.identifier, item.title = str(number), f"Project {number}"
assert portal.gallery_page(bpy.context)[:2] == (list(range(6)), 13)
browser.page = 99
assert browser.page == 3 and portal.gallery_page(bpy.context)[0] == [12]
browser.search = "Project 12"
assert browser.page == 1 and portal.gallery_page(bpy.context)[:2] == ([12], 1)
browser.rows.clear()
browser.search = ""
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
with TemporaryDirectory() as directory:
    # Exercise the installed Blender host's Windows MAX_PATH boundary as well.
    runtime = portal.Runtime(directory, Path(directory).joinpath(*(["nested-cache-" + "x" * 38] * 5)))
    identifier = "ef4cb85d-b868-4e51-a0de-bac3bfa269ed"
    identity = ("owner@example.test", "session")
    account = SimpleNamespace(base_url="https://portal.lichtfeld.io",
        snapshot=lambda: SimpleNamespace(email=identity[0], connected_since=identity[1], signed_in=True))
    owner = runtime.cache_owner(account, identity)
    cache = runtime.cache_directory / "assets" / owner / identifier
    cache.mkdir(parents=True)
    (cache / "download.json").write_text("[]")
    executable = Path(directory) / "test-backend"
    executable.touch()
    from importlib import import_module
    bridge = import_module(NAME + ".bridge_core")
    record = bridge.write_splat(directory, parameters[0], 1, "test")

    downloads = []

    def download(identifier, project, **options):
        assert options["checkpoint"] is None and options["reuse_completed"]
        downloads.append(identifier)
        project.write_bytes(b"fake download; exporter is the boundary under test")

    client = SimpleNamespace(expected_session=identity, download=download)
    canceled = threading.Event()
    canceled.set()
    with patch.object(runtime, "_client", return_value=client):
        try:  # The real child supervisor stops before starting the probe.
            runtime._download(account, identifier, str(executable), cancel=canceled)
            raise AssertionError("A canceled import started the capability probe")
        except InterruptedError:
            assert not downloads

    calls, older = [], [(2, b"Flag could not be matched: capabilities"),
                        (0, b"Input: .licht (project)"),
                        (0, b"Unsupported feature: visible-project-splats-v1\n"),
                        (0, b"visible-project-splats-v1\n")]

    def backend(arguments, log, cancel, timeout):
        calls.append(arguments[1:])
        if arguments[-1] == "--capabilities":
            # Older builds reject the option; a build ignoring it must not pass either.
            code, text = older.pop(0) if older else (0, b"other-feature-v2\r\nvisible-project-splats-v1\r\nproject-crops-rad-v1\r\n")
            log.write(text)
            return code
        if exporter_fails:
            log.write(b"Error: The project has no visible splats to export.")
            return 1
        shutil.copyfile(Path(directory) / record["path"], IOPath(arguments[-1]))
        return 0

    exporter_fails = True
    try:
        with patch.object(runtime, "_client", return_value=client), \
             patch.object(runtime, "_backend", side_effect=backend):
            while older:
                try:
                    runtime._download(account, identifier, str(executable), cancel=runtime.cancel)
                    raise AssertionError("An older convert was accepted for Portal projects")
                except ValueError as error:
                    assert "Update LichtFeld Studio" in str(error)
                assert not downloads and calls[-1] == ["convert", "--capabilities"]
            try:
                runtime._download(account, identifier, str(executable), cancel=runtime.cancel)
                raise AssertionError("A failed export was imported")
            except ValueError as error:
                assert "could not prepare this project" in str(error)
                assert "no visible splats" in runtime.snapshot()["details"]
            exporter_fails = False
            with locked_sidecar(cache / "download.lock"):
                try:
                    runtime._download(account, identifier, str(executable), cancel=runtime.cancel)
                    raise AssertionError("A second import bypassed the existing asset lock")
                except ValueError as error:
                    assert "cache is in use or unavailable" in str(error)
            runtime._download(account, identifier, str(executable), cancel=runtime.cancel)
        assert calls[-1][:2] == ["convert", "--no-provenance"] and calls[-1][-1].endswith("asset.ply")
        kind, payload = runtime.events.get_nowait()
        assert kind == "import" and payload[3] == 2
        assert runtime.snapshot()["phase"] == "ready"
    finally:
        runtime.close()

with TemporaryDirectory() as directory:
    portal._runtime = portal.Runtime(directory)
    identifier = "ef4cb85d-b868-4e51-a0de-bac3bfa269ed"
    portal._runtime.update(rows=({"id": identifier, "title": "Portal test", "description": ""},))
    identity = ("owner@example.test", "session")
    portal._identity = identity
    portal._refresh_wanted = False
    portal._runtime.update(account=SimpleNamespace(email=identity[0], connected_since=identity[1],
        signed_in=True), rows_identity=identity, bump_generation=True)
    image_path = IOPath(directory).joinpath(*(["thumbnail-" + "x" * 40] * 5), "image.png")
    image_path.parent.mkdir(parents=True)
    shutil.copyfile(Path(extension.__file__).parent / "assets/lichtfeld-icon.png", image_path)
    image = str(image_path)
    portal._runtime.events.put(("thumbnail", (identity, identifier, image)))
    portal.tick_steps()
    thumbnail = portal._icons[identifier]
    assert list(thumbnail.image_size) == [128, 128]
    icon_id = thumbnail.icon_id
    portal._runtime.update(bump_generation=True)
    portal.tick_steps()
    assert portal._icons[identifier] is thumbnail  # Refresh must not invalidate a drawn icon.
    portal._runtime.events.put(("thumbnail", (identity, identifier, image)))
    portal.tick_steps()
    assert portal._icons[identifier].icon_id == icon_id
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
    # Same world directions as native LichtFeld drag-out into Blender, rather
    # than the different transform recorded for a Blender capture roundtrip.
    from mathutils import Vector
    assert imported.matrix_world.to_3x3() @ Vector((0, -1, 0)) == Vector((0, 0, 1))
    assert imported.matrix_world.to_3x3() @ Vector((0, 0, 1)) == Vector((0, 1, 0))
    assert imported.matrix_world.to_3x3().determinant() == 1
    np.testing.assert_allclose(extension.session_geometry.source_model_transform(), np.diag([1, -1, -1, 1]))
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
assert not hasattr(bpy.types.WindowManager, "lichtfeld_workspace")
print("PORTAL_GALLERY_NATIVE_OK", flush=True)
