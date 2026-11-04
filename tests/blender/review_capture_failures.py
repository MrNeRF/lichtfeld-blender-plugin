# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture cleanup invariants after errors, aborts and source-scene deletion."""
from pathlib import Path
import sys
import uuid
import bpy
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'lichtfeld_training'))
bpy.ops.preferences.addon_enable(module='lichtfeld_training')
from lichtfeld_training import capture_preview
from lichtfeld_training.capture_observations import ObservationWriter
from lichtfeld_training.capture_passes import GeometryPasses

source=bpy.context.scene
layer=bpy.context.view_layer
source.render.use_compositing=False
source.render.compositor_precision='FULL'
prior=(source.compositing_node_group,source.render.use_compositing,source.render.compositor_precision,
       layer.use_pass_z,layer.use_pass_position,layer.use_pass_normal)
root=ROOT/'artifacts'/'review-capture-failures'/uuid.uuid4().hex
root.mkdir(exist_ok=True)
output=root/'geometry';output.mkdir(exist_ok=True)
unrelated=output/'_geometry_passes';unrelated.mkdir(exist_ok=True)
sentinel=unrelated/'user-note.txt';sentinel.write_text('preserve',encoding='utf-8')
passes=GeometryPasses(source,layer,output,observations=True,normals=False)
prefix=passes.prefix
observation_temps=list((output/'sparse'/'0').glob('*.lf-*.tmp'))
assert len(observation_temps)==3 and all(p.name.startswith(('cameras.bin.lf-','images.bin.lf-','points3D.bin.lf-')) for p in observation_temps)
passes.prepare(3)
orphan=passes.directory/f'{prefix}Depth_000003_0001.exr'
orphan.write_bytes(b'partial EXR')
unowned=passes.directory/'unrelated.exr'
unowned.write_bytes(b'keep')
try:
    raise OSError('injected failure after the depth compositor rendered')
except OSError:
    passes.close(commit=False)
assert passes.closed
passes.close(commit=False) # repeated cleanup is safe
assert not orphan.exists() and unowned.read_bytes()==b'keep' and sentinel.read_text(encoding='utf-8')=='preserve'
assert all(not path.exists() for path in observation_temps)
assert not list((output/'sparse'/'0').glob('*.bin'))
assert (source.compositing_node_group,source.render.use_compositing,source.render.compositor_precision,
        layer.use_pass_z,layer.use_pass_position,layer.use_pass_normal)==prior

# A complete geometry pass commits observation files only after successful close.
complete=GeometryPasses(source,layer,output,observations=True,normals=False)
complete.close(commit=True)
sparse=output/'sparse'/'0'
assert all((sparse/f'{name}.bin').is_file() for name in ('cameras','images','points3D'))
assert not list(sparse.glob('*.lf-*.tmp'))
# A writer abort never truncates an existing completed model.
before={p.name:p.read_bytes() for p in sparse.glob('*.bin')}
aborted=ObservationWriter(output)
aborted.points.write(b'partial')
aborted.abort()
assert before=={p.name:p.read_bytes() for p in sparse.glob('*.bin')}
assert not list(sparse.glob('*.lf-*.tmp'))

# Removing the original scene while its temporary framing scene is open still
# lets unload cleanup close the preview, restore a surviving scene and preserve
# a copied camera that the user deliberately linked into that surviving scene.
s=source.lichtfeld_training
s.views=4;s.scope='SCENE'
operator=type('Rig',(),dict(collection=None,rig_backup=[],committed=False,report=lambda *args:None))()
for _ in __import__('lichtfeld_training').LF_OT_rig.generate(operator,bpy.context):pass
fallback=bpy.data.scenes.new('User fallback scene')
for _ in capture_preview.open_steps(source,bpy.context.window,None):pass
preview=bpy.context.scene
preview_name=preview.name
camera=preview.camera
fallback.collection.objects.link(camera)
bpy.data.scenes.remove(source)
assert capture_preview.is_preview(preview) and capture_preview.source_scene(preview) is None
capture_preview.cleanup()
assert preview_name not in bpy.data.scenes
assert bpy.context.scene==fallback and camera.name in fallback.objects
assert camera.data.name in bpy.data.cameras and 'lichtfeld_capture_preview_camera' not in camera
assert not any(capture_preview.is_preview(scene) for scene in bpy.data.scenes)

