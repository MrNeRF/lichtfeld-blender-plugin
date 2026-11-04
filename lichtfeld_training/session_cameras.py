# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded installation of session-owned camera previews, separate from capture rigs."""
import bpy
from mathutils import Matrix
from .session_geometry import matrix, FLIP


def remove_steps(scene):
 for collection in list(scene.collection.children):
  if not collection.get('lichtfeld_session_cameras'):continue
  if collection.users>1:
   scene.collection.children.unlink(collection)
   yield
   continue
  for obj in list(collection.objects):
   if obj.type!='CAMERA' or not obj.get('lichtfeld_session_camera'):
    continue
   if len(obj.users_collection)>1:
    collection.objects.unlink(obj)
    yield
    continue
   data=obj.data
   bpy.data.objects.remove(obj,do_unlink=True)
   yield
   if data and data.users==0:bpy.data.cameras.remove(data)
   yield
  if not collection.objects:
   bpy.data.collections.remove(collection)
  else:
   # Keep unrelated user objects that were moved into this collection.
   del collection['lichtfeld_session_cameras']
  yield
 scene.pop('lichtfeld_camera_geometry',None)
 scene.lichtfeld_training.session_camera_count=0


def visibility_steps(scene):
 for collection in scene.collection.children:
  if collection.get('lichtfeld_session_cameras'):
   for obj in collection.objects:
    obj.hide_viewport=not scene.lichtfeld_training.show_session_cameras
    yield


def install_steps(scene,geometry,revision):
 if scene.get('lichtfeld_camera_geometry')==revision:return
 yield from remove_steps(scene)
 if not geometry or not geometry.get('cameras'):
  scene['lichtfeld_camera_geometry']=revision
  return
 collection=bpy.data.collections.new('LichtFeld session cameras · read only')
 collection['lichtfeld_session_cameras']=True
 scene.collection.children.link(collection)
 yield
 to_source=matrix(geometry.get('data_to_source',FLIP))
 import numpy as np
 flip=np.asarray(FLIP)
 for index,camera in enumerate(geometry.get('cameras',[])):
  data=bpy.data.cameras.new('Session camera '+str(index+1))
  data.sensor_fit='HORIZONTAL';data.sensor_width=36
  data.lens=camera['fx']*36/camera['width']
  data.shift_x=(camera['width']/2-camera['cx'])/camera['width']
  data.shift_y=(camera['cy']-camera['height']/2)/camera['width']
  obj=bpy.data.objects.new(camera['name'],data)
  obj['lichtfeld_session_camera']=True
  for name in ('width','height','fx','fy','cx','cy'):obj['lichtfeld_'+name]=camera[name]
  obj.matrix_world=Matrix((to_source@matrix(camera['camera_to_data'])@flip).tolist())
  obj.hide_render=True;obj.hide_select=True
  collection.objects.link(obj)
  scene.lichtfeld_training.session_camera_count=index+1
  yield
  obj.hide_viewport=not scene.lichtfeld_training.show_session_cameras
  yield
 scene['lichtfeld_camera_geometry']=revision
