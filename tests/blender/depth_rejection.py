# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse,bpy,sys,time,uuid
from pathlib import Path
parser=argparse.ArgumentParser(description='Reject an explicitly supplied degenerate depth fixture')
parser.add_argument('area',type=Path)
parser.add_argument('backend',type=Path)
parser.add_argument('fixture',type=Path,help='Job JSON describing a degenerate-depth capture')
options=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
bpy.ops.preferences.addon_enable(module='lichtfeld_training')
import lichtfeld_training as ext
base=options.area.resolve();base.mkdir(parents=True,exist_ok=True)
fixture=options.fixture.resolve()
job=ext.read_json(fixture);root=base/'jobs'/uuid.uuid4().hex
(base/'job.txt').write_text(str(root))
job.update(id=root.name,root=str(root),dataset_root=str(root/'dataset'),schema=1,
 backend=str(options.backend.resolve()),blender=bpy.app.binary_path,
 iterations=32,eval_interval=16,max_points=10000,sh_degree=1,preview_seconds=1,preview_points=1000)
job.pop('capture_staging_file',None);job.pop('source_sha256',None)
ext.launch_supervisor(job)
deadline=time.monotonic()+90
while time.monotonic()<deadline:
 state=ext.read_json(root/'state.json',{})
 if state.get('state') in {'failed','completed','stopped'}:
  assert state['state']=='failed',state
  assert 'anchored' in state['error'],state
  print('DEGENERATE_DEPTH_FAILS_EXPLICITLY_PASS',flush=True);break
 time.sleep(.1)
else:raise AssertionError('Depth rejection timed out')
