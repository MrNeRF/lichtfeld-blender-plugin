# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import sys,importlib
import bpy
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
module='bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
if module not in bpy.context.preferences.addons:
 bpy.ops.preferences.addon_enable(module=module)
ext=importlib.import_module(module)
s=bpy.context.scene.lichtfeld_training
assert s.engine==bpy.context.scene.render.engine
s.use_exposure_correction=True
assert not s.use_ppisp and not s.use_bilateral_grid
s.use_ppisp=True
assert not s.use_exposure_correction
s.use_ppisp=False
assert not ext.LF_OT_start.poll(bpy.context)
s.quality='BALANCED'
assert (s.views,s.iterations)==(96,7000)
s.views=8
assert s.quality=='CUSTOM'
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
first=s.camera_collection
assert sum(o.type=="CAMERA" for o in first.objects)==8
s.quality='BALANCED'
assert sum(o.type=="CAMERA" for o in first.objects)==8 and s.views==96
s.views=9
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert sum(o.type == "CAMERA" for o in s.camera_collection.objects)==9
assert len([c for c in bpy.data.collections if c.get('lichtfeld_generated')])==1
assert len([o for o in bpy.data.objects if o.get('lichtfeld_generated')])==9
s.job_directory='/previous/job'
s.previous_job=s.job_directory;s.job_directory='';s.attached_session='/external/session'
bpy.ops.lichtfeld.detach_session()
assert not s.job_directory and not s.attached_session
assert s.previous_job=='/previous/job'
for action in (lambda:bpy.ops.lichtfeld.training_command(action='pause'),lambda:bpy.ops.lichtfeld.import_result()):
 try:
  result=action();assert result=={'CANCELLED'}
 except RuntimeError:
  pass
ext.preflight._pending['stale']=True
ext.preflight.clear()
assert not ext.preflight._pending
import tempfile
from unittest.mock import patch
# Navigation retains the single setup and requests discovery only on user entry.
s.attached_session='';s.job_directory='';s.previous_job='/previous/job'
s.mode='OPEN';s.dataset_mode='EXPORT';s.rig_radius=7
with patch.object(ext,'refresh_sessions') as scan:
 assert bpy.ops.lichtfeld.setup_view(mode='NEW')=={'FINISHED'}
 assert s.mode=='NEW' and s.dataset_mode=='EXPORT' and s.rig_radius==7
 scan.assert_not_called()
 assert bpy.ops.lichtfeld.setup_view(mode='CONNECT')=={'FINISHED'}
 scan.assert_called_once()
 scan.reset_mock()
 other=bpy.data.scenes.new('Inactive trainer view');other.lichtfeld_training.mode='CONNECT'
 with patch.object(ext.preflight,'tick'),patch.object(ext.ui_runtime,'tick',return_value=.1):
  for _ in range(3):assert ext.poll_jobs()==.1
 scan.assert_not_called()
 assert bpy.ops.lichtfeld.scan_sessions()=={'FINISHED'}
 scan.assert_called_once()
 bpy.data.scenes.remove(other)
 assert bpy.ops.lichtfeld.setup_view(mode='NEW')=={'FINISHED'}
assert bpy.ops.lichtfeld.previous_job()=={'FINISHED'} and s.job_directory=='/previous/job'
assert not bpy.ops.lichtfeld.setup_view.poll()
s.job_directory='';s.operation_busy=True
assert not bpy.ops.lichtfeld.setup_view.poll()
s.operation_busy=False;s.running=True
assert not bpy.ops.lichtfeld.setup_view.poll()
s.running=False
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory)/'job'
 source=Path(directory)/'source.blend';source.write_bytes(b'saved source')
 job={'id':'launch-failure','root':str(root),'source_file':str(source),'scene':'Scene','connection_directory':str(Path(directory)/'connections')}
 with patch.object(ext.jobs,'check_environment',return_value={'ok':True}),patch.object(ext.jobs,'remember_job',side_effect=PermissionError('index denied')),patch.object(ext.subprocess,'Popen') as spawn:
  try:ext.launch_supervisor(job)
  except PermissionError:pass
  else:raise AssertionError('Expected failure')
  spawn.assert_not_called()
 assert ext.read_json(root/'state.json')['state']=='failed'
 assert not source.with_name('.lichtfeld-capture-launch-failure.blend').exists()
 for mode in ({}, {'reuse_dataset':True}, {'resume':True}):
  root=Path(directory)/('incompatible-'+str(len(mode)))
  job={'id':'incompatible','root':str(root),'backend':'changed-backend',**mode}
  with patch.object(ext.jobs,'check_environment',return_value={'ok':False,'message':'Incompatible backend'}) as check, \
       patch.object(ext.jobs,'freeze_source') as freeze,patch.object(ext.subprocess,'Popen') as spawn:
   try:ext.launch_supervisor(job)
   except ValueError as error:assert str(error)=='Incompatible backend'
   else:raise AssertionError('An incompatible backend was accepted')
   check.assert_called_once_with('changed-backend',str(root.parent))
   freeze.assert_not_called();spawn.assert_not_called()
bpy.ops.preferences.addon_disable(module=module)
assert not bpy.app.timers.is_registered(ext.poll_jobs)
print('WORKFLOW_CONTRACTS_PASS')
