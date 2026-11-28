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

# Multi-direction stations reuse curve sampling and perspective camera poses.
s.rig_lens=35;s.rig_auto_fit=False;s.rig_position_variation=0;s.rig_rotation_variation=0
s.rig_mode='PATH';s.rig_orientation='PANORAMA';s.rig_aim_mode='OBJECT';s.rig_target=None
s.views=4;s.rig_station_views=8;s.rig_path_link=False;s.rig_path_sampling='COUNT';s.rig_phase=.2
for kind in ('POLY','BEZIER','NURBS'):
    curve=bpy.data.curves.new('Panorama '+kind,'CURVE');curve.dimensions='3D'
    spline=curve.splines.new(kind)
    coords=[(-3,0,1),(0,0,1),(3,0,1)]
    if kind=='BEZIER':
        spline.bezier_points.add(2)
        for point,co in zip(spline.bezier_points,coords):
            point.co=co;point.handle_left_type='VECTOR';point.handle_right_type='VECTOR'
    else:
        spline.points.add(2)
        for point,co in zip(spline.points,coords):point.co=(*co,1)
        if kind=='NURBS':spline.order_u=3;spline.use_endpoint_u=True
    guide=bpy.data.objects.new('Panorama '+kind,curve);bpy.context.scene.collection.objects.link(guide)
    guide.location=(1,2,.5);guide.rotation_euler=(.3,-.2,.4);guide.scale=(1.3,)*3
    s.rig_curve=guide
    for degrees in (120,180,270,360):
        s.rig_station_sweep=math.radians(degrees)
        assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
        bpy.context.view_layer.update()
        cameras=generated()
        stations=[Vector(p) for p in ext.rig_path.positions(bpy.context,guide,s.views) if p is not None]
        assert len(cameras)==len(stations)*s.rig_station_views==32
        axes=list(ext.rig_geometry.directions(8,'RINGS',0,0,1,s.rig_phase,sweep=s.rig_station_sweep))
        rotation=guide.matrix_world.to_quaternion().normalized()
        poses=[]
        for index,camera in enumerate(cameras):
            pose=ext.capture_settings.camera_pose(camera,bpy.context.evaluated_depsgraph_get())
            assert (pose.translation-stations[index//8]).length<1e-5
            assert (pose.to_3x3() @ Vector((0,0,-1))).dot(rotation @ Vector(axes[index%8]))>.99999
            assert (pose.to_3x3() @ Vector((0,1,0))).dot(rotation @ Vector((0,0,1)))>.99999
            assert camera.constraints.get(ext.rig_edit.AIM) is None
            assert camera.constraints.get(ext.rig_edit.ROTATION) is None
            poses.append(tuple(round(v,5) for row in pose for v in row))
        assert len(set(poses))==32
    s.rig_path_sampling='SPACING';s.rig_path_spacing=2
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    stations=[p for p in ext.rig_path.positions(bpy.context,guide,s.views,spacing=2) if p is not None]
    assert len(generated())==len(stations)*8
    s.rig_path_sampling='COUNT'
    if kind=='POLY':
        last=spline.points[-1].co.copy();spline.points[-1].co=spline.points[0].co
        bpy.context.view_layer.update()
        try:
            bpy.ops.lichtfeld.create_rig()
        except RuntimeError as error:
            assert 'repeated camera positions' in str(error)
        else:raise AssertionError('Accepted duplicate panorama stations')
        spline.points[-1].co=last
print('PANORAMA_POLY_BEZIER_NURBS_SECTORS_POSES_SPACING_PASS',flush=True)

s.rig_path_link=True
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
bpy.context.view_layer.update()
identities=[o.as_pointer() for o in generated()]
guide.location.x+=1;guide.rotation_euler=(.6,.2,1);guide.scale=(2,)*3
bpy.context.view_layer.update()
linked=[ext.capture_settings.camera_pose(o,bpy.context.evaluated_depsgraph_get()) for o in generated()]
frozen=ext.capture_settings.copy_camera(generated()[0],bpy.context.evaluated_depsgraph_get(),{})
assert not frozen.constraints
assert max(abs(a-b) for x,y in zip(frozen.matrix_world,linked[0]) for a,b in zip(x,y))<1e-5
data=frozen.data;bpy.data.objects.remove(frozen);bpy.data.cameras.remove(data)
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
bpy.context.view_layer.update()
assert [o.as_pointer() for o in generated()]==identities
assert all(max(abs(a-b) for x,y in zip(ext.capture_settings.camera_pose(o,bpy.context.evaluated_depsgraph_get()),m) for a,b in zip(x,y))<1e-4 for o,m in zip(generated(),linked))
print('PANORAMA_GUIDE_FRAME_LINK_MOVE_ROTATE_SCALE_REAPPLY_PASS',flush=True)
# Frame extraction uses the same rigid-transform contract as capture calibration.
saved_matrix=guide.matrix_world.copy()
counts=(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections))
for link in (False,True):
    s.rig_path_link=link
    for scale in ((-1,1,1),(1,1,0)):
        guide.scale=scale;bpy.context.view_layer.update()
        try:
            bpy.ops.lichtfeld.create_rig()
        except RuntimeError as error:
            assert 'Panorama guide' in str(error)
            assert ('mirrored transform' if scale[0]<0 else 'zero or near-zero scale') in str(error)
        else:raise AssertionError('Accepted a mirrored or singular panorama frame')
        assert counts==(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections))
    guide.matrix_world=saved_matrix
