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
bpy.context.window_manager.lichtfeld_workspace = 'TRAINING'
area=options.area.resolve();area.mkdir(parents=True,exist_ok=True)
bpy.context.preferences.view.show_splash=False
s=bpy.context.scene.lichtfeld_training
prefs=ext.preferences();prefs.backend_path=str(options.backend.resolve());prefs.jobs_directory=str(area/'jobs')
s.job_directory='';s.attached_session=''
s.engine='CYCLES';bpy.context.scene.cycles.device='CPU'
s.views=48
bpy.ops.object.select_all(action='DESELECT');bpy.data.objects['Cube'].select_set(True);bpy.context.view_layer.objects.active=bpy.data.objects['Cube']
view=max(bpy.context.window.screen.areas,key=lambda a:a.width*a.height);view.type='VIEW_3D'
state={'step':-2,'start':time.monotonic(),'window':bpy.context.window}
def popup():
 with bpy.context.temp_override(area=view,region=next(r for r in view.regions if r.type=='WINDOW')):bpy.ops.wm.call_panel(name='LF_PT_lichtfeld',keep_open=True)

def snapshot(name):
 # Programmatic RNA changes do not necessarily invalidate the displayed region.
 for current in bpy.context.screen.areas:current.tag_redraw()
 bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP',iterations=1)
 bpy.ops.screen.screenshot(filepath=str(area/name))
