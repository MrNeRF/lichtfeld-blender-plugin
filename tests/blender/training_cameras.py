# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare exported intrinsics with Blender's own camera projection."""

from pathlib import Path
import sys
import bpy
import numpy as np
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lichtfeld_training"))
from render_worker import intrinsics

scene = bpy.context.scene
camera = scene.camera
scene.render.resolution_x = 160
scene.render.resolution_y = 120
scene.render.resolution_percentage = 100
camera.data.shift_x = 0.12
camera.data.shift_y = -0.08
camera.data.lens = 42
for sensor_fit in ("AUTO", "HORIZONTAL", "VERTICAL"):
    for aspect in (1, 1.25):
        scene.render.pixel_aspect_x = aspect
        scene.render.pixel_aspect_y = 1
        camera.data.sensor_fit = sensor_fit
        bpy.context.view_layer.update()
        k = intrinsics(scene, camera, 160, 120)
        for local in (Vector((0.1, -0.3, -3)), Vector((-0.4, 0.2, -5))):
            world = camera.matrix_world @ local
            ndc = world_to_camera_view(scene, camera, world)
            reference = (ndc.x * 160, (1 - ndc.y) * 120)
            exported = (k["fl_x"] * local.x / -local.z + k["cx"],
                        -k["fl_y"] * local.y / -local.z + k["cy"])
            np.testing.assert_allclose(exported, reference, atol=1e-4)
        print("CAMERA_PROJECTION_VERIFIED", sensor_fit, aspect, flush=True)
