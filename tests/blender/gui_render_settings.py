# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated GUI: visible native settings windows, ownership and async devices."""
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
main = bpy.context.window
view = next(a for a in main.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in view.regions if r.type == 'WINDOW')
properties = next(a for a in main.screen.areas if a.type == 'PROPERTIES')
scene = main.scene
s = scene.lichtfeld_training
s.scope = 'SCENE'
s.views = 4
s.dataset_mode = 'EXPORT'
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 2
scene.render.resolution_x = 160
scene.render.resolution_y = 96
scene.render.resolution_percentage = 100
owner = SimpleNamespace(collection=None, rig_backup=[], committed=False, report=lambda *args:None)
for _ in ext.LF_OT_rig.generate(owner, bpy.context):
    pass
view.spaces.active.show_region_ui = True
bpy.context.preferences.view.show_splash = False
phase = 0
proof = {}
properties.spaces.active.context = 'RENDER'
screens_after_first_open = None
deadline = time.monotonic()+50
heartbeat = {'ticks':0,'max_gap_ms':0,'probe_ticks':0,'probe_max_gap_ms':0}
last = time.monotonic()


def finish(ok, error=''):
    proof.update(ok=ok, error=error, heartbeat=heartbeat)
    (output/'result.json').write_text(json.dumps(proof), encoding='utf-8')
    print('GUI_OFFICIAL_RENDER_SETTINGS_PASS' if ok else 'GUI_OFFICIAL_RENDER_SETTINGS_FAILED',json.dumps(proof),flush=True)
    bpy.ops.wm.quit_blender()
    return None


def check_window(section, source=scene):
    extras = [w for w in bpy.context.window_manager.windows if w != main]
    assert len(extras) == 1, len(extras)
    window = extras[0]
    assert window == ext.scene_render.settings_window(bpy.context.window_manager)
    editor = next(a for a in window.screen.areas if a.type == 'PROPERTIES')
    assert editor.spaces.active.context == section
    assert editor.spaces.active.pin_id == source
    assert editor.spaces.active.use_pin_id
    if phase == 5:
        with bpy.context.temp_override(window=window,area=editor):
            assert bpy.context.scene == source
    assert view.type == 'VIEW_3D'
    assert properties.spaces.active.context == 'RENDER'
    return window, editor


