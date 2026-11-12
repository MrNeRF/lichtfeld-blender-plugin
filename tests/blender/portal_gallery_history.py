# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Foreground native Undo/Redo preserves prior edits and restores the Portal splat."""
import importlib
import tempfile
import bpy
import numpy as np

NAME = "bl_ext.user_default.lichtfeld_training"
bpy.ops.preferences.addon_enable(module=NAME)
extension = importlib.import_module(NAME)
portal = extension.portal_ui
bpy.context.preferences.view.show_splash = False


def run():
    area = next(area for area in bpy.context.window.screen.areas if area.type == "VIEW_3D")
    region = next(region for region in area.regions if region.type == "WINDOW")
    try:
        with bpy.context.temp_override(area=area, region=region):
            with tempfile.TemporaryDirectory() as directory:
                if portal._runtime:
                    portal._runtime.close()
                portal._runtime = portal.Runtime(directory)
                identifier = "ef4cb85d-b868-4e51-a0de-bac3bfa269ed"
                from importlib import import_module
                reader = import_module(NAME + ".session_reader")
                arrays = {"means": np.zeros((2, 3), dtype=np.float32),
                          "scale": np.full((2, 3), -4, dtype=np.float32),
                          "rotation": np.array([[1, 0, 0, 0], [1, 0, 0, 0]], dtype=np.float32),
                          "opacity": np.zeros((2, 1), dtype=np.float32),
                          "sh": np.zeros((2, 1, 3), dtype=np.float32)}
                fields = reader.blender_fields(arrays)
                bpy.ops.ed.undo_push(message="Before unrelated edit")
                bpy.context.scene.objects["Cube"]["prior_edit"] = 42
                bpy.ops.ed.undo_push(message="Unrelated edit")
                uid = bpy.context.scene.session_uid
                for _ in portal.import_steps(bpy.context.scene, identifier, 2, fields):
                    pass
                bpy.ops.ed.undo()
                assert bpy.context.scene.session_uid == uid
                assert bpy.context.scene.objects["Cube"]["prior_edit"] == 42
                assert not any(obj.get("lichtfeld_portal_scene") == identifier for obj in bpy.context.scene.objects)
                bpy.ops.ed.redo()
                assert any(obj.get("lichtfeld_portal_scene") == identifier for obj in bpy.context.scene.objects)
                assert bpy.context.scene.objects["Cube"]["prior_edit"] == 42
                portal._runtime.close()
                portal._runtime = None
        print("PORTAL_HISTORY_OK", flush=True)
    except Exception:
        import traceback
        traceback.print_exc()
        print("PORTAL_HISTORY_FAILED", flush=True)
    finally:
        bpy.ops.wm.quit_blender()


bpy.app.timers.register(run, first_interval=1)
