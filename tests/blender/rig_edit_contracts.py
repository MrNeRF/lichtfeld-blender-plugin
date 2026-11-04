# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender: managed identity, target tracking and transaction cancellation."""
import bpy, sys, math
from types import SimpleNamespace
from pathlib import Path
from mathutils import Vector
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
bpy.ops.preferences.addon_enable(module="lichtfeld_training")
import lichtfeld_training as ext
s=bpy.context.scene.lichtfeld_training
subject=bpy.data.collections.new("Rig subject")
bpy.context.scene.collection.children.link(subject)
cube=bpy.data.objects.get("Cube")
for collection in list(cube.users_collection):collection.objects.unlink(cube)
subject.objects.link(cube)
s.scope="COLLECTION";s.source_collection=subject;s.views=8;s.rig_mode="SPHERE"
assert bpy.ops.lichtfeld.create_rig()=={"FINISHED"}
collection=s.camera_collection
original=sorted((o for o in collection.objects if o.type=="CAMERA"),key=lambda o:o["lichtfeld_camera_index"])
ids=[o.as_pointer() for o in original];target=s.rig_aim;target_id=target.as_pointer()
locations=[o.location.copy() for o in original]
manual=bpy.data.objects.new("User helper",None);collection.objects.link(manual)
s.rig_aim_offset=(0,0,.5)
assert bpy.ops.lichtfeld.create_rig()=={"FINISHED"}
assert s.camera_collection==collection and manual.name in collection.objects
updated=sorted((o for o in collection.objects if o.type=="CAMERA"),key=lambda o:o["lichtfeld_camera_index"])
assert [o.as_pointer() for o in updated]==ids and s.rig_aim.as_pointer()==target_id
assert all((o.location-p).length<1e-6 for o,p in zip(updated,locations)),"Aim changes moved camera positions"
target.location.z+=.25
bpy.context.view_layer.update()
for camera in updated:
    forward=camera.matrix_world.to_quaternion()@Vector((0,0,-1))
    desired=(target.matrix_world.translation-camera.matrix_world.translation).normalized()
    assert forward.dot(desired)>.9999
print("RIG_IDENTITIES_AND_LIVE_AIM_PASS")
s.views=12
assert bpy.ops.lichtfeld.create_rig()=={"FINISHED"}
assert all(o.as_pointer()==ids[i] for i,o in enumerate(updated))
assert sum(o.type=="CAMERA" for o in collection.objects)==12
s.views=6
assert bpy.ops.lichtfeld.create_rig()=={"FINISHED"}
assert sum(o.type=="CAMERA" for o in collection.objects)==6
assert manual.name in collection.objects
print("RIG_COUNT_EDIT_PRESERVES_MANUAL_OBJECTS_PASS")
# Every pre-commit yield must roll existing transforms/data/constraints back.
s.rig_distance=5
baseline=[(o,o.data,o.matrix_world.copy(),o.constraints.get(ext.rig_edit.AIM).target) for o in collection.objects if o.type=="CAMERA"]
base_counts=(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections))
completed=False
for cut in range(150):
    operator=SimpleNamespace(collection=None,rig_backup=[],committed=False,report=lambda *args:None)
    work=ext.LF_OT_rig.generate(operator,bpy.context)
    try:
        for _ in range(cut):next(work)
    except StopIteration:
        completed=True;break
    if operator.committed:
        for _ in work:pass
        completed=True;break
    work.close()
    for _ in ext.LF_OT_rig.cleanup(operator):pass
    assert base_counts==(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections)),cut
    bpy.context.view_layer.update()
    for obj,data,matrix,aim in baseline:
        assert obj.data==data and obj.constraints.get(ext.rig_edit.AIM).target==aim,cut
        assert max(abs(a-b) for x,y in zip(obj.matrix_world,matrix) for a,b in zip(x,y))<1e-5,cut
assert completed
print("RIG_CANCEL_EVERY_PRECOMMIT_YIELD_PASS",cut)

# Loaded recipes restore controls; invalid metadata must not partially edit them.
assert bpy.ops.lichtfeld.read_rig()=={"FINISHED"}
recipe=collection["lichtfeld_rig_recipe"]
saved_views=s.views
collection["lichtfeld_rig_recipe"]='{"views":24,"rig_mode":"INVALID"}'
try:
    result=bpy.ops.lichtfeld.read_rig()
    assert result=={"CANCELLED"}
except RuntimeError:pass
assert s.views==saved_views
collection["lichtfeld_rig_recipe"]=recipe
# Choosing the viewport target keeps its moved position when other rig settings change.
assert bpy.ops.lichtfeld.select_aim()=={"FINISHED"}
assert s.rig_aim_mode=="VIEWPORT"
s.rig_aim.location.z+=.4
bpy.context.view_layer.update()
point=s.rig_aim.matrix_world.translation.copy()
s.rig_lens=45
assert bpy.ops.lichtfeld.create_rig()=={"FINISHED"}
bpy.context.view_layer.update()
assert (s.rig_aim.matrix_world.translation-point).length<1e-5
external=bpy.data.objects.new("External target",None);bpy.context.scene.collection.objects.link(external)
external.location=(.1,.2,.3);s.rig_aim_mode="OBJECT";s.rig_target=external;s.rig_aim_offset=(0,0,.5)
assert bpy.ops.lichtfeld.create_rig()=={"FINISHED"}
external.location.z+=.2
bpy.context.view_layer.update()
assert (s.rig_aim.matrix_world.translation-(external.location+Vector(s.rig_aim_offset))).length<1e-5
print("RIG_RECIPE_AND_OBJECT_TRACKING_PASS")


s.scope="SELECTION"
for obj in bpy.context.selected_objects:obj.select_set(False)
cube.select_set(True);bpy.context.view_layer.objects.active=cube
assert bpy.ops.lichtfeld.create_rig()=={"FINISHED"}
assert bpy.ops.lichtfeld.select_aim()=={"FINISHED"}
assert ext.sources_for(bpy.context)==[cube]
assert bpy.ops.lichtfeld.create_rig()=={"FINISHED"}
work=ext.preflight.collect(bpy.context)
while True:
    try:next(work)
    except StopIteration as stop:
        assert stop.value["sources"]==[cube.name]
        break
print("RIG_EDIT_RETAINS_SOURCE_SELECTION_PASS")

bpy.ops.preferences.addon_disable(module="lichtfeld_training")
