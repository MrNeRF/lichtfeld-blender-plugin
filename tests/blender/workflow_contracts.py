# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import sys,importlib,time
import bpy
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
module='bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
if module not in bpy.context.preferences.addons:
 bpy.ops.preferences.addon_enable(module=module)
ext=importlib.import_module(module)
s=bpy.context.scene.lichtfeld_training
profile=ext.training_options.PROFILES['mrnf']
assert (s.iterations,s.max_points,s.sh_degree,s.seed_points)==(
 profile['iterations'],profile['max_cap'],profile['sh_degree'],profile['init_num_pts'])
assert s.eval_interval==profile['eval_steps'][0]
assert not s.enable_eval and ext.training_settings(s)['enable_eval'] is False
import math
for name in ext.training_options.FIELDS:
 actual,expected=getattr(s,name),profile[name]
 assert math.isclose(actual,expected,rel_tol=1e-6,abs_tol=1e-9),(name,actual,expected)
s.strategy='mcmc'
s.strategy='mrnf'
assert s.use_exposure_correction and not s.use_ppisp and not s.use_bilateral_grid
print('FRESH_SCENE_NATIVE_MRNF_DEFAULTS_PASS')
assert s.engine==bpy.context.scene.render.engine
s.use_exposure_correction=True
assert not s.use_ppisp and not s.use_bilateral_grid
s.use_ppisp=True
assert not s.use_exposure_correction
s.use_ppisp=False
assert not ext.LF_OT_start.poll(bpy.context)
assert s.quality=='CUSTOM' and s.resolution==1024
s.quality='HIGH'
assert ext.capture_settings.format_for(bpy.context.scene,s)['width']==1536
s.capture_aspect='WIDE'
s.quality='HIGH'
format_wide=ext.capture_settings.format_for(bpy.context.scene,s)
assert (format_wide['width'],format_wide['height'])==(1536,864)
s.capture_aspect='SQUARE'
s.quality='BALANCED'
assert (s.views,s.iterations)==(96,7000)
assert ext.capture_settings.format_for(bpy.context.scene,s)['width']==1024
s.views=8
assert s.quality=='CUSTOM' and s.get('quality_preset')=='BALANCED'
s.views=96
assert s.quality=='BALANCED'
s.views=8
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
first=s.camera_collection
assert sum(o.type=="CAMERA" for o in first.objects)==8
s.quality='BALANCED'
assert sum(o.type=="CAMERA" for o in first.objects)==8 and s.views==96
s.views=9
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
assert sum(o.type == "CAMERA" for o in s.camera_collection.objects)==9
assert len([c for c in bpy.data.collections if c.get('lichtfeld_generated')])==1
assert len([o for o in bpy.data.objects if o.get('lichtfeld_generated')])==9
# The volume belongs to capture setup, independently of the content source.
assert bpy.ops.lichtfeld.create_capture_region(shape="SPHERE") == {"FINISHED"}
region = s.capture_region_object
assert region.empty_display_type == "SPHERE"
for scope in ("SCENE", "SELECTION", "COLLECTION"):
 s.scope = scope
 assert s.capture_region_object == region
signature = ext.preflight.signature(bpy.context)
region.empty_display_size *= 2
assert ext.preflight.signature(bpy.context) != signature
bpy.data.objects.remove(region, do_unlink=True)
s.scope = "SCENE"
capture_region = importlib.import_module(f'{module}.capture_region')
cube = bpy.data.objects["Cube"]
cube["test_roi_attribute"] = 0.75
cube["test_roi_vector"] = [0.25, 0.5, 0.75]
original_name = cube.name
original_texspace = tuple(cube.data.texspace_size)
collection = cube.users_collection[0]
original_link = collection.collection_objects[collection.objects.find(cube.name)]
original_link.light_linking.link_state = "EXCLUDE"
default_material = bpy.data.materials.new("ROI source material")
override = bpy.data.materials.new("ROI object override")
cube.data.materials.append(default_material)
cube.material_slots[-1].link = "OBJECT"
cube.material_slots[-1].material = override
modifier = cube.modifiers.new("ROI render subdivision", "SUBSURF")
modifier.subdivision_type = "SIMPLE"
modifier.levels = 0
modifier.render_levels = 2
cube.visible_camera = False
cube.keyframe_insert(data_path="hide_render", frame=1)
assert capture_region.animated_render_visibility(cube)
cube.animation_data_clear()
volume = capture_region.local_transform("BOX", [[2,0,0,0],[0,0.5,0,0],[0,0,0.5,0],[0,0,0,1]])
asset = bpy.data.collections.new("ROI test asset")
asset_child = bpy.data.collections.new("ROI test asset child")
asset.children.link(asset_child)
instance_mesh = bpy.data.objects.new("ROI instance mesh", cube.data.copy())
asset_child.objects.link(instance_mesh)
light = bpy.data.objects.new("ROI instance light", bpy.data.lights.new("ROI instance light", "POINT"))
asset.objects.link(light)
instances = bpy.data.collections.new("ROI test instances")
bpy.context.scene.collection.children.link(instances)
inside = bpy.data.objects.new("ROI inside", None)
outside = bpy.data.objects.new("ROI outside", None)
for instance in (inside, outside):
 instances.objects.link(instance)
 instance.instance_type = "COLLECTION"
 instance.instance_collection = asset
 instance.scale = (0.1, 0.1, 0.1)
