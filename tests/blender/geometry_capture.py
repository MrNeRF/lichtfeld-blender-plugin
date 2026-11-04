# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import bpy,sys,subprocess
import numpy as np
area=Path(sys.argv[sys.argv.index('--')+1]).resolve();area.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
bpy.ops.preferences.addon_enable(module='lichtfeld_training')
import lichtfeld_training as ext
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add();obj=bpy.context.object
cameras=[]
for i in range(4):
 data=bpy.data.cameras.new('Known camera');camera=bpy.data.objects.new('Known camera',data)
 bpy.context.scene.collection.objects.link(camera);direction=__import__('mathutils').Vector(((0,0,1),(1,0,0),(0,1,0),(-1,0,0))[i]);camera.location=direction*5;camera.rotation_euler=(-direction).to_track_quat('-Z','Y').to_euler();cameras.append(camera.name)
size=16 if '--zero-depth' in sys.argv else 64
source=area/'source.blend';bpy.ops.wm.save_as_mainfile(filepath=str(source))
root=area/'job';root.mkdir(exist_ok=True)
job={'id':'geometry-test','root':str(root),'source_file':str(source),'scene':bpy.context.scene.name,'view_layer':bpy.context.view_layer.name,
 'frame':1,'scope':'SELECTION','sources':[obj.name],'cameras':cameras,'engine':'BLENDER_EEVEE' if '--eevee' in sys.argv else 'CYCLES','resolution':size,'samples':4,'seed_points':1000,'test_every':4,
 'use_depth_loss':True,'use_normal_loss':True,'depth_loss_weight':0 if '--zero-depth' in sys.argv else 2}
if '--relative-job' in sys.argv:job['root']=str(root.relative_to(Path.cwd()))
ext.jobs.freeze_source(job);ext.atomic_json(root/'job.json',job)
try:
 result=subprocess.run([bpy.app.binary_path,'--background','--factory-startup','--disable-autoexec',job['capture_staging_file'],'--python-exit-code','1','--python',str(Path(ext.__file__).with_name('render_worker.py')),'--',str(root/'job.json')],capture_output=True,timeout=180)
 (area/'render.log').write_bytes(result.stdout+result.stderr)
 assert not result.returncode,(result.stdout+result.stderr).decode(errors='replace')[-6000:]
 def read(path):
  image=bpy.data.images.load(str(path),check_existing=False);image.colorspace_settings.name='Non-Color'
  data=np.empty(size*size*4,dtype=np.float32);image.pixels.foreach_get(data);bpy.data.images.remove(image)
  return data.reshape(size,size,4)
 for i in range(4):
  depth=read(root/'dataset/depths'/f'view_{i:06d}.tiff')[:,:,0]
  normal=read(root/'dataset/normals'/f'view_{i:06d}.tiff')[:,:,:3]
  valid=depth>0
  assert valid.sum()>(0 if '--zero-depth' in sys.argv else 100),valid.sum()
  assert np.max(np.abs(depth[valid]-4))<1e-4,depth[valid]
  assert np.max(np.abs(normal[valid]-np.array([.5,.5,0])))<1e-4,normal[valid]
  assert np.max(np.abs(normal[~valid]-.5))<1e-4
 if '--zero-depth' in sys.argv:
  assert not (root/'dataset/sparse').exists()
  assert (root/'dataset/transforms.json').is_file()
  assert valid.sum()<256
  print('ZERO_DEPTH_WEIGHT_SMALL_FOREGROUND_PASS',flush=True)
 print('GEOMETRY_METRIC_Z_CAMERA_OPENCV_TIFF_PASS',flush=True)
finally:ext.jobs.release_source(job)
