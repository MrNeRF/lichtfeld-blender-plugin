# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Official Blender render settings, asynchronous readiness and frozen requests."""
import json
import time
import bpy
from . import render_devices, ui_runtime

_cache = {}
_pending = set()
_generation = 0
_settings_window = 0
_settings_token = ''
_SETTINGS_TAG = 'lichtfeld_capture_settings_window'


def device_request(scene):
    engine = scene.render.engine
    if engine == 'BLENDER_EEVEE':
        system = bpy.context.preferences.system
        return render_devices.validate({'engine': engine, 'mode': 'GRAPHICS',
            'backend': system.gpu_backend, 'graphics_device': system.gpu_preferred_device})
    if engine != 'CYCLES':
        raise ValueError('Choose Cycles or EEVEE in Blender Render settings')
    prefs = bpy.context.preferences.addons['cycles'].preferences
    backend = prefs.compute_device_type or 'NONE'
    preferences = {'backend': backend,
        'devices': [{'id': d.id, 'name': d.name, 'type': d.type} for d in prefs.devices
                    if d.use and d.type in {backend, 'CPU'}],
        'options': {name: getattr(prefs, name) for name in render_devices.OPTIONS if hasattr(prefs, name)}}
    if backend == 'NONE':
        preferences['devices'] = []
    if scene.cycles.device == 'CPU':
        return render_devices.validate({'engine': engine, 'mode': 'CPU', 'backend': 'CPU',
            'devices': [], 'preferences': preferences,
            'preferences_required': bool(scene.cycles.use_denoising and
                                         (scene.cycles.denoising_use_gpu or scene.cycles.denoiser == 'OPTIX'))})
    return render_devices.validate({'engine': engine, 'mode': 'GPU', **preferences})


