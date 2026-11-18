# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit camera diagnostics with one transient report; no scene state."""
import math
import time
from itertools import product
import bpy
import numpy as np
from bpy.props import BoolProperty, FloatProperty
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from . import preflight, rig_geometry, rig_edit, capture_preview, capture_settings
from .ui_text import draw_paragraph
from .visibility import object_steps

_active = None
_report = None
_report_region = None
_REPORT_WIDTH = 420
_SURFACE_BUDGET = 200_000


def draw_camera_row(layout, name, detail=''):
    row = layout.split(factor=.78)
    row.label(text=f'Camera: “{name}”{detail}')
    row.operator('lichtfeld.inspect_capture_camera', text='Select').camera_name = name


def clearance_steps(context, poses, distance):
    """Measure camera centres against the evaluated viewport mesh surfaces."""
    graph = context.evaluated_depsgraph_get()
    settings = context.scene.lichtfeld_training
    helpers = preflight.capture_helpers(settings)
    vertices, polygons = [], []
    corners = 0
    for obj in object_steps(preflight.capture_objects(context)):
        yield None
        if (obj is None or obj.type != 'MESH' or not preflight.is_surface(obj, helpers)
                or not obj.visible_get(view_layer=context.view_layer)):
            continue
        evaluated = obj.evaluated_get(graph)
        mesh = evaluated.data
        if not mesh.polygons:
            continue
        if len(vertices) + len(mesh.vertices) > _SURFACE_BUDGET or corners + len(mesh.loops) > _SURFACE_BUDGET:
            return [], 'Mesh budget exceeded. Narrow the capture scope to run this check.'
        local = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get('co', local)
        world = np.array(evaluated.matrix_world, dtype=np.float64)
        points = local.reshape(-1, 3) @ world[:3, :3].T + world[:3, 3]
        if not np.isfinite(points).all():
            return [], 'A mesh has invalid coordinates. Check its geometry and transform.'
        mesh.calc_loop_triangles()
        indices = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get('vertices', indices)
        triangles = indices.reshape(-1, 3)
        a, b, c = (points[triangles[:, i]] for i in range(3))
        usable = np.any(np.cross(b - a, c - a) != 0, axis=1)
        if usable.any():
            polygons.extend((triangles[usable] + len(vertices)).tolist())
            vertices.extend(points.tolist())
        corners += len(mesh.loops)
    if not polygons:
        return [], 'No visible mesh surfaces in the capture scope.'
    yield None
    tree = BVHTree.FromPolygons(vertices, polygons, all_triangles=True)
    del vertices, polygons
    hits = []
    for name, matrix in poses:
        centre = tuple(matrix[axis][3] for axis in range(3))
        _, _, _, nearest = tree.find_nearest(centre, distance)
        if nearest is not None and nearest < distance:
            hits.append((name, nearest))
        yield None
    hits.sort(key=lambda hit: hit[1])
    return hits, ''


def outside_frame(points, pose, projection, near, far):
    inverse = pose.inverted()
    camera_space = [inverse @ point for point in points]
    if all(-point.z < near for point in camera_space):
        return 'Before near clip'
    if all(-point.z > far for point in camera_space):
        return 'Beyond far clip'
    projected = [projection @ point.to_4d() for point in camera_space]
    if any(all(sign * point[axis] > point.w for point in projected)
           for axis, sign in product((0, 1), (-1, 1))):
        return 'Outside image'
    return ''


def framing_steps(context, findings):
    settings = context.scene.lichtfeld_training
    reference = settings.camera_collection.lichtfeld_rig_reference
    if reference.mode == 'UNSET':
        return None, 'The camera collection has no applied rig reference.'
    try:
        output = capture_settings.format_for(context.scene, settings)
        overrides = capture_settings.camera_for(settings)
        if settings.override_optics or settings.override_shift:
            return None, 'Use Check framing to inspect capture lens or shift overrides.'
        sources = yield from rig_edit.reference_sources(context, reference)
        low, high = yield from rig_edit.reference_bounds_steps(sources, context.evaluated_depsgraph_get())
    except ValueError as error:
        return None, str(error)
    if not all(math.isfinite(value) for value in (*low, *high)):
        return None, 'The applied rig reference has invalid bounds.'
    points = tuple(Vector(corner) for corner in product(*zip(low, high)))

    def inspect(camera, graph, pose):
        evaluated = camera.evaluated_get(graph)
        projection = capture_settings.camera_projection(evaluated, graph, output)
        near = overrides.get('clip_start', evaluated.data.clip_start)
        far = overrides.get('clip_end', evaluated.data.clip_end)
        reason = outside_frame(points, pose, projection, near, far)
        if reason:
            findings.append((camera.name, reason))

    return inspect, ''