def step():
 try:
  assert time.monotonic()-state['start']<60
  if s.operation_busy:return .1
  if state['step']==-2:
   view.spaces.active.show_region_ui=True;state['step']=-1;return .5
  if state['step']==-1:
   region=next(r for r in view.regions if r.type=='UI');region.active_panel_category='LichtFeld'
   snapshot('sidebar-first-use.png')
   s.dataset_mode='EXPORT';prefs.backend_path=''
   snapshot('sidebar-first-dataset.png')
   s.dataset_mode='CAPTURE';prefs.backend_path=str(options.backend.resolve())
   bpy.ops.lichtfeld.create_rig();state['step']=0;return .5
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
   state['perspective']=view.spaces.active.region_3d.view_perspective
   with bpy.context.temp_override(area=view,region=next(r for r in view.regions if r.type=='WINDOW')):
    assert bpy.ops.lichtfeld.capture_framing()=={'FINISHED'}
   state['step']=6;return .1
  if state['step']==6:
   assert ext.capture_preview.source_scene(bpy.context.scene)==state['source']
   assert view.spaces.active.region_3d.view_perspective=='CAMERA'
   snapshot('sidebar-framing.png')
   state['preview']=bpy.context.scene
   state['counts']=(len(bpy.data.scenes),len(bpy.data.objects),len(bpy.data.cameras))
   s.capture_preview_index=48;s.capture_preview_index=7;s.capture_preview_index=2
   state['step']=6.1;return .1
  if state['step']==6.1:
   if ext.capture_preview.camera_pending(bpy.context.scene):return .02
   assert bpy.context.scene==state['preview']
   assert bpy.context.scene['capture_camera_name']=='Capture 002'
   assert state['counts']==(len(bpy.data.scenes),len(bpy.data.objects),len(bpy.data.cameras))
   assert not s.operation_busy
   assert view.spaces.active.region_3d.view_perspective=='CAMERA'
   snapshot('sidebar-framing-camera-2.png')
   source=state['source'];source.render.resolution_x=2000;source.render.resolution_y=1000;source.render.resolution_percentage=50
   s.override_optics=True;s.capture_lens=50
   assert bpy.ops.lichtfeld.capture_framing()=={'FINISHED'}
   state['step']=6.2;return .1
  if state['step']==6.2:
   if ext.capture_preview.camera_pending(bpy.context.scene):return .02
   assert bpy.context.scene==state['preview']
   assert (bpy.context.scene.render.resolution_x,bpy.context.scene.render.resolution_y)==(1000,500)
   assert bpy.context.scene.camera.data.lens==50
   assert state['counts']==(len(bpy.data.scenes),len(bpy.data.objects),len(bpy.data.cameras))
   snapshot('sidebar-framing-updated.png')
   preview=state['preview'];state['render_engine']=preview.render.engine
   preview.render.engine='CYCLES';preview.cycles.device='CPU';preview.cycles.samples=8192
   preview.cycles.use_adaptive_sampling=False;preview.cycles.use_denoising=False
   preview.render.resolution_x=128;preview.render.resolution_y=128
   preview.render.use_lock_interface=True
   bpy.context.preferences.view.render_display_type='NONE'
   state['render_camera']=preview.camera.as_pointer();state['render_ticks']=0
   assert bpy.ops.render.render('INVOKE_DEFAULT')=={'RUNNING_MODAL'}
   s.capture_preview_index=3
   state['step']=6.21;return .01
  if state['step']==6.21:
   if bpy.app.is_job_running('RENDER'):
    state['render_ticks']+=1
    assert state['preview'].camera.as_pointer()==state['render_camera']
    assert state['counts']==(len(bpy.data.scenes),len(bpy.data.objects),len(bpy.data.cameras))
    assert not bpy.ops.lichtfeld.close_capture_framing.poll()
    return .01
   assert state['render_ticks']>=3,'The native render did not overlap the pending request'
   if ext.capture_preview.camera_pending(state['preview']):return .02
   assert state['preview']['capture_camera_name']=='Capture 003'
   state['preview'].render.engine=state['render_engine']
   windows={window.as_pointer() for window in bpy.context.window_manager.windows}
   assert bpy.ops.wm.window_new_main()=={'FINISHED'}
   second=next(window for window in bpy.context.window_manager.windows if window.as_pointer() not in windows)
   state['second']=second;second.scene=state['source']
   with bpy.context.temp_override(window=second):
    other=next(current for current in second.screen.areas if current.type=='VIEW_3D')
    with bpy.context.temp_override(area=other):
     assert bpy.ops.lichtfeld.capture_framing()=={'FINISHED'}
   state['step']=6.3;return .1
  if state['step']==6.3:
   second=state['second'];preview=second.scene
   assert state['window'].scene==state['preview'] and preview!=state['preview']
   assert ext.capture_preview.source_scene(preview)==state['source']
   state['second_preview']=preview
   s.capture_preview_index=10000
   state['deadline']=time.monotonic()+5;state['step']=6.4;return .05
  if state['step']==6.4:
   previews=(state['preview'],state['second_preview'])
   assert time.monotonic()<state['deadline'],'Two previews keep triggering updates'
   if any(ext.capture_preview.camera_pending(preview) for preview in previews):return .02
   assert s.capture_preview_index==48
   assert all(preview['capture_camera_name']=='Capture 048' for preview in previews)
   state['camera_ids']=[preview.camera.as_pointer() for preview in previews]
   state['step']=6.5;return .2
  if state['step']==6.5:
   previews=(state['preview'],state['second_preview'])
   assert state['camera_ids']==[preview.camera.as_pointer() for preview in previews]
   assert not any(ext.capture_preview.camera_pending(preview) for preview in previews)
   # Join the first preview, defer its update while the source is busy, then
   # close the initially chosen window. The surviving window must be resolved
   # again after setup; wmWindow references do not have ID invalidation.
   second=state['second']
   with bpy.context.temp_override(window=second):
    other=next(current for current in second.screen.areas if current.type=='VIEW_3D')
    with bpy.context.temp_override(area=other):
     assert bpy.ops.lichtfeld.close_capture_framing()=={'FINISHED'}
   second.scene=state['preview'];state['window'].scene=state['source']
   s.operation_busy=True;s.capture_preview_index=4
   ext.ui_runtime.tick()
   assert ext.capture_preview.camera_pending(state['preview'])
   state['window'].scene=state['preview']
   with bpy.context.temp_override(window=second):
    assert bpy.ops.wm.window_close()=={'FINISHED'}
   def finish_setup():
    s.operation_busy=False
    yield
   ext.ui_runtime.schedule(finish_setup())
   state['step']=6.6;return .1
  if state['step']==6.6:
   if ext.capture_preview.camera_pending(state['preview']):return .02
   assert bpy.context.scene==state['preview']
   assert bpy.context.scene['capture_camera_name']=='Capture 004'
   assert state['counts']==(len(bpy.data.scenes),len(bpy.data.objects),len(bpy.data.cameras))
   assert not ext.capture_preview._pending
   with bpy.context.temp_override(area=view,region=next(r for r in view.regions if r.type=='WINDOW')):
    assert bpy.ops.lichtfeld.close_capture_framing()=={'FINISHED'}
   assert bpy.context.scene==state['source'] and not s.operation_busy
   assert view.spaces.active.region_3d.view_perspective==state['perspective']
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
   s.rig_generator=True;s.rig_details=False
   state['step']=12;return .5
  if state['step']==12:
   snapshot('sidebar-pending-rig.png')
   (area/'result.json').write_text(json.dumps({'ok':True,'framing_return':True,'native_render_ticks':state['render_ticks'],'two_main_windows':True,'deferred_shared_window_close':True,'navigation_return':True,'tasks':['CAPTURE','EXPORT','REUSE']}))
   print('SETTINGS_UI_TASKS_NATIVE_SECTIONS_AND_FRAMING_RETURN_PASS',flush=True)
   bpy.ops.wm.quit_blender();return None
 except Exception:
  (area/'result.json').write_text(json.dumps({'ok':False,'error':traceback.format_exc()}))
  traceback.print_exc();bpy.ops.wm.quit_blender();return None
def tick():
 with bpy.context.temp_override(window=state['window']):
  return step()
bpy.app.timers.register(tick,first_interval=1)
