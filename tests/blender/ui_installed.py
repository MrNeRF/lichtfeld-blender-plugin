# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Inspect the installed ZIP in an isolated Blender profile."""

from pathlib import Path
import importlib
import sys
import bpy

area = Path(sys.argv[sys.argv.index("--") + 1])
extension = importlib.import_module("bl_ext.user_default.lichtfeld_training")
assert extension.__package__ in bpy.context.preferences.addons
bpy.ops.wm.open_mainfile(filepath=str(area / "smoke-result.blend"))
prefs = extension.preferences()
prefs.backend_path = str(area / "build/LichtFeld-Studio.exe")
prefs.jobs_directory = str(area / "jobs")
bpy.context.scene.lichtfeld_training.job_directory = (area / "smoke-job.txt").read_text()
bpy.context.preferences.view.show_splash = False
print("INSTALLED_UI_READY", extension.__package__, flush=True)
