# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture framing in an isolated scene; source cameras/render settings stay intact."""
from pathlib import Path
import time

import bpy
from . import capture_settings, ui_runtime
from .ui_text import draw_paragraph
from .visibility import object_steps

SOURCE = 'lichtfeld_capture_preview_source'
CAMERA = 'lichtfeld_capture_preview_camera'
ORIGINAL_CAMERA = 'lichtfeld_capture_preview_original_camera'
_views = {}
_pending = {}
_indices = {}


def clear_camera_indices():
    _indices.clear()


def camera_index(settings):
    return _indices.get(settings.id_data.session_uid, settings.get('capture_preview_index', 1))


def set_camera_index(settings, value):
    _indices[settings.id_data.session_uid] = value


def visible_camera_index(_state):
    source = source_scene(bpy.context.scene)
    return source.lichtfeld_training.capture_preview_index if source is not None else 1


def set_visible_camera_index(_state, value):
    source = source_scene(bpy.context.scene)
    if source is not None:
        source.lichtfeld_training.capture_preview_index = value


def camera_pending(scene):
    return scene.as_pointer() in _pending


def clear_pending():
    """Forget requests after the UI queue has been cancelled."""
    _pending.clear()


def restore_history_navigation(context):
    """Use one visible, restored camera per source, preferring the active window."""
    windows = sorted(context.window_manager.windows, key=lambda window: window != context.window)
    restored = set()
    for window in windows:
        preview = window.scene
        source = source_scene(preview)
        if source is None or source in restored:
            continue
        restored.add(source)
        settings = source.lichtfeld_training
        index = preview.get('capture_camera_index')
        if type(index) is int and 1 <= index <= capture_settings.MAX_VIEWS:
            set_camera_index(settings, index)
        camera_changed(settings, context, refresh_only=True)


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
        perspective, local_camera, camera, location, rotation, distance, offset, zoom = state
        if camera is not None and camera.name not in bpy.data.objects:
            camera = None
        region = space.region_3d
        space.use_local_camera = local_camera
        space.camera = camera
        region.view_perspective = perspective
        region.view_location = location
        region.view_rotation = rotation
        region.view_distance = distance
        region.view_camera_offset = offset
        region.view_camera_zoom = zoom
    except (ReferenceError, RuntimeError, TypeError):
        pass


def owned_camera(scene):
    camera = scene.camera
    if camera and camera.get(CAMERA):
        return camera
    return next((obj for obj in scene.collection.objects if obj.type == 'CAMERA' and obj.get(CAMERA)), None)


def remove_camera(scene, camera=None):
    if camera is None:
        camera = owned_camera(scene)
    if camera:
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


def restore_source(window, preview, source):
    layer = source.view_layers.get(preview.get('capture_source_layer', '')) or source.view_layers[0]
    window.scene = source
    window.view_layer = layer


def cleanup():
    for scene in list(bpy.data.scenes):
        if not is_preview(scene):
            continue
        source = source_scene(scene) or fallback_scene(scene, create=bool(bpy.context.window_manager.windows))
        for window in bpy.context.window_manager.windows:
            if window.scene == scene:
                if source:
                    restore_source(window, scene, source)
                for area in window.screen.areas:
                    restore_window(window, area)
        remove(scene)
    _views.clear()
    _pending.clear()


def prepare_camera(source, window, *, prefer_selected=False, request=None):
    """Use the capture copy contract for both opening and changing a view."""
    settings = source.lichtfeld_training
    scene = window.scene
    from .preflight import CAMERA_EXCLUDED, camera_objects
    cameras = yield from camera_objects(source, settings, include_excluded=True)
    if not cameras:
        raise ValueError('Choose a camera collection first')
    # Read the latest request after the cooperative scene walk, without another
    # yield before the copy. An Update framing click can be coalesced meanwhile.
    value = capture_settings.format_for(source, settings)
    overrides = capture_settings.camera_for(settings)
    index = min(settings.capture_preview_index, len(cameras))
    if prefer_selected:
        active = window.view_layer.objects.active
        if active in cameras and active.select_get(view_layer=window.view_layer):
            index = cameras.index(active) + 1
    original = cameras[index - 1]
    count = len(cameras)
    excluded = sum(bool(obj.get(CAMERA_EXCLUDED)) for obj in cameras)
    if (request and request['refresh_only'] and scene.get(ORIGINAL_CAMERA) == original
            and scene.get('capture_camera_index') == index):
        return None, original, count, excluded, index, value
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
    return camera, original, count, excluded, index, value


