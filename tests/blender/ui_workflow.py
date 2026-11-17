# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import argparse,bpy,importlib,sys,time,traceback
parser=argparse.ArgumentParser(description='Installed extension workflow in a disposable GUI profile')
parser.add_argument('area',type=Path)
parser.add_argument('backend',type=Path)
parser.add_argument('--installed',action='store_true')
options=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
area=options.area.resolve();area.mkdir(parents=True,exist_ok=True)
ext=importlib.import_module('bl_ext.user_default.lichtfeld_training')
bpy.context.window_manager.lichtfeld_workspace = 'TRAINING'
prefs=ext.preferences();prefs.backend_path=str(options.backend.resolve());prefs.jobs_directory=str(area/'jobs')
bpy.context.preferences.view.show_splash=False
view=max(bpy.context.window.screen.areas,key=lambda a:a.width*a.height)
view.type='VIEW_3D'
with bpy.context.temp_override(area=view):bpy.ops.screen.screen_full_area(use_hide_panels=False)
settings=bpy.context.scene.lichtfeld_training
settings.quality='DRAFT';settings.views=8;settings.resolution=64;settings.samples=1;settings.max_points=2000
settings.preview_points=1000;settings.preview_seconds=1;settings.iterations=5000;settings.eval_interval=1000;settings.engine='BLENDER_EEVEE'
ext.capture_settings.apply_format(bpy.context.scene, ext.capture_settings.preset_format(settings))
bpy.ops.object.select_all(action='DESELECT');source=bpy.data.objects.get('Cube');source.select_set(True);bpy.context.view_layer.objects.active=source
bpy.ops.lichtfeld.create_rig()
state={'step':0,'start':time.monotonic()}
def popup():
 view=max(bpy.context.window.screen.areas,key=lambda a:a.width*a.height)
 with bpy.context.temp_override(area=view,region=next(r for r in view.regions if r.type=='WINDOW')):
  bpy.ops.wm.call_panel(name='LF_PT_lichtfeld',keep_open=True)
def snap(name):
 bpy.ops.screen.screenshot(filepath=str(area/(name+'.png')));print('AUDIT_CAPTURE',name,flush=True)
def tick():
 try:
  if time.monotonic()-state['start']>240:raise RuntimeError('Audit timeout')
  step=state['step'];settings=bpy.context.scene.lichtfeld_training
  if step==0:
   if settings.operation_busy:return .25
   bpy.ops.wm.save_as_mainfile(filepath=str(area/'audit-source.blend'))
   state['step']=10;return 1
  if step==10:
   if ext.preflight.issues(ext.preflight.cached(bpy.context,prefs)):return .2
   popup();state['step']=1;return 1
  if step==1:
   snap('03-ready-popover');bpy.ops.lichtfeld.start_training();popup();state['step']=2;return 1
  if step==2:
   snap('04-preparing-popover');state['step']=3;return .25
  if step==3:
   if settings.running and settings.result_object and settings.status.lower()=='training':
    popup();state['step']=4;return .5
   return .25
  if step==4:
   snap('05-training-popover');bpy.ops.lichtfeld.training_command(action='pause');state['step']=5;return .25
  if step==5:
   if not settings.paused:return .25
   popup();state['step']=6;return .5
  if step==6:
   snap('06-paused-popover');bpy.ops.lichtfeld.training_command(action='resume');state['step']=7;return .25
  if step==7:
   if settings.running or settings.importing:return .25
   popup();state['step']=8;return 1
  if step==8:
   snap('07-completed-popover');bpy.ops.wm.save_as_mainfile(filepath=str(area/'audit-result.blend'))
   print('AUDIT_COMPLETE',settings.status,settings.detail,flush=True);state['step']=9;return 1
  if step==9:bpy.ops.wm.quit_blender();return None
 except Exception:
  traceback.print_exc();bpy.ops.wm.quit_blender();return None
bpy.app.timers.register(tick,first_interval=3)
