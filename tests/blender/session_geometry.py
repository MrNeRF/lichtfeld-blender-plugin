# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import sys,numpy as np,importlib
from types import SimpleNamespace
from unittest.mock import patch
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
if '--installed' in sys.argv:
 ext=importlib.import_module('bl_ext.user_default.lichtfeld_training')
 assert ext.__package__ in bpy.context.preferences.addons
else:
 bpy.ops.preferences.addon_enable(module='lichtfeld_training')
 ext=importlib.import_module('lichtfeld_training')
g=importlib.import_module(ext.__package__+'.session_geometry')
session_cameras=importlib.import_module(ext.__package__+'.session_cameras')
scene=bpy.context.scene;s=scene.lichtfeld_training
pose=np.eye(4);pose[:3,3]=[3,5,7]
pose[:3,:3]=[[0,-1,0],[1,0,0],[0,0,1]]
geometry=g.from_capture([{'file_path':'view.png','transform_matrix':pose.tolist(),'w':640,'h':480,'fl_x':500,'fl_y':500,'cx':310,'cy':235}])
M=np.eye(4);M[:3,3]=[13,17,19];geometry['model_to_data']=M.tolist()
M[:3,:3]=[[0,0,1],[0,1,0],[-1,0,0]];geometry['model_to_data']=M.tolist()
D=np.array(g.FLIP);point=np.array([2.,3.,11.,1.])
frame={'count':1,'record':{'id':'asymmetric','iteration':1,'kind':'preview'},'job':{'id':'session:fixture','sources':[]},'transport':'fixture','geometry':geometry,'geometry_revision':'fixture','data':[('position','FLOAT_VECTOR','vector',(D@point)[:3].astype(np.float32))]}
key=ext.ui_runtime.scene_key(scene)
for _ in ext.ui_runtime.build(scene,frame,key):pass
np.testing.assert_allclose(s.result_object.matrix_world,D@M)
collections=[c for c in scene.collection.children if c.get('lichtfeld_session_cameras')]
assert len(collections)==1 and len(collections[0].objects)==1
camera=collections[0].objects[0]
np.testing.assert_allclose(camera.matrix_world,pose)
assert camera.hide_select and camera.hide_render and s.session_camera_count==1
for _ in ext.ui_runtime.build(scene,frame,key):pass
assert len(collections[0].objects)==1
# A camera/pose-only update retains the exact PointCloud allocation and reaches
# the isolated color view without uploading or replacing its Gaussian data.
colors=importlib.import_module(ext.__package__+'.preview_scene')
for _ in colors.open_steps(scene,bpy.context.window,None):pass
color_scene=bpy.context.scene
clone=colors.scene_result(color_scene)
cloud=s.result_object.data
clouds=len(bpy.data.pointclouds)
updated={**geometry,'model_to_data':np.eye(4).tolist()}
updated['model_to_data'][0][3]=23
new_camera={**geometry['cameras'][0]}
new_pose=np.asarray(new_camera['camera_to_data']).copy();new_pose[0,3]+=11
new_camera['camera_to_data']=new_pose.tolist()
updated['cameras']=[new_camera]
metadata={**frame,'geometry':updated,'geometry_revision':'updated','geometry_only':True,'data':None}
for _ in ext.ui_runtime.build(scene,metadata,key):pass
assert s.result_object.data==cloud and clone.data==cloud and len(bpy.data.pointclouds)==clouds
np.testing.assert_allclose(s.result_object.matrix_world,g.source_model_transform(updated))
np.testing.assert_allclose(clone.matrix_world,s.result_object.matrix_world)
collection=next(c for c in scene.collection.children if c.get('lichtfeld_session_cameras'))
camera=collection.objects[0]
np.testing.assert_allclose(camera.matrix_world,np.asarray(g.FLIP)@new_pose@np.asarray(g.FLIP))
# A camera linked by the user elsewhere survives replacement of our helpers.
user_collection=bpy.data.collections.new('User retained cameras');scene.collection.children.link(user_collection)
user_collection.objects.link(camera)
s.show_session_cameras=False
for _ in session_cameras.visibility_steps(scene):pass
assert camera.hide_viewport
for _ in ext.ui_runtime.build(scene,{**metadata,'geometry':None,'geometry_revision':''},key):pass
assert camera.name in user_collection.objects
assert s.session_camera_count==0 and not any(c.get('lichtfeld_session_cameras') for c in scene.collection.children)
np.testing.assert_allclose(s.result_object.matrix_world,g.source_model_transform())
assert s.result_object.data==cloud and clone.data==cloud and len(bpy.data.pointclouds)==clouds
# A helper collection shared with another scene is detached only from this one.
for _ in ext.ui_runtime.build(scene,metadata,key):pass
shared=next(c for c in scene.collection.children if c.get('lichtfeld_session_cameras'))
other=bpy.data.scenes.new('Other scene keeps session cameras');other.collection.children.link(shared)
shared_camera=shared.objects[0]
for _ in session_cameras.remove_steps(scene):pass
assert shared.name not in scene.collection.children and shared.name in other.collection.children
assert shared_camera.name in shared.objects
# User content in a helper collection is retained, with the helper tag removed.
for _ in ext.ui_runtime.build(scene,{**metadata,'geometry_revision':'user-content'},key):pass
collection=next(c for c in scene.collection.children if c.get('lichtfeld_session_cameras'))
sentinel=bpy.data.objects.new('User sentinel in helper collection',None);collection.objects.link(sentinel)
for _ in session_cameras.remove_steps(scene):pass
assert sentinel.name in collection.objects and not collection.get('lichtfeld_session_cameras')
# Missing display data asks the reader to restore its complete immutable frame.
saved=s.result_object;s.result_object=None
try:
 list(ext.ui_runtime.build(scene,metadata,key))
except RuntimeError as error:
 assert 'reloading' in str(error)
else:raise AssertionError('Missing displayed model accepted a geometry-only update')
s.result_object=saved
colors.close_view(bpy.context)
print('POSE_ONLY_REUSES_GAUSSIANS_CAMERA_REMOVAL_AND_SHARED_OWNERSHIP_PASS',flush=True)
for _ in ext.ui_runtime.build(scene,frame,key):pass
camera=next(c for c in scene.collection.children if c.get('lichtfeld_session_cameras')).objects[0]
s.show_session_cameras=False
while ext.ui_runtime._actions:ext.ui_runtime.tick()
assert camera.hide_viewport
for _ in session_cameras.remove_steps(scene):pass
assert not any(c.get('lichtfeld_session_cameras') for c in scene.collection.children)
# Refresh uses a worker and keeps a selected trainer through a reordered list.
callbacks=[]
a={'id':'a','pid':1,'label':'A'};b={'id':'b','pid':2,'label':'B'}
with patch.object(ext.ui_runtime,'submit',side_effect=lambda fn,done,fail:callbacks.append(done)):
 ext.refresh_sessions(force=True);callbacks.pop()( [a,b] )
 s.discovered_session='b'
 ext.refresh_sessions(force=True);callbacks.pop()( [b,a] )
 assert s.discovered_session=='b'
 ext.refresh_sessions(force=True);callbacks.pop()( [] )
 assert s.discovered_session=='NONE'
print('ASYMMETRIC_MODEL_CAMERA_COORDINATES_AND_LIVE_DISCOVERY_PASS',flush=True)
ext.ui_runtime.clear()