s.rig_path_link=True
parent=bpy.data.objects.new('Sheared panorama parent',None);bpy.context.scene.collection.objects.link(parent)
parent.scale=(2,1,1);guide.parent=parent;guide.rotation_euler=(0,0,.4)
bpy.context.view_layer.update()
try:
    bpy.ops.lichtfeld.create_rig()
except RuntimeError as error:
    assert 'sheared transform' in str(error) and 'guide' in str(error)
else:raise AssertionError('Accepted a sheared panorama frame')
guide.parent=None;guide.matrix_world=saved_matrix;bpy.data.objects.remove(parent)
bpy.context.view_layer.update()
print('PANORAMA_SHARED_RIGID_FRAME_MIRROR_ZERO_SHEAR_REJECTED_PASS',flush=True)


counts=(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections))
recipe=s.camera_collection['lichtfeld_rig_recipe']
s.views=1000;s.rig_station_views=11
try:
    bpy.ops.lichtfeld.create_rig()
except RuntimeError as error:
    assert '10,000' in str(error)
else:raise AssertionError('Expanded station count exceeded capture contract')
assert counts==(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections))
assert s.camera_collection['lichtfeld_rig_recipe']==recipe
s.views=4;s.rig_station_views=4
for orientation in ('TARGET','PANORAMA','TANGENT'):
    s.rig_orientation=orientation;s.rig_aim_mode='SOURCE'
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    assert generated()[0].as_pointer()==identities[0]
    assert custom.name in s.camera_collection.objects
    assert bool(generated()[0].constraints.get(ext.rig_edit.AIM))==(orientation=='TARGET')
print('PANORAMA_COUNT_GUARD_TARGET_TANGENT_CUSTOM_IDENTITIES_PASS',flush=True)

# A panorama update has the same cancellable transaction as all other rigs.
s.rig_orientation='PANORAMA'
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
bpy.context.view_layer.update()
baseline=[(o,o.data,o.matrix_world.copy()) for o in generated()]
counts=(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections))
s.rig_station_sweep=math.pi
for cut in range(500):
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
    assert counts==(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections)),cut
    for camera,data,matrix in baseline:
        assert camera.data==data
        assert max(abs(a-b) for x,y in zip(camera.matrix_world,matrix) for a,b in zip(x,y))<1e-5,cut
else:raise AssertionError('Station update never committed')
print('PANORAMA_CANCEL_EVERY_PRECOMMIT_YIELD_PASS',flush=True)

# Sectors can turn with the route or center on a shared native target.
from mathutils import Quaternion
s.rig_mode='PATH';s.rig_orientation='PANORAMA';s.rig_station_frame='PATH'
s.views=8;s.rig_station_views=5;s.rig_station_sweep=math.pi;s.rig_phase=0;s.rig_path_reverse=False
assert bpy.ops.lichtfeld.create_path_guide(shape='ORBIT',radius=4)=={'FINISHED'}
guide=s.rig_curve;s.rig_path_link=True
for reverse in (False,True):
    s.rig_path_reverse=reverse
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    bpy.context.view_layer.update()
    points=[Vector(p) for p in ext.rig_path.positions(bpy.context,guide,s.views) if p is not None]
    for station in range(8):
        camera=generated()[station*5+2]
        forward=camera.matrix_world.to_quaternion() @ Vector((0,0,-1))
        assert forward.dot(ext.rig_path.tangent(points,station,True,reverse).normalized())>.99999
        assert camera.constraints.get(ext.rig_edit.AIM) is None
s.rig_path_reverse=False
for facing in ('PATH','TARGET'):
    s.rig_station_frame=facing;s.rig_aim_mode='SOURCE'
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    bpy.context.view_layer.update()
    if facing=='TARGET':
        for station in range(8):
            camera=generated()[station*5+2]
            forward=camera.matrix_world.to_quaternion() @ Vector((0,0,-1))
            assert forward.dot((s.rig_aim.matrix_world.translation-camera.matrix_world.translation).normalized())>.99999
            assert camera.constraints.get(ext.rig_edit.AIM).target==s.rig_aim
            assert camera.constraints.get(ext.rig_edit.ROTATION).target==s.rig_aim
            for view in generated()[station*5:(station+1)*5]:
                q=view.matrix_world.to_quaternion()
                direction=q @ Vector((0,0,-1))
                up=Vector((0,0,1))-direction*direction.z
                assert (q @ Vector((0,1,0))).dot(up.normalized())>.99999
        s.rig_aim_mode='VIEWPORT';s.rig_aim.location+=(Vector((.3,-.2,.4)))
        bpy.context.view_layer.update()
        camera=generated()[2]
        assert (camera.matrix_world.to_quaternion() @ Vector((0,0,-1))).dot((s.rig_aim.location-camera.matrix_world.translation).normalized())>.99999
    guide.location.x+=.5;guide.rotation_euler=(.2,.3,.6);guide.scale=(1.5,)*3
    bpy.context.view_layer.update()
    before=[ext.capture_settings.camera_pose(o,bpy.context.evaluated_depsgraph_get()) for o in generated()]
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    bpy.context.view_layer.update()
    assert all(max(abs(a-b) for x,y in zip(ext.capture_settings.camera_pose(o,bpy.context.evaluated_depsgraph_get()),m) for a,b in zip(x,y))<1e-4 for o,m in zip(generated(),before)),facing
    assert sum(o.type=='EMPTY' for o in s.camera_collection.objects)==1
    s.rig_station_frame='GUIDE'
    assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
    assert s.rig_station_frame==facing
