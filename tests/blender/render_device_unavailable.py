# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""An explicitly selected missing GPU must fail before producing any images."""
from pathlib import Path
import sys
import time
import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
bpy.ops.preferences.addon_enable(module='lichtfeld_training')
import lichtfeld_training as ext
output = Path(sys.argv[sys.argv.index('--')+1]).resolve()
output.mkdir(parents=True, exist_ok=True)
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.device = 'GPU'
scene.cycles.samples = 1
scene.render.resolution_x = scene.render.resolution_y = 64
scene.render.resolution_percentage = 100
preferences = bpy.context.preferences.addons['cycles'].preferences
preferences.compute_device_type = 'OPTIX'
available = preferences.get_devices_for_type('OPTIX')
selected = next(d.id for d in available if d.type == 'OPTIX')
for device in preferences.devices:
    device.use = device.type == 'OPTIX' and device.id == selected
s = scene.lichtfeld_training
s.scope = 'SCENE'
s.views = 4
s.dataset_mode = 'EXPORT'
ext.preferences().backend_path = ''
ext.preferences().jobs_directory = str(output/'jobs')
assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
bpy.ops.wm.save_as_mainfile(filepath=str(output/'source.blend'))
(output/'exports').mkdir(exist_ok=True)
# Simulate hardware disappearing after the UI has validated the selection.
launch = ext.launch_supervisor
def disappeared(job):
    job['render_settings']['request']['devices'][0].update(
        id='lichtfeld-test-missing-device', name='Missing selected GPU')
    return launch(job)
ext.launch_supervisor = disappeared
assert bpy.ops.lichtfeld.export_dataset('EXEC_DEFAULT', directory=str(output/'exports')) == {'FINISHED'}
job = Path(s.job_directory)
deadline = time.monotonic()+45
while time.monotonic() < deadline:
    state = ext.read_json(job/'state.json', {})
    if state.get('state') in {'completed','failed','stopped'}:
        break
    time.sleep(.1)
assert state.get('state') == 'failed', state
assert 'Missing selected GPU' in state['error'], state
assert not list((job/'dataset/images').glob('*.png'))
assert not (job/'training.json').exists()
assert ext.read_json(job/'capture-render.json')['state'] == 'failed'
print('MISSING_EXPLICIT_GPU_NO_CPU_FALLBACK_OR_RENDER_PASS',state['error'],flush=True)
