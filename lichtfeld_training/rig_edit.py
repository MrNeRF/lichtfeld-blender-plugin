# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Staged managed-rig updates. All Blender work yields between object edits."""
import json
import math
import struct
import bpy
from mathutils import Vector
from .visibility import object_steps

AIM = "LichtFeld Aim"
FOLLOW = "LichtFeld Follow"
PATH_LINK = "LichtFeld Path Transform"
ROTATION = "LichtFeld Rotation Variation"


def copy_reference(source, destination):
    """Copy native RNA storage, including missing-ID tombstones, without aliases."""
    if source.mode == 'OBJECTS' and source.get('objects') is not None:
        destination['objects'] = source['objects']
    else:
        destination.objects.clear()
    destination.collection = source.collection if source.mode == 'COLLECTION' else None
    destination.mode = source.mode
    if isinstance(destination.id_data, bpy.types.Scene):
        from . import preflight
        preflight.invalidate(destination.id_data)


def reference_changed(current, saved):
    """Compare the active reference incrementally, outside panel drawing."""
    if current.mode != saved.mode:
        return True
    if current.mode == 'COLLECTION':
        return current.collection != saved.collection
    if current.mode == 'OBJECTS':
        if len(current.objects) != len(saved.objects):
            return True
        for left, right in zip(current.objects, saved.objects):
            if left.object != right.object:
                return True
            yield
    return False


def reference_sources(context, reference):
    """Resolve the explicit rig subject cooperatively; never fall back on selection."""
    from . import preflight
    if reference.mode == 'OBJECTS':
        objects = reference.objects
    elif reference.mode == 'COLLECTION':
        if reference.collection is None:
            raise ValueError('Choose a rig reference collection; the previous collection may have been removed')
        objects = object_steps(reference.collection.all_objects)
    elif reference.mode == 'SCENE':
        objects = object_steps(context.scene.objects)
    else:
        raise ValueError('Choose a rig reference before applying parameters to this older rig')
    # Scene collections allow local and linked IDs with identical names. Build
    # one transaction-local identity set cooperatively, not a name lookup/cache.
    members = set()
    if reference.mode != 'SCENE':
        for obj in object_steps(context.scene.objects):
            members.add(obj)
            yield
    sources = []
    helpers = preflight.capture_helpers(context.scene.lichtfeld_training)
    for entry in objects:
        obj = entry.object if reference.mode == 'OBJECTS' else entry
        in_scene = reference.mode == 'SCENE' or obj in members
        if reference.mode == 'OBJECTS' and (not in_scene or not preflight.is_surface(obj, helpers)):
            raise ValueError('A rig reference object is missing or unavailable: ' + entry.name + '. Choose the reference again')
        if in_scene and preflight.is_surface(obj, helpers):
            sources.append(obj)
        yield
    if not sources:
        raise ValueError('The rig reference has no surface objects in this scene')
    return sources


def render_busy(context):
    """Main datablocks must stay unchanged while Blender's render job owns them."""
    return bpy.app.is_job_running('RENDER') or context.window_manager.is_interface_locked


def reference_bounds_steps(sources, graph):
    """World bounds of the evaluated rig reference used for placement and checks."""
    low, high = Vector((float('inf'),) * 3), Vector((float('-inf'),) * 3)
    for obj in sources:
        evaluated = obj.evaluated_get(graph)
        world = evaluated.matrix_world
        for corner in evaluated.bound_box:
            point = world @ Vector(corner)
            if not all(math.isfinite(value) for value in point):
                raise ValueError('The rig reference has invalid bounds: ' + obj.name)
            for axis in range(3):
                low[axis] = min(low[axis], point[axis])
                high[axis] = max(high[axis], point[axis])
        yield
    return low, high


RIG_OBJECT_KEYS = {
    'target': 'lichtfeld_rig_target',
    'curve': 'lichtfeld_rig_curve',
    'mesh': 'lichtfeld_placement_mesh',
}


def remember_objects(collection, settings):
    for name, key in RIG_OBJECT_KEYS.items():
        value = getattr(settings, f'rig_{name}')
        if value:
            collection[key] = value
        elif key in collection:
            del collection[key]


RADIAL_MODES = {"SPHERE", "HEMISPHERE", "RINGS"}


def radial_panorama(settings):
    return settings.rig_mode in RADIAL_MODES and settings.rig_radial_orientation == "PANORAMA"


def rotation_variation(settings):
    return 0 if radial_panorama(settings) else settings.rig_rotation_variation


def panorama_stations(settings):
    return radial_panorama(settings) or settings.rig_mode == "PATH" and settings.rig_orientation == "PANORAMA"


