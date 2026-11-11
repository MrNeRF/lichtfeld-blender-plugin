# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Staged managed-rig updates. All Blender work yields between object edits."""
import json
import math
import bpy
from mathutils import Vector

AIM = "LichtFeld Aim"
FOLLOW = "LichtFeld Follow"


def render_busy(context):
    """Main datablocks must stay unchanged while Blender's render job owns them."""
    return bpy.app.is_job_running('RENDER') or context.window_manager.is_interface_locked


def remember_objects(collection, settings):
    for key, value in (('lichtfeld_placement_mesh', settings.rig_mesh),
                       ('lichtfeld_rig_curve', settings.rig_curve), ('lichtfeld_rig_target', settings.rig_target)):
        if value:
            collection[key] = value
        elif key in collection:
            del collection[key]


def aim_point(context, settings, center):
    if settings.rig_aim_mode == "OBJECT":
        if not settings.rig_target:
            raise ValueError("Choose a target object or aim at Subject center")
        point = settings.rig_target.matrix_world.translation.copy()
    elif settings.rig_aim_mode == "CURSOR":
        point = context.scene.cursor.location.copy()
    elif settings.rig_aim_mode == "POSITION":
        point = Vector(settings.rig_aim_position)
    elif settings.rig_aim_mode == "VIEWPORT":
        if not settings.rig_aim or not settings.camera_collection or settings.rig_aim.name not in settings.camera_collection.objects:
            raise ValueError("Create a rig target first or choose Subject center")
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


def rollback(owner):
    for obj, data, matrix, constraint_target, had_constraint in reversed(getattr(owner, "rig_backup", [])):
        if obj.name in bpy.data.objects:
            obj.data = data
            obj.matrix_world = matrix
            track(obj, constraint_target if had_constraint else None)
        yield
    owner.rig_backup = []


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
    old = [obj for obj in previous.objects if obj.type == "CAMERA" and obj.get("lichtfeld_generated")]
    new = sorted((obj for obj in staging.objects if obj.type == "CAMERA"),
                 key=lambda obj: obj["lichtfeld_camera_index"])
    targets = [obj for obj in previous.objects if obj.get("lichtfeld_aim")]
    if len(targets) > 1:
        raise ValueError('Multiple aim helpers in this rig. Remove duplicated helpers from the rig collection before updating')
    old_target = targets[0] if targets else None
    if uses_aim and settings.rig_target and (settings.rig_target in old or settings.rig_target == old_target):
        raise ValueError("The aim object must be outside its own capture rig")
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
        if obj.library or (constraint and constraint.type != "TRACK_TO"):
            raise ValueError("Make generated cameras local and keep LichtFeld Aim reserved")
        yield
    old.sort(key=lambda obj: obj['lichtfeld_camera_index'])
    owner.rig_backup = []
    for index, (original, prepared) in enumerate(zip(old, new)):
        constraint = original.constraints.get(AIM)
        owner.rig_backup.append((original, original.data, original.matrix_world.copy(),
                                 constraint.target if constraint else None, bool(constraint)))
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
    for index, prepared in enumerate(new):
        if index < len(old):
            original = old[index]
            original["lichtfeld_camera_index"] = index
            track(original, final_target if prepared.constraints.get(AIM) else None)
            data = prepared.data
            bpy.data.objects.remove(prepared, do_unlink=True)
            if data.users == 0:
                bpy.data.cameras.remove(data)
        else:
            track(prepared, final_target if prepared.constraints.get(AIM) else None)
            previous.objects.link(prepared)
            staging.objects.unlink(prepared)
        yield
    unused = {data.as_pointer(): data for obj, data, matrix, constraint_target, had_constraint in owner.rig_backup}
    for obj in old[len(new):]:
        unused[obj.data.as_pointer()] = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        yield
    for data in unused.values():
        if data.users == 0:
            bpy.data.cameras.remove(data)
        yield
    owner.rig_backup = []
    if target != final_target:
        bpy.data.objects.remove(target, do_unlink=True)
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
    ranges = {"views": (4,1000), "rig_rings": (1,250), "rig_min":(-90,90), "rig_max":(-90,90),
              "rig_distance":(1.05,100), "rig_lens":(1,500), "rig_radius":(.01,1000000), "rig_mesh_offset":(-100000,100000)}
    for name in (*ranges, "rig_phase"):
        if name not in values:continue
        value=values[name]
        if type(value) not in (int,float) or not math.isfinite(value):raise ValueError("Invalid rig number: "+name)
        if name in {"views","rig_rings"} and type(value) is not int:raise ValueError("Invalid rig count")
        if name in ranges and not ranges[name][0] <= value <= ranges[name][1]:raise ValueError("Rig parameter outside its range: "+name)
    for name, valid in (("rig_mode",{"SPHERE","HEMISPHERE","RINGS","PATH","MESH"}),
                        ("rig_orientation",{"TARGET","TANGENT"}),
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
    return values
