# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender: managed identity, target tracking and transaction cancellation."""
import bpy, sys, math, importlib, tempfile, time, json
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
assert all(tuple(obj.users_collection)==(collection,) for obj in collection.objects if obj.type=='CAMERA')
assert not any(obj.type=='CAMERA' and obj.get('lichtfeld_generated') for obj in bpy.context.scene.collection.objects)
original=sorted((o for o in collection.objects if o.type=="CAMERA"),key=lambda o:o["lichtfeld_camera_index"])
ids=[o.as_pointer() for o in original];target=s.rig_aim;target_id=target.as_pointer()
s.rig_auto_fit=False;s.rig_radius=3.25
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert collection['lichtfeld_generated_radius']==3.25 and not collection['lichtfeld_fit_limited']
assert all(abs(o.location.length-3.25)<1e-5 for o in original)
s.rig_auto_fit=True
with patch.object(ext.capture_settings,'fitting_factor',return_value=10):
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert collection['lichtfeld_fit_limited']
assert all(abs(o.location.length-collection['lichtfeld_generated_radius'])<1e-5 for o in original)
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
locations=[o.location.copy() for o in original]
assert all(abs(o.location.length-collection['lichtfeld_generated_radius'])<1e-5 for o in original)
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
base_radius=collection['lichtfeld_generated_radius']
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
    assert collection['lichtfeld_generated_radius']==base_radius,cut
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
        cancel_requested=False,render_waiting=False,history_cancelled=False,scene=bpy.context.scene,report=lambda *args:None)
    owner.cleanup=lambda:ext.LF_OT_rig.cleanup(owner)
    owner.remove_timer=lambda manager:ext._RigOperation.remove_timer(owner,manager)
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

# A non-radial rig cannot retain a stale radius from its preceding sphere.
curve=bpy.data.curves.new('Distance feedback path','CURVE');curve.dimensions='3D'
spline=curve.splines.new('POLY');spline.points.add(1)
spline.points[0].co=(-4,-4,2,1);spline.points[1].co=(4,-4,2,1)
guide=bpy.data.objects.new('Distance feedback path',curve);bpy.context.scene.collection.objects.link(guide)
s.rig_mode='PATH';s.rig_curve=guide
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert 'lichtfeld_generated_radius' not in collection and 'lichtfeld_fit_limited' not in collection
s.rig_mode='SPHERE';s.rig_curve=None
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
bpy.data.objects.remove(guide,do_unlink=True);bpy.data.curves.remove(curve)
print('GENERATED_DISTANCE_MANUAL_AUTOFIT_CANCEL_AND_PATH_PASS',flush=True)

# Loaded recipes restore controls; invalid metadata must not partially edit them.
assert bpy.ops.lichtfeld.read_rig()=={"FINISHED"}
recipe=collection["lichtfeld_rig_recipe"]
s.rig_path_sampling='SPACING';s.rig_path_spacing=2;s.rig_path_reverse=True
s.rig_station_views=12;s.rig_station_sweep=1;s.rig_station_frame='TARGET'
s.rig_position_variation=5;s.rig_rotation_variation=.04;s.rig_variation_seed=73
assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
assert s.rig_path_sampling=='COUNT' and s.rig_path_spacing==1 and not s.rig_path_reverse
assert s.rig_position_variation==0 and s.rig_rotation_variation==0 and s.rig_variation_seed==0
assert s.rig_station_frame=='GUIDE'
assert s.rig_station_views==8 and abs(s.rig_station_sweep-6.283185307179586)<1e-6
assert ext.rig_recipe(s)==recipe
saved_views=s.views
for field, invalid in (('rig_path_sampling','UNKNOWN'),('rig_path_spacing',0),
                       ('rig_path_spacing',float('nan')),('rig_path_reverse',1),('rig_path_link',1),
                       ('rig_station_frame','INVALID'),('rig_station_views',3),('rig_station_views',4.5),('rig_station_views',True),
                       ('rig_station_sweep',0),('rig_station_sweep',7),('rig_station_sweep',float('nan')),
                       ('rig_rotation_variation',-.01),('rig_rotation_variation',1),
                       ('rig_rotation_variation',float('nan')),('rig_position_variation',26),('rig_position_variation',float('nan')),
                       ('rig_variation_seed',-1),('rig_variation_seed',1.5),('rig_variation_seed',True)):
    values=json.loads(recipe);values[field]=invalid;values['views']=24
    collection['lichtfeld_rig_recipe']=json.dumps(values)
    try:
        assert bpy.ops.lichtfeld.read_rig()=={'CANCELLED'}
    except RuntimeError:
        pass
    assert s.views==saved_views and s.rig_path_sampling=='COUNT' and not s.rig_path_reverse
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