def tick():
    global phase, last, screens_after_first_open
    try:
        now = time.monotonic()
        heartbeat['ticks'] += 1
        heartbeat['max_gap_ms'] = max(heartbeat['max_gap_ms'], (now-last)*1000)
        if phase == 8:
            heartbeat['probe_ticks'] += 1
            heartbeat['probe_max_gap_ms'] = max(heartbeat['probe_max_gap_ms'], (now-last)*1000)
        last = now
        assert now < deadline, phase
        with bpy.context.temp_override(window=main,area=view,region=region):
            if phase == 0:
                for r in view.regions:
                    if r.type == 'UI':
                        with bpy.context.temp_override(region=r):
                            r.active_panel_category = 'LichtFeld'
                assert bpy.ops.lichtfeld.render_settings(section='RENDER') == {'FINISHED'}
                check_window('RENDER')
                screens_after_first_open = len(bpy.data.screens)
                proof['already_visible_render_tab'] = True
                phase = 1
            elif phase == 1:
                window, editor = check_window('RENDER')
                with bpy.context.temp_override(window=window,area=editor):
                    bpy.ops.screen.screenshot(filepath=str(output/'01-native-render.png'))
                assert bpy.ops.lichtfeld.render_settings(section='OUTPUT') == {'FINISHED'}
                check_window('OUTPUT')
                assert len(bpy.data.screens) == screens_after_first_open, 'Settings screens accumulated'
                phase = 2
            elif phase == 2:
                window, editor = check_window('OUTPUT')
                with bpy.context.temp_override(window=window,area=editor):
                    bpy.ops.screen.screenshot(filepath=str(output/'02-native-output.png'))
                    assert bpy.ops.lichtfeld.render_settings(section='RENDER') == {'FINISHED'}
                assert check_window('RENDER')[0] == window
                for _ in range(4):
                    assert bpy.ops.lichtfeld.render_settings(section='OUTPUT') == {'FINISHED'}
                    check_window('OUTPUT')
                    assert len(bpy.data.screens) == screens_after_first_open
                proof['repeat_clicks_no_window_or_screen_leak'] = True
                phase = 3
            elif phase == 3:
                window, _ = check_window('OUTPUT')
                with bpy.context.temp_override(window=window):
                    bpy.ops.wm.window_close()
                properties.type = 'TEXT_EDITOR'
                assert bpy.ops.lichtfeld.render_settings(section='RENDER') == {'FINISHED'}
                extras = [w for w in bpy.context.window_manager.windows if w != main]
                assert len(extras) == 1
                assert extras[0].screen.areas[0].type == 'PROPERTIES'
                ext.scene_render.close_settings_window()
                assert len(bpy.context.window_manager.windows) == 1
                properties.type = 'PROPERTIES'
                properties.spaces.active.context = 'RENDER'
                proof['manual_close_and_no_properties_editor'] = True
                phase = 4
            elif phase == 4:
                preview = bpy.data.scenes.new('Framing source test')
                preview[ext.capture_preview.SOURCE] = scene
                preview['lichtfeld_capture_preview'] = True
                main.scene = preview
                assert bpy.ops.lichtfeld.render_settings(section='RENDER') == {'FINISHED'}
                check_window('RENDER', source=scene)
                assert main.scene == preview
                phase = 5
            elif phase == 5:
                check_window('RENDER', source=scene)
                preview = main.scene
                ext.scene_render.clear()
                assert len(bpy.context.window_manager.windows) == 1
                main.scene = scene
                bpy.data.scenes.remove(preview)
                proof['framing_source_and_cleanup'] = True
                assert bpy.ops.lichtfeld.render_devices() == {'FINISHED'}
                assert bpy.context.preferences.active_section == 'SYSTEM'
                phase = 6
            elif phase == 6:
                prefs = [w for w in bpy.context.window_manager.windows if w != main]
                assert len(prefs) == 1, 'Official preferences window did not open'
                with bpy.context.temp_override(window=prefs[0]):
                    bpy.ops.screen.screenshot(filepath=str(output/'03-native-devices.png'))
                # Simulate native address reuse after our window was closed.
                ext.scene_render._settings_window = prefs[0].as_pointer()
                ext.scene_render._settings_token = 'retired-settings-window'
                ext.scene_render.close_settings_window()
                assert prefs[0] in list(bpy.context.window_manager.windows)
                proof['stale_window_address_preserves_unowned_window'] = True
                assert bpy.ops.lichtfeld.render_settings(section='RENDER') == {'FINISHED'}
                assert len(bpy.context.window_manager.windows) == 3
                ext.scene_render.close_settings_window()
                assert prefs[0] in list(bpy.context.window_manager.windows), 'Unowned preferences window was closed'
                with bpy.context.temp_override(window=prefs[0]):
                    bpy.ops.wm.window_close()
                proof['unowned_windows_preserved'] = True
                phase = 7
            elif phase == 7:
                # The probe is deliberately performed outside the UI process.
                scene.render.engine = 'BLENDER_EEVEE'
                ext.scene_render.tick(scene, force=True)
                phase = 8
            elif phase == 8:
                ext.ui_runtime.tick()
                ready = ext.scene_render.readiness(scene)
                if ready.get('checking'):
                    return .02
                assert ready['ok'], ready
                assert ready['effective']['devices']
                assert heartbeat['ticks'] > 10
                assert heartbeat['probe_ticks'] > 5 and heartbeat['probe_max_gap_ms'] < 500, heartbeat
                bpy.ops.screen.screenshot(filepath=str(output/'04-verified-eevee.png'))
                proof.update(render=True, output=True, devices=True,
                             effective=ready['effective'])
                return finish(True)
        return .3 if phase < 8 else .02
    except Exception:
        return finish(False, traceback.format_exc())

bpy.app.timers.register(tick, first_interval=1)
