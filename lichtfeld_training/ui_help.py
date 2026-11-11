# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""User-facing help shared by RNA definitions and context-sensitive buttons."""

PROPERTIES = {
    'mode': 'Create a dataset or training job, reopen a saved job, or view another running trainer',
    'dataset_mode': 'Choose capture with automatic training, dataset export only, or a fresh training on saved images',
    'dataset_job_directory': 'Folder containing an exported dataset or a previous LichtFeld Training job; images are reused without rendering',
    'dataset_coverage': 'Foreground coverage measured from the saved capture; low coverage can reduce subject detail',
    'dataset_summary': 'Result of the latest check of the selected saved dataset',
    'job_details': 'Show diagnostic messages, capture devices, saved files and recovery actions for this job',
    'previous_job': 'Job viewed before connecting an external trainer or preparing a new run',
    'operation_busy': 'A scene operation is being prepared in small steps; conflicting edits are temporarily unavailable',
    'importing': 'A complete splat update is being prepared while the previous model remains visible',
    'ui_max_ms': 'Longest measured chunk of work applying training previews on Blender’s main thread',
    'result_object': 'Blender point cloud containing the complete currently displayed Gaussian model',
    'discovered_session': 'Choose an active, compatible trainer on this computer; connecting gives read-only viewing',
    'attached_session': 'Session currently viewed in read-only mode; its owner retains training controls',
    'show_session_cameras': 'Show or hide camera helpers received from the connected trainer; training is unaffected',
    'session_camera_count': 'Number of capture cameras received from the connected trainer',
    'scope': 'Capture selected surfaces, surfaces in a collection, or the whole scene with its existing lighting',
    'source_objects': 'Remembered source objects, retained when selecting or editing capture cameras',
    'source_selection': 'Saved names used to recover source selection from older projects',
    'source_collection': 'Collection whose surface objects are captured; available when the source is Collection',
    'camera_collection': 'Collection of at least four Perspective cameras; nested cameras are included in name order',
    'quality': 'Apply a capture size, camera count and training budget preset; review the scene render settings and rig before starting',
    'rig_mode': 'Place generated cameras on a hemisphere, sphere, rings, curve path or helper mesh',
    'rig_min': 'Lowest camera elevation above the rig reference center, in degrees; used by caps and rings',
    'rig_max': 'Highest camera elevation above the rig reference center, in degrees; used by caps and rings',
    'rig_rings': 'Distribute the total camera count between this many orbits at different elevations',
    'rig_radius': 'Distance from the rig reference center to generated cameras when automatic framing is disabled',
    'rig_distance': 'Minimum distance relative to the rig reference bounding radius; automatic framing can increase it to fit the image',
    'rig_phase': 'Rotate the generated sphere, hemisphere or rings around the rig reference vertical axis',
    'rig_lens': 'Focal length for generated cameras, in millimeters; capture overrides can replace it on capture copies',
    'rig_target': 'Object whose world origin is used as the generated cameras’ aiming target',
    'rig_aim': 'Generated Empty controlling camera aim; moving it updates tracking cameras in the viewport',
    'rig_aim_mode': 'Choose how to position the aiming target independently of the camera distribution center',
    'rig_aim_position': 'Explicit world-space position of the aiming target before the aim offset is added',
    'rig_curve': 'One evaluated Poly, Bezier or NURBS spline sampled at equal world-space distances; excluded from capture',
    'rig_mesh': 'Evaluated helper mesh used to place cameras; the helper is excluded from captured images',
    'rig_mesh_distribution': 'Sample the helper surface at the requested count, or place one camera at each face center',
    'rig_mesh_orientation': 'Point cameras inward, outward along helper normals, or toward the chosen aiming target',
    'rig_mesh_offset': 'Offset camera positions along the helper’s world-space face normals, in scene units',
    'seed_method': 'Initialize Gaussians from rendered visible surfaces and image colors, or from sampled surface geometry',
    'seed_points': 'Maximum initial point count shared across all captured views; positive depth supervision needs enough visible correspondences',
    'rig_orientation': 'Aim path cameras at the target or along the curve tangent; camera positions stay on the sampled path',
    'rig_generator': 'Show parameters for creating or updating generated cameras; existing cameras are used until Apply is pressed',
    'rig_details': 'Show generated-camera distance, elevation, rotation and lens controls',
    'optimizer_file': 'Optional native optimizer JSON; managed parameters must be set with the visible training controls',
    'mask_mode': 'Choose how native training uses foreground alpha masks; whole-scene captures use Automatic or None',
    'strategy': 'Native LichtFeld training strategy; changing it applies that strategy’s optimizer defaults',
    'refine_start': 'Fraction of total training steps after which native refinement begins',
    'refine_end': 'Fraction of total training steps after which native refinement stops',
    'growth_end': 'Fraction of total training steps after which Gaussian growth stops; must not exceed refinement end',
    'keep_all_checkpoints': 'Keep checkpoints produced during training instead of retaining only the normal managed outputs',
    'training_advanced': 'Show evaluation frequency, mask behavior and native optimizer parameters',
    'capture_advanced': 'Show capture-specific output rules, point initialization budget and device diagnostics',
    'preview_advanced': 'Show preview settling behavior and further viewport update settings',
    'use_depth_loss': 'Capture metric camera-Z depth TIFFs, or require saved depth maps when reusing images; positive weight enables native depth supervision',
    'use_normal_loss': 'Capture camera-space normal TIFFs, or require saved normal maps when reusing images; positive weight enables normal supervision',
    'depth_loss_weight': 'Weight of native depth supervision; zero keeps the exported maps but disables this training loss',
    'normal_loss_weight': 'Weight of native normal supervision; zero keeps the exported maps but disables this training loss',
    'normal_start_fraction': 'Fraction of total steps at which normal supervision begins',
    'normal_end_fraction': 'Fraction of total steps at which normal supervision ends; must be after its start',
    'cycles_device': 'Alias of the scene’s official Cycles Device setting; GPU Compute uses enabled devices from System Preferences',
    'views': 'Total number of generated cameras; existing custom cameras in the collection remain additional capture views',
    'resolution': 'Longest output dimension for the size preset; press Apply size preset to update official Output Properties',
    'capture_format': 'Legacy size-preset helper; actual capture dimensions always come from official Blender Output Properties',
    'capture_aspect': 'Aspect ratio for the size preset; takes effect when Apply size preset is pressed',
    'capture_orientation': 'Landscape or portrait orientation for the size preset; applied at 100% output scale',
    'capture_width': 'Width used by legacy custom size presets; new captures read the scene Output Properties',
    'capture_height': 'Height used by legacy custom size presets; new captures read the scene Output Properties',
    'format_presets_open': 'Show aspect-ratio and size helpers that can be explicitly applied to Blender Output Properties',
    'camera_overrides_open': 'Show optional capture-only lens, sensor, shift and clipping overrides for all cameras',
    'capture_lens': 'Common focal length in millimeters, applied to capture copies when the optics override is enabled',
    'capture_sensor_width': 'Common sensor width in millimeters for capture copies; affects field of view and calibration',
    'capture_sensor_height': 'Common sensor height in millimeters for capture copies; affects field of view and calibration',
    'capture_sensor_fit': 'Choose which sensor dimension determines the field of view for the capture format',
    'capture_shift_x': 'Horizontal optical shift on capture copies; zero centers the optical axis horizontally',
    'capture_shift_y': 'Vertical optical shift on capture copies; zero centers the optical axis vertically',
    'capture_clip_start': 'Nearest visible distance for capture copies, in scene units; must be smaller than Far',
    'capture_clip_end': 'Farthest visible distance for capture copies, in scene units; must be greater than Near',
    'samples': 'Alias of the active scene renderer’s official maximum render sample count',
    'iterations': 'Total native training step budget; a fresh run starts at step zero',
    'max_points': 'Maximum number of Gaussians in the trained model; also bounds the initialization point budget',
    'sh_degree': 'Maximum spherical-harmonic degree learned for view-dependent color; SH 0 stores base color only',
    'preview_mode': 'Choose adaptive live model updates or a configured minimum interval; saved models retain full quality',
    'engine': 'Alias of the official scene Render Engine; supported capture engines are Cycles and EEVEE',
    'validation_details': 'Expand the current capture readiness checks and any issues that prevent starting',
    'advanced': 'Legacy expansion state for capture and training details',
    'job_directory': 'Folder containing this job’s configuration, captured dataset, logs and saved training results',
    'status': 'Current stage reported by this job’s capture or training process',
    'detail': 'Latest stage detail or recoverable error reported by the job',
    'progress': 'Fraction of capture views or training steps completed in the current stage',
    'running': 'Whether this scene’s selected job is still active',
    'paused': 'Whether the owned trainer has acknowledged a pause request',
    'previews_enabled': 'Whether live model transfer is enabled; freezing previews does not pause training',
    'show_result': 'Display the trained splat and hide captured source geometry, or restore the source visibility',
    'object': 'Source object retained independently of the current viewport selection',
    'verification': 'Result of checking the configured LichtFeld executable and output folder',
    'directory': 'Choose the parent folder for a new, uniquely named complete dataset export',
    'new_total': 'New total step count, including steps already completed in the saved checkpoint',
    'action': 'Action sent to the owned job; connected external trainers remain read-only',
    'dataset': 'Open the published exported dataset instead of the internal job folder',
    'filepath': 'Select the job.json file of a saved LichtFeld Training job',
    'filter_glob': 'Show LichtFeld Training job.json files in the file picker',
    'section': 'Open official Render settings or Output dimensions for the source scene',
    'level': 'Severity of the displayed LichtFeld notification',
    'message': 'Text of the LichtFeld notification',
}

