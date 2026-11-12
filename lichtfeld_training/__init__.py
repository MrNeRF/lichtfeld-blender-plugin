# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Installable LichtFeld Training extension for Blender 5.3."""

import math
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
import textwrap
import uuid
import logging

import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty, FloatVectorProperty)
from mathutils import Matrix, Vector

from .bridge_core import (SCHEMA, TERMINAL, atomic_json, read_json, verify_artifact, process_alive)
from lichtfeld_sessions.transport import read_snapshot, discover, SessionClient
from .visibility import set_hidden, source_steps
from .ui_text import draw_paragraph
from .session_geometry import source_model_transform
from . import ui_runtime
from . import jobs, preflight, workflow, rig_geometry, rig_path, rig_edit, training_options, preview_scene, capture_settings, capture_preview, scene_render, ui_help

_PACKAGE = __package__
_ROOT = Path(__file__).resolve().parent
_imported = {}
_clients = {}
_sessions = []
_SESSION_PLACEHOLDER = [("NONE", "Refresh to discover trainers", "Refresh the list to find compatible trainers that are currently running")]
_session_items = _SESSION_PLACEHOLDER
_session_scan_pending = False


def refresh_sessions():
    global _session_scan_pending
    if _session_scan_pending:
        return
    _session_scan_pending = True
    def done(values):
        global _sessions, _session_items, _session_scan_pending
        _session_scan_pending = False
        items = [(item["id"], f"{item.get('label', 'Trainer')} · PID {item['pid']}",
                  item.get("project") or item.get("job_root") or item.get("dataset", "")) for item in values]
        items = items or [("NONE", "No active trainers", "No compatible live trainer was found on this computer")]
        changed = items != _session_items
        _sessions = values
        if changed:
            previous = [(scene, scene.lichtfeld_training.discovered_session) for scene in bpy.data.scenes]
            _session_items = items
            valid = {item[0] for item in items}
            for scene, selected in previous:
                settings = scene.lichtfeld_training
                settings.discovered_session = selected if selected in valid else items[0][0]
            for screen in bpy.data.screens:
                for area in screen.areas:
                    if area.type == "VIEW_3D": area.tag_redraw()
    def failed(error):
        done([])
        logging.getLogger(__name__).warning("Trainer discovery: %s", error)
    ui_runtime.submit(discover, done, failed)


def session_items(_self, _context):
    return _session_items


def session_camera_visibility(_settings, context):
    from .session_cameras import visibility_steps
    ui_runtime.schedule(visibility_steps(_settings.id_data))


def preferences(context=None):
    return (context or bpy.context).preferences.addons[_PACKAGE].preferences


def python_binary():
    prefix = Path(sys.prefix)
    for candidate in (prefix / "bin/python.exe", prefix / "bin/python3.13",
                      prefix / "bin/python3", prefix / "python.exe"):
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("Blender's bundled Python interpreter could not be found")


def process_options(detach=False):
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW
        if detach:
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        return {"creationflags": flags}
    return {"start_new_session": detach}


def sources_for(context):
    settings = context.scene.lichtfeld_training
    if settings.scope == 'COLLECTION' and not settings.source_collection:
        raise ValueError('Choose a source collection')
    helpers = preflight.capture_helpers(settings)
    sources = [obj for obj in preflight.capture_objects(context) if preflight.is_surface(obj, helpers)]
    if not sources:
        raise ValueError("Select surface objects, or choose a source collection")
    return sorted(sources, key=lambda obj: obj.name)


def cameras_for(settings):
    if not settings.camera_collection:
        raise ValueError("Create a camera rig or choose a camera collection")
    cameras = sorted((obj for obj in settings.camera_collection.all_objects if obj.type == "CAMERA"),
                     key=lambda obj: obj.name)
    capture_settings.validate_view_count(len(cameras))
    graph = bpy.context.evaluated_depsgraph_get()
    for camera in cameras:
        capture_settings.camera_pose(camera, graph)
    return cameras


def quality_get(settings):
    if settings.get('quality_custom'):
        return 3
    try:
        value = capture_settings.format_for(settings.id_data, settings)
        values = {name: getattr(settings, name) for name in workflow.PRESET_FIELDS}
        values['resolution'] = max(value['width'], value['height'])
        name = workflow.preset_name(values)
        return {'DRAFT': 0, 'BALANCED': 1, 'HIGH': 2, 'CUSTOM': 3}[name]
    except (ValueError, AttributeError):
        return 3


def quality_set(settings, selected):
    if selected == 3:
        settings['quality_custom'] = True
        return
    settings["applying_preset"] = True
    try:
        preset = ('DRAFT', 'BALANCED', 'HIGH')[selected]
        for name, value in zip(workflow.PRESET_FIELDS, workflow.PRESETS[preset]):
            setattr(settings, name, value)
        settings.capture_format = 'PRESET'
        capture_settings.apply_format(settings.id_data, capture_settings.preset_format(settings))
        settings['quality_custom'] = False
    finally:
        settings["applying_preset"] = False


def custom_changed(settings, _context):
    if not settings.get("applying_preset"):
        settings.quality = "CUSTOM"



def strategy_changed(settings, context):
    profile = training_options.PROFILES[settings.strategy]
    for name in training_options.FIELDS:
        setattr(settings, name, profile[name])
    # Synthetic views share one camera/lighting response by default.
    settings.use_exposure_correction = False
    settings.use_ppisp = False
    settings.use_bilateral_grid = False
    custom_changed(settings, context)


def rig_recipe(settings):
    return json.dumps({**{name:getattr(settings,name) for name in (
        'rig_mode','views','rig_min','rig_max','rig_rings','rig_distance','rig_phase','rig_lens','rig_orientation','rig_auto_fit','rig_radius','rig_mesh_distribution','rig_mesh_orientation','rig_mesh_offset')},
        'aim_mode':settings.rig_aim_mode, 'aim_position':list(settings.rig_aim_position),
        'aim_offset':list(settings.rig_aim_offset),
        'target':settings.rig_target.name_full if settings.rig_target else '',
        'curve':settings.rig_curve.name_full if settings.rig_curve else '',
        'mesh':settings.rig_mesh.name_full if settings.rig_mesh else '',
        'capture_framing': capture_settings.framing_signature(settings.id_data, settings)},sort_keys=True)


def appearance_update(name):
    def update(settings, context):
        if getattr(settings,name):
            if name == 'use_exposure_correction':
                settings.use_ppisp = False
                settings.use_bilateral_grid = False
            else:
                settings.use_exposure_correction = False
        custom_changed(settings,context)
    return update


class LichtFeldPreferences(bpy.types.AddonPreferences):
    bl_idname = _PACKAGE

    backend_path: StringProperty(name="LichtFeld executable", subtype="FILE_PATH",
                                description="The LichtFeld Studio installation used for headless training")
    jobs_directory: StringProperty(name="Training jobs", subtype="DIR_PATH",
                                  description="Permanent folder for datasets, checkpoints and results")
    verification: StringProperty(options={"SKIP_SAVE"})

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "backend_path")
        row = layout.row()
        row.enabled = bool(self.backend_path)
        row.operator("lichtfeld.verify_installation", icon="CHECKMARK")
        if self.verification:
            for line in textwrap.wrap(self.verification, width=70):
                layout.label(text=line)
        layout.prop(self, "jobs_directory")
        for line in textwrap.wrap("Jobs: " + preflight.jobs_folder(self), width=70):
            layout.label(text=line)
        for line in textwrap.wrap("Training runs in a separate process. Closing Blender does not stop it.", width=70):
            layout.label(text=line)
        for line in textwrap.wrap("Reopen the saved project to reconnect, or use Open job if it is not listed.", width=70):
            layout.label(text=line)


class LichtFeldSourceRef(bpy.types.PropertyGroup):
    object: PointerProperty(type=bpy.types.Object)


class LichtFeldRigReference(bpy.types.PropertyGroup):
    mode: EnumProperty(name='Rig reference', description='Subject used to position and aim the rig; independent of the content rendered', items=[
        ('UNSET', 'Capture content (first rig)', 'A new rig adopts the capture source once; older rigs require an explicit reference before applying changes'),
        ('OBJECTS', 'Remembered objects', 'Fit around the objects remembered with Use selected objects as center'),
        ('COLLECTION', 'Collection', 'Fit around surface objects in the chosen collection'),
        ('SCENE', 'Entire scene', 'Fit around surface objects in this scene')])
    objects: CollectionProperty(type=LichtFeldSourceRef, description='Remembered rig surface objects; renaming preserves identity, missing objects require an explicit replacement')
    collection: PointerProperty(name='Reference collection', type=bpy.types.Collection,
                                description='Collection used for rig bounds, without restricting captured content')


