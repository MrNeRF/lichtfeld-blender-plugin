# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender: managed identity, target tracking and transaction cancellation."""
import bpy, sys, math, importlib, tempfile
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from mathutils import Vector
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
module = 'bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
bpy.ops.preferences.addon_enable(module=module)
ext = importlib.import_module(module)
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

# Render pauses cover updates, post-commit deletion, rollback and curve finally.
def datablocks():
    return (tuple((o.as_pointer(),o.data.as_pointer() if o.data else 0,
                   tuple(tuple(row) for row in o.matrix_world)) for o in bpy.data.objects),
            tuple(item.as_pointer() for item in bpy.data.cameras),
            tuple(item.as_pointer() for item in bpy.data.collections),
            tuple(item.as_pointer() for item in bpy.data.curves))

bpy.context.view_layer.update()
with patch.object(ext.rig_edit,'render_busy',return_value=True):
    before=datablocks()
    assert not bpy.ops.lichtfeld.create_rig.poll()
    try:bpy.ops.lichtfeld.create_rig()
    except RuntimeError as error:assert 'poll' in str(error)
    else:raise AssertionError('Rig started during render')
    assert datablocks()==before

for phase in ('update','committed','rollback','path'):
    guide=None
    s.views=8;s.rig_mode='SPHERE';s.rig_aim_mode='SOURCE'
    if phase=='path':
        curve=bpy.data.curves.new('Render pause path','CURVE');curve.dimensions='3D'
        spline=curve.splines.new('POLY');spline.points.add(1)
        spline.points[0].co=(-4,-4,2,1);spline.points[1].co=(4,-4,2,1)
        guide=bpy.data.objects.new('Render pause path',curve);bpy.context.scene.collection.objects.link(guide)
        s.rig_mode='PATH';s.rig_curve=guide
    bpy.context.view_layer.update()
    before=datablocks()
    owner=SimpleNamespace(collection=None,rig_backup=[],committed=False,cancelled=False,
        cancel_requested=False,render_waiting=False,scene=bpy.context.scene,report=lambda *args:None)
    owner.cleanup=lambda:ext.LF_OT_rig.cleanup(owner)
    owner.work=ext.LF_OT_rig.generate(owner,bpy.context)
    owner.timer=bpy.context.window_manager.event_timer_add(.02,window=bpy.context.window)
    s.operation_busy=True
    while True:
        next(owner.work)
        if (phase=='path' and len(bpy.data.curves)>len(before[3]) or
            phase=='committed' and owner.committed or
            phase in {'update','rollback'} and owner.rig_backup):break
    if phase=='rollback':
        owner.work.close();owner.work=owner.cleanup();owner.cancelled=True
    paused=datablocks()
    with patch.object(ext.rig_edit,'render_busy',return_value=True):
        for _ in range(3):
            assert ext.LF_OT_rig.modal(owner,bpy.context,SimpleNamespace(type='TIMER'))=={'PASS_THROUGH'}
        if phase in {'path','committed'}:
            ext.LF_OT_rig.modal(owner,bpy.context,SimpleNamespace(type='ESC'))
        assert datablocks()==paused,phase
        assert s.operation_busy
    result={'RUNNING_MODAL'}
    for _ in range(1000):
        result=ext.LF_OT_rig.modal(owner,bpy.context,SimpleNamespace(type='TIMER'))
        if result!={'RUNNING_MODAL'}:break
    assert result==({'CANCELLED'} if phase in {'rollback','path'} else {'FINISHED'}),(phase,result)
    assert not s.operation_busy
    if phase in {'rollback','path'}:assert datablocks()==before,phase
    if guide:
        data=guide.data;bpy.data.objects.remove(guide,do_unlink=True);bpy.data.curves.remove(data)
s.rig_mode='SPHERE';s.rig_curve=None
print('RIG_RENDER_PAUSE_CANCEL_ROLLBACK_AND_POSTCOMMIT_CLEANUP_PASS',flush=True)

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
collection['lichtfeld_rig_recipe']='{"views":24}'
collection['lichtfeld_rig_target']=42
try:
    result=bpy.ops.lichtfeld.read_rig()
    assert result=={'CANCELLED'}