def camera_changed(settings, _context, *, refresh_only=False):
    """Queue one update per visible preview; RNA callbacks never copy scene data."""
    source = settings.id_data
    if settings.running or settings.job_directory:
        return
    for window in bpy.context.window_manager.windows:
        preview = window.scene
        if source_scene(preview) != source:
            continue
        key = preview.as_pointer()
        if key in _pending:
            if not refresh_only:
                _pending[key]['refresh_only'] = False
            continue
        token = {'refresh_only': refresh_only}
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
                work = prepare_camera(source, window, request=token)
            try:
                next(work)
            except ReferenceError:
                work.close()
                work = None
                yield ui_runtime.DEFER
                continue
            except StopIteration as result:
                camera, original, count, excluded, index, value = result.value
                break
            yield
        if camera is None:
            preview['capture_camera_count'] = count
            preview['capture_excluded_count'] = excluded
            return
        # No yield between camera allocation and commit/cleanup.
        previous = owned_camera(preview)
        preview.collection.objects.link(camera)
        # Keep an active camera throughout the swap so Blender stays in Camera view.
        preview.camera = camera
        camera = None
        if previous is not None:
            remove_camera(preview, previous)
        capture_settings.apply_format(preview, value)
        preview['capture_camera_name'] = original.name
        preview[ORIGINAL_CAMERA] = original
        preview['capture_excluded_count'] = excluded
        preview['capture_camera_count'] = count
        preview['capture_camera_index'] = index
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


def open_steps(source, window, area, *, prefer_selected=False):
    """Opening can span UI ticks; defer every data edit while a render owns Main."""
    from . import rig_edit
    work = _open_steps(source, window, area, prefer_selected=prefer_selected)
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


def _open_steps(source, window, area, *, prefer_selected):
    settings = source.lichtfeld_training
    settings.operation_busy = True
    preview = None
    camera = None
    complete = False
    try:
        if window.scene != source:
            return
        camera, original, count, excluded, index, value = yield from prepare_camera(source, window, prefer_selected=prefer_selected)
        if settings.capture_preview_index != index:
            settings.capture_preview_index = index
        layer = window.view_layer
        preview = bpy.data.scenes.new('LichtFeld capture framing')
        preview[SOURCE] = source
        preview['lichtfeld_capture_preview'] = True
        preview['capture_camera_name'] = original.name
        preview[ORIGINAL_CAMERA] = original
        preview['capture_excluded_count'] = excluded
        preview['capture_camera_count'] = count
        preview['capture_camera_index'] = index
        preview['capture_source_layer'] = layer.name
        preview.collection.objects.link(camera)
        preview.camera = camera
        preview.world = source.world
        preview.render.engine = source.render.engine
        preview.render.film_transparent = source.render.film_transparent
        if source.render.engine == 'CYCLES':
            preview.cycles.device = source.cycles.device
        for field in ('use_border', 'use_crop_to_border', 'border_min_x', 'border_min_y', 'border_max_x', 'border_max_y'):
            setattr(preview.render, field, getattr(source.render, field))
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
        for obj in object_steps(preview.objects):
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
                region = space.region_3d
                _views[key] = (region.view_perspective, space.use_local_camera, space.camera,
                               region.view_location.copy(), region.view_rotation.copy(), region.view_distance,
                               tuple(region.view_camera_offset), region.view_camera_zoom)
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
        from .preflight import camera_setup_issue
        if issue := camera_setup_issue(s, allow_live=True):
            cls.poll_message_set(issue)
            return False
        return not s.running and not s.operation_busy and not s.job_directory

    def execute(self, context):
        source = source_scene(context.scene) or context.scene
        if is_preview(context.scene) and source == context.scene:
            self.report({'ERROR'}, 'The original scene was removed. Close this temporary preview to return to another scene.')
            return {'CANCELLED'}
        if is_preview(context.scene):
            camera_changed(source.lichtfeld_training, context)
            return {'FINISHED'}
        work = open_steps(source, context.window, context.area, prefer_selected=True)
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
        restore_source(context.window, scene, source)
        restore_window(context.window, context.area)
        remains_open = any(w.scene == scene for w in context.window_manager.windows)
        if not remains_open:
            remove(scene)
        if missing_source:
            message = ('The source scene was removed. The preview will be cleaned up when no window uses it.'
                       if remains_open else 'The source scene was removed. Returned to another scene and cleaned up the preview.')
            self.report({'WARNING'}, message)
        return {'FINISHED'}