def uses_aim(settings):
    if settings.rig_mode == 'PATH':
        return settings.rig_orientation == 'TARGET' or (
            settings.rig_orientation == 'PANORAMA' and settings.rig_station_frame == 'TARGET')
    return settings.rig_mode != 'MESH' or settings.rig_mesh_orientation == 'TARGET'


def aim_point(context, settings, center, *, new_target=False):
    if settings.rig_aim_mode == "OBJECT":
        if not settings.rig_target:
            raise ValueError("Choose a target object or aim at Reference center")
        point = settings.rig_target.matrix_world.translation.copy()
    elif settings.rig_aim_mode == "CURSOR":
        point = context.scene.cursor.location.copy()
    elif settings.rig_aim_mode == "POSITION":
        point = Vector(settings.rig_aim_position)
    elif settings.rig_aim_mode == "VIEWPORT" and not new_target:
        if not settings.rig_aim or not settings.camera_collection or settings.rig_aim.name not in settings.camera_collection.objects:
            raise ValueError("Create a rig target first or choose Reference center")
        point = settings.rig_aim.matrix_world.translation.copy()
    else:
        point = center.copy()
    return point + Vector(settings.rig_aim_offset)


def configure_target(target, settings, point, follow=True):
    constraint = target.constraints.get(FOLLOW)
    if constraint:
        target.constraints.remove(constraint)
    target.location = point
    if follow and settings.rig_aim_mode == "OBJECT":
        constraint = target.constraints.new("COPY_LOCATION")
        constraint.name = FOLLOW
        constraint.target = settings.rig_target
        constraint.use_offset = True
        target.location = settings.rig_aim_offset


def track(camera, target):
    constraint = camera.constraints.get(AIM)
    if constraint and constraint.type != "TRACK_TO":
        raise ValueError("A custom camera constraint uses the reserved LichtFeld Aim name")
    if target:
        constraint = constraint or camera.constraints.new("TRACK_TO")
        constraint.name = AIM
        constraint.target = target
        constraint.track_axis = "TRACK_NEGATIVE_Z"
        constraint.up_axis = "UP_Y"
    elif constraint:
        camera.constraints.remove(constraint)


def rotate_after_aim(camera, target, angles=None, *, world=False):
    """Constant angular offset after native tracking, without extra IDs."""
    constraint = camera.constraints.get(ROTATION)
    if constraint and constraint.type != 'TRANSFORM':
        raise ValueError('Keep LichtFeld Rotation Variation reserved for generated rotation offsets')
    if angles is None:
        if constraint:
            camera.constraints.remove(constraint)
        return
    constraint = constraint or camera.constraints.new('TRANSFORM')
    constraint.name = ROTATION
    constraint.target = target
    constraint.map_from = 'LOCATION'
    constraint.map_to = 'ROTATION'
    constraint.mix_mode_rot = 'BEFORE' if world else 'AFTER'
    constraint.owner_space = 'WORLD' if world else 'LOCAL'
    constraint.target_space = 'WORLD'
    constraint.to_euler_order = 'XYZ'
    for axis, value in zip('xyz', angles):
        setattr(constraint, 'from_min_' + axis, -1)
        setattr(constraint, 'from_max_' + axis, 1)
        setattr(constraint, 'to_min_' + axis + '_rot', value)
        setattr(constraint, 'to_max_' + axis + '_rot', value)
    aim_index = list(camera.constraints).index(camera.constraints.get(AIM))
    index = list(camera.constraints).index(constraint)
    camera.constraints.move(index, aim_index + 1 - (index < aim_index))


def path_link_state(camera):
    constraint = camera.constraints.get(PATH_LINK)
    return (constraint.target, constraint.inverse_matrix.copy(), list(camera.constraints).index(constraint)) if constraint else None


def link_path(camera, curve, inverse=None, index=0):
    """Native transform relationship, evaluated before independent target aiming."""
    constraint = camera.constraints.get(PATH_LINK)
    if constraint and constraint.type != 'CHILD_OF':
        raise ValueError('Keep LichtFeld Path Transform reserved for the guide link')
    if inverse is None:
        if constraint:
            camera.constraints.remove(constraint)
        return
    constraint = constraint or camera.constraints.new('CHILD_OF')
    constraint.name = PATH_LINK
    constraint.target = curve
    constraint.inverse_matrix = inverse
    constraint.set_inverse_pending = False
    camera.constraints.move(list(camera.constraints).index(constraint), index)


