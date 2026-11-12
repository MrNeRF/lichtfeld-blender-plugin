# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture framing in an isolated scene; source cameras/render settings stay intact."""
import bpy
from . import capture_settings, ui_runtime
from .ui_text import draw_paragraph

SOURCE = 'lichtfeld_capture_preview_source'
CAMERA = 'lichtfeld_capture_preview_camera'
_views = {}
_pending = {}


def camera_pending(scene):
    return scene.as_pointer() in _pending


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


def remove_camera(scene):
    camera = scene.get(CAMERA)
    if isinstance(camera, bpy.types.Object) and camera.get(CAMERA):
        other_scenes = [item for item in camera.users_scene if item != scene]
        if other_scenes:
            # A user deliberately linked the temporary camera elsewhere.
            camera.pop(CAMERA, None)
            if camera.name in scene.collection.objects:
                scene.collection.objects.unlink(camera)
        else:
            data = camera.data
            bpy.data.objects.remove(camera, do_unlink=True)
            if data.users == 0:
                bpy.data.cameras.remove(data)


def remove(scene):
    _pending.pop(scene.as_pointer(), None)
    remove_camera(scene)
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
    _pending.clear()


def prepare_camera(source, window):
    """Use the capture copy contract for both opening and changing a view."""
    settings = source.lichtfeld_training
    scene = window.scene
    cameras = []
    if settings.camera_collection:
        for obj in settings.camera_collection.all_objects:
            if obj.type == 'CAMERA':
                cameras.append(obj)
            yield
    cameras.sort(key=lambda obj: obj.name)
    if not cameras:
        raise ValueError('Choose a camera collection first')
    # Read the latest request after the cooperative scene walk, without another
    # yield before the copy. An Update framing click can be coalesced meanwhile.
    value = capture_settings.format_for(source, settings)
    overrides = capture_settings.camera_for(settings)
    index = min(settings.capture_preview_index, len(cameras))
    original = cameras[index - 1]
    if original.name not in scene.objects:
        raise ValueError('The camera collection changed. Return to capture setup and reopen framing.')
    # Another window can still display a shared preview after its first window
    # closes. Pick a live context without changing any window's scene.
    window = next((item for item in bpy.context.window_manager.windows
                   if item == window and item.scene == scene), None) or next(
                       (item for item in bpy.context.window_manager.windows if item.scene == scene), None)
    if window is None:
        raise ReferenceError('The framing window was closed')
    if scene != source and (scene.frame_current != source.frame_current or scene.frame_subframe):
        scene.frame_set(source.frame_current)
    with bpy.context.temp_override(window=window):
        graph = bpy.context.evaluated_depsgraph_get()
        camera = capture_settings.copy_camera(original, graph, overrides)
    camera[CAMERA] = True
    return camera, original.name, len(cameras), index, value


def camera_changed(settings, _context):
    """Queue one update per visible preview; RNA callbacks never copy scene data."""
    source = settings.id_data
    if settings.running or settings.job_directory:
        return
    for window in bpy.context.window_manager.windows:
        preview = window.scene
        if source_scene(preview) != source or camera_pending(preview):
            continue
        key, token = preview.as_pointer(), object()
        _pending[key] = token
        ui_runtime.schedule(camera_steps(source, preview, key, token),
                            lambda error: ui_runtime.report_notice('ERROR', error))


def camera_steps(source, preview, key, token):
    """Replace only the owned camera/format, retaining the prepared scene and view."""
    from . import rig_edit
    camera = None
    try:
        # The preview can be closed, replaced or deleted before its queued turn.
        if source_scene(preview) != source:
            return
        settings = source.lichtfeld_training
        if settings.running or settings.job_directory:
            return
        work = None
        while True:
            window = next((item for item in bpy.context.window_manager.windows if item.scene == preview), None)
            if (window is None or
                    source_scene(preview) != source or settings.running or settings.job_directory):
                if work is not None:
                    work.close()
                return
            if rig_edit.render_busy(bpy.context):
                yield ui_runtime.WAIT
                continue
            if settings.operation_busy:
                # A setup operation can be queued behind us. Let it finish on
                # the same queue instead of blocking it or dropping this input.
                # Its edits can invalidate a partially scanned camera list.
                if work is not None:
                    work.close()
                    work = None
                yield ui_runtime.DEFER
                continue
            if work is None:
                work = prepare_camera(source, window)
            try:
                next(work)
            except StopIteration as result:
                camera, name, count, index, value = result.value
                break
            yield
        # No yield between camera allocation and commit/cleanup.
        preview.collection.objects.link(camera)
        preview.camera = camera
        remove_camera(preview)
        preview[CAMERA] = camera
        camera = None
        capture_settings.apply_format(preview, value)
        preview['capture_camera_name'] = name
        preview['capture_camera_count'] = count
        preview['capture_format_label'] = f"{value['width']} × {value['height']} px"
        if settings.capture_preview_index != index:
            settings.capture_preview_index = index
    except ReferenceError:
        # Closing a window or deleting a scene invalidates its pending update.
        return
    finally:
        if camera is not None:
            data = camera.data
            bpy.data.objects.remove(camera, do_unlink=True)
            if data.users == 0:
                bpy.data.cameras.remove(data)
        if _pending.get(key) is token:
            _pending.pop(key)