def analyze_steps(context, clearance=0.0, framing=False):
    if not math.isfinite(clearance):
        raise ValueError('Minimum distance must be finite')
    signature = preflight.signature(context)
    scene, frame = context.scene.name, context.scene.frame_current
    outside, framing_note = [], ''
    inspect = None
    if framing and context.scene.lichtfeld_training.camera_collection:
        inspect, framing_note = yield from framing_steps(context, outside)
    checks = yield from preflight.camera_checks(context, poses=True, inspect=inspect)
    if not checks['camera_count']:
        raise ValueError('Choose a collection containing cameras')
    matches = yield from rig_geometry.repeated_poses([matrix for name, matrix in checks['poses']])
    nearby, clearance_note = [], ''
    if clearance > 0 and checks['poses']:
        yield 'Checking distance to mesh surfaces — Esc to cancel'
        nearby, clearance_note = yield from clearance_steps(context, checks['poses'], clearance)
    elif clearance > 0:
        clearance_note = 'No valid camera centres to check.'
    if signature != preflight.signature(context):
        raise ValueError('The capture setup changed during the check; run it again')
    return dict(checks, matches=matches, scene=scene, frame=frame,
                clearance=clearance, nearby=nearby, clearance_note=clearance_note,
                framing=framing, outside=outside, framing_note=framing_note)


def last_result(context):
    collection = context.scene.lichtfeld_training.camera_collection
    if (_report is not None and collection is not None and
            _report['scene_id'] == context.scene.session_uid and
            _report['collection_id'] == collection.session_uid and
            _report['frame'] == context.scene.frame_current):
        return _report
    return None


def show_result(context, result):
    global _report
    names = [name for name, matrix in result['poses']]
    _report = dict(scene_id=context.scene.session_uid,
                   collection_id=context.scene.lichtfeld_training.camera_collection.session_uid,
                   frame=result['frame'],
                   clearance=result['clearance'], nearby=result['nearby'],
                   clearance_note=result['clearance_note'],
                   framing=result['framing'], outside=result['outside'], framing_note=result['framing_note'],
                   repeats=[(names[first], names[repeated]) for first, repeated in result['matches']])
    refresh_report()


def refresh_report():
    global _report_region
    if _report_region is not None:
        try:
            _report_region.tag_refresh_ui()
        except ReferenceError:
            _report_region = None


def report_options_changed(self, context):
    refresh_report()


class LF_OT_camera_report(bpy.types.Operator):
    bl_idname = 'lichtfeld.camera_report'
    bl_label = 'Camera checks'
    bl_description = 'Review camera issues and check repeated poses or distance to mesh surfaces'
    clearance: FloatProperty(name='Minimum distance', subtype='DISTANCE', unit='LENGTH', min=0.0,
        default=0.0, options={'SKIP_SAVE'}, update=report_options_changed,
        description='Flag camera centres closer than this distance to visible evaluated meshes in the capture scope. Zero disables this check')
    framing: BoolProperty(name='Check rig reference framing', options={'SKIP_SAVE'},
        update=report_options_changed,
        description='Flag views where the applied rig reference bounds are entirely outside the image or clipping range')

    @classmethod
    def poll(cls, context):
        return bool(context.scene.lichtfeld_training.camera_collection) and not capture_preview.is_preview(context.scene)

    def invoke(self, context, event):
        report = last_result(context)
        if report is not None and not self.properties.is_property_set('clearance'):
            self.clearance = report['clearance']
        if report is not None and not self.properties.is_property_set('framing'):
            self.framing = report['framing']
        return context.window_manager.invoke_popup(self, width=_REPORT_WIDTH)

    def execute(self, context):
        self.cancel(context)
        return {'FINISHED'}

    def cancel(self, context):
        global _report_region
        _report_region = None

    def draw(self, context):
        global _report_region
        _report_region = context.region_popup
        scale = context.preferences.system.ui_scale
        # Blender can draw before assigning the popup its final region width.
        width = _REPORT_WIDTH * scale
        layout = self.layout
        layout.label(text='Camera checks')
        layout.separator()
        ready = preflight.camera_status(context)
        report = last_result(context)
        issues = list(ready['camera_issues'].items()) if ready is not None else None
        if issues is None:
            layout.label(text='Camera issues: checking…')
        else:
            layout.label(text=f'Camera issues: {len(issues):,}')
            for message, name in issues[:8]:
                body = layout.column(align=True)
                draw_camera_row(body, name)
                reason = message.removeprefix(f'Camera {name}: ')
                draw_paragraph(body, f'Issue: {reason}', width, scale)
            if len(issues) > 8:
                layout.label(text=f'Showing 8 of {len(issues):,} issues')

        layout.separator()
        if report is None:
            layout.label(text='Repeated poses: not checked')
        else:
            repeats = report['repeats']
            layout.label(text=f'Repeated poses (last check): {len(repeats):,}')
            for index, (first, repeated) in enumerate(repeats[:8], start=1):
                pair = layout.box().column(align=True)
                pair.label(text=f'Pair {index}: same position and orientation' if len(repeats) > 1
                           else 'Same position and orientation')
                draw_camera_row(pair, first)
                draw_camera_row(pair, repeated)
            if len(repeats) > 8:
                layout.label(text=f'Showing 8 of {len(repeats):,} matches')
        layout.separator()
        layout.prop(self, 'clearance')
        layout.label(text='Viewport meshes in capture scope · 0 disables')
        if report is not None and report['clearance'] > 0:
            if report['clearance_note']:
                draw_paragraph(layout, report['clearance_note'], width, scale)
            else:
                nearby = report['nearby']
                layout.label(text=f'Near a surface (last check): {len(nearby):,}')
                for name, distance in nearby[:5]:
                    draw_camera_row(layout, name, ' · ' + format_distance(context.scene, distance))
                if len(nearby) > 5:
                    layout.label(text='Showing the 5 closest cameras')
            layout.label(text='Checked distance: ' + format_distance(context.scene, report['clearance']))
        layout.prop(self, 'framing')
        if report is not None and report['framing']:
            if report['framing_note']:
                draw_paragraph(layout, report['framing_note'], width, scale)
            else:
                outside = report['outside']
                layout.label(text=f'Reference outside frame (last check): {len(outside):,}')
                for name, reason in outside[:5]:
                    draw_camera_row(layout, name, ' · ' + reason)
                if len(outside) > 5:
                    layout.label(text=f'Showing 5 of {len(outside):,} cameras')
        row = layout.row()
        row.alignment = 'RIGHT'
        check = row.operator('lichtfeld.check_camera_poses', text='Run again' if report is not None else 'Run check')
        check.clearance = self.clearance
        check.framing = self.framing


