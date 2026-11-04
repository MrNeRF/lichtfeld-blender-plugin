# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import sys
import bpy
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
bpy.ops.preferences.addon_enable(module='lichtfeld_training')
import lichtfeld_training as ext
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
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory)/'job'
 source=Path(directory)/'source.blend';source.write_bytes(b'saved source')
 job={'id':'launch-failure','root':str(root),'source_file':str(source),'scene':'Scene','connection_directory':str(Path(directory)/'connections')}
 with patch.object(ext.jobs,'remember_job',side_effect=PermissionError('index denied')),patch.object(ext.subprocess,'Popen') as spawn:
  try:ext.launch_supervisor(job)
  except PermissionError:pass
  else:raise AssertionError('Expected failure')
  spawn.assert_not_called()
 assert ext.read_json(root/'state.json')['state']=='failed'
 assert not source.with_name('.lichtfeld-capture-launch-failure.blend').exists()
bpy.ops.preferences.addon_disable(module="lichtfeld_training")
assert not bpy.app.timers.is_registered(ext.poll_jobs)
print('WORKFLOW_CONTRACTS_PASS')