print('STATION_PATH_TARGET_CENTER_REVERSE_LIVE_AIM_GUIDE_REAPPLY_PASS',flush=True)

# Upright frames retain the guide horizon on default helices and spirals.
for shape in ('HELIX','SPIRAL'):
    assert bpy.ops.lichtfeld.create_path_guide(shape=shape,radius=3)=={'FINISHED'}
    test_guide=s.rig_curve
    points=[Vector(p) for p in ext.rig_path.positions(bpy.context,test_guide,48) if p is not None]
    guide_rotation=Quaternion((1,0,0,0))
    frames=list(ext.rig_path.station_frames(points,guide_rotation))
    turn=Quaternion(Vector((1,2,3)).normalized(),.8)
    rotated=list(ext.rig_path.station_frames([turn@p+Vector((1,2,3)) for p in points],turn))
    for index,(frame,transformed) in enumerate(zip(frames,rotated)):
        forward=ext.rig_path.tangent(points,index,False).normalized()
        up=Vector((0,0,1))-forward*forward.z
        assert (frame @ Vector((0,0,-1))).dot(forward)>.99999
        assert (frame @ Vector((0,1,0))).dot(up.normalized())>.99999
        angle=(turn@frame).rotation_difference(transformed).angle
        assert min(angle,2*math.pi-angle)<1e-3
    # Inspect real generated views; measure bearing/elevation independently of
    # the quaternion composition used by the generator.
    test_guide.rotation_euler=(.2,.3,.6);test_guide.scale=(1.2,)*3
    s.rig_aim_mode='SOURCE';s.rig_aim_offset=(0,0,0)
    for facing in ('PATH','TARGET'):
        s.rig_station_frame=facing
        assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
        bpy.context.view_layer.update()
        stations=[Vector(p) for p in ext.rig_path.positions(bpy.context,test_guide,s.views) if p is not None]
        up=(test_guide.matrix_world.to_quaternion() @ Vector((0,0,1))) if facing=='PATH' else Vector((0,0,1))
        for station,position in enumerate(stations):
            reference=(ext.rig_path.tangent(stations,station,False) if facing=='PATH' else s.rig_aim.matrix_world.translation-position).normalized()
            flat_reference=reference-up*reference.dot(up)
            for view,camera in enumerate(generated()[station*5:(station+1)*5]):
                pose=ext.capture_settings.camera_pose(camera,bpy.context.evaluated_depsgraph_get())
                forward=pose.to_3x3() @ Vector((0,0,-1))
                flat_forward=forward-up*forward.dot(up)
                bearing=math.atan2(up.dot(flat_reference.cross(flat_forward)),flat_reference.dot(flat_forward))
                difference=bearing-(-math.pi/2+view*math.pi/4)
                assert abs(math.atan2(math.sin(difference),math.cos(difference)))<1e-4,(shape,facing,station,view)
                assert abs(forward.dot(up)-reference.dot(up))<1e-5
                expected_up=(up-forward*forward.dot(up)).normalized()
                assert (pose.to_3x3() @ Vector((0,1,0))).dot(expected_up)>.99999
try:
    list(ext.rig_path.station_frames([Vector(p) for p in ((0,0,0),(0,0,1),(0,.5,2),(1,1,3))],Quaternion((1,0,0,0))))
except ValueError as error:
    assert 'vertical' in str(error) and 'Guide axes' in str(error)
else:raise AssertionError('Accepted an undefined upright frame at a vertical tangent')
s.rig_curve=guide
print('STATION_UPRIGHT_HELIX_SPIRAL_COVARIANCE_VERTICAL_FAILURE_PASS',flush=True)

# Target coincidence cleans staging and leaves the applied rig untouched.
s.rig_station_frame='TARGET';s.rig_aim_mode='POSITION'
s.rig_aim_offset=(0,0,0)
s.rig_aim_position=next(Vector(p) for p in ext.rig_path.positions(bpy.context,guide,s.views) if p is not None)
counts=(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections))
recipe=s.camera_collection['lichtfeld_rig_recipe']
try:
    bpy.ops.lichtfeld.create_rig()
except RuntimeError as error:
    assert 'coincides with its target' in str(error)
