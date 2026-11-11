# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real evaluated copies, portrait fitting and temporary framing lifecycle."""
from pathlib import Path
import sys
import importlib
import bpy
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
module = 'bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
bpy.ops.preferences.addon_enable(module=module)
ext = importlib.import_module(module)
source = bpy.context.scene
s = source.lichtfeld_training
s.scope='SCENE';s.views=4;s.rig_distance=1.05
s.capture_aspect='WIDE';s.resolution=1024
ext.capture_settings.apply_format(source, ext.capture_settings.preset_format(s))
print('CHECK first wide rig',flush=True)
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
cameras=ext.cameras_for(s)
r_wide=cameras[0].location.length
s.capture_aspect='SQUARE'
ext.capture_settings.apply_format(source, ext.capture_settings.preset_format(s))
print('CHECK square rig',flush=True)
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
r_square=ext.cameras_for(s)[0].location.length
assert r_wide>r_square*1.4,(r_wide,r_square)
s.capture_aspect='WIDE';s.capture_orientation='PORTRAIT'
ext.capture_settings.apply_format(source, ext.capture_settings.preset_format(s))
print('CHECK portrait rig',flush=True)
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert abs(ext.cameras_for(s)[0].location.length-r_wide)<1e-4
cameras=ext.cameras_for(s)
# Shared animated optics are frozen once, then independently overridden.
data=cameras[0].data
cameras[1].data=data
data.lens=22;data.keyframe_insert(data_path='lens',frame=1)
data.lens=66;data.keyframe_insert(data_path='lens',frame=20)
source.frame_set(1);bpy.context.view_layer.update()
graph=bpy.context.evaluated_depsgraph_get()
print('CHECK independent copy',flush=True)
before_matrix=cameras[0].evaluated_get(graph).matrix_world.copy()
before_render=tuple(getattr(source.render,k) for k in ('resolution_x','resolution_y','pixel_aspect_y'))
s.override_optics=True;s.capture_lens=40;s.capture_sensor_fit='HORIZONTAL'
s.override_shift=True;s.capture_shift_x=.12;s.capture_shift_y=-.08
s.override_clipping=True;s.capture_clip_start=.05;s.capture_clip_end=200
copy=ext.capture_settings.copy_camera(cameras[0],graph,ext.capture_settings.camera_for(s))
source.collection.objects.link(copy)
bpy.context.view_layer.update()
assert copy.data!=data and copy.data.animation_data is None and copy.data.lens==40
assert np.max(np.abs(np.asarray(copy.matrix_world)-before_matrix))<1e-6
assert data.lens==22 and cameras[1].data==data
owned=copy.data;bpy.data.objects.remove(copy);bpy.data.cameras.remove(owned)
bpy.context.view_layer.update()
counts=(len(bpy.data.scenes),len(bpy.data.objects),len(bpy.data.cameras))
for _ in range(3):
    print('CHECK framing lifecycle',flush=True)
    for _ in ext.capture_preview.open_steps(source,bpy.context.window,None):pass
    preview=bpy.context.scene
    assert ext.capture_preview.source_scene(preview)==source
    assert (preview.render.resolution_x,preview.render.resolution_y)==(576,1024)
    assert preview.camera.data.lens==40 and preview.camera.data!=data
    assert preview.camera.data.shift_x==s.capture_shift_x
    assert not s.operation_busy
    assert tuple(getattr(source.render,k) for k in ('resolution_x','resolution_y','pixel_aspect_y'))==before_render
    assert data.lens==22 and np.max(np.abs(np.asarray(cameras[0].matrix_world)-before_matrix))<1e-6
    s.capture_preview_index=2
    for _ in ext.capture_preview.open_steps(source,bpy.context.window,None):pass
    assert len(bpy.data.scenes)==counts[0]+1
    assert bpy.ops.lichtfeld.close_capture_framing()=={'FINISHED'}
    assert bpy.context.scene==source
    assert (len(bpy.data.scenes),len(bpy.data.objects),len(bpy.data.cameras))==counts
# Invalid settings fail before creating any preview data, with the source restored.
s.capture_clip_start=300
try:
    for _ in ext.capture_preview.open_steps(source,bpy.context.window,None):pass
    raise AssertionError('Invalid clipping was accepted')
except ValueError:pass
assert not s.operation_busy and bpy.context.scene==source
assert (len(bpy.data.scenes),len(bpy.data.objects),len(bpy.data.cameras))==counts
print('RECTANGULAR_FITTING_EVALUATED_OVERRIDES_AND_PREVIEW_LIFECYCLE_PASS',flush=True)

