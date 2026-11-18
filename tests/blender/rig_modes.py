# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import sys, importlib
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
module = 'bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
bpy.ops.preferences.addon_enable(module=module)
ext = importlib.import_module(module)
s=bpy.context.scene.lichtfeld_training
source=bpy.context.active_object
for mode in ('SPHERE','HEMISPHERE','RINGS'):
    s.rig_mode=mode;s.views=48;s.rig_rings=3
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    points=[tuple(round(v,5) for v in o.location) for o in s.camera_collection.objects if o.type == "CAMERA"]
    assert len(set(points))==48
for kind in ('POLY','BEZIER','NURBS'):
    curve=bpy.data.curves.new('Test path','CURVE');curve.dimensions='3D'
    spline=curve.splines.new(kind)
    coordinates=[(-4,-4,2),(0,-4,2),(4,-4,2)]
    if kind=='BEZIER':
        spline.bezier_points.add(2)
        for p,co in zip(spline.bezier_points,coordinates):
            p.co=co;p.handle_left_type='VECTOR';p.handle_right_type='VECTOR'
    else:
        spline.points.add(2)
        for p,co in zip(spline.points,coordinates):p.co=(*co,1)
        if kind=='NURBS':spline.order_u=3;spline.use_endpoint_u=True
    obj=bpy.data.objects.new('Test path',curve);bpy.context.scene.collection.objects.link(obj)
    s.rig_curve=obj;s.rig_mode='PATH';s.views=9
    before=len(bpy.data.curves)
    for orientation in ('TARGET','TANGENT'):
        s.rig_orientation=orientation
        assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
        cameras=sorted((o for o in s.camera_collection.objects if o.type == "CAMERA"),key=lambda o:o.name)
        x=[o.location.x for o in cameras]
        assert all(abs(b-a-1)<1e-4 for a,b in zip(x,x[1:])),(kind,x)
        assert len(bpy.data.curves)==before
    s.rig_path_sampling='SPACING';s.rig_path_spacing=1
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    points=sorted(o.location.x for o in s.camera_collection.objects if o.type=='CAMERA')
    assert len(points)==9 and all(abs(b-a-1)<1e-4 for a,b in zip(points,points[1:])),(kind,points)
    s.rig_path_sampling='COUNT'
    print('PATH_MODE_PASS',kind,flush=True)
# A cyclic non-collinear curve tests outgoing orientation at the seam.
spline=s.rig_curve.data.splines[0]
spline.points[0].co=(-4,-4,2,1);spline.points[1].co=(0,-6,3,1);spline.points[2].co=(4,-4,2,1)
spline.use_cyclic_u=True;s.rig_orientation='TANGENT'
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
cameras=sorted((o for o in s.camera_collection.objects if o.type == "CAMERA"),key=lambda o:o.name)
from mathutils import Vector
expected=(cameras[0].location-cameras[-1].location).normalized()
actual=cameras[-1].rotation_euler.to_matrix() @ Vector((0,0,-1))
assert expected.dot(actual)>.999
before=(len(bpy.data.curves),len(bpy.data.objects))
work=ext.rig_path.positions(bpy.context,s.rig_curve,9)
for _ in range(3):next(work)
work.close()
assert (len(bpy.data.curves),len(bpy.data.objects))==before
print('RIG_MODES_UNIQUE_ARC_LENGTH_CLEANUP_PASS',flush=True)

# Native guides feed the same rig transaction, not a second camera generator.
previous = s.rig_curve
custom = bpy.data.objects.new('Custom camera', bpy.data.cameras.new('Custom camera'))
s.camera_collection.objects.link(custom)
for shape in ('ORBIT', 'HELIX', 'SPIRAL'):
    cameras_before = {o.as_pointer() for o in s.camera_collection.objects if o.type == 'CAMERA'}
    assert bpy.ops.lichtfeld.create_path_guide(shape=shape, center=(1,2,3), radius=4) == {'FINISHED'}
    guide = s.rig_curve
    assert guide != previous and guide.type == 'CURVE' and guide.hide_render
    assert not ext.preflight.is_surface(guide)
    if previous.get('lichtfeld_path_guide'):
        assert not ext.preflight.is_surface(previous)
    assert guide in ext.preflight.capture_helpers(s)
    assert {o.as_pointer() for o in s.camera_collection.objects if o.type == 'CAMERA'} == cameras_before
    s.rig_path_sampling = 'SPACING'; s.rig_path_spacing = 1
    s.rig_path_reverse = True; s.rig_orientation = 'TANGENT'
    assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
    assert custom in s.camera_collection.objects.values()
    assert len([o for o in s.camera_collection.objects if o.get('lichtfeld_generated')]) >= 4
    s.rig_path_sampling = 'COUNT'; s.rig_path_spacing = 3; s.rig_path_reverse = False
    assert bpy.ops.lichtfeld.read_rig() == {'FINISHED'}
    assert s.rig_path_sampling == 'SPACING' and s.rig_path_spacing == 1 and s.rig_path_reverse
    previous = guide