else:raise AssertionError('Accepted a station at its own target')
assert counts==(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections))
assert recipe==s.camera_collection['lichtfeld_rig_recipe']
s.rig_aim_mode='SOURCE';s.views=4;s.rig_station_views=4
for applied,wanted in (('GUIDE','TARGET'),('TARGET','PATH'),('PATH','GUIDE')):
    s.rig_station_frame=applied
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    bpy.context.view_layer.update()
    baseline=[(o,o.data,o.matrix_world.copy(),[c.name for c in o.constraints]) for o in generated()]
    s.rig_station_frame=wanted
    for cut in range(500):
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
    else:raise AssertionError('Facing transaction never committed')
print('STATION_FACING_TRANSITIONS_CANCEL_EVERY_YIELD_PASS',flush=True)

# Inspect evaluated perspective cameras, including native target tracking and
# guide links, rather than checking only the sampler's output.
s.rig_aim_mode='SOURCE';s.rig_aim_offset=(0,0,0);s.views=4
s.rig_station_views=17;s.rig_phase=.25;s.rig_station_sweep=math.pi
s.rig_station_min=-30;s.rig_station_max=60
for linked in (False,True):
    s.rig_path_link=linked
    for facing in ('GUIDE','PATH','TARGET'):
        s.rig_station_frame=facing
        for coverage in ('GRID','ELEVATION','HEMISPHERE','SPHERE','BAND'):
            s.rig_station_shape=coverage
            s.rig_station_min=-30;s.rig_station_max=60
            assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
            bpy.context.view_layer.update()
            positions=[Vector(p) for p in ext.rig_path.positions(bpy.context,s.rig_curve,s.views) if p is not None]
            samples=list(ext.rig_geometry.station_directions(17,coverage,.25,math.pi,-30,60,centered=facing!='GUIDE'))
            per_station=len(samples)
            def inspect_station_poses():
                poses=[]
                for i,camera in enumerate(generated()):
                    pose=ext.capture_settings.camera_pose(camera,bpy.context.evaluated_depsgraph_get())
                    station=i//per_station
                    assert (pose.translation-positions[station]).length<1e-4
                    forward=pose.to_3x3()@Vector((0,0,-1));poses.append(tuple(round(v,5) for v in forward))
                    if facing=='GUIDE':
                        local=s.rig_curve.matrix_world.to_quaternion().inverted()@forward
                    elif coverage!='BAND':
                        front=(ext.rig_path.tangent(positions,station,s.rig_curve.data.splines[0].use_cyclic_u,s.rig_path_reverse) if facing=='PATH' else s.rig_aim.matrix_world.translation-positions[station]).normalized()
                        up=s.rig_curve.matrix_world.to_quaternion()@Vector((0,0,1)) if facing=='PATH' else Vector((0,0,1))
                        left=up.cross(front).normalized();up=front.cross(left).normalized()
                        local=Vector((forward.dot(front),forward.dot(left),forward.dot(up)))
                    else:continue  # Legacy band is covered above, with guide/world yaw.
                    assert (local-Vector(samples[i%per_station])).length<1e-4,(linked,facing,coverage,i,local,samples[i%per_station])
                for station in range(4):
                    assert len(set(poses[station*per_station:(station+1)*per_station]))==per_station
            inspect_station_poses()
            if facing=='TARGET':
                s.rig_aim.location.z+=1.7;bpy.context.view_layer.update()
                inspect_station_poses()
            if linked and facing=='TARGET' and coverage!='BAND':
                s.rig_curve.location.x+=.3;s.rig_curve.rotation_euler.z+=.2
                bpy.context.view_layer.update()
                positions=[Vector(p) for p in ext.rig_path.positions(bpy.context,s.rig_curve,s.views) if p is not None]
                inspect_station_poses()
            recipe=ext.rig_edit.decode_recipe(s.camera_collection['lichtfeld_rig_recipe'])
            assert recipe.get('rig_station_shape','BAND')==coverage
            assert ('rig_station_min' in recipe)==(coverage in {'GRID','ELEVATION'})
            s.rig_station_shape='BAND' if coverage!='BAND' else 'SPHERE'
            s.rig_station_min=-10;s.rig_station_max=10;s.rig_station_rows=3
            assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
            assert s.rig_station_shape==coverage
            assert s.rig_station_rows==2
            assert (s.rig_station_min,s.rig_station_max)==((-30,60) if coverage in {'GRID','ELEVATION'} else (-45,45))
            if coverage in {'SPHERE','HEMISPHERE'}:
                before_recipe=ext.rig_recipe(s)
                s.rig_station_sweep=1.2;s.rig_station_min=-20
                assert ext.rig_recipe(s)==before_recipe
                s.rig_station_sweep=math.pi
print('STATION_SPHERICAL_NATIVE_FRAMES_LINK_TARGET_METADATA_PASS',flush=True)