def reference_ready():
    # Exercise the existing cooperative cache, including invalidation at equal counts.
    with patch.object(ext.preflight,'environment',return_value={'ok':True}), \
         patch.object(ext.scene_render,'tick'), \
         patch.object(ext.scene_render,'readiness',return_value={'ok':True}):
        for _ in range(1000):
            ext.preflight.tick(bpy.context,None)
            ready=ext.preflight.cached(bpy.context,None)
            if not ready.get('checking'):
                return ready['reference_changed']
            time.sleep(.005)
    raise AssertionError('Reference preflight did not finish')

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
assert not reference_ready()
for obj in bpy.context.selected_objects:obj.select_set(False)
distant.select_set(True)
assert bpy.ops.lichtfeld.pick_sources(for_rig=True)=={'FINISHED'}
assert s.scope=='SCENE' and s.rig_reference.objects[0].object==distant
assert rig.lichtfeld_rig_reference.objects[0].object==cube
assert reference_ready(), 'A same-count reference change reused stale readiness'
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert not reference_ready(), 'Apply did not clear the pending reference change'
assert all((o.location-Vector(before[o.name])-Vector((100,0,0))).length<1e-4
           for o in rig.objects if o.type=='CAMERA')
# Loading the saved subject never rewrites the capture scope or source selection.
s.scope='COLLECTION';s.source_collection=bpy.data.collections['Rig subject']
captured=s.source_collection
s.rig_reference.mode='SCENE'
assert reference_ready()
assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
assert not reference_ready(), 'Load did not restore the saved reference state'
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

# Names are not identities: a foreign linked Cube must not match the local Cube.
cube=bpy.data.objects['Cube']
with tempfile.TemporaryDirectory() as directory:
    library=Path(directory)/'reference-library.blend'
    bpy.data.libraries.write(str(library),{cube})
    with bpy.data.libraries.load(str(library),link=True) as (source,loaded):
        loaded.objects=['Cube']
    linked=loaded.objects[0]
    other=bpy.data.scenes.new('Foreign reference scene')
    group=bpy.data.collections.new('Foreign reference group')
    other.collection.children.link(group);group.objects.link(linked)
    assert linked.name==cube.name and linked not in list(bpy.context.scene.objects)
    s.rig_reference.mode='OBJECTS'
    s.rig_reference['objects']=[{'object':linked,'name':linked.name}]
    before={o.name:tuple(o.location) for o in s.camera_collection.objects if o.type=='CAMERA'}
    try:bpy.ops.lichtfeld.create_rig()
    except RuntimeError as error:assert 'rig reference object is missing' in str(error),error
    else:raise AssertionError('Foreign linked object matched a local object with the same name')
    assert before=={o.name:tuple(o.location) for o in s.camera_collection.objects if o.type=='CAMERA'}
    s.rig_reference.mode='COLLECTION';s.rig_reference.collection=group
    try:bpy.ops.lichtfeld.create_rig()
    except RuntimeError as error:assert 'no surface objects in this scene' in str(error),error
    else:raise AssertionError('A foreign collection matched local objects by name')
    bpy.context.scene.collection.children.link(group)
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    s.rig_reference.mode='OBJECTS'
    s.rig_reference['objects']=[{'object':obj,'name':obj.name} for obj in (cube,linked)]
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    assert [entry.object for entry in s.camera_collection.lichtfeld_rig_reference.objects]==[cube,linked]
print('RIG_LINKED_OMONYMS_FOREIGN_REJECTION_AND_LOCAL_ACCEPTANCE_PASS',flush=True)

bpy.ops.preferences.addon_disable(module=module)
# Live radial controls preserve IDs and use native dependency-graph evaluation.
bpy.ops.wm.read_factory_settings(use_empty=False)
bpy.ops.preferences.addon_enable(module=module)
s = bpy.context.scene.lichtfeld_training
s.scope = 'SCENE'; s.views = 8; s.rig_mode = 'HEMISPHERE'
s.rig_auto_fit = False; s.rig_radius = 3
s.rig_distance = 1.05
assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
rig = s.camera_collection
assert s.rig_distance < 1.05  # Native RNA boundary rounded to float32.
assert ext.rig_edit.decode_recipe(rig['lichtfeld_rig_recipe'])['rig_distance']==s.rig_distance
s.rig_distance = 2.8
assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
assert s.rig_distance < 1.05
invalid=json.loads(rig['lichtfeld_rig_recipe']);invalid['rig_distance']=1.0
try:
    ext.rig_edit.decode_recipe(json.dumps(invalid))
    raise AssertionError('An actual out-of-range rig distance was accepted')
except ValueError:
    pass
cams = sorted((o for o in rig.objects if o.type == 'CAMERA'), key=lambda o: o['lichtfeld_camera_index'])
bpy.context.view_layer.update()
poses = [o.matrix_world.copy() for o in cams]
ids = [(o.as_pointer(), o.data.as_pointer()) for o in cams]
extra = bpy.data.objects.new('Additional camera', bpy.data.cameras.new('Additional camera'))
rig.objects.link(extra)
extra.location = (20, 30, 40)
assert bpy.ops.lichtfeld.live_rig() == {'FINISHED'}
root = ext.rig_edit.live_root(s)
assert root
assert ext.rig_edit.LF_GGT_live_rig.poll(bpy.context)
bpy.context.view_layer.update()
assert all(max(abs(a-b) for x,y in zip(o.matrix_world,m) for a,b in zip(x,y)) < 1e-5 for o,m in zip(cams,poses))
assert not ext.LF_OT_rig.poll(bpy.context)
s.live_radius = 6
s.live_phase = math.pi / 2
s.live_aim = (0, 0, .5)
bpy.context.view_layer.update()
from mathutils import Matrix
rotation = Matrix.Rotation(math.pi / 2, 4, 'Z')
for camera, before in zip(cams, poses):
    assert (camera.matrix_world.translation - rotation @ (before.translation * 2)).length < 1e-4
    assert all(abs(v-1) < 1e-5 for v in camera.matrix_world.to_scale())
    ext.capture_settings.camera_pose(camera, bpy.context.evaluated_depsgraph_get())
    forward = camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))
    assert forward.dot((s.rig_aim.matrix_world.translation - camera.matrix_world.translation).normalized()) > .99999
