# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""One capture contract for rendering, calibration, rig fitting and framing."""
import math

MIN_VIEWS = 4
MAX_VIEWS = 10000


ASPECTS = {'SQUARE': (1, 1), 'FOUR_THREE': (4, 3), 'THREE_TWO': (3, 2),
           'WIDE': (16, 9), 'CINEMA': (21, 9)}
FORMAT_FIELDS = ('capture_format', 'capture_aspect', 'capture_orientation', 'resolution',
                 'capture_width', 'capture_height')
CAMERA_FIELDS = ('override_optics', 'capture_lens', 'capture_sensor_width', 'capture_sensor_height',
                 'capture_sensor_fit', 'override_shift', 'capture_shift_x', 'capture_shift_y',
                 'override_clipping', 'capture_clip_start', 'capture_clip_end')


def validate_view_count(count):
    if type(count) is not int or not MIN_VIEWS <= count <= MAX_VIEWS:
        raise ValueError(f'Capture needs {MIN_VIEWS}–{MAX_VIEWS:,} views')


def validate_format(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid capture format')
    result = {key: value.get(key) for key in ('width', 'height', 'pixel_aspect_x', 'pixel_aspect_y')}
    if any(type(result[key]) is not int or not 64 <= result[key] <= 4096 for key in ('width', 'height')):
        raise ValueError('Capture width and height must each be between 64 and 4096 pixels')
    if any(type(result[key]) not in (int, float) or not math.isfinite(result[key]) or not 1 <= result[key] <= 200
           for key in ('pixel_aspect_x', 'pixel_aspect_y')):
        raise ValueError('Invalid capture pixel aspect')
    return result


def format_for(scene, settings):
    """The official scene output is the only source for new captures."""
    render = scene.render
    return validate_format(dict(width=render.resolution_x * render.resolution_percentage // 100,
                                height=render.resolution_y * render.resolution_percentage // 100,
                                pixel_aspect_x=render.pixel_aspect_x, pixel_aspect_y=render.pixel_aspect_y))


def preset_format(settings, mode=None):
    """Build an explicit size preset; it takes effect only when applied."""
    mode = mode or settings.capture_format
    px = py = 1.0
    if mode == 'SCENE':
        mode = 'PRESET'
    if mode == 'CUSTOM':
        width, height = settings.capture_width, settings.capture_height
    elif mode == 'PRESET':
        x, y = ASPECTS[settings.capture_aspect]
        if settings.capture_orientation == 'PORTRAIT':
            x, y = y, x
        size = settings.resolution
        width, height = round(size * x / max(x, y)), round(size * y / max(x, y))
    else:
        raise ValueError('Unknown capture format')
    return validate_format(dict(width=width, height=height, pixel_aspect_x=px, pixel_aspect_y=py))


def camera_for(settings):
    result = {}
    if settings.override_optics:
        result.update(lens=settings.capture_lens, sensor_width=settings.capture_sensor_width,
                      sensor_height=settings.capture_sensor_height, sensor_fit=settings.capture_sensor_fit)
    if settings.override_shift:
        result.update(shift_x=settings.capture_shift_x, shift_y=settings.capture_shift_y)
    if settings.override_clipping:
        result.update(clip_start=settings.capture_clip_start, clip_end=settings.capture_clip_end)
    return validate_camera(result)


def validate_camera(value):
    ranges = {'lens': (1, 500), 'sensor_width': (1, 100), 'sensor_height': (1, 100),
              'shift_x': (-2, 2), 'shift_y': (-2, 2), 'clip_start': (.00001, 100000), 'clip_end': (.00002, 1000000)}
    if not isinstance(value, dict) or set(value) - set(ranges) - {'sensor_fit'}:
        raise ValueError('Invalid capture camera overrides')
    for key, number in value.items():
        if key == 'sensor_fit':
            if number not in {'AUTO', 'HORIZONTAL', 'VERTICAL'}:
                raise ValueError('Invalid capture sensor fit')
        elif type(number) not in (int, float) or not math.isfinite(number) or not ranges[key][0] <= number <= ranges[key][1]:
            raise ValueError('Invalid capture camera parameter: ' + key)
    if ('clip_start' in value) != ('clip_end' in value):
        raise ValueError('Capture clipping needs both near and far distances')
    if 'clip_start' in value and value['clip_start'] >= value['clip_end']:
        raise ValueError('Capture far clipping must be greater than near clipping')
    return dict(value)


def job_format(job, scene):
    if 'capture_format' in job:
        return validate_format(job['capture_format'])
    # Jobs written by 0.1.0 before rectangular capture used the scene's pixels.
    size = job['resolution']
    return validate_format(dict(width=size, height=size, pixel_aspect_x=scene.render.pixel_aspect_x,
                                pixel_aspect_y=scene.render.pixel_aspect_y))


def apply_format(scene, value):
    value = validate_format(value)
    scene.render.resolution_x = value['width']
    scene.render.resolution_y = value['height']
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = value['pixel_aspect_x']
    scene.render.pixel_aspect_y = value['pixel_aspect_y']
    scene.render.use_border = False
    scene.render.use_crop_to_border = False


def apply_camera(data, value):
    for key, number in validate_camera(value).items():
        setattr(data, key, number)
    data.dof.use_dof = False


def camera_pose(camera, graph):
    """Match Blender's scale-free camera frame; reject non-rigid calibration."""
    evaluated = camera.evaluated_get(graph)
    if evaluated.data.type != 'PERSP':
        raise ValueError(f'Camera {camera.name}: capture requires a Perspective camera')
    matrix = evaluated.matrix_world.copy()
    if any(not math.isfinite(value) for row in matrix for value in row):
        raise ValueError(f'Camera {camera.name}: transform contains non-finite values')
    axes = [matrix.to_3x3().col[index].copy() for index in range(3)]
    if any(not math.isfinite(axis.length) or axis.length < 1e-8 for axis in axes):
        raise ValueError(f'Camera {camera.name}: zero or near-zero scale; inspect the camera and its parents')
    axes = [axis.normalized() for axis in axes]
    if any(abs(axes[a].dot(axes[b])) > 1e-5 for a, b in ((0, 1), (0, 2), (1, 2))):
        raise ValueError(f'Camera {camera.name}: sheared transform; inspect the camera and its parents')
    if axes[0].cross(axes[1]).dot(axes[2]) < 0:
        raise ValueError(f'Camera {camera.name}: mirrored transform; inspect the camera and its parents')
    return matrix.normalized()


def copy_camera(camera, graph, overrides):
    """Freeze evaluated pose/optics before unlinking rig parents or constraints."""
    import bpy
    pose = camera_pose(camera, graph)
    evaluated = camera.evaluated_get(graph)
    data = evaluated.data.copy()
    data.animation_data_clear()
    try:
        apply_camera(data, overrides)
        copy = bpy.data.objects.new(camera.name + ' capture pose', data)
        copy.matrix_world = pose
        return copy
    except Exception:
        if not data.users:
            bpy.data.cameras.remove(data)
        raise


def fitting_factor(scene, settings, graph):
    """Smallest actual half-FOV, including portrait format and optical overrides."""
    import bpy
    value = format_for(scene, settings)
    data = bpy.data.cameras.new('LichtFeld framing calculation')
    camera = None
    try:
        data.lens = settings.rig_lens
        apply_camera(data, camera_for(settings))
        camera = bpy.data.objects.new('LichtFeld framing calculation', data)
        p = camera.calc_matrix_camera(graph, x=value['width'], y=value['height'],
                                     scale_x=value['pixel_aspect_x'], scale_y=value['pixel_aspect_y'])
        limits = ((1 - abs(p[0][2])) / p[0][0], (1 - abs(p[1][2])) / p[1][1])
        if min(limits) <= 0:
            raise ValueError('Automatic framing needs the optical axis inside the capture frame; reduce camera shift')
        return 1.05 / math.sin(math.atan(min(limits)))
    finally:
        if camera is not None:
            bpy.data.objects.remove(camera)
        bpy.data.cameras.remove(data)


def signature(scene, settings):
    return (tuple(getattr(settings, name) for name in FORMAT_FIELDS + CAMERA_FIELDS),
            tuple(getattr(scene.render, name) for name in ('resolution_x', 'resolution_y', 'resolution_percentage',
                                                          'pixel_aspect_x', 'pixel_aspect_y')))


def framing_signature(scene, settings):
    """Only changes that can alter generated automatic camera distance."""
    if not settings.rig_auto_fit or settings.rig_mode in {'PATH', 'MESH'}:
        return None
    try:
        value = format_for(scene, settings)
        aspect = round(value['width'] * value['pixel_aspect_x'] / (value['height'] * value['pixel_aspect_y']), 8)
        camera = camera_for(settings)
        return (aspect, {key: number for key, number in camera.items() if not key.startswith('clip_')})
    except ValueError:
        return ('invalid capture framing',)