# Rejected elevation ranges leave applied objects and recipe untouched.
s.rig_station_shape='ELEVATION';s.rig_station_min=40;s.rig_station_max=20
before=(len(bpy.data.objects),len(bpy.data.cameras),s.camera_collection['lichtfeld_rig_recipe'])
try:bpy.ops.lichtfeld.create_rig()
except RuntimeError as error:assert 'Minimum elevation' in str(error)
else:raise AssertionError('Accepted reversed station elevations')
assert before==(len(bpy.data.objects),len(bpy.data.cameras),s.camera_collection['lichtfeld_rig_recipe'])
s.views=4;s.rig_station_views=4;s.rig_station_min=-45;s.rig_station_max=45
s.rig_station_frame='TARGET'
for applied,wanted in (('BAND','SPHERE'),('SPHERE','ELEVATION'),('ELEVATION','GRID'),('GRID','HEMISPHERE'),('HEMISPHERE','BAND')):
    s.rig_station_shape=applied
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    bpy.context.view_layer.update()
    baseline=[(o,o.data,o.matrix_world.copy(),[c.name for c in o.constraints]) for o in generated()]
    s.rig_station_shape=wanted
    for cut in range(500):
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
    else:raise AssertionError('Coverage transaction never committed')
print('STATION_COVERAGE_INVALID_RANGE_CANCEL_EVERY_YIELD_PASS',flush=True)

# Canonical panorama split: 8 azimuths at each of three elevations, full turn.
# Measure the evaluated poses independently of the station sampler, including
# exact quarter-turn Euler offsets under native tracking and a sloping route.
s.rig_station_shape='GRID';s.rig_station_rows=3;s.rig_station_views=8
s.rig_station_sweep=2*math.pi;s.rig_phase=0
s.rig_station_min=-60;s.rig_station_max=60;s.rig_aim_mode='VIEWPORT'
assert bpy.ops.lichtfeld.create_path_guide(shape='HELIX',radius=4,height=3,turns=.75)=={'FINISHED'}
for facing in ('GUIDE','PATH','TARGET'):
    s.rig_station_frame=facing
    assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
    if facing=='TARGET':s.rig_aim.location.z+=4
    bpy.context.view_layer.update()
    positions=[Vector(p) for p in ext.rig_path.positions(bpy.context,s.rig_curve,s.views) if p is not None]
    assert len(generated())==s.views*24
    for index,camera in enumerate(generated()):
        station=index//24;row=(index%24)//8;column=index%8
        pose=ext.capture_settings.camera_pose(camera,bpy.context.evaluated_depsgraph_get())
        forward=pose.to_3x3()@Vector((0,0,-1))
        if facing=='GUIDE':
            local=s.rig_curve.matrix_world.to_quaternion().inverted()@forward
            up=s.rig_curve.matrix_world.to_quaternion()@Vector((0,0,1))
        else:
            front=(ext.rig_path.tangent(positions,station,False) if facing=='PATH' else s.rig_aim.matrix_world.translation-positions[station]).normalized()
            up=s.rig_curve.matrix_world.to_quaternion()@Vector((0,0,1)) if facing=='PATH' else Vector((0,0,1))
            left=up.cross(front).normalized();up=front.cross(left).normalized()
            local=Vector((forward.dot(front),forward.dot(left),forward.dot(up)))
        projected_up=(up-forward*forward.dot(up)).normalized()
        assert (pose.to_3x3()@Vector((0,1,0))).dot(projected_up)>.99999,(facing,row,column)
        assert abs(math.asin(max(-1,min(1,local.z)))-math.radians(-60+row*60))<1e-4
        bearing=math.atan2(local.y,local.x)-column*math.pi/4
        assert abs(math.atan2(math.sin(bearing),math.cos(bearing)))<1e-4,(facing,row,column)
    s.rig_station_rows=1
    assert bpy.ops.lichtfeld.read_rig()=={'FINISHED'}
    assert s.rig_station_rows==3
counts=(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections))
recipe=s.camera_collection['lichtfeld_rig_recipe']
s.views=1000;s.rig_station_views=4;s.rig_station_rows=3
try:bpy.ops.lichtfeld.create_rig()
except RuntimeError as error:assert '10,000' in str(error)
else:raise AssertionError('Grid rows bypassed the expanded capture count')
assert counts==(len(bpy.data.objects),len(bpy.data.cameras),len(bpy.data.collections))
assert s.camera_collection['lichtfeld_rig_recipe']==recipe
print('STATION_ORDERED_FULL_TURN_THREE_ROWS_INCLINED_FRAMES_COUNT_GUARD_PASS',flush=True)

# The density proposal edits only the pending count, using the actual expanded
# limit for the current stations and rows. Apply owns camera creation.
try:
    bpy.ops.lichtfeld.panorama_density(maximum_step=math.radians(20))
except RuntimeError as error:
    assert '10,000' in str(error)
else:
    raise AssertionError('Accepted a density proposal above the capture limit')
assert s.rig_station_views == 4
s.views = 4
before = tuple(generated())
assert bpy.ops.lichtfeld.panorama_density(maximum_step=math.radians(20)) == {'FINISHED'}
assert s.rig_station_views == 18
assert tuple(generated()) == before and s.camera_collection['lichtfeld_rig_recipe'] == recipe
assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
assert len(generated()) == 4 * 3 * 18
s.rig_path_sampling = 'SPACING'
assert not bpy.ops.lichtfeld.panorama_density.poll()
s.rig_path_sampling = 'COUNT'
for shape in ('ELEVATION', 'SPHERE', 'HEMISPHERE'):
    s.rig_station_shape = shape
    assert not bpy.ops.lichtfeld.panorama_density.poll()
