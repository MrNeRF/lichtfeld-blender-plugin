# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture framing in an isolated scene; source cameras/render settings stay intact."""
import bpy
from . import capture_settings, ui_runtime
from .ui_text import draw_paragraph
from .visibility import object_steps

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


def prepare_camera(source, window, *, prefer_selected=False):
    """Use the capture copy contract for both opening and changing a view."""
    settings = source.lichtfeld_training
    scene = window.scene
    from .preflight import camera_objects
    cameras = yield from camera_objects(source, settings)
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
            except ReferenceError:
                work.close()
                work = None
                yield ui_runtime.DEFER
                continue
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
        camera, name, count, index, value = yield from prepare_camera(source, window, prefer_selected=prefer_selected)
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
        from .preflight import camera_collections
        return bool(camera_collections(s)) and not s.running and not s.operation_busy and not s.job_directory

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
    if s.test_render_active:
        layout.label(text='Rendering test image…')
    elif s.test_render_error:
        wrapped(layout, s.test_render_error)
    row = layout.row(align=True)
    if s.test_render_active:
        row.operator('lichtfeld.cancel_capture_test')
    else:
        if bpy.data.filepath:
            row.operator('lichtfeld.render_capture_test', text='Save and render test image')
        else:
            row.operator('lichtfeld.save_capture_source', text='Save scene…')
    if s.test_render_image:
        row.operator('lichtfeld.show_test_image', text='View last')


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
        while child.poll() is None and not settings.test_render_cancel:
            yield ui_runtime.DEFER
        finish = ui_runtime.submit(lambda: finish_capture_test(launch))
        while not finish.done():
            yield ui_runtime.DEFER
        state = finish.result()
        if state.get('state') == 'stopped':
            return
        if state.get('state') != 'completed':
            raise RuntimeError(state.get('error', 'Test render failed. See render.log in ' + job['root']))
        from pathlib import Path
        path = str(Path(job['root']) / state['image'])
        settings.test_render_image = path
        if (not bpy.app.background and any(item == window for item in bpy.context.window_manager.windows)
                and (window.scene == source or source_scene(window.scene) == source)
                and any(item == area for item in window.screen.areas)):
            with bpy.context.temp_override(window=window, area=area):
                bpy.ops.lichtfeld.show_test_image('EXEC_DEFAULT')
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
        def failed(error):
            source.lichtfeld_training.test_render_error = str(error)
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
        source = source_scene(context.scene) or context.scene
        return source is not None and source.lichtfeld_training.test_render_active

    def execute(self, context):
        source = source_scene(context.scene) or context.scene
        source.lichtfeld_training.test_render_cancel = True
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
            image = next((item for item in bpy.data.images if item.get('lichtfeld_test_image') and item.users <= 1), None)
            if image is None:
                image = bpy.data.images.load(path, check_existing=True)
                image['lichtfeld_test_image'] = True
            elif image.filepath != path:
                image.filepath = path
                image.reload()
            image.use_view_as_render = False
            window = next((item for item in context.window_manager.windows if item.screen.get('lichtfeld_test_viewer')), None)
            if window != context.window:
                if window is not None:
                    with bpy.context.temp_override(window=window):
                        bpy.ops.wm.window_close()
                before = {window.as_pointer() for window in context.window_manager.windows}
                # Area-duplicate windows inherit their parent's scene. A main
                # window keeps the image viewer independent from framing.
                bpy.ops.wm.window_new_main('EXEC_DEFAULT')
                window = next((window for window in context.window_manager.windows if window.as_pointer() not in before), None)
            if window is None:
                raise RuntimeError('Blender could not open the Image Editor')
            framing_source = source_scene(window.scene)
            if framing_source is not None:
                restore_source(window, window.scene, framing_source)
            window.screen['lichtfeld_test_viewer'] = True
            area = max(window.screen.areas, key=lambda area: area.width * area.height)
            area.type = 'IMAGE_EDITOR'
            area.spaces.active.image = image
            if len(window.screen.areas) > 1:
                with bpy.context.temp_override(window=window, area=area):
                    bpy.ops.screen.screen_full_area(use_hide_panels=True)
                window.screen['lichtfeld_test_viewer'] = True
            return {'FINISHED'}
        except (OSError, RuntimeError, KeyError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


CLASSES = (LF_OT_capture_framing, LF_OT_close_capture_framing, LF_OT_render_capture_test,
           LF_OT_save_capture_source, LF_OT_cancel_capture_test, LF_OT_show_test_image)
