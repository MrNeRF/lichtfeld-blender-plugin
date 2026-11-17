# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit, read-only pose diagnostics; no result history or scene state."""
import time
import bpy
from . import preflight, rig_geometry, rig_edit, capture_preview
from .ui_text import draw_paragraph

_active = None


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


def show_result(context, result):
    names = [name for name, frame in result['poses']]
    issues = list(result['camera_issues'].items())
    matches = result['matches']
    scene, frame, count = result['scene'], result['frame'], result['camera_count']
    scale = context.preferences.system.ui_scale
    def draw(menu, _context):
        layout = menu.layout
        layout.label(text=f'{scene} · frame {frame} · {count:,} cameras')
        layout.label(text=f'{len(issues):,} pose issues; {len(matches):,} repeated poses')
        for message, name in issues[:4]:
            draw_paragraph(layout, message, 420, scale)
            layout.operator('lichtfeld.inspect_capture_camera', text='Select ' + name).camera_name = name
        for first, repeated in matches[:8]:
            layout.operator('lichtfeld.inspect_capture_camera', text=names[repeated] + ' — same pose as ' + names[first]).camera_name = names[repeated]
        remaining = max(0,len(issues)-4) + max(0,len(matches)-8)
        if remaining:
            layout.label(text=f'{remaining:,} more matches/issues; inspect the listed cameras and rerun')
        layout.label(text='Exact poses only; images may differ with different optics')
        layout.label(text='No cameras changed. Rerun after scene edits')
    context.window_manager.popup_menu(draw, title='Camera pose check')


def is_running():
    return _active is not None


def clear():
    if _active is not None:
        _active.release()


class LF_OT_check_poses(bpy.types.Operator):
    bl_idname = 'lichtfeld.check_camera_poses'
    bl_label = 'Check camera poses'
    bl_description = 'Find invalid or repeated evaluated camera poses on request; no cameras are changed and image coverage is not assessed'

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


CLASSES = (LF_OT_check_poses,)
