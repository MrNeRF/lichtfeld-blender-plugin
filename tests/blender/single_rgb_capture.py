# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""One RGB uses the real capture path without publishing a reusable dataset."""
from pathlib import Path
import sys
import time
import shutil
from concurrent.futures import Future
import uuid
from unittest.mock import patch
import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
bpy.ops.preferences.addon_enable(module='lichtfeld_training')
import lichtfeld_training as ext

output = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
output.mkdir(parents=True, exist_ok=True)
scene = bpy.context.scene
scene.frame_set(3)
capture_layer = scene.view_layers.new('Capture layer')
bpy.context.window.view_layer = capture_layer
s = scene.lichtfeld_training
ext.preferences().backend_path = ''
ext.preferences().jobs_directory = str(output/'jobs')
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 1
scene.cycles.use_denoising = False
scene.render.resolution_x = 96
scene.render.resolution_y = 64
scene.render.resolution_percentage = 100
s.camera_collection = bpy.data.collections['Collection']
s.scope = 'SELECTION'
for _ in ext.preflight.remember_sources(s, [bpy.data.objects['Cube']]):
    pass
capture_name='Camera';capture_index=1
if '--linked-guide' in sys.argv or '--position-variation' in sys.argv or '--rotation-variation' in sys.argv or '--panorama' in sys.argv:
    s.views=4
    if '--linked-guide' in sys.argv or '--panorama' in sys.argv:
        s.rig_mode='PATH';s.rig_path_link=True
        assert bpy.ops.lichtfeld.create_path_guide(shape='ORBIT',radius=4)=={'FINISHED'}
    else:
        s.rig_mode='SPHERE';s.rig_position_variation=5 if '--position-variation' in sys.argv else 0;s.rig_variation_seed=73
        if '--rotation-variation' in sys.argv:
            s.rig_rotation_variation=.03
    if '--panorama' in sys.argv:
        s.rig_orientation='PANORAMA';s.rig_station_views=8
        s.rig_station_shape='GRID' if '--ordered' in sys.argv else 'SPHERE' if '--spherical' in sys.argv else 'BAND'
        s.rig_station_frame='PATH' if '--path-facing' in sys.argv else 'TARGET' if '--target-facing' in sys.argv else 'GUIDE'
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    if '--linked-guide' in sys.argv or '--panorama' in sys.argv:
        s.rig_curve.location.x+=.3;s.rig_curve.scale=(1.2,1.2,1.2)
    bpy.context.view_layer.update()
    capture_name=sorted(o.name for o in s.camera_collection.objects if o.type=='CAMERA')[-1]
    capture_index=sum(o.type=='CAMERA' for o in s.camera_collection.objects)
s.use_depth_loss = True  # A test image never generates supervision maps.
s.use_normal_loss = True
s.refine_start = s.refine_end  # Training validation must not block RGB.
s.override_optics = True
s.capture_lens = 40
s.capture_preview_index = 99  # Same clamping contract as framing.
s.previous_job = 'keep previous job'
s.dataset_job_directory = 'keep dataset'
source = output/'source.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source))
saved = source.read_bytes()
before = (s.job_directory, s.previous_job, s.dataset_job_directory, s.result_object)
# Exercise the real framing action, including explicit save and framing restore.
assert bpy.ops.lichtfeld.capture_framing()=={'FINISHED'}
assert ext.capture_preview.source_scene(bpy.context.scene)==scene
bpy.context.scene.frame_set(77)  # Framing's timeline is not the source frame.
assert ext.capture_preview.LF_OT_render_capture_test.poll(bpy.context)
assert bpy.ops.lichtfeld.render_capture_test()=={'FINISHED'}
assert s.test_render_active  # Preparation is visible before the first timer tick.
saved=source.read_bytes()
deadline=time.monotonic()+180
other_actions=[]
def independent_action():
    assert s.test_render_active
    other_actions.append(True)
    yield
ext.ui_runtime.schedule(independent_action())
while s.operation_busy:
    assert time.monotonic()<deadline,'Single RGB timed out'
    ext.ui_runtime.tick()
    time.sleep(.01)
assert other_actions
assert ext.capture_preview.source_scene(bpy.context.scene)==scene
# The .blend saved by the action must contain only the source, not a framing scene.
inspection=output/'saved-source-inspection.blend'
shutil.copyfile(source,inspection)
with bpy.data.libraries.load(str(inspection)) as (saved_data,_):
    assert saved_data.scenes==[scene.name],saved_data.scenes