def validate_path_link(curve, collection, graph):
    if curve is None:
        raise ValueError('Choose a path curve before linking cameras')
    try:
        inverse = curve.evaluated_get(graph).matrix_world.inverted()
    except ValueError:
        raise ValueError('The path transform is singular. Use a nonzero guide scale before linking cameras') from None
    if not all(math.isfinite(v) for row in inverse for v in row):
        raise ValueError('The path transform must be finite')
    parent = curve.parent
    while parent:
        if collection and collection.objects.get(parent.name) == parent and parent.get('lichtfeld_generated'):
            raise ValueError('The path cannot be parented to one of its generated cameras')
        parent = parent.parent
    return inverse


def rollback(owner):
    for obj, data, matrix, constraint_target, constraint_index in reversed(getattr(owner, "rig_backup", [])):
        try:
            if obj.name in bpy.data.objects:
                obj.data = data
                obj.matrix_world = matrix[0]
                obj.matrix_basis = matrix[1]
                track(obj, constraint_target if constraint_index >= 0 else None)
                constraint = obj.constraints.get(AIM)
                if constraint:
                    obj.constraints.move(list(obj.constraints).index(constraint), constraint_index)
        except ReferenceError:
            pass  # A native deletion by the user must not strand other backups.
        yield
    owner.rig_backup = []


def managed_members(collection):
    """One index/identity contract for manual and live generated-rig edits."""
    old = []
    targets = []
    for obj in object_steps(collection.objects):
        if obj.type == 'CAMERA' and obj.get('lichtfeld_generated'):
            old.append(obj)
        if obj.get('lichtfeld_aim'):
            targets.append(obj)
        yield
    if len(targets) > 1:
        raise ValueError('Multiple aim helpers in this rig. Remove duplicated helpers from the rig collection before updating')
    # Validate before mutating any existing camera.
    indices = {}
    for obj in old:
        index = obj.get('lichtfeld_camera_index')
        if type(index) is not int or index < 0:
            raise ValueError(f'Invalid generated camera index: {obj.name}. Remove this camera from the rig collection before updating')
        if index in indices:
            raise ValueError(f'Duplicated generated cameras: {indices[index]} and {obj.name}. Remove the duplicate from the rig collection before updating')
        indices[index] = obj.name
        constraint = obj.constraints.get(AIM)
        path = obj.constraints.get(PATH_LINK)
        rotation = obj.constraints.get(ROTATION)
        if rotation and rotation.type != 'TRANSFORM':
            raise ValueError('Keep LichtFeld Rotation Variation reserved for generated rotation offsets')
        if obj.library or (constraint and constraint.type != "TRACK_TO") or (path and path.type != 'CHILD_OF'):
            raise ValueError("Make generated cameras local and keep LichtFeld Aim and Path Transform names reserved")
        yield
    old.sort(key=lambda obj: obj['lichtfeld_camera_index'])
    return old, targets[0] if targets else None


def remove_camera_objects(objects, unused):
    valid = []
    for obj in objects:
        try:
            data = obj.data
        except ReferenceError:
            continue  # Blender may have deleted the object between modal steps.
        if data is not None:
            unused[data.as_pointer()] = data
        valid.append(obj)
    if valid:
        bpy.data.batch_remove(ids=valid)


def station_exclusions(settings, previous, old, new):
    from .preflight import CAMERA_EXCLUDED
    from .rig_geometry import pose_key

    if not any(camera.get(CAMERA_EXCLUDED) for camera in old):
        return None
    current_points = settings.rig_mode == 'PATH' and settings.rig_path_sampling == 'POINTS'
    if not current_points:
        try:
            applied = decode_recipe(previous.get('lichtfeld_rig_recipe'))
        except ValueError:
            return None
        if applied.get('rig_mode') != 'PATH' or applied.get('rig_path_sampling') != 'POINTS':
            return None
    depsgraph = bpy.context.evaluated_depsgraph_get()
    def evaluated_pose(camera):
        # Blender can decompose and reconstruct the same pose during Apply.
        return tuple(round(value, 5) for value in pose_key(camera.evaluated_get(depsgraph).matrix_world))

    old_poses = {}
    for camera in old:
        key = evaluated_pose(camera)
        old_poses[key] = key not in old_poses and bool(camera.get(CAMERA_EXCLUDED))
        yield
    new_poses, duplicates = {}, set()
    for index, camera in enumerate(new):
        key = evaluated_pose(camera)
        if key in new_poses:
            duplicates.add(key)
        new_poses[key] = index
        yield
    return {index for key, index in new_poses.items() if key not in duplicates and old_poses.get(key)}