class LichtFeldSettings(bpy.types.PropertyGroup):
    rig_reference: PointerProperty(type=LichtFeldRigReference, description='Independent subject used for camera placement; never restricts captured content')
    mode: EnumProperty(name="Workflow", items=[("NEW", "New", "Prepare a dataset or start a new training job"),
        ("OPEN", "Open job", "Reconnect to an existing training job"),
        ("CONNECT", "Connect", "Read-only view of an active trainer")])
    dataset_mode: EnumProperty(name="Task", items=[
        ("CAPTURE", "Capture and train", "Generate a dataset from this scene, then start training automatically", 'PLAY', 0),
        ("EXPORT", "Generate dataset only", "Choose a folder and generate images, calibrated cameras and optional maps; no trainer is needed", 'EXPORT', 2),
        ("REUSE", "Train saved images", "Start a fresh model on an existing dataset without rendering cameras again", 'IMAGE_DATA', 1)])
    dataset_job_directory: StringProperty(name="Saved job folder", subtype="DIR_PATH")
    dataset_coverage: StringProperty(default="Framing not checked", options={"SKIP_SAVE"})
    dataset_summary: StringProperty(default="Choose a saved job or reuse the current job", options={"SKIP_SAVE"})
    job_details: BoolProperty(name="Job details and recovery", default=False)
    previous_job: StringProperty(subtype="DIR_PATH")
    operation_busy: BoolProperty(default=False, options={"SKIP_SAVE"})
    importing: BoolProperty(default=False, options={"SKIP_SAVE"})
    ui_max_ms: FloatProperty(default=0, options={"SKIP_SAVE"})
    result_object: PointerProperty(type=bpy.types.Object)
    discovered_session: EnumProperty(name="Trainer", items=session_items)
    attached_session: StringProperty(name="Connected session", subtype="DIR_PATH")
    show_session_cameras: BoolProperty(name="Show capture cameras", default=True, update=session_camera_visibility)
    session_camera_count: IntProperty(default=0, options={"SKIP_SAVE"})
    scope: EnumProperty(name="Source", items=[
        ("SELECTION", "Selected objects", "Capture the selected surface objects with scene lighting"),
        ("COLLECTION", "Collection", "Capture surface objects in a collection"),
        ("SCENE", "Entire scene", "Capture the scene; place cameras for the areas you need")])
    source_objects: CollectionProperty(type=LichtFeldSourceRef)
    source_selection: StringProperty(default="", maxlen=262144, options={"HIDDEN"})
    source_collection: PointerProperty(name="Source collection", type=bpy.types.Collection)
    camera_collection: PointerProperty(name="Capture cameras", type=bpy.types.Collection)
    quality: EnumProperty(name="Quality", items=[
        ("DRAFT", "Draft", "Fast iteration to check coverage and appearance"),
        ("BALANCED", "Balanced", "More views and a larger model"),
        ("HIGH", "High", "More capture and training time"),
        ("CUSTOM", "Custom", "Individually adjusted settings")], get=quality_get, set=quality_set)
    rig_mode: EnumProperty(name="Distribution", items=[
        ("HEMISPHERE", "Hemisphere / cap", "Equal-area coverage between elevation limits"),
        ("SPHERE", "Sphere", "Uniform coverage around the whole object"),
        ("RINGS", "Orbit / rings", "One full turn per ring, without duplicate cameras"),
        ("PATH", "Curve path", "Equal-distance samples along one evaluated curve"),
        ("MESH", "Placement mesh", "Distribute cameras over a helper mesh, which is excluded from capture")])
    rig_min: FloatProperty(name="Minimum elevation", default=0, min=-90, max=90)
    rig_max: FloatProperty(name="Maximum elevation", default=85, min=-90, max=90)
    rig_rings: IntProperty(name="Rings", default=1, min=1, max=250)
    rig_auto_fit: BoolProperty(name="Automatic framing", default=True,
        description="Fit the rig reference at the chosen lens and output aspect; the minimum distance factor can be increased by fitting")
    rig_radius: FloatProperty(name="Camera radius", default=3, min=.01, max=1000000, subtype="DISTANCE", unit="LENGTH")
    rig_distance: FloatProperty(name="Minimum distance multiplier", default=2.8, min=1.05, max=100)
    rig_phase: FloatProperty(name="Azimuth offset", subtype="ANGLE", default=0)
    rig_lens: FloatProperty(name="Focal length (mm)", default=35, min=1, max=500)
    rig_target: PointerProperty(name="Target object", type=bpy.types.Object)
    rig_aim: PointerProperty(name="Viewport aim target", type=bpy.types.Object)
    rig_aim_mode: EnumProperty(name="Aim at", items=[
        ("SOURCE", "Reference center", "Center of the rig reference bounding box; capture content is independent"),
        ("OBJECT", "Object origin", "World origin of the selected target object"),
        ("CURSOR", "3D cursor", "Current world position of the 3D cursor"),
        ("VIEWPORT", "Viewport target", "Keep the current generated target position when applying rig changes"),
        ("POSITION", "World position", "Explicit world coordinates")])
    rig_aim_position: FloatVectorProperty(name="Aim position", subtype="TRANSLATION", size=3)
    rig_aim_offset: FloatVectorProperty(name="Aim offset", subtype="TRANSLATION", size=3,
        description="World-space offset from the chosen aim point; camera distribution stays centered on the source")
    rig_curve: PointerProperty(name="Path curve", type=bpy.types.Object,
        poll=lambda self,obj: obj.type == 'CURVE')
    rig_mesh: PointerProperty(name="Placement mesh", type=bpy.types.Object, poll=lambda self,obj: obj.type == 'MESH')
    rig_mesh_distribution: EnumProperty(name="Placement", items=[("SURFACE", "Even surface coverage", "Area-weighted deterministic camera positions; choose camera count"), ("FACES", "One camera per face", "Use every face center, with 4–1,000 faces")])
    rig_mesh_orientation: EnumProperty(name="Look", items=[("INWARD", "Inward", "Look opposite the outward face normals"), ("OUTWARD", "Outward", "Look along the face normals"), ("TARGET", "Look at target", "Use the aim target controls")])
    rig_mesh_offset: FloatProperty(name="Surface offset", default=.05, min=-100000, max=100000, subtype="DISTANCE", unit="LENGTH")
    seed_method: EnumProperty(name="Initial points", items=[("VISIBLE", "From rendered views", "Visible surfaces colored from the captured images; reuses the same render"), ("SURFACE", "Surface geometry", "Sample scene geometry with base material colors; no geometry render passes")], default="VISIBLE")
    seed_points: IntProperty(name="Initial point budget", default=30000, min=1000, max=250000)
    rig_orientation: EnumProperty(name="Orientation", items=[
        ("TARGET", "Look at target", "Aim at the explicit target or source center"),
        ("TANGENT", "Follow path", "Aim along the sampled path")])
    rig_generator: BoolProperty(name="Edit camera rig")
    rig_parameter_source: PointerProperty(type=bpy.types.Collection, options={'HIDDEN'},
        description="Generated rig whose parameters were explicitly loaded or last applied; choosing another collection does not replace the controls")
    rig_details: BoolProperty(name="Advanced rig settings")
    optimizer_file: StringProperty(name="Additional native parameters (JSON)", subtype="FILE_PATH")
    mask_mode: EnumProperty(name="Foreground mask",items=[
        ('AUTO','Automatic','Alpha-consistent for objects/collections; no mask for a whole-scene capture'),
        ('none','None','Train without the captured foreground mask'),
        ('alpha_consistent','Match alpha','Enforce the captured foreground alpha'),
        ('ignore','Foreground only','Ignore pixels outside the captured foreground'),
        ('segment','Segment','Penalize opacity outside the captured foreground'),
        ('segment_and_ignore','Three-band mask','Use the native three-band interpretation of captured alpha')])
    strategy: EnumProperty(name="Strategy", items=[("mrnf","MRNF","Native MRNF optimizer"),
        ("mcmc","MCMC","Native MCMC optimizer"),("igs+","IGS+","Native IGS+ optimizer")], update=strategy_changed)
    refine_start: FloatProperty(name="Refine from (fraction)", default=0, min=0, max=1)
    refine_end: FloatProperty(name="Refine until (fraction)", default=.95, min=0, max=1)
    growth_end: FloatProperty(name="Grow until (fraction)", default=.5, min=0, max=1)
    keep_all_checkpoints: BoolProperty(name="Keep all checkpoints", default=False)
    training_advanced: BoolProperty(name="Optimizer details")
    capture_advanced: BoolProperty(name="Advanced capture settings")
    reuse_render_data: BoolProperty(name="Reuse Cycles scene data",
        get=lambda s: s.id_data.render.use_persistent_data,
        set=lambda s, v: setattr(s.id_data.render, 'use_persistent_data', v),
        description="Alias of Blender Persistent Data; new captures use the saved scene performance settings")
    preview_advanced: BoolProperty(name="Preview details")
    use_depth_loss: BoolProperty(name="Depth supervision", default=False)
    use_normal_loss: BoolProperty(name="Normal supervision", default=False)
    depth_loss_weight: FloatProperty(name="Depth loss weight", default=2, min=0, max=100)
    normal_loss_weight: FloatProperty(name="Normal loss weight", default=.005, min=0, max=100, precision=4)
    normal_start_fraction: FloatProperty(name="Normals start (fraction)", default=.08, min=0, max=1)
    normal_end_fraction: FloatProperty(name="Normals end (fraction)", default=1, min=0, max=1)
    cycles_device: EnumProperty(name="Cycles device", items=[("CPU","CPU","Render with the CPU"),
        ("GPU","GPU Compute","Use exactly the backend and devices enabled in Blender System Preferences")],
        get=scene_render.device_get, set=scene_render.device_set)
    views: IntProperty(name="Rig camera count", default=48, min=4, max=1000, update=custom_changed,
        description="Cameras to create or update in the generated rig. Additional cameras in the collection are also captured")
    resolution: IntProperty(name="Longest side", default=512, min=64, max=4096, update=custom_changed,
                            description="Pixels on the longest side; the aspect ratio determines the other side")
    capture_format: EnumProperty(name="Image format", items=[
        ('PRESET', 'Aspect preset', 'Choose an aspect ratio and size; square pixels'),
        ('SCENE', 'From scene', 'Use scene width, height, percentage and pixel aspect; ignores render border'),
        ('CUSTOM', 'Custom dimensions', 'Explicit output width and height; square pixels')], update=custom_changed)
    capture_aspect: EnumProperty(name="Aspect ratio", items=[
        ('SQUARE', '1:1', ''), ('FOUR_THREE', '4:3', ''), ('THREE_TWO', '3:2', ''),
        ('WIDE', '16:9', ''), ('CINEMA', '21:9', '')], update=custom_changed)
    capture_orientation: EnumProperty(name="Orientation", items=[
        ('LANDSCAPE', 'Landscape', ''), ('PORTRAIT', 'Portrait', '')], update=custom_changed)
    capture_width: IntProperty(name="Width", default=1920, min=64, max=4096, update=custom_changed)
    capture_height: IntProperty(name="Height", default=1080, min=64, max=4096, update=custom_changed)
    format_presets_open: BoolProperty(name="Size presets")
    camera_overrides_open: BoolProperty(name="Capture-only camera overrides")
    override_optics: BoolProperty(name="Common lens and sensor", description="Override optics on all capture copies; camera positions and originals are kept")
    capture_lens: FloatProperty(name="Focal length (mm)", default=35, min=1, max=500)
    capture_sensor_width: FloatProperty(name="Sensor width (mm)", default=36, min=1, max=100)
    capture_sensor_height: FloatProperty(name="Sensor height (mm)", default=24, min=1, max=100)
    capture_sensor_fit: EnumProperty(name="Sensor fit", items=[('AUTO','Auto','Fit the longer image dimension'),
        ('HORIZONTAL','Horizontal','Fit sensor width'),('VERTICAL','Vertical','Fit sensor height')])
    override_shift: BoolProperty(name="Common lens shift", description="Set the same image shift on every capture copy; zero recenters them")
    capture_shift_x: FloatProperty(name="Shift X", default=0, min=-2, max=2)
    capture_shift_y: FloatProperty(name="Shift Y", default=0, min=-2, max=2)
    override_clipping: BoolProperty(name="Common clipping", description="Set near/far clipping distances on capture copies only")
    capture_clip_start: FloatProperty(name="Near", default=.01, min=.00001, max=100000, subtype='DISTANCE', unit='LENGTH')
    capture_clip_end: FloatProperty(name="Far", default=1000, min=.00002, max=1000000, subtype='DISTANCE', unit='LENGTH')
    capture_preview_index: IntProperty(name="Capture camera", default=1, min=1, max=capture_settings.MAX_VIEWS,
        description="Camera number in capture order; press Update framing to apply")
    samples: IntProperty(name="Render samples", min=1, max=2147483647, get=scene_render.sample_get, set=scene_render.sample_set, update=custom_changed)
    iterations: IntProperty(name="Training steps", default=2000, min=4, max=1000000, update=custom_changed)
    eval_interval: IntProperty(name="Evaluate every", default=500, min=1, max=100000, update=custom_changed,
                               description="Choose the best model using reserved views at this step interval")
    max_points: IntProperty(name="Gaussian budget", default=250000, min=1000, max=10000000, update=custom_changed)
    sh_degree: IntProperty(name="SH degree", default=3, min=0, max=3)
    preview_mode: EnumProperty(name="Preview updates", items=[
        ("LIVE", "Live (adaptive)", "Frequent full-quality updates within a 5% capture-time budget"),
        ("PERIODIC", "Periodic", "Use the configured minimum snapshot interval")])
    preview_seconds: FloatProperty(name="Update every", default=1, min=.1, max=600,
                                   subtype="TIME", description="Minimum seconds between previews; adapts to capture cost")
    full_preview: BoolProperty(name="Full quality previews", default=True,
        description="Send every Gaussian and all trained SH coefficients. Disable to limit preview size")
    preview_hold_seconds: FloatProperty(name="Preview settling (s)", default=.5, min=0, max=5,
        description="Keep a complete snapshot visible while Blender accumulates transparency samples. Zero gives the fastest updates. Training and status reporting continue normally")
    preview_sh_degree: IntProperty(name="Preview SH limit", default=1, min=0, max=3,
        description="Maximum SH degree in limited previews. SH 0 keeps base color only. Training, checkpoints and saved results keep their configured SH degree")
    preview_points: IntProperty(name="Preview budget", default=100000, min=1000, max=1000000,
        description="Viewport Gaussian budget only. The final best model is imported at full resolution")
    engine: EnumProperty(name="Capture renderer", items=[
        ("CYCLES", "Cycles", "Capture the scene using Cycles"),
        ("BLENDER_EEVEE", "EEVEE", "Capture the scene using EEVEE"),
        ("UNSUPPORTED", "Unsupported engine", "Choose Cycles or EEVEE in Blender Render Properties")],
        get=scene_render.engine_get, set=scene_render.engine_set)
    validation_details: BoolProperty(name="Validation details", default=False)
    advanced: BoolProperty(name="Capture and training details")
    job_directory: StringProperty(name="Current job", subtype="DIR_PATH")
    status: StringProperty(default="Ready", options={"SKIP_SAVE"})
    detail: StringProperty(options={"SKIP_SAVE"})
    progress: FloatProperty(default=0, min=0, max=1, options={"SKIP_SAVE"})
    running: BoolProperty(default=False, options={"SKIP_SAVE"})
    paused: BoolProperty(default=False, options={"SKIP_SAVE"})
    previews_enabled: BoolProperty(default=True, options={"SKIP_SAVE"})
    show_result: BoolProperty(default=True)


for _name in training_options.FIELDS:
    _default = training_options.PROFILES['mrnf'][_name]
    if _name in {'use_exposure_correction','use_ppisp','use_bilateral_grid'}:
        _default = False
    _label = training_options.LABELS.get(_name, _name.replace('_',' ').capitalize())
    if isinstance(_default, bool):
        _property = BoolProperty(name=_label, default=_default, update=appearance_update(_name) if _name in {"use_exposure_correction","use_ppisp","use_bilateral_grid"} else custom_changed)
    elif isinstance(_default, int):
        _property = IntProperty(name=_label, default=_default, min=1, update=custom_changed)
    else:
        _property = FloatProperty(name=_label, default=_default,
            min=-1 if _name in {'shs_lr','grow_fraction'} else 0,
            max=1 if _name in training_options.PROBABILITIES else 1e20, precision=6, update=custom_changed)
    LichtFeldSettings.__annotations__[_name] = _property


