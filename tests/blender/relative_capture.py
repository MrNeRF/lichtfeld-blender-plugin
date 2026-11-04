# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import bpy,sys,subprocess,importlib
area=Path(sys.argv[sys.argv.index('--')+1]);area.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.preferences.addon_enable(module='lichtfeld_training')
import lichtfeld_training as ext
image=bpy.data.images.new('Relative image',width=8,height=8)
image.generated_color=(.7,.2,.1,1)
image.filepath_raw=str(area/'texture.png');image.file_format='PNG';image.save()
mat=bpy.data.materials.new('Textured');mat.use_nodes=True
node=mat.node_tree.nodes.new('ShaderNodeTexImage');node.image=image
mat.node_tree.links.new(node.outputs['Color'],mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])
bpy.ops.mesh.primitive_cube_add();obj=bpy.context.object;obj.data.materials.append(mat)
assets=area/'assets';assets.mkdir(exist_ok=True)
library=assets/'library.blend'
bpy.data.libraries.write(str(library),{obj.data},path_remap='RELATIVE')
name=obj.data.name;bpy.data.objects.remove(obj,do_unlink=True)
with bpy.data.libraries.load(str(library),link=True) as (src,dst):dst.meshes=[name]
obj=bpy.data.objects.new('Linked source',dst.meshes[0]);bpy.context.scene.collection.objects.link(obj)
obj.select_set(True);bpy.context.view_layer.objects.active=obj
s=bpy.context.scene.lichtfeld_training;s.views=8
bpy.ops.lichtfeld.create_rig()
for lib in bpy.data.libraries:lib.filepath=bpy.path.relpath(lib.filepath,start=str(area))
source=area/'source.blend';bpy.ops.wm.save_as_mainfile(filepath=str(source))
root=area/'job';root.mkdir(exist_ok=True)
job={'id':'relative-test','root':str(root),'source_file':str(source),'scene':bpy.context.scene.name,'view_layer':bpy.context.view_layer.name,
 'frame':1,'scope':'SELECTION','sources':[obj.name],'cameras':[o.name for o in s.camera_collection.objects if o.type == "CAMERA"],
 'engine':'BLENDER_EEVEE','resolution':64,'samples':1,'seed_points':1000,'test_every':4}
ext.jobs.freeze_source(job);ext.atomic_json(root/'job.json',job)
try:
 result=subprocess.run([bpy.app.binary_path,'--background','--factory-startup','--disable-autoexec',job['capture_staging_file'],'--python-exit-code','1','--python',str(Path(ext.__file__).with_name('render_worker.py')),'--',str(root/'job.json')],capture_output=True,timeout=120)
 if result.returncode:raise AssertionError(result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace'))
 assert (root/'dataset/transforms.json').is_file()
 bpy.ops.wm.open_mainfile(filepath=str(root/'capture.blend'))
 for lib in bpy.data.libraries:assert Path(bpy.path.abspath(lib.filepath)).is_file(),lib.filepath
 for image in bpy.data.images:
  if image.source=='FILE' and image.filepath and not image.packed_file:
   assert Path(bpy.path.abspath(image.filepath,library=image.library)).is_file(),image.filepath
 print('RELATIVE_LINKED_CAPTURE_PASS',flush=True)
finally:ext.jobs.release_source(job)