def commit(owner, settings, previous, staging, target, recipe):
    """Preserve generated camera/target IDs, custom objects and collection links."""
    uses_aim = target.get('lichtfeld_aim_used', True)
    if uses_aim and settings.rig_aim_mode == 'OBJECT' and not settings.rig_target:
        raise ValueError('The rig aim object was removed while preparing the update')
    if not previous or not previous.get("lichtfeld_generated"):
        settings.camera_collection = staging
        settings.rig_aim = target
        staging["lichtfeld_generated"] = True
        staging["lichtfeld_rig_recipe"] = recipe
        remember_objects(staging, settings)
        settings.rig_parameter_source = staging
        owner.committed = True
        owner.collection = None
        return
    if previous.library:
        raise ValueError("Make the rig collection local before updating it")
    old, old_target = yield from managed_members(previous)
    new = sorted((obj for obj in staging.objects if obj.type == "CAMERA"),
                 key=lambda obj: obj["lichtfeld_camera_index"])
    if uses_aim and settings.rig_target and (settings.rig_target in old or settings.rig_target == old_target):
        raise ValueError("The aim object must be outside its own capture rig")
    exclusions = yield from station_exclusions(settings, previous, old, new)
    from .preflight import CAMERA_EXCLUDED
    retired = {obj.get(LIVE) for obj in old if isinstance(obj.get(LIVE), bpy.types.Object)}
    owner.rig_backup = []
    for index, (original, prepared) in enumerate(zip(old, new)):
        constraint = original.constraints.get(AIM)
        owner.rig_backup.append((original, original.data, (original.matrix_world.copy(), original.matrix_basis.copy()),
                                 constraint.target if constraint else None, list(original.constraints).index(constraint) if constraint else -1))
        original.data = prepared.data
        original.matrix_world = prepared.matrix_basis
        track(original, target if prepared.constraints.get(AIM) else None)
        yield
    # Cancellation before this boundary rolls existing objects back in cleanup().
    owner.committed = True
    if old_target and target:
        configure_target(old_target, settings, target.location.copy() if not target.constraints.get(FOLLOW) else
                         settings.rig_target.matrix_world.translation + Vector(settings.rig_aim_offset), follow=uses_aim)
        final_target = old_target
        final_target['lichtfeld_aim_used'] = uses_aim
    else:
        final_target = target
        previous.objects.link(target)
        staging.objects.unlink(target)
    settings.rig_aim = final_target
    # Bound each removal so the modal operator can pause for native renders.
    batch_size = 32
    consumed = []
    unused = {data.as_pointer(): data for obj, data, matrix, constraint_target, constraint_index in owner.rig_backup}
    for index, prepared in enumerate(new):
        if index < len(old):
            original = old[index]
            original["lichtfeld_camera_index"] = index
            if exclusions is not None:
                if index in exclusions:
                    original[CAMERA_EXCLUDED] = True
                elif CAMERA_EXCLUDED in original:
                    del original[CAMERA_EXCLUDED]
            if LIVE in original:
                _bake_live_camera(original, prepared.matrix_basis,
                                  original.data.clip_start, original.data.clip_end)
            # Keep the applied link intact until the transaction commits, so
            # cancellation needs no second constraint snapshot/history.
            path = path_link_state(prepared)
            link_path(original, *(path if path else (None, None)))
            track(original, final_target if prepared.constraints.get(AIM) else None)
            rotation = prepared.constraints.get(ROTATION)
            rotate_after_aim(original, final_target,
                tuple(getattr(rotation, 'to_min_' + axis + '_rot') for axis in 'xyz') if rotation else None,
                world=bool(rotation and rotation.owner_space == 'WORLD'))
            consumed.append(prepared)
            if len(consumed) == batch_size:
                remove_camera_objects(consumed, unused)
                consumed.clear()
        else:
            track(prepared, final_target if prepared.constraints.get(AIM) else None)
            rotation = prepared.constraints.get(ROTATION)
            if rotation:
                rotation.target = final_target
            if exclusions is not None and index in exclusions:
                prepared[CAMERA_EXCLUDED] = True
            previous.objects.link(prepared)
            staging.objects.unlink(prepared)
        yield
    if consumed:
        remove_camera_objects(consumed, unused)
        yield
    surplus = old[len(new):]
    for start in range(0, len(surplus), batch_size):
        remove_camera_objects(surplus[start:start + batch_size], unused)
        yield
    data_blocks = list(unused.values())
    for start in range(0, len(data_blocks), batch_size):
        orphaned = []
        for data in data_blocks[start:start + batch_size]:
            try:
                if data.users == 0:
                    orphaned.append(data)
            except ReferenceError:
                continue
        if orphaned:
            bpy.data.batch_remove(ids=orphaned)
        yield
    owner.rig_backup = []
    for root in retired:
        _release_controller(root, previous)
        yield
    if target != final_target:
        bpy.data.objects.remove(target, do_unlink=True)
    copy_reference(staging.lichtfeld_rig_reference, previous.lichtfeld_rig_reference)
    for key in ('lichtfeld_generated_radius', 'lichtfeld_fit_limited'):
        if key in staging:
            previous[key] = staging[key]
        elif key in previous:
            del previous[key]
    bpy.data.collections.remove(staging)
    owner.collection = None
    previous["lichtfeld_rig_recipe"] = recipe
    remember_objects(previous, settings)
    settings.camera_collection = previous
    settings.rig_parameter_source = previous


