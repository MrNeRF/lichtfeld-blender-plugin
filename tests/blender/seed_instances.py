# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
import sys
from pathlib import Path
import bpy,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'lichtfeld_training'))
from render_worker import write_seed
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add();cube=bpy.context.object
collection=bpy.data.collections.new('Instances');collection.objects.link(cube)
for i in range(30):
 obj=bpy.data.objects.new('Instance',None);obj.instance_type='COLLECTION';obj.instance_collection=collection
 bpy.context.scene.collection.objects.link(obj);obj.location.x=i*3
curve=bpy.data.curves.new('Curve','CURVE');curve.dimensions='3D';curve.bevel_depth=.1
spl=curve.splines.new('POLY');spl.points.add(1);spl.points[0].co=(0,0,0,1);spl.points[1].co=(0,0,3,1)
obj=bpy.data.objects.new('Curve',curve);bpy.context.scene.collection.objects.link(obj)
bpy.context.view_layer.update()
path=Path(__file__).resolve().parents[2]/'artifacts/instance-seed.ply'
for repeat in range(3):
 write_seed(path,[cube,obj],3000)
 with path.open('rb') as stream:
  count=0
  while (line:=stream.readline())!=b'end_header\n':
   if line.startswith(b'element vertex '):count=int(line.split()[-1])
  data=np.fromfile(stream,dtype=[('xyz','<f4',(3,)),('rgb','u1',(3,))])
 assert 0<len(data)==count<=3000 and np.isfinite(data['xyz']).all()
 assert np.max(data['xyz'][:,0])>85
print('TRANSIENT_INSTANCES_AND_CURVE_SEED_PASS',flush=True)