class LF_OT_toggle_capture_camera(bpy.types.Operator):
    bl_idname = 'lichtfeld.toggle_capture_camera'
    bl_label = 'Change camera capture inclusion'
    bl_description = 'Include or exclude this camera from training and dataset capture without changing its pose'
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        from .preflight import camera_collections
        from .rig_edit import render_busy
        preview = context.scene
        source = source_scene(preview)
        if source is None or camera_pending(preview) or render_busy(context):
            return False
        settings = source.lichtfeld_training
        camera = preview.get(ORIGINAL_CAMERA)
        if settings.running or settings.operation_busy or settings.attached_session or settings.job_directory:
            return False
        return (isinstance(camera, bpy.types.Object) and camera.is_editable
                and source.objects.get(camera.name) == camera
                and any(group.all_objects.get(camera.name) == camera for group in camera_collections(settings)))

    def execute(self, context):
        from .preflight import CAMERA_EXCLUDED, camera_collections, invalidate
        preview = context.scene
        camera = preview[ORIGINAL_CAMERA]
        if not bpy.app.background and bpy.ops.ed.undo_push.poll():
            bpy.ops.ed.undo_push(message='Before changing capture inclusion')
        was_excluded = bool(camera.get(CAMERA_EXCLUDED))
        if was_excluded:
            del camera[CAMERA_EXCLUDED]
        else:
            camera[CAMERA_EXCLUDED] = True
        invalidate()
        sources = {source_scene(window.scene) for window in context.window_manager.windows}
        for source in sources - {None}:
            settings = source.lichtfeld_training
            if any(group.all_objects.get(camera.name) == camera for group in camera_collections(settings)):
                camera_changed(settings, context, refresh_only=True)
        return {'FINISHED'}


_test_viewer_owners = {}


def clear_test_viewers():
    _test_viewer_owners.clear()


def draw_test_progress(layout, settings):
    if settings.test_render_cancel:
        layout.label(text='Stopping test render…', icon='TIME')
    elif settings.test_render_samples:
        sample, total = settings.test_render_sample, settings.test_render_samples
        label = 'Finishing…' if sample == total else f'{sample}/{total} samples'
        layout.progress(type='BAR', factor=sample / total, text=label)
    else:
        layout.label(text='Rendering test image…', icon='TIME')


def draw_test_viewer_header(self, context):
    source = test_viewer_source(context)
    if source is None:
        return
    settings = source.lichtfeld_training
    if settings.test_render_active:
        draw_test_progress(self.layout, settings)
        row = self.layout.row()
        row.enabled = not settings.test_render_cancel
        row.operator('lichtfeld.cancel_capture_test', text='Cancel')
    elif settings.test_render_error:
        self.layout.label(text='Test render failed', icon='ERROR')


def find_test_viewer(manager, source):
    return next((window for window in manager.windows
                 if _test_viewer_owners.get(window.screen.session_uid) == source.session_uid), None)


def test_viewer_source(context):
    owner = _test_viewer_owners.get(context.window.screen.session_uid) if context.window else None
    if owner is not None:
        return next((scene for scene in bpy.data.scenes if scene.session_uid == owner), None)
    return None


def redraw_test_viewer(source):
    window = find_test_viewer(bpy.context.window_manager, source)
    if window:
        for area in window.screen.areas:
            if area.type == 'IMAGE_EDITOR':
                area.tag_redraw()


def show_test_viewer(context, source, image=None):
    screens = {screen.session_uid for screen in bpy.data.screens}
    for uid in _test_viewer_owners.keys() - screens:
        del _test_viewer_owners[uid]
    window = find_test_viewer(context.window_manager, source)
    if window is None:
        before = {item.as_pointer() for item in context.window_manager.windows}
        bpy.ops.wm.window_new_main('EXEC_DEFAULT')
        window = next((item for item in context.window_manager.windows if item.as_pointer() not in before), None)
    if window is None:
        raise RuntimeError('Blender could not open the Image Editor')
    if source_scene(window.scene) == source:
        restore_source(window, window.scene, source)
    elif window.scene != source:
        window.scene = source
    _test_viewer_owners[window.screen.session_uid] = source.session_uid
    area = max(window.screen.areas, key=lambda area: area.width * area.height)
    area.type = 'IMAGE_EDITOR'
    area.spaces.active.image = image
    area.tag_redraw()
    if len(window.screen.areas) > 1:
        with bpy.context.temp_override(window=window, area=area):
            bpy.ops.screen.screen_full_area(use_hide_panels=False)
        _test_viewer_owners[window.screen.session_uid] = source.session_uid


