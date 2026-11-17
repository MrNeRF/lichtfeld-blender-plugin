# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Open the installed native browser in an isolated UI qualification profile."""
import importlib
import sys
from pathlib import Path
import bpy

bpy.ops.preferences.addon_enable(module="bl_ext.user_default.lichtfeld_training")
extension = importlib.import_module("bl_ext.user_default.lichtfeld_training")
bpy.context.preferences.view.show_splash = False
root = Path(bpy.utils.user_resource("CONFIG")).parent
if "--" in sys.argv:
    extension.preferences().backend_path = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.save_as_mainfile(filepath=str(root / "portal-gallery-ui.blend"))


def open_browser():
    area = next(area for area in bpy.context.window.screen.areas if area.type == "VIEW_3D")
    area.spaces.active.show_region_ui = True
    area.spaces.active.region_3d.view_distance = 12
    region = next(region for region in area.regions if region.type == "WINDOW")
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.lichtfeld.portal_gallery("INVOKE_DEFAULT")
    print("PORTAL_GALLERY_UI_READY", flush=True)


bpy.app.timers.register(open_browser, first_interval=1.5)
