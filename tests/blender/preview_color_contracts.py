# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real EEVEE photometric and shared/PLY preview parity contracts."""
import bpy,sys,tempfile,time
import numpy as np
from pathlib import Path
from mathutils import Matrix
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import importlib
module='bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
bpy.ops.preferences.addon_enable(module=module)
ext=importlib.import_module(module)
colors=importlib.import_module(module+'.preview_scene')
reader=importlib.import_module(module+'.session_reader')
transport=importlib.import_module('lichtfeld_sessions.transport')
core=importlib.import_module(module+'.bridge_core')
blender_fields,load_record=reader.blender_fields,reader.load_record
SnapshotPool,read_snapshot=transport.SnapshotPool,transport.read_snapshot
write_splat=core.write_splat

def exhaust(work):
 while True:
  try:next(work)
  except StopIteration as done:return done.value

bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
s=bpy.context.scene;s.render.engine='BLENDER_EEVEE';s.render.resolution_x=s.render.resolution_y=64;s.render.resolution_percentage=100
s.render.film_transparent=False;s.world.color=(0,0,0);s.render.image_settings.file_format='PNG';s.view_settings.look='None';s.view_settings.view_transform='Standard'
c=bpy.data.cameras.new('color test');co=bpy.data.objects.new('color test',c);s.collection.objects.link(co);co.location=(0,0,5);c.type='ORTHO';c.ortho_scale=1;s.camera=co
base={'means':np.array([[0,0,.1],[0,0,0]],np.float32),'scale':np.log(np.array([[100,100,.001],[100,100,.001]],np.float32)),
 'rotation':np.tile([1,0,0,0],(2,1)).astype(np.float32),'opacity':np.array([[0],[20]],np.float32),
 'sh':(np.array([[[.1,.1,.1]],[[.8,.8,.8]]],np.float32)-.5)/.28209479177387814}
p=bpy.data.pointclouds.new('color test');p.type='GAUSSIAN_SPLAT';p.resize(2)
for name,kind,prop,value in blender_fields(base):
 a=p.attributes.get(name) or p.attributes.new(name,kind,'POINT');a.data.foreach_set(prop,value)