def test_image_path(image):
    return Path(bpy.path.abspath(image.filepath, library=image.library))


def load_test_image(path):
    image = next((item for item in bpy.data.images
                  if item.get('lichtfeld_test_image') and test_image_path(item) == Path(path)), None)
    if image is None:
        image = bpy.data.images.load(path, check_existing=False)
        image['lichtfeld_test_image'] = True
    image.use_view_as_render = False
    return image


def discard_previous_test_image(source, old_path):
    from .jobs import discard_superseded_test_image

    if any(scene != source and scene.lichtfeld_training.test_render_image
           and Path(bpy.path.abspath(scene.lichtfeld_training.test_render_image)) == old_path
           for scene in bpy.data.scenes):
        return
    for image in list(bpy.data.images):
        if not image.filepath or test_image_path(image) != old_path:
            continue
        # Image Editor can leave one extra real user after detaching the image.
        if image.get('lichtfeld_test_image') and image.users <= 1 and not image.use_fake_user:
            if not any(space.image == image for screen in bpy.data.screens for area in screen.areas
                       for space in area.spaces if space.type == 'IMAGE_EDITOR'):
                if not bpy.data.user_map(subset=(image,)).get(image):
                    bpy.data.images.remove(image)
                    continue
        if image.users:
            return
    ui_runtime.submit(lambda: discard_superseded_test_image(old_path))


def read_test_samples(root):
    from .jobs import read_json
    try:
        data = read_json(root / 'capture-progress.json', {})
        sample, total = data.get('sample'), data.get('samples')
        if type(sample) is int and type(total) is int and 0 <= sample <= total and total > 0:
            return sample, total
    except (OSError, ValueError, AttributeError):
        pass
    return None


def draw(layout, context, source, *, width=None):
    from . import draw_capture_format, draw_capture_overrides
    s = source.lichtfeld_training
    def wrapped(box, text):
        draw_paragraph(box, text, width if width is not None else context.region.width, context.preferences.system.ui_scale)
    layout.label(text='Capture framing', icon='VIEW_CAMERA')
    layout.operator('lichtfeld.close_capture_framing', icon='BACK')
    box = layout.box()
    camera = context.scene.get(ORIGINAL_CAMERA)
    box.label(text=camera.name if camera else context.scene.get('capture_camera_name', ''))
    from .rig_edit import render_busy
    box.enabled = not (render_busy(context) or s.operation_busy or s.running or s.job_directory)
    box.prop(context.window_manager.lichtfeld_panels, 'framing_camera', text='Camera number')
    from .preflight import CAMERA_EXCLUDED
    total = context.scene.get('capture_camera_count', 0)
    excluded = context.scene.get('capture_excluded_count', 0)
    if camera_pending(context.scene):
        box.label(text='Updating cameras…')
    elif excluded:
        included = total - excluded
        wrapped(box, f"{included:,} included · {excluded:,} excluded")
        if included < capture_settings.MIN_VIEWS:
            wrapped(box, f'Include at least {capture_settings.MIN_VIEWS} cameras to capture.')
    else:
        wrapped(box, f"{total:,} cameras, in name order")
    if camera and camera.get(CAMERA_EXCLUDED):
        box.label(text='Excluded from capture')
        box.operator('lichtfeld.toggle_capture_camera', text='Include again')
    else:
        box.operator('lichtfeld.toggle_capture_camera', text='Exclude from capture')
    draw_capture_format(box, source, s, wrapped)
    draw_capture_overrides(box, s, wrapped)
    box.operator('lichtfeld.capture_framing', text='Apply preview settings')
    if s.test_render_active:
        draw_test_progress(layout, s)
    elif s.test_render_error:
        wrapped(layout, s.test_render_error)
    row = layout.row(align=True)
    if s.test_render_active:
        row.operator('lichtfeld.cancel_capture_test')
    else:
        if bpy.data.filepath:
            row.operator('lichtfeld.render_capture_test', text='Render test')
        else:
            row.operator('lichtfeld.save_capture_source', text='Save scene…')
    if s.test_render_image:
        row.operator('lichtfeld.show_test_image', text='Last image')


def draw_orphaned(layout, _context):
    layout.label(text='The source scene was removed', icon='ERROR')
    layout.operator('lichtfeld.close_capture_framing', text='Close preview', icon='BACK')


