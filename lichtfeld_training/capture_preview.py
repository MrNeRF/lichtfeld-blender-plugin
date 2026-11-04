# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture framing in an isolated scene; source cameras/render settings stay intact."""
import bpy
from . import capture_settings, ui_runtime
from .ui_text import draw_paragraph

SOURCE = 'lichtfeld_capture_preview_source'
CAMERA = 'lichtfeld_capture_preview_camera'
_views = {}


def is_preview(scene):
    try:
        return isinstance(scene, bpy.types.Scene) and (bool(scene.get('lichtfeld_capture_preview')) or SOURCE in scene)
    except ReferenceError:
        return False


def source_scene(scene):
    if not is_preview(scene):
        return None
    try:
        source = scene.get(SOURCE)
        return source if isinstance(source, bpy.types.Scene) and source.name in bpy.data.scenes else None
    except ReferenceError:
        return None


def fallback_scene(excluding=None, *, create=False):
    scene = next((item for item in bpy.data.scenes if item != excluding and not is_preview(item)), None)
    if scene is None and create:
        scene = bpy.data.scenes.new('Scene')
    return scene


def restore_window(window, area):
    if not area:
        return
    state = _views.pop((window.as_pointer(), area.as_pointer()), None)
    if not state or area.type != 'VIEW_3D':
        return
    try:
        space = area.spaces.active
        perspective, local_camera, camera = state
        if camera is not None and camera.name not in bpy.data.objects:
            camera = None
        space.region_3d.view_perspective = perspective
        space.use_local_camera = local_camera
        space.camera = camera
    except (ReferenceError, RuntimeError, TypeError):
        pass


def remove(scene):
    camera = scene.get(CAMERA)
    if isinstance(camera, bpy.types.Object) and camera.get(CAMERA):
        other_scenes = [item for item in camera.users_scene if item != scene]
        if other_scenes:
            # A user deliberately linked the temporary camera elsewhere.
            camera.pop(CAMERA, None)
        else:
            data = camera.data
            bpy.data.objects.remove(camera, do_unlink=True)
            if data.users == 0:
                bpy.data.cameras.remove(data)
    bpy.data.scenes.remove(scene)


def cleanup():
    for scene in list(bpy.data.scenes):
        if not is_preview(scene):
            continue
        source = source_scene(scene) or fallback_scene(scene, create=bool(bpy.context.window_manager.windows))
        for window in bpy.context.window_manager.windows:
            if window.scene == scene:
                if source:
                    window.scene = source
                for area in window.screen.areas:
                    restore_window(window, area)
        remove(scene)
    _views.clear()


def open_steps(source, window, area):
    settings = source.lichtfeld_training
    settings.operation_busy = True
    preview = None
    camera = None
    complete = False
    try:
        if window.scene != source and source_scene(window.scene) != source:
            return
        value = capture_settings.format_for(source, settings)
        overrides = capture_settings.camera_for(settings)
        cameras = []
        if settings.camera_collection:
            for obj in settings.camera_collection.all_objects:
                if obj.type == 'CAMERA':
                    cameras.append(obj)
                yield
        cameras.sort(key=lambda obj: obj.name)
        if not cameras:
            raise ValueError('Choose a camera collection first')
        settings.capture_preview_index = min(settings.capture_preview_index, len(cameras))
        original = cameras[settings.capture_preview_index - 1]
        if original.data.type != 'PERSP':
            raise ValueError('Capture framing requires a Perspective camera')
        layer = source.view_layers.get(window.scene.get('capture_source_layer', '')) if source_scene(window.scene) else window.view_layer
        layer = layer or source.view_layers[0]
        previous_scene, previous_layer = window.scene, window.view_layer
        try:
            window.scene = source
            window.view_layer = layer
            # A scene/view-layer temp_override can leave Blender 5.3 beta's
            # graph without valid Bases. Switch and restore before yielding.
            with bpy.context.temp_override(window=window):
                graph = bpy.context.evaluated_depsgraph_get()
                camera = capture_settings.copy_camera(original, graph, overrides)
        finally:
            window.scene = previous_scene
            window.view_layer = previous_layer
        camera[CAMERA] = True
        preview = bpy.data.scenes.new('LichtFeld capture framing')
        preview[SOURCE] = source
        preview['lichtfeld_capture_preview'] = True
        preview['capture_camera_name'] = original.name
        preview['capture_camera_count'] = len(cameras)
        preview['capture_source_layer'] = layer.name
        preview['capture_format_label'] = f"{value['width']} × {value['height']} px"
        preview.collection.objects.link(camera)
        preview.camera = camera
        preview[CAMERA] = camera
        preview.world = source.world
        preview.frame_set(source.frame_current)
        capture_settings.apply_format(preview, value)
        yield
        for child in source.collection.children:
            preview.collection.children.link(child)
            yield
        for obj in source.collection.objects:
            preview.collection.objects.link(obj)
            yield
        # Preserve collection exclusions without changing the shared collections.
        def exclusions(before, after):
            after.exclude = before.exclude
            after.hide_viewport = before.hide_viewport
            for child in before.children:
                target = after.children.get(child.name)
                if target:
                    yield from exclusions(child, target)
                yield
        yield from exclusions(layer.layer_collection, preview.view_layers[0].layer_collection)
        from .preflight import capture_helpers, selection_objects
        sources = None
        if settings.scope == 'COLLECTION':
            sources = set(settings.source_collection.all_objects) if settings.source_collection else set()
        elif settings.scope == 'SELECTION':
            from types import SimpleNamespace
            sources = set(selection_objects(SimpleNamespace(scene=source, selected_objects=bpy.context.selected_objects)))
        helpers = set(capture_helpers(settings))
        for obj in preview.objects:
            hidden = (obj in helpers or obj.get('lichtfeld_result') or obj.type == 'CAMERA' and obj != camera or
                      sources is not None and obj not in sources and obj.type != 'LIGHT' and obj != camera)
            if hidden and obj.name in preview.view_layers[0].objects:
                obj.hide_set(True, view_layer=preview.view_layers[0])
            yield
        if window.scene != source and source_scene(window.scene) != source:
            return
        previous = window.scene if source_scene(window.scene) else None
        key = (window.as_pointer(), area.as_pointer()) if area else None
        if area and area.type == 'VIEW_3D':
            space = area.spaces.active
            if key not in _views:
                _views[key] = (space.region_3d.view_perspective, space.use_local_camera, space.camera)
            space.use_local_camera = False
            space.region_3d.view_perspective = 'CAMERA'
        window.scene = preview
        complete = True
        if previous and not any(w.scene == previous for w in bpy.context.window_manager.windows):
            remove(previous)
        yield
    finally:
        if not complete and preview is not None:
            remove(preview)
        elif not complete and camera is not None:
            data = camera.data
            bpy.data.objects.remove(camera, do_unlink=True)
            if data.users == 0:
                bpy.data.cameras.remove(data)
        if not complete and area and area.type == 'VIEW_3D':
            restore_window(window, area)
        settings.operation_busy = False