# Spacing uses evaluated world-space distances, including nonuniform scale.
curve = bpy.data.curves.new('World spacing', 'CURVE'); curve.dimensions = '3D'
spline = curve.splines.new('POLY'); spline.points.add(1)
spline.points[0].co = (0,-4,2,1); spline.points[1].co = (4,-4,2,1)
guide = bpy.data.objects.new('World spacing', curve); bpy.context.scene.collection.objects.link(guide)
guide.scale.x = 2; s.rig_curve = guide; s.rig_path_spacing = 2
s.rig_path_reverse = True; s.rig_orientation = 'TANGENT'
bpy.context.view_layer.update()
assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
cameras = sorted((o for o in s.camera_collection.objects if o.get('lichtfeld_generated')), key=lambda o:o.name)
assert len(cameras) == 5 and all(abs(b.location.x-a.location.x-2)<1e-5 for a,b in zip(cameras,cameras[1:]))
for camera in (cameras[0],cameras[-1]):
    assert (camera.rotation_euler.to_matrix() @ Vector((0,0,-1))).dot(Vector((-1,0,0)))>.999
collection = s.camera_collection; identities = {o.as_pointer() for o in collection.objects}
s.rig_path_spacing = .0001
try:
    result = bpy.ops.lichtfeld.create_rig()
except RuntimeError:
    result = {'CANCELLED'}
assert result == {'CANCELLED'} and s.camera_collection == collection
assert {o.as_pointer() for o in collection.objects} == identities
assert not s.operation_busy
print('PATH_GUIDES_SPACING_WORLD_UNITS_RECIPE_ROLLBACK_PASS', flush=True)

# Reverse is a viewing direction, never a hidden reorder of camera identities.
s.rig_path_sampling='COUNT';s.views=8;s.rig_path_reverse=False
assert bpy.ops.lichtfeld.create_path_guide(shape='ORBIT',radius=4)=={'FINISHED'}
guide=s.rig_curve
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
def generated():
    return sorted((o for o in s.camera_collection.objects if o.get('lichtfeld_generated')),key=lambda o:o['lichtfeld_camera_index'])
forward=[(o.as_pointer(),o.location.copy(),o.rotation_euler.to_matrix() @ Vector((0,0,-1))) for o in generated()]
s.rig_path_reverse=True
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
for index,(camera,(identity,position,direction)) in enumerate(zip(generated(),forward)):
    assert camera.as_pointer()==identity and (camera.location-position).length<1e-5
    backward=(forward[index-1][1]-position).normalized()
    assert backward.dot(camera.rotation_euler.to_matrix() @ Vector((0,0,-1)))>.999
s.rig_orientation='TARGET'
assert 'rig_path_reverse' not in ext.rig_edit.decode_recipe(ext.rig_recipe(s))
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
before=[o.matrix_world.copy() for o in generated()]
s.rig_path_reverse=False
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
bpy.context.view_layer.update()
assert all(max(abs(a-b) for row_a,row_b in zip(o.matrix_world,m) for a,b in zip(row_a,row_b))<1e-5 for o,m in zip(generated(),before))

