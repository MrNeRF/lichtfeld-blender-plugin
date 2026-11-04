# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local UI inspection fixture; use with an isolated Blender config."""

from pathlib import Path
import sys
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
bpy.ops.preferences.addon_enable(module="lichtfeld_training")
import lichtfeld_training as extension

area = Path(sys.argv[sys.argv.index("--") + 1])
prefs = extension.preferences()
prefs.backend_path = str(area / "build/LichtFeld-Studio.exe")
prefs.jobs_directory = str(area / "jobs")
bpy.context.preferences.view.show_splash = False
for screen in bpy.data.screens:
    for editor in screen.areas:
        if editor.type == "VIEW_3D":
            editor.spaces.active.show_region_ui = True
print("UI_FIXTURE_READY", flush=True)
