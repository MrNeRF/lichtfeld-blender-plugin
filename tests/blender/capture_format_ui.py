# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Self-closing, isolated GUI capture of format/override framing controls."""
from pathlib import Path
from types import SimpleNamespace
import sys
import bpy
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
bpy.ops.preferences.addon_enable(module='lichtfeld_training')
import lichtfeld_training as ext
destination=ROOT/'artifacts/capture-format-ui';destination.mkdir(exist_ok=True)
source=bpy.context.scene;s=source.lichtfeld_training
s.scope='SCENE';s.views=4;s.capture_aspect='WIDE';s.resolution=1024
ext.capture_settings.apply_format(source, ext.capture_settings.preset_format(s))
s.camera_overrides_open=True;s.override_optics=True;s.capture_lens=35
operator=SimpleNamespace(collection=None,rig_backup=[],committed=False,report=lambda *args:None)
for _ in ext.LF_OT_rig.generate(operator,bpy.context):pass
bpy.context.preferences.view.show_splash=False
area=next(a for a in bpy.context.window.screen.areas if a.type=='VIEW_3D')
area.spaces.active.show_region_ui=True
step=0
def tick():
    global step
    if step==0:
        for region in area.regions:
            if region.type=='UI':
                with bpy.context.temp_override(window=bpy.context.window,area=area,region=region):
                    region.active_panel_category='LichtFeld'
    elif step==1:
        bpy.ops.screen.screenshot(filepath=str(destination/'01-setup.png'))
        for _ in ext.capture_preview.open_steps(source,bpy.context.window,area):pass
    elif step==2:
        bpy.ops.screen.screenshot(filepath=str(destination/'02-landscape.png'))
        s.capture_orientation='PORTRAIT'
        ext.capture_settings.apply_format(source, ext.capture_settings.preset_format(s))
        for _ in ext.capture_preview.open_steps(source,bpy.context.window,area):pass
    elif step==3:
        bpy.ops.screen.screenshot(filepath=str(destination/'03-portrait.png'))
        with bpy.context.temp_override(area=area):
            assert bpy.ops.lichtfeld.close_capture_framing()=={'FINISHED'}
        assert bpy.context.scene==source
    else:
        print('CAPTURE_FORMAT_GUI_SCREENSHOTS_PASS',flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step+=1
    for editor in bpy.context.window.screen.areas:editor.tag_redraw()
    return 1.5
bpy.app.timers.register(tick,first_interval=3)
bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None,first_interval=25)