o=bpy.data.objects.new('color test splats',p);s.collection.objects.link(o)
with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]/"artifacts") as temp:
 def render(label):
  s.render.filepath=str(Path(temp)/(label+'.png'));bpy.ops.render.render(write_still=True)
  im=bpy.data.images.load(s.render.filepath,check_existing=False)
  try:return np.array(im.pixels[:]).reshape(64,64,4)[32,32,:3]
  finally:bpy.data.images.remove(im)
 native=render('native');assert np.max(np.abs(native-.45))>.1,native
 p.materials.append(exhaust(colors.material_steps(0)));s.view_settings.view_transform='Raw'
 corrected=render('encoded');assert np.max(np.abs(corrected-.45))<2/255,corrected
 print('ENCODED_COMPOSITING_PASS',native.tolist(),corrected.tolist(),flush=True)
 source_view=bpy.data.scenes.new('source display transform')
 source_view.view_settings.view_transform='Standard';source_view.view_settings.look='None'
 s.render.compositor_device='GPU'
 exhaust(colors.display_steps(source_view,s,True))
 decoded=render('post-composite-decode')
 assert np.max(np.abs(decoded-.45))<2/255,decoded
 assert s.view_settings.view_transform=='Standard'
 exhaust(colors.display_steps(source_view,s,False))
 assert s.view_settings.view_transform=='Raw'
 bpy.data.scenes.remove(source_view)
 print('POST_COMPOSITE_SOURCE_VIEW_TRANSFORM_PASS',flush=True)
 # Source display curves/white balance are never baked into new training RGB.
 worker=importlib.import_module(module+'.render_worker')
 s.view_settings.use_curve_mapping=True;s.view_settings.use_white_balance=True
 s.view_settings.white_balance_temperature=5000
 captured=worker.capture_color_profile(s)
 assert captured['use_curve_mapping'] and captured['use_white_balance']
 assert not s.view_settings.use_curve_mapping and not s.view_settings.use_white_balance
 s.view_settings.view_transform='Raw'
 print('CAPTURE_DISPLAY_OVERRIDES_NOT_BAKED_PASS',flush=True)
 # Each band at the -Z axis: nonzero asymmetric coefficients exercise signs/order.
 for degree,basis in [(1,[0,-.4886025119029199,0]),(2,[0,-.4886025119029199,0,0,0,.6307831305050401,0,0]),
    (3,[0,-.4886025119029199,0,0,0,.6307831305050401,0,0,0,0,0,-.7463526651802308,0,0,0])]:
  arrays={k:v[:1].copy() for k,v in base.items()};arrays['opacity'][:]=20
  arrays['sh']=np.zeros((1,(degree+1)**2,3),np.float32)
  arrays['sh'][0,0]=(.35-.5)/.28209479177387814
  values=np.arange(1,(arrays['sh'].shape[1]-1)*3+1,dtype=np.float32).reshape(-1,3)*.002
  values[::2]*=-1
  values*=np.array([1,-2,3],np.float32)
  arrays['sh'][0,1:]=values
  o.hide_render=True
  p=bpy.data.pointclouds.new('SH test');p.type='GAUSSIAN_SPLAT';p.resize(1)
  o=bpy.data.objects.new('SH test',p);s.collection.objects.link(o)
  p.materials.append(None)
  for name,kind,prop,value in blender_fields(arrays):
   a=p.attributes.get(name) or p.attributes.new(name,kind,'POINT');a.data.foreach_set(prop,value)
  p.materials[0]=exhaust(colors.material_steps(degree));p.update_tag()
  actual=render('SH'+str(degree));expected=.35+np.dot(basis,values)
  assert np.max(np.abs(actual-expected))<2/255,(degree,actual,expected)
  print('SH_RENDER_PASS',degree,actual.tolist(),expected,flush=True)
 # Off-axis SH3 exercises every coefficient, including inverse object rotation.
 from mathutils import Vector, Euler
 arrays['scale'][:]=np.log(100);arrays['means'][:]=0
 p.attributes['position'].data.foreach_set('vector',arrays['means'].ravel())
 p.attributes['scale'].data.foreach_set('vector',np.exp(arrays['scale']).ravel())
 o.rotation_euler=(.2,-.4,.3)
 for index,location in enumerate([(3,-4,5),(-4,2,3)]):
  co.location=location;co.rotation_euler=(-co.location).to_track_quat('-Z','Y').to_euler()
  bpy.context.view_layer.update()
  d=o.matrix_world.inverted().to_3x3() @ (-Vector(location));d.normalize();x,y,z=d
  basis=[-.4886025119029199*y,.4886025119029199*z,-.4886025119029199*x,
   1.0925484305920792*x*y,-1.0925484305920792*y*z,.31539156525252005*(3*z*z-1),
   -1.0925484305920792*x*z,.5462742152960396*(x*x-y*y),
   -.5900435899266435*y*(3*x*x-y*y),2.890611442640554*x*y*z,
   -.4570457994644658*y*(5*z*z-1),.3731763325901154*z*(5*z*z-3),
   -.4570457994644658*x*(5*z*z-1),1.445305721320277*z*(x*x-y*y),
   -.5900435899266435*x*(x*x-3*y*y)]
  expected=.35+np.dot(basis,values);p.update_tag()
  actual=render('off-axis'+str(index))
  assert np.max(np.abs(actual-expected))<2/255,(actual,expected)
 print('OFF_AXIS_ROTATED_SH3_PASS',flush=True)
 # Blender also compiles this material for mesh-based material previews.
 # Exercise that path with the same coefficients and orientation as the splat.
 o.hide_render=True
 bpy.ops.mesh.primitive_plane_add(size=200)
 mesh=bpy.context.object;mesh.rotation_euler=o.rotation_euler
 for name,kind,prop,value in blender_fields(arrays):
  if name.startswith(('radiance:', 'lichtfeld_rgb_tail_')):
   attribute=mesh.data.attributes.new(name,kind,'POINT')
   attribute.data.foreach_set(prop,np.tile(value,len(mesh.data.vertices)))
 mesh.data.materials.append(exhaust(colors.material_steps(3)))
 actual=render('SH3-mesh-preview')
 assert np.max(np.abs(actual-expected))<2/255,(actual,expected)
 mesh.hide_render=True;o.hide_render=False
 print('SH3_MESH_MATERIAL_RENDER_PASS',flush=True)
 # Transport parity is exact for all trained attributes and every SH coefficient.
 pool=SnapshotPool(temp)
 try:
  shared=read_snapshot(pool.write(arrays,100))
  saved,_=load_record(temp,write_splat(temp,arrays,100,'best'))
  for (an,ak,ap,av),(bn,bk,bp,bv) in zip(blender_fields(shared),blender_fields(saved)):
   assert (an,ak,ap)==(bn,bk,bp);np.testing.assert_array_equal(av,bv)
 finally:pool.close()
 print('SHARED_PLY_EXACT_ATTRIBUTE_PARITY_PASS',flush=True)
# Source scene color settings and pointcloud material slots remain native.
s.view_settings.view_transform='AgX';s.lichtfeld_training.result_object=o
original_material=o.material_slots[0].material
original_material['lichtfeld_encoded_material']=1  # Previous saved material layout.
s.view_settings.use_curve_mapping=True
s.view_settings.curve_mapping.curves[3].points.new(.4,.3)
s.view_settings.curve_mapping.update()
s.view_settings.use_white_balance=True;s.view_settings.white_balance_temperature=5000
# Upgrade a saved SH3 result with the previous three-attribute packed tail.
for a in list(p.attributes):
 if a.name.startswith('lichtfeld_rgb_tail_'):p.attributes.remove(a)