assert tuple(extra.location) == (20, 30, 40) and extra.parent is None
assert ids == [(o.as_pointer(),o.data.as_pointer()) for o in cams]
live_poses = [o.matrix_world.copy() for o in cams]
clips = [(o.data.clip_start, o.data.clip_end) for o in cams]
assert all(o.animation_data is None or o.animation_data.action is None for o in [root, *cams])
# Save/reload uses simple native drivers, with no Python autoexecution.
with tempfile.TemporaryDirectory() as directory:
    path = str(Path(directory) / 'live-rig.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path, use_scripts=False)
s = bpy.context.scene.lichtfeld_training
rig = s.camera_collection
cams = sorted((o for o in rig.objects if o.type == 'CAMERA' and o.get('lichtfeld_generated')), key=lambda o:o['lichtfeld_camera_index'])
assert ext.rig_edit.live_root(s) and not bpy.app.autoexec_fail
bpy.context.view_layer.update()
assert all((o.matrix_world.translation-m.translation).length < 1e-4 for o,m in zip(cams, live_poses))
# Every precommit yield in Finish can be cancelled without changing the live rig.
for cut in range(40):
    op = SimpleNamespace(live_backup=[], live_controller=None, live_finishing=True, committed=False)
    work = ext.rig_edit.edit_live(op, bpy.context)
    try:
        for _ in range(cut): next(work)
    except StopIteration:
        break
    if op.committed:
        for _ in work: pass
        break
    work.close()
    for _ in ext.rig_edit.rollback_live(op): pass
    bpy.context.view_layer.update()
    assert ext.rig_edit.live_root(s)
    assert all((o.matrix_world.translation-m.translation).length < 1e-4 for o,m in zip(cams,live_poses)), cut
else:
    raise AssertionError('Finish never committed')
assert not ext.rig_edit.live_root(s)
bpy.context.view_layer.update()
assert all(o.parent is None for o in cams)
assert all((o.matrix_world.translation-m.translation).length < 1e-4 for o,m in zip(cams,live_poses))
assert all(abs(o.data.clip_end-far) < 1e-3 for o,(near,far) in zip(cams,clips))
assert s.rig_radius == 6 and not s.rig_auto_fit and abs(s.rig_phase-math.pi/2) < 1e-6
assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
bpy.context.view_layer.update()
assert all((o.matrix_world.translation-m.translation).length < 1e-4 for o,m in zip(cams,live_poses))
print('LIVE_RIG_NATIVE_POSES_SAVE_RELOAD_CANCEL_FINISH_REAPPLY_PASS')
# Cancellation during entry leaves no native controller, drivers or changed IDs.
s = bpy.context.scene.lichtfeld_training
baseline = [(o, o.matrix_world.copy(), o.data.clip_start, o.data.clip_end) for o in cams]
counts = (len(bpy.data.objects), len(bpy.data.cameras))
for cut in range(120):
    op = SimpleNamespace(live_backup=[], live_controller=None, live_finishing=False, committed=False)
    work = ext.rig_edit.edit_live(op, bpy.context)
    try:
        for _ in range(cut): next(work)
    except StopIteration:
        break
    if op.committed:
        for _ in work: pass
        break
    work.close()
    for _ in ext.rig_edit.rollback_live(op): pass
    bpy.context.view_layer.update()
    assert counts == (len(bpy.data.objects), len(bpy.data.cameras)), cut
    for camera, matrix, near, far in baseline:
        assert camera.parent is None and not camera.animation_data and not camera.data.animation_data, cut
        assert max(abs(a-b) for x,y in zip(camera.matrix_world,matrix) for a,b in zip(x,y)) < 1e-5, cut
assert ext.rig_edit.live_root(s)
assert bpy.ops.lichtfeld.live_rig() == {'FINISHED'}
print('LIVE_RIG_ENTRY_CANCEL_EVERY_YIELD_PASS')

# A translated center, nonzero initial orbit and following aim remain independent.
for shape in ('SPHERE', 'RINGS'):
    s.rig_mode = shape; s.rig_phase = .4; s.rig_radius = 4
    cube = bpy.data.objects['Cube']; cube.location = (10, -7, 3)
    s.rig_aim_mode = 'OBJECT'; s.rig_target = cube; s.rig_aim_offset = (0, 0, .3)
    bpy.context.view_layer.update()
    assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
    bpy.context.view_layer.update()
    cameras = [o for o in s.camera_collection.objects if o.type=='CAMERA' and o.get('lichtfeld_generated')]
    before = [o.matrix_world.copy() for o in cameras]
    assert bpy.ops.lichtfeld.live_rig() == {'FINISHED'}
    bpy.context.view_layer.update()
    root = ext.rig_edit.live_root(s)
    assert (root.location-cube.location).length < 1e-4
    s.live_radius = 5; s.live_phase = .8; s.live_aim = (.2, -.1, .6)
    bpy.context.view_layer.update()
    poses = [o.matrix_world.copy() for o in cameras]
    with patch.object(ext.rig_edit, 'render_busy', return_value=True):
        s.live_radius = 15; s.live_aim = (20, 20, 20)
        assert s.live_radius == 5 and abs(s.live_aim[0]-.2)<1e-6
        assert not ext.LF_OT_live_rig.poll(bpy.context)
    assert bpy.ops.lichtfeld.live_rig() == {'FINISHED'}
    bpy.context.view_layer.update()
    assert all(max(abs(a-b) for x,y in zip(o.matrix_world,m) for a,b in zip(x,y)) < 1e-4 for o,m in zip(cameras,poses))
    assert s.rig_aim_mode == 'OBJECT' and (Vector(s.rig_aim_offset)-Vector((.2,-.1,.6))).length < 1e-5
    assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
    bpy.context.view_layer.update()
    assert all((o.matrix_world.translation-m.translation).length < 1e-4 for o,m in zip(cameras,poses))
print('LIVE_RIG_TRANSLATED_CENTER_ALL_RADIAL_SHAPES_FOLLOW_RENDER_GUARDS_PASS')

# Leaving untouched controls preserves their original automatic/reference modes.
s.rig_aim_mode='SOURCE';s.rig_aim_offset=(0,0,0);s.rig_auto_fit=True
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
s.live_radius = s.live_radius  # Native RNA/Escape round-trip at an automatic fitted radius.
s.live_phase += .1
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
assert s.rig_auto_fit and s.rig_aim_mode=='SOURCE'
# Native deletion of the owned controller can be repaired by the canonical Apply.
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
root=ext.rig_edit.live_root(s)
bpy.data.objects.remove(root,do_unlink=True)
assert ext.rig_edit.live_root(s) is None
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
print('LIVE_RIG_UNTOUCHED_MODES_AND_DELETED_CONTROLLER_REPAIR_PASS')
# Object deletion/duplication while a camera scan is suspended is memory-safe.
for action in ('DELETE','DUPLICATE'):
    work=ext.preflight.collect(bpy.context)
    while len(work.gi_frame.f_locals.get('cameras',()))<2:next(work)
    victim=work.gi_frame.f_locals['obj']
    for obj in bpy.context.selected_objects:obj.select_set(False)
    victim.select_set(True);bpy.context.view_layer.objects.active=victim
    if action=='DELETE':bpy.ops.object.delete()
    else:bpy.ops.object.duplicate()
    try:
        for _ in work:pass
    except ReferenceError:pass  # The canonical scan owner retries at the next tick.
    fresh=ext.preflight.collect(bpy.context)
    for _ in fresh:pass
print('SUSPENDED_CAMERA_SCAN_NATIVE_DELETE_DUPLICATE_SAFE_PASS')
# Repair deliberate duplicate metadata before exercising interruption cleanup.
seen=set()
for camera in list(s.camera_collection.objects):
    if camera.type!='CAMERA' or not camera.get('lichtfeld_generated'):continue
    index=camera['lichtfeld_camera_index']
    if index in seen:bpy.data.objects.remove(camera,do_unlink=True)
    else:seen.add(index)
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
op=SimpleNamespace(live_backup=[],live_controller=None,live_finishing=False,committed=False)
work=ext.rig_edit.edit_live(op,bpy.context)
while len(op.live_backup)<4:next(work)
victim=op.live_backup[1][0]
for obj in bpy.context.selected_objects:obj.select_set(False)
victim.select_set(True);bpy.context.view_layer.objects.active=victim
bpy.ops.object.delete()
work.close()
for _ in ext.rig_edit.rollback_live(op):pass
assert ext.rig_edit.live_root(s) is None
assert not any(o.get(ext.rig_edit.LIVE) is True for o in bpy.data.objects)
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
s.live_radius *= 1.1;bpy.context.view_layer.update()
cameras=[o for o in s.camera_collection.objects if o.type=='CAMERA' and o.get('lichtfeld_generated')]
poses=[o.matrix_world.copy() for o in cameras]
bpy.ops.preferences.addon_disable(module=module)
bpy.context.view_layer.update()
assert all((o.matrix_world.translation-m.translation).length<1e-4 for o,m in zip(cameras,poses))
bpy.ops.preferences.addon_enable(module=module)
s=bpy.context.scene.lichtfeld_training
assert ext.rig_edit.live_root(s)
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
print('LIVE_RIG_DELETED_BACKUP_CLEANUP_DISABLE_REENABLE_NATIVE_POSES_PASS')

# Explicit user references retain a finished helper; ordinary scene bases do not.
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
root=ext.rig_edit.live_root(s)
bpy.context.scene['Retained controller']=root
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
assert bpy.context.scene['Retained controller']==root
assert root.name in bpy.data.objects
bpy.context.scene.pop('Retained controller')
bpy.data.objects.remove(root,do_unlink=True)
print('LIVE_CONTROLLER_USER_REFERENCE_RETAINED_PASS')
# Native cancellation while rendering retains IDs and resumes the same Finish.
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
s.live_radius += .15
bpy.context.view_layer.update()
cameras=[o for o in s.camera_collection.objects if o.type=='CAMERA' and o.get('lichtfeld_generated')]
poses=[o.matrix_world.copy() for o in cameras]
op=SimpleNamespace(live_backup=[],live_controller=None,live_finishing=True,committed=False,
    history_cancelled=False,rig_backup=[],collection=None,scene=bpy.context.scene,report=lambda *args:None)
op.work=ext.rig_edit.edit_live(op,bpy.context)
while not op.committed:next(op.work)
with patch.object(ext.rig_edit,'render_busy',return_value=True):
    ext._RigOperation.release(op,restore=True)
root=ext.rig_edit.live_root(s)
assert root and root['finishing']
old=s.live_radius;s.live_radius=100
assert s.live_radius==old
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
bpy.context.view_layer.update()
assert all((o.matrix_world.translation-m.translation).length<1e-4 for o,m in zip(cameras,poses))
assert ext.rig_edit.live_root(s) is None
print('INTERRUPTED_FINISH_RENDER_GUARD_RESUMES_CANONICAL_BAKE_PASS')

# An incomplete entry persisted by Save/Undo is repaired by the existing Apply.
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
op=SimpleNamespace(live_backup=[],live_controller=None,live_finishing=False,committed=False)
work=ext.rig_edit.edit_live(op,bpy.context)
while len(op.live_backup)<2:next(work)
work.close()  # Deliberately retain the same state a native save/undo step captures.
with tempfile.TemporaryDirectory() as directory:
    path=str(Path(directory)/'interrupted-entry.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path,use_scripts=False)
s=bpy.context.scene.lichtfeld_training
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert all(o.parent is None for o in s.camera_collection.objects if o.type=='CAMERA')
assert not any(o.get(ext.rig_edit.LIVE) is True for o in bpy.data.objects)
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
print('PERSISTED_PARTIAL_ENTRY_APPLY_REPAIRS_CANONICAL_PARENT_AND_DRIVERS_PASS')
# Live entry uses the same perspective/rigid-pose contract as capture.
camera=next(o for o in s.camera_collection.objects if o.type=='CAMERA')
counts=(len(bpy.data.objects),len(bpy.data.cameras))
camera.data.type='ORTHO'
try:
    bpy.ops.lichtfeld.live_rig()
except RuntimeError as error:
    assert camera.name in str(error) and 'Perspective' in str(error)
else:raise AssertionError('Live editing accepted an unsupported capture camera')
assert counts==(len(bpy.data.objects),len(bpy.data.cameras))
assert ext.rig_edit.live_root(s) is None
camera.data.type='PERSP'
print('LIVE_ENTRY_REUSES_NAMED_CAPTURE_CALIBRATION_CONTRACT_PASS')

# A removed native aim reference is a tombstone, not a name-based legacy lookup.
cube=bpy.data.objects['Cube']
s.rig_aim_mode='OBJECT';s.rig_target=cube
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
old_name=cube.name
bpy.data.objects.remove(cube,do_unlink=True)
replacement=bpy.data.objects.new(old_name,None)
bpy.context.scene.collection.objects.link(replacement)
assert 'lichtfeld_rig_target' in s.camera_collection
assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
assert s.rig_target is None
print('REMOVED_AIM_ID_NEVER_REBINDS_TO_REUSED_NAME_PASS')


# The explicit pose check is read-only and treats panorama directions as distinct.
bpy.ops.mesh.primitive_cube_add()
s.rig_reference.objects.clear();s.rig_reference.objects.add().object=bpy.context.object
s.rig_reference.mode='OBJECTS';s.rig_aim_mode='SOURCE';s.rig_target=None
s.rig_mode='PATH';s.rig_orientation='PANORAMA';s.rig_station_frame='TARGET';s.rig_path_link=True
s.rig_station_shape='GRID';s.rig_station_rows=3;s.rig_station_views=8;s.views=4
s.rig_station_sweep=2*math.pi;s.rig_phase=0;s.rig_station_min=-60;s.rig_station_max=60
assert bpy.ops.lichtfeld.create_path_guide(shape='ORBIT',radius=4)=={'FINISHED'}
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}

def checked():
    work=ext.rig_analysis.analyze_steps(bpy.context)
    while True:
        try:next(work)
        except StopIteration as done:return done.value

assert checked()['matches']==[]
original=next(o for o in s.camera_collection.objects if o.type=='CAMERA')
nested=bpy.data.collections.new('Pose check nested')
s.camera_collection.children.link(nested)
copy=original.copy();copy.data=original.data.copy();nested.objects.link(copy)
copy.data.lens=80  # Same pose does not claim identical images.
invalid=original.copy();invalid.data=original.data.copy();nested.objects.link(invalid)
invalid.data.type='ORTHO'
with tempfile.TemporaryDirectory() as directory:
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(directory)/'pose-check.blend'))
    identities=(tuple(o.as_pointer() for o in bpy.data.objects),tuple(o.as_pointer() for o in bpy.data.cameras))
    selection=(bpy.context.view_layer.objects.active,tuple(bpy.context.selected_objects))
    before=(original.matrix_world.copy(),copy.matrix_world.copy(),invalid.matrix_world.copy())
    result=checked()
    assert result['camera_count']==98 and len(result['matches'])==1
    names=[name for name,matrix in result['poses']]
    pair=result['matches'][0]
    assert {names[pair[0]],names[pair[1]]}=={original.name,copy.name}
    assert list(result['camera_issues'].values())==[invalid.name]
    assert not bpy.data.is_dirty
    assert identities==(tuple(o.as_pointer() for o in bpy.data.objects),tuple(o.as_pointer() for o in bpy.data.cameras))
    assert selection==(bpy.context.view_layer.objects.active,tuple(bpy.context.selected_objects))
    assert all(max(abs(a-b) for ra,rb in zip(matrix,obj.matrix_world) for a,b in zip(ra,rb))==0 for matrix,obj in zip(before,(original,copy,invalid)))
    assert bpy.ops.lichtfeld.check_camera_poses()=={'FINISHED'}
    assert not bpy.data.is_dirty
    snapshot = ext.rig_analysis.last_result(bpy.context)
    assert snapshot is not None and set(snapshot['repeats'][0]) == {original.name, copy.name}
    frame = bpy.context.scene.frame_current
    bpy.context.scene.frame_set(frame + 1)
    assert ext.rig_analysis.last_result(bpy.context) is None
    bpy.context.scene.frame_set(frame)
    assert ext.rig_analysis.last_result(bpy.context) is snapshot

    # A completed readiness refresh after selection must update the open report.
    from unittest.mock import Mock
    region = Mock()
    with patch.object(ext.rig_analysis, '_report_region', region):
        for camera in (invalid, copy):
            assert bpy.ops.lichtfeld.inspect_capture_camera(camera_name=camera.name) == {'FINISHED'}
            assert bpy.context.view_layer.objects.active == camera
            ext.preflight.invalidate(bpy.context.scene)
            deadline = time.monotonic() + 5
            while ext.preflight.camera_status(bpy.context) is None and time.monotonic() < deadline:
                ext.preflight.tick(bpy.context, ext.preferences())
            readiness = ext.preflight.camera_status(bpy.context)
            assert readiness is not None and invalid.name in readiness['camera_issues'].values()
        assert region.tag_refresh_ui.call_count == 2
