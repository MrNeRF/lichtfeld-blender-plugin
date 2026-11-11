# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real rendered appearance, hidden-surface exclusion and reusable point samples."""
from pathlib import Path
import sys,json,time,struct,subprocess,importlib
import numpy as np
import bpy
from mathutils import Matrix,Euler
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
module = 'bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
bpy.ops.preferences.addon_enable(module=module)
ext = importlib.import_module(module)
area=Path(sys.argv[sys.argv.index('--')+1]) if '--installed' in sys.argv else ROOT/'artifacts'/('rectangular-portrait' if '--portrait' in sys.argv else 'rectangular-capture' if '--rectangular' in sys.argv else 'visible-seed-shift' if '--shift' in sys.argv else 'visible-seed-cycles' if '--use-cycles' in sys.argv else 'visible-seed-mesh' if '--mesh' in sys.argv else 'visible-seed-depth' if '--depth' in sys.argv else 'visible-seed-capture')
area.mkdir(parents=True,exist_ok=True)
cube=bpy.data.objects['Cube']
material=bpy.data.materials.new('Rendered red and blue; base color intentionally green')
material.diffuse_color=(0,1,0,1);material.use_nodes=True
nodes=material.node_tree.nodes;nodes.clear()
output=nodes.new('ShaderNodeOutputMaterial');emission=nodes.new('ShaderNodeEmission');checker=nodes.new('ShaderNodeTexChecker')
checker.inputs['Color1'].default_value=(1,0,0,1);checker.inputs['Color2'].default_value=(0,0,1,1)
checker.inputs['Scale'].default_value=4
material.node_tree.links.new(checker.outputs['Color'],emission.inputs['Color'])
material.node_tree.links.new(emission.outputs[0],output.inputs['Surface'])
cube.data.materials.clear();cube.data.materials.append(material)
bpy.ops.mesh.primitive_cube_add(size=.5)
inner=bpy.context.object;inner.name='Invisible internal surface'
source_matrix=Matrix.Translation((13,17,19))@Euler((.2,-.3,.4)).to_matrix().to_4x4()
cube.matrix_world=source_matrix;inner.matrix_world=source_matrix
s=bpy.context.scene.lichtfeld_training;s.scope='SCENE';s.views=8;s.resolution=128 if '--depth' in sys.argv else 64
s.engine='CYCLES' if '--use-cycles' in sys.argv else 'BLENDER_EEVEE';s.samples=8;s.rig_distance=1.05;s.seed_points=3000
s.use_depth_loss='--depth' in sys.argv
s.use_normal_loss='--normal' in sys.argv
if '--rectangular' in sys.argv:
    s.capture_format='CUSTOM'
    s.capture_width,s.capture_height=(96,160) if '--portrait' in sys.argv else (160,96)
ext.capture_settings.apply_format(bpy.context.scene, ext.capture_settings.preset_format(s))
if '--isolated' in sys.argv:
    s.scope='COLLECTION';s.source_collection=bpy.data.collections.new('Isolated capture subjects')
    bpy.context.scene.collection.children.link(s.source_collection)
    s.source_collection.objects.link(cube);s.source_collection.objects.link(inner)
if '--surface-init' in sys.argv:s.seed_method='SURFACE'
if '--mesh' in sys.argv:
    bpy.ops.mesh.primitive_cube_add(size=8)
    helper=bpy.context.object;helper.name='Capture placement envelope'
    helper.matrix_world=source_matrix
    s.rig_mode='MESH';s.rig_mesh=helper;s.rig_mesh_distribution='FACES'
prefs=ext.preferences();prefs.backend_path='';prefs.jobs_directory=str(area/'jobs')
assert bpy.ops.lichtfeld.create_rig()=={'FINISHED'}
if '--shift' in sys.argv:
    bpy.context.scene.render.pixel_aspect_y=1.3
    if '--rectangular' not in sys.argv:
        s.capture_format='SCENE'
        bpy.context.scene.render.resolution_x=s.resolution;bpy.context.scene.render.resolution_y=s.resolution
        bpy.context.scene.render.resolution_percentage=100
    for camera in s.camera_collection.objects:
        if camera.type=='CAMERA':camera.data.shift_x=.12;camera.data.shift_y=-.08
if '--overrides' in sys.argv:
    s.override_optics=True;s.capture_lens=35;s.capture_sensor_fit='HORIZONTAL'
    s.override_shift=True;s.capture_shift_x=.08;s.capture_shift_y=-.04
    s.override_clipping=True;s.capture_clip_start=.01;s.capture_clip_end=1000
    for camera in s.camera_collection.objects:
        if camera.type=='CAMERA':camera.data.lens=70
original_render=tuple(getattr(bpy.context.scene.render,k) for k in ('resolution_x','resolution_y','resolution_percentage','pixel_aspect_x','pixel_aspect_y'))
bpy.ops.wm.save_as_mainfile(filepath=str(area/'source.blend'))
destination=area/'exports';destination.mkdir(exist_ok=True)
assert bpy.ops.lichtfeld.export_dataset('EXEC_DEFAULT',directory=str(destination))=={'FINISHED'}
root=Path(s.job_directory);deadline=time.monotonic()+180
while time.monotonic()<deadline:
    state=ext.read_json(root/'state.json',{})
    if state.get('state') in {'completed','failed','stopped'}:break
    time.sleep(.1)
