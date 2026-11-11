# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender event-loop heartbeat while the modal operator creates 1000 cameras."""
from pathlib import Path
import json
import importlib
import sys
import time
import traceback
from types import SimpleNamespace
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
module = 'bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
bpy.ops.preferences.addon_enable(module=module)
extension = importlib.import_module(module)
output = Path(sys.argv[sys.argv.index("--") + 1])
if '--installed' in sys.argv:
    output = output/'result.json'
state = {"ticks": 0, "max_gap_ms": 0}


def start():
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.scene.lichtfeld_training.views = 1000
    assert bpy.ops.lichtfeld.create_rig() == {"RUNNING_MODAL"}
    state["started"] = state["last"] = time.perf_counter()
    bpy.app.timers.register(heartbeat, first_interval=.01)


def heartbeat():
    now = time.perf_counter()
    state["ticks"] += 1
    state["max_gap_ms"] = max(state["max_gap_ms"], (now - state["last"]) * 1000)
    state["last"] = now
    settings = bpy.context.scene.lichtfeld_training
    if not settings.operation_busy:
        state["cameras"] = sum(o.type == "CAMERA" for o in settings.camera_collection.objects)
        assert state["cameras"] == 1000 and state["ticks"] > 5, state
        state["elapsed"] = now - state["started"]
        output.write_text(json.dumps(state))
        print("RIG_EVENT_LOOP_HEARTBEAT_OK", state, flush=True)
        bpy.ops.wm.quit_blender()
        return None
    assert now - state["started"] < 60, state
    return .01


def render_interleave():
    """A native render may start between two rig TIMER callbacks."""
    scene=bpy.context.scene
    settings=scene.lichtfeld_training
    window=bpy.context.window
    other=bpy.data.scenes.new('Render interleave context')
    settings.scope='SCENE';settings.views=48
    owner=SimpleNamespace(collection=None,rig_backup=[],committed=False,report=lambda *args:None)
    for _ in extension.LF_OT_rig.generate(owner,bpy.context):pass
    rig=settings.camera_collection
    original_ids=[o.as_pointer() for o in rig.objects if o.type=='CAMERA']
    scene.camera=next(o for o in rig.objects if o.type=='CAMERA')
    scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1024
    scene.cycles.use_adaptive_sampling=False;scene.cycles.use_denoising=False
    scene.render.resolution_x=128;scene.render.resolution_y=128;scene.render.resolution_percentage=100
    bpy.context.preferences.view.render_display_type='NONE'
    proof={'phase':'idle','render_ticks':0,'ticks':0}
    deadline=time.monotonic()+150

    def snapshot():
        return (tuple((o.as_pointer(),o.data.as_pointer() if o.data else 0,
                       tuple(tuple(row) for row in o.matrix_world)) for o in bpy.data.objects),
                tuple(o.as_pointer() for o in bpy.data.cameras),
                tuple(o.as_pointer() for o in bpy.data.collections))

    def render(phase,lock=False):
        scene.render.use_lock_interface=lock
        window.view_layer.update()
        proof.update(phase=phase,render_ticks=0)
        proof['snapshot']=snapshot()
        with bpy.context.temp_override(window=window):
            assert bpy.ops.render.render('INVOKE_DEFAULT')=={'RUNNING_MODAL'}

    def update():
        settings.views=1000
        with bpy.context.temp_override(window=window):
            assert bpy.ops.lichtfeld.create_rig()=={'RUNNING_MODAL'}

    def finish(ok):
        proof.pop('snapshot',None)
        proof['ok']=ok
        output.write_text(json.dumps(proof),encoding='utf-8')
        print('RIG_NATIVE_RENDER_INTERLEAVE_PASS' if ok else 'RIG_NATIVE_RENDER_INTERLEAVE_FAILED',proof,flush=True)
        bpy.ops.wm.quit_blender()

    def tick():
        try:
            proof['ticks']+=1
            assert time.monotonic()<deadline,proof['phase']
            phase=proof['phase']
            rendering=bpy.app.is_job_running('RENDER')
            if phase in {'idle','precommit','postcommit'}:
                if rendering:
                    proof['render_ticks']+=1
                    assert snapshot()==proof['snapshot'],phase
                    if phase=='idle':
                        assert not settings.operation_busy
                        assert not bpy.ops.lichtfeld.create_rig.poll()
                        if proof['render_ticks']==1:
                            try:bpy.ops.lichtfeld.create_rig()
                            except RuntimeError:pass
                            else:raise AssertionError('Rig started during native render')
                    elif phase=='precommit' and proof['render_ticks']==2:
                        window.scene=other
                    return .01
                assert proof['render_ticks']>=3,proof
                proof[phase+'_ticks']=proof['render_ticks']
                proof['phase']='start_update' if phase=='idle' else 'wait_cancel' if phase=='precommit' else 'wait_finish'
            elif phase=='start_update':
                proof['baseline']=snapshot()
                update();proof['phase']='build_precommit'
            elif phase=='build_precommit':
                staging=next((c for c in scene.collection.children if c!=rig and c.name.startswith('LichtFeld Capture Cameras')),None)
                if staging and len(staging.objects)>8:
                    render('precommit')
                else:assert settings.operation_busy,'Missed precommit boundary'
            elif phase=='wait_cancel':
                if not settings.operation_busy:
                    window.scene=scene
                    assert snapshot()==proof.pop('baseline')
                    update();proof['phase']='build_postcommit'
            elif phase=='build_postcommit':
                count=sum(o.type=='CAMERA' for o in rig.objects)
                if count>48:
                    assert settings.operation_busy,'Missed postcommit boundary'
                    render('postcommit',lock='--locked-render' in sys.argv)
            elif phase=='wait_finish' and not settings.operation_busy:
                assert sum(o.type=='CAMERA' for o in rig.objects)==1000
                assert set(original_ids)<={o.as_pointer() for o in rig.objects}
                assert not any(c!=rig and c.name.startswith('LichtFeld Capture Cameras') for c in scene.collection.children)
                finish(True)
                return None
            return .01
        except Exception:
            proof['error']=traceback.format_exc()
            finish(False)
            return None

    render('idle')
    bpy.app.timers.register(tick,first_interval=.01)


bpy.app.timers.register(render_interleave if '--render-interleave' in sys.argv else start, first_interval=1)