# Native guide links move positions immediately, while target aiming stays independent.
s.rig_path_link=True
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
bpy.context.view_layer.update()
original=[o.matrix_world.translation.copy() for o in generated()]
apply_inverse=guide.matrix_world.inverted()
guide.location.x+=2;guide.rotation_euler.z=.4;guide.scale=(1.5,2,1)
bpy.context.view_layer.update()
for camera,position in zip(generated(),original):
    expected=guide.matrix_world @ apply_inverse @ position
    assert (camera.matrix_world.translation-expected).length<1e-4
    pose=ext.capture_settings.camera_pose(camera,bpy.context.evaluated_depsgraph_get())
    direction=(s.rig_aim.matrix_world.translation-pose.translation).normalized()
    assert direction.dot(pose.to_3x3() @ Vector((0,0,-1)))>.999
assert custom.constraints.get(ext.rig_edit.PATH_LINK) is None
s.rig_path_link=False
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert all(o.constraints.get(ext.rig_edit.PATH_LINK) is None for o in generated())
s.rig_orientation='TANGENT';s.rig_path_link=True
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
guide.scale=tuple(value*2 for value in guide.scale);guide.rotation_euler.z=.8
bpy.context.view_layer.update()
for camera in generated():
    ext.capture_settings.camera_pose(camera,bpy.context.evaluated_depsgraph_get())
guide.scale.x*=2
bpy.context.view_layer.update()
try:
    ext.capture_settings.camera_pose(generated()[0],bpy.context.evaluated_depsgraph_get())
    raise AssertionError('Nonuniform guide scaling must not produce invalid capture poses')
except ValueError as error:
    assert 'apply rig changes' in str(error),str(error)
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
bpy.context.view_layer.update()
for camera in generated():
    ext.capture_settings.camera_pose(camera,bpy.context.evaluated_depsgraph_get())
print('PATH_REVERSE_VIEW_ONLY_LINK_TRANSFORMS_TARGET_SCALE_CAPTURE_PASS',flush=True)

# The existing transaction restores link existence/target/inverse at every cut.
from types import SimpleNamespace
for applied,wanted in ((True,False),(False,True),(True,True)):
    s.rig_path_link=applied
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    guide.location.y+=.2
    bpy.context.view_layer.update()
    s.rig_path_link=wanted
    baseline=[(o,o.data,o.matrix_world.copy(),ext.rig_edit.path_link_state(o)) for o in generated()]
    counts=(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections),len(bpy.data.curves))
    for cut in range(400):
        owner=SimpleNamespace(collection=None,rig_backup=[],committed=False,report=lambda *args:None)
        work=ext.LF_OT_rig.generate(owner,bpy.context)
        try:
            for _ in range(cut):next(work)
        except StopIteration:
            assert owner.committed
            break
        if owner.committed:
            for _ in work:pass
            break
        work.close()
        for _ in ext.LF_OT_rig.cleanup(owner):pass
        bpy.context.view_layer.update()
        assert counts==(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections),len(bpy.data.curves)),(applied,wanted,cut)
        for camera,data,matrix,link in baseline:
            assert camera.data==data
            assert max(abs(a-b) for x,y in zip(camera.matrix_world,matrix) for a,b in zip(x,y))<1e-5,(applied,wanted,cut)
            restored=ext.rig_edit.path_link_state(camera)
            assert bool(restored)==bool(link)
            if link:
                assert restored[0]==link[0] and restored[2]==link[2]
                assert max(abs(a-b) for x,y in zip(restored[1],link[1]) for a,b in zip(x,y))<1e-6
    else:raise AssertionError('Path rig never reached commit')
print('PATH_LINK_UNLINK_RELINK_CANCEL_EVERY_PRECOMMIT_YIELD_PASS',flush=True)
s.rig_mode='SPHERE'
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert all(o.constraints.get(ext.rig_edit.PATH_LINK) is None for o in generated())
assert 'rig_path_link' not in ext.rig_edit.decode_recipe(ext.rig_recipe(s))
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
print('PATH_LINK_TO_RADIAL_LIVE_REUSES_NATIVE_OWNER_PASS',flush=True)
s.rig_mode='PATH';s.rig_orientation='TARGET';s.rig_path_link=True
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
camera=generated()[0]
camera.constraints.move(list(camera.constraints).index(camera.constraints.get(ext.rig_edit.AIM)),0)
order=[c.name for c in camera.constraints]
s.rig_orientation='TANGENT'
owner=SimpleNamespace(collection=None,rig_backup=[],committed=False,report=lambda *args:None)
work=ext.LF_OT_rig.generate(owner,bpy.context)
while not owner.rig_backup:next(work)
assert camera.constraints.get(ext.rig_edit.AIM) is None
work.close()
for _ in ext.LF_OT_rig.cleanup(owner):pass
assert [c.name for c in camera.constraints]==order
print('PATH_LINK_USER_CONSTRAINT_ORDER_CANCEL_PASS',flush=True)