assert state.get('state')=='completed',state
if '--mesh' in sys.argv:
    assert not helper.hide_render
    assert ext.read_json(root/'job.json')['capture_helpers']==[helper.name]
dataset=Path(state['export_directory']);manifest=ext.read_json(dataset/'manifest.json')
assert manifest['initialization']['method']=='visible_rendered'
raw=(dataset/'pointcloud.ply').read_bytes().split(b'end_header\n',1)[1]
points=np.frombuffer(raw,dtype=[('xyz','<f4',(3,)),('rgb','u1',(3,))])
assert 100<len(points)<=3000,len(points)
pose=np.asarray(source_matrix)
local=(points['xyz']-pose[:3,3])@pose[:3,:3]
# Renderer depth is sampled/filtered, so compare in rendered subpixel units.
frames=ext.read_json(dataset/manifest['transforms_file'])['frames']
expected_format=ext.capture_settings.format_for(bpy.context.scene,s)
assert manifest['capture_format']==expected_format
assert {(f['w'],f['h']) for f in frames}=={(expected_format['width'],expected_format['height'])}
assert tuple(getattr(bpy.context.scene.render,k) for k in ('resolution_x','resolution_y','resolution_percentage','pixel_aspect_x','pixel_aspect_y'))==original_render
if '--overrides' in sys.argv:
    assert all(camera.data.lens==70 for camera in s.camera_collection.objects if camera.type=='CAMERA')
    expected_fx=35*expected_format['width']/36
    assert all(abs(f['fl_x']-expected_fx)<1e-4 and abs(f['cx']-expected_format['width']*(.5-.08))<1e-4 for f in frames)
tolerance=max(np.linalg.norm(np.asarray(f['transform_matrix'])[:3,3]-pose[:3,3])/min(f['fl_x'],f['fl_y']) for f in frames)*.15
surface_error=np.min(np.abs(np.abs(local)-1),axis=1)
assert np.all(surface_error<tolerance),(float(surface_error.max()),tolerance,'Hidden geometry or incorrect source coordinates')
colors=points['rgb'].astype(float)
assert np.percentile(colors[:,0]+colors[:,2],50)>220 and np.percentile(colors[:,1],99)<5,'Seed used base material color instead of rendered appearance'
if '--depth' in sys.argv:
    payload=(dataset/'sparse/0/points3D.bin').read_bytes()
    count=struct.unpack_from('<Q',payload)[0];offset=8;point_tracks={};rgb=[]
    for _ in range(count):
        record=struct.unpack_from('<Q3d3BdQ',payload,offset);offset+=51
        identifier=record[0];rgb.append(record[4:7]);tracks=record[-1]
        point_tracks[identifier]=[struct.unpack_from('<II',payload,offset+i*8) for i in range(tracks)]
        offset+=tracks*8
    assert offset==len(payload) and count<=3000 and sum(map(len,point_tracks.values()))>count
    rgb=np.array(rgb,float)
    assert np.percentile(rgb[:,1],99)<5 and np.percentile(rgb[:,0]+rgb[:,2],50)>220
    image_data=(dataset/'sparse/0/images.bin').read_bytes();offset=8;images={}
    for _ in range(struct.unpack_from('<Q',image_data)[0]):
        header=struct.unpack_from('<i7di',image_data,offset);offset+=64
        offset=image_data.index(b'\0',offset)+1
        length=struct.unpack_from('<Q',image_data,offset)[0];offset+=8
        records=np.frombuffer(image_data,dtype=[('xy','<f8',(2,)),('id','<i8')],count=length,offset=offset)
        images[header[0]]=records['id'];offset+=length*24
        assert length>=256
    assert offset==len(image_data)
    assert all(images[image][index]==point for point,tracks in point_tracks.items() for image,index in tracks)
    captured = root/'dataset'
    folders = ('images', 'masks', 'depths', 'normals')
    before={str(p.relative_to(captured)):(p.stat().st_mtime_ns,p.read_bytes())
            for folder in folders for p in (captured/folder).glob('*') if p.is_file()}
    geometry_before={name:(root/'dataset'/name).read_bytes() for name in ('pointcloud.ply','sparse/0/cameras.bin','sparse/0/images.bin','sparse/0/points3D.bin')}
    recovery=subprocess.run([bpy.app.binary_path,'-b','--disable-autoexec',str(root/'capture.blend'),
        '--python-exit-code','1','--python',str(Path(ext.__file__).with_name('render_worker.py')),'--',str(root/'job.json'),'--finalize-capture'],capture_output=True,timeout=120)
    assert recovery.returncode==0,(recovery.stdout+recovery.stderr).decode(errors='replace')[-3000:]
    after={str(p.relative_to(captured)):(p.stat().st_mtime_ns,p.read_bytes())
           for folder in folders for p in (captured/folder).glob('*') if p.is_file()}
    assert before==after
    assert geometry_before=={name:(root/'dataset'/name).read_bytes() for name in geometry_before}
    print('DEPTH_SHARED_TRACKS_BUDGET_AND_NO_RENDER_RECOVERY_PASS',count,flush=True)
(area/'result.json').write_text(json.dumps({'job':str(root),'dataset':str(dataset),'points':len(points),'initialization':manifest['initialization']},indent=2))
print('VISIBLE_RENDERED_COLORS_HIDDEN_SURFACES_AND_BOUNDED_INITIALIZATION_PASS',len(points),flush=True)