except RuntimeError as error:
    assert 'Invalid rig object reference' in str(error),error
assert s.views==saved_views
del collection['lichtfeld_rig_target']
collection['lichtfeld_rig_recipe']=recipe
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

# Duplicating generated tags must never silently reassign or delete user cameras.
camera=next(o for o in collection.objects if o.type=='CAMERA')
duplicate=camera.copy();duplicate.data=camera.data.copy();collection.objects.link(duplicate)
duplicate.location.x+=7
bpy.context.view_layer.update()
s.views=4
recipe=collection['lichtfeld_rig_recipe']
baseline=[(o,o.data,o.matrix_world.copy()) for o in collection.objects if o.type=='CAMERA']
counts=(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections))
for index in (camera['lichtfeld_camera_index'], 'invalid', -1, True):
    duplicate['lichtfeld_camera_index']=index
    try:
        result=bpy.ops.lichtfeld.create_rig()
        assert result=={'CANCELLED'},result
    except RuntimeError as error:
        assert 'generated camera' in str(error).lower(),error
    assert counts==(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections))
    assert collection['lichtfeld_rig_recipe']==recipe
    for obj,data,matrix in baseline:
        assert obj.data==data
        assert max(abs(a-b) for x,y in zip(obj.matrix_world,matrix) for a,b in zip(x,y))<1e-5
data=duplicate.data
bpy.data.objects.remove(duplicate,do_unlink=True);bpy.data.cameras.remove(data)
duplicate=s.rig_aim.copy();collection.objects.link(duplicate)
try:
    result=bpy.ops.lichtfeld.create_rig()
    assert result=={'CANCELLED'},result
except RuntimeError as error:
    assert 'Multiple aim helpers' in str(error),error
assert collection['lichtfeld_rig_recipe']==recipe
bpy.data.objects.remove(duplicate,do_unlink=True)
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
print('RIG_DUPLICATE_AND_INVALID_INDEX_NO_MUTATION_PASS',flush=True)

# Changing the chosen rig neither loads controls nor permits an accidental Apply.
first=collection
first_views=s.views
s.camera_collection=None;s.views=8;s.rig_lens=60;s.rig_aim_mode='SOURCE'
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
second=s.camera_collection
assert s.rig_parameter_source==second
s.camera_collection=first
assert s.views==8 and s.rig_lens==60
assert not bpy.ops.lichtfeld.create_rig.poll() and not bpy.ops.lichtfeld.select_aim.poll()
assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
assert s.rig_parameter_source==first and s.views==first_views
s.views=6
s.camera_collection=second
assert s.views==6 and not bpy.ops.lichtfeld.create_rig.poll()
assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
assert s.views==8 and s.rig_lens==60 and s.rig_parameter_source==second
# Loading older files without this property requires an explicit Load too.
s.rig_parameter_source=None
assert not bpy.ops.lichtfeld.create_rig.poll()
assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
second_name=second.name
with tempfile.TemporaryDirectory() as directory:
    filepath=str(Path(directory)/'rig-controls.blend')
    bpy.ops.wm.save_as_mainfile(filepath=filepath)
    bpy.ops.wm.open_mainfile(filepath=filepath,load_ui=False)
    s=bpy.context.scene.lichtfeld_training
    assert s.rig_parameter_source==s.camera_collection and s.camera_collection.name==second_name
    assert bpy.ops.lichtfeld.create_rig.poll()
print('RIG_SELECTION_EXPLICIT_LOAD_APPLY_AND_SAVE_PASS',flush=True)

