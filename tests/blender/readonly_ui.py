# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import bpy,importlib,sys,time,traceback
import numpy as np
ext=importlib.import_module('bl_ext.user_default.lichtfeld_training')
area=Path(sys.argv[sys.argv.index('--')+1]);area.mkdir(parents=True,exist_ok=True)
bpy.context.preferences.view.show_splash=False
view=max(bpy.context.window.screen.areas,key=lambda a:a.width*a.height)
view.type='VIEW_3D'
s=bpy.context.scene.lichtfeld_training;s.job_directory='';s.attached_session='';s.mode='CONNECT'
state={'step':0,'start':time.monotonic()}
def publish():
 from importlib import import_module
 transport=import_module('lichtfeld_sessions.transport')
 publisher=transport.SessionPublisher({'label':'Read-only transport fixture','state':'training','source':'test','project':'Fixture project'})
 arrays={'means':np.zeros((100,3),dtype=np.float32),'scale':np.full((100,3),-4,dtype=np.float32),'rotation':np.tile(np.array([1,0,0,0],dtype=np.float32),(100,1)),'opacity':np.zeros((100,1),dtype=np.float32),'sh':np.zeros((100,1,3),dtype=np.float32)}
 publisher.publish(arrays,120)
 return publisher
def ready(publisher):state['publisher']=publisher;bpy.ops.lichtfeld.scan_sessions()
ext.ui_runtime.submit(publish,ready)
def tick():
 try:
  assert time.monotonic()-state['start']<60
  publisher=state.get('publisher')
  if not publisher:return .1
  step=state['step']
  if step==0:
   if not any(item['id']==publisher.id for item in ext._sessions):return .1
   s.discovered_session=publisher.id
   assert bpy.ops.lichtfeld.attach_session()=={'FINISHED'}
   state['step']=1;return .1
  if step==1:
   if not s.result_object or s.importing:return .1
   assert not any(ext.allowed(bpy.context,action) for action in ('pause','resume','stop','import_best','continue'))
   with bpy.context.temp_override(area=view,region=next(r for r in view.regions if r.type=='WINDOW')):
    bpy.ops.wm.call_panel(name='LF_PT_training',keep_open=True)
   state['step']=2;return .5
  if step==2:
   bpy.ops.screen.screenshot(filepath=str(area/'08-readonly-popover.png'))
   bpy.ops.lichtfeld.detach_session()
   assert not s.attached_session and not s.job_directory and publisher.record['state']=='training'
   ext.ui_runtime.submit(lambda:publisher.update(state='completed'))
   state['step']=3;return .5
  if step==3:
   bpy.ops.lichtfeld.scan_sessions();state['step']=4;return .5
  if step==4:
   assert not any(item['id']==publisher.id for item in ext._sessions)
   publisher.stop.set();ext.ui_runtime.submit(publisher.pool.close)
   print('READONLY_ATTACH_DETACH_ACTIVE_FILTER_PASS',flush=True)
   bpy.ops.wm.quit_blender();return None
 except Exception:
  traceback.print_exc();bpy.ops.wm.quit_blender();return None
bpy.app.timers.register(tick,first_interval=1)