def open_steps(source, window, area):
    """Opening can span UI ticks; defer every data edit while a render owns Main."""
    from . import rig_edit
    work = _open_steps(source, window, area)
    try:
        while True:
            if rig_edit.render_busy(bpy.context):
                yield ui_runtime.WAIT
                continue
            try:
                step = next(work)
            except StopIteration:
                return
            yield step
    finally:
        work.close()


def _open_steps(source, window, area):
    settings = source.lichtfeld_training
    settings.operation_busy = True
    preview = None
    camera = None
    complete = False
    try:
        if window.scene != source:
            return
        camera, name, count, index, value = yield from prepare_camera(source, window)
        if settings.capture_preview_index != index:
            settings.capture_preview_index = index
        layer = window.view_layer
        preview = bpy.data.scenes.new('LichtFeld capture framing')
        preview[SOURCE] = source
        preview['lichtfeld_capture_preview'] = True
        preview['capture_camera_name'] = name
        preview['capture_camera_count'] = count
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
        if window.scene != source:
            return
        key = (window.as_pointer(), area.as_pointer()) if area else None
        if area and area.type == 'VIEW_3D':
            space = area.spaces.active
            if key not in _views:
                _views[key] = (space.region_3d.view_perspective, space.use_local_camera, space.camera)
        window.scene = preview
        if area and area.type == 'VIEW_3D':
            space.use_local_camera = False
            space.region_3d.view_perspective = 'CAMERA'
        complete = True
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
        from .rig_edit import render_busy
        if render_busy(context):
            return False
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
        if is_preview(context.scene):
            camera_changed(source.lichtfeld_training, context)
            return {'FINISHED'}
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

    @classmethod
    def poll(cls, context):
        from .rig_edit import render_busy
        source = source_scene(context.scene)
        return is_preview(context.scene) and not render_busy(context) and not (
            source and source.lichtfeld_training.operation_busy)

    def execute(self, context):
        from .rig_edit import render_busy
        if render_busy(context):
            return {'CANCELLED'}
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


def draw(layout, context, source, *, width=None):
    from . import draw_capture_format, draw_capture_overrides
    s = source.lichtfeld_training
    def wrapped(box, text):
        draw_paragraph(box, text, width if width is not None else context.region.width, context.preferences.system.ui_scale)
    layout.label(text='Capture framing', icon='VIEW_CAMERA')
    layout.operator('lichtfeld.close_capture_framing', icon='BACK')
    box = layout.box()
    box.label(text=context.scene.get('capture_camera_name', ''))
    box.label(text=context.scene.get('capture_format_label', ''))
    from .rig_edit import render_busy
    box.enabled = not (render_busy(context) or s.operation_busy or s.running or s.job_directory)
    box.prop(s, 'capture_preview_index', text='Camera number')
    box.label(text=f"{context.scene.get('capture_camera_count', 0)} cameras, sorted by name")
    draw_capture_format(box, source, s, wrapped)
    draw_capture_overrides(box, s, wrapped)
    box.operator('lichtfeld.capture_framing', text='Update framing', icon='FILE_REFRESH')
    wrapped(layout, 'Camera changes are automatic. Update framing applies output settings and camera overrides. Source cameras stay unchanged.')


def draw_orphaned(layout, _context):
    layout.label(text='The source scene was removed', icon='ERROR')
    layout.operator('lichtfeld.close_capture_framing', text='Close preview', icon='BACK')


CLASSES = (LF_OT_capture_framing, LF_OT_close_capture_framing)
