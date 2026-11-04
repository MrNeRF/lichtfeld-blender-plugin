# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import argparse,bpy,importlib,sys,time,traceback
parser=argparse.ArgumentParser(description='Installed extension settings in a disposable GUI profile')
parser.add_argument('area',type=Path)
parser.add_argument('backend',type=Path)
options=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
ext=importlib.import_module('bl_ext.user_default.lichtfeld_training')
area=options.area.resolve();area.mkdir(parents=True,exist_ok=True)
bpy.context.preferences.view.show_splash=False
s=bpy.context.scene.lichtfeld_training
prefs=ext.preferences();prefs.backend_path=str(options.backend.resolve());prefs.jobs_directory=str(area/'jobs')
s.job_directory='';s.attached_session=''
assert s.engine=='CYCLES',s.engine
s.views=48
bpy.ops.object.select_all(action='DESELECT');bpy.data.objects['Cube'].select_set(True);bpy.context.view_layer.objects.active=bpy.data.objects['Cube']
bpy.ops.lichtfeld.create_rig()
view=max(bpy.context.window.screen.areas,key=lambda a:a.width*a.height);view.type='VIEW_3D'
state={'step':0,'start':time.monotonic()}
def popup():
 with bpy.context.temp_override(area=view,region=next(r for r in view.regions if r.type=='WINDOW')):bpy.ops.wm.call_panel(name='LF_PT_training',keep_open=True)
def tick():
 try:
  assert time.monotonic()-state['start']<40
  if s.operation_busy:return .1
  if state['step']==0:
   bpy.ops.wm.save_as_mainfile(filepath=str(area/'settings.blend'));state['step']=1;return 1
  if state['step']==1:
   if ext.preflight.issues(ext.preflight.cached(bpy.context,prefs)):return .1
   popup();state['step']=2;return 1
  if state['step']==2:
   bpy.ops.screen.screenshot(filepath=str(area/'settings-ready.png'))
   print('SETTINGS_UI_CYCLES_DEFAULT_PASS',flush=True);bpy.ops.wm.quit_blender();return None
 except Exception:
  traceback.print_exc();bpy.ops.wm.quit_blender();return None
bpy.app.timers.register(tick,first_interval=1)