class LF_OT_capture_framing(bpy.types.Operator):
    bl_idname = 'lichtfeld.capture_framing'
    bl_label = 'Preview capture framing'
    bl_description = 'View the exact capture format and camera overrides in a temporary scene; no render is started'

    @classmethod
    def poll(cls, context):
        if is_preview(context.scene) and source_scene(context.scene) is None:
            return False
        source = source_scene(context.scene) or context.scene
        s = source.lichtfeld_training
        return bool(s.camera_collection) and not s.running and not s.operation_busy and not s.job_directory

    def execute(self, context):
        source = source_scene(context.scene) or context.scene
        if is_preview(context.scene) and source == context.scene:
            self.report({'ERROR'}, 'The original scene was removed. Close this temporary preview to return to another scene.')
            return {'CANCELLED'}
        work = open_steps(source, context.window, context.area)
        def failed(error):
            ui_runtime.report_notice('ERROR', error)
        if bpy.app.background:
            try:
                for _ in work:
                    pass
            except Exception as error:
                failed(str(error))
                return {'CANCELLED'}
        else:
            source.lichtfeld_training.operation_busy = True
            ui_runtime.schedule(work, failed)
        return {'FINISHED'}


class LF_OT_close_capture_framing(bpy.types.Operator):
    bl_idname = 'lichtfeld.close_capture_framing'
    bl_label = 'Back to capture setup'
    bl_description = 'Return to the original scene and remove the temporary framing scene and camera'

    def execute(self, context):
        scene = context.scene
        source = source_scene(scene)
        if not is_preview(scene) or source and source.lichtfeld_training.operation_busy:
            return {'CANCELLED'}
        missing_source = source is None
        source = source or fallback_scene(scene, create=True)
        context.window.scene = source
        restore_window(context.window, context.area)
        remains_open = any(w.scene == scene for w in context.window_manager.windows)
        if not remains_open:
            remove(scene)
        if missing_source:
            message = ('The source scene was removed. The preview will be cleaned up when no window uses it.'
                       if remains_open else 'The source scene was removed. Returned to another scene and cleaned up the preview.')
            self.report({'WARNING'}, message)
        return {'FINISHED'}


def draw(layout, context, source):
    from . import draw_capture_format, draw_capture_overrides
    s = source.lichtfeld_training
    def wrapped(box, text):
        draw_paragraph(box, text, context.region.width, context.preferences.system.ui_scale)
    layout.label(text='Capture framing', icon='VIEW_CAMERA')
    layout.operator('lichtfeld.close_capture_framing', icon='BACK')
    box = layout.box()
    box.label(text=context.scene.get('capture_camera_name', ''))
    box.label(text=context.scene.get('capture_format_label', ''))
    box.prop(s, 'capture_preview_index', text='Camera number')
    box.label(text=f"{context.scene.get('capture_camera_count', 0)} cameras, sorted by name")
    draw_capture_format(box, source, s, wrapped)
    draw_capture_overrides(box, s, wrapped)
    box.operator('lichtfeld.capture_framing', text='Update framing', icon='FILE_REFRESH')
    wrapped(layout, 'Framing preview only. Apply changes with Update framing. Return to setup to capture; source cameras stay unchanged.')


def draw_orphaned(layout, _context):
    layout.label(text='The source scene was removed', icon='ERROR')
    layout.operator('lichtfeld.close_capture_framing', text='Close preview', icon='BACK')


CLASSES = (LF_OT_capture_framing, LF_OT_close_capture_framing)