outside.location.x = 10
layer = bpy.context.view_layer.layer_collection.children[instances.name]
layer.hide_viewport = True
for member in (asset_child, instance_mesh):
 member.hide_viewport = True
 try:
  capture_region.clip_capture_scene(bpy.context, "BOX", volume, [cube])
 except ValueError as error:
  assert "different viewport and render visibility" in str(error)
 else:
  raise AssertionError("Viewport-hidden render geometry was accepted")
 member.hide_viewport = False
clipped, = capture_region.clip_capture_scene(bpy.context, "BOX", volume, [cube])
assert not inside.hide_render and outside.hide_render and layer.hide_viewport
bpy.data.collections.remove(instances)
bpy.data.objects.remove(inside)
bpy.data.objects.remove(outside)
bpy.data.objects.remove(instance_mesh, do_unlink=True)
bpy.data.objects.remove(light, do_unlink=True)
bpy.data.collections.remove(asset_child)
bpy.data.collections.remove(asset)
assert cube.hide_render and clipped != cube
assert max(abs(vertex.co.x) for vertex in clipped.data.vertices) <= 0.50001
assert len(clipped.data.polygons) > len(cube.data.polygons)
assert not clipped.visible_camera and clipped["test_roi_attribute"] == 0.75
assert clipped.material_slots[-1].material == override
assert clipped.name == original_name
assert tuple(clipped["test_roi_vector"]) == (0.25, 0.5, 0.75)
assert tuple(clipped.data.texspace_size) == original_texspace
assert modifier.levels == 0
clipped_link = collection.collection_objects[collection.objects.find(clipped.name)]
assert clipped_link.light_linking.link_state == "EXCLUDE"
original_link.light_linking.link_state = "INCLUDE"
bpy.data.objects.remove(clipped, do_unlink=True)
cube.name = original_name
cube.hide_render = False
cube.visible_camera = True
cube.modifiers.remove(modifier)
cube.data.materials.pop(index=len(cube.data.materials)-1)
del cube["test_roi_attribute"]
del cube["test_roi_vector"]
cube.hide_render = True
curve = bpy.data.curves.new("ROI Bezier", "CURVE")
curve.dimensions = "3D"
curve.bevel_depth = 0.12
spline = curve.splines.new("BEZIER")
spline.bezier_points.add(1)
for point, x in zip(spline.bezier_points, (-2, 2)):
 point.co = (x, 0, 0)
 point.handle_left_type = point.handle_right_type = "AUTO"
bezier = bpy.data.objects.new("ROI Bezier", curve)
bpy.context.scene.collection.objects.link(bezier)
tree = bpy.data.node_groups.new("ROI passthrough", "GeometryNodeTree")
for direction in ("INPUT", "OUTPUT"):
 tree.interface.new_socket(name="Geometry", in_out=direction, socket_type="NodeSocketGeometry")
incoming = tree.nodes.new("NodeGroupInput")
outgoing = tree.nodes.new("NodeGroupOutput")
tree.links.new(incoming.outputs[0], outgoing.inputs[0])
bezier.modifiers.new("Passthrough", "NODES").node_group = tree
# A real instance emitted by the modifier must still be rejected.
object_info = tree.nodes.new("GeometryNodeObjectInfo")
object_info.inputs["Object"].default_value = cube
object_info.inputs["As Instance"].default_value = True
tree.links.new(object_info.outputs["Geometry"], outgoing.inputs[0])
try:
 capture_region.clip_capture_scene(bpy.context, "BOX", volume, [bezier])
except ValueError as error:
 assert "requires realized geometry" in str(error)
else:
 raise AssertionError("An instance emitted by Geometry Nodes was accepted")