print('POSE_CHECK_PANORAMA_NESTED_COPY_INVALID_READ_ONLY_CANCEL_PASS',flush=True)

# The distance check uses evaluated world-space geometry in the capture scope.
scope, source_collection = s.scope, s.source_collection
surfaces = bpy.data.collections.new('Clearance test surfaces')
bpy.context.scene.collection.children.link(surfaces)
mesh = bpy.data.meshes.new('Clearance test plane')
mesh.from_pydata([(-.05, -.05, 1), (.05, -.05, 1), (.05, .05, 1), (-.05, .05, 1)], [], [(0, 1, 2, 3)])
plane = bpy.data.objects.new('Clearance test plane', mesh)
surfaces.objects.link(plane)
centre = original.matrix_world.translation
plane.location = (centre.x, centre.y, centre.z - 2.25)
plane.scale = (2, 3, 2)
s.scope = 'COLLECTION'
s.source_collection = surfaces
bpy.context.view_layer.update()
with tempfile.TemporaryDirectory() as directory:
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(directory) / 'clearance.blend'))
    assert bpy.ops.lichtfeld.check_camera_poses(clearance=.5) == {'FINISHED'}
    report = ext.rig_analysis.last_result(bpy.context)
    assert report['clearance_note'] == '' and report['clearance'] == .5
    distances = dict(report['nearby'])
    assert abs(distances[original.name] - .25) < 1e-5
    assert abs(distances[copy.name] - .25) < 1e-5
    assert invalid.name not in distances
    assert not bpy.data.is_dirty
    assert bpy.ops.lichtfeld.check_camera_poses(clearance=.2) == {'FINISHED'}
    assert not ext.rig_analysis.last_result(bpy.context)['nearby']
    modifier = plane.modifiers.new('Evaluated surface copy', 'ARRAY')
    modifier.use_relative_offset = False
    modifier.use_constant_offset = True
    modifier.constant_offset_displace = (0, 0, .0625)
    bpy.context.view_layer.update()
    assert bpy.ops.lichtfeld.check_camera_poses(clearance=.2) == {'FINISHED'}
    assert abs(dict(ext.rig_analysis.last_result(bpy.context)['nearby'])[original.name] - .125) < 1e-5
    with patch.object(ext.rig_analysis, '_SURFACE_BUDGET', 3):
        assert bpy.ops.lichtfeld.check_camera_poses(clearance=.5) == {'FINISHED'}
        report = ext.rig_analysis.last_result(bpy.context)
        assert not report['nearby'] and 'budget exceeded' in report['clearance_note']
    previous_guide = s.rig_mesh
    s.rig_mesh = plane
    assert bpy.ops.lichtfeld.check_camera_poses(clearance=.5) == {'FINISHED'}
    report = ext.rig_analysis.last_result(bpy.context)
    assert not report['nearby'] and 'No visible mesh surfaces' in report['clearance_note']
    s.rig_mesh = previous_guide
    plane.hide_set(True)
    assert bpy.ops.lichtfeld.check_camera_poses(clearance=.5) == {'FINISHED'}
    assert 'No visible mesh surfaces' in ext.rig_analysis.last_result(bpy.context)['clearance_note']
    plane.hide_set(False)