# Nested custom collections use the same explicit order in readiness and framing.
s.capture_clip_start = .05
collection = bpy.data.collections.new('Capture order')
nested = bpy.data.collections.new('Nested capture cameras')
source.collection.children.link(collection)
collection.children.link(nested)
names = ['Order Z', 'Order A', 'Order M', 'Order B']
for index, name in enumerate(names):
    camera = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    (nested if index % 2 else collection).objects.link(camera)
s.camera_collection = collection
work = ext.preflight.collect(bpy.context)
while True:
    try:
        next(work)
    except StopIteration as complete:
        ready = complete.value
        break
assert ready['cameras'] == sorted(names)
assert (ready['format']['width'],ready['format']['height'])==(576,1024)
assert ready['render']['engine']==source.render.engine
assert [camera.name for camera in ext.cameras_for(s)] == ready['cameras']
s.capture_preview_index = 1
for _ in ext.capture_preview.open_steps(source, bpy.context.window, None):
    pass
assert bpy.context.scene['capture_camera_name'] == ready['cameras'][0]
assert bpy.ops.lichtfeld.close_capture_framing() == {'FINISHED'}
print('NESTED_CAPTURE_ORDER_MATCHES_READINESS_AND_FRAMING_PASS', flush=True)

# Blender ignores orthogonal camera scale; framing and capture must do the same.
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view
import tempfile
camera = bpy.data.objects[sorted(names)[0]]
parent = bpy.data.objects.new('Scaled camera parent', None)
source.collection.objects.link(parent)
parent.location = (1, -2, .5)
parent.rotation_euler = (.1, -.2, .3)
camera.parent = parent
camera.location = (4, -6, 3)
camera.rotation_euler = (-Vector(camera.location)).to_track_quat('-Z', 'Y').to_euler()
camera.data.lens = 35
camera.data.shift_x = .08
camera.data.shift_y = -.03
s.override_optics=False;s.override_shift=False;s.override_clipping=False
for parent_scale, local_scale in (((1,1,1),(2,3,4)), ((2,2,2),(1,1,1)), ((1,1,1),(-2,-3,4))):
    parent.scale=parent_scale;camera.scale=local_scale
    bpy.context.view_layer.update()
    graph=bpy.context.evaluated_depsgraph_get()
    before=camera.evaluated_get(graph).matrix_world.copy()
    copy=ext.capture_settings.copy_camera(camera,graph,{})
    source.collection.objects.link(copy)
    bpy.context.view_layer.update()
    assert np.allclose(np.asarray(copy.matrix_world)[:3,:3].T@np.asarray(copy.matrix_world)[:3,:3],np.eye(3),atol=1e-5)
    for point in ((0,0,0),(1,.5,.2),(-1,.2,1)):
        a=world_to_camera_view(source,camera.evaluated_get(graph),Vector(point))
        b=world_to_camera_view(source,copy,Vector(point))
        assert (a-b).length<1e-5,(a,b)
    assert np.allclose(np.asarray(camera.evaluated_get(graph).matrix_world),before,atol=1e-6)
    owned=copy.data;bpy.data.objects.remove(copy,do_unlink=True);bpy.data.cameras.remove(owned)
assert camera.parent==parent
print('ORTHOGONAL_SCALE_PARENT_AND_DOUBLE_NEGATIVE_PROJECTION_PASS',flush=True)

from types import SimpleNamespace
invalid=before.copy();invalid[0][3]=float('nan')
nonfinite=SimpleNamespace(name='Non-finite camera',evaluated_get=lambda _:SimpleNamespace(data=camera.data,matrix_world=invalid))
try:ext.capture_settings.camera_pose(nonfinite,graph)
except ValueError as error:assert 'non-finite' in str(error) and nonfinite.name in str(error)
else:raise AssertionError('Non-finite pose accepted')

# Invalid transforms are rejected by the same owner before any copy allocation.
counts=(len(bpy.data.objects),len(bpy.data.cameras))
camera.data.type='ORTHO'
bpy.context.view_layer.update()
graph=bpy.context.evaluated_depsgraph_get()
try:ext.capture_settings.copy_camera(camera,graph,{})
except ValueError as error:assert camera.name in str(error) and 'Perspective' in str(error)
else:raise AssertionError('Unsupported projection accepted')
camera.data.type='PERSP'
for scale, message in (((0,1,1),'zero'),((1e-10,1,1),'zero'),((-1,1,1),'mirrored')):
    parent.scale=(1,1,1);camera.scale=scale
    bpy.context.view_layer.update()
    try:ext.capture_settings.copy_camera(camera,bpy.context.evaluated_depsgraph_get(),{})
    except ValueError as error:assert camera.name in str(error) and message in str(error),error
    else:raise AssertionError('Invalid pose accepted')
    assert counts==(len(bpy.data.objects),len(bpy.data.cameras))