def key_for(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def snapshot(scene):
    request = device_request(scene)
    cycles = scene.render.engine == 'CYCLES'
    return {'schema': 1, 'engine': scene.render.engine,
        'samples': scene.cycles.samples if cycles else scene.eevee.taa_render_samples,
        'device': scene.cycles.device if cycles else 'GPU',
        'adaptive_sampling': bool(scene.cycles.use_adaptive_sampling) if cycles else False,
        'denoising': bool(scene.cycles.use_denoising) if cycles else False,
        'denoiser': scene.cycles.denoiser if cycles else '',
        'denoising_use_gpu': bool(scene.cycles.denoising_use_gpu) if cycles else False,
        'persistent_data': bool(scene.render.use_persistent_data),
        'request': request}


def signature(scene):
    try:
        return key_for(snapshot(scene))
    except (ValueError, AttributeError) as error:
        return str(error)


def readiness(scene):
    try:
        request = device_request(scene)
    except ValueError as error:
        return {'ok': False, 'message': str(error)}
    if request['mode'] == 'CPU':
        return _cache.get(key_for(request), {'ok': True, 'effective': {'engine': 'CYCLES', 'backend': 'CPU', 'devices': [{'name': 'CPU rendering · verifying processor'}]}})
    return _cache.get(key_for(request), {'ok': False, 'checking': True, 'message': 'Checking render devices…'})


def tick(scene, force=False):
    try:
        request = device_request(scene)
    except ValueError:
        return
    key = key_for(request)
    cached = _cache.get(key)
    if key in _pending or cached and not force and (cached['ok'] or time.monotonic() < cached.get('retry', 0)):
        return
    _pending.add(key)
    generation = _generation
    binary = bpy.app.binary_path
    def done(effective):
        if generation == _generation:
            _pending.discard(key)
            _cache[key] = {'ok': True, 'effective': effective}
    def failed(error):
        if generation == _generation:
            _pending.discard(key)
            _cache[key] = {'ok': False, 'message': str(error), 'retry': time.monotonic() + 30}
    ui_runtime.submit(lambda: render_devices.probe(binary, request), done, failed)


def clear():
    global _generation
    _generation += 1
    _pending.clear()
    _cache.clear()
    close_settings_window()


def settings_window(manager):
    # Native window addresses may be reused after a manual close. A screen
    # token prevents mistaking an unrelated new window for our old one.
    if not _settings_token or manager is None:
        return None
    return next((window for window in manager.windows
                 if window.as_pointer() == _settings_window and window.screen.get(_SETTINGS_TAG) == _settings_token), None)


def close_settings_window():
    global _settings_window, _settings_token
    window = settings_window(bpy.context.window_manager)
    _settings_window = 0
    _settings_token = ''
    if window is not None:
        with bpy.context.temp_override(window=window):
            bpy.ops.wm.window_close()


def sample_get(settings):
    scene = settings.id_data
    return scene.cycles.samples if scene.render.engine == 'CYCLES' else scene.eevee.taa_render_samples


def sample_set(settings, value):
    scene = settings.id_data
    if scene.render.engine == 'CYCLES':
        scene.cycles.samples = value
    elif scene.render.engine == 'BLENDER_EEVEE':
        scene.eevee.taa_render_samples = value


def engine_get(settings):
    return {'CYCLES': 0, 'BLENDER_EEVEE': 1}.get(settings.id_data.render.engine, 2)


def engine_set(settings, value):
    if value in (0, 1):
        settings.id_data.render.engine = ('CYCLES', 'BLENDER_EEVEE')[value]


def device_get(settings):
    return int(settings.id_data.cycles.device == 'GPU')


def device_set(settings, value):
    settings.id_data.cycles.device = 'GPU' if value else 'CPU'


def summary_lines(value, effective=None):
    if not isinstance(value, dict) or value.get('engine') not in ('CYCLES', 'BLENDER_EEVEE'):
        return ('Render configuration is being read…', 'Device: waiting for renderer', 'Settings are fixed for this job')
    engine = 'Cycles' if value.get('engine') == 'CYCLES' else 'EEVEE'
    request = value.get('request') if isinstance(value.get('request'), dict) else {}
    resolved = effective if isinstance(effective, dict) else request
    backend = resolved['backend'][:32] if isinstance(resolved.get('backend'), str) else 'Pending'
    backend = {'OPTIX':'OptiX', 'VULKAN':'Vulkan', 'OPENGL':'OpenGL', 'METAL':'Metal', 'ONEAPI':'oneAPI'}.get(backend, backend)
    devices = resolved.get('devices') if isinstance(resolved.get('devices'), list) else []
    names = ', '.join(d['name'][:128] for d in devices[:64] if isinstance(d, dict) and isinstance(d.get('name'), str))[:256]
    if not names:
        names = 'CPU' if request.get('mode') == 'CPU' else 'Automatic graphics GPU · checking' if request.get('mode') == 'GRAPHICS' else 'Choose render devices'
    samples = value.get('samples') if type(value.get('samples')) is int and 0 < value['samples'] <= 2147483647 else 0
    options = f"{samples:,} max samples"
    if value.get('adaptive_sampling'):
        options += ' · adaptive'
    lines = (f'{engine} · {backend}', names, options)
    if value.get('denoising'):
        denoiser = 'OptiX' if value.get('denoiser') == 'OPTIX' else 'OpenImageDenoise'
        device = 'GPU enabled' if value.get('denoising_use_gpu') or value.get('denoiser') == 'OPTIX' else 'CPU'
        lines += (f'Denoising: {denoiser} · {device}',)
    return lines


def draw(box, scene, wrapped):
    box.prop(scene.render, 'engine', text='Engine')
    try:
        value = snapshot(scene)
        ready = readiness(scene)
        lines = summary_lines(value, ready.get('effective'))
    except ValueError as error:
        ready = {'ok': False}
        lines = (str(error), 'Open Render settings or Render devices', 'Cycles and EEVEE are supported')
    # Fixed rows: asynchronous driver checks never move editable controls.
    for line in lines:
        box.label(text=line)
    box.label(text='RGB: Standard sRGB · no compositor')
    row = box.row(align=True)
    row.operator('lichtfeld.render_settings', text='Render settings…', icon='PREFERENCES').section = 'RENDER'
    row.operator('lichtfeld.render_devices', text='Render devices', icon='SETTINGS')
    if scene.render.engine == 'CYCLES':
        box.prop(scene.cycles, 'device', text='Device')
    box.prop(scene.lichtfeld_training, 'capture_advanced', text='Capture details',
             icon='TRIA_DOWN' if scene.lichtfeld_training.capture_advanced else 'TRIA_RIGHT', emboss=False)
    if scene.lichtfeld_training.capture_advanced:
        wrapped(box, 'Uses the saved scene render settings. Capture fixes PNG output, Standard sRGB, no scene compositing, no motion blur or depth of field, and no render border.')
        if not ready.get('ok'):
            wrapped(box, ready.get('message', 'Render device check is running'))


class LF_OT_render_settings(bpy.types.Operator):
    bl_idname = 'lichtfeld.render_settings'
    bl_label = 'Render settings'
    bl_description = 'Open a dedicated window with Blender’s official Render or Output Properties for the source scene; close it to return to capture setup'
    section: bpy.props.EnumProperty(items=[('RENDER', 'Render', 'Engine, sampling, denoising and light paths'),
                                           ('OUTPUT', 'Output', 'Scene resolution, scale and pixel aspect')])

    @classmethod
    def poll(cls, context):
        return context.window is not None and not bpy.app.background

    def execute(self, context):
        global _settings_window, _settings_token
        from . import capture_preview
        source = capture_preview.source_scene(context.scene) or context.scene
        window = settings_window(context.window_manager)
        if window != context.window:
            # Blender exposes no Python API to raise an existing arbitrary
            # window. Replace only our own window so every click visibly opens
            # native Properties in front, without accumulating extra windows.
            close_settings_window()
            before = {w.as_pointer() for w in context.window_manager.windows}
            bpy.ops.screen.area_dupli('INVOKE_DEFAULT')
            windows = [w for w in context.window_manager.windows if w.as_pointer() not in before]
            if not windows:
                self.report({'ERROR'}, 'Blender could not open a Properties window')
                return {'CANCELLED'}
            window = windows[-1]
            _settings_window = window.as_pointer()
            _settings_token = str(time.monotonic_ns())
            window.screen[_SETTINGS_TAG] = _settings_token
        area = max(window.screen.areas, key=lambda a: a.width * a.height)
        area.type = 'PROPERTIES'
        space = area.spaces.active
        # Pin the source explicitly, including when opened from framing preview.
        space.pin_id = source
        space.use_pin_id = True
        space.context = self.section
        area.tag_redraw()
        return {'FINISHED'}


class LF_OT_render_devices(bpy.types.Operator):
    bl_idname = 'lichtfeld.render_devices'
    bl_label = 'Render devices'
    bl_description = 'Open Blender System Preferences: select Cycles backend and devices, or EEVEE graphics backend and preferred GPU'

    @classmethod
    def poll(cls, context):
        return context.window is not None and not bpy.app.background

    def execute(self, context):
        context.preferences.active_section = 'SYSTEM'
        bpy.ops.screen.userpref_show('INVOKE_DEFAULT')
        return {'FINISHED'}


class LF_OT_apply_capture_format(bpy.types.Operator):
    bl_idname = 'lichtfeld.apply_capture_format'
    bl_label = 'Apply size preset'
    bl_description = 'Apply the chosen aspect and size to official Blender Output Properties, at 100% scale with square pixels'

    @classmethod
    def poll(cls, context):
        from . import capture_preview
        scene = capture_preview.source_scene(context.scene) or context.scene
        s = scene.lichtfeld_training
        return scene.is_editable and not s.running and not s.operation_busy and not s.attached_session

    def execute(self, context):
        from . import capture_settings, capture_preview
        scene = capture_preview.source_scene(context.scene) or context.scene
        try:
            capture_settings.apply_format(scene, capture_settings.preset_format(scene.lichtfeld_training, mode='PRESET'))
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


CLASSES = (LF_OT_render_settings, LF_OT_render_devices, LF_OT_apply_capture_format)