s.scope, s.source_collection = scope, source_collection
bpy.data.objects.remove(plane, do_unlink=True)
bpy.data.meshes.remove(mesh)
bpy.data.collections.remove(surfaces)
print('CAMERA_CLEARANCE_WORLD_SCALE_VISIBILITY_THRESHOLD_BUDGET_PASS', flush=True)

# A partly visible reference must not be reported as entirely out of frame.
previous_rig = s.camera_collection
framing_rig = bpy.data.collections.new('Reference framing fixture')
bpy.context.scene.collection.children.link(framing_rig)
subject_mesh = bpy.data.meshes.new('Reference plane')
subject_mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
subject_object = bpy.data.objects.new('Reference plane', subject_mesh)
bpy.context.scene.collection.objects.link(subject_object)
reference = framing_rig.lichtfeld_rig_reference
reference.mode = 'OBJECTS'
reference.objects.add().object = subject_object
optics = bpy.data.cameras.new('Reference framing optics')
optics.clip_start, optics.clip_end = .1, 20
shifted_optics = optics.copy()
shifted_optics.shift_x = 1
for name, position in {
    'Framing centred': (0, 0, 5),
    'Framing partial': (2, 0, 5),
    'Framing outside': (10, 0, 5),
    'Framing before near': (0, 0, .05),
    'Framing beyond far': (0, 0, 30),
    'Framing facing away': (0, 0, 5),
    'Framing shifted': (0, 0, 5),
}.items():
    camera = bpy.data.objects.new(name, shifted_optics if name == 'Framing shifted' else optics)
    framing_rig.objects.link(camera)
    camera.location = position
    if name == 'Framing facing away':
        camera.rotation_euler.x = math.pi