# GeometrySet instances can refer back to the curve itself in the depsgraph.
box = tree.nodes.new("GeometryNodeMeshCube")
geometry_instance = tree.nodes.new("GeometryNodeGeometryToInstance")
tree.links.new(box.outputs["Mesh"], geometry_instance.inputs["Geometry"])
tree.links.new(geometry_instance.outputs[0], outgoing.inputs[0])
for offset in (0, 10):
 bezier.location.x = offset
 try:
  capture_region.clip_capture_scene(bpy.context, "BOX", volume, [bezier])
 except ValueError as error:
  assert "requires realized geometry" in str(error)
  assert not bezier.hide_render
 else:
  raise AssertionError("A GeometrySet instance was accepted as a legacy self instance")
bezier.location.x = 0
# A mixed GeometrySet must not lose its points during mesh conversion.
points = tree.nodes.new("GeometryNodeMeshToPoints")
join = tree.nodes.new("GeometryNodeJoinGeometry")
tree.links.new(box.outputs["Mesh"], points.inputs["Mesh"])
tree.links.new(incoming.outputs[0], join.inputs[0])
tree.links.new(points.outputs["Points"], join.inputs[0])
tree.links.new(join.outputs[0], outgoing.inputs[0])
mesh_object = bpy.data.objects.new("ROI Mesh", cube.data.copy())
bpy.context.scene.collection.objects.link(mesh_object)
mesh_object.modifiers.new("Mixed output", "NODES").node_group = tree
for subject, inactive in ((bezier, mesh_object), (mesh_object, bezier)):
 inactive.hide_render = True
 original_name, original_data = subject.name, subject.data
 subject.scale = (0.1, 0.1, 0.1)
 assert capture_region.clip_capture_scene(bpy.context, "BOX", volume, [subject]) == [subject]
 assert not subject.hide_render and subject.data == original_data
 assert subject.modifiers[0].node_group == tree
 subject.location.x = 10
 assert capture_region.clip_capture_scene(bpy.context, "BOX", volume, [subject]) == []
 assert subject.hide_render
 subject.hide_render = False
 subject.location.x = 0
 subject.scale = (1, 1, 1)
 try:
  capture_region.clip_capture_scene(bpy.context, "BOX", volume, [subject])
 except ValueError as error:
  assert "cannot preserve points" in str(error)
  assert not subject.hide_render and subject.name == original_name and subject.data == original_data
 else:
  raise AssertionError(f"A mixed {subject.type} and points output was accepted")
 inactive.hide_render = False
bpy.data.objects.remove(mesh_object, do_unlink=True)
tree.links.new(incoming.outputs[0], outgoing.inputs[0])
clipped, = capture_region.clip_capture_scene(bpy.context, "BOX", volume, [bezier])
assert bezier.hide_render and clipped.type == "MESH" and clipped.data.polygons
assert all(abs(vertex.co.x) <= 0.50001 for vertex in clipped.data.vertices)
bpy.data.objects.remove(clipped, do_unlink=True)
bpy.data.objects.remove(bezier, do_unlink=True)
cube.hide_render = False
print("CAPTURE_REGION_RENDER_PROPERTIES_PASS")
s.job_directory='/previous/job'
s.previous_job=s.job_directory;s.job_directory='';s.attached_session='/external/session'
bpy.ops.lichtfeld.detach_session()
assert not s.job_directory and not s.attached_session
assert s.previous_job=='/previous/job'
for action in (lambda:bpy.ops.lichtfeld.training_command(action='pause'),lambda:bpy.ops.lichtfeld.import_result()):
 try:
  result=action();assert result=={'CANCELLED'}
 except RuntimeError:
  pass
ext.preflight._pending['stale']=True
ext.preflight.clear()
assert not ext.preflight._pending
import tempfile
from unittest.mock import patch
# Navigation retains the single setup and requests discovery only on user entry.
s.attached_session='';s.job_directory='';s.previous_job='/previous/job'
s.mode='OPEN';s.dataset_mode='EXPORT';s.rig_radius=7
with patch.object(ext,'refresh_sessions') as scan:
 assert bpy.ops.lichtfeld.setup_view(mode='NEW')=={'FINISHED'}
 assert s.mode=='NEW' and s.dataset_mode=='EXPORT' and s.rig_radius==7
 scan.assert_not_called()
 assert bpy.ops.lichtfeld.setup_view(mode='CONNECT')=={'FINISHED'}
 scan.assert_called_once()
 scan.reset_mock()
 other=bpy.data.scenes.new('Inactive trainer view');other.lichtfeld_training.mode='CONNECT'
 with patch.object(ext.preflight,'tick'),patch.object(ext.ui_runtime,'tick',return_value=.1):
  for _ in range(3):assert ext.poll_jobs()==.1
 scan.assert_not_called()
 assert bpy.ops.lichtfeld.scan_sessions()=={'FINISHED'}
 scan.assert_called_once()
 bpy.data.scenes.remove(other)
 assert bpy.ops.lichtfeld.setup_view(mode='NEW')=={'FINISHED'}
