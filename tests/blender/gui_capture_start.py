# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real GUI: deferred callbacks must outlive their non-modal operator safely."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import time
import traceback
import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
bpy.ops.preferences.addon_enable(module='lichtfeld_training')
import lichtfeld_training as ext
output = Path(sys.argv[sys.argv.index('--')+1]).resolve()
output.mkdir(parents=True, exist_ok=True)
scene = bpy.context.scene
s = scene.lichtfeld_training
prefs = ext.preferences()
prefs.backend_path = str(ROOT.parent/'build/LichtFeld-Studio.exe')
prefs.jobs_directory = str(output/'jobs')
s.scope = 'SCENE'; s.views = 4; s.resolution = 64
s.engine = 'BLENDER_EEVEE'; s.samples = 1
ext.capture_settings.apply_format(scene, ext.capture_settings.preset_format(s))
owner = SimpleNamespace(collection=None, rig_backup=[], committed=False, report=lambda *args:None)
for _ in ext.LF_OT_rig.generate(owner, bpy.context):
    pass
bpy.ops.wm.save_as_mainfile(filepath=str(output/'source.blend'))
bpy.context.preferences.view.show_splash = False
calls = []
notices = []
launch = ext.LF_OT_start.launch
notice = ext.ui_runtime.report_notice
open_folder = ext.jobs.open_folder
restart_data = ext.jobs.restart_data
def record_launch(context, capture, destination=''):
    assert context.scene == scene
    assert len(capture['cameras']) == 4 and capture['sources']
    calls.append(destination)
    return {'FINISHED'}
def record_notice(level, message):
    notices.append((level, str(message)))
    notice(level, message)  # Exercise the real, fresh report operator too.
def denied_folder(_path):
    raise PermissionError('injected folder access failure')

def denied_restart(*args, **kwargs):
    raise PermissionError('injected saved dataset access failure')
ext.LF_OT_start.launch = staticmethod(record_launch)
ext.ui_runtime.report_notice = record_notice
ext.jobs.open_folder = denied_folder
ext.jobs.restart_data = denied_restart
phase = 'ready'
deadline = time.monotonic()+35
proof = {}

def finish(ok, error=''):
    ext.LF_OT_start.launch = staticmethod(launch)
    ext.ui_runtime.report_notice = notice
    ext.jobs.open_folder = open_folder
    ext.jobs.restart_data = restart_data
    proof.update(ok=ok, error=error, notices=notices, launches=calls)
    (output/'result.json').write_text(json.dumps(proof), encoding='utf-8')
    print('GUI_DEFERRED_OPERATOR_LIFETIME_PASS' if ok else 'GUI_DEFERRED_OPERATOR_LIFETIME_FAILED', json.dumps(proof), flush=True)
    bpy.ops.wm.quit_blender()
    return None

def tick():
    global phase
    try:
        assert time.monotonic()<deadline, (phase, s.status, s.detail)
        if phase == 'ready':
            ext.preflight.tick(bpy.context, prefs)
            ext.ui_runtime.tick()
            ready = ext.preflight.cached(bpy.context, prefs)
            if not ext.preflight.issues(ready):
                assert bpy.ops.lichtfeld.start_training('EXEC_DEFAULT') == {'FINISHED'}
                assert s.operation_busy and not calls
                phase = 'train_wait'
        elif phase == 'train_wait':
            ext.ui_runtime.tick()
            assert s.status != 'Failed', s.detail
            if calls:
                assert calls == [''] and not s.operation_busy
                phase = 'export_start'
        elif phase == 'export_start':
            ext.preflight.tick(bpy.context, prefs)
            ext.ui_runtime.tick()
            if not ext.preflight.issues(ext.preflight.cached(bpy.context, prefs, capture_only=True)):
                assert bpy.ops.lichtfeld.export_dataset('EXEC_DEFAULT', directory=str(output/'export')) == {'FINISHED'}
                assert len(calls)==1 and s.operation_busy
                phase = 'export_wait'
        elif phase == 'export_wait':
            ext.ui_runtime.tick()
            assert s.status != 'Failed', s.detail
            if len(calls)==2:
                assert Path(calls[1]) == output/'export'
                assert not s.operation_busy
                s.job_directory = str(output/'jobs'/'notice-fixture')
                bpy.ops.lichtfeld.open_job()
                s.job_directory = ''
                phase = 'folder_wait'
        elif phase == 'folder_wait':
            ext.ui_runtime.tick()
            if notices:
                assert notices[-1][0]=='WARNING' and 'injected folder access failure' in notices[-1][1]
                assert s.status != 'Failed'
                phase = 'error_ready'
        elif phase == 'error_ready':
            ext.preflight.tick(bpy.context, prefs)
            ext.ui_runtime.tick()
            if not ext.preflight.issues(ext.preflight.cached(bpy.context,prefs)):
                assert bpy.ops.lichtfeld.start_training('EXEC_DEFAULT') == {'FINISHED'}
                # Change the scene only after execute has returned and before
                # its queued generator resumes. The ERROR reporter must survive.
                scene.frame_set(scene.frame_current+1)
                phase = 'error_wait'
        elif phase == 'error_wait':
            ext.ui_runtime.tick()
            if s.status == 'Failed':
                assert 'capture setup changed' in s.detail.lower(), s.detail
                assert any(level=='ERROR' and 'capture setup changed' in message.lower() for level,message in notices)
                assert 'StructRNA' not in str(notices)
                assert len(calls)==2
                proof.update(train=True, export=True, deferred_warning=True, deferred_error=True)
                s.dataset_job_directory = str(output/'missing-dataset')
                assert bpy.ops.lichtfeld.restart_training('EXEC_DEFAULT') == {'FINISHED'}
                assert s.operation_busy
                phase = 'restart_failure_wait'
        elif phase == 'restart_failure_wait':
            ext.ui_runtime.tick()
            if not s.operation_busy:
                assert s.dataset_summary == 'injected saved dataset access failure', s.dataset_summary
                assert notices[-1] == ('ERROR', s.dataset_summary), notices
                assert 'StructRNA' not in str(notices)
                proof['deferred_restart_error'] = True
                return finish(True)
        return .02
    except Exception:
        return finish(False, traceback.format_exc())

bpy.app.timers.register(tick, first_interval=1)
