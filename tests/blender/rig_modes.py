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
assert len(cameras) == 5 and all(abs(a.location.x-b.location.x-2)<1e-5 for a,b in zip(cameras,cameras[1:]))
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