# Rig bounds stay independent of capture scope and subsequent viewport selection.
cube=bpy.data.objects['Cube']
rig=s.camera_collection
before={o.name:tuple(o.location) for o in rig.objects if o.type=='CAMERA'}
distant=cube.copy();distant.data=cube.data.copy();distant.name='Distant capture content'
bpy.context.scene.collection.objects.link(distant);distant.location=(100,0,0)
s.scope='SCENE'
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert before=={o.name:tuple(o.location) for o in rig.objects if o.type=='CAMERA'}
assert distant in ext.sources_for(bpy.context) and cube in ext.sources_for(bpy.context)
for obj in bpy.context.selected_objects:obj.select_set(False)
distant.select_set(True)
assert bpy.ops.lichtfeld.pick_sources(for_rig=True)=={'FINISHED'}
assert s.scope=='SCENE' and s.rig_reference.objects[0].object==distant
assert rig.lichtfeld_rig_reference.objects[0].object==cube
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert all((o.location-Vector(before[o.name])-Vector((100,0,0))).length<1e-4
           for o in rig.objects if o.type=='CAMERA')
# Loading the saved subject never rewrites the capture scope or source selection.
s.scope='COLLECTION';s.source_collection=bpy.data.collections['Rig subject']
captured=s.source_collection
s.rig_reference.mode='SCENE'
assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
assert s.scope=='COLLECTION' and s.source_collection==captured
assert s.rig_reference.mode=='OBJECTS' and s.rig_reference.objects[0].object==distant
distant.name='Renamed rig reference'
assert s.rig_reference.objects[0].object==distant
assert distant not in ext.preflight.capture_helpers(s)
rig_name=rig.name
with tempfile.TemporaryDirectory() as directory:
    filepath=str(Path(directory)/'independent-reference.blend')
    bpy.ops.wm.save_as_mainfile(filepath=filepath)
    bpy.ops.wm.open_mainfile(filepath=filepath,load_ui=False)
    s=bpy.context.scene.lichtfeld_training;rig=s.camera_collection
    distant=bpy.data.objects['Renamed rig reference']
    assert rig.name==rig_name and rig.lichtfeld_rig_reference.objects[0].object==distant
    assert s.rig_reference.objects[0].object==distant
    bpy.data.objects.remove(distant,do_unlink=True)
    before={o.name:tuple(o.location) for o in rig.objects if o.type=='CAMERA'}
    assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
    assert s.rig_reference.objects[0].object is None
    try:bpy.ops.lichtfeld.create_rig()
    except RuntimeError as error:assert 'rig reference object is missing' in str(error),error
    else:raise AssertionError('Missing reference silently changed the subject')
    assert before=={o.name:tuple(o.location) for o in rig.objects if o.type=='CAMERA'}
    assert len(ext.cameras_for(s))==8
    # Old managed rigs retain capture poses but cannot infer a historical subject.
    rig.lichtfeld_rig_reference.mode='UNSET'
    assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
    assert s.rig_reference.mode=='UNSET' and len(ext.cameras_for(s))==8
    try:bpy.ops.lichtfeld.create_rig()
    except RuntimeError as error:assert 'older rig' in str(error),error
    else:raise AssertionError('Legacy subject inferred from current capture scope')
    s.rig_reference.mode='COLLECTION';s.rig_reference.collection=s.source_collection
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    assert rig.lichtfeld_rig_reference.collection==s.source_collection
    assert s.scope=='COLLECTION'
    reference_collection=bpy.data.collections.new('Temporary rig reference')
    bpy.context.scene.collection.children.link(reference_collection)
    reference_collection.objects.link(bpy.data.objects['Cube'])
    s.rig_reference.collection=reference_collection
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    bpy.data.collections.remove(reference_collection)
    assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
    assert s.rig_reference.collection is None
    try:bpy.ops.lichtfeld.create_rig()
    except RuntimeError as error:assert 'rig reference collection' in str(error),error
    else:raise AssertionError('Deleted reference collection silently changed the subject')
    s.rig_reference.mode='SCENE'
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    assert rig.lichtfeld_rig_reference.mode=='SCENE' and s.scope=='COLLECTION'
print('INDEPENDENT_RIG_REFERENCE_CAPTURE_LOAD_RENAME_DELETE_LEGACY_SAVE_PASS',flush=True)

bpy.ops.preferences.addon_disable(module=module)
