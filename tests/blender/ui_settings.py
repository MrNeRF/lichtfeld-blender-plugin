# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import argparse,bpy,importlib,sys,time,traceback,json
parser=argparse.ArgumentParser(description='Installed extension settings in a disposable GUI profile')
parser.add_argument('area',type=Path)
parser.add_argument('backend',type=Path)
parser.add_argument('--installed',action='store_true')
options=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
ext=importlib.import_module('bl_ext.user_default.lichtfeld_training')
area=options.area.resolve();area.mkdir(parents=True,exist_ok=True)
bpy.context.preferences.view.show_splash=False
s=bpy.context.scene.lichtfeld_training
prefs=ext.preferences();prefs.backend_path=str(options.backend.resolve());prefs.jobs_directory=str(area/'jobs')
s.job_directory='';s.attached_session=''
s.engine='CYCLES';bpy.context.scene.cycles.device='CPU'
s.views=48
bpy.ops.object.select_all(action='DESELECT');bpy.data.objects['Cube'].select_set(True);bpy.context.view_layer.objects.active=bpy.data.objects['Cube']
bpy.ops.lichtfeld.create_rig()
view=max(bpy.context.window.screen.areas,key=lambda a:a.width*a.height);view.type='VIEW_3D'
state={'step':0,'start':time.monotonic()}
def popup():
 with bpy.context.temp_override(area=view,region=next(r for r in view.regions if r.type=='WINDOW')):bpy.ops.wm.call_panel(name='LF_PT_training',keep_open=True)

def snapshot(name):
 # Programmatic RNA changes do not necessarily invalidate the displayed region.
 for current in bpy.context.screen.areas:current.tag_redraw()
 bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP',iterations=1)
 bpy.ops.screen.screenshot(filepath=str(area/name))
def tick():
 try:
  assert time.monotonic()-state['start']<60
  if s.operation_busy:return .1
  if state['step']==0:
   bpy.ops.wm.save_as_mainfile(filepath=str(area/'settings.blend'));state['step']=1;return 1
  if state['step']==1:
   if ext.preflight.issues(ext.preflight.cached(bpy.context,prefs)):return .1
   popup();state['step']=2;return 1
  if state['step']==2:
   snapshot('settings-ready.png')
   bpy.context.window.event_simulate(type='ESC',value='PRESS')
   view.spaces.active.show_region_ui=True
   state['step']=3;return .5
  if state['step']==3:
   region=next(r for r in view.regions if r.type=='UI')
   region.active_panel_category='LichtFeld'
   state['step']=4;return .5
  if state['step']==4:
   snapshot('sidebar-capture.png')
   s.dataset_mode='EXPORT';state['step']=5;return .5
  if state['step']==5:
   snapshot('sidebar-export.png')
   state['source']=bpy.context.scene
   with bpy.context.temp_override(area=view,region=next(r for r in view.regions if r.type=='WINDOW')):
    assert bpy.ops.lichtfeld.capture_framing()=={'FINISHED'}
   state['step']=6;return .1
  if state['step']==6:
   assert ext.capture_preview.source_scene(bpy.context.scene)==state['source']
   snapshot('sidebar-framing.png')
   assert bpy.ops.lichtfeld.close_capture_framing()=={'FINISHED'}
   assert bpy.context.scene==state['source'] and not s.operation_busy
   s.dataset_mode='REUSE';state['step']=7;return .5
  if state['step']==7:
   snapshot('sidebar-saved-images.png')
   s.previous_job=str(area/'previous-job')
   assert bpy.ops.lichtfeld.setup_view(mode='OPEN')=={'FINISHED'}
   state['step']=8;return .5
  if state['step']==8:
   snapshot('sidebar-open.png')
   assert bpy.ops.lichtfeld.setup_view(mode='CONNECT')=={'FINISHED'}
   assert ext._session_scan_pending
   snapshot('sidebar-searching.png')
   state['step']=9;return .5
  if state['step']==9:
   if ext._session_scan_pending:return .1
   snapshot('sidebar-trainers.png')
   assert bpy.ops.lichtfeld.setup_view(mode='NEW')=={'FINISHED'}
   assert s.dataset_mode=='REUSE'
   s.dataset_mode='CAPTURE';state['step']=10;return .5
  if state['step']==10:
   snapshot('sidebar-return.png')
   state['rig_source']=s.rig_parameter_source
   s.rig_parameter_source=None;state['step']=11;return .5
  if state['step']==11:
   snapshot('sidebar-load-rig.png')
   s.rig_parameter_source=state['rig_source'];s.rig_distance+=.25
   state['step']=12;return .5
  if state['step']==12:
   snapshot('sidebar-pending-rig.png')
   (area/'result.json').write_text(json.dumps({'ok':True,'framing_return':True,'navigation_return':True,'tasks':['CAPTURE','EXPORT','REUSE']}))
   print('SETTINGS_UI_TASKS_NATIVE_SECTIONS_AND_FRAMING_RETURN_PASS',flush=True)
   bpy.ops.wm.quit_blender();return None
 except Exception:
  (area/'result.json').write_text(json.dumps({'ok':False,'error':traceback.format_exc()}))
  traceback.print_exc();bpy.ops.wm.quit_blender();return None
bpy.app.timers.register(tick,first_interval=1)
