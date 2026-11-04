# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock,patch
import bpy,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
bpy.ops.preferences.addon_enable(module='lichtfeld_training')
import lichtfeld_training as ext
from lichtfeld_training import visibility
scene=bpy.context.scene;s=scene.lichtfeld_training;layer=bpy.context.view_layer
cube=scene.objects['Cube']
bpy.ops.mesh.primitive_cube_add();hidden=bpy.context.object;hidden.name='Originally hidden';hidden.hide_set(True)
collection=bpy.data.collections.new('Instances');collection.objects.link(cube)
instance=bpy.data.objects.new('Collection instance',None);instance.instance_type='COLLECTION';instance.instance_collection=collection;scene.collection.objects.link(instance)
excluded=bpy.data.collections.new('Excluded');scene.collection.children.link(excluded)
mesh=bpy.data.meshes.new('Excluded mesh');widget=bpy.data.objects.new('WGT-Root',mesh);excluded.objects.link(widget)
layer.layer_collection.children[excluded.name].exclude=True
job={'id':'old','scope':'SCENE','view_layer':layer.name,'sources':['Cube','WGT-Root'],'source_visibility':{'Cube':False}}
for _ in visibility.source_steps(scene,job,True):pass
assert cube.hide_get() and instance.hide_get() and hidden.hide_get()
assert visibility.set_hidden(widget,True,layer) is False
probe=SimpleNamespace(name='Probe', hide_get=Mock(return_value=True), hide_set=Mock())
probe_layer=SimpleNamespace(objects={'Probe':probe})
assert visibility.set_hidden(probe,True,probe_layer)
probe.hide_set.assert_not_called()
for _ in visibility.source_steps(scene,job,False):pass
assert not cube.hide_get() and not instance.hide_get() and hidden.hide_get()
key=ext.ui_runtime.scene_key(scene)
def frame(job_id,artifact):
 return {'count':1,'record':{'id':artifact,'kind':'preview','iteration':1},'job':{**job,'id':job_id},'transport':'fixture',
  'data':[('position','FLOAT_VECTOR','vector',np.zeros(3,dtype=np.float32))]}
for _ in ext.ui_runtime.build(scene,frame('old','old-artifact'),key):pass
old=s.result_object
closed=Mock();current=SimpleNamespace(status={'job':job},close=closed,key='old')
ext.ui_runtime._bindings[key]=current
s.job_directory='/old/run'
with patch.object(ext.ui_runtime,'submit'):
 assert bpy.ops.lichtfeld.use_dataset()=={'FINISHED'}
assert not s.job_directory and not s.result_object
assert s.dataset_job_directory=='/old/run'
closed.assert_called_once()
# Deleting the old result while editing a new run must not reimport it.
bpy.data.objects.remove(old,do_unlink=True)
for _ in ext.ui_runtime.build(scene,frame('old','late-old-artifact'),key):pass
assert not s.result_object
while ext.ui_runtime._actions:
 ext.ui_runtime.tick()
scene['lichtfeld_expected_job']='fresh'
for _ in ext.ui_runtime.build(scene,frame('fresh','fresh-artifact'),key):pass
assert s.result_object.get('lichtfeld_job_id')=='fresh'
assert s.result_object.get('lichtfeld_artifact')=='fresh-artifact'
print('FRESH_RUN_ISOLATION_INSTANCE_VISIBILITY_AND_RESTORE_PASS',flush=True)
ext.ui_runtime.clear()