s.rig_station_shape = 'BAND'
s.rig_orientation = 'TANGENT'
assert not bpy.ops.lichtfeld.panorama_density.poll()
print('PANORAMA_DENSITY_PROPOSES_COUNT_AND_PRESERVES_CAMERAS_UNTIL_APPLY_PASS', flush=True)


# All radial shapes share panorama coverage while legacy radial rigs keep one view.
from collections import Counter
from mathutils import Vector

s.rig_orientation = 'PANORAMA'  # Existing curve setting is independent of radial orientation.
s.rig_station_frame = 'PATH'
s.rig_aim_mode = 'SOURCE'
s.rig_radial_orientation = 'TARGET'
s.rig_mode = 'SPHERE'
s.views = 4
assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
assert len(generated()) == 4
assert 'rig_radial_orientation' not in ext.rig_edit.decode_recipe(s.camera_collection['lichtfeld_rig_recipe'])
s.rig_radial_orientation = 'PANORAMA'
s.rig_station_shape = 'BAND'
s.rig_station_views = 4
s.rig_station_sweep = math.tau
s.rig_rings = 1
for mode in ('SPHERE', 'HEMISPHERE', 'RINGS'):
    s.rig_mode = mode
    assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    cameras = sorted(generated(), key=lambda camera: camera['lichtfeld_camera_index'])
    poses = [camera.evaluated_get(graph).matrix_world.copy() for camera in cameras]
    assert len(poses) == 16
    assert sorted(Counter(tuple(round(value, 5) for value in pose.translation) for pose in poses).values()) == [4] * 4
    aim = s.rig_aim.evaluated_get(graph).matrix_world.translation
    tilted = False
    for index in range(0, 16, 4):
        axes = [pose.to_quaternion() @ Vector((0, 0, -1)) for pose in poses[index:index + 4]]
        assert axes[0].dot((aim - poses[index].translation).normalized()) > .999
        assert abs(axes[0].dot(axes[1])) < 1e-4
        assert axes[0].dot(axes[2]) < -.999
        up = poses[index].to_quaternion() @ Vector((0, 1, 0))
        assert all(abs(axis.dot(up)) < 1e-4 for axis in axes)
        tilted |= abs(up.z) < .95
    if mode in {'SPHERE', 'HEMISPHERE'}:
        assert tilted
    assert not bpy.ops.lichtfeld.live_rig.poll()
    s.rig_radial_orientation = 'TARGET'
    assert bpy.ops.lichtfeld.read_rig() == {'FINISHED'}
    assert s.rig_radial_orientation == 'PANORAMA'
    assert s.rig_station_views == 4
