# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Classification of rendered surfaces in an oriented capture volume."""

import numpy as np


def local_transform(shape, world_to_unit):
    if shape not in {"BOX", "ELLIPSOID"}:
        raise ValueError("Unknown capture region shape")
    transform = np.asarray(world_to_unit, dtype=np.float64)
    if (transform.shape != (4, 4) or not np.isfinite(transform).all()
            or not np.array_equal(transform[3], [0, 0, 0, 1])
            or np.linalg.slogdet(transform[:3, :3])[0] == 0):
        raise ValueError("Capture region needs finite, nonzero dimensions")
    return transform.astype(np.float32)


def region_spec(context, obj):
    """Freeze the evaluated volume using the Empty's displayed bounds."""
    from mathutils import Matrix

    if context.scene.objects.get(obj.name) != obj:
        raise ValueError("Choose a Capture region Empty from this scene")
    evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
    shape = {"CUBE": "BOX", "SPHERE": "ELLIPSOID"}.get(evaluated.empty_display_type)
    if evaluated.type != 'EMPTY' or shape is None:
        raise ValueError("Capture region requires a Cube or Sphere Empty")
    world_from_unit = evaluated.matrix_world @ Matrix.Scale(evaluated.empty_display_size, 4)
    try:
        world_to_unit = [list(row) for row in world_from_unit.inverted()]
    except ValueError as exc:
        raise ValueError("Capture region needs finite, nonzero dimensions") from exc
    local_transform(shape, world_to_unit)
    return {"shape": shape, "world_to_unit": world_to_unit}


def render_layers(layer):
    if layer.exclude or layer.collection.hide_render:
        return
    yield layer
    for child in layer.children:
        yield from render_layers(child)


def uses_viewport_switch(tree, seen):
    if tree is None or tree in seen:
        return False
    seen.add(tree)
    return any(node.bl_idname == "GeometryNodeIsViewport"
               or uses_viewport_switch(getattr(node, "node_tree", None), seen)
               for node in tree.nodes)


def animated_render_visibility(obj):
    from bpy_extras.anim_utils import action_get_channelbag_for_slot

    animation = obj.animation_data
    if animation is None:
        return False
    if any(curve.data_path == "hide_render" for curve in animation.drivers):
        return True
    actions = [(animation.action, animation.action_slot)]
    for track in animation.nla_tracks:
        if not track.mute:
            actions.extend((strip.action, strip.action_slot) for strip in track.strips if strip.type == "CLIP")
    for action, slot in actions:
        if action is None or slot is None:
            continue
        bag = action_get_channelbag_for_slot(action, slot)
        if bag is not None and any(curve.data_path == "hide_render" for curve in bag.fcurves):
            return True
    return False