def decode_recipe(encoded):
    if not isinstance(encoded, str) or len(encoded) > 65536:
        raise ValueError("Rig metadata is too large or invalid")
    values = json.loads(encoded)
    if not isinstance(values, dict):
        raise ValueError("Rig metadata must be an object")
    properties = bpy.types.Scene.bl_rna.properties['lichtfeld_training'].fixed_type.properties
    for name in ('views', 'rig_rings', 'rig_min', 'rig_max', 'rig_distance',
                 'rig_lens', 'rig_radius', 'rig_mesh_offset', 'rig_phase', 'rig_path_spacing',
                 'rig_position_variation', 'rig_rotation_variation', 'rig_variation_seed',
                 'rig_station_views', 'rig_station_sweep', 'rig_station_min', 'rig_station_max', 'rig_station_rows'):
        if name not in values:continue
        value=values[name]
        if type(value) not in (int,float) or not math.isfinite(value):raise ValueError("Invalid rig number: "+name)
        if name in {"views","rig_rings","rig_variation_seed","rig_station_views","rig_station_rows"} and type(value) is not int:raise ValueError("Invalid rig count or seed")
        # RNA owns both the bounds and their float32 representation. Metadata
        # must accept what the native controls write, without a second table.
        prop = properties[name]
        if not prop.hard_min <= value <= prop.hard_max:
            raise ValueError("Rig parameter outside its range: "+name)
    for name, valid in (("rig_mode",{"SPHERE","HEMISPHERE","RINGS","PATH","MESH"}),
                        ("rig_orientation",{"TARGET","TANGENT","PANORAMA"}),
                        ("rig_radial_orientation",{"TARGET","PANORAMA"}),
                        ('rig_path_sampling', {'COUNT', 'SPACING', 'POINTS'}),
                        ('rig_station_frame', {'GUIDE', 'PATH', 'TARGET'}),
                        ('rig_station_shape', {'BAND', 'GRID', 'ELEVATION', 'HEMISPHERE', 'SPHERE'}),
                        ("rig_mesh_distribution",{"SURFACE","FACES"}),
                        ("rig_mesh_orientation",{"INWARD","OUTWARD","TARGET"}),
                        ("aim_mode",{"SOURCE","OBJECT","CURSOR","POSITION","VIEWPORT"})):
        if name in values and values[name] not in valid:raise ValueError("Unknown rig mode: "+name)
    for name in ("aim_position","aim_offset"):
        if name in values and (not isinstance(values[name],list) or len(values[name])!=3 or
                any(type(v) not in (int,float) or not math.isfinite(v) for v in values[name])):
            raise ValueError("Invalid aim coordinates")
    for name in ("target","curve","mesh"):
        if name in values and (not isinstance(values[name],str) or "\0" in values[name]):raise ValueError("Invalid rig object name")
    if "rig_auto_fit" in values and type(values["rig_auto_fit"]) is not bool:raise ValueError("Invalid framing mode")
    if 'rig_path_reverse' in values and type(values['rig_path_reverse']) is not bool:
        raise ValueError('Invalid path direction')
    if 'rig_path_link' in values and type(values['rig_path_link']) is not bool:
        raise ValueError('Invalid path link mode')
    return values

def restore_legacy_distance(scene):
    settings = scene.lichtfeld_training
    if not scene.is_editable or settings.is_property_set('rig_distance'):
        return
    rig = settings.rig_parameter_source
    if rig is None or rig != settings.camera_collection or not rig.get('lichtfeld_generated'):
        return
    try:
        value = decode_recipe(rig.get('lichtfeld_rig_recipe')).get('rig_distance')
    except (TypeError, ValueError):
        return
    # The old RNA default was absent from the scene but stored in its applied recipe.
    if value is not None and math.isclose(value, 2.8, abs_tol=1e-6):
        settings.rig_distance = value


def recipe_matches(collection, settings):
    from . import rig_recipe

    stored = collection.get('lichtfeld_rig_recipe')
    current = rig_recipe(settings)
    for name, key in RIG_OBJECT_KEYS.items():
        if key in collection and collection.get(key) != getattr(settings, f'rig_{name}'):
            return False
    if stored == current:
        return True
    try:
        applied = decode_recipe(stored)
    except (TypeError, ValueError):
        return False
    for name, key in RIG_OBJECT_KEYS.items():
        if key in collection and name in applied:
            obj = collection.get(key)
            applied[name] = obj.name_full if obj is not None else ''
    return json.dumps(applied, sort_keys=True) == current