s.camera_collection = framing_rig
bpy.context.view_layer.update()
with tempfile.TemporaryDirectory() as directory:
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(directory) / 'reference-framing.blend'))
    assert bpy.ops.lichtfeld.check_camera_poses(framing=True) == {'FINISHED'}
    report = ext.rig_analysis.last_result(bpy.context)
    assert not report['framing_note']
    assert dict(report['outside']) == {
        'Framing outside': 'Outside image',
        'Framing before near': 'Before near clip',
        'Framing beyond far': 'Beyond far clip',
        'Framing facing away': 'Behind camera',
        'Framing shifted': 'Outside image',
    }
    assert not bpy.data.is_dirty
    s.override_clipping = True
    s.capture_clip_start, s.capture_clip_end = .01, 40
    assert bpy.ops.lichtfeld.check_camera_poses(framing=True) == {'FINISHED'}
    assert dict(ext.rig_analysis.last_result(bpy.context)['outside']) == {
        'Framing outside': 'Outside image',
        'Framing facing away': 'Behind camera',
        'Framing shifted': 'Outside image',
    }
    s.override_clipping = False
    s.override_shift = True
    assert bpy.ops.lichtfeld.check_camera_poses(framing=True) == {'FINISHED'}
    report = ext.rig_analysis.last_result(bpy.context)
    assert not report['outside'] and 'Skipped because capture lens or shift overrides are enabled' in report['framing_note']
    s.override_shift = False
    nodes = subject_object.modifiers.new('Potential instance geometry', 'NODES')
    assert bpy.ops.lichtfeld.check_camera_poses(framing=True) == {'FINISHED'}
    report = ext.rig_analysis.last_result(bpy.context)
    assert not report['outside'] and 'reference uses Geometry Nodes' in report['framing_note']
    subject_object.modifiers.remove(nodes)