image = Path(s.test_render_image)
job = image.parent
assert image.is_file()
assert ext.read_json(job/'state.json')['state'] == 'completed'
assert not s.operation_busy and not s.test_render_active
assert before == (s.job_directory, s.previous_job, s.dataset_job_directory, s.result_object)
assert source.read_bytes() == saved
assert not (job/'dataset').exists()
assert not (job/'training.json').exists()
assert not (job/'capture.blend').exists()
assert not (job/'supervisor.lock').exists()
config = ext.read_json(job/'job.json')
assert config['cameras'] == [capture_name]
assert config['view_layer'] == 'Capture layer'
assert config['frame'] == 3
assert s.capture_preview_index == capture_index
assert config['capture_camera']['lens'] == 40
assert not config.get('connection_directory')
assert not Path(config['capture_staging_file']).exists()
framing_window=bpy.context.window
if not bpy.app.background:
    # The image window must not keep the framing scene alive after Back.
    window=bpy.context.window
    area=next(area for area in window.screen.areas if area.type=='VIEW_3D')
    with bpy.context.temp_override(window=window,area=area):
        assert bpy.ops.lichtfeld.show_test_image('EXEC_DEFAULT')=={'FINISHED'}
    viewer=next(item for item in bpy.context.window_manager.windows if item.screen.get('lichtfeld_test_viewer'))
    assert viewer.scene==scene
    assert viewer.view_layer==capture_layer
loaded = bpy.data.images.load(str(image))
assert tuple(loaded.size) == (96, 64)
bpy.data.images.remove(loaded)
cancel_job = dict(config, id=uuid.uuid4().hex, root=str(output/'cancel'))
launch = Future()
launch.set_result(ext.start_capture_test(cancel_job))
stopped = ext.finish_capture_test(launch)
assert stopped['state'] == 'stopped', stopped
assert not (output/'cancel/test-render.png').exists()
assert not Path(cancel_job['capture_staging_file']).exists()
assert not (output/'cancel/supervisor.lock').exists()
with bpy.context.temp_override(window=framing_window):
    assert bpy.ops.lichtfeld.close_capture_framing()=={'FINISHED'}
assert not any(ext.capture_preview.is_preview(item) for item in bpy.data.scenes)
assert bpy.context.window.view_layer == capture_layer
# A display-only framing failure must not stop an already launched RGB test.
bpy.ops.wm.save_mainfile()
s.operation_busy=True
previous_image=s.test_render_image
work=ext.capture_preview.test_image_steps(bpy.context,source=scene,window=bpy.context.window,framing=True)
errors=[]
with patch.object(ext.capture_preview,'open_steps',side_effect=ValueError('Framing no longer available')):
    ext.ui_runtime.schedule(work,errors.append)
    deadline=time.monotonic()+180
    while s.operation_busy:
        assert time.monotonic()<deadline
        ext.ui_runtime.tick()
        time.sleep(.01)
assert not errors,errors
assert s.test_render_image != previous_image
assert ext.read_json(Path(s.test_render_image).parent/'state.json')['state']=='completed'
# Cancellation during validation must not launch a renderer, and a genuine
# source change must remain visible after the transient operator report fades.
with bpy.context.temp_override(window=framing_window):
    assert bpy.ops.lichtfeld.capture_framing()=={'FINISHED'}
    assert bpy.ops.lichtfeld.render_capture_test()=={'FINISHED'}
    assert bpy.ops.lichtfeld.cancel_capture_test()=={'FINISHED'}
with patch.object(ext,'start_capture_test') as launch_test:
    while s.operation_busy:
        ext.ui_runtime.tick()
    launch_test.assert_not_called()
assert not s.test_render_active and not s.test_render_error
collect=ext.preflight.collect
def changed_source(*args,**kwargs):
    yield
    scene.render.resolution_x += 1
    return (yield from collect(*args,**kwargs))
with bpy.context.temp_override(window=framing_window):
    assert bpy.ops.lichtfeld.capture_framing()=={'FINISHED'}
    with patch.object(ext.preflight,'collect',changed_source), patch.object(ext,'start_capture_test') as launch_test:
        assert bpy.ops.lichtfeld.render_capture_test()=={'FINISHED'}
        while s.operation_busy:
            ext.ui_runtime.tick()
        launch_test.assert_not_called()
assert 'capture setup changed' in s.test_render_error
assert not s.test_render_active
# Native history/load callbacks must clear stale flags even if a file/history
# step captured a running test. These flags never reconnect a supervisor.
for restore in (ext.history_changed, ext.loaded):
    s.test_render_active = True
    s.test_render_cancel = True
    restore(None)
    assert not s.test_render_active and not s.test_render_cancel
print('SINGLE_RGB_CAPTURE_OK', flush=True)