TRAINING = {
    'means_lr': 'Initial learning rate for Gaussian positions',
    'means_lr_end': 'Final learning rate for Gaussian positions',
    'shs_lr': 'Learning rate for color coefficients; -1 uses the MRNF automatic value',
    'opacity_lr': 'Learning rate for Gaussian opacity',
    'scaling_lr': 'Initial learning rate for Gaussian scales',
    'scaling_lr_end': 'Final learning rate for Gaussian scales',
    'rotation_lr': 'Learning rate for Gaussian rotations',
    'sh_degree_interval': 'Training steps between increases in the active spherical-harmonic degree',
    'refine_every': 'Number of training steps between native refinement operations',
    'growth_grad_threshold': 'Native gradient threshold used when deciding which Gaussians need more detail',
    'grow_fraction': 'Native growth fraction; -1 uses the MRNF automatic value',
    'use_error_map': 'Use image reconstruction error to guide native refinement',
    'use_edge_map': 'Use image edges to guide native refinement',
    'opacity_decay': 'Opacity decay applied by the selected native refinement strategy',
    'scale_decay': 'Scale decay applied by the selected native refinement strategy',
    'lambda_dssim': 'Contribution of structural-similarity loss relative to the image reconstruction loss',
    'opacity_reg': 'Weight of native opacity regularization; zero disables this term',
    'scale_reg': 'Weight of native scale regularization; zero disables this term',
    'erank_reg': 'Weight of native effective-rank regularization; zero disables this term',
    'normal_consistency_weight': 'Weight of the native normal-consistency loss; zero disables this term',
    'normal_flatten_weight': 'Weight of the native Gaussian flattening loss; zero disables this term',
    'use_exposure_correction': 'Use combined native exposure correction, replacing standalone PPISP and bilateral-grid controls',
    'use_ppisp': 'Use standalone native PPISP appearance correction; disabled by default for consistent synthetic views',
    'use_bilateral_grid': 'Use standalone native bilateral-grid appearance correction',
    'mip_filter': 'Enable the native mip filter during training',
    'enable_sparsity': 'Enable the native optimizer’s Gaussian sparsity control',
}

