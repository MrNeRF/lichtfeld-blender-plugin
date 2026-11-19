# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import sys,tempfile,time,json,subprocess,importlib,shutil
from unittest.mock import Mock,patch
import bpy
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
module = 'bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
bpy.ops.preferences.addon_enable(module=module)
ext = importlib.import_module(module)
s=bpy.context.scene.lichtfeld_training
prefs=ext.preferences(bpy.context)
assert bpy.types.Operator.bl_rna_get_subclass_py('LICHTFELD_OT_start_training') is ext.LF_OT_start
assert bpy.types.Operator.bl_rna_get_subclass_py('LICHTFELD_OT_export_dataset') is ext.LF_OT_export_dataset
assert ext.LF_OT_start.bl_rna.identifier != ext.LF_OT_export_dataset.bl_rna.identifier
assert ext.LF_OT_folder.bl_description
s.running=True;s.status='Rendering';s.detail='Original status'
def immediate(fn,callback=None,failure=None):
 try:fn()
 except Exception as e:failure(str(e))
with patch.object(ext.ui_runtime,'submit',side_effect=immediate),patch.object(ext.jobs,'open_folder',side_effect=PermissionError('folder denied')):
 bpy.ops.lichtfeld.open_job()
assert s.running and s.status=='Rendering' and s.detail=='Original status'
s.running=False
with tempfile.TemporaryDirectory() as directory:
 area=Path(directory);prefs.jobs_directory=str(area/'jobs');prefs.backend_path=''
 s.dataset_mode='EXPORT'
 s.scope='SCENE';s.views=4;s.resolution=32;s.samples=4;s.engine='BLENDER_EEVEE';s.use_depth_loss=False;s.use_normal_loss=False
 ext.capture_settings.apply_format(bpy.context.scene, ext.capture_settings.preset_format(s))
 render=bpy.context.scene.render
 render.use_border=True;render.use_crop_to_border=True
 render.border_min_x=.25;render.border_min_y=.25;render.border_max_x=.75;render.border_max_y=.75
 s.refine_start=.9;s.refine_end=.1 # Irrelevant training settings must not block export.
 bpy.ops.lichtfeld.create_rig()
 expected_cameras={obj.name for obj in s.camera_collection.objects if obj.type=='CAMERA'}
 if '--multi-rig' in sys.argv:
  first=s.camera_collection
  s.rig_phase=.3
  assert bpy.ops.lichtfeld.create_rig(new_rig=True)=={'FINISHED'}
  assert [entry.collection for entry in s.capture_collections] == [first]
  assert bpy.ops.lichtfeld.capture_collection_entry()=={'FINISHED'}
  assert bpy.ops.lichtfeld.capture_collection_entry()=={'CANCELLED'}
  assert bpy.ops.lichtfeld.capture_collection_entry(use_edited=True)=={'FINISHED'}
  assert [entry.collection for entry in s.capture_collections] == [first, s.camera_collection]
  assert bpy.ops.lichtfeld.capture_collection_entry(use_edited=True)=={'CANCELLED'}
  expected_cameras.update(obj.name for obj in s.camera_collection.objects if obj.type=='CAMERA')
  assert len(expected_cameras)==8
 bpy.ops.wm.save_as_mainfile(filepath=str(area/'source.blend'))
 destination=area/'exports';destination.mkdir()
 result=bpy.ops.lichtfeld.export_dataset('EXEC_DEFAULT',directory=str(destination))
 assert result=={'FINISHED'},result
 root=Path(s.job_directory);deadline=time.monotonic()+150
 while time.monotonic()<deadline:
  state=ext.read_json(root/'state.json',{})
  if state.get('state') in {'completed','failed','stopped'}:break
  time.sleep(.2)
 assert state.get('state')=='completed',state
 metadata=ext.read_json(root/'job.json')
 assert set(metadata['cameras'])==expected_cameras
 first_output=Path(state['export_directory'])
 assert not (root/'dataset').exists()
 image_paths=list((first_output/'images').glob('*.png'))
 assert len(image_paths)==len(expected_cameras)
 frame=metadata['capture_format']
 for image_path in image_paths:
  image=bpy.data.images.load(str(image_path),check_existing=False)
  try: assert tuple(image.size)==(frame['width'],frame['height']), tuple(image.size)
  finally: bpy.data.images.remove(image)
 assert render.use_border and render.use_crop_to_border
 assert ext.jobs.captured_dataset(root)[2]==first_output
 # Reconstruct an interrupted capture from the exported data for the recovery case.
 shutil.copytree(first_output, root/'dataset',ignore=shutil.ignore_patterns('job.json'))
 metadata.pop('dataset_root')
 ext.atomic_json(root/'job.json',metadata)
 before={p.name:(p.stat().st_mtime_ns,p.read_bytes()) for p in (root/'dataset/images').glob('*.png')}
 journal=ext.read_json(root/'capture-frames.json')
 ext.atomic_json(root/'capture-frames.json',{**journal,'frames':journal['frames'][:-1]})
 rejected=subprocess.run([bpy.app.binary_path,'-b','--disable-autoexec',str(root/'capture.blend'),
  '--python-exit-code','1','--python',str(Path(ext.__file__).with_name('render_worker.py')),
  '--',str(root/'job.json'),'--finalize-capture'],capture_output=True,timeout=120)
 assert rejected.returncode!=0 and b'Saved camera calibration is incomplete' in rejected.stdout+rejected.stderr
 assert before=={p.name:(p.stat().st_mtime_ns,p.read_bytes()) for p in (root/'dataset/images').glob('*.png')}
 ext.atomic_json(root/'capture-frames.json',journal)
 # A completed-image render failure must enter the real supervisor recovery path.
 sys.path.insert(0,str(Path(ext.__file__).parent))
 import supervisor
 job=ext.jobs.freeze_source(ext.jobs.read_job(root/'job.json'))
 ext.atomic_json(root/'job.json',job)
 (root/'dataset/manifest.json').unlink()
 previous_render=ext.read_json(root/'capture-render.json',{})
 ext.atomic_json(root/'capture-render.json',{**previous_render,'error':'Simulated finalization failure','state':'failed'})
 popen=subprocess.Popen
 launches=[]
 failed=Mock(returncode=1)
 failed.poll.return_value=1
 def launch(arguments,**kwargs):
  launches.append(arguments)
  return failed if len(launches)==1 else popen(arguments,**kwargs)
 # Keep the scratch copy during the recovery fixture to compare file identity.
 real_atomic=supervisor.atomic_json
 def write_job(path,value):
  if path==root/'job.json' and value.get('dataset_root'):
   raise OSError('Simulated cleanup metadata failure')
  return real_atomic(path,value)
 with patch.object(supervisor.subprocess,'Popen',side_effect=launch),patch.object(supervisor,'atomic_json',side_effect=write_job):
  supervisor.supervise(root/'job.json')
 state=ext.read_json(root/'state.json',{})
 assert state.get('state')=='completed',state
 assert len(launches)==2 and '--finalize-capture' in launches[1]
 assert Path(launches[1][launches[1].index('--python-exit-code')-1]).samefile(root/'capture.blend')
 assert not ext.read_json(root/'capture-render.json',{}).get('error')
 after={p.name:(p.stat().st_mtime_ns,p.read_bytes()) for p in (root/'dataset/images').glob('*.png')}
 assert before==after,'Recovery must not render or rewrite existing images'
 ext.jobs.captured_dataset(root)
 # Optional render diagnostics must not invalidate an otherwise complete dataset.
 (root/'capture-render.json').write_text('{invalid',encoding='utf-8')
 finalized=subprocess.run([bpy.app.binary_path,'-b','--disable-autoexec',str(root/'capture.blend'),
  '--python-exit-code','1','--python',str(Path(ext.__file__).with_name('render_worker.py')),
  '--',str(root/'job.json'),'--finalize-capture'],capture_output=True,timeout=120)
 assert finalized.returncode==0,(finalized.stdout+finalized.stderr).decode(errors='replace')
 assert before=={p.name:(p.stat().st_mtime_ns,p.read_bytes()) for p in (root/'dataset/images').glob('*.png')}
 ext.jobs.captured_dataset(root)
 print('FINALIZE_CAPTURE_REUSES_IMAGES_PASS',flush=True)
 output=Path(state['export_directory'])
 assert output.parent.samefile(destination)
 assert len(list((output/'images').glob('*.png')))==len(expected_cameras)
 assert (output/'pointcloud.ply').is_file()
 geometry=json.loads((output/'view_geometry.json').read_text())
 assert geometry['source_frame']=='blender-world' and len(geometry['cameras'])==len(expected_cameras)
 assert not (root/'training.json').exists() and not (root/'training.log').exists() and not (root/'backend.json').exists()
 job,_,data,manifest=ext.jobs.captured_dataset(output)
 assert data==output and manifest['views']==len(expected_cameras)
 assert manifest['initialization']['method']=='visible_rendered'
 assert 4 <= manifest['initialization']['points'] <= s.seed_points
 assert manifest['view_geometry_file']=='view_geometry.json'
 # Export metadata is reusable by fresh training without exporting again.
 job,_=ext.jobs.restart_data(output,{'iterations':100,'optimizer_schedule':{'refine_start':0,'refine_end':.95,'growth_end':.5}},backend='TRUSTED',jobs_directory=prefs.jobs_directory)
 assert not job.get('capture_only') and not job.get('export_parent')
 print('DATASET_ONLY_CAPTURE_EXPORT_REUSE_AND_NONFATAL_FOLDER_PASS',flush=True)