# Small seeded displacements retain native aiming and the existing live owner.
for mode in ('SPHERE','HEMISPHERE','RINGS'):
    s.rig_mode=mode;s.views=12;s.rig_rings=2;s.rig_auto_fit=False;s.rig_radius=4
    s.rig_aim_mode='SOURCE';s.rig_position_variation=0;s.rig_phase=.3
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    baseline=[tuple(o.location) for o in generated()]
    identities=[o.as_pointer() for o in generated()]
    assert 'rig_position_variation' not in ext.rig_edit.decode_recipe(ext.rig_recipe(s))
    s.rig_position_variation=5;s.rig_variation_seed=73
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    varied=[tuple(o.location) for o in generated()]
    assert varied!=baseline and [o.as_pointer() for o in generated()]==identities
    assert all((Vector(a)-Vector(b)).length<=.2+1e-6 for a,b in zip(varied,baseline))
    generated()[0].name='Renamed variation camera'
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    assert [tuple(o.location) for o in generated()]==varied
    s.rig_variation_seed=74
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    assert [tuple(o.location) for o in generated()]!=varied
    before=[tuple(o.location) for o in generated()]
    assert bpy.ops.wm.context_set_float(data_path='scene.lichtfeld_training.rig_position_variation',value=0)=={'FINISHED'}
    assert s.rig_variation_seed==74 and [tuple(o.location) for o in generated()]==before
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    assert [tuple(o.location) for o in generated()]==baseline
    s.rig_position_variation=5
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
    s.live_radius=6;s.live_phase=.8;s.live_aim=(.1,.2,.3)
    bpy.context.view_layer.update()
    live=[o.matrix_world.copy() for o in generated()]
    assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
    assert s.rig_position_variation==5
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    bpy.context.view_layer.update()
    for camera,matrix in zip(generated(),live):
        assert max(abs(a-b) for x,y in zip(camera.matrix_world,matrix) for a,b in zip(x,y))<1e-4,mode
        pose=ext.capture_settings.camera_pose(camera,bpy.context.evaluated_depsgraph_get())
        desired=(s.rig_aim.matrix_world.translation-pose.translation).normalized()
        assert desired.dot(pose.to_3x3() @ Vector((0,0,-1)))>.999
    s.rig_position_variation=0;s.rig_variation_seed=0
    assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
    assert s.rig_position_variation==5 and s.rig_variation_seed==74
print('SEEDED_RADIAL_VARIATION_RESET_IDENTITIES_LIVE_REAPPLY_AIM_PASS',flush=True)
before=[o.matrix_world.copy() for o in generated()]
owner=SimpleNamespace(live_controller=None,live_backup=[],live_finishing=False,committed=False)
work=ext.rig_edit.edit_live(owner,bpy.context)
while len(owner.live_backup)<2:next(work)
work.close()
for _ in ext.rig_edit.rollback_live(owner):pass
bpy.context.view_layer.update()
assert ext.rig_edit.live_root(s) is None and s.rig_position_variation==5 and s.rig_variation_seed==74
assert all(max(abs(a-b) for x,y in zip(o.matrix_world,m) for a,b in zip(x,y))<1e-5 for o,m in zip(generated(),before))
print('SEEDED_VARIATION_LIVE_ENTRY_CANCEL_PASS',flush=True)