s.rig_station_shape = 'SPHERE'
assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
assert len(generated()) == 16
s.rig_station_shape = 'GRID'
s.rig_station_rows = 3
s.rig_station_min, s.rig_station_max = -35, 35
assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
assert len(generated()) == 4 * 4 * 3
graph = bpy.context.evaluated_depsgraph_get()
aim = s.rig_aim.evaluated_get(graph).matrix_world.translation
first_station = [camera.evaluated_get(graph).matrix_world.copy() for camera in generated()[:12]]
up = (aim - first_station[0].translation).to_track_quat('-Z', 'Y') @ Vector((0, 1, 0))
assert abs(up.z) < .95  # This station is elevated, so its grid does not use world Z.
for index, pose in enumerate(first_station):
    axis = pose.to_quaternion() @ Vector((0, 0, -1))
    elevation = (-35, 0, 35)[index // 4]
    assert abs(axis.dot(up) - math.sin(math.radians(elevation))) < 1e-4
print('RADIAL_PANORAMA_STATIONS_PASS', flush=True)


# Hidden rotation jitter must have no effect on automatic fitting or recipe reload.
s.rig_mode = 'HEMISPHERE'
s.rig_auto_fit = True
s.rig_lens = 150
s.rig_rotation_variation = math.radians(15)
s.rig_station_shape = 'BAND'
s.rig_station_views = 5
s.rig_station_sweep = math.pi / 2
assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
applied = s.camera_collection
radius = applied['lichtfeld_generated_radius']
recipe = applied['lichtfeld_rig_recipe']
bpy.context.view_layer.update()
graph = bpy.context.evaluated_depsgraph_get()
poses = [camera.evaluated_get(graph).matrix_world.copy() for camera in generated()[:5]]
aim = s.rig_aim.evaluated_get(graph).matrix_world.translation
forward = (aim - poses[2].translation).normalized()
for pose, degrees in zip(poses, (45, 22.5, 0, 22.5, 45)):
    axis = pose.to_quaternion() @ Vector((0, 0, -1))
    assert abs(axis.dot(forward) - math.cos(math.radians(degrees))) < 1e-4
assert bpy.ops.lichtfeld.read_rig() == {'FINISHED'}
assert s.rig_rotation_variation == 0
assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
assert applied['lichtfeld_rig_recipe'] == recipe
assert math.isclose(applied['lichtfeld_generated_radius'], radius, rel_tol=1e-6)

# Test the common density operator and the other coverage shapes on radial positions.
assert bpy.ops.lichtfeld.panorama_density(maximum_step=math.radians(20)) == {'FINISHED'}
assert s.rig_station_views == 6 and len(generated()) == 20
s.rig_position_variation = 5
for coverage in ('ELEVATION', 'HEMISPHERE'):
    s.rig_station_shape = coverage
    assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
    assert len(generated()) == 24
    assert sorted(Counter(tuple(camera.location) for camera in generated()).values()) == [6] * 4

# Cancel an update after new cameras have been staged.
bpy.context.view_layer.update()
saved = [(camera, camera.data, camera.matrix_world.copy()) for camera in generated()]
saved_recipe = applied['lichtfeld_rig_recipe']
counts = (len(bpy.data.objects), len(bpy.data.cameras), len(bpy.data.collections))
s.rig_station_views = 7
owner = SimpleNamespace(collection=None, rig_backup=[], committed=False, report=lambda *args: None)
work = ext.LF_OT_rig.generate(owner, bpy.context)
while owner.collection is None or not any(obj.type == 'CAMERA' for obj in owner.collection.objects):
    next(work)
work.close()
for _ in ext.LF_OT_rig.cleanup(owner):
    pass
assert applied['lichtfeld_rig_recipe'] == saved_recipe
assert (len(bpy.data.objects), len(bpy.data.cameras), len(bpy.data.collections)) == counts
assert [(camera, camera.data, camera.matrix_world) for camera in generated()] == saved
print('RADIAL_PANORAMA_FIT_PARTIAL_DENSITY_AND_CANCEL_PASS', flush=True)


# A saved panorama recipe must restore its orientation and camera count.
from tempfile import TemporaryDirectory

with TemporaryDirectory(prefix='lichtfeld-radial-') as directory:
    saved_file = str(Path(directory) / 'panorama.blend')
    s['rig_path_sampling'] = 1  # RNA value used by pre-station .blend files.
    assert s.rig_path_sampling == 'SPACING'
    bpy.ops.wm.save_as_mainfile(filepath=saved_file)
    bpy.ops.wm.open_mainfile(filepath=saved_file, use_scripts=False)
    s = bpy.context.scene.lichtfeld_training
    assert s.rig_path_sampling == 'SPACING'
    assert bpy.ops.lichtfeld.read_rig() == {'FINISHED'}
    assert s.rig_radial_orientation == 'PANORAMA' and s.rig_station_shape == 'HEMISPHERE'
    assert s.rig_station_views == 6 and len(generated()) == 24
print('RADIAL_PANORAMA_SAVED_SCENE_PASS', flush=True)

# A floor proposal must produce exact, independently editable centers and leave the current rig intact until Apply.
bpy.ops.mesh.primitive_plane_add(size=4, location=(20, 20, 0))
floor=bpy.context.object
collection=s.camera_collection
assert bpy.ops.lichtfeld.propose_floor_stations(spacing=2, height=1.5, clearance=0)=={'FINISHED'}
assert s.camera_collection == collection and bpy.context.active_object == floor
assert s.rig_path_sampling == 'POINTS'
guide=s.rig_curve
assert len(guide.data.splines)==4 and not ext.preflight.is_surface(guide)
guide.data.splines[0].points[0].co.x += .25
ordered = [tuple(spline.points[0].co.to_3d()) for spline in guide.data.splines]
expected=set(ordered)
assert bpy.ops.lichtfeld.propose_floor_stations(refresh=True, spacing=2, height=1.5, clearance=0)=={'FINISHED'}
assert [tuple(spline.points[0].co.to_3d()) for spline in guide.data.splines] == ordered
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
actual={tuple(o.location) for o in s.camera_collection.objects if o.type=='CAMERA' and o.name.startswith('Capture ')}
assert actual == expected, (actual,expected)
print('INDOOR_STATION_RIG_PASS',flush=True)

# Updating the proposal keeps moved points and remembers removals even through a spacing change.
moved = tuple(guide.data.splines[0].points[0].co.to_3d())
removed = tuple(guide.data.splines[1].points[0].co.to_3d())
guide.data.splines.remove(guide.data.splines[1])
expected.remove(removed)
applied_collection = s.camera_collection
cameras = sorted(generated(), key=lambda camera: camera['lichtfeld_camera_index'])
flag = ext.preflight.CAMERA_EXCLUDED
deleted_view, surviving_view = cameras[6], cameras[12]
deleted_view[flag] = surviving_view[flag] = True
surviving_pose = ext.rig_geometry.pose_key(surviving_view.matrix_world)
s.rig_station_frame = 'TARGET'
assert bpy.ops.lichtfeld.propose_floor_stations(refresh=True, spacing=2, height=1.5, clearance=0)=={'FINISHED'}
assert s.rig_station_frame == 'TARGET'
s.rig_station_frame = 'GUIDE'
applied = {tuple(o.location) for o in applied_collection.objects if o.type == 'CAMERA'}
for spacing in (2, 1, 2):
    assert bpy.ops.lichtfeld.propose_floor_stations(refresh=True, spacing=spacing, height=1.5, clearance=0)=={'FINISHED'}
    assert s.rig_curve == guide and s.camera_collection == applied_collection
    points = {tuple(spline.points[0].co.to_3d()) for spline in guide.data.splines}
    assert removed not in points and moved in points, points
    assert {tuple(o.location) for o in applied_collection.objects if o.type == 'CAMERA'} == applied
assert points == expected, (points, expected)
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert {tuple(o.location) for o in s.camera_collection.objects
        if o.type == 'CAMERA' and o.name.startswith('Capture ')} == expected
excluded_poses = [ext.rig_geometry.pose_key(camera.matrix_world) for camera in generated() if camera.get(flag)]
assert len(excluded_poses) == 1, excluded_poses
assert max(abs(actual - expected) for actual, expected in zip(excluded_poses[0], surviving_pose)) < 1e-5
print('INDOOR_STATION_REFRESH_EDITS_EXCLUSIONS_PASS',flush=True)

# Imported floors may have reversed normals, while their upper surface still supports stations.
floor.scale.z = -1
bpy.context.view_layer.update()
assert len(list(ext.rig_path.floor_stations(bpy.context, floor, 2, 1.5, 0))) == 4
assert all(camera.data.display_size < .5 and camera.data.lens == s.rig_lens for camera in generated())
print('INDOOR_INVERTED_FLOOR_AND_CAMERA_DISPLAY_PASS', flush=True)

# Count mode includes fixed positions and changes the applied cameras only on Apply.
applied = {tuple(camera.location) for camera in generated()}
for count in (8, 5):
    assert bpy.ops.lichtfeld.propose_floor_stations(
        refresh=True, sampling='COUNT', count=count, height=1.5, clearance=0)=={'FINISHED'}
    points = {tuple(spline.points[0].co.to_3d()) for spline in guide.data.splines}
    assert len(points) == count and moved in points and removed not in points
    assert guide['lichtfeld_floor_station_count'] == count
    assert {tuple(camera.location) for camera in generated()} == applied
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert len(generated()) == count * s.rig_station_views
print('INDOOR_STATION_COUNT_PRESERVES_MANUAL_EDITS_PASS', flush=True)

# A solid crossing floor level encloses cameras without a floor-to-camera hit.
bpy.ops.mesh.primitive_cube_add(size=2, location=(19, 20, .6))
obstacle = bpy.context.object
obstacle.scale.y = 2.1
bpy.context.view_layer.objects.active = floor
assert bpy.ops.lichtfeld.propose_floor_stations(
    sampling='COUNT', count=900, height=1.45, clearance=.1)=={'FINISHED'}
partial = [spline.points[0].co.to_3d() for spline in s.rig_curve.data.splines]
assert 0 < len(partial) < 900 and all(point.x > 20 for point in partial)
assert s.rig_curve['lichtfeld_floor_station_count'] == 900
print('INDOOR_PARTIAL_COUNT_EXCLUDES_EMBEDDED_POSITIONS_PASS', flush=True)

# The initial bounding-box grid undersamples a thin, rotated floor.
bpy.ops.mesh.primitive_plane_add(size=1, location=(40, 40, 0), rotation=(0, 0, math.pi/4))
strip = bpy.context.object
strip.scale = (10, .5, 1)
assert bpy.ops.lichtfeld.propose_floor_stations(
    sampling='COUNT', count=20, height=1.45, clearance=0)=={'FINISHED'}
assert len(s.rig_curve.data.splines) == 20
print('INDOOR_DIAGONAL_FLOOR_COUNT_RETRY_PASS', flush=True)

# A second floor feeds the existing capture-set workflow without replacing the first room.
floor.name = 'Room A'
bpy.context.view_layer.objects.active = floor
assert bpy.ops.lichtfeld.propose_floor_stations(sampling='COUNT', count=6, clearance=0)=={'FINISHED'}
s.capture_collections.clear()
s.camera_collection = None
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
room_a = s.camera_collection
assert room_a.name == 'Capture · Room A'
room_a_poses = {camera.name: tuple(camera.location) for camera in generated()}
bpy.ops.mesh.primitive_plane_add(size=4, location=(26, 20, 0))
bpy.context.object.name = 'Room B'
assert bpy.ops.lichtfeld.propose_floor_stations(sampling='COUNT', count=6, clearance=0)=={'FINISHED'}
assert bpy.ops.lichtfeld.create_rig(new_rig=True)=={'FINISHED'}
room_b = s.camera_collection
assert room_b.name == 'Capture · Room B'
assert bpy.ops.lichtfeld.capture_collection_entry(use_edited=True)=={'FINISHED'}
assert ext.preflight.camera_collections(s) == (room_a, room_b)
assert {camera.name: tuple(camera.location) for camera in room_a.objects if camera.type=='CAMERA'} == room_a_poses
assert len(ext.cameras_for(s)) == 2 * 6 * s.rig_station_views
print('INDOOR_TWO_FLOORS_ONE_CAPTURE_PASS', flush=True)
