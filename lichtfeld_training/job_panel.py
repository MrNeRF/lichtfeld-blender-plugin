# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable job controls: snapshot staging is deliberately absent from UI policy."""
from pathlib import Path
from . import workflow, scene_render
from .ui_text import draw_disclosure


def draw(layout, context, settings, status, reader_error, wrapped, color_view=False, status_error=False):
    external = bool(settings.attached_session)
    capture_only = status.get("job", {}).get("capture_only", False)
    phase = workflow.phase(status.get("state", settings.status))
    capture = phase in workflow.CAPTURE_PHASES
    busy = settings.operation_busy
    result = settings.result_object
    best = status.get("best") or status.get("result")
    flags = workflow.actions(phase, external=external, result=bool(result), best=bool(best),
                             checkpoint=bool(status.get("checkpoint_available")), busy=busy,
                             control=status.get("control_available", False))
    job = layout.column()
    command = "lichtfeld.color_view_command" if color_view else "lichtfeld.training_command"
    heading = ("Training continues · viewing only" if phase == 'controller lost' and status.get('backend_alive') else
               workflow.status_label(phase))
    job.label(text="Connected trainer · Read only" if external else
                   "Dataset export" if capture_only else heading, icon="LINKED" if external else "RENDER_STILL")
    if external or capture_only:
        job.label(text=workflow.status_label(phase))
    job_error = status.get('error') or (settings.detail if not status and phase == 'failed' else '')
    if job_error:
        job.label(text='Job needs attention', icon='ERROR')
        wrapped(job, job_error)
    if not capture_only and phase in workflow.TERMINAL and 'iteration' in status:
        job.label(text=f"Reached step {int(status['iteration']):,}")
    if settings.running:
        step = int(status.get("iteration", 0))
        total = int(status.get("iterations", status.get("job", {}).get("iterations", 0)))
        count = status.get("capture", {})
        if capture:
            text = f"View {count.get('done', 0)} / {count.get('total', len(status.get('job', {}).get('cameras', [])))}"
        elif phase in workflow.STARTUP_PHASES or not total:
            text = 'Preparing…'
        else:
            text = f"Step {step:,} / {total:,}"
        job.progress(factor=settings.progress, type="BAR", text=text)
        if capture and not external:
            render = status.get('capture_render', {})
            requested = render.get('requested') or status.get('job', {}).get('render_settings')
            legacy = status.get('job', {})
            lines = scene_render.summary_lines(requested, render.get('effective'))
            if not requested and legacy.get('engine'):
                lines = (f"{legacy['engine']} · legacy capture", 'Device details were not recorded by this job',
                         f"{legacy.get('samples', 0)} samples")
            for line in lines:
                job.label(text=line)
        if phase in workflow.TRAINING_PHASES and not external:
            row = job.row(align=True)
            row.enabled = flags["pause"]
            row.operator(command, text="Resume" if settings.paused else "Pause", icon="PAUSE").action = "resume" if settings.paused else "pause"
            row.operator(command, text="Stop and save", icon="CANCEL").action = "stop"
        elif not external and (capture or phase in workflow.STARTUP_PHASES):
            row = job.row()
            row.enabled = flags["stop"]
            row.operator(command, text="Cancel capture" if capture else "Cancel startup", icon="CANCEL").action = "stop"
    if not capture_only and (not capture or result or external):
        header, preview = layout.panel("lichtfeld_job_preview", default_closed=False)
        header.label(text="Preview")
        if preview is not None:
            preview.label(text=(f"Shown: step {int(result.get('lichtfeld_iteration', 0)):,} · {int(result.get('lichtfeld_count', 0)):,} points"
                                if result else "No preview was produced" if phase in workflow.TERMINAL else "Waiting for the first preview…"))
            quality = "No saved model is available" if phase in workflow.TERMINAL else "Updates arrive automatically"
            if result:
                kind = result.get('lichtfeld_kind', 'preview')
                if status.get("preview_fallback") and kind == "preview":
                    quality = f"Auto limited · {int(result.get('lichtfeld_count', 0)):,} points"
                else:
                    quality = ('Best evaluated model' if kind == 'best' else 'Saved model' if kind != 'preview' else 'Shared preview' if external else
                               'Full preview' if status.get('job', {}).get('full_preview', settings.full_preview) else 'Limited preview')
                quality += f" · SH {int(result.get('lichtfeld_sh_degree', 0))}"
            elif status.get("preview_fallback"):
                quality = "Preview size limited automatically"
            preview.label(text=quality)
            if phase in workflow.TRAINING_PHASES and not external:
                row = preview.row(align=True)
                row.enabled = not busy
                row.operator("lichtfeld.model_view", text="Current", depress=status.get("viewer_mode") != "best").mode = "CURRENT"
                best_row = row.row(align=True)
                best_row.enabled = bool(best)
                best_row.operator("lichtfeld.model_view", text="Best evaluated", depress=status.get("viewer_mode") == "best").mode = "BEST"
                row = preview.row()
                row.enabled = flags["previews"]
                row.operator(command, text="Freeze preview" if settings.previews_enabled else "Resume preview", icon="PAUSE" if settings.previews_enabled else "PLAY").action = "preview_off" if settings.previews_enabled else "preview_on"
            if not color_view:
                row = preview.row(align=True)
                row.enabled = bool(result) and not busy
                row.operator("lichtfeld.compare_result", text="Show source" if settings.show_result else "Show splat", icon="HIDE_OFF")
                row.operator("lichtfeld.trainer_color_view", text="Trainer colors", icon="SHADING_RENDERED")
            if external and settings.session_camera_count:
                preview.prop(settings, "show_session_cameras", text=f"Capture cameras ({settings.session_camera_count})")
            preview_error = status.get('preview_error') or (reader_error if not status_error else '')
            if preview_error:
                preview.label(text='Preview update needs attention', icon='ERROR')
                wrapped(preview, preview_error)
                if settings.running:
                    wrapped(preview, 'Preview updates retry automatically.')
    if external:
        wrapped(layout, "Read-only preview. Disconnecting or closing Blender leaves this trainer running.")
        if settings.previous_job:
            wrapped(layout, "After disconnecting, choose Back to previous job to resume your earlier job view.")
        if not color_view:
            layout.operator("lichtfeld.detach_session", text="Disconnect preview", icon="UNLINKED")
    elif not settings.running and not color_view:
        row = layout.column(align=True)
        row.enabled = not busy
        row.scale_y = 1.2
        if capture_only and status.get("export_directory"):
            row.label(text="Dataset ready · training was not started", icon="CHECKMARK")
            row.operator("lichtfeld.open_job", text="Open dataset folder", icon="FILE_FOLDER").dataset = True
        reuse = row.row()
        reuse.enabled = status.get('dataset_ready', phase == 'completed' or bool(best))
        reuse.operator("lichtfeld.use_dataset", text="Train these images" if capture_only else "Train again · same images", icon="FILE_REFRESH")
        if flags["continue"]:
            row.operator("lichtfeld.resume_job", text="Continue from checkpoint…", icon="RECOVER_LAST")
        row.operator("lichtfeld.new_capture", text="Capture new images", icon="RENDER_STILL")
        if status.get('dataset_ready') is False:
            wrapped(layout, 'This job has no complete dataset. Capture again with the current setup.')
    details = draw_disclosure(layout, "job_details", "Details" if color_view else "Details and files")
    if details is not None:
        recorded = status.get('job', {})
        size = recorded.get('capture_format', {})
        if recorded.get('cameras') or (size.get('width') and size.get('height')) or (not capture_only and recorded.get('iterations')):
            details.label(text='Settings used for this job', icon='PRESET')
            if recorded.get('cameras'):
                details.label(text=f"{len(recorded['cameras']):,} capture cameras")
            if size.get('width') and size.get('height'):
                details.label(text=f"{size['width']} × {size['height']} px")
            if not capture_only and recorded.get('iterations'):
                details.label(text=f"Training budget: {recorded['iterations']:,} steps")
        render = status.get('capture_render', {})
        if render:
            details.label(text='Capture render devices', icon='RENDER_STILL')
            for line in scene_render.summary_lines(render.get('requested'), render.get('effective')):
                wrapped(details, line)
            if render.get('error'):
                wrapped(details, str(render['error'])[:2048])
        if settings.detail:
            wrapped(details, settings.detail)
        if status.get("preview_note"):
            wrapped(details, status["preview_note"])
        if status.get("geometry_error"):
            wrapped(details, "Capture cameras: " + status["geometry_error"] + ". Model updates continue; camera metadata retries automatically.")
        details.label(text="Session: " + Path(settings.attached_session or settings.job_directory).name[:8])
        if result:
            details.prop(settings, "preview_hold_seconds", text="Preview settling (s)")
            wrapped(details, "Trainer colors provides a separate navigable view for color comparison.")
            if not color_view:
                details.operator("lichtfeld.capture_lighting", text="Use capture lighting", icon="LIGHT")
        if flags["import"] and not color_view:
            details.operator("lichtfeld.import_result", text="Load best saved model", icon="IMPORT")
        if not external and not color_view:
            details.operator("lichtfeld.open_job", text="Open job folder", icon="FILE_FOLDER")
            if not settings.running:
                details.operator("lichtfeld.load_job", text="Open another job…", icon="FILEBROWSER")
    if not external and settings.running and status.get('control_available') is False:
        warning = layout.column()
        warning.label(text='Training controls unavailable', icon='LOCKED')
        wrapped(warning, status.get('control_reason') or 'Monitoring remains connected; controls will recover automatically when the job supervisor responds.')
    if reader_error and status_error:
        warning = layout.column()
        warning.label(text="Monitoring needs attention", icon="ERROR")
        wrapped(warning, reader_error)
