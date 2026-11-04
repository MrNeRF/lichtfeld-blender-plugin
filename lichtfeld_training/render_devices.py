# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit device contracts; driver discovery runs only in disposable workers."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ENGINES = {'CYCLES', 'BLENDER_EEVEE'}
BACKENDS = {'CUDA', 'OPTIX', 'HIP', 'METAL', 'ONEAPI'}
MARKER = 'LICHTFELD_RENDER_DEVICE '
OPTIONS = {'peer_memory': bool, 'use_hiprt': bool, 'use_oneapirt': bool,
           'metalrt': {'OFF', 'ON', 'AUTO'},
           'kernel_optimization_level': {'OFF', 'INTERSECT', 'FULL'}}


def validate_preferences(value, require_gpu=True):
    if not isinstance(value, dict) or not isinstance(value.get('backend'), str) or value['backend'] not in BACKENDS | {'NONE'}:
        raise ValueError('Choose a Cycles backend and enable a GPU in Render devices')
    backend = value['backend']
    devices = value.get('devices')
    if not isinstance(devices, list) or len(devices) > 64:
        raise ValueError('Invalid selected Cycles devices')
    keys = set()
    for device in devices:
        if (not isinstance(device, dict) or not isinstance(device.get('type'), str) or device['type'] not in {backend, 'CPU'} or
                any(not isinstance(device.get(k), str) or not device[k] or len(device[k]) > 1024 or '\0' in device[k]
                    for k in ('id', 'name'))):
            raise ValueError('Invalid selected Cycles device')
        key = device['type'], device['id']
        if key in keys:
            raise ValueError('A Cycles device was selected more than once')
        keys.add(key)
    if require_gpu and (backend not in BACKENDS or not any(d['type'] == backend for d in devices)):
        raise ValueError('GPU Compute needs an enabled GPU; choose CPU in Render settings for CPU rendering')
    options = value.get('options', {})
    if not isinstance(options, dict) or set(options) - set(OPTIONS):
        raise ValueError('Unsupported Cycles device preference')
    for key, item in options.items():
        rule = OPTIONS[key]
        if (type(item) is not bool if rule is bool else not isinstance(item, str) or item not in rule):
            raise ValueError('Invalid Cycles device preference: ' + key)
    return value


def validate(value):
    if (not isinstance(value, dict) or not isinstance(value.get('engine'), str) or value['engine'] not in ENGINES):
        raise ValueError('Choose Cycles or EEVEE in Blender Render Properties')
    mode = value.get('mode')
    if value['engine'] == 'BLENDER_EEVEE':
        if mode != 'GRAPHICS' or not isinstance(value.get('backend'), str) or value['backend'] not in {'OPENGL', 'VULKAN', 'METAL'}:
            raise ValueError('Choose a supported graphics backend in Blender System Preferences')
        device = value.get('graphics_device', 'AUTO')
        if not isinstance(device, str) or not (device == 'AUTO' or re.fullmatch(r'[0-9a-fA-F]+/[0-9a-fA-F]+/[0-9a-fA-F]+', device)):
            raise ValueError('The selected graphics device identifier is not supported')
        if device != 'AUTO' and value['backend'] != 'VULKAN':
            raise ValueError('An explicit EEVEE GPU requires Vulkan; OpenGL and Metal use the system graphics device')
    elif mode == 'GPU':
        validate_preferences(value)
    elif mode != 'CPU' or value.get('backend') != 'CPU':
        raise ValueError('Choose CPU or GPU Compute in Blender Render Properties')
    elif 'preferences' in value:
        validate_preferences(value['preferences'], require_gpu=False)
        if 'preferences_required' in value and type(value['preferences_required']) is not bool:
            raise ValueError('Invalid denoising device requirement')
    return value


def arguments(value):
    """Graphics preferences do not survive --factory-startup; pass them explicitly."""
    validate(value)
    if value['engine'] != 'BLENDER_EEVEE':
        return []
    result = ['--gpu-backend', value['backend'].lower()]
    if value.get('graphics_device', 'AUTO') != 'AUTO':
        result += ['--gpu-device', value['graphics_device'], '--gpu-device-no-fallback']
    return result