# Force a worker error after GeometryPasses has opened all three observation
# streams. Its render-loop finally must release EXRs, compositor IDs and temps.
import lichtfeld_training as ext
from lichtfeld_training import bridge_core, render_worker
import numpy as np
worker_scene=bpy.context.scene
worker_scene.render.use_compositing=False
worker_cameras=[]
for name in ('A invalid projection','B perspective','C perspective','D perspective'):
    data=bpy.data.cameras.new(name)
    data.type='ORTHO' if name.startswith('A') else 'PERSP'
    camera=bpy.data.objects.new(name,data)
    worker_scene.collection.objects.link(camera)
    worker_cameras.append(camera)
before_nodes=len(bpy.data.node_groups)
job_root=ROOT/'artifacts'/'review-capture-failures'/uuid.uuid4().hex
job_root.mkdir(parents=True)
source_file=job_root/'source.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source_file))
job={'id':'failure-cleanup','root':str(job_root),'source_file':str(source_file),
     'scene':worker_scene.name,'view_layer':bpy.context.view_layer.name,'frame':worker_scene.frame_current,
     'scope':'SCENE','sources':['Cube'],'cameras':[camera.name for camera in worker_cameras],
     'engine':'BLENDER_EEVEE','samples':1,'resolution':64,'seed_points':1000,
     'test_every':4,'use_depth_loss':True,'depth_loss_weight':2,'use_normal_loss':False}
bridge_core.atomic_json(job_root/'job.json',job)
try:
    render_worker.render_dataset(job_root/'job.json')
    raise AssertionError('Invalid camera unexpectedly rendered')
except ValueError as error:
    assert 'perspective' in str(error).lower()
assert len(bpy.data.node_groups)==before_nodes
assert worker_scene.compositing_node_group is None and not worker_scene.render.use_compositing
assert not list((job_root/'dataset'/'_geometry_passes').glob('*.exr'))
assert not list((job_root/'dataset'/'sparse'/'0').glob('*.lf-*.tmp'))
assert not list((job_root/'dataset'/'sparse'/'0').glob('*.bin'))
print('RENDER_WORKER_ERROR_FINALLY_RELEASES_COMPOSITOR_AND_OBSERVATION_STREAMS',flush=True)

# Exercise the actual dependency graph with more mesh instances than the old
# four-points-per-instance floor could fit in a 100-point budget.
mesh=bpy.data.meshes.new('budgeted sample triangle')
mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
instances=[]
for index in range(300):
    obj=bpy.data.objects.new(f'Budgeted surface {index:03d}',mesh)
    worker_scene.collection.objects.link(obj)
    obj.location.x=index*2
    instances.append(obj)
sample_file=job_root/'surface-budget.ply'
concatenate=np.concatenate
def bounded_concatenate(values,*args,**kwargs):
    assert sum(len(value) for value in values)<=100,'intermediate surface samples exceeded the configured cap'
    return concatenate(values,*args,**kwargs)
render_worker.np.concatenate=bounded_concatenate
try:
    render_worker.write_seed(sample_file,instances,100)
finally:
    render_worker.np.concatenate=concatenate
header=sample_file.read_bytes().split(b'end_header\n',1)[0]
assert b'element vertex 100\n' in header
print('MANY_INSTANCE_SURFACE_INITIALIZATION_STAYS_WITHIN_GLOBAL_BUDGET',flush=True)
empty_mesh=bpy.data.meshes.new('Empty surface must not consume sample allocation')
empty_sources=[]
for index in range(300):
    obj=bpy.data.objects.new(f'Empty budget surface {index:03d}',empty_mesh)
    worker_scene.collection.objects.link(obj)
    empty_sources.append(obj)
render_worker.write_seed(job_root/'sparse-source-budget.ply',empty_sources+[instances[-1]],100)
assert b'element vertex 100\n' in (job_root/'sparse-source-budget.ply').read_bytes().split(b'end_header\n',1)[0]
print('EMPTY_SURFACES_DO_NOT_HIDE_VALID_INITIALIZATION_GEOMETRY',flush=True)
print('RENDER_FAILURE_CLEANUP_OBSERVATION_ATOMICITY_AND_ORPHAN_PREVIEW_PASS',flush=True)