s.camera_collection = previous_rig
for camera in list(framing_rig.objects):
    bpy.data.objects.remove(camera, do_unlink=True)
bpy.data.cameras.remove(optics)
bpy.data.cameras.remove(shifted_optics)
bpy.data.collections.remove(framing_rig)
bpy.data.objects.remove(subject_object, do_unlink=True)
bpy.data.meshes.remove(subject_mesh)
print('RIG_REFERENCE_FRAMING_OUTSIDE_PARTIAL_CLIPPING_READ_ONLY_PASS', flush=True)

# Two equally sized surfaces, one fully occluded from the initial cameras.
capture_scope, capture_collection = s.scope, s.source_collection
sample_sources = bpy.data.collections.new('Coverage fixture surfaces')
sample_cameras = bpy.data.collections.new('Coverage fixture cameras')
for collection in (sample_sources, sample_cameras):
    bpy.context.scene.collection.children.link(collection)
mesh = bpy.data.meshes.new('Coverage sample plane')
mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
front = bpy.data.objects.new('Coverage front', mesh)
rear = bpy.data.objects.new('Coverage rear', mesh)
for obj in (front, rear):
    sample_sources.objects.link(obj)
rear.location.z = -2
optics = bpy.data.cameras.new('Coverage optics')
optics.lens = 18
views = [bpy.data.objects.new(name, optics) for name in ('Coverage A', 'Coverage B')]
for camera, x in zip(views, (-.1, .1)):
    sample_cameras.objects.link(camera)
    camera.location = (x, 0, 5)
