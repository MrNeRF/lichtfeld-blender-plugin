# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import sys
import bpy
from mathutils import Vector
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
bpy.ops.preferences.addon_enable(module='lichtfeld_training')
import lichtfeld_training as ext
s=bpy.context.scene.lichtfeld_training
original=bpy.data.objects['Camera'];original.keyframe_insert('location',frame=1)
action=original.animation_data.action;original_lens=original.data.lens
bpy.ops.mesh.primitive_cube_add(size=8,location=(0,0,0))
helper=bpy.context.object;helper.name='Placement envelope';helper.scale=(1,1.5,2)
s.rig_mode='MESH';s.rig_mesh=helper;s.rig_mesh_distribution='FACES';s.rig_mesh_offset=.1;s.scope='SCENE'
s.rig_aim_mode='OBJECT';s.rig_target=None  # Unused target settings must never block normals.
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
collection=s.camera_collection
cameras=sorted((o for o in collection.objects if o.type=='CAMERA'),key=lambda o:o['lichtfeld_camera_index'])
assert len(cameras)==6 and original.animation_data.action==action and original.data.lens==original_lens
assert not helper.hide_render and helper not in ext.sources_for(bpy.context)
capture=ext.preflight.synchronous(bpy.context,ext.preferences())
assert helper.name not in capture['sources']
job=dict(id='helper-visibility',sources=capture['sources'],visibility_sources=capture['visibility_sources'],source_visibility=capture['visibility'])
for _ in ext.ui_runtime.source_steps(bpy.context.scene,job,True):pass
assert helper.hide_get()
for _ in ext.ui_runtime.source_steps(bpy.context.scene,job,False):pass
assert not helper.hide_get()
bpy.context.view_layer.update()
for c in cameras:
    forward=c.matrix_world.to_quaternion()@Vector((0,0,-1))
    assert forward.dot(-c.matrix_world.translation.normalized())>.999
ids=[c.as_pointer() for c in cameras]
s.rig_mesh_orientation='OUTWARD'
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
bpy.context.view_layer.update()
for c in cameras:
    assert (c.matrix_world.to_quaternion()@Vector((0,0,-1))).dot(c.matrix_world.translation.normalized())>.999
assert [c.as_pointer() for c in cameras]==ids
s.rig_mesh_distribution='SURFACE';s.views=41
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert sum(o.type=='CAMERA' for o in collection.objects)==41
s.rig_mesh_orientation='TARGET';s.rig_aim_mode='SOURCE';s.rig_aim_offset=(.2,.3,.4)
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert all(o.constraints.get(ext.rig_edit.AIM) for o in collection.objects if o.type=='CAMERA')
recipe=ext.rig_recipe(s);s.rig_mesh_orientation='INWARD';bpy.ops.lichtfeld.read_rig()
assert ext.rig_recipe(s)==recipe
helper.name='Renamed placement envelope';s.rig_mesh=None
assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'} and s.rig_mesh==helper
data=bpy.data.cameras.new('Mirrored manual camera');camera=bpy.data.objects.new('Mirrored manual camera',data)
collection.objects.link(camera)
camera.scale.x=-1;bpy.context.view_layer.update()
try:ext.cameras_for(s)
except ValueError:pass
else:raise AssertionError('Mirrored camera passed capture validation')
bpy.data.objects.remove(camera,do_unlink=True);bpy.data.cameras.remove(data)
print('MESH_RIG_NONDESTRUCTIVE_NORMALS_COUNT_TARGET_AND_RECIPE_PASS',flush=True)
ext.ui_runtime.clear()
