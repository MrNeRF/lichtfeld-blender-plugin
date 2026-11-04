# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real capture honors official Blender settings and explicit worker devices."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import time
import subprocess
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
bpy.ops.preferences.addon_enable(module='lichtfeld_training')
import lichtfeld_training as ext

output = Path(sys.argv[sys.argv.index('--')+1]).resolve()
output.mkdir(parents=True, exist_ok=True)
scene = bpy.context.scene
s = scene.lichtfeld_training
prefs = ext.preferences()
prefs.backend_path = ''
prefs.jobs_directory = str(output/'jobs')

for cls in ext._CLASSES:
    if issubclass(cls, bpy.types.Operator):
        assert len(getattr(cls, 'bl_description', '')) >= 20, cls.__name__
    for name in getattr(cls, '__annotations__', {}):
        prop = cls.bl_rna.properties.get(name)
        assert prop is None or prop.description, (cls.__name__, name)
        if prop and prop.type == 'ENUM' and name != 'discovered_session':
            assert all(item.description for item in prop.enum_items), (cls.__name__, name)
assert 'checkpoint' in ext.LF_OT_command.description(bpy.context, SimpleNamespace(action='stop'))
assert 'continues' in ext.LF_OT_command.description(bpy.context, SimpleNamespace(action='preview_off'))

# A preset edits official settings; direct official edits are reflected immediately.
s.quality = 'DRAFT'
assert s.quality == 'DRAFT'
scene.eevee.taa_render_samples += 1
assert s.quality == 'CUSTOM'
scene.eevee.taa_render_samples -= 1
assert s.quality == 'DRAFT'
scene.render.engine = 'CYCLES' if '--use-cycles' in sys.argv else 'BLENDER_EEVEE'
scene.cycles.device = 'GPU' if '--gpu' in sys.argv else 'CPU'
scene.cycles.samples = 2
scene.cycles.use_adaptive_sampling = False
scene.cycles.use_denoising = False
scene.cycles.max_bounces = 3
scene.render.use_persistent_data = False
scene.eevee.taa_render_samples = 3
assert s.samples == (2 if '--use-cycles' in sys.argv else 3)
assert s.engine == scene.render.engine
assert s.cycles_device == scene.cycles.device
if '--gpu' in sys.argv or '--cpu-gpu-denoise' in sys.argv:
    cycles = bpy.context.preferences.addons['cycles'].preferences
    backend = 'CUDA' if '--cuda' in sys.argv else 'OPTIX'
    cycles.compute_device_type = backend
    available = cycles.get_devices_for_type(backend)
    selected = next(d.id for d in available if d.type == backend)
    for device in cycles.devices:
        device.use = device.type == backend and device.id == selected
if '--cpu-gpu-denoise' in sys.argv:
    scene.cycles.use_denoising = True
    scene.cycles.denoising_use_gpu = True
    scene.cycles.denoiser = 'OPTIX'
if '--opengl' in sys.argv:
    bpy.context.preferences.system.gpu_backend = 'OPENGL'
    bpy.context.preferences.system.gpu_preferred_device = 'AUTO'

scene.render.resolution_x = 128
scene.render.resolution_y = 80
scene.render.resolution_percentage = 100
scene.render.pixel_aspect_x = 1
scene.render.pixel_aspect_y = 1.2
s.resolution = 64  # Unapplied helpers must never override official output.
s.capture_aspect = 'SQUARE'
assert ext.capture_settings.format_for(scene, s)['width'] == 128

