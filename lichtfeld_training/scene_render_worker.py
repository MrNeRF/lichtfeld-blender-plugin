# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate frozen scene settings without importing the extension in a worker."""


def verify_saved_settings(scene, value):
    from render_devices import validate
    if not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1:
        raise ValueError('Unsupported saved render settings')
    validate(value.get('request'))
    cycles = scene.render.engine == 'CYCLES'
    actual = {'engine': scene.render.engine,
        'samples': scene.cycles.samples if cycles else scene.eevee.taa_render_samples,
        'device': scene.cycles.device if cycles else 'GPU',
        'adaptive_sampling': bool(scene.cycles.use_adaptive_sampling) if cycles else False,
        'denoising': bool(scene.cycles.use_denoising) if cycles else False,
        'persistent_data': bool(scene.render.use_persistent_data)}
    for name in ('denoiser', 'denoising_use_gpu'):
        if name in value:
            actual[name] = getattr(scene.cycles, name) if cycles else '' if name == 'denoiser' else False
    if (any(type(value.get(name)) is not type(expected) or value[name] != expected for name, expected in actual.items()) or
            value['request']['engine'] != actual['engine'] or cycles and value['request']['mode'] != actual['device']):
        raise ValueError('Saved Blender render settings differ from this capture. Save the scene and start a new capture.')