class LF_OT_verify(bpy.types.Operator):
    bl_idname = "lichtfeld.verify_installation"
    bl_label = "Verify installation"
    bl_description = "Check that the executable starts; training API compatibility is checked when a job starts"

    def execute(self, context):
        prefs = preferences(context)
        path = bpy.path.abspath(prefs.backend_path)
        prefs.verification = "Checking installation…"
        def done(text):
            prefs.verification = text
        directory = preflight.jobs_folder(prefs)
        ui_runtime.submit(lambda: jobs.check_environment(path, directory)["message"], callback=done, failure=done)
        preflight.environment(prefs, force=True)
        return {"FINISHED"}


class LF_OT_setup(bpy.types.Operator):
    bl_idname = "lichtfeld.setup"
    bl_label = "Set up LichtFeld"
    bl_description = "Choose the LichtFeld executable and training output folder"

    def execute(self, context):
        bpy.ops.screen.userpref_show()
        context.preferences.active_section = "ADDONS"
        bpy.ops.preferences.addon_show(module=_PACKAGE)
        return {"FINISHED"}


class LF_OT_tight_framing(bpy.types.Operator):
    bl_idname = "lichtfeld.tight_framing"
    bl_label = "Use tight framing"
    bl_description = "Set a close, automatically fitted radius for a centered rig. Then apply rig changes and review coverage before capturing again"

    @classmethod
    def poll(cls,context):
        s=context.scene.lichtfeld_training
        return not s.running and not s.operation_busy and s.rig_mode not in {'PATH','MESH'}

    def execute(self,context):
        s=context.scene.lichtfeld_training
        s.rig_auto_fit=True
        s.rig_distance=1.05
        return {"FINISHED"}