parent.scale=(2,1,1);camera.scale=(1,1,1);camera.rotation_euler=(.2,.4,.6)
bpy.context.view_layer.update()
work=ext.preflight.collect(bpy.context)
while True:
    try:next(work)
    except StopIteration as result:ready=result.value;break
assert any('sheared' in error and camera.name in error for error in ready['errors']),ready
assert camera.name in ready['camera_issues'].values()
try:ext.cameras_for(s)
except ValueError as error:assert 'sheared' in str(error)
else:raise AssertionError('Job creation accepted shear')
assert bpy.ops.lichtfeld.inspect_capture_camera(camera_name=camera.name)=={'FINISHED'}
assert bpy.context.view_layer.objects.active==camera
assert camera.parent==parent and tuple(parent.scale)==(2,1,1)
counts=(len(bpy.data.scenes),len(bpy.data.objects),len(bpy.data.cameras))
try:
    for _ in ext.capture_preview.open_steps(source,bpy.context.window,None):pass
except ValueError as error:assert 'sheared' in str(error) and camera.name in str(error)
else:raise AssertionError('Framing accepted shear')
assert not s.operation_busy and bpy.context.scene==source
assert counts==(len(bpy.data.scenes),len(bpy.data.objects),len(bpy.data.cameras))
print('NAMED_INVALID_CAMERA_AND_CONTEXT_SELECTION_PASS',flush=True)

# A constraint can invalidate an otherwise unit-scale source object.
parent.scale=(1,1,1);camera.scale=(1,1,1)
scale_target=bpy.data.objects.new('Animated scale target',None)
source.collection.objects.link(scale_target)
scale_target.scale=(1,1,1);scale_target.keyframe_insert(data_path='scale',frame=1)
scale_target.scale=(0,1,1);scale_target.keyframe_insert(data_path='scale',frame=2)
constraint=camera.constraints.new('COPY_SCALE');constraint.target=scale_target
source.frame_set(1);bpy.context.view_layer.update()
ext.capture_settings.camera_pose(camera,bpy.context.evaluated_depsgraph_get())
source.frame_set(2);bpy.context.view_layer.update()
assert tuple(camera.scale)==(1,1,1)
try:ext.capture_settings.camera_pose(camera,bpy.context.evaluated_depsgraph_get())
except ValueError as error:assert 'zero' in str(error) and camera.name in str(error)
else:raise AssertionError('Evaluated constraint scale was ignored')
camera.constraints.remove(constraint);bpy.data.objects.remove(scale_target,do_unlink=True)
source.frame_set(1)
print('ANIMATED_CONSTRAINT_SCALE_USES_CAPTURE_FRAME_PASS',flush=True)

# Compare actual source/copy render pixels, independently of projection math.
parent.scale=(2,2,2);camera.scale=(2,3,4)
camera.rotation_euler=(-Vector(camera.location)).to_track_quat('-Z','Y').to_euler()
bpy.context.view_layer.update()
graph=bpy.context.evaluated_depsgraph_get()
copy=ext.capture_settings.copy_camera(camera,graph,{})
source.collection.objects.link(copy)
source.render.resolution_x=64;source.render.resolution_y=64;source.render.resolution_percentage=100
source.render.image_settings.file_format='PNG'
source.render.film_transparent=True
with tempfile.TemporaryDirectory() as directory:
    for engine in ('BLENDER_EEVEE','CYCLES'):
        source.render.engine=engine
        if engine=='CYCLES':source.cycles.device='CPU';source.cycles.samples=4;source.cycles.seed=19;source.cycles.use_denoising=False
        pixels=[]
        for index,obj in enumerate((camera,copy)):
            source.camera=obj
            path=str(Path(directory)/f'{engine}-{index}.png')
            source.render.filepath=path
            bpy.ops.render.render(write_still=True)
            image=bpy.data.images.load(path,check_existing=False)
            values=np.empty(64*64*4,dtype=np.float32);image.pixels.foreach_get(values)
            pixels.append(values);bpy.data.images.remove(image)
        assert np.max(np.abs(pixels[0]-pixels[1]))<.01,(engine,np.max(np.abs(pixels[0]-pixels[1])))
        assert np.any(pixels[0].reshape(-1,4)[:,3]>.1),'Empty render cannot prove parity'
        assert np.std(pixels[0].reshape(-1,4)[:,:3])>.01,'Uniform render cannot prove parity'
        print('SCALED_CAMERA_RENDER_PARITY_PASS',engine,flush=True)
owned=copy.data;bpy.data.objects.remove(copy,do_unlink=True);bpy.data.cameras.remove(owned)