# Native controller properties are the live state: Blender owns gestures, Undo,
# dependency-graph evaluation and persistence. There is no timer or second history.
LIVE = 'lichtfeld_live_rig'


def live_root(settings):
    collection = settings.camera_collection
    root = collection.get(LIVE) if collection else None
    return root if isinstance(root, bpy.types.Object) and root.get(LIVE) else None


def _live_writable(settings):
    root = live_root(settings)
    if not root or root.get('finishing') or settings.running or settings.operation_busy or settings.importing or settings.job_directory or settings.attached_session or render_busy(bpy.context):
        return None
    return root


def live_radius_get(settings):
    root = live_root(settings)
    return root['radius'] if root else .01


def live_radius_set(settings, value):
    root = _live_writable(settings)
    if root and math.isfinite(value):
        root['radius'] = max(.01, min(1000000, value))
        root.update_tag()


def live_phase_get(settings):
    root = live_root(settings)
    return root['phase'] if root else 0.0


def live_phase_set(settings, value):
    root = _live_writable(settings)
    if root and math.isfinite(value):
        root['phase'] = value
        root.update_tag()


def live_aim_get(settings):
    target = settings.rig_aim
    return tuple(target.location) if live_root(settings) and target else (0, 0, 0)


def live_aim_set(settings, value):
    if _live_writable(settings) and settings.rig_aim and all(math.isfinite(v) for v in value):
        settings.rig_aim.location = value


def _radius_driver(data, path, root, expression, index=None):
    curve = data.driver_add(path) if index is None else data.driver_add(path, index)
    driver = curve.driver
    driver.type = 'SCRIPTED'
    variable = driver.variables.new()
    variable.name = 'radius'
    variable.targets[0].id = root
    variable.targets[0].data_path = '["radius"]'
    driver.expression = expression


def _remove_live_drivers(camera):
    camera.pop(LIVE, None)
    for axis in range(3):
        camera.driver_remove('scale', axis)
    for path in ('clip_start', 'clip_end'):
        camera.data.driver_remove(path)
    for data in (camera, camera.data):
        animation = data.animation_data
        if animation and not animation.action and not animation.drivers and not animation.nla_tracks:
            data.animation_data_clear()


def _release_controller(root, collection):
    # Scene bases are weak membership references reported by user_map. Real
    # Scene ID properties increase users; other weak users also retain the ID.
    users = bpy.data.user_map(subset={root}).get(root, set()) - {root, collection, *root.users_scene}
    if not users and root.users <= 1:
        bpy.data.objects.remove(root, do_unlink=True)


def _bake_live_camera(camera, matrix, near, far):
    camera.parent = None
    _remove_live_drivers(camera)
    camera.matrix_world = matrix
    camera.data.clip_start, camera.data.clip_end = near, far


def _shared_outside_preview(obj, source):
    from . import capture_preview
    return any(scene != source and capture_preview.source_scene(scene) != source for scene in obj.users_scene)


