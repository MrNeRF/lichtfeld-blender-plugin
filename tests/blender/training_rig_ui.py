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
bpy.context.preferences.view.show_splash = False
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
        # The completed modal transaction is one undo step, including its subject.
        assert settings.rig_reference.mode == 'OBJECTS' and settings.rig_reference.objects
        rig_name = settings.camera_collection.name
        assert bpy.ops.ed.undo() == {'FINISHED'}
        assert bpy.context.scene.lichtfeld_training.camera_collection is None
        assert bpy.context.scene.lichtfeld_training.rig_reference.mode == 'UNSET'
        assert bpy.ops.ed.redo() == {'FINISHED'}
        settings = bpy.context.scene.lichtfeld_training
        assert settings.camera_collection.name == rig_name
        assert settings.rig_reference.objects[0].object is not None
        assert settings.camera_collection.lichtfeld_rig_reference.objects[0].object == settings.rig_reference.objects[0].object
        state['reference_undo_redo'] = True
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
    scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=8192
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


def live_controls():
    """Exercise native widget gestures and measure evaluated, calibrated poses."""
    scene = bpy.context.scene
    settings = scene.lichtfeld_training
    settings.scope = 'SCENE'; settings.views = 1000 if '--large' in sys.argv else 48
    settings.rig_auto_fit = False; settings.rig_radius = 3
    settings.rig_generator = True
    scene.tool_settings.use_keyframe_insert_auto = True
    bpy.context.preferences.view.show_splash = False
    area = next(a for a in bpy.context.window.screen.areas if a.type == 'VIEW_3D')
    area.spaces.active.show_region_ui = True
    proof = {'phase':'create', 'ticks':0}
    deadline = time.perf_counter() + 120
    assert bpy.ops.lichtfeld.create_rig() == {'RUNNING_MODAL'}

    def control(settings):
        return tuple(settings.live_aim) if '--aim-gizmo' in sys.argv else settings.live_radius

    def changed(value, baseline):
        if isinstance(value, tuple):
            return sum((a-b)**2 for a,b in zip(value, baseline))**.5
        return abs(value-baseline)

    def shot(name):
        for editor in bpy.context.screen.areas: editor.tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        bpy.ops.screen.screenshot(filepath=str(output.parent / name))

    def tick():
        try:
            proof['ticks'] += 1
            assert time.perf_counter() < deadline, proof
            settings = bpy.context.scene.lichtfeld_training
            if settings.operation_busy: return .02
            if proof['phase'] == 'create':
                next(r for r in area.regions if r.type == 'UI').active_panel_category = 'LichtFeld'
                assert bpy.ops.lichtfeld.live_rig() == {'RUNNING_MODAL'}
                proof['phase'] = 'enter'
                return .02
            if proof['phase'] == 'enter':
                assert extension.rig_edit.live_root(settings)
                # Include evaluation and calibrated export, not just a heartbeat.
                times = []
                for i in range(12):
                    before = time.perf_counter()
                    settings.live_radius = 3 + .05*i
                    settings.live_phase = .01*i
                    bpy.context.view_layer.update()
                    graph = bpy.context.evaluated_depsgraph_get()
                    for camera in settings.camera_collection.objects:
                        if camera.type == 'CAMERA': extension.capture_settings.camera_pose(camera, graph)
                    times.append((time.perf_counter()-before)*1000)
                proof['max_evaluated_edit_ms'] = max(times)
                proof['mean_evaluated_edit_ms'] = sum(times)/len(times)
                settings.live_radius = 3; settings.live_phase = 0
                bpy.context.view_layer.update()
                if '--aim-gizmo' in sys.argv:
                    from bpy_extras.view3d_utils import location_3d_to_region_2d
                    from mathutils import Vector
                    settings.live_aim = (0, 0, 2)
                    region = next(r for r in area.regions if r.type == 'WINDOW')
                    point = location_3d_to_region_2d(region, area.spaces.active.region_3d, Vector(settings.live_aim))
                    proof['gizmo_xy'] = (round(region.x+point.x), round(region.y+point.y))
                proof['baseline'] = control(settings)
                bpy.ops.ed.undo_push(message='Live gesture baseline')
                shot('live-controls.png')
                proof['phase'] = 'gesture'; proof['step'] = 0
                return .5
            if proof['phase'] == 'gesture':
                if '--widget-y' not in sys.argv and '--aim-gizmo' not in sys.argv:
                    proof['ok'] = True
                    output.write_text(json.dumps(proof), encoding='utf-8')
                    bpy.ops.wm.quit_blender(); return None
                x, y = proof['gizmo_xy'] if '--aim-gizmo' in sys.argv else (1050, int(sys.argv[sys.argv.index('--widget-y')+1]))
                window = bpy.context.window
                step = proof['step']
                if step in {0, 4}:
                    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
                    window.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
                elif step in {1, 5}:
                    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x+5, y=y)
                    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x+70, y=y)
                elif step == 2:
                    proof['during'] = control(settings)
                    assert changed(control(settings), proof['baseline']) > .001, proof
                    window.event_simulate(type='ESC', value='PRESS')
                    window.event_simulate(type='ESC', value='RELEASE')
                    window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x+70, y=y)
                elif step == 3:
                    assert changed(control(settings), proof['baseline']) < 1e-5, proof
                elif step == 6:
                    window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x+70, y=y)
                elif step == 7:
                    proof['committed'] = control(settings)
                    assert changed(control(settings), proof['baseline']) > .001, proof
                    # Reproduce the native crash boundary deterministically:
                    # Undo must close a suspended all_objects RNA iterator.
                    extension.preflight.invalidate()
                    work = extension.preflight.collect(bpy.context)
                    while len(work.gi_frame.f_locals.get('cameras', ())) < 2:
                        next(work)
                    extension.preflight._work = (bpy.context.scene.as_pointer(), work)
                    with bpy.context.temp_override(area=area): bpy.ops.ed.undo()
                    assert extension.preflight._work is None
                elif step == 8:
                    assert changed(control(bpy.context.scene.lichtfeld_training), proof['baseline']) < 1e-5, proof
                    with bpy.context.temp_override(area=area): bpy.ops.ed.redo()
                elif step == 9:
                    settings = bpy.context.scene.lichtfeld_training
                    assert changed(control(settings), proof['committed']) < 1e-5, proof
                    root = extension.rig_edit.live_root(settings)
                    assert root.animation_data.action is None
                    assert settings.rig_aim.animation_data is None
                    assert bpy.context.scene.tool_settings.use_keyframe_insert_auto
                    proof['ok'] = True
                    output.write_text(json.dumps(proof), encoding='utf-8')
                    shot('live-gesture-finished.png')
                    bpy.ops.wm.quit_blender(); return None
                proof['step'] += 1
                return .3
        except Exception:
            proof['error'] = traceback.format_exc()
            output.write_text(json.dumps(proof), encoding='utf-8')
            bpy.ops.wm.quit_blender()
            return None
    bpy.app.timers.register(tick, first_interval=.02)


bpy.app.timers.register(live_controls if '--live' in sys.argv else
    render_interleave if '--render-interleave' in sys.argv else start, first_interval=1)