def format_distance(scene, distance):
    units = scene.unit_settings
    if units.system == 'NONE':
        return f'{distance:.4g} BU'
    return bpy.utils.units.to_string(units.system, 'LENGTH', distance * units.scale_length, precision=4)



def is_running():
    return _active is not None


def clear():
    global _report, _report_region
    _report = None
    _report_region = None
    if _active is not None:
        _active.release()


class LF_OT_check_poses(bpy.types.Operator):
    bl_idname = 'lichtfeld.check_camera_poses'
    clearance: FloatProperty(min=0.0, default=0.0, options={'HIDDEN', 'SKIP_SAVE'})
    framing: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})
    bl_label = 'Check camera poses'
    bl_description = 'Check evaluated camera poses, with optional surface distance and reference framing checks'

    @classmethod
    def poll(cls, context):
        if _active is not None or context.mode != 'OBJECT' or capture_preview.is_preview(context.scene):
            return False
        s = context.scene.lichtfeld_training
        return bool(s.camera_collection) and not s.running and not s.operation_busy and not rig_edit.render_busy(context) and not rig_edit.live_root(s)

    def release(self):
        global _active
        if self.work is not None:
            self.work.close()
            self.work = None
        if self.timer is not None:
            try:self.manager.event_timer_remove(self.timer)
            except (ReferenceError, RuntimeError):pass
            self.timer = None
        try:
            if self.workspace is not None:self.workspace.status_text_set(None)
        except ReferenceError:pass
        if _active == self:_active = None

    def execute(self, context):
        global _active
        self.scene = context.scene
        self.frame = self.scene.frame_current
        self.manager = context.window_manager
        self.workspace = context.workspace
        self.work = analyze_steps(context, self.clearance, self.framing)
        self.timer = None
        if bpy.app.background:
            try:
                while True:next(self.work)
            except StopIteration as done:
                result=done.value
                show_result(context, result)
                self.report({'INFO'}, f"{len(result['matches']):,} repeated camera poses; {len(result['camera_issues']):,} pose issues")
                return {'FINISHED'}
            except (ValueError, RuntimeError) as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}
            finally:self.release()
        _active = self
        self.workspace.status_text_set('Checking cameras — Esc to cancel')
        self.timer = self.manager.event_timer_add(.02, window=context.window)
        self.manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if self.work is None:return {'CANCELLED'}
        if (event.type in {'ESC', 'RIGHTMOUSE'} or context.scene != self.scene or
                context.scene.frame_current != self.frame or
                context.scene.lichtfeld_training.operation_busy or rig_edit.render_busy(context)):
            self.release()
            return {'CANCELLED'}
        if event.type == 'TIMER':
            try:
                deadline=time.perf_counter()+.010
                progress=None
                while time.perf_counter()<deadline:
                    step=next(self.work)
                    if step is not None:progress=step
                if isinstance(progress, str):
                    self.workspace.status_text_set(progress)
                elif progress is not None:
                    self.workspace.status_text_set(f'Checking cameras {progress[0]:,}/{progress[1]:,} — Esc to cancel')
            except StopIteration as done:
                self.release()
                if done.value is not None:show_result(context, done.value)
                return {'FINISHED'}
            except Exception as error:
                self.release()
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        if hasattr(self, 'work'):self.release()


CLASSES = (LF_OT_check_poses, LF_OT_camera_report)