def test_image_steps(context, *, source=None, window=None, area=None, framing=False):
    """Use the capture supervisor without binding/replacing a training job."""
    from . import preflight, capture_job, start_capture_test, finish_capture_test
    source = source if source is not None else context.scene
    settings = source.lichtfeld_training
    launch = None
    finish = None
    viewer_opened = False
    work = preflight.collect(context, capture_only=True, single_render=True)
    try:
        # A UI save clears Main's dirty flag through Blender's notifier loop.
        # Let that loop finish before freezing the validation signature; a
        # True -> False transition is the save completing, not a source edit.
        yield ui_runtime.WAIT
        initial = preflight.signature(context)
        if context.scene != source:
            raise ValueError('The active scene changed. Return to capture setup and try again.')
        while True:
            if settings.test_render_cancel:
                return
            try:
                next(work)
            except StopIteration as stop:
                capture = stop.value
                break
            yield
        if bpy.data.is_dirty or context.scene != source or initial != preflight.signature(context):
            raise ValueError('The capture setup changed. Save and try again.')
        if capture['errors']:
            raise ValueError(' '.join(capture['errors']))
        settings.capture_preview_index = min(settings.capture_preview_index, capture['camera_count'])
        job = capture_job(context, capture)
        job.update(purpose='test_render', capture_only=True)
        job.pop('connection_directory', None)
        job.pop('dataset_root', None)
        settings.test_render_active = True
        launch = ui_runtime.submit(lambda: start_capture_test(job))
        while not launch.done():
            yield ui_runtime.DEFER
        child, _ = launch.result()
        if context.scene != source or initial != preflight.signature(context):
            raise ValueError('The capture setup changed during launch. Save and try again.')
        if framing and any(item == window for item in bpy.context.window_manager.windows):
            try:
                target_area = area if any(item == area for item in window.screen.areas) else None
                yield from open_steps(source, window, target_area)
            except (ValueError, ReferenceError, RuntimeError) as error:
                ui_runtime.report_notice('WARNING', 'Could not restore framing: ' + str(error))
            finally:
                settings.operation_busy = True
        if (not bpy.app.background and not settings.test_render_cancel and
                any(item == window for item in bpy.context.window_manager.windows) and
                any(item == area for item in window.screen.areas)):
            try:
                with bpy.context.temp_override(window=window, area=area):
                    show_test_viewer(bpy.context, source)
                viewer_opened = True
            except RuntimeError as error:
                ui_runtime.report_notice('WARNING', str(error))
        root = Path(job['root'])
        update = None
        next_update = 0.0
        while child.poll() is None and not settings.test_render_cancel:
            if update is not None and update.done():
                value = update.result()
                if value and value != (settings.test_render_sample, settings.test_render_samples):
                    settings.test_render_sample, settings.test_render_samples = value
                    if viewer_opened:
                        redraw_test_viewer(source)
                update = None
            if update is None and time.monotonic() >= next_update:
                next_update = time.monotonic() + .25
                update = ui_runtime.submit(lambda: read_test_samples(root))
            yield ui_runtime.DEFER
        finish = ui_runtime.submit(lambda: finish_capture_test(launch))
        while not finish.done():
            yield ui_runtime.DEFER
        state = finish.result()
        if state.get('state') == 'stopped':
            ui_runtime.report_notice('INFO', 'Test render cancelled')
            return
        if state.get('state') != 'completed':
            raise RuntimeError(state.get('error', 'Test render failed. See render.log in ' + job['root']))
        path = str(root / state['image'])
        previous = settings.test_render_image
        settings.test_render_image = path
        if (not bpy.app.background and any(item == window for item in bpy.context.window_manager.windows)
                and (window.scene == source or source_scene(window.scene) == source)
                and any(item == area for item in window.screen.areas)
                and (not viewer_opened or find_test_viewer(bpy.context.window_manager, source))):
            with bpy.context.temp_override(window=window, area=area):
                bpy.ops.lichtfeld.show_test_image('EXEC_DEFAULT')
        if previous and previous != path:
            discard_previous_test_image(source, Path(bpy.path.abspath(previous)))
    except ReferenceError:
        return  # Source removal is cancellation, not a StructRNA error notice.
    finally:
        if launch is not None and finish is None:
            ui_runtime.submit(lambda: finish_capture_test(launch))
        work.close()
        try:
            settings.operation_busy = False
            settings.test_render_active = False
            settings.test_render_cancel = False
            settings.test_render_sample = settings.test_render_samples = 0
            if viewer_opened:
                viewer = find_test_viewer(bpy.context.window_manager, source)
                if viewer and settings.test_render_image:
                    area = next((area for area in viewer.screen.areas if area.type == 'IMAGE_EDITOR'), None)
                    if area and area.spaces.active.image is None:
                        try:
                            area.spaces.active.image = load_test_image(settings.test_render_image)
                        except (OSError, RuntimeError):
                            pass
                redraw_test_viewer(source)
        except ReferenceError:
            pass