s.scope, s.source_collection, s.camera_collection = 'COLLECTION', sample_sources, sample_cameras
bpy.context.view_layer.update()
with tempfile.TemporaryDirectory() as directory:
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(directory) / 'coverage.blend'))
    assert bpy.ops.lichtfeld.check_camera_poses(coverage=True, clearance=.1) == {'FINISHED'}
    report = ext.rig_analysis.last_result(bpy.context)
    assert not report['coverage_note'] and not report['nearby']
    assert report['sampled'] == ([48, 48, 0], [(rear.name, 48)])
    assert not bpy.data.is_dirty
    front.hide_set(True)
    bpy.context.view_layer.update()
    assert bpy.ops.lichtfeld.check_camera_poses(coverage=True) == {'FINISHED'}
    assert ext.rig_analysis.last_result(bpy.context)['sampled'] == ([0, 96, 0], [])
    for camera, x in zip(views, (-2, 2)):
        camera.location.x = x
        camera.rotation_euler = (rear.location - camera.location).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.view_layer.update()
    assert bpy.ops.lichtfeld.check_camera_poses(coverage=True) == {'FINISHED'}
    assert ext.rig_analysis.last_result(bpy.context)['sampled'] == ([0, 0, 96], [])
    for camera, z in zip(views, (5, -9)):
        camera.location = (0, 0, z)
        camera.rotation_euler = (rear.location - camera.location).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.view_layer.update()
    assert bpy.ops.lichtfeld.check_camera_poses(coverage=True) == {'FINISHED'}
    assert ext.rig_analysis.last_result(bpy.context)['sampled'] == ([0, 96, 0], [])
    optics.clip_end = 6
    bpy.context.view_layer.update()
    assert bpy.ops.lichtfeld.check_camera_poses(coverage=True) == {'FINISHED'}
    assert ext.rig_analysis.last_result(bpy.context)['sampled'] == ([96, 0, 0], [(rear.name, 96)])
s.scope, s.source_collection, s.camera_collection = capture_scope, capture_collection, previous_rig
for obj in (front, rear, *views):
    bpy.data.objects.remove(obj, do_unlink=True)
for collection in (sample_sources, sample_cameras):
    bpy.data.collections.remove(collection)
bpy.data.meshes.remove(mesh)
bpy.data.cameras.remove(optics)
print('RIG_SAMPLED_COVERAGE_OCCLUSION_DIVERSITY_READ_ONLY_PASS', flush=True)

# Existing Select camera must not choose an arbitrary linked namesake.
with tempfile.TemporaryDirectory() as directory:
    path=str(Path(directory)/'linked-camera.blend')
    bpy.data.libraries.write(path,{original})
    with bpy.data.libraries.load(path,link=True) as (available,loaded):loaded.objects=[original.name]
    linked=loaded.objects[0];bpy.context.scene.collection.objects.link(linked)
    bpy.context.view_layer.update()
    selection=(bpy.context.view_layer.objects.active,tuple(bpy.context.selected_objects))
    try:bpy.ops.lichtfeld.inspect_capture_camera(camera_name=original.name)
    except RuntimeError as error:assert 'Several linked cameras' in str(error)
    else:raise AssertionError('Selected an ambiguous linked camera name')
    assert selection==(bpy.context.view_layer.objects.active,tuple(bpy.context.selected_objects))
print('POSE_ISSUE_SELECTION_REJECTS_LINKED_NAME_AMBIGUITY_PASS',flush=True)

# Creating an additional rig keeps the original objects and saved recipe.
first_rig = s.camera_collection
first_recipe = first_rig['lichtfeld_rig_recipe']
first_objects = {obj.as_pointer() for obj in first_rig.objects}
first_poses = {obj.as_pointer(): obj.matrix_world.copy() for obj in first_rig.objects if obj.type == 'CAMERA'}
s.rig_mode = 'HEMISPHERE'
s.views = 8
assert bpy.ops.lichtfeld.create_rig(new_rig=True) == {'FINISHED'}
second_rig = s.camera_collection
assert second_rig != first_rig and s.rig_parameter_source == second_rig
assert sum(obj.type == 'CAMERA' for obj in second_rig.objects) == 8
assert {obj.as_pointer() for obj in first_rig.objects} == first_objects
assert {obj.as_pointer(): obj.matrix_world for obj in first_rig.objects if obj.type == 'CAMERA'} == first_poses
assert first_rig['lichtfeld_rig_recipe'] == first_recipe
s.camera_collection = first_rig
assert bpy.ops.lichtfeld.read_rig() == {'FINISHED'}
assert s.rig_parameter_source == first_rig and s.rig_mode == 'PATH'
s.camera_collection = second_rig
assert bpy.ops.lichtfeld.read_rig() == {'FINISHED'}
assert s.rig_parameter_source == second_rig and s.rig_mode == 'HEMISPHERE'
s.views = 10
assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
assert s.camera_collection == second_rig
assert sum(obj.type == 'CAMERA' for obj in second_rig.objects) == 10
assert {obj.as_pointer() for obj in first_rig.objects} == first_objects
print('ADDITIONAL_RIG_PRESERVES_ORIGINAL_AND_EDITS_INDEPENDENTLY_PASS', flush=True)
