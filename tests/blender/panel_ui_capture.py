# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture the real Blender sidebar in an isolated, self-closing QA instance."""
from pathlib import Path
from types import SimpleNamespace
import sys
import bpy
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
bpy.ops.preferences.addon_enable(module='lichtfeld_training')
import lichtfeld_training as ext
area=ROOT/'artifacts/panel-ui';area.mkdir(exist_ok=True)
scene=bpy.context.scene;s=scene.lichtfeld_training
prefs=ext.preferences();prefs.backend_path=str(ROOT.parent/'build/LichtFeld-Studio.exe');prefs.jobs_directory=str(area/'jobs')
s.scope='SCENE';s.views=4
operator=SimpleNamespace(collection=None,rig_backup=[],committed=False,report=lambda *args:None)
for _ in ext.LF_OT_rig.generate(operator,bpy.context):pass
for screen in [bpy.context.window.screen]:
    for editor in screen.areas:
        if editor.type=='VIEW_3D':
            editor.spaces.active.show_region_ui=True
            editor.spaces.active.region_3d.view_distance=8
status={'state':'training','control_available':True,'iteration':1250,'iterations':3000,
        'best':{'iteration':1000},'job':{'id':'ui-fixture'}}
fake=SimpleNamespace(status=status,reader=SimpleNamespace(error=''))
previous_tick=ext.ui_runtime.tick;previous_peek=ext.ui_runtime.peek
bpy.context.preferences.view.show_splash=False
step=-1
def capture():
    global step
    if step==-1:
        for screen in [bpy.context.window.screen]:
            for editor in screen.areas:
                if editor.type=='VIEW_3D':
                    for region in editor.regions:
                        if region.type=='UI':
                            with bpy.context.temp_override(window=bpy.context.window,area=editor,region=region):
                                region.active_panel_category='LichtFeld'
    elif step==0:
        bpy.ops.screen.screenshot(filepath=str(area/'01-capture-setup.png'))
        ext.ui_runtime.tick=lambda: .02
        frame={'count':1,'record':{'id':'ui','iteration':1200,'kind':'preview'},
               'job':{'id':'ui-fixture','sources':['Cube']},'transport':'fixture',
               'data':[('position','FLOAT_VECTOR','vector',np.zeros(3,np.float32))]}
        for _ in ext.ui_runtime.build(scene,frame,'ui'):pass
        s.job_directory='D:/example/training-job';s.status='Training';s.running=True;s.progress=1250/3000
        s.importing=False;ext.ui_runtime.peek=lambda _:fake
    elif step==1:
        bpy.ops.screen.screenshot(filepath=str(area/'02-training.png'))
        s.importing=True
    elif step==2:
        bpy.ops.screen.screenshot(filepath=str(area/'03-snapshot-updating.png'))
        s.importing=False;s.running=False;s.status='Completed';status['state']='completed';status['checkpoint_available']=True
    elif step==3:
        bpy.ops.screen.screenshot(filepath=str(area/'04-training-finished.png'))
    elif step==4:
        s.running=True;s.status='Training';status['state']='training'
        editor=next(a for a in bpy.context.window.screen.areas if a.type=='VIEW_3D')
        for _ in ext.preview_scene.open_steps(scene,bpy.context.window,editor):pass
    elif step==5:
        bpy.ops.screen.screenshot(filepath=str(area/'05-trainer-colors.png'))
    else:
        assert (area/'02-training.png').read_bytes()==(area/'03-snapshot-updating.png').read_bytes()
        print('REAL_PANEL_SCREENSHOTS_CAPTURED',flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step+=1
    for screen in bpy.data.screens:
        for editor in screen.areas:editor.tag_redraw()
    return 1.5
bpy.app.timers.register(capture,first_interval=3)
bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None,first_interval=30)