OPERATORS = {
    'LF_OT_rig': 'Create or update the chosen camera distribution and aiming rig; custom cameras remain in the collection',
    'LF_OT_command': 'Control the owned training job; external trainer previews remain read-only',
    'LF_OT_inspect_dataset': 'Check saved images, camera calibration, enabled maps and initialization data without starting a renderer or trainer',
    'LF_OT_scan_sessions': 'Refresh the list of active compatible trainers on this computer; completed and dead sessions are excluded',
    'LF_OT_color_command': 'Control the source scene’s owned trainer while viewing its isolated color preview',
    'LF_OT_compare': 'Switch between the trained splat and captured source geometry, restoring the source’s previous visibility',
    'LF_OT_notice': 'Display a LichtFeld information, warning or error notification',
}

ACTIONS = {
    'pause': 'Pause the owned trainer at a safe step; the model and optimizer remain in memory',
    'resume': 'Resume the paused owned trainer from its current model and optimizer state',
    'stop': 'Stop capture and preserve completed images, or stop training and save a resumable checkpoint',
    'preview_off': 'Stop transferring live model updates; training continues and the last complete preview stays visible',
    'preview_on': 'Resume automatic live model updates without restarting training',
    'view_current': 'Follow the current training model as updates arrive',
    'view_best': 'View the best evaluated model; training continues independently',
}


def command_description(cls, _context, properties):
    if getattr(properties, 'action', '') == 'stop' and _context and getattr(_context, 'scene', None):
        from . import workflow, preview_scene
        source = preview_scene.source_scene(_context.scene) or _context.scene
        phase = workflow.phase(source.lichtfeld_training.status)
        if phase in workflow.STARTUP_PHASES:
            return 'Cancel startup before training begins; existing checkpoints and saved models stay unchanged'
        if phase in workflow.CAPTURE_PHASES:
            return 'Cancel capture and preserve completed images. Training will not start for this cancelled capture'
        return 'Stop the owned training and save a resumable checkpoint; completed best models remain available'
    return ACTIONS.get(getattr(properties, 'action', ''), cls.bl_description)


def apply(classes):
    """Populate missing RNA help before registration; never fall back to a label."""
    for cls in classes:
        if cls.__name__ in OPERATORS:
            cls.bl_description = OPERATORS[cls.__name__]
        if cls.__name__ in {'LF_OT_command', 'LF_OT_color_command'}:
            cls.description = classmethod(command_description)
        for name, prop in getattr(cls, '__annotations__', {}).items():
            keywords = getattr(prop, 'keywords', None)
            if keywords is None:
                continue
            if not keywords.get('description'):
                description = ('Choose the current training model or best evaluated snapshot; training and saved files stay unchanged'
                               if cls.__name__ == 'LF_OT_model_view' and name == 'mode' else
                               TRAINING.get(name) if cls.__name__ == 'LichtFeldSettings' and name in TRAINING else PROPERTIES.get(name))
                if description:
                    keywords['description'] = description
            items = keywords.get('items')
            if isinstance(items, (list, tuple)):
                # Enum option descriptions are independently displayed by Blender.
                keywords['items'] = [tuple(item[:2]) + (item[2] or keywords.get('description', ''),) + tuple(item[3:]) for item in items]
