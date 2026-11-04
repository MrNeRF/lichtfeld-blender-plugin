# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real reader -> cooperative Blender installation while the trainer is paused."""
import importlib
import os
from pathlib import Path
import sys
import time

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
module='bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
if module not in bpy.context.preferences.addons:bpy.ops.preferences.addon_enable(module=module)
extension=importlib.import_module(module)
transport=importlib.import_module(module+'.session_transport')
geometry=importlib.import_module(module+'.session_geometry')
area=Path(sys.argv[sys.argv.index('--')+1]).resolve()
area.mkdir(parents=True,exist_ok=True)
os.environ['LFS_TRAINING_SESSIONS_DIR']=str(area/'sessions')
scene=bpy.context.scene
settings=scene.lichtfeld_training
publisher=transport.SessionPublisher({})


def until(predicate):
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        extension.ui_runtime.tick()
        if predicate():return
        time.sleep(.01)
    binding=extension.ui_runtime.peek(scene)
    raise AssertionError((settings.detail,binding.reader.error if binding else 'No binding'))


def value(offset):
    pose=np.eye(4);pose[0,3]=offset
    result=geometry.from_capture([{'file_path':'camera.png','transform_matrix':pose.tolist(),
        'w':640,'h':480,'fl_x':500,'fl_y':500,'cx':320,'cy':240}])
    result['model_to_data']=pose.tolist()
    return result


try:
    geometry.publish(publisher,value(2))
    publisher.publish({'means':np.zeros((7,3),np.float32),'scale':np.zeros((7,3),np.float32),
        'rotation':np.tile(np.array([1,0,0,0],np.float32),(7,1)),
        'opacity':np.zeros((7,1),np.float32),'sh':np.zeros((7,1,3),np.float32)},17)
    publisher.update(state='paused')
    settings.attached_session=str(publisher.root)
    until(lambda:settings.result_object is not None and not settings.importing and settings.session_camera_count==1)
    binding=extension.ui_runtime.peek(scene)
    original=scene.lichtfeld_training.result_object.data
    initial_id=settings.result_object['lichtfeld_artifact']
    for offset in (5,11,23):
        geometry.publish(publisher,value(offset))
        revision=publisher.record['geometry']
        until(lambda:binding.reader.last_geometry_ref==revision and not settings.importing)
        np.testing.assert_allclose(settings.result_object.matrix_world,geometry.source_model_transform(value(offset)))
        assert settings.result_object.data==original
        assert settings.result_object['lichtfeld_artifact']==initial_id and settings.session_camera_count==1
    publisher.update(geometry={'path':'view_geometry.json','bytes':1,'sha256':'bad'})
    until(lambda:bool(binding.reader.status.get('geometry_error')))
    assert settings.result_object.data==original and settings.session_camera_count==1
    geometry.publish(publisher,value(31))
    until(lambda:not binding.reader.status.get('geometry_error') and binding.reader.last_geometry_ref==publisher.record['geometry'])
    assert settings.result_object.data==original
    publisher.update(geometry=None)
    until(lambda:binding.reader.last_geometry_ref is None and settings.session_camera_count==0)
    np.testing.assert_allclose(settings.result_object.matrix_world,geometry.source_model_transform())
    assert settings.result_object.data==original
    # A deleted display is restored automatically after the metadata-only ACK fails.
    bpy.data.objects.remove(settings.result_object,do_unlink=True)
    geometry.publish(publisher,value(41))
    until(lambda:settings.result_object is not None and not settings.importing and not binding.reader.error)
    np.testing.assert_allclose(settings.result_object.matrix_world,geometry.source_model_transform(value(41)))
    assert settings.result_object['lichtfeld_artifact']==initial_id
    assert len(settings.result_object.data.points)==7
    print('PAUSED_REAL_READER_POSE_RECOVERY_NO_GAUSSIAN_REUPLOAD_PASS',flush=True)
finally:
    binding=extension.ui_runtime.peek(scene)
    settings.attached_session=''
    extension.ui_runtime.clear()
    if binding:binding.reader.thread.join(5)
    publisher.finish();publisher.thread.join(3);publisher.pool.close()