assert bpy.ops.lichtfeld.previous_job()=={'FINISHED'} and s.job_directory=='/previous/job'
assert not bpy.ops.lichtfeld.setup_view.poll()
s.job_directory='';s.operation_busy=True
assert not bpy.ops.lichtfeld.setup_view.poll()
s.operation_busy=False;s.running=True
assert not bpy.ops.lichtfeld.setup_view.poll()
s.running=False
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory)/'job'
 source=Path(directory)/'source.blend';source.write_bytes(b'saved source')
 job={'id':'launch-failure','root':str(root),'source_file':str(source),'scene':'Scene','connection_directory':str(Path(directory)/'connections')}
 with patch.object(ext.jobs,'check_environment',return_value={'ok':True}),patch.object(ext.jobs,'remember_job',side_effect=PermissionError('index denied')),patch.object(ext.subprocess,'Popen') as spawn:
  try:ext.launch_supervisor(job)
  except PermissionError:pass
  else:raise AssertionError('Expected failure')
  spawn.assert_not_called()
 assert ext.read_json(root/'state.json')['state']=='failed'
 assert not source.with_name('.lichtfeld-capture-launch-failure.blend').exists()
 for mode in ({}, {'reuse_dataset':True}, {'resume':True}):
  root=Path(directory)/('incompatible-'+str(len(mode)))
  job={'id':'incompatible','root':str(root),'backend':'changed-backend',**mode}
  with patch.object(ext.jobs,'check_environment',return_value={'ok':False,'message':'Incompatible backend'}) as check, \
       patch.object(ext.jobs,'freeze_source') as freeze,patch.object(ext.subprocess,'Popen') as spawn:
   try:ext.launch_supervisor(job)
   except ValueError as error:assert str(error)=='Incompatible backend'
   else:raise AssertionError('An incompatible backend was accepted')
   check.assert_called_once_with('changed-backend',str(root.parent))
   freeze.assert_not_called();spawn.assert_not_called()
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory)
 clean=root/'clean.blend';target=root/'capture.blend'
 prefs=ext.preferences(bpy.context)
 prefs.jobs_directory=str(root/'jobs')
 connections=ext.preflight.connection_folder(prefs)
 bpy.context.scene.lichtfeld_training.job_directory=''
 bpy.ops.wm.save_as_mainfile(filepath=str(clean))
 bpy.context.scene.frame_set(2)
 bpy.ops.wm.save_as_mainfile(filepath=str(target))
 job_root=root/'job';job_root.mkdir()
 manifest=job_root/'job.json'
 job={'schema':ext.jobs.SCHEMA,'id':'saved-capture','root':str(job_root),'source_file':str(target),
      'scene':bpy.context.scene.name,'source_identity':ext.jobs.source_identity(target.stat())}
 ext.jobs.atomic_json(manifest,job)
 ext.jobs.remember_job(connections,job)
 def settle_recovery():
  deadline=time.monotonic()+5
  while ext.ui_runtime._tasks and time.monotonic()<deadline:
   ext.ui_runtime.tick()
   time.sleep(.01)
  assert not ext.ui_runtime._tasks, 'Job recovery worker did not finish'
 bpy.ops.wm.open_mainfile(filepath=str(target),use_scripts=False)
 settle_recovery()
 assert bpy.context.scene.lichtfeld_training.job_directory==str(job_root)
 bpy.ops.wm.open_mainfile(filepath=str(clean),use_scripts=False)
 settle_recovery()
 assert not bpy.context.scene.lichtfeld_training.job_directory
 bpy.ops.wm.save_as_mainfile(filepath=str(target))
 bpy.ops.wm.open_mainfile(filepath=str(target),use_scripts=False)
 settle_recovery()
 assert not bpy.context.scene.lichtfeld_training.job_directory
 assert bpy.ops.lichtfeld.load_job(filepath=str(manifest))=={'FINISHED'}
 settle_recovery()
 assert bpy.context.scene.lichtfeld_training.job_directory==str(job_root)
 bpy.ops.wm.save_mainfile()
 bpy.ops.wm.open_mainfile(filepath=str(target),use_scripts=False)
 settle_recovery()
 assert bpy.context.scene.lichtfeld_training.job_directory==str(job_root)
 print('REPLACED_BLEND_SKIPS_AUTOMATIC_JOB_RECOVERY_PASS',flush=True)
bpy.ops.preferences.addon_disable(module=module)
assert not bpy.app.timers.is_registered(ext.poll_jobs)
print('WORKFLOW_CONTRACTS_PASS')
