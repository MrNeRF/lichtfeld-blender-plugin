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
    if '--position-variation' in sys.argv or '--rotation-variation' in sys.argv:
        bpy.context.scene.lichtfeld_training.rig_position_variation = 5
        bpy.context.scene.lichtfeld_training.rig_variation_seed = 73
    if '--rotation-variation' in sys.argv:
        bpy.context.scene.lichtfeld_training.rig_rotation_variation=.03
    if '--panorama' in sys.argv:
        settings=bpy.context.scene.lichtfeld_training
        settings.rig_mode='PATH';settings.rig_orientation='PANORAMA';settings.views=125
        settings.rig_station_shape='GRID' if '--ordered' in sys.argv else 'SPHERE' if '--spherical' in sys.argv else 'BAND'
        settings.rig_station_views=4 if '--ordered' in sys.argv else 8;settings.rig_station_sweep=4.71238898038469;settings.rig_path_link=True
        assert bpy.ops.lichtfeld.create_path_guide(shape='ORBIT',radius=4)=={'FINISHED'}
        settings.rig_station_frame='PATH' if '--path-facing' in sys.argv else 'TARGET' if '--target-facing' in sys.argv else 'GUIDE'
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
        if '--position-variation' in sys.argv or '--rotation-variation' in sys.argv:
            assert settings.rig_position_variation == 5 and settings.rig_variation_seed == 73
        assert settings.rig_reference.objects[0].object is not None
        assert settings.camera_collection.lichtfeld_rig_reference.objects[0].object == settings.rig_reference.objects[0].object
        if '--position-variation' in sys.argv or '--rotation-variation' in sys.argv or '--panorama' in sys.argv:
            path=str(output.with_suffix('.blend'))
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path,use_scripts=False)
            settings=bpy.context.scene.lichtfeld_training
            if '--panorama' in sys.argv:
                assert settings.rig_mode=='PATH' and settings.rig_orientation=='PANORAMA'
                assert settings.views==125 and settings.rig_station_views==(4 if '--ordered' in sys.argv else 8)
                assert settings.rig_station_shape==('GRID' if '--ordered' in sys.argv else 'SPHERE' if '--spherical' in sys.argv else 'BAND')
                assert abs(settings.rig_station_sweep-(6.283185307179586 if '--spherical' in sys.argv else 4.71238898038469))<1e-6
                for camera in settings.camera_collection.objects:
                    if camera.type=='CAMERA':
                        assert camera.constraints.get(extension.rig_edit.PATH_LINK).target==settings.rig_curve
                        if '--target-facing' in sys.argv:
                            assert camera.constraints.get(extension.rig_edit.AIM).target==settings.rig_aim
                            assert camera.constraints.get(extension.rig_edit.ROTATION).owner_space==('LOCAL' if '--spherical' in sys.argv or '--ordered' in sys.argv else 'WORLD')
                        else:
                            assert camera.constraints.get(extension.rig_edit.AIM) is None
                assert settings.rig_station_frame==('PATH' if '--path-facing' in sys.argv else 'TARGET' if '--target-facing' in sys.argv else 'GUIDE')
                state['panorama_save_reload']=True
            else:
                assert settings.rig_position_variation==5 and settings.rig_variation_seed==73
            assert sum(o.type=='CAMERA' for o in settings.camera_collection.objects)==1000
            if '--rotation-variation' in sys.argv:
                assert abs(settings.rig_rotation_variation-.03)<1e-6
                for camera in settings.camera_collection.objects:
                    if camera.type=='CAMERA':
                        assert camera.constraints.get(extension.rig_edit.ROTATION).target==settings.rig_aim
            state['variation_save_reload']=True
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
    bpy.context.window_manager.lichtfeld_panels.rig_generator = True
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
                    extension.preflight._work = (extension.preflight.cache_key(bpy.context), work)
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
                    shot('live-gesture-finished.png')
                    assert bpy.ops.lichtfeld.live_rig() == {'RUNNING_MODAL'}
                    proof['phase'] = 'finish'
                    return .02
                proof['step'] += 1
                return .3
            if proof['phase'] == 'finish':
                assert extension.rig_edit.live_root(settings) is None
                with bpy.context.temp_override(area=area): assert bpy.ops.ed.undo() == {'FINISHED'}
                settings = bpy.context.scene.lichtfeld_training
                assert extension.rig_edit.live_root(settings)
                assert changed(control(settings), proof['committed']) < 1e-5
                with bpy.context.temp_override(area=area): assert bpy.ops.ed.redo() == {'FINISHED'}
                assert extension.rig_edit.live_root(bpy.context.scene.lichtfeld_training) is None
                proof['finish_undo_redo'] = True
                with bpy.context.temp_override(area=area):
                    assert bpy.ops.lichtfeld.capture_framing() == {'FINISHED'}
                proof['phase'] = 'framing_open'
                return .02
            if proof['phase'] == 'framing_open':
                preview = bpy.context.scene
                if not extension.capture_preview.is_preview(preview): return .02
                source = extension.capture_preview.source_scene(preview)
                if source.lichtfeld_training.operation_busy: return .02
                bpy.ops.ed.undo_push(message='Framing baseline')
                source.lichtfeld_training.capture_preview_index = 2
                bpy.ops.ed.undo_push(message='Framing index')
                # Keep the real framing iterator suspended across native Undo.
                action = extension.ui_runtime._actions[-1][0]
                for _ in range(8): next(action)
                with bpy.context.temp_override(area=area): assert bpy.ops.ed.undo() == {'FINISHED'}
                proof['phase'] = 'framing_undo'
                return .02
            if proof['phase'] in {'framing_undo', 'framing_redo'}:
                preview = bpy.context.scene
                if extension.capture_preview.camera_pending(preview): return .02
                source = extension.capture_preview.source_scene(preview)
                assert source
                index = 1 if proof['phase'] == 'framing_undo' else 2
                assert source.lichtfeld_training.capture_preview_index == index
                assert preview['capture_camera_name'] == f'Capture {index:03d}'
                if proof['phase'] == 'framing_undo':
                    with bpy.context.temp_override(area=area): assert bpy.ops.ed.redo() == {'FINISHED'}
                    proof['phase'] = 'framing_redo'
                    return .02
                proof['framing_undo_redo_resync'] = True
                proof['ok'] = True
                output.write_text(json.dumps(proof), encoding='utf-8')
                bpy.ops.wm.quit_blender(); return None
        except Exception:
            proof['error'] = traceback.format_exc()
            output.write_text(json.dumps(proof), encoding='utf-8')
            bpy.ops.wm.quit_blender()
            return None
    bpy.app.timers.register(tick, first_interval=.02)