class LF_OT_render_capture_test(bpy.types.Operator):
    bl_idname = 'lichtfeld.render_capture_test'
    bl_label = 'Render test image'
    bl_description = 'Save the source scene without temporary framing scenes, then render this camera with the dataset settings; no dataset or training is created'

    @classmethod
    def poll(cls, context):
        from .rig_edit import render_busy
        source = source_scene(context.scene)
        if source is None:
            return False
        s = source.lichtfeld_training
        if s.running or s.operation_busy or s.attached_session or render_busy(context):
            return False
        if not bpy.data.filepath:
            cls.poll_message_set('Save the scene once before rendering a test image')
            return False
        from .preflight import camera_collections
        return bool(camera_collections(s))

    def execute(self, context):
        if not self.poll(context):
            return {'CANCELLED'}
        source = source_scene(context.scene)
        # Saving is explicit. Remove owned framing scenes first so the saved
        # project contains the real setup, with no temporary camera or preview.
        cleanup()
        try:
            with bpy.context.temp_override(window=context.window, scene=source):
                if bpy.ops.wm.save_mainfile('EXEC_DEFAULT') != {'FINISHED'}:
                    return {'CANCELLED'}
        except RuntimeError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        source.lichtfeld_training.operation_busy = True
        source.lichtfeld_training.test_render_active = True
        source.lichtfeld_training.test_render_cancel = False
        source.lichtfeld_training.test_render_error = ''
        source.lichtfeld_training.test_render_sample = source.lichtfeld_training.test_render_samples = 0
        def failed(error):
            source.lichtfeld_training.test_render_error = str(error)
            redraw_test_viewer(source)
            ui_runtime.report_notice('ERROR', error)
        ui_runtime.schedule(test_image_steps(context, source=source, window=context.window, area=context.area, framing=True),
                            failed)
        return {'FINISHED'}


class LF_OT_save_capture_source(bpy.types.Operator):
    bl_idname = 'lichtfeld.save_capture_source'
    bl_label = 'Save source scene'
    bl_description = 'Return to the source scene and open Blender’s Save As dialog without saving temporary framing scenes'

    @classmethod
    def poll(cls, context):
        return source_scene(context.scene) is not None and LF_OT_close_capture_framing.poll(context)

    def execute(self, context):
        cleanup()
        result = bpy.ops.wm.save_as_mainfile('INVOKE_DEFAULT')
        return {'CANCELLED'} if result == {'CANCELLED'} else {'FINISHED'}


class LF_OT_cancel_capture_test(bpy.types.Operator):
    bl_idname = 'lichtfeld.cancel_capture_test'
    bl_label = 'Cancel test render'
    bl_description = 'Stop only the single-view test render; keep previous training jobs and images'

    @classmethod
    def poll(cls, context):
        source = test_viewer_source(context) or source_scene(context.scene) or context.scene
        return source is not None and source.lichtfeld_training.test_render_active

    def execute(self, context):
        source = test_viewer_source(context) or source_scene(context.scene) or context.scene
        source.lichtfeld_training.test_render_cancel = True
        redraw_test_viewer(source)
        return {'FINISHED'}


class LF_OT_show_test_image(bpy.types.Operator):
    bl_idname = 'lichtfeld.show_test_image'
    bl_label = 'View test image'
    bl_description = 'Open the last rendered test PNG in Blender’s Image Editor'

    @classmethod
    def poll(cls, context):
        source = source_scene(context.scene) or context.scene
        return context.window is not None and context.area is not None and not bpy.app.background and bool(
            source.lichtfeld_training.test_render_image)

    def execute(self, context):
        try:
            source = source_scene(context.scene) or context.scene
            path = source.lichtfeld_training.test_render_image
            show_test_viewer(context, source, load_test_image(path))
            return {'FINISHED'}
        except (OSError, RuntimeError, KeyError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


CLASSES = (LF_OT_toggle_capture_camera, LF_OT_capture_framing, LF_OT_close_capture_framing, LF_OT_render_capture_test,
           LF_OT_save_capture_source, LF_OT_cancel_capture_test, LF_OT_show_test_image)
