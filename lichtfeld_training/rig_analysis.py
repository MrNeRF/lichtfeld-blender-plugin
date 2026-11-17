# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit pose diagnostics with one transient repeated-pose snapshot; no scene state."""
import time
import bpy
from bpy.props import BoolProperty
from . import preflight, rig_geometry, rig_edit, capture_preview
from .ui_text import draw_disclosure, draw_paragraph

_active = None
_report = None


def analyze_steps(context):
    signature = preflight.signature(context)
    scene, frame = context.scene.name, context.scene.frame_current
    checks = yield from preflight.camera_checks(context, poses=True)
    if not checks['camera_count']:
        raise ValueError('Choose a collection containing cameras')
    matches = yield from rig_geometry.repeated_poses([matrix for name, matrix in checks['poses']])
    if signature != preflight.signature(context):
        raise ValueError('The capture setup changed during the check; run it again')
    return dict(checks, matches=matches, scene=scene, frame=frame)


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
                   frame=result['frame'], count=result['camera_count'],
                   repeats=[(names[first], names[repeated]) for first, repeated in result['matches']])
    bpy.ops.lichtfeld.camera_report('INVOKE_DEFAULT')


class LF_OT_camera_report(bpy.types.Operator):
    bl_idname = 'lichtfeld.camera_report'
    bl_label = 'Camera checks'
    bl_description = 'Review current camera issues and the last explicit repeated-pose check'
    issues_open: BoolProperty(name='Camera issues', default=False, options={'SKIP_SAVE'},
        description='Expand invalid camera settings and select a camera to inspect')
    repeats_open: BoolProperty(name='Repeated poses', default=False, options={'SKIP_SAVE'},
        description='Expand cameras with exactly matching evaluated position and orientation; different optics may still produce different images')

    @classmethod
    def poll(cls, context):
        return bool(context.scene.lichtfeld_training.camera_collection) and not capture_preview.is_preview(context.scene)

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=420)

    def draw(self, context):
        layout = self.layout
        layout.label(text='Camera checks')
        layout.separator()
        ready = preflight.camera_status(context)
        report = last_result(context)
        issues = list(ready['camera_issues'].items()) if ready is not None else None
        groups = (('issues_open', 'Camera issues', issues),
                  ('repeats_open', 'Repeated poses', report['repeats'] if report is not None else None))
        for field, title, entries in groups:
            if entries is None:
                layout.label(text=title + (': checking…' if field == 'issues_open' else ': not checked'))
                continue
            if not entries:
                layout.label(text=title + ': 0')
                continue
            body = draw_disclosure(layout, field, f'{title} ({len(entries):,})', nested=False, owner=self)
            if body is None:continue
            if field == 'repeats_open':
                body.label(text=f"Last check · frame {report['frame']} · {report['count']:,} cameras")
            for first, name in entries[:8]:
                row = body.split(factor=.78)
                row.label(text=name)
                row.operator('lichtfeld.inspect_capture_camera', text='Select').camera_name = name
                detail = first.removeprefix(f'Camera {name}: ') if field == 'issues_open' else 'Same pose: ' + first
                region = context.region_popup or context.region
                draw_paragraph(body, detail, region.width, context.preferences.system.ui_scale)
                body.separator(factor=.35)
            if len(entries)>8:body.label(text=f'First 8 of {len(entries):,}; inspect and check again')
        layout.separator()
        row = layout.row()
        row.alignment = 'RIGHT'
        row.operator('lichtfeld.check_camera_poses', text='Run again' if report is not None else 'Run check')



def is_running():
    return _active is not None


def clear():
    global _report
    _report = None
    if _active is not None:
        _active.release()


class LF_OT_check_poses(bpy.types.Operator):
    bl_idname = 'lichtfeld.check_camera_poses'
    bl_label = 'Check camera poses'
    bl_description = 'Run a fresh check for exactly repeated evaluated poses; no cameras are changed and image coverage is not assessed'

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
        self.work = analyze_steps(context)
        self.timer = None
        if bpy.app.background:
            try:
                while True:next(self.work)
            except StopIteration as done:
                result=done.value
                self.report({'INFO'}, f"{len(result['matches']):,} repeated camera poses; {len(result['camera_issues']):,} pose issues")
                return {'FINISHED'}
            except (ValueError, RuntimeError) as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}
            finally:self.release()
        _active = self
        self.workspace.status_text_set('Checking camera poses — Esc to cancel')
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
                if progress is not None:
                    self.workspace.status_text_set(f'Checking camera poses {progress[0]:,}/{progress[1]:,} — Esc to cancel')
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
