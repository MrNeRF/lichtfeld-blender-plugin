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
destination=Path(sys.argv[sys.argv.index('--')+1]) if '--' in sys.argv else ROOT/'artifacts/capture-format-ui'
destination.mkdir(parents=True, exist_ok=True)
source=bpy.context.scene;s=source.lichtfeld_training
s.scope='SCENE';s.views=4;s.capture_aspect='WIDE';s.resolution=1024
ext.capture_settings.apply_format(source, ext.capture_settings.preset_format(s))
bpy.context.window_manager.lichtfeld_panels.camera_overrides_open=True;s.override_optics=True;s.capture_lens=35
operator=SimpleNamespace(collection=None,rig_backup=[],committed=False,report=lambda *args:None)
for _ in ext.LF_OT_rig.generate(operator,bpy.context):pass
bpy.context.preferences.view.show_splash=False
area=next(a for a in bpy.context.window.screen.areas if a.type=='VIEW_3D')
area.spaces.active.show_region_ui=True
step=0
first_view=None
excluded_camera=None
def tick():
    global step, first_view, excluded_camera
    if s.operation_busy or (ext.capture_preview.is_preview(bpy.context.scene) and ext.capture_preview.camera_pending(bpy.context.scene)):
        return .05
    if step==0:
        for region in area.regions:
            if region.type=='UI':
                with bpy.context.temp_override(window=bpy.context.window,area=area,region=region):
                    region.active_panel_category='LichtFeld'
    elif step==1:
        bpy.ops.screen.screenshot(filepath=str(destination/'01-setup.png'))
        with bpy.context.temp_override(area=area):
            assert bpy.ops.lichtfeld.capture_framing()=={'FINISHED'}
    elif step==2:
        bpy.ops.screen.screenshot(filepath=str(destination/'02-landscape.png'))
        first_view = area.spaces.active.region_3d.view_matrix.copy()
        bpy.context.window_manager.lichtfeld_panels.framing_camera = 2
    elif step==3:
        preview = bpy.context.scene
        region = area.spaces.active.region_3d
        assert preview['capture_camera_index'] == 2
        assert region.view_perspective == 'CAMERA' and region.view_matrix != first_view
        excluded_camera = preview[ext.capture_preview.ORIGINAL_CAMERA]
        with bpy.context.temp_override(area=area):
            assert bpy.ops.lichtfeld.toggle_capture_camera() == {'FINISHED'}
    elif step==4:
        preview = bpy.context.scene
        assert excluded_camera[ext.preflight.CAMERA_EXCLUDED]
        assert preview['capture_excluded_count'] == 1
        bpy.context.window_manager.lichtfeld_panels.framing_camera = 3
        assert ext.capture_preview.camera_pending(preview)
        # Run the history callbacks before Blender's timer advances the queued generator.
        ext.history_changing(None)
        assert not ext.capture_preview.camera_pending(preview)
        ext.history_changed(None)
    elif step==5:
        assert bpy.context.scene['capture_camera_index'] == 2
        assert s.capture_preview_index == 2
        bpy.context.window_manager.lichtfeld_panels.framing_camera = 3
    elif step==6:
        assert bpy.context.scene['capture_camera_index'] == 3
        assert area.spaces.active.region_3d.view_perspective == 'CAMERA'
        s.capture_orientation='PORTRAIT'
        ext.capture_settings.apply_format(source, ext.capture_settings.preset_format(s))
        with bpy.context.temp_override(area=area):
            assert bpy.ops.lichtfeld.capture_framing()=={'FINISHED'}
    elif step==7:
        assert area.spaces.active.region_3d.view_perspective == 'CAMERA'
        bpy.ops.screen.screenshot(filepath=str(destination/'03-portrait.png'))
        area.spaces.active.region_3d.view_perspective = 'PERSP'
        bpy.context.window_manager.lichtfeld_panels.framing_camera = 4
    elif step==8:
        assert bpy.context.scene['capture_camera_index'] == 4
        assert area.spaces.active.region_3d.view_perspective == 'PERSP'
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
