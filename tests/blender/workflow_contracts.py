# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import sys,importlib,time
import bpy
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
module='bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
if module not in bpy.context.preferences.addons:
 bpy.ops.preferences.addon_enable(module=module)
ext=importlib.import_module(module)
s=bpy.context.scene.lichtfeld_training
profile=ext.training_options.PROFILES['mrnf']
assert (s.iterations,s.max_points,s.sh_degree,s.seed_points)==(
 profile['iterations'],profile['max_cap'],profile['sh_degree'],profile['init_num_pts'])
assert s.eval_interval==profile['eval_steps'][0]
assert not s.enable_eval and ext.training_settings(s)['enable_eval'] is False
import math
for name in ext.training_options.FIELDS:
 actual,expected=getattr(s,name),profile[name]
 assert math.isclose(actual,expected,rel_tol=1e-6,abs_tol=1e-9),(name,actual,expected)
s.strategy='mcmc'
s.strategy='mrnf'
assert s.use_exposure_correction and not s.use_ppisp and not s.use_bilateral_grid
print('FRESH_SCENE_NATIVE_MRNF_DEFAULTS_PASS')
assert s.engine==bpy.context.scene.render.engine
s.use_exposure_correction=True
assert not s.use_ppisp and not s.use_bilateral_grid
s.use_ppisp=True
assert not s.use_exposure_correction
s.use_ppisp=False
assert not ext.LF_OT_start.poll(bpy.context)
assert s.quality=='CUSTOM' and s.resolution==1024
s.quality='HIGH'
assert ext.capture_settings.format_for(bpy.context.scene,s)['width']==1536
s.capture_aspect='WIDE'
s.quality='HIGH'
format_wide=ext.capture_settings.format_for(bpy.context.scene,s)
assert (format_wide['width'],format_wide['height'])==(1536,864)
s.capture_aspect='SQUARE'
s.quality='BALANCED'
assert (s.views,s.iterations)==(96,7000)
assert ext.capture_settings.format_for(bpy.context.scene,s)['width']==1024
s.views=8
assert s.quality=='CUSTOM' and s.get('quality_preset')=='BALANCED'
s.views=96
assert s.quality=='BALANCED'
s.views=8
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
# The volume belongs to capture setup, independently of the content source.
assert bpy.ops.lichtfeld.create_capture_region(shape="SPHERE") == {"FINISHED"}
region = s.capture_region_object
assert region.empty_display_type == "SPHERE"
for scope in ("SCENE", "SELECTION", "COLLECTION"):
 s.scope = scope
 assert s.capture_region_object == region
signature = ext.preflight.signature(bpy.context)
region.empty_display_size *= 2
assert ext.preflight.signature(bpy.context) != signature
bpy.data.objects.remove(region, do_unlink=True)
s.scope = "SCENE"
capture_region = importlib.import_module(f'{module}.capture_region')
cube = bpy.data.objects["Cube"]
cube["test_roi_attribute"] = 0.75
default_material = bpy.data.materials.new("ROI source material")
override = bpy.data.materials.new("ROI object override")
cube.data.materials.append(default_material)
cube.material_slots[-1].link = "OBJECT"
cube.material_slots[-1].material = override
modifier = cube.modifiers.new("ROI render subdivision", "SUBSURF")
modifier.subdivision_type = "SIMPLE"
modifier.levels = 0
modifier.render_levels = 2
cube.visible_camera = False
cube.keyframe_insert(data_path="hide_render", frame=1)
assert capture_region.animated_render_visibility(cube)
cube.animation_data_clear()
volume = capture_region.local_transform("BOX", [[2,0,0,0],[0,0.5,0,0],[0,0,0.5,0],[0,0,0,1]])
clipped, = capture_region.clip_capture_scene(bpy.context, "BOX", volume, [cube])
assert cube.hide_render and clipped != cube
assert max(abs(vertex.co.x) for vertex in clipped.data.vertices) <= 0.50001
assert len(clipped.data.polygons) > len(cube.data.polygons)
assert not clipped.visible_camera and clipped["test_roi_attribute"] == 0.75
assert clipped.material_slots[-1].material == override
bpy.data.objects.remove(clipped, do_unlink=True)
cube.hide_render = False
cube.visible_camera = True
cube.modifiers.remove(modifier)
cube.data.materials.pop(index=len(cube.data.materials)-1)
del cube["test_roi_attribute"]
print("CAPTURE_REGION_RENDER_PROPERTIES_PASS")
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
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory)
 clean=root/'clean.blend';target=root/'capture.blend'
 prefs=ext.preferences(bpy.context)
 prefs.jobs_directory=str(root/'jobs')
 connections=ext.preflight.connection_folder(prefs)
 bpy.context.scene.lichtfeld_training.job_directory=''
 bpy.ops.wm.save_as_mainfile(filepath=str(clean))
 bpy.context.scene.frame_set(2)
 bpy.ops.wm.save_as_mainfile(filepath=str(target))
 job_root=root/'job';job_root.mkdir()
 manifest=job_root/'job.json'
 job={'schema':ext.jobs.SCHEMA,'id':'saved-capture','root':str(job_root),'source_file':str(target),
      'scene':bpy.context.scene.name,'source_identity':ext.jobs.source_identity(target.stat())}
 ext.jobs.atomic_json(manifest,job)
 ext.jobs.remember_job(connections,job)
 def settle_recovery():
  deadline=time.monotonic()+5
  while ext.ui_runtime._tasks and time.monotonic()<deadline:
   ext.ui_runtime.tick()
   time.sleep(.01)
  assert not ext.ui_runtime._tasks, 'Job recovery worker did not finish'
 bpy.ops.wm.open_mainfile(filepath=str(target),use_scripts=False)
 settle_recovery()
 assert bpy.context.scene.lichtfeld_training.job_directory==str(job_root)
 bpy.ops.wm.open_mainfile(filepath=str(clean),use_scripts=False)
 settle_recovery()
 assert not bpy.context.scene.lichtfeld_training.job_directory
 bpy.ops.wm.save_as_mainfile(filepath=str(target))
 bpy.ops.wm.open_mainfile(filepath=str(target),use_scripts=False)
 settle_recovery()
 assert not bpy.context.scene.lichtfeld_training.job_directory
 assert bpy.ops.lichtfeld.load_job(filepath=str(manifest))=={'FINISHED'}
 settle_recovery()
 assert bpy.context.scene.lichtfeld_training.job_directory==str(job_root)
 bpy.ops.wm.save_mainfile()
 bpy.ops.wm.open_mainfile(filepath=str(target),use_scripts=False)
 settle_recovery()
 assert bpy.context.scene.lichtfeld_training.job_directory==str(job_root)
 print('REPLACED_BLEND_SKIPS_AUTOMATIC_JOB_RECOVERY_PASS',flush=True)
bpy.ops.preferences.addon_disable(module=module)
assert not bpy.app.timers.is_registered(ext.poll_jobs)
print('WORKFLOW_CONTRACTS_PASS')