def _live_members(settings, graph):
    """Qualify the actual applied rig, not today's possibly moved reference."""
    from . import rig_geometry, capture_settings
    collection = settings.camera_collection
    if collection.library or collection.override_library:
        raise ValueError('Make the rig local before editing it live')
    recipe = decode_recipe(collection.get('lichtfeld_rig_recipe'))
    if recipe.get('rig_radial_orientation') == 'PANORAMA':
        raise ValueError('Use Apply to edit panorama stations')
    radius = collection.get('lichtfeld_generated_radius')
    if recipe.get('rig_mode') not in RADIAL_MODES or type(radius) not in (int, float) or not math.isfinite(radius) or not .01 <= radius <= 1000000:
        raise ValueError('Apply a sphere, hemisphere or rings rig before editing it live')
    cameras, target = yield from managed_members(collection)
    if target is None or target != settings.rig_aim:
        raise ValueError('Load a rig with one generated aim target before editing it live')
    follow = target.constraints.get(FOLLOW)
    if (target.library or target.override_library or target.parent or target.animation_data or
            _shared_outside_preview(target, settings.id_data) or any(c != follow for c in target.constraints) or
            follow and (follow.type != 'COPY_LOCATION' or not follow.target)):
        raise ValueError('Live editing needs a local, unanimated aim helper with its original following constraint')
    for camera in cameras:
        aim = camera.constraints.get(AIM)
        rotation = camera.constraints.get(ROTATION)
        expected = [aim, rotation] if recipe.get('rig_rotation_variation', 0) else [aim]
        if (list(camera.constraints) != expected or
                rotation and (rotation.type != 'TRANSFORM' or rotation.target != target)):
            raise ValueError('Apply the rig to restore its native aiming and rotation constraints before live editing')
        if (camera.override_library or camera.parent or camera.animation_data or
                camera.data.library or camera.data.override_library or camera.data.animation_data or camera.data.users != 1 or
                _shared_outside_preview(camera, settings.id_data) or not aim or aim.type != 'TRACK_TO' or aim.target != target):
            raise ValueError('Live editing needs the original local generated cameras; keep custom cameras as additional cameras')
        yield
    count = recipe.get('views', 0)
    if len(cameras) != count or any(o['lichtfeld_camera_index'] != i for i, o in enumerate(cameras)):
        raise ValueError('Apply the rig before live editing: generated camera count or indices changed')
    directions = rig_geometry.directions(count, recipe['rig_mode'], recipe.get('rig_min', 0),
        recipe.get('rig_max', 85), recipe.get('rig_rings', 1), recipe.get('rig_phase', 0))
    center = None
    for index, (camera, direction) in enumerate(zip(cameras, directions)):
        capture_settings.camera_pose(camera, graph)
        position = camera.matrix_world.translation
        offset = Vector(direction) * radius + Vector(rig_geometry.seeded_offset(index,
            recipe.get('rig_variation_seed', 0), radius*recipe.get('rig_position_variation', 0)/100, recipe.get('rig_phase', 0)))
        center = position - offset if center is None else center
        if ((position - center - offset).length > max(radius * 1e-5, 1e-5) or
                any(abs(v - 1) > 1e-5 for v in camera.matrix_world.to_scale())):
            raise ValueError('Generated cameras were transformed manually. Apply the rig before editing it live')
        yield
    return cameras, target, recipe, radius, center


def edit_live(owner, context):
    """Enter without moving cameras; finish by baking the same native poses."""
    from . import rig_recipe, capture_settings
    settings = context.scene.lichtfeld_training
    collection = settings.camera_collection
    root = live_root(settings)
    if not root:
        if not recipe_matches(collection, settings):
            raise ValueError('Apply or revert pending rig parameters before editing live')
        context.view_layer.update()
        cameras, target, recipe, radius, center = yield from _live_members(settings, context.evaluated_depsgraph_get())
        root = bpy.data.objects.new('LichtFeld Live Rig', None)
        owner.live_controller = root
        root[LIVE] = True
        root['radius'] = radius
        root['phase'] = float(recipe.get('rig_phase', 0))
        root['aim_at_entry'] = tuple(target.location)
        root.location = center
        root.hide_select = True
        root.lock_location = (True, True, True)
        root.lock_rotation = (True, True, True)
        root.lock_scale = (True, True, True)
        collection.objects.link(root)
        for axis in range(3):
            _radius_driver(root, 'scale', root, f'radius/{radius!r}', axis)
        curve = root.driver_add('rotation_euler', 2)
        variable = curve.driver.variables.new()
        variable.name = 'phase'
        variable.targets[0].id = root
        variable.targets[0].data_path = '["phase"]'
        curve.driver.expression = f'phase-({root["phase"]!r})'
        context.view_layer.update()
        inverse = root.matrix_world.inverted()
        for camera in cameras:
            matrix = camera.matrix_world.copy()
            owner.live_backup.append((camera, matrix, camera.data.clip_start, camera.data.clip_end))
            camera[LIVE] = root
            camera.parent = root
            camera.matrix_parent_inverse = inverse
            camera.matrix_world = matrix
            for axis in range(3):
                _radius_driver(camera, 'scale', root, f'{radius!r}/max(radius,0.01)', axis)
            _radius_driver(camera.data, 'clip_start', root, f'max(0.000001,radius*{camera.data.clip_start / radius!r})')
            _radius_driver(camera.data, 'clip_end', root, f'max(0.001,radius*{camera.data.clip_end / radius!r})')
            yield
        collection[LIVE] = root
        owner.committed = True
    else:
        owner.live_controller = root
        applied = decode_recipe(collection.get('lichtfeld_rig_recipe'))
        cameras = []
        for obj in object_steps(collection.objects):
            if obj.parent == root:
                if obj.type != 'CAMERA' or not obj.get('lichtfeld_generated') or obj.library or obj.data.users != 1:
                    raise ValueError('Remove custom children or shared camera data from the live controller before finishing')
                cameras.append(obj)
            yield
        context.view_layer.update()
        for camera in cameras:
            evaluated = camera.evaluated_get(context.evaluated_depsgraph_get())
            owner.live_backup.append((camera, evaluated.matrix_world.copy(), evaluated.data.clip_start, evaluated.data.clip_end))
            yield
        # Snapshotting is cancellable. Once native relationships are removed,
        # finish cleanup cooperatively, just like the managed-rig commit owner.
        root['finishing'] = True
        owner.committed = True
        for camera, matrix, near, far in owner.live_backup:
            _bake_live_camera(camera, matrix, near, far)
            yield
        # RNA Float controls round to float32, including Escape restoration.
        distance_changed = struct.pack('=f', root['radius']) != struct.pack('=f', collection['lichtfeld_generated_radius'])
        settings.rig_auto_fit = False if distance_changed else applied.get('rig_auto_fit', True)
        settings.rig_radius = root['radius'] if distance_changed else applied.get('rig_radius', 3)
        settings.rig_phase = root['phase']
        target = settings.rig_aim
        if target:
            if target.constraints.get(FOLLOW):
                settings.rig_aim_mode = 'OBJECT'
                settings.rig_aim_offset = target.location
            elif tuple(target.location) != tuple(root['aim_at_entry']) or applied.get('aim_mode') == 'VIEWPORT':
                settings.rig_aim_mode = 'VIEWPORT'
                settings.rig_aim_offset = (0, 0, 0)
            else:
                settings.rig_aim_mode = applied.get('aim_mode', 'SOURCE')
                settings.rig_aim_offset = applied.get('aim_offset', (0, 0, 0))
        collection['lichtfeld_generated_radius'] = root['radius']
        if distance_changed:
            collection['lichtfeld_fit_limited'] = False
        applied.update(rig_auto_fit=settings.rig_auto_fit, rig_radius=settings.rig_radius, rig_phase=settings.rig_phase,
                       aim_mode=settings.rig_aim_mode, aim_offset=list(settings.rig_aim_offset))
        if not settings.rig_auto_fit:
            applied['capture_framing'] = capture_settings.framing_signature(settings.id_data, settings)
        collection['lichtfeld_rig_recipe'] = rig_recipe(settings, applied)
        del collection[LIVE]
        _release_controller(root, collection)
    owner.live_backup = []


