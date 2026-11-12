# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender: managed identity, target tracking and transaction cancellation."""
import bpy, sys, math, importlib, tempfile, time
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
assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
rig = s.camera_collection
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
