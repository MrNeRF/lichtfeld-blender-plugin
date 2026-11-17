# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender: each task exposes one action and validates only its inputs."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys, importlib
import time
import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
module='bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
bpy.ops.preferences.addon_enable(module=module)
ext=importlib.import_module(module)

scene = bpy.context.scene
s = scene.lichtfeld_training
s.max_points = 10_000_001
assert s.max_points == 10_000_001
s.max_points = 250_000
prefs = ext.preferences()
area = Path(sys.argv[sys.argv.index('--')+1]).resolve()
area.mkdir(parents=True, exist_ok=True)
prefs.jobs_directory = str(area/'jobs')
prefs.backend_path = ''
s.scope = 'SCENE'
s.views = 4
bpy.ops.lichtfeld.create_rig()
s.refine_start = .9
s.refine_end = .1  # Training errors must not block a standalone dataset.
bpy.ops.wm.save_as_mainfile(filepath=str(area/'source.blend'))

# Existing saved files stored CAPTURE=0 and REUSE=1. Adding EXPORT preserves both.
items = s.bl_rna.properties['dataset_mode'].enum_items
assert {item.identifier: item.value for item in items} == {'CAPTURE':0, 'REUSE':1, 'EXPORT':2}

class Layout:
    def __init__(self, records=None, parent=None):
        self.records = [] if records is None else records
        self.parent = parent
        self.enabled = True
    def child(self): return Layout(self.records, self)
    def box(self): return self.child()
    def row(self, **kwargs): return self.child()
    def column(self, **kwargs): return self.child()
    def panel(self, *args, **kwargs): return self.child(),self.child()
    def prop(self, owner, name, **kwargs): self.records.append(('prop', name))
    def label(self, **kwargs): self.records.append(('label',))
    def operator(self, name, **kwargs):
        self.records.append(('operator', name))
        return SimpleNamespace()

context = SimpleNamespace(scene=scene, view_layer=bpy.context.view_layer, region=SimpleNamespace(width=300), preferences=bpy.context.preferences)
ready = {'checking':False, 'errors':[], 'cameras':[1,2,3,4], 'environment':{'ok':True}}
for task, action in [('CAPTURE','lichtfeld.start_training'), ('EXPORT','lichtfeld.export_dataset'),
                     ('REUSE','lichtfeld.restart_training')]:
    s.dataset_mode = task
    layout = Layout()
    with patch.object(ext.preflight, 'cached', return_value=ready) as cached, \
         patch.object(ext.preflight, 'environment', return_value={'backend':True}):
        ext.draw_training(SimpleNamespace(layout=layout), context)
    actions = [name for kind, *rest in layout.records if kind=='operator' for name in rest
               if name in {'lichtfeld.start_training','lichtfeld.export_dataset','lichtfeld.restart_training'}]
    assert actions == [action], (task, actions)
    fields = [item[1] for item in layout.records if item[0]=='prop']
    assert ('iterations' in fields) == (task != 'EXPORT'), (task, fields)
    assert ('preview_mode' in fields) == (task != 'EXPORT')
    assert ('camera_collection' in fields) == (task != 'REUSE')
    if task=='EXPORT':
        assert 'use_depth_loss' in fields and 'use_normal_loss' in fields
        assert 'depth_loss_weight' not in fields and 'normal_loss_weight' not in fields
        assert all(call.kwargs.get('capture_only') is True for call in cached.call_args_list)

# An unavailable backend exposes setup, not unusable training controls.
s.dataset_mode = 'CAPTURE'
layout = Layout()
with patch.object(ext.preflight, 'environment', return_value={'backend':False, 'message':'Choose LichtFeld Studio'}):
    ext.draw_training(SimpleNamespace(layout=layout), context)
assert ('operator', 'lichtfeld.setup') in layout.records
assert ('operator', 'lichtfeld.start_training') not in layout.records
assert not any(kind == 'prop' and name in {'iterations', 'camera_collection'} for kind, *items in layout.records for name in items)
assert bpy.ops.lichtfeld.setup_view(mode='EXPORT') == {'FINISHED'}
assert s.mode == 'NEW' and s.dataset_mode == 'EXPORT'

# Validation updates never move the export button or editable fields.
s.dataset_mode = 'EXPORT'
orders = []
for checking, errors in [(True,['Checking source and cameras…']), (False,[]),
                         (False,['Capture error '*30])]:
    layout = Layout()
    value = {**ready, 'checking':checking, 'errors':errors}
    with patch.object(ext.preflight, 'cached', return_value=value), patch.object(ext.preflight, 'issues', return_value=errors):
        ext.draw_training(SimpleNamespace(layout=layout), context)
    orders.append(layout.records)
assert all(order == orders[0] for order in orders)

def collect(capture_only):
    work = ext.preflight.collect(bpy.context, capture_only=capture_only)
    while True:
        try: next(work)
        except StopIteration as stop: return stop.value

assert not collect(True)['errors']
assert any('Refinement' in error for error in collect(False)['errors'])
assert ext.jobs.check_environment('', prefs.jobs_directory, require_backend=False)['ok']
assert not ext.jobs.check_environment('', prefs.jobs_directory, require_backend=True)['ok']