def clip_capture_scene(context, shape, transform, sources):
    """Clip evaluated surfaces in the disposable capture scene."""
    import bmesh
    import bpy
    from idprop.types import IDPropertyArray
    from mathutils import Matrix, Vector

    planes = [(Vector(axis), 1.0) for axis in
              ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]
    if shape == "ELLIPSOID":
        # An inscribed convex polyhedron keeps every retained point inside the sphere.
        hull = bmesh.new()
        try:
            bmesh.ops.create_icosphere(hull, subdivisions=3, radius=1.0)
            hull.normal_update()
            planes.extend((face.normal.copy(), face.normal.dot(face.verts[0].co)) for face in hull.faces)
        finally:
            hull.free()

    layers = tuple(render_layers(context.view_layer.layer_collection))
    collections = tuple(dict.fromkeys(layer.collection for layer in layers))
    objects = tuple(dict.fromkeys(obj for collection in collections for obj in collection.objects
                                  if not obj.hide_render))
    viewport_settings = []
    hidden_objects = []

    def for_evaluation(owner, field, value):
        original = getattr(owner, field)
        if value != original:
            if not owner.id_data.is_editable or owner.is_property_readonly(field):
                raise ValueError(f'Capture region cannot adjust {field} on linked data {owner.id_data.name}.')
            setattr(owner, field, value)
            viewport_settings.append((owner, field, original))

    # Check geometry with render-dependent bounds before using a viewport bbox.
    for obj in objects:
        for modifier in obj.modifiers:
            if not modifier.show_render:
                continue
            if modifier.type == "NODES" and uses_viewport_switch(modifier.node_group, set()):
                raise ValueError(f"Capture region cannot clip {obj.name} using Geometry Nodes Is Viewport.")
            if modifier.type == "PARTICLE_SYSTEM":
                raise ValueError(f"Capture region cannot clip particle systems on {obj.name}. Realize them before capture.")

    for layer in layers:
        for_evaluation(layer, 'hide_viewport', False)
    for collection in collections:
        for_evaluation(collection, "hide_viewport", False)
    for obj in objects:
        for_evaluation(obj, "hide_viewport", False)
        if obj.hide_get(view_layer=context.view_layer):
            obj.hide_set(False, view_layer=context.view_layer)
            hidden_objects.append(obj)
        if obj.type in {"CURVE", "SURFACE", "FONT"}:
            for axis in ("u", "v"):
                resolution = getattr(obj.data, f"render_resolution_{axis}")
                if resolution:
                    for_evaluation(obj.data, f"resolution_{axis}", resolution)
        if obj.type == "META":
            for_evaluation(obj.data, "resolution", obj.data.render_resolution)
        for modifier in obj.modifiers:
            for_evaluation(modifier, "show_viewport", modifier.show_render)
            if not modifier.show_render:
                continue
            if modifier.type in {"SUBSURF", "MULTIRES"}:
                for_evaluation(modifier, "levels", modifier.render_levels)
            if modifier.type == "SCREW":
                for_evaluation(modifier, "steps", modifier.render_steps)
            if modifier.type == "OCEAN":
                for_evaluation(modifier, "viewport_resolution", modifier.resolution)
    render = context.scene.render
    if render.use_simplify:
        for_evaluation(render, "simplify_subdivision", render.simplify_subdivision_render)
    context.view_layer.update()
    graph = context.evaluated_depsgraph_get()
    region_matrix = Matrix(transform.tolist())

    def crossing_bounds(evaluated, to_region):
        corners = [to_region @ Vector(point) for point in evaluated.bound_box]
        crossing = []
        for normal, limit in planes:
            distances = [normal.dot(point) - limit for point in corners]
            if min(distances) > 0:
                return None
            if max(distances) > 0:
                crossing.append((normal, limit))
        return crossing

    # A direct collection instance can be kept or hidden as a whole. Mixing
    # inside and outside children would require realizing the collection.
    replacements = {}
    instance_bounds = {}
    render_objects = set(objects)
    for entry in graph.object_instances:
        if not entry.is_instance or not entry.parent or entry.parent.original not in render_objects:
            continue
        parent = entry.parent.original
        # Blender reports evaluated NURBS surfaces as a self instance.
        if parent.type == "SURFACE" and entry.object.original == parent and not parent.modifiers:
            continue
        if parent.type != "EMPTY" or parent.instance_type != "COLLECTION":
            raise ValueError(f"Capture region requires realized geometry on {parent.name}.")
        instance = entry.object
        if instance.type not in {"MESH", "CURVE", "SURFACE", "FONT"} or instance.modifiers:
            raise ValueError(f"Capture region requires realized geometry on {parent.name}.")
        bounds = crossing_bounds(instance, region_matrix @ entry.matrix_world)
        if bounds:
            raise ValueError(f"Instance {parent.name} crosses the capture region. Realize it before capture.")
        instance_bounds.setdefault(parent, set()).add(bounds is None)
    for parent, outside in instance_bounds.items():
        if len(outside) != 1:
            raise ValueError(f"Instance {parent.name} spans the capture region. Realize it before capture.")
        if True in outside:
            replacements[parent] = None

    for obj in objects:
        if obj.type in {"CAMERA", "LIGHT", "EMPTY", "ARMATURE", "LATTICE", "LIGHT_PROBE", "SPEAKER"}:
            continue
        evaluated = obj.evaluated_get(graph)
        world = evaluated.matrix_world.copy()
        to_region = region_matrix @ world
        crossing_planes = crossing_bounds(evaluated, to_region)
        if crossing_planes is None:
            replacements[obj] = None
        if not crossing_planes:
            continue

        if obj.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
            raise ValueError(f"Capture region cannot clip {obj.name} ({obj.type}). Convert it to a mesh first.")
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=graph)
        if hasattr(evaluated.data, "texspace_size"):
            # Freeze the original bounds used by Generated coordinates before
            # changing the vertex buffer on the clipped mesh.
            mesh.use_auto_texspace = False
            mesh.texspace_location = evaluated.data.texspace_location
            mesh.texspace_size = evaluated.data.texspace_size
        surface = bmesh.new()
        try:
            surface.from_mesh(mesh)
            for normal, limit in crossing_planes:
                if not surface.faces:
                    break
                local_normal = to_region.to_3x3().transposed() @ normal
                if local_normal.length_squared == 0:
                    continue
                local_limit = limit - normal.dot(to_region.translation)
                bmesh.ops.bisect_plane(
                    surface, geom=[*surface.verts, *surface.edges, *surface.faces],
                    dist=0.000001, plane_co=local_normal * (local_limit / local_normal.length_squared),
                    plane_no=local_normal.normalized(), clear_outer=True,
                )
            has_faces = bool(surface.faces)
            if has_faces:
                surface.to_mesh(mesh)
        finally:
            surface.free()
        if not has_faces:
            bpy.data.meshes.remove(mesh)
            replacements[obj] = None
            continue
        clipped = bpy.data.objects.new(f"{obj.name} · Capture region", mesh)
        clipped.color = obj.color
        for key, value in obj.items():
            if isinstance(value, (bool, int, float, str)):
                clipped[key] = value
            elif isinstance(value, IDPropertyArray):
                clipped[key] = value.to_list()
        for index, slot in enumerate(obj.material_slots):
            if slot.link == "OBJECT" and index < len(mesh.materials) and mesh.materials[index] != slot.material:
                mesh.materials[index] = slot.material
        for field in ("visible_camera", "visible_diffuse", "visible_glossy", "visible_transmission",
                      "visible_volume_scatter", "visible_shadow", "is_holdout", "is_shadow_catcher",
                      "pass_index", "shadow_terminator_offset", "shadow_terminator_geometry_offset",
                      "is_caustics_caster", "is_caustics_receiver", "hide_probe_volume",
                      "hide_probe_sphere", "hide_probe_plane"):
            if not hasattr(obj, field) or not hasattr(clipped, field):
                continue
            value = getattr(obj, field)
            if getattr(clipped, field) != value:
                setattr(clipped, field, value)
        clipped.matrix_world = world
        replacements[obj] = clipped

    # Restore viewport settings before rendering so the evaluated view layer
    # does not keep render-resolution modifiers and hidden meshes alive.
    for obj in hidden_objects:
        obj.hide_set(True, view_layer=context.view_layer)
    for owner, field, value in reversed(viewport_settings):
        setattr(owner, field, value)

    # Keep the source IDs linked for depsgraph dependencies (rigs and constraints).
    if blocked := next((obj for obj in replacements if animated_render_visibility(obj)), None):
        raise ValueError(f"Capture region cannot override animated render visibility on {blocked.name}.")
    for obj, clipped in replacements.items():
        if not obj.is_editable or obj.is_property_readonly("hide_render"):
            raise ValueError(f"Capture region cannot cut or hide linked object {obj.name}. Make it local first.")
        if clipped is not None and any(not collection.is_editable for collection in obj.users_collection):
            raise ValueError(f"Capture region cannot replace {obj.name} in a linked collection. Make the collection local first.")
    for obj, clipped in replacements.items():
        obj.hide_render = True
        if clipped is None:
            continue
        name = obj.name
        obj.name = f"{name} · Capture source"
        clipped.name = name
        if obj.lightgroup:
            clipped.lightgroup = obj.lightgroup
        for field in ("receiver_collection", "blocker_collection"):
            collection = getattr(obj.light_linking, field)
            if collection is not None:
                setattr(clipped.light_linking, field, collection)
        for collection in obj.users_collection:
            collection.objects.link(clipped)
            source_link = collection.collection_objects[collection.objects.find(obj.name)]
            target_link = collection.collection_objects[collection.objects.find(clipped.name)]
            state = source_link.light_linking.link_state
            if target_link.light_linking.link_state != state:
                target_link.light_linking.link_state = state
    context.view_layer.update()
    return [result for obj in sources if (result := replacements.get(obj, obj)) is not None]