# Stable emission allows an actual color check independent of lighting/backend.
cube = bpy.data.objects['Cube']
material = bpy.data.materials.new('Capture reference emission')
material.use_nodes = True
nodes = material.node_tree.nodes
nodes.clear()
emission = nodes.new('ShaderNodeEmission')
emission.inputs['Color'].default_value = (.2,.4,.6,1)
out = nodes.new('ShaderNodeOutputMaterial')
material.node_tree.links.new(emission.outputs[0], out.inputs['Surface'])
cube.data.materials.clear()
cube.data.materials.append(material)
s.scope = 'SCENE'
s.views = 4
s.seed_points = 1000
s.dataset_mode = 'EXPORT'
assert bpy.ops.lichtfeld.create_rig() == {'FINISHED'}
before = ext.scene_render.snapshot(scene)
source = output/'source.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source))
source_bytes = source.read_bytes()
(output/'exports').mkdir(exist_ok=True)
assert bpy.ops.lichtfeld.export_dataset('EXEC_DEFAULT', directory=str(output/'exports')) == {'FINISHED'}
job = Path(s.job_directory)
deadline = time.monotonic()+180
while time.monotonic() < deadline:
    state = ext.read_json(job/'state.json', {})
    if state.get('state') in {'completed','failed','stopped'}:
        break
    time.sleep(.1)
assert state.get('state') == 'completed', (state, (job/'render.log').read_text(errors='replace')[-4000:])
report = ext.read_json(job/'capture-render.json')
assert report['requested'] == before
assert report['effective']['backend'] == before['request']['backend']
assert report['effective']['devices']
if '--cpu-gpu-denoise' in sys.argv:
    assert report['effective']['backend'] == 'CPU'
    assert report['effective']['preferences']['backend'] == 'OPTIX'
    assert any(d['type']=='OPTIX' for d in report['effective']['preferences']['devices'])
assert ext.scene_render.snapshot(scene) == before
assert source.read_bytes() == source_bytes
assert scene.cycles.max_bounces == 3
assert not (job/'training.json').exists()
manifest = ext.read_json(job/'dataset/manifest.json')
assert manifest['capture_format'] == {'width':128,'height':80,'pixel_aspect_x':1.,'pixel_aspect_y':scene.render.pixel_aspect_y}
image = bpy.data.images.load(str(job/'dataset/images/view_000000.png'), check_existing=False)
try:
    image.colorspace_settings.name = 'Non-Color'
    pixels = np.empty(len(image.pixels), np.float32)
    image.pixels.foreach_get(pixels)
    center = pixels.reshape(80,128,4)[40,64,:3]
    expected = 1.055*np.power([.2,.4,.6],1/2.4)-.055
    assert np.max(np.abs(center-expected)) < .025, (center,expected)
finally:
    bpy.data.images.remove(image)
if '--gpu' in sys.argv:
    # Finalization reuses existing pixels and must work even when that GPU is no
    # longer present. A missing device would fail immediately if queried.
    config = ext.read_json(job/'job.json')
    saved_config = (job/'job.json').read_bytes()
    before_images = {p.name:(p.stat().st_mtime_ns,p.read_bytes()) for p in (job/'dataset/images').glob('*.png')}
    config['render_settings']['request']['devices'][0]['id'] = 'removed-gpu'
    ext.atomic_json(job/'job.json', config)
    try:
        recovery = subprocess.run([bpy.app.binary_path,'-b','--factory-startup','--disable-autoexec',str(job/'capture.blend'),
            '--python-exit-code','1','--python',str(ROOT/'lichtfeld_training/render_worker.py'),'--',str(job/'job.json'),'--finalize-capture'],
            capture_output=True,timeout=90)
        assert recovery.returncode == 0, (recovery.stdout+recovery.stderr).decode(errors='replace')[-3000:]
        assert before_images == {p.name:(p.stat().st_mtime_ns,p.read_bytes()) for p in (job/'dataset/images').glob('*.png')}
        assert ext.read_json(job/'capture-render.json') == report
    finally:
        (job/'job.json').write_bytes(saved_config)
    print('FINALIZATION_REUSES_IMAGES_WITHOUT_AVAILABLE_GPU_PASS',flush=True)
print('OFFICIAL_RENDER_SETTINGS_DEVICE_AND_TOOLTIP_CONTRACTS_PASS', json.dumps(report), flush=True)