# Saving refreshes source identity and permissions, not the executable probe.
backend = area/'fake-backend'
backend.write_bytes(b'backend')
prefs.backend_path = str(backend)
ext.preflight.clear()
def wait_environment():
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = ext.preflight.environment(prefs)
        ext.poll_jobs()
        if not value.get('checking'):
            return value
        time.sleep(.01)
    raise AssertionError('Environment check timed out')
with patch.object(ext.jobs, 'verify_backend', return_value='Verified backend') as verify:
    before = wait_environment()
    assert before['ok'], before
    bpy.ops.wm.save_as_mainfile(filepath=str(area/'source.blend'))
    checking = ext.preflight.environment(prefs)
    assert checking.get('checking') and checking.get('backend') and not checking['ok'], checking
    after = wait_environment()
    assert after['ok'], after
    assert after['generation'] != before['generation']
    verify.assert_called_once()
    # A different saved scene is checked again without hiding the training UI.
    bpy.ops.wm.save_as_mainfile(filepath=str(area/'other-source.blend'))
    checking = ext.preflight.environment(prefs)
    assert checking.get('checking') and checking.get('backend') and not checking['ok'], checking
    assert wait_environment()['ok']
    verify.assert_called_once()
    checking = ext.preflight.environment(prefs, force=True)
    assert checking.get('checking') and checking.get('backend') and not checking['ok'], checking
    assert wait_environment()['ok']
    assert verify.call_count == 2
# An explicit verification supersedes an in-flight cached check, including its callback.
ext.preflight.clear()
queued = []
with patch.object(ext.ui_runtime, 'submit', side_effect=lambda *args: queued.append(args)):
    ext.preflight.environment(prefs)
    info = backend.stat()
    identity = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
    queued.pop(0)[1]({'ok': True, 'backend': True, 'folder': True, 'message': 'Verified backend',
                      'backend_version': 'Verified backend', 'backend_identity': identity})
    ext.preflight._save_generation += 1
    ext.preflight.environment(prefs)
    old_check = queued.pop(0)
    assert ext.preflight.environment(prefs, force=True)['backend']
    new_check = queued.pop(0)
    assert ext.preflight.environment(prefs)['backend']
    old_check[1]({'ok': True, 'backend': True, 'folder': True, 'message': 'Stale result'})
    new_check[2]('Incompatible backend')
    final = ext.preflight.environment(prefs)
    assert not final['backend'] and final['message'] == 'Incompatible backend', final
    ext.preflight.environment(prefs, capture_only=True)
    stale_capture = queued.pop(0)
    ext.preflight.environment(prefs, force=True)
    forced_check = queued.pop(0)
    stale_capture[1]({'ok': False, 'backend': True, 'folder': False, 'message': 'Stale folder error'})
    assert ext.preflight.environment(prefs, capture_only=True)['checking']
    assert queued  # Dataset-only validation was scheduled again.
    forced_check[2]('Incompatible backend')
ext.preflight.clear()
prefs.backend_path = ''
with patch.object(ext.preflight, 'environment') as environment, patch.object(ext.scene_render, 'tick'):
    ext.preflight.tick(bpy.context, prefs)
    assert environment.call_args.kwargs['capture_only'] is True
ext.preflight.clear()
with patch.object(ext.jobs, 'freeze_source') as freeze:
    try:
        ext.launch_supervisor({'id':'invalid-destination','root':str(area/'invalid-job'),
                               'capture_only':True,'export_parent':str(area/'missing-folder')})
        raise AssertionError('Invalid export destination was accepted')
    except ValueError:
        pass
    freeze.assert_not_called()

# A second export keeps the task; training later uses the published copy.
s.running = False
s.job_directory = str(area/'internal-job')
s.dataset_mode = 'CAPTURE'
published = str(area/'published-dataset')
binding = SimpleNamespace(status={'job':{'capture_only':True}, 'export_directory':published})
with patch.object(ext.ui_runtime, 'peek', return_value=binding), \
     patch.object(ext.ui_runtime, 'reset_for_new_training'), patch.object(ext.ui_runtime, 'submit'):
    assert bpy.ops.lichtfeld.new_capture() == {'FINISHED'}
    assert s.dataset_mode == 'EXPORT'
    assert bpy.ops.lichtfeld.use_dataset() == {'FINISHED'}
    assert s.dataset_mode == 'REUSE' and s.dataset_job_directory == published
    folders = []
    def open_later(function, **kwargs): folders.append(function)
    with patch.object(ext.ui_runtime, 'submit', side_effect=open_later), \
         patch.object(ext.jobs, 'open_folder') as opener:
        assert bpy.ops.lichtfeld.open_job(dataset=True) == {'FINISHED'}
        assert not opener.called
        folders[0]()
        opener.assert_called_once_with(published)
print('CAPTURE_TASK_ACTIONS_VALIDATION_EXPORT_REUSE_PASS', flush=True)
s.job_directory = ''
ext.ui_runtime.clear()