def rollback_live(owner):
    root = getattr(owner, 'live_controller', None)
    if not root or owner.live_finishing:
        return
    for camera, matrix, near, far in reversed(owner.live_backup):
        try:
            _bake_live_camera(camera, matrix, near, far)
        except ReferenceError:
            pass
        yield
    try:
        bpy.data.objects.remove(root, do_unlink=True)
    except ReferenceError:
        pass
    owner.live_backup = []

class LF_GGT_live_rig(bpy.types.GizmoGroup):
    bl_idname = 'LICHTFELD_GGT_live_rig'
    bl_label = 'Live camera rig'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT'}

    @classmethod
    def poll(cls, context):
        s = context.scene.lichtfeld_training
        return context.mode == 'OBJECT' and bool(_live_writable(s))

    def setup(self, context):
        self.distance = self.gizmos.new('GIZMO_GT_arrow_3d')
        self.orbit = self.gizmos.new('GIZMO_GT_dial_3d')
        self.aim = self.gizmos.new('GIZMO_GT_move_3d')
        theme = context.preferences.themes[0].user_interface
        for gizmo, color in ((self.distance, theme.gizmo_primary),
                             (self.orbit, theme.gizmo_secondary), (self.aim, theme.gizmo_a)):
            gizmo.color = color[:3]
            gizmo.color_highlight = theme.gizmo_hi[:3]
            gizmo.alpha = .6
            gizmo.alpha_highlight = 1
            gizmo.use_draw_value = True
            gizmo.use_undo = True
        self.aim.scale_basis = .2
        self.refresh(context)

    def refresh(self, context):
        from mathutils import Matrix
        settings = context.scene.lichtfeld_training
        root = live_root(settings)
        if not root:
            return
        # Native RNA targets retain Blender's own gesture cancellation and Undo.
        # These non-animatable adapters do not insert keys with Auto Key enabled.
        self.distance.target_set_prop('offset', settings, 'live_radius')
        self.orbit.target_set_prop('offset', settings, 'live_phase')
        self.aim.target_set_prop('offset', settings, 'live_aim')
        self.distance.matrix_basis = Vector((1, 0, 0)).to_track_quat('Z', 'Y').to_matrix().to_4x4()
        self.distance.matrix_basis.translation = root.location
        self.orbit.matrix_basis = Matrix.Translation(root.location)
        target = settings.rig_aim
        follow = target.constraints.get(FOLLOW) if target else None
        self.aim.matrix_basis = Matrix.Translation(follow.target.matrix_world.translation) if follow and follow.target else Matrix.Identity(4)