# Native rotation is bounded, reproducible and independent of position sampling.
import math
for mode in ('SPHERE','HEMISPHERE','RINGS'):
    s.rig_mode=mode;s.rig_auto_fit=False;s.rig_radius=4;s.rig_rotation_variation=0
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    bpy.context.view_layer.update()
    positions=[tuple(o.location) for o in generated()]
    identities=[o.as_pointer() for o in generated()]
    s.rig_rotation_variation=math.radians(3)
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    bpy.context.view_layer.update()
    assert [tuple(o.location) for o in generated()]==positions
    assert [o.as_pointer() for o in generated()]==identities
    assert len([o for o in s.camera_collection.objects if o.type=='EMPTY'])==1
    for camera in generated():
        rotation=camera.constraints.get(ext.rig_edit.ROTATION)
        assert rotation.target==s.rig_aim
        actual=camera.matrix_world.to_quaternion()
        rotation.mute=True;bpy.context.view_layer.update()
        base=camera.matrix_world.to_quaternion()
        rotation.mute=False;bpy.context.view_layer.update()
        angle=base.rotation_difference(actual).angle
        assert 0 < min(angle,2*math.pi-angle) <= s.rig_rotation_variation+1e-5
        ext.capture_settings.camera_pose(camera,bpy.context.evaluated_depsgraph_get())
    assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
    s.live_radius=6;s.live_phase=1.1;s.live_aim=(.4,-.3,.2)
    bpy.context.view_layer.update()
    live=[o.matrix_world.copy() for o in generated()]
    assert bpy.ops.lichtfeld.live_rig()=={'FINISHED'}
    bpy.context.view_layer.update()
    assert all(max(abs(a-b) for x,y in zip(o.matrix_world,m) for a,b in zip(x,y))<1e-4 for o,m in zip(generated(),live))
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    bpy.context.view_layer.update()
    assert all(max(abs(a-b) for x,y in zip(o.matrix_world,m) for a,b in zip(x,y))<1e-4 for o,m in zip(generated(),live)),mode
    s.rig_rotation_variation=0
    assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
    assert abs(s.rig_rotation_variation-math.radians(3))<1e-6
print('ROTATION_BOUND_NATIVE_TRACK_LIVE_FINISH_REAPPLY_PASS',flush=True)

# Updating the existing owner is still cancellable before its commit boundary.
s.views=4;s.rig_rings=1
for applied,wanted in ((0,.04),(.04,0),(.04,.07)):
    s.rig_rotation_variation=applied
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    bpy.context.view_layer.update()
    baseline=[(o,o.data,o.matrix_world.copy(),[c.name for c in o.constraints]) for o in generated()]
    s.rig_rotation_variation=wanted
    for cut in range(300):
        owner=SimpleNamespace(collection=None,rig_backup=[],committed=False,report=lambda *args:None)
        work=ext.LF_OT_rig.generate(owner,bpy.context)
        try:
            for _ in range(cut):next(work)
        except StopIteration:break
        if owner.committed:
            for _ in work:pass
            break
        work.close()
        for _ in ext.LF_OT_rig.cleanup(owner):pass
        bpy.context.view_layer.update()
        for camera,data,matrix,order in baseline:
            assert camera.data==data and [c.name for c in camera.constraints]==order
            assert max(abs(a-b) for x,y in zip(camera.matrix_world,matrix) for a,b in zip(x,y))<1e-5,(applied,wanted,cut)
    else:raise AssertionError('Rotation rig never committed')
s.rig_rotation_variation=0
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert all(o.constraints.get(ext.rig_edit.ROTATION) is None for o in generated())
assert 'rig_rotation_variation' not in ext.rig_edit.decode_recipe(ext.rig_recipe(s))
print('ROTATION_CANCEL_ALL_PRECOMMIT_CUTS_ZERO_REMOVAL_PASS',flush=True)
# The canonical auto-fit reserves angular margin, including portrait and long lenses.
s.rig_auto_fit=True;s.rig_position_variation=0;s.rig_lens=50;s.rig_rotation_variation=.04
scene=bpy.context.scene
scene.render.resolution_x=600;scene.render.resolution_y=1200
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
bpy.context.view_layer.update()
from bpy_extras.object_utils import world_to_camera_view
for camera in generated():
    for corner in source.bound_box:
        p=world_to_camera_view(scene,camera,source.matrix_world @ Vector(corner))
        assert 0 <= p.x <= 1 and 0 <= p.y <= 1 and p.z>0,(p,camera.name)
s.rig_lens=500;s.rig_rotation_variation=.2
try:
    bpy.ops.lichtfeld.create_rig()
except RuntimeError as error:
    assert 'half field of view' in str(error)
else:raise AssertionError('Accepted rotation outside the optical field of view')
print('ROTATION_AUTOFIT_PORTRAIT_MARGIN_LONG_LENS_FAILURE_PASS',flush=True)
