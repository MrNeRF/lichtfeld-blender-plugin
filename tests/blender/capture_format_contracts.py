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
assert [camera.name for camera in ext.cameras_for(s)] == ready['cameras']
s.capture_preview_index = 1
for _ in ext.capture_preview.open_steps(source, bpy.context.window, None):
    pass
assert bpy.context.scene['capture_camera_name'] == ready['cameras'][0]
assert bpy.ops.lichtfeld.close_capture_framing() == {'FINISHED'}
print('NESTED_CAPTURE_ORDER_MATCHES_READINESS_AND_FRAMING_PASS', flush=True)