class LF_OT_rig(bpy.types.Operator):
    bl_idname = "lichtfeld.create_rig"
    bl_label = "Create capture rig"
    bl_description = "Create or update cameras around the rig reference without changing the content to capture"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        s = context.scene.lichtfeld_training
        if s.running or s.operation_busy:
            return False
        if rig_edit.render_busy(context):
            cls.poll_message_set("Finish Blender's render or locked job before updating the rig")
            return False
        if s.camera_collection and s.camera_collection.get('lichtfeld_generated') and s.rig_parameter_source != s.camera_collection:
            cls.poll_message_set("Load selected rig before applying changes")
            return False
        return True

    def generate(self, context):
        settings = context.scene.lichtfeld_training
        reference = settings.rig_reference
        legacy = settings.camera_collection and settings.camera_collection.get('lichtfeld_generated')
        if reference.mode == 'UNSET' and not legacy:
            sources = sources_for(context)
        else:
            sources = yield from rig_edit.reference_sources(context, reference)
        helpers = preflight.capture_helpers(settings)
        capture_sources = ([obj for obj in preflight.selection_objects(context) if preflight.is_surface(obj, helpers)]
                           if settings.scope == 'SELECTION' else None)
        recipe = rig_recipe(settings)
        low, high = Vector((float("inf"),) * 3), Vector((float("-inf"),) * 3)
        depsgraph = context.evaluated_depsgraph_get()
        for obj in sources:
            evaluated = obj.evaluated_get(depsgraph)
            for corner in evaluated.bound_box:
                point = evaluated.matrix_world @ Vector(corner)
                for axis in range(3):
                    low[axis] = min(low[axis], point[axis])
                    high[axis] = max(high[axis], point[axis])
            yield
        settings = context.scene.lichtfeld_training
        center = (low + high) / 2
        uses_aim = not (settings.rig_mode == 'PATH' and settings.rig_orientation == 'TANGENT' or
                        settings.rig_mode == 'MESH' and settings.rig_mesh_orientation != 'TARGET')
        aim = rig_edit.aim_point(context, settings, center) if uses_aim else center.copy()
        fit = (capture_settings.fitting_factor(context.scene, settings, depsgraph)
               if settings.rig_auto_fit and settings.rig_mode not in {'PATH', 'MESH'} else 1)
        radius = (max((high - low).length / 2, 0.1) * max(settings.rig_distance,fit)
                  if settings.rig_auto_fit else settings.rig_radius)
        points = []
        mesh_directions = []
        if settings.rig_mode == "MESH":
            from . import rig_mesh
            for item in rig_mesh.positions(context, settings.rig_mesh, settings.views, settings.rig_mesh_distribution, settings.rig_mesh_orientation, settings.rig_mesh_offset):
                if item is not None:
                    points.append(Vector(item[0]))
                    mesh_directions.append(Vector(item[1]))
                yield
        elif settings.rig_mode == 'PATH':
            for point in rig_path.positions(context, settings.rig_curve, settings.views):
                if point is not None:
                    points.append(Vector(point))
                yield
        else:
            for direction in rig_geometry.directions(settings.views, settings.rig_mode,
                    settings.rig_min, settings.rig_max, settings.rig_rings, settings.rig_phase):
                points.append(center + Vector(direction)*radius)
                yield
        if any(not math.isfinite(value) for point in points for value in point):
            raise ValueError("Camera positions must be finite")
        if len({tuple(point) for point in points}) != len(points):
            raise ValueError("The path produces repeated camera positions. Adjust the curve or sample count.")
        previous = settings.camera_collection
        collection = bpy.data.collections.new("LichtFeld Capture Cameras")
        context.scene.collection.children.link(collection)
        self.collection = collection
        prepared_reference = collection.lichtfeld_rig_reference
        if reference.mode == 'UNSET':
            prepared_reference.mode = 'OBJECTS'
            for obj in sources:
                entry = prepared_reference.objects.add()
                entry.object = obj
                entry.name = obj.name
                yield
        else:
            rig_edit.copy_reference(reference, prepared_reference)
        target = bpy.data.objects.new("LichtFeld Aim Target", None)
        target.empty_display_type = "SPHERE"
        target.show_in_front = True
        target.empty_display_size = max((high-low).length * .05, .05)
        target["lichtfeld_aim"] = True
        target['lichtfeld_aim_used'] = uses_aim
        collection.objects.link(target)
        rig_edit.configure_target(target, settings, aim, follow=uses_aim)
        for index, position in enumerate(points):
            data = bpy.data.cameras.new(f"Capture {index + 1:03d}")
            data.lens = settings.rig_lens
            data.clip_start = max(radius / 10000, 0.001)
            data.clip_end = max(radius * 10, (position-center).length*10, 100)
            obj = bpy.data.objects.new(f"Capture {index + 1:03d}", data)
            collection.objects.link(obj)
            obj["lichtfeld_generated"] = True
            obj["lichtfeld_camera_index"] = index
            obj.location = position
            direction = mesh_directions[index] if settings.rig_mode == "MESH" and settings.rig_mesh_orientation != "TARGET" else aim-position
            if settings.rig_mode == 'PATH' and settings.rig_orientation == 'TANGENT':
                cyclic = settings.rig_curve.data.splines[0].use_cyclic_u
                direction = points[index+1]-position if index+1<len(points) else (points[0]-position if cyclic else position-points[index-1])
            if direction.length < 1e-8:
                raise ValueError("A camera coincides with its target or a duplicate path sample; adjust the rig")
            obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
            if (settings.rig_mode != "PATH" or settings.rig_orientation != "TANGENT") and (settings.rig_mode != "MESH" or settings.rig_mesh_orientation == "TARGET"):
                rig_edit.track(obj, target)
            yield
        for _ in rig_edit.commit(self, settings, previous, collection, target, recipe):
            yield
        rig_edit.copy_reference(settings.camera_collection.lichtfeld_rig_reference, reference)
        if capture_sources:
            for _ in preflight.remember_sources(settings, capture_sources):
                yield
        self.report({"INFO"}, f"Applied {len(points)} cameras. Review coverage before training.")

    def execute(self, context):
        self.collection = None
        self.rig_backup = []
        self.scene = context.scene
        self.cancelled = False
        self.cancel_requested = False
        self.render_waiting = False
        self.committed = False
        self.work = self.generate(context)
        if bpy.app.background:
            try:
                for _ in self.work:
                    pass
                return {"FINISHED"}
            except Exception as error:
                if not self.committed:
                    for _ in self.cleanup():
                        pass
                self.report({"ERROR"}, str(error))
                return {"CANCELLED"}
        context.scene.lichtfeld_training.operation_busy = True
        self.timer = context.window_manager.event_timer_add(0.02, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if not self.committed and (event.type == "ESC" or context.scene != self.scene):
            self.cancel_requested = True
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if rig_edit.render_busy(context):
            if not self.render_waiting:
                self.report({'INFO'}, 'Rig update waits for Blender to finish rendering or unlock the interface')
                self.render_waiting = True
            return {'PASS_THROUGH'}
        self.render_waiting = False
        if not self.committed and not self.cancelled and self.cancel_requested:
            self.cancelled = True
            self.work.close()
            self.work = self.cleanup()
        deadline = time.perf_counter() + 0.003
        try:
            while time.perf_counter() < deadline:
                next(self.work)
        except StopIteration:
            context.window_manager.event_timer_remove(self.timer)
            self.scene.lichtfeld_training.operation_busy = False
            return {"CANCELLED"} if self.cancelled else {"FINISHED"}
        except Exception as error:
            self.report({"ERROR"}, str(error))
            if not self.committed and not self.cancelled:
                self.cancelled = True
                self.work.close()
                self.work = self.cleanup()
                return {"RUNNING_MODAL"}
            context.window_manager.event_timer_remove(self.timer)
            self.scene.lichtfeld_training.operation_busy = False
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def cleanup(self):
        for _ in rig_edit.rollback(self):
            yield
        if self.collection:
            for obj in list(self.collection.objects):
                camera = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if camera and camera.users == 0:
                    bpy.data.cameras.remove(camera)
                yield
            bpy.data.collections.remove(self.collection)


def launch_supervisor(job):
    root = Path(job["root"])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        if job.get('capture_only'):
            jobs.check_export_destination(job)
        if not job.get("resume") and not job.get("reuse_dataset"):
            jobs.freeze_source(job)
        atomic_json(root / "job.json", job)
        if not job.get("capture_only"):
            jobs.prepare_optimizer(job)
        atomic_json(root / "job.json", job)
        if job.get("connection_directory"):
            jobs.remember_job(job["connection_directory"], job)
        with (root / "supervisor-console.log").open("wb") as log:
            subprocess.Popen([python_binary(), str(_ROOT / "supervisor.py"), str(root / "job.json")],
                             stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             **process_options(detach=True))
    except Exception as error:
        jobs.release_source(job)
        try:
            atomic_json(root / "state.json", {"job_id":job["id"], "state":"failed", "error":str(error), "updated":time.time()})
        except OSError:
            pass
        raise


def allowed(context, action):
    settings = context.scene.lichtfeld_training
    current = ui_runtime.peek(context.scene)
    status = current.status if current else {}
    flags = workflow.actions(status.get("state", settings.status),
        external=bool(settings.attached_session), result=bool(settings.result_object),
        best=bool(status.get("best") or status.get("result")),
        checkpoint=bool(status.get("checkpoint_available")),
        busy=settings.operation_busy,
        control=status.get("control_available", False))
    key = {"resume":"pause", "preview_on":"previews", "preview_off":"previews", "import_best":"import"}.get(action, action)
    return flags.get(key, False)


class LF_OT_pick_sources(bpy.types.Operator):
    bl_idname = "lichtfeld.pick_sources"
    bl_label = "Use selected objects"
    bl_description = "Remember the selected surface objects as the source, independently of later viewport selections"
    for_rig: BoolProperty(default=False, options={'HIDDEN'}, description='Remember this selection for rig placement only; captured content stays unchanged')

    @classmethod
    def description(cls, context, properties):
        return ('Use selected surface objects for rig placement and aiming; captured content stays unchanged'
                if properties.for_rig else cls.bl_description)
    @classmethod
    def poll(cls, context):
        s = context.scene.lichtfeld_training
        return not s.running and not s.operation_busy
    def execute(self, context):
        s = context.scene.lichtfeld_training
        sources = [obj for obj in context.selected_objects if preflight.is_surface(obj, preflight.capture_helpers(s))]
        for_rig = self.for_rig
        if not sources:
            self.report({"ERROR"}, "Select surface objects for the rig reference first" if for_rig else "Select the surface objects to capture first")
            return {"CANCELLED"}
        def apply():
            try:
                if for_rig:
                    entries = []
                    for obj in sources:
                        entries.append({'object': obj, 'name': obj.name})
                        yield
                    s.rig_reference['objects'] = entries
                    s.rig_reference.collection = None
                    s.rig_reference.mode = 'OBJECTS'
                    preflight.invalidate(s.id_data)
                else:
                    yield from preflight.remember_sources(s, sources)
            finally:
                s.operation_busy = False
        if bpy.app.background:
            for _ in apply():pass
        else:
            s.operation_busy = True
            ui_runtime.schedule(apply(), lambda error: ui_runtime.fail(context.scene, error))
        return {"FINISHED"}


class LF_OT_read_rig(bpy.types.Operator):
    bl_idname = "lichtfeld.read_rig"
    bl_label = "Load selected rig"
    bl_description = "Reload the selected rig's saved controls without moving cameras or changing capture format and overrides"
    bl_options = {"REGISTER", "UNDO"}
    def execute(self, context):
        s = context.scene.lichtfeld_training
        collection = s.camera_collection
        if not collection or not collection.get("lichtfeld_generated") or s.running or s.operation_busy:
            return {"CANCELLED"}
        encoded = collection.get("lichtfeld_rig_recipe", "")
        try:
            values = rig_edit.decode_recipe(encoded)
            objects = {}
            for name, key in (('target', 'lichtfeld_rig_target'), ('curve', 'lichtfeld_rig_curve'),
                              ('mesh', 'lichtfeld_placement_mesh')):
                obj = collection.get(key) or bpy.data.objects.get(values.get(name, ''))
                if obj is not None and (not isinstance(obj, bpy.types.Object) or
                                        name == 'curve' and obj.type != 'CURVE' or
                                        name == 'mesh' and obj.type != 'MESH'):
                    raise ValueError('Invalid rig object reference: ' + name)
                objects[name] = obj
            for name in ("rig_mode", "views", "rig_min", "rig_max", "rig_rings", "rig_distance",
                         "rig_phase", "rig_lens", "rig_orientation", "rig_auto_fit", "rig_radius",
                         "rig_mesh_distribution", "rig_mesh_orientation", "rig_mesh_offset"):
                if name in values:
                    setattr(s, name, values[name])
            s.rig_auto_fit = values.get("rig_auto_fit", True)
            s.rig_radius = values.get("rig_radius", 3)
            s.rig_aim_mode = values.get("aim_mode", "OBJECT" if values.get("target") else "SOURCE")
            s.rig_aim_position = values.get("aim_position", (0, 0, 0))
            s.rig_aim_offset = values.get("aim_offset", (0, 0, 0))
            s.rig_target = objects['target']
            s.rig_curve = objects['curve']
            s.rig_mesh = objects['mesh']
            s.rig_aim = next((obj for obj in collection.objects if obj.get("lichtfeld_aim")), None)
            rig_edit.copy_reference(collection.lichtfeld_rig_reference, s.rig_reference)
            s.rig_parameter_source = collection
        except (ValueError, TypeError, KeyError) as error:
            self.report({"ERROR"}, "Invalid rig metadata: " + str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class LF_OT_select_aim(bpy.types.Operator):
    bl_idname = "lichtfeld.select_aim"
    bl_label = "Select aim target"
    bl_description = "Select the generated Empty. Moving it updates camera directions without moving their positions"
    @classmethod
    def poll(cls, context):
        s = context.scene.lichtfeld_training
        return (not s.running and not s.operation_busy and s.camera_collection is not None and
                s.rig_parameter_source == s.camera_collection and s.rig_aim is not None and
                s.rig_aim.name in s.camera_collection.objects)

    def execute(self, context):
        settings = context.scene.lichtfeld_training
        target = settings.rig_aim
        if not target or target.name not in context.view_layer.objects:
            return {"CANCELLED"}
        for obj in context.selected_objects:
            obj.select_set(False)
        settings.rig_aim_mode = "VIEWPORT"
        settings.rig_aim_offset = (0, 0, 0)
        target.hide_set(False)
        target.select_set(True)
        context.view_layer.objects.active = target
        return {"FINISHED"}


class LF_OT_inspect_camera(bpy.types.Operator):
    bl_idname = 'lichtfeld.inspect_capture_camera'
    bl_label = 'Select camera'
    bl_description = 'Select the camera named in this validation issue to inspect its transform, constraints and parents; no transforms are applied'
    bl_options = {'REGISTER', 'UNDO'}
    camera_name: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        s = context.scene.lichtfeld_training
        return context.mode == 'OBJECT' and not s.running and not s.operation_busy

    def execute(self, context):
        camera = context.view_layer.objects.get(self.camera_name)
        if not camera or camera.type != 'CAMERA':
            self.report({'ERROR'}, 'Camera is not in this view layer; enable its collection in the Outliner')
            return {'CANCELLED'}
        for obj in context.selected_objects:
            obj.select_set(False)
        camera.hide_set(False)
        camera.select_set(True)
        context.view_layer.objects.active = camera
        return {'FINISHED'}


class LF_OT_notice(bpy.types.Operator):
    bl_idname = "lichtfeld.report_notice"
    bl_label = "LichtFeld notice"
    bl_options = {"INTERNAL"}
    level: EnumProperty(items=[('INFO', 'Information', ''), ('WARNING', 'Warning', ''), ('ERROR', 'Error', '')])
    message: StringProperty()

    def execute(self, context):
        self.report({self.level}, self.message)
        return {"FINISHED"}


class LF_OT_start(bpy.types.Operator):
    bl_idname = "lichtfeld.start_training"
    bl_label = "Start training"
    bl_description = "Capture the scene and train with LichtFeld in the background"

    @classmethod
    def poll(cls, context):
        if context.scene.lichtfeld_training.dataset_mode == "REUSE":
            cls.poll_message_set("Use Train saved images from scratch for the selected dataset")
            return False
        if context.scene.lichtfeld_training.attached_session:
            cls.poll_message_set("Disconnect the viewer before starting a new job in this scene")
            return False
        if context.scene.lichtfeld_training.running:
            return False
        try:
            if not preferences(context).backend_path:
                cls.poll_message_set("Choose the LichtFeld executable in the add-on preferences")
                return False
            if context.scene.lichtfeld_training.operation_busy:
                return False
            if not bpy.data.filepath or bpy.data.is_dirty:
                cls.poll_message_set("Save the .blend before capture")
                return False
            if bpy.app.background:
                cameras_for(context.scene.lichtfeld_training)
                sources_for(context)
                return True
            errors = preflight.issues(preflight.cached(context, preferences(context)))
            if errors:
                cls.poll_message_set(errors[0])
            return not errors
        except ValueError as error:
            cls.poll_message_set(str(error))
            return False

    @staticmethod
    def launch(context, capture, export_directory=""):
        try:
            settings = context.scene.lichtfeld_training
            prefs = preferences(context)
            backend = Path(bpy.path.abspath(prefs.backend_path))
            if not bpy.data.filepath or bpy.data.is_dirty:
                raise ValueError("Save the .blend first. Background capture uses that exact saved revision.")
            if capture["errors"]:
                raise ValueError(" ".join(capture["errors"]))
            if not export_directory and not settings.refine_start < settings.refine_end:
                raise ValueError("Refinement start must be before its end")
            if not export_directory and settings.growth_end > settings.refine_end:
                raise ValueError("Growth end must be at or before refinement end")
            errors = training_options.validate(settings.strategy, training_options.optimizer_values(settings))
            if errors and not export_directory:
                raise ValueError(" ".join(errors))
            if not export_directory and settings.use_normal_loss and not settings.normal_start_fraction < settings.normal_end_fraction:
                raise ValueError("Normal supervision start must be before its end")
            sources = capture["sources"]
            cameras = capture["cameras"]
            render_settings = scene_render.snapshot(context.scene)
            directory = (Path(bpy.path.abspath(prefs.jobs_directory)) if prefs.jobs_directory else
                         Path(bpy.utils.user_resource("DATAFILES", path="lichtfeld/jobs", create=False)))
            job_id = uuid.uuid4().hex
            root = directory / job_id
            job = {"schema": SCHEMA, "id": job_id, "root": str(root),
                   "backend": str(backend), "blender": bpy.app.binary_path, "dataset_root":str(root/"dataset"),
                   "blender_version": bpy.app.version_string,
                   "blender_hash": bpy.app.build_hash.decode(), "source_file": bpy.data.filepath,
                   "frame": context.scene.frame_current, "scope": settings.scope, "view_layer": context.view_layer.name,
                   "scene": context.scene.name, "connection_directory": preflight.connection_folder(prefs),
                   "sources": sources, "cameras": cameras,
                   "source_visibility": capture["visibility"], "visibility_sources":capture.get("visibility_sources", sources),
                   "test_every": 8 if len(cameras) >= 16 else 4, "seed_points": min(settings.seed_points, settings.max_points),
                   "seed_method": 'VISIBLE' if settings.use_depth_loss and settings.depth_loss_weight > 0 else settings.seed_method,
                   "capture_helpers": [obj.name for obj in preflight.capture_helpers(settings)],
                   "capture_format": capture_settings.format_for(context.scene, settings),
                   "render_settings": render_settings,
                   "capture_camera": capture_settings.camera_for(settings),
                   **{name: getattr(settings, name) for name in (
                       "iterations", "eval_interval", "max_points", "sh_degree", "preview_seconds",
                       "preview_points", "preview_sh_degree", "full_preview", "preview_mode", "resolution", "samples", "engine", "cycles_device",
                       "strategy", "mask_mode", "use_depth_loss", "use_normal_loss", "depth_loss_weight",
                       "normal_loss_weight", "normal_start_fraction", "normal_end_fraction", "reuse_render_data")},
                   "optimizer_file": bpy.path.abspath(settings.optimizer_file) if settings.optimizer_file else "",
                   "optimizer": training_options.optimizer_values(settings),
                   "optimizer_schedule": {name:getattr(settings,name) for name in ("refine_start","refine_end","growth_end")}}
            job.update(engine=render_settings['engine'], samples=render_settings['samples'],
                       cycles_device=render_settings['device'],
                       resolution=max(job['capture_format']['width'], job['capture_format']['height']))
            if export_directory:
                job.update(capture_only=True, export_parent=export_directory)
            if not bpy.app.background:
                readiness = preflight.cached(context, prefs, capture_only=bool(export_directory))
                if preflight.issues(readiness):
                    raise ValueError("Wait for capture validation before starting")
                job["source_identity"] = readiness["environment"].get("source_identity")
            settings.dataset_job_directory = str(root)
            context.scene["lichtfeld_expected_job"] = job_id
            settings.job_directory = str(root)
            ui_runtime.submit(lambda: launch_supervisor(job), failure=lambda error, scene=context.scene: ui_runtime.fail(scene, error))
            settings.running = True
            settings.status = "Preparing capture"
            settings.detail = "Capture and export only; no trainer will be started" if export_directory else "LichtFeld runs without opening its window"
            settings.progress = 0
            print("LichtFeld: dataset-only capture started in the background" if export_directory else
                  "LichtFeld: capture and training started in the background", flush=True)
            return {"FINISHED"}
        except Exception as error:
            ui_runtime.fail(context.scene, str(error))
            ui_runtime.report_notice('ERROR', str(error))
            return {"CANCELLED"}


    def execute(self, context):
        scene = context.scene
        settings = scene.lichtfeld_training
        capture_only = isinstance(self, LF_OT_export_dataset)
        # Blender frees a non-modal operator after execute returns. Freeze its
        # RNA inputs now; queued work must not retain or dereference `self`.
        export_directory = bpy.path.abspath(self.directory) if capture_only and self.directory else ""
        if capture_only and not export_directory:
            self.report({'ERROR'}, 'Choose a destination folder')
            return {'CANCELLED'}
        work = preflight.collect(context, capture_only=capture_only)
        initial = preflight.signature(context)
        def prepare():
            next_check = 0
            try:
                while True:
                    # The signature includes render-device configuration. Rebuild
                    # it at a bounded cadence, rather than once per scene object.
                    now = time.perf_counter()
                    if now >= next_check:
                        if context.scene != scene or preflight.signature(context) != initial:
                            raise ValueError("The capture setup changed. Save the scene and start again.")
                        next_check = now + .05
                    try:
                        next(work)
                    except StopIteration as stop:
                        if context.scene != scene or preflight.signature(context) != initial:
                            raise ValueError("The capture setup changed. Save the scene and start again.")
                        LF_OT_start.launch(context, stop.value, export_directory)
                        return
                    yield
            finally:
                work.close()
                try:
                    settings.operation_busy = False
                except ReferenceError:
                    pass
        if bpy.app.background:
            try:
                for _ in prepare():
                    pass
                return {"CANCELLED"} if settings.detail and not settings.running else {"FINISHED"}
            except Exception as error:
                ui_runtime.fail(scene, str(error))
                self.report({"ERROR"}, str(error))
                return {"CANCELLED"}
        settings.operation_busy = True
        settings.status = "Checking capture"
        settings.detail = ""
        def failed(error):
            settings.operation_busy = False
            ui_runtime.fail(scene, error)
            ui_runtime.report_notice('ERROR', error)
        ui_runtime.schedule(prepare(), failed)
        return {"FINISHED"}


class LF_OT_export_dataset(bpy.types.Operator):
    bl_idname = "lichtfeld.export_dataset"
    bl_label = "Generate dataset…"
    bl_description = "Choose a destination and capture images, calibrated cameras and enabled maps in the background, without starting LichtFeld training"
    directory: StringProperty(name="Destination folder", subtype="DIR_PATH")

    def execute(self, context):
        return LF_OT_start.execute(self, context)

    @classmethod
    def poll(cls, context):
        s = context.scene.lichtfeld_training
        if s.running or s.operation_busy or s.attached_session:
            return False
        if not bpy.data.filepath or bpy.data.is_dirty:
            cls.poll_message_set("Save the .blend before exporting its exact saved revision")
            return False
        if bpy.app.background:
            return True
        errors = preflight.issues(preflight.cached(context, preferences(context), capture_only=True))
        if errors:
            cls.poll_message_set(errors[0])
        return not errors

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

class LF_OT_command(bpy.types.Operator):
    bl_idname = "lichtfeld.training_command"
    bl_label = "Training control"
    action: StringProperty()

    def execute(self, context):
        try:
            if not allowed(context, self.action):
                raise ValueError("This control is unavailable in the current training phase")
            current = ui_runtime.binding(context.scene)
            if not current:
                raise ValueError("No training job is selected")
            current.reader.commands.put(self.action)
            return {"FINISHED"}
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


def pointcloud_from_arrays(arrays):
    if not bpy.app.background:
        raise RuntimeError("Use the staged viewer importer on an interactive Blender session")
    import numpy as np
    count = len(arrays["means"])
    cloud = bpy.data.pointclouds.new("LichtFeld Snapshot")
    try:
        cloud.type = "GAUSSIAN_SPLAT"
        cloud.resize(count)
        def attribute(name, kind, property_name, values):
            item = cloud.attributes.get(name) or cloud.attributes.new(name, kind, "POINT")
            item.data.foreach_set(property_name, np.ascontiguousarray(values, dtype=np.float32).ravel())
        for name, kind, property_name, values in ui_runtime.blender_fields(arrays):
            attribute(name, kind, property_name, values)
        cloud.update_tag()
        return cloud
    except Exception:
        bpy.data.pointclouds.remove(cloud)
        raise


def import_splat(scene, root, record, job=None):
    if not bpy.app.background:
        current = ui_runtime.binding(scene)
        if current:
            current.reader.commands.put("import_best")
        return True
    arrays = None
    if record.get("shared"):
        try:
            arrays = read_snapshot(record["shared"])
        except FileNotFoundError:
            if "path" not in record:
                return False  # A newer immutable generation will be published.
    path = verify_artifact(root, record) if arrays is None else None
    job = job or jobs.read_job(Path(root) / "job.json")
    existing = next((obj for obj in scene.objects if obj.get("lichtfeld_job_id") == job["id"]), None)
    before = set(bpy.data.objects)
    selected = list(bpy.context.selected_objects)
    active = bpy.context.view_layer.objects.active
    try:
        if arrays is not None:
            cloud = pointcloud_from_arrays(arrays)
            imported = bpy.data.objects.new("LichtFeld Snapshot", cloud)
            scene.collection.objects.link(imported)
            added = [imported]
        else:
            bpy.ops.wm.ply_import(filepath=str(path), forward_axis="Y", up_axis="Z", import_attributes=True)
            added = list(set(bpy.data.objects) - before)
        if len(added) != 1 or added[0].type != "POINTCLOUD":
            for obj in added:
                bpy.data.objects.remove(obj, do_unlink=True)
            raise ValueError("Blender did not recognize this result as a Gaussian splat")
        imported = added[0]
        if existing:
            old = existing.data
            existing.data = imported.data
            bpy.data.objects.remove(imported, do_unlink=True)
            if old.users == 0:
                bpy.data.pointclouds.remove(old)
            result = existing
        else:
            result = imported
            result.name = "LichtFeld Result"
            result["lichtfeld_result"] = True
            result["lichtfeld_job_id"] = job["id"]
        current = ui_runtime.peek(scene)
        geometry = current.reader.geometry if current else None
        result.matrix_world = Matrix(source_model_transform(geometry).tolist())
        result["lichtfeld_iteration"] = record["iteration"]
        result["lichtfeld_artifact"] = record["id"]
        result["lichtfeld_transport"] = record["shared"]["transport"] if arrays is not None else "ply"
        settings = scene.lichtfeld_training
        layer = scene.view_layers.get(job.get("view_layer", "")) or scene.view_layers[0]
        set_hidden(result, not settings.show_result, layer)
        for name in job.get("sources", []):
            source = scene.objects.get(name)
            if source:
                set_hidden(source, settings.show_result or job.get("source_visibility", {}).get(name, False), layer)
        atomic_json(Path(root) / "ack.json", {"id": record["id"]})
    finally:
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        for obj in selected:
            if obj.name in bpy.context.view_layer.objects:
                obj.select_set(True)
        if active and active.name in bpy.context.view_layer.objects:
            bpy.context.view_layer.objects.active = active
    return True


class LF_OT_import(bpy.types.Operator):
    bl_idname = "lichtfeld.import_result"
    bl_label = "Import best result"
    bl_description = "Import the verified best candidate, keeping the result object stable"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not allowed(context, "import_best"):
            self.report({"ERROR"}, "Wait for a valid best candidate and the current import to finish")
            return {"CANCELLED"}
        current = ui_runtime.binding(context.scene)
        if current:
            current.reader.commands.put("import_best")
        return {"FINISHED"}


def training_settings(settings):
    values = {name: getattr(settings, name) for name in workflow.TRAINING_FIELDS
              if name not in {"optimizer", "optimizer_schedule", "optimizer_file"}}
    values["optimizer"] = training_options.optimizer_values(settings)
    values["optimizer_file"] = bpy.path.abspath(settings.optimizer_file) if settings.optimizer_file else ""
    values["optimizer_schedule"] = {name: getattr(settings, name) for name in ("refine_start", "refine_end", "growth_end")}
    return values


class LF_OT_dataset(bpy.types.Operator):
    bl_idname = "lichtfeld.use_dataset"
    bl_label = "Reuse this dataset"
    bl_description = "Prepare a new training on this job's saved images; preserve its existing result"
    def execute(self, context):
        s = context.scene.lichtfeld_training
        if s.running or s.operation_busy or not s.job_directory:
            return {"CANCELLED"}
        current = ui_runtime.peek(context.scene)
        s.dataset_job_directory = (current.status.get("export_directory") if current else "") or s.job_directory
        ui_runtime.reset_for_new_training(context.scene)
        s.dataset_mode = "REUSE"
        s.mode = "NEW"
        bpy.ops.lichtfeld.inspect_dataset()
        return {"FINISHED"}


class LF_OT_new_capture(bpy.types.Operator):
    bl_idname = "lichtfeld.new_capture"
    bl_label = "Prepare a new capture"
    bl_description = "Keep this camera/source setup and prepare a fresh capture; existing images and training results are preserved"

    def execute(self, context):
        settings = context.scene.lichtfeld_training
        if settings.running or settings.operation_busy or settings.attached_session:
            return {"CANCELLED"}
        current = ui_runtime.peek(context.scene)
        capture_only = settings.dataset_mode == "EXPORT" or bool(current and current.status.get("job", {}).get("capture_only"))
        ui_runtime.reset_for_new_training(context.scene)
        settings.mode = "NEW"
        settings.dataset_mode = "EXPORT" if capture_only else "CAPTURE"
        return {"FINISHED"}


class LF_OT_inspect_dataset(bpy.types.Operator):
    bl_idname = "lichtfeld.inspect_dataset"
    bl_label = "Check saved dataset"
    def execute(self, context):
        s = context.scene.lichtfeld_training
        directory = bpy.path.abspath(s.dataset_job_directory) if s.dataset_job_directory else s.job_directory
        s.dataset_summary = "Checking saved images…"
        def done(value):
            current = bpy.path.abspath(s.dataset_job_directory) if s.dataset_job_directory else s.job_directory
            if current != directory:
                return
            original, previous, dataset, manifest = value
            coverage = manifest.get("foreground_fraction")
            s.dataset_coverage = (f"Subject: {coverage:.0%} of image pixels" + (" - consider a closer rig" if coverage < .08 else "")
                                  if coverage is not None else "No saved foreground coverage")
            s.dataset_summary = f"{manifest['views']} saved views · {Path(original.get('source_file', '')).name}"
        def fail(error):
            current = bpy.path.abspath(s.dataset_job_directory) if s.dataset_job_directory else s.job_directory
            if current == directory:
                s.dataset_summary = str(error)
        ui_runtime.submit(lambda: jobs.captured_dataset(directory), done, fail)
        return {"FINISHED"}


class LF_OT_restart(bpy.types.Operator):
    bl_idname = "lichtfeld.restart_training"
    bl_label = "Start training on saved images"
    bl_description = "Create a separate run using these training settings and saved images. No cameras are rendered"
    @classmethod
    def poll(cls, context):
        s = context.scene.lichtfeld_training
        return not s.running and not s.operation_busy and not s.attached_session
    def execute(self, context):
        scene = context.scene
        s = scene.lichtfeld_training
        prefs = preferences(context)
        directory = bpy.path.abspath(s.dataset_job_directory) if s.dataset_job_directory else s.job_directory
        backend = bpy.path.abspath(prefs.backend_path)
        connection = preflight.connection_folder(prefs)
        jobs_directory = preflight.jobs_folder(prefs)
        options = training_settings(s)
        if not directory or not backend:
            self.report({"ERROR"}, "Choose a saved dataset job and configure LichtFeld in Settings")
            return {"CANCELLED"}
        s.operation_busy = True
        def work():
            job, previous = jobs.restart_data(directory, options, backend=backend, jobs_directory=jobs_directory, connection_directory=connection)
            launch_supervisor(job)
            return job
        def done(job):
            def activate():
                scene["lichtfeld_expected_job"] = job["id"]
                s.job_directory = job["root"]
                s.operation_busy = False
                s.running = True
                ui_runtime.reconnect(scene)
            ui_runtime.reset_for_new_training(scene, activate)
        def failed(error):
            s.operation_busy = False
            s.dataset_summary = str(error)
            ui_runtime.report_notice("ERROR", error)
        ui_runtime.submit(work, done, failed)
        return {"FINISHED"}


class LF_OT_resume(bpy.types.Operator):
    bl_idname = "lichtfeld.resume_job"
    bl_label = "Continue saved checkpoint"
    bl_description = "Continue saved model and optimizer state in a separate run"
    new_total: IntProperty(name="New total steps", min=4, max=1000000, default=2000)
    @staticmethod
    def saved_status(context):
        current = ui_runtime.peek(context.scene)
        return current.status if current else {}

    def default_total(self, context):
        status = self.saved_status(context)
        return max(int(status.get('job', {}).get('iterations', 2000)),
                   int(status.get('iteration', 0)) + 1000)

    def invoke(self, context, event):
        self.new_total = self.default_total(context)
        return context.window_manager.invoke_props_dialog(self, width=420)
    def draw(self, context):
        self.layout.label(text="Continue the saved model and optimizer.")
        self.layout.prop(self, "new_total")
        interval = self.saved_status(context).get('job', {}).get('eval_interval')
        if interval is not None:
            self.layout.label(text=f"Evaluate every {interval:,} steps (saved job)")
        self.layout.label(text="Saved training and preview settings are retained.")

    def execute(self, context):
        scene = context.scene
        settings = scene.lichtfeld_training
        if not allowed(context, "continue"):
            self.report({"ERROR"}, "A finished job with a saved checkpoint is required")
            return {"CANCELLED"}
        prefs = preferences(context)
        if not prefs.backend_path.strip():
            self.report({"ERROR"}, "Choose the LichtFeld executable in Settings before continuing")
            return {"CANCELLED"}
        backend = bpy.path.abspath(prefs.backend_path)
        connection_directory = preflight.connection_folder(prefs)
        jobs_directory = preflight.jobs_folder(prefs)
        directory = settings.job_directory
        steps = self.new_total if self.properties.is_property_set("new_total") else self.default_total(context)
        settings.operation_busy = True
        def work():
            job, previous = jobs.resume_data(directory, steps,
                                             backend=backend, jobs_directory=jobs_directory, connection_directory=connection_directory)
            launch_supervisor(job)
            return job, previous
        def done(value):
            job, previous = value
            result = settings.result_object
            if result and result.get("lichtfeld_job_id") == previous:
                result["lichtfeld_job_id"] = job["id"]
            settings.job_directory = job["root"]
            settings.operation_busy = False
            settings.running = True
        def failed(error):
            settings.operation_busy = False
            ui_runtime.fail(scene, error)
        ui_runtime.submit(work, done, failed)
        return {"FINISHED"}


class LF_OT_setup_view(bpy.types.Operator):
    bl_idname = "lichtfeld.setup_view"
    bl_label = "Choose setup view"
    bl_description = "Open capture setup, a saved job, or read-only trainer viewing without changing capture settings"
    mode: EnumProperty(items=[('NEW', 'Capture setup', 'Return to the current capture or saved-image setup'),
                              ('OPEN', 'Open job', 'Open a saved training job'),
                              ('CONNECT', 'View trainer', 'Find active trainers for read-only viewing')])

    @classmethod
    def poll(cls, context):
        settings = context.scene.lichtfeld_training
        return not (settings.operation_busy or settings.running or settings.importing or
                    settings.job_directory or settings.attached_session)

    @classmethod
    def description(cls, context, properties):
        return {'NEW': 'Return to capture or saved-image setup; your current settings are retained',
                'OPEN': 'Choose a saved training job to reopen; capture settings are retained',
                'CONNECT': 'Find active trainers for read-only viewing; this does not start or stop training'}.get(properties.mode, cls.bl_description)

    def execute(self, context):
        context.scene.lichtfeld_training.mode = self.mode
        if self.mode == 'CONNECT':
            refresh_sessions()
        return {'FINISHED'}


class LF_OT_scan_sessions(bpy.types.Operator):
    bl_idname = "lichtfeld.scan_sessions"
    bl_label = "Refresh trainers"

    def execute(self, context):
        refresh_sessions()
        return {"FINISHED"}


class LF_OT_attach_session(bpy.types.Operator):
    bl_idname = "lichtfeld.attach_session"
    bl_label = "Connect to trainer"
    bl_description = "View a running trainer without starting or stopping it"

    def execute(self, context):
        settings = context.scene.lichtfeld_training
        if settings.running or settings.operation_busy or settings.importing:
            self.report({"ERROR"}, "Finish this scene's current job before connecting another trainer")
            return {"CANCELLED"}
        session = next((item for item in _sessions if item["id"] == settings.discovered_session), None)
        if not session:
            self.report({"ERROR"}, "Refresh trainers and choose a session")
            return {"CANCELLED"}
        if settings.job_directory:
            settings.previous_job = settings.job_directory
        settings.job_directory = ""
        settings.attached_session = session["directory"]
        return {"FINISHED"}


class LF_OT_detach_session(bpy.types.Operator):
    bl_idname = "lichtfeld.detach_session"
    bl_label = "Disconnect viewer"
    bl_description = "Keep the last snapshot in Blender and leave the trainer running"

    def execute(self, context):
        scene = context.scene
        settings = scene.lichtfeld_training
        settings.attached_session = ""
        # Close the external observer now. A detached read-only snapshot remains
        # in the scene, while stale external status must not block reconnecting
        # to the retained local job or attaching another trainer.
        ui_runtime.reconnect(scene)
        settings.running = False
        settings.paused = False
        settings.importing = False
        from .session_cameras import remove_steps
        ui_runtime.schedule(remove_steps(scene))
        settings.status = "Disconnected"
        settings.detail = "The trainer continues independently."
        return {"FINISHED"}


class LF_OT_previous_job(bpy.types.Operator):
    bl_idname = "lichtfeld.previous_job"
    bl_label = "Return to previous job"
    bl_description = "Return to the job viewed before preparing this setup or connecting an external trainer"

    @classmethod
    def poll(cls, context):
        s = context.scene.lichtfeld_training
        return bool(s.previous_job) and not s.running and not s.attached_session and not s.operation_busy

    def execute(self, context):
        s = context.scene.lichtfeld_training
        s.job_directory, s.previous_job = s.previous_job, ""
        return {"FINISHED"}


class LF_OT_folder(bpy.types.Operator):
    bl_idname = "lichtfeld.open_job"
    bl_label = "Open job folder"
    bl_description = "Open the folder in the system file browser; an opening error does not stop capture, training or monitoring"
    dataset: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        scene = context.scene
        current = ui_runtime.peek(scene)
        path = (current.status.get("export_directory", "") if current else "") if self.dataset else scene.lichtfeld_training.job_directory
        label = "dataset folder" if self.dataset else "job folder"
        if not path:
            self.report({"WARNING"}, "The " + label + " is not available yet")
            return {"CANCELLED"}
        ui_runtime.submit(lambda: jobs.open_folder(path), failure=lambda error: ui_runtime.report_notice("WARNING", "Could not open " + label + ": " + str(error)))
        return {"FINISHED"}


class LF_OT_reconnect(bpy.types.Operator):
    bl_idname = "lichtfeld.reconnect_job"
    bl_label = "Reconnect viewer"
    bl_description = "Restart this connection without starting or stopping the trainer"

    def execute(self, context):
        ui_runtime.reconnect(context.scene)
        return {"FINISHED"}


class LF_OT_load_job(bpy.types.Operator):
    bl_idname = "lichtfeld.load_job"
    bl_label = "Open training job"
    bl_description = "Reconnect to a saved job.json without starting another trainer"
    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="job.json", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        scene = context.scene
        if scene.lichtfeld_training.running or scene.lichtfeld_training.operation_busy:
            self.report({"ERROR"}, "Wait for the active job before opening another")
            return {"CANCELLED"}
        path = self.filepath
        scene.lichtfeld_training.operation_busy = True
        def done(directory):
            settings = scene.lichtfeld_training
            settings.attached_session = ""
            settings.job_directory = directory
            ui_runtime.reconnect(scene)
            settings.operation_busy = False
        def failed(error):
            scene.lichtfeld_training.operation_busy = False
            ui_runtime.fail(scene, error)
        ui_runtime.submit(lambda: jobs.load_job(path), done, failed)
        return {"FINISHED"}


class LF_OT_color_look(bpy.types.Operator):
    bl_idname = "lichtfeld.color_view_look"
    bl_label = "Change preview display transform"
    bl_description = "Choose trainer RGB or decode the composited RGB and apply the source scene's view transform, look, exposure and gamma"

    def execute(self, context):
        scene = context.scene
        source = preview_scene.source_scene(scene)
        if not source or source.lichtfeld_training.operation_busy:
            return {"CANCELLED"}
        settings = source.lichtfeld_training
        settings.operation_busy = True
        enabled = not scene.get("lichtfeld_source_transform",False)
        def apply():
            try:
                yield from preview_scene.display_steps(source,scene,enabled)
            finally:
                settings.operation_busy = False
        ui_runtime.schedule(apply(),lambda error:ui_runtime.report_notice("ERROR",error))
        return {"FINISHED"}


class LF_OT_color_command(bpy.types.Operator):
    bl_idname = "lichtfeld.color_view_command"
    bl_label = "Control source training"
    action: StringProperty()

    def execute(self, context):
        source = preview_scene.source_scene(context.scene)
        if not source:
            return {"CANCELLED"}
        # Reuse the same ownership and state checks as the main training panel.
        try:
            with context.temp_override(scene=source):
                return bpy.ops.lichtfeld.training_command('EXEC_DEFAULT', action=self.action)
        except RuntimeError:
            return {"CANCELLED"}  # The source operator already reported the reason.


class LF_OT_model_view(bpy.types.Operator):
    bl_idname = "lichtfeld.model_view"
    bl_label = "Change viewed training model"
    bl_description = "Follow the live model or retain the best evaluated model as training continues; this never changes training or saved checkpoints"

    mode: EnumProperty(items=[("TOGGLE", "Toggle", "Switch model"), ("CURRENT", "Current", "Follow live training"), ("BEST", "Best evaluated", "Follow evaluation results")], default="TOGGLE")

    def execute(self, context):
        scene = preview_scene.source_scene(context.scene) or context.scene
        current = ui_runtime.peek(scene)
        if not current or current.reader.external:
            return {"CANCELLED"}
        if self.mode == "CURRENT" or self.mode == "TOGGLE" and current.status.get("viewer_mode") == "best":
            current.reader.commands.put("view_current")
        elif current.status.get("best"):
            current.reader.commands.put("view_best")
        return {"FINISHED"}


class LF_OT_color_view(bpy.types.Operator):
    bl_idname = "lichtfeld.trainer_color_view"
    bl_label = "Open trainer color view"
    bl_description = "Navigate the splat with RGB compositing matching the trainer, in an isolated scene; source materials and color settings stay intact"

    def execute(self, context):
        scene = context.scene
        if preview_scene.source_scene(scene):
            preview_scene.close_view(context)
            return {"FINISHED"}
        settings = scene.lichtfeld_training
        if not settings.result_object or settings.operation_busy:
            self.report({"WARNING"}, "Wait for the requested scene operation to finish")
            return {"CANCELLED"}
        settings.operation_busy = True
        window, area = context.window, context.area
        def apply():
            try:
                yield from preview_scene.open_steps(scene, window, area)
            finally:
                settings.operation_busy = False
        def failed(error):
            settings.operation_busy = False
            ui_runtime.report_notice("ERROR", error)
        ui_runtime.schedule(apply(), failed)
        return {"FINISHED"}


class LF_OT_capture_lighting(bpy.types.Operator):
    bl_idname = "lichtfeld.capture_lighting"
    bl_label = "Use scene lighting for comparison"
    bl_description = "Show source materials under the scene lights and world used to capture training images, instead of a different studio HDRI"

    def execute(self, context):
        if context.area and context.area.type == "VIEW_3D":
            space = context.area.spaces.active
            space.shading.type = "MATERIAL"
            space.shading.use_scene_lights = True
            space.shading.use_scene_world = True
        return {"FINISHED"}


class LF_OT_compare(bpy.types.Operator):
    bl_idname = "lichtfeld.compare_result"
    bl_label = "Toggle source and splat"

    def execute(self, context):
        scene = context.scene
        settings = scene.lichtfeld_training
        if not allowed(context, "compare"):
            return {"CANCELLED"}
        current = ui_runtime.binding(scene)
        job = current.status.get("job", {}) if current else {}
        settings.show_result = not settings.show_result
        show = settings.show_result
        layer = scene.view_layers.get(job.get("view_layer", "")) or scene.view_layers[0]
        def apply():
            if settings.result_object:
                set_hidden(settings.result_object, not show, layer)
            yield
            yield from source_steps(scene, job, show)
        ui_runtime.schedule(apply(), lambda error: ui_runtime.report_notice("WARNING", "Visibility update: " + str(error)))
        return {"FINISHED"}


def draw_training_controls(setup, settings, wrapped):
    box = setup.box()
    box.label(text="Training", icon="MOD_PARTICLES")

    box.prop(settings, "strategy",text="Method")
    box.prop(settings, "iterations")
    box.prop(settings, "max_points")
    box.prop(settings, "sh_degree")
    row = box.row(align=True)
    row.prop(settings, "use_depth_loss",text="Depth")
    row.prop(settings, "use_normal_loss",text="Normals")
    if settings.use_depth_loss:
        box.prop(settings, "depth_loss_weight")
        wrapped(box, "Metric camera-Z depth, float TIFF. SSI-depth supervision; no automatic map downloads.")
    if settings.use_normal_loss:
        box.prop(settings, "normal_loss_weight")
        box.prop(settings, "normal_start_fraction")
        box.prop(settings, "normal_end_fraction")
        wrapped(box, "Shading normals, linear encoding in camera OpenCV space. Float TIFF.")
    box.prop(settings, "training_advanced", icon="TRIA_DOWN" if settings.training_advanced else "TRIA_RIGHT", emboss=False)
    if settings.training_advanced:
        box.prop(settings, "eval_interval")
        box.prop(settings, "keep_all_checkpoints")
        box.prop(settings,"mask_mode")
        for group, fields in training_options.GROUPS.items():
            part = box.box()
            part.label(text=group)
            for name in fields:
                part.prop(settings, name)
            if group == 'Refinement':
                for name in ('refine_start','refine_end','growth_end'):
                    part.prop(settings, name)
        box.prop(settings,"optimizer_file")
        wrapped(box,"Expert JSON controls additional native parameters. Managed and visible fields must use the controls above.")
        wrapped(box, "Exposure correction combines PPISP and the bilateral grid. For consistent synthetic views these are off by default.")
    box = setup.box()
    box.label(text="Live splat preview", icon="OUTLINER_DATA_POINTCLOUD")
    box.prop(settings, "preview_mode", text="Updates")
    if settings.preview_mode == "PERIODIC":
        box.prop(settings, "preview_seconds", text="Minimum interval (s)")
    box.prop(settings, "full_preview")
    if not settings.full_preview:
        box.prop(settings, "preview_points")
        box.prop(settings, "preview_sh_degree")
    box.prop(settings, "preview_advanced", icon="TRIA_DOWN" if settings.preview_advanced else "TRIA_RIGHT", emboss=False)
    if settings.preview_advanced:
        box.prop(settings, "preview_hold_seconds")
        wrapped(box, "Updates adapt to training cost. A short settling time reduces Blender transparency noise; zero prioritizes speed. Saved results keep full quality.")


def draw_capture_format(box, scene, settings, wrapped):
    box.prop(scene.render, 'resolution_x', text='Width (px)')
    box.prop(scene.render, 'resolution_y', text='Height (px)')
    box.prop(scene.render, 'resolution_percentage', text='Scale %')
    row = box.row(align=True)
    row.operator('lichtfeld.render_settings', text='Output settings…', icon='OUTPUT').section = 'OUTPUT'
    box.prop(settings, 'format_presets_open', icon='TRIA_DOWN' if settings.format_presets_open else 'TRIA_RIGHT', emboss=False)
    if settings.format_presets_open:
        box.prop(settings, 'capture_aspect', text='Aspect')
        box.prop(settings, 'capture_orientation', text='Layout')
        box.prop(settings, 'resolution', text='Preset longest side')
        box.operator('lichtfeld.apply_capture_format', icon='CHECKMARK')
    try:
        value = capture_settings.format_for(scene, settings)
        box.label(text=f"Output: {value['width']} × {value['height']} px", icon='IMAGE_DATA')
        if value['pixel_aspect_x'] != value['pixel_aspect_y']:
            box.label(text=f"Pixel aspect: {value['pixel_aspect_x']:.3g}:{value['pixel_aspect_y']:.3g}")
    except ValueError as error:
        wrapped(box, str(error))


def draw_capture_overrides(box, settings, wrapped):
    enabled = any((settings.override_optics, settings.override_shift, settings.override_clipping))
    box.prop(settings, 'camera_overrides_open', text='Camera overrides' + (' · active' if enabled else ''),
             icon='TRIA_DOWN' if settings.camera_overrides_open else 'TRIA_RIGHT', emboss=False)
    if not settings.camera_overrides_open:
        return
    col = box.column(align=True)
    col.prop(settings, 'override_optics')
    if settings.override_optics:
        col.prop(settings, 'capture_lens')
        col.prop(settings, 'capture_sensor_fit')
        col.prop(settings, 'capture_sensor_width')
        col.prop(settings, 'capture_sensor_height')
    col.prop(settings, 'override_shift')
    if settings.override_shift:
        col.prop(settings, 'capture_shift_x')
        col.prop(settings, 'capture_shift_y')
    col.prop(settings, 'override_clipping')
    if settings.override_clipping:
        col.prop(settings, 'capture_clip_start')
        col.prop(settings, 'capture_clip_end')
    wrapped(box, 'Applies to every capture camera. Unchecked settings keep each camera’s evaluated values. Original cameras and their positions stay unchanged.')


def draw_setup_section(layout, settings, name, title, icon, *, closed=True):
    header, content = layout.panel('lichtfeld_setup_' + name, default_closed=closed)
    header.enabled = not settings.operation_busy
    header.label(text=title, icon=icon)
    if content is not None:
        content.enabled = not settings.operation_busy
    return content


def draw_capture_plan(setup, context, settings, ready, pending):
    """Read only the existing readiness snapshot and bounded RNA properties."""
    box = setup.box()
    scope = {'SCENE': 'Entire scene', 'COLLECTION': 'Isolated collection', 'SELECTION': 'Isolated objects'}[settings.scope]
    box.label(text=scope, icon='SCENE_DATA')
    value = ready.get('format')
    box.label(text=f"{len(ready['cameras']):,} cameras · {value['width']} × {value['height']} px" if value else 'Cameras and output: checking…')
    rig = settings.camera_collection
    managed = rig and rig.get('lichtfeld_generated')
    box.label(text='Updating setup…' if settings.operation_busy else
                   'Load selected rig before editing' if managed and settings.rig_parameter_source != rig else
                   'Rig/framing changes need Apply' if pending else
                   'Checking camera setup…' if ready.get('checking') else 'Capture uses current cameras',
              icon='INFO')
    header, details = box.panel('lichtfeld_capture_plan', default_closed=True)
    header.label(text='Capture details', icon='INFO')
    if details is None:
        return
    box = details
    box.label(text='View Layer: ' + context.view_layer.name)
    reference = rig.lichtfeld_rig_reference if managed else settings.rig_reference
    if not managed or reference.mode == 'UNSET':
        subject = 'Existing camera poses'
    elif reference.mode == 'COLLECTION':
        subject = reference.collection.name if reference.collection else 'Missing reference collection'
    elif reference.mode == 'SCENE':
        subject = 'Entire scene'
    else:
        entry = reference.objects[0] if reference.objects else None
        subject = (entry.object.name if entry.object else 'Missing: ' + entry.name) if entry else 'No reference objects'
        if len(reference.objects) > 1:
            subject += f' (+{len(reference.objects)-1:,})'
    box.label(text='Rig reference: ' + subject)
    render = ready.get('render')
    render_lines = scene_render.summary_lines(render, ready.get('render_device'))
    engine = render_lines[0] + f" · {render['samples']:,} samples" if render else 'Render settings: checking…'
    box.label(text=engine)
    box.label(text=f'Lens override: {settings.capture_lens:g} mm' if settings.override_optics else
                   'Shift/clipping overrides active' if settings.override_shift or settings.override_clipping else 'Lens: each capture camera')
    output = 'Dataset only' if settings.dataset_mode == 'EXPORT' else f'{settings.iterations:,} training steps'
    box.label(text=f'Frame {context.scene.frame_current} · {output}')



def draw_capture_sources(box, context, settings):
    box.prop(settings, "scope", text="Content")
    if settings.scope == "COLLECTION":
        box.prop(settings, "source_collection", text="")
    elif settings.scope == "SELECTION":
        box.operator("lichtfeld.pick_sources", icon="RESTRICT_SELECT_OFF").for_rig = False
    box.prop(settings, "quality")
    box.label(text=f"Static capture at frame {context.scene.frame_current}")


def draw_rig_actions(layout, settings, pending, *, edit=False, compact=False):
    """One action site, in the camera body or its collapsed header."""
    rig = settings.camera_collection
    managed = rig and rig.get('lichtfeld_generated')
    row = layout.row(align=True)
    if managed and settings.rig_parameter_source != rig:
        row.operator('lichtfeld.read_rig', text='Load' if compact else 'Load selected rig', icon='IMPORT')
    elif not rig or edit or managed and pending:
        row.operator('lichtfeld.create_rig', text=('Apply' if managed else 'Create') if compact else
                     ('Apply rig changes' if managed else 'Create camera rig'), icon='CAMERA_DATA')
        if managed and pending:
            row.operator('lichtfeld.read_rig', text='' if compact else 'Revert', icon='LOOP_BACK')
    if rig:
        (row if compact else layout).operator('lichtfeld.capture_framing', text='' if compact else 'Check framing', icon='VIEW_CAMERA')


def draw_capture_cameras(box, context, settings, ready, rig_changed, wrapped):
    rig = settings.camera_collection
    managed = rig and rig.get('lichtfeld_generated')
    box.prop(settings, 'camera_collection', text='Existing' if not rig else 'Cameras')
    if rig:
        box.label(text='Updating setup…' if settings.operation_busy else
                       'Checking cameras…' if ready.get('checking') else f"{len(ready['cameras']):,} capture cameras")
        additional = len(ready['cameras']) - ready.get('generated_cameras', 0)
        if managed and additional > 0:
            box.label(text=f"{ready.get('generated_cameras', 0):,} generated · {additional:,} additional")
        if managed and settings.rig_parameter_source != rig:
            draw_rig_actions(box, settings, rig_changed)
            return
        box.prop(settings, 'rig_generator', text='Edit rig' if managed else 'Create a new rig',
                 icon='TRIA_DOWN' if settings.rig_generator else 'TRIA_RIGHT', emboss=False)
        if not settings.rig_generator:
            draw_rig_actions(box, settings, rig_changed)
            return
    else:
        box.label(text='Updating setup…' if settings.operation_busy else 'Or create a rig',
                  icon='TIME' if settings.operation_busy else 'CAMERA_DATA')
    reference = settings.rig_reference
    box.prop(reference, 'mode', text='Center')
    if reference.mode == 'COLLECTION':
        box.prop(reference, 'collection', text='')
    elif reference.mode == 'OBJECTS':
        if reference.objects:
            entry = reference.objects[0]
            name = entry.object.name if entry.object else 'Missing: ' + entry.name
            box.label(text=name + (f' (+{len(reference.objects)-1:,})' if len(reference.objects) > 1 else ''))
        box.operator('lichtfeld.pick_sources', text='Use selected objects as center', icon='RESTRICT_SELECT_OFF').for_rig = True
    elif reference.mode == 'UNSET' and managed:
        wrapped(box, 'Capture content applies only to new rigs. Choose an explicit center for this older rig.')
    box.prop(settings, 'rig_mode', text='Shape')
    if settings.rig_mode != 'MESH' or settings.rig_mesh_distribution != 'FACES':
        box.prop(settings, 'views', text='Generated cameras')
    if settings.rig_mode not in {'PATH', 'MESH'}:
        box.prop(settings, 'rig_auto_fit', text='Fit subject automatically')
        box.prop(settings, 'rig_distance' if settings.rig_auto_fit else 'rig_radius',
                 text='Minimum distance factor' if settings.rig_auto_fit else 'Distance from center')
    elif settings.rig_mode == 'PATH':
        box.prop(settings, 'rig_curve')
        box.prop(settings, 'rig_orientation')
    else:
        box.prop(settings, 'rig_mesh')
        box.prop(settings, 'rig_mesh_distribution')
        box.prop(settings, 'rig_mesh_orientation')
        box.prop(settings, 'rig_mesh_offset')
    box.prop(settings, 'rig_details', icon='TRIA_DOWN' if settings.rig_details else 'TRIA_RIGHT', emboss=False)
    if settings.rig_details:
        if settings.rig_mode not in {'PATH', 'MESH'}:
            if settings.rig_mode != 'SPHERE':
                box.prop(settings, 'rig_min')
                box.prop(settings, 'rig_max')
            if settings.rig_mode == 'RINGS':
                box.prop(settings, 'rig_rings')
            box.prop(settings, 'rig_phase')
            box.operator('lichtfeld.tight_framing', icon='VIEW_CAMERA')
        box.prop(settings, 'rig_lens')
        uses_aim = not (settings.rig_mode == 'PATH' and settings.rig_orientation == 'TANGENT' or
                        settings.rig_mode == 'MESH' and settings.rig_mesh_orientation != 'TARGET')
        if uses_aim:
            box.prop(settings, 'rig_aim_mode')
            if settings.rig_aim_mode == 'OBJECT':
                box.prop(settings, 'rig_target')
            elif settings.rig_aim_mode == 'POSITION':
                box.prop(settings, 'rig_aim_position')
            box.prop(settings, 'rig_aim_offset')
            if settings.rig_aim:
                box.operator('lichtfeld.select_aim', text='Move aim target in viewport', icon='EMPTY_AXIS')
        wrapped(box, 'Updating replaces generated camera placement and preserves additional cameras. Undo restores the previous rig.')
    draw_rig_actions(box, settings, rig_changed, edit=True)


def draw_capture_options(box, context, settings, capture_only, wrapped):
    scene_render.draw(box, context.scene, wrapped)
    draw_capture_format(box, context.scene, settings, wrapped)
    draw_capture_overrides(box, settings, wrapped)
    if capture_only:
        row = box.row(align=True)
        row.prop(settings, "use_depth_loss", text="Depth maps")
        row.prop(settings, "use_normal_loss", text="Normal maps")
        if settings.use_depth_loss or settings.use_normal_loss:
            wrapped(box, "Float TIFF maps in LichtFeld camera coordinates, aligned with the RGB images.")
    if settings.use_depth_loss and settings.depth_loss_weight > 0:
        box.label(text="Points: rendered views (depth)")
    else:
        box.prop(settings, "seed_method", text="Points")
    if settings.capture_advanced:
        box.prop(settings, "seed_points")
        wrapped(box, "Object captures include foreground alpha masks. Denoising and Persistent Data follow the saved Cycles settings.")


def draw_capture_actions(setup, settings, ready, errors, capture_only, wrapped):
    checking = settings.operation_busy or ready.get('checking') or ready.get('environment', {}).get('checking')
    setup.label(text='Updating setup…' if settings.operation_busy else
                     'Checking capture setup…' if checking else errors[0] if errors else 'Ready to capture',
                icon='TIME' if checking else 'ERROR' if errors else 'CHECKMARK')
    if not bpy.data.filepath or bpy.data.is_dirty:
        setup.operator('wm.save_as_mainfile' if not bpy.data.filepath else 'wm.save_mainfile',
                       text='Save scene before capture', icon='FILE_TICK')
    row = setup.row()
    row.enabled = not errors and not checking
    row.scale_y = 1.4
    row.operator('lichtfeld.export_dataset' if capture_only else 'lichtfeld.start_training',
                 text='Generate dataset…' if capture_only else 'Capture and start training',
                 icon='EXPORT' if capture_only else 'PLAY')


class LF_PT_training(bpy.types.Panel):
    bl_label = "LichtFeld Training"
    bl_idname = "LF_PT_training"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "LichtFeld"
    bl_ui_units_x = 20

    def draw(self, context):
        layout = self.layout
        scale = bpy.context.preferences.system.ui_scale
        width = context.region.width
        if getattr(self, 'is_popover', False):
            width = min(width, LF_PT_training.bl_ui_units_x * 20 * scale)
        def wrapped(container, text):
            draw_paragraph(container, text, width, scale)
        if capture_preview.is_preview(context.scene):
            capture_source = capture_preview.source_scene(context.scene)
            if capture_source:
                capture_preview.draw(layout, context, capture_source, width=width)
            else:
                capture_preview.draw_orphaned(layout, context)
            return
        if preview_scene.is_preview(context.scene):
            color_source = preview_scene.source_scene(context.scene)
            if color_source:
                box = layout.box()
                box.label(text="Trainer colors", icon="SHADING_RENDERED")
                box.operator("lichtfeld.trainer_color_view", text="Back to scene", icon="BACK")
                source_look = context.scene.get("lichtfeld_source_transform", False)
                box.operator("lichtfeld.color_view_look", text="Use trainer colors" if source_look else "Use source display transform")
                current = ui_runtime.peek(color_source)
                from . import job_panel
                job_panel.draw(layout, context, color_source.lichtfeld_training, current.status if current else {},
                               current.reader.error if current else "", wrapped, color_view=True)
            else:
                preview_scene.draw_orphaned(layout, context, wrapped)
            return
        settings = context.scene.lichtfeld_training
        prefs = preferences(context)
        current = ui_runtime.peek(context.scene)
        status = current.status if current else {}
        if settings.attached_session or settings.job_directory:
            from . import job_panel
            job_panel.draw(layout, context, settings, status, current.reader.error if current else "", wrapped)
            return
        if settings.detail:
            wrapped(layout.box(), settings.detail)
        row = layout.row(align=True)
        row.enabled = not settings.operation_busy
        if settings.mode != 'NEW':
            row.operator('lichtfeld.setup_view', text='Back to setup', icon='BACK').mode = 'NEW'
        else:
            row.operator('lichtfeld.setup_view', text='Open job', icon='FILEBROWSER').mode = 'OPEN'
            row.operator('lichtfeld.setup_view', text='View trainer', icon='LINKED').mode = 'CONNECT'
        row.operator('lichtfeld.setup', text='', icon='PREFERENCES')
        row.operator('wm.url_open', text='', icon='QUESTION').url = 'https://github.com/MrNeRF/lichtfeld-blender-plugin/blob/main/docs/user-guide.md'
        if settings.previous_job:
            layout.operator('lichtfeld.previous_job', text='Return to previous job', icon='RECOVER_LAST')
        if settings.mode == "OPEN":
            layout.operator("lichtfeld.load_job", icon="FILEBROWSER")
            return
        if settings.mode == "CONNECT":
            layout.label(text="View an active trainer", icon='LOCKED')
            wrapped(layout, 'Read-only: view the model and cameras. Training stays under its owner’s control.')
            layout.label(text='Searching for trainers…' if _session_scan_pending else 'Refresh to update the list',
                         icon='TIME' if _session_scan_pending else 'INFO')
            column = layout.column()
            column.enabled = not _session_scan_pending
            column.operator("lichtfeld.scan_sessions", icon="FILE_REFRESH")
            column.prop(settings, "discovered_session", text="")
            row = column.row()
            row.enabled = settings.discovered_session not in {'NONE', ''}
            row.operator("lichtfeld.attach_session", icon="LINKED")
            return
        setup = layout.column()
        setup.enabled = not settings.operation_busy
        setup.prop(settings, "dataset_mode", text="Activity")
        if settings.dataset_mode == "REUSE":
            box = setup.box()
            box.label(text="Saved capture", icon="IMAGE_DATA")
            box.prop(settings, "dataset_job_directory", text="Job folder")
            box.operator("lichtfeld.inspect_dataset", icon="VIEWZOOM")
            box.label(text=settings.dataset_summary)
            box.label(text=settings.dataset_coverage)
            wrapped(box, "Uses saved images and camera poses. Scene or rig edits require a new capture.")
            wrapped(box, "Review the settings below, then start training. No images will be rendered again.")
            row = setup.row()
            row.scale_y = 1.4
            row.operator("lichtfeld.restart_training", text="Train saved images from scratch", icon="PLAY")
            box = draw_setup_section(layout, settings, 'training', 'Training and preview', 'MOD_PARTICLES')
            if box is not None:
                draw_training_controls(box, settings, wrapped)
            return
        capture_only = settings.dataset_mode == "EXPORT"
        ready = preflight.cached(context, prefs, capture_only=capture_only)
        errors = preflight.issues(ready)
        rig = settings.camera_collection
        pending = rig and rig.get('lichtfeld_generated') and (rig.get('lichtfeld_rig_recipe') != rig_recipe(settings) or ready.get('reference_changed', False))
        box = draw_setup_section(layout, settings, 'content', '1 · Content and quality', 'SCENE_DATA', closed=False)
        if box is not None:
            draw_capture_sources(box, context, settings)
        header, box = layout.panel('lichtfeld_setup_cameras', default_closed=False)
        header.enabled = not settings.operation_busy
        header.label(text='Updating…' if settings.operation_busy else '2 · Cameras',
                     icon='TIME' if settings.operation_busy else 'CAMERA_DATA')
        if box is not None:
            box.enabled = not settings.operation_busy
            draw_capture_cameras(box, context, settings, ready, pending, wrapped)
        else:
            draw_rig_actions(header, settings, pending, compact=True)
        box = draw_setup_section(layout, settings, 'output', 'Render and output', 'RENDER_STILL')
        if box is not None:
            draw_capture_options(box, context, settings, capture_only, wrapped)
        if not capture_only:
            box = draw_setup_section(layout, settings, 'training', 'Training and preview', 'MOD_PARTICLES')
            if box is not None:
                draw_training_controls(box, settings, wrapped)
        if not rig:
            return
        start = layout.column()
        start.enabled = not settings.operation_busy
        start.label(text='3 · Generate dataset' if capture_only else '3 · Capture and train', icon='PLAY')
        draw_capture_plan(start, context, settings, ready, pending)
        draw_capture_actions(start, settings, ready, errors, capture_only, wrapped)
        box = layout.box()
        box.enabled = not settings.operation_busy
        box.prop(settings, "validation_details", icon="TRIA_DOWN" if settings.validation_details else "TRIA_RIGHT", emboss=False)
        if settings.validation_details:
            for error in errors[:5]:
                wrapped(box, error)
                if name := ready.get('camera_issues', {}).get(error):
                    box.operator('lichtfeld.inspect_capture_camera', icon='CAMERA_DATA').camera_name = name
            if len(errors) > 5:
                wrapped(box, f'{len(errors)-5:,} more issues; resolve the listed issues first.')
            if not errors:
                box.label(text="Capture setup is ready", icon="CHECKMARK")


def poll_jobs():
    if bpy.context.scene and not preview_scene.source_scene(bpy.context.scene):
        preflight.tick(bpy.context, preferences())
    return ui_runtime.tick()


@persistent
def saved(_):
    preflight.saved()


@persistent
def unloading(_):
    global _session_scan_pending, _sessions, _session_items
    _session_scan_pending = False
    _sessions = []
    _session_items = _SESSION_PLACEHOLDER
    for scene in bpy.data.scenes:
        scene.lichtfeld_training.discovered_session = 'NONE'
    preflight.clear()
    scene_render.clear()
    ui_runtime.clear()
    capture_preview.cleanup()


@persistent
def loaded(_):
    preflight.clear()
    ui_runtime.clear()
    _imported.clear()
    source = bpy.data.filepath
    if not source:
        return
    directory = preflight.connection_folder(preferences())
    for scene in bpy.data.scenes:
        settings = scene.lichtfeld_training
        if settings.job_directory or settings.attached_session:
            continue
        name = scene.name
        def done(root, scene=scene, source=source):
            try:
                settings = scene.lichtfeld_training
                if root and bpy.data.filepath == source and not settings.job_directory and not settings.attached_session and not settings.running:
                    settings.job_directory = root
            except ReferenceError:
                pass
        ui_runtime.submit(lambda name=name: jobs.remembered_job(directory, source, name), done)


_CLASSES = (LichtFeldPreferences, LichtFeldSourceRef, LichtFeldRigReference, LichtFeldSettings, LF_OT_notice, LF_OT_verify, LF_OT_setup, LF_OT_tight_framing, LF_OT_rig,
            LF_OT_start, LF_OT_export_dataset, LF_OT_pick_sources, LF_OT_select_aim, LF_OT_inspect_camera, LF_OT_read_rig, LF_OT_command, LF_OT_import, LF_OT_resume, LF_OT_dataset, LF_OT_new_capture, LF_OT_inspect_dataset, LF_OT_restart, LF_OT_folder, LF_OT_reconnect, LF_OT_load_job, LF_OT_compare,
            LF_OT_setup_view, LF_OT_scan_sessions, LF_OT_attach_session, LF_OT_detach_session, LF_OT_previous_job, LF_OT_color_look, LF_OT_color_command, LF_OT_model_view, LF_OT_color_view, LF_OT_capture_lighting,
            *capture_preview.CLASSES, *scene_render.CLASSES, LF_PT_training)

ui_help.apply(_CLASSES)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.lichtfeld_training = PointerProperty(type=LichtFeldSettings)
    bpy.types.Collection.lichtfeld_rig_reference = PointerProperty(type=LichtFeldRigReference)
    bpy.app.handlers.save_post.append(saved)
    bpy.app.handlers.load_post.append(loaded)
    bpy.app.handlers.load_pre.append(unloading)
    bpy.app.timers.register(poll_jobs, first_interval=1, persistent=True)


def unregister():
    unloading(None)
    _imported.clear()
    if bpy.app.timers.is_registered(poll_jobs):
        bpy.app.timers.unregister(poll_jobs)
    if saved in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(saved)
    if loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(loaded)
    if unloading in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(unloading)
    del bpy.types.Scene.lichtfeld_training
    del bpy.types.Collection.lichtfeld_rig_reference
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _imported.clear()