old_tail=arrays['sh'][:,12:16].reshape(len(p.points),3,4)
for i in range(3):
 attribute=p.attributes.new(f'lichtfeld_rgb_tail_{i}','FLOAT4','POINT')
 attribute.data.foreach_set('vector',old_tail[:,i].ravel())
source_camera=s.camera
source_camera.data.dof.use_dof=True
exhaust(colors.open_steps(s,bpy.context.window,None));view=bpy.context.scene
assert view.camera and view.camera != source_camera
assert view.camera.data != source_camera.data and not view.camera.data.dof.use_dof
assert source_camera.data.dof.use_dof
assert max(abs(a-b) for row,other in zip(view.camera.matrix_world,source_camera.matrix_world) for a,b in zip(row,other))<1e-5
print('ISOLATED_CAMERA_VIEW_PRESERVED_PASS',flush=True)
assert all(p.attributes.get(f'lichtfeld_rgb_tail_{i}') for i in range(reader.SH3_TAIL_ATTRIBUTES))
assert view!=s and colors.source_scene(view)==s and s.view_settings.view_transform=='AgX'
clone=next(x for x in view.objects if x.get(colors.RESULT));assert clone.data==o.data
assert clone.material_slots[0].material != original_material
assert exhaust(colors.material_steps(3)) == clone.material_slots[0].material
for name,kind,prop,value in blender_fields(arrays):
 if name.startswith('lichtfeld_rgb_tail_'):
  actual=np.empty_like(value);p.attributes[name].data.foreach_get(prop,actual)
  np.testing.assert_array_equal(actual,value)
source_material=o.material_slots[0].material
assert source_material == original_material
# Continuous pointcloud replacement keeps the isolated view current and permits reuse.
key=ext.ui_runtime.scene_key(s);ids=[]
for index in range(5):
 frame={'count':len(arrays['means']),'record':{'id':str(index),'iteration':index,'kind':'preview'},
  'job':{'id':'color parity','sources':[]},'transport':'test','data':blender_fields(arrays)}
 exhaust(ext.ui_runtime.build(s,frame,key))
 assert clone.data==s.lichtfeld_training.result_object.data
 ids.append(clone.data.as_pointer())
assert len(set(ids))==2,ids
assert len(ext.ui_runtime._cloud_buffers)==1
print('LIVE_COLOR_VIEW_DOUBLE_BUFFER_PASS',flush=True)
assert view.view_settings.view_transform=='Raw' and clone.material_slots[0].link=='OBJECT'
exhaust(colors.display_steps(s,view,True))
assert view.view_settings.view_transform=='AgX' and view.view_settings.use_curve_mapping
assert view.view_settings.use_white_balance and abs(view.view_settings.white_balance_temperature-5000)<1
assert len(view.view_settings.curve_mapping.curves[3].points)==len(s.view_settings.curve_mapping.curves[3].points)
exhaust(colors.display_steps(s,view,False))
assert view.view_settings.view_transform=='Raw' and not view.view_settings.use_curve_mapping and not view.view_settings.use_white_balance
assert s.view_settings.view_transform=='AgX' and s.view_settings.use_curve_mapping
print('SOURCE_CURVE_WHITE_BALANCE_PRESERVED_PASS',flush=True)
# Dedicated-view controls must preserve source ownership and read-only boundaries.
from unittest.mock import patch
from types import SimpleNamespace
from queue import SimpleQueue
commands=SimpleQueue()
current=SimpleNamespace(status={'state':'training','control_available':True},reader=SimpleNamespace(external=False,commands=commands))
with patch.object(ext.ui_runtime,'peek',return_value=current),patch.object(ext.ui_runtime,'binding',return_value=current):
 assert bpy.ops.lichtfeld.color_view_command(action='pause')=={'FINISHED'}
 assert commands.get_nowait()=='pause'
 s.lichtfeld_training.attached_session='read-only-test'
 try:
  try:reply=bpy.ops.lichtfeld.color_view_command(action='stop')
  except RuntimeError:reply={'CANCELLED'}
  assert reply=={'CANCELLED'} and commands.empty()
 finally:s.lichtfeld_training.attached_session=''
print('COLOR_VIEW_CONTROL_OWNERSHIP_PASS',flush=True)
colors.close_view(bpy.context);assert bpy.context.scene==s
print('ISOLATED_COLOR_SCENE_NAVIGATION_PASS',flush=True)
ext.ui_runtime.clear()