def history_interleave():
    """Undo during real modal entry/finish must not resume freed RNA."""
    scene = bpy.context.scene
    s = scene.lichtfeld_training
    s.scope='SCENE';s.views=1000
    owner=SimpleNamespace(collection=None,rig_backup=[],committed=False,report=lambda *args:None)
    for _ in extension.LF_OT_rig.generate(owner,bpy.context):pass
    bpy.ops.ed.undo_push(message='Applied rig baseline')
    proof={'phase':'entry','ticks':0}
    deadline=time.monotonic()+120
    assert bpy.ops.lichtfeld.live_rig()=={'RUNNING_MODAL'}

    def active():
        return next((op for op in bpy.context.window.modal_operators if isinstance(op,extension._RigOperation)),None)

    def tick():
        try:
            proof['ticks']+=1
            assert time.monotonic()<deadline,proof
            op=active();s=bpy.context.scene.lichtfeld_training
            if proof['phase'] in {'entry','finish'}:
                threshold = 500 if '--disable-interleave' in sys.argv else 5
                if op and len(getattr(op,'live_backup',()))>threshold and not op.committed:
                    if '--disable-interleave' in sys.argv:
                        phase=proof['phase']
                        started=time.perf_counter()
                        bpy.ops.preferences.addon_disable(module=module)
                        proof[phase+'_disable_ms']=(time.perf_counter()-started)*1000
                        assert not any(isinstance(item,extension._RigOperation) for item in bpy.context.window.modal_operators)
                        bpy.ops.preferences.addon_enable(module=module)
                        s=bpy.context.scene.lichtfeld_training
                        assert not s.operation_busy
                        if phase=='entry':
                            assert extension.rig_edit.live_root(s) is None
                            assert not any(o.get(extension.rig_edit.LIVE) is True for o in bpy.data.objects)
                            assert bpy.ops.lichtfeld.live_rig()=={'RUNNING_MODAL'}
                            proof['phase']='wait-live'
                        else:
                            assert extension.rig_edit.live_root(s)
                            proof['ok']=True
                            output.write_text(json.dumps(proof),encoding='utf-8')
                            bpy.ops.wm.quit_blender();return None
                        return .02
                    with bpy.context.temp_override(area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')):
                        assert bpy.ops.ed.undo()=={'FINISHED'}
                    assert op.history_cancelled
                    proof['phase']+='-undone'
            elif proof['phase'].endswith('-undone'):
                if op:return .02
                assert not s.operation_busy
                assert not any(o.get(extension.rig_edit.LIVE) is True for o in bpy.data.objects)
                with bpy.context.temp_override(area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')):
                    assert bpy.ops.ed.redo()=={'FINISHED'}
                if proof['phase']=='entry-undone':
                    assert bpy.ops.lichtfeld.live_rig()=={'RUNNING_MODAL'}
                    proof['phase']='wait-live'
                else:
                    assert extension.rig_edit.live_root(bpy.context.scene.lichtfeld_training)
                    proof['ok']=True
                    output.write_text(json.dumps(proof),encoding='utf-8')
                    bpy.ops.wm.quit_blender();return None
            elif proof['phase']=='wait-live' and op is None and not s.operation_busy:
                assert extension.rig_edit.live_root(s)
                assert bpy.ops.lichtfeld.live_rig()=={'RUNNING_MODAL'}
                proof['phase']='finish'
            return .02
        except Exception:
            proof['error']=traceback.format_exc()
            output.write_text(json.dumps(proof),encoding='utf-8')
            bpy.ops.wm.quit_blender();return None
    bpy.app.timers.register(tick,first_interval=.02)


def path_guides():
    """Guide Undo and spacing cancellation use Blender's actual event loop."""
    proof = {'phase': 'guide'}
    try:
        bpy.context.scene.lichtfeld_training.rig_mode = 'PATH'
        bpy.ops.ed.undo_push(message='Before path guide')
        assert bpy.ops.lichtfeld.create_path_guide(shape='ORBIT', radius=4) == {'FINISHED'}
        name = bpy.context.scene.lichtfeld_training.rig_curve.name
        # A Python timer does not create the UI event's automatic undo boundary.
        bpy.ops.ed.undo_push(message='Create path guide')
        assert bpy.ops.ed.undo() == {'FINISHED'}
        assert bpy.data.objects.get(name) is None
        assert bpy.context.scene.lichtfeld_training.rig_curve is None
        assert bpy.ops.ed.redo() == {'FINISHED'}
        assert bpy.context.scene.lichtfeld_training.rig_curve.name == name
        s = bpy.context.scene.lichtfeld_training
        s.scope = 'SCENE'; s.rig_path_sampling = 'SPACING'; s.rig_path_spacing = .03
        s.rig_path_link = True
        assert bpy.ops.lichtfeld.create_rig() == {'RUNNING_MODAL'}
    except Exception:
        output.write_text(traceback.format_exc(), encoding='utf-8')
        bpy.ops.wm.quit_blender(); return None
    deadline = time.monotonic() + 60

    def tick():
        try:
            assert time.monotonic() < deadline
            s = bpy.context.scene.lichtfeld_training
            op = next((op for op in bpy.context.window.modal_operators if isinstance(op, extension.LF_OT_rig)), None)
            if proof['phase'] == 'guide' and op:
                # Cancel during preparation through the operator's normal path.
                op.cancel_requested = True
                proof['phase'] = 'cancel'
            elif proof['phase'] == 'cancel' and not s.operation_busy:
                assert s.camera_collection is None
                assert s.rig_curve and not any(c.get('lichtfeld_generated') for c in bpy.data.collections)
                s.rig_path_spacing = 1
                assert bpy.ops.lichtfeld.create_rig() == {'RUNNING_MODAL'}
                proof['phase'] = 'apply'
            elif proof['phase'] == 'apply' and not s.operation_busy:
                assert s.camera_collection and sum(o.type == 'CAMERA' for o in s.camera_collection.objects) == 26
                assert bpy.ops.lichtfeld.create_path_guide.poll()
                def first_position():
                    bpy.context.view_layer.update()
                    current=bpy.context.scene.lichtfeld_training
                    camera=next(o for o in current.camera_collection.objects if o.get('lichtfeld_camera_index')==0)
                    assert camera.constraints.get(extension.rig_edit.PATH_LINK).target==current.rig_curve
                    return camera.matrix_world.translation.copy()
                before=first_position()
                bpy.ops.ed.undo_push(message='Linked path rig')
                s.rig_curve.location.x+=1
                assert abs(first_position().x-before.x-1)<1e-4
                bpy.ops.ed.undo_push(message='Move linked guide')
                assert bpy.ops.ed.undo()=={'FINISHED'}
                assert (first_position()-before).length<1e-4
                assert bpy.ops.ed.redo()=={'FINISHED'}
                moved=first_position()
                assert abs(moved.x-before.x-1)<1e-4
                path=str(output.with_suffix('.blend'))
                bpy.ops.wm.save_as_mainfile(filepath=path)
                bpy.ops.wm.open_mainfile(filepath=path)
                assert (first_position()-moved).length<1e-4
                proof['linked_transform_undo_redo_save_reload']=True
                proof['ok'] = True
                output.write_text(json.dumps(proof), encoding='utf-8')
                print('PATH_GUIDE_NATIVE_UNDO_REDO_MODAL_CANCEL_APPLY_PASS', flush=True)
                bpy.ops.wm.quit_blender(); return None
            return .02
        except Exception:
            output.write_text(traceback.format_exc(), encoding='utf-8')
            bpy.ops.wm.quit_blender(); return None
    bpy.app.timers.register(tick, first_interval=.02)


def pose_checks():
    """Read-only modal scan at capture scale, including native lifecycle changes."""
    collection=bpy.data.collections.new('Pose check scale')
    bpy.context.scene.collection.children.link(collection)
    data=bpy.data.cameras.new('Pose check optics')
    cameras=[]
    for index in range(10000):
        camera=bpy.data.objects.new(f'Check {index:05d}',data)
        collection.objects.link(camera);camera.location=(index%100,index//100,2)
        cameras.append(camera)
    cameras[-2].location=cameras[0].location
    cameras[-1].data=data.copy();cameras[-1].data.type='ORTHO'
    bpy.context.scene.lichtfeld_training.camera_collection=collection
    path=str(output.with_suffix('.blend'))
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.ed.undo_push(message='Pose check saved baseline')
    proof={'phase':'first','results':[],'ticks':0,'max_gap_ms':0}
    from unittest.mock import patch
    original_show=extension.rig_analysis.show_result
    def shown(context,result):
        proof['results'].append(result)
        original_show(context,result)
    extension.rig_analysis.show_result=shown
    selection=(bpy.context.view_layer.objects.active,tuple(bpy.context.selected_objects))
    identities=tuple(obj.as_pointer() for obj in bpy.data.objects)
    def begin():
        proof['started']=proof['last']=time.perf_counter()
        assert bpy.ops.lichtfeld.check_camera_poses()=={'RUNNING_MODAL'}
    def finish():
        proof['ok']=True
        extension.rig_analysis.show_result=original_show
        output.write_text(json.dumps(proof),encoding='utf-8')
        print('POSE_CHECK_NATIVE_READ_ONLY_10000_CANCEL_UNDO_LOAD_UNREGISTER_PASS',proof,flush=True)
        bpy.ops.wm.quit_blender()
    def after_load():
        try:
            assert extension.rig_analysis._active is None
            assert bpy.ops.lichtfeld.check_camera_poses()=={'RUNNING_MODAL'}
            bpy.ops.preferences.addon_disable(module=module)
            assert extension.rig_analysis._active is None
            proof['unregister']=True
            finish()
        except Exception:
            output.write_text(traceback.format_exc(),encoding='utf-8');bpy.ops.wm.quit_blender()
        return None
    def tick():
        try:
            now=time.perf_counter();proof['ticks']+=1
            proof['max_gap_ms']=max(proof['max_gap_ms'],(now-proof['last'])*1000);proof['last']=now
            assert now-proof['started']<60
            if extension.rig_analysis._active is not None:return .01
            assert not bpy.data.is_dirty
            assert identities==tuple(obj.as_pointer() for obj in bpy.data.objects)
            assert selection==(bpy.context.view_layer.objects.active,tuple(bpy.context.selected_objects))
            if proof['phase'] in {'first','repeat'}:
                result=proof['results'].pop()
                assert result['camera_count']==10000 and len(result['matches'])==1
                assert len(result['camera_issues'])==1
                snapshot=extension.rig_analysis.last_result(bpy.context)
                assert snapshot is not None and len(snapshot['repeats'])==1 and 'poses' not in snapshot
                reopened=time.perf_counter()
                with patch.object(extension.rig_analysis,'analyze_steps',side_effect=AssertionError('Reopen started a second scan')):
                    assert bpy.ops.lichtfeld.camera_report('INVOKE_DEFAULT')=={'RUNNING_MODAL'}
                assert extension.rig_analysis._active is None
                assert not bpy.data.is_dirty
                proof[proof['phase']+'_reopen_seconds']=time.perf_counter()-reopened
                proof[proof['phase']+'_seconds']=now-proof['started']
                print('POSE_CHECK_TIMING',proof['phase'],now-proof['started'],proof['max_gap_ms'],flush=True)
                if proof['phase']=='first':proof['phase']='repeat';begin();return .01
                proof['phase']='cancel'
                begin()
                owner=extension.rig_analysis._active
                assert owner.modal(bpy.context,SimpleNamespace(type='ESC'))=={'CANCELLED'}
                proof['escape']=True
                return .02
            bpy.ops.ed.undo_push(message='Before read-only pose check')
            begin()
            assert bpy.ops.ed.undo()=={'FINISHED'}
            assert extension.rig_analysis._active is None and extension.rig_analysis.last_result(bpy.context) is None
            proof['undo']=True
            begin()
            bpy.ops.wm.open_mainfile(filepath=path,use_scripts=False)
            assert extension.rig_analysis._active is None and extension.rig_analysis.last_result(bpy.context) is None
            proof['load']=True
            bpy.app.timers.register(after_load,first_interval=.1)
            return None
        except Exception:
            output.write_text(traceback.format_exc(),encoding='utf-8');bpy.ops.wm.quit_blender();return None
    begin()
    bpy.app.timers.register(tick,first_interval=.01)


bpy.app.timers.register(pose_checks if '--pose-checks' in sys.argv else
    path_guides if '--path-guides' in sys.argv else
    history_interleave if '--history-interleave' in sys.argv else
    live_controls if '--live' in sys.argv else
    render_interleave if '--render-interleave' in sys.argv else start, first_interval=1)