def configure_options(prefs, options):
    for key, item in options.items():
        if not hasattr(prefs, key):
            raise ValueError('This Blender build does not support the saved device option: ' + key)
        setattr(prefs, key, item)


def configure_preferences(prefs, value):
    backend = value['backend']
    prefs.compute_device_type = backend
    available = list(prefs.get_devices_for_type(backend)) if backend != 'NONE' else []
    wanted = {(d['type'], d['id']) for d in value['devices']}
    found = {(d.type, d.id) for d in available if getattr(d, 'meets_driver_requirement', True)}
    missing = [d['name'] for d in value['devices'] if (d['type'], d['id']) not in found]
    if missing:
        raise ValueError('Selected render device unavailable: ' + ', '.join(missing) + '. Review Render devices.')
    for entry in prefs.devices:
        entry.use = (entry.type, entry.id) in wanted
    configure_options(prefs, value.get('options', {}))
    resolved = [{'name': d.name, 'type': d.type, 'id': d.id} for d in available if (d.type, d.id) in wanted]
    return {'backend': backend, 'devices': resolved}


def configure(value):
    """Resolve the exact requested devices in the worker, with no CPU fallback."""
    validate(value)
    import bpy
    if value['engine'] == 'BLENDER_EEVEE':
        import gpu
        gpu.init()
        backend = gpu.platform.backend_type_get()
        if backend != value['backend']:
            raise ValueError(f"Requested {value['backend']}, but Blender initialized {backend}. Check Render devices.")
        return {'engine': value['engine'], 'backend': backend,
                'devices': [{'name': gpu.platform.renderer_get(), 'type': 'GPU'}]}
    prefs = bpy.context.preferences.addons['cycles'].preferences
    if value['mode'] == 'CPU':
        if value.get('preferences_required') and 'preferences' in value:
            configured = configure_preferences(prefs, value['preferences'])
        else:
            # CPU rendering/denoising must not depend on an unused GPU driver.
            prefs.compute_device_type = 'NONE'
            configure_options(prefs, value.get('preferences', {}).get('options', {}))
            configured = {'backend': 'NONE', 'devices': [], 'not_required': True}
        devices = prefs.get_device_list('CPU')
        return {'engine': 'CYCLES', 'backend': 'CPU', 'preferences': configured,
                'devices': [{'name': d[0], 'type': 'CPU', 'id': d[2]} for d in devices if d[1] == 'CPU']}
    return {'engine': 'CYCLES', **configure_preferences(prefs, value)}


def probe(binary, value):
    """Never call driver discovery from a Blender UI callback."""
    validate(value)
    command = [binary, '--background', '--factory-startup', '--disable-autoexec', *arguments(value),
               '--python-exit-code', '1', '--python', str(Path(__file__).resolve()), '--', json.dumps(value)]
    environment = os.environ.copy()
    for name in ('PYTHONHOME', 'PYTHONPATH', 'OCIO'):
        environment.pop(name, None)
    environment['PYTHONUTF8'] = '1'
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=40,
                                env=environment, **options)
    except subprocess.TimeoutExpired as error:
        raise ValueError('Render device check timed out. Review Blender Render devices and the graphics driver.') from error
    output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
    records = [line[len(MARKER):] for line in output.splitlines() if line.startswith(MARKER)]
    if result.returncode or not records:
        raise ValueError('Render device check failed: ' + output.strip()[-1200:])
    resolved = json.loads(records[-1])
    if resolved.get('error'):
        raise ValueError(resolved['error'])
    return resolved


if __name__ == '__main__':
    try:
        resolved = configure(json.loads(sys.argv[sys.argv.index('--') + 1]))
    except Exception as error:
        resolved = {'error': str(error)}
    print(MARKER + json.dumps(resolved), flush=True)
