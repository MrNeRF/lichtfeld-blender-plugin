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


def render_collections(layer):
    if layer.exclude or layer.collection.hide_render:
        return
    yield layer.collection
    for child in layer.children:
        yield from render_collections(child)


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
    from mathutils import Matrix, Vector

    if shape == "BOX":
        planes = [(Vector(axis), 1.0) for axis in
                  ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]
    else:
        # An inscribed convex polyhedron keeps every retained point inside the sphere.
        hull = bmesh.new()
        try:
            bmesh.ops.create_icosphere(hull, subdivisions=3, radius=1.0)
            hull.normal_update()
            planes = [(face.normal.copy(), face.normal.dot(face.verts[0].co)) for face in hull.faces]
        finally:
            hull.free()

    collections = tuple(dict.fromkeys(render_collections(context.view_layer.layer_collection)))
    objects = tuple(dict.fromkeys(obj for collection in collections for obj in collection.objects
                                  if not obj.hide_render))
    for collection in collections:
        if collection.hide_viewport:
            collection.hide_viewport = False
    for obj in objects:
        if obj.hide_viewport:
            obj.hide_viewport = False
        if obj.hide_get(view_layer=context.view_layer):
            obj.hide_set(False, view_layer=context.view_layer)
        if obj.type in {"CURVE", "SURFACE", "FONT"} and obj.data.render_resolution_u:
            obj.data.resolution_u = obj.data.render_resolution_u
        if obj.type == "META":
            obj.data.resolution = obj.data.render_resolution
        for modifier in obj.modifiers:
            if modifier.show_viewport != modifier.show_render:
                modifier.show_viewport = modifier.show_render
            if modifier.type in {"SUBSURF", "MULTIRES"} and modifier.levels != modifier.render_levels:
                modifier.levels = modifier.render_levels
            if modifier.type == "SCREW":
                modifier.steps = modifier.render_steps
            if modifier.type == "OCEAN":
                modifier.viewport_resolution = modifier.resolution
    render = context.scene.render
    if render.use_simplify:
        render.simplify_subdivision = render.simplify_subdivision_render
        render.simplify_child_particles = render.simplify_child_particles_render
    context.view_layer.update()
    graph = context.evaluated_depsgraph_get()
    render_objects = set(objects)
    if any(entry.is_instance and entry.parent and entry.parent.original in render_objects
           for entry in graph.object_instances):
        raise ValueError("Capture region requires realized geometry. Realize visible instances before capture.")

    region_matrix = Matrix(transform.tolist())
    replacements = {}
    for obj in objects:
        if obj.type in {"CAMERA", "LIGHT", "EMPTY", "ARMATURE", "LATTICE", "LIGHT_PROBE", "SPEAKER"}:
            continue
        evaluated = obj.evaluated_get(graph)
        world = evaluated.matrix_world.copy()
        to_region = region_matrix @ world
        corners = [to_region @ Vector(point) for point in evaluated.bound_box]
        crossing_planes = []
        for normal, limit in planes:
            distances = [normal.dot(point) - limit for point in corners]
            if min(distances) > 0:
                replacements[obj] = None
                break
            if max(distances) > 0:
                crossing_planes.append((normal, limit))
        if obj in replacements or not crossing_planes:
            continue

        if any(modifier.type == "NODES" and modifier.show_render
               and uses_viewport_switch(modifier.node_group, set()) for modifier in obj.modifiers):
            raise ValueError(f"Capture region cannot clip {obj.name} using Geometry Nodes Is Viewport.")
        if obj.particle_systems:
            raise ValueError(f"Capture region cannot clip particle systems on {obj.name}. Realize them before capture.")
        if obj.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
            raise ValueError(f"Capture region cannot clip {obj.name} ({obj.type}). Convert it to a mesh first.")
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=graph)
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
        for index, slot in enumerate(obj.material_slots):
            if slot.link == "OBJECT" and index < len(mesh.materials) and mesh.materials[index] != slot.material:
                mesh.materials[index] = slot.material
        for field in ("visible_camera", "visible_diffuse", "visible_glossy", "visible_transmission",
                      "visible_volume_scatter", "visible_shadow", "is_holdout", "is_shadow_catcher",
                      "pass_index"):
            value = getattr(obj, field)
            if getattr(clipped, field) != value:
                setattr(clipped, field, value)
        clipped.matrix_world = world
        for collection in obj.users_collection:
            collection.objects.link(clipped)
        replacements[obj] = clipped

    # Keep the source IDs linked for depsgraph dependencies (rigs and constraints).
    for obj in replacements:
        if animated_render_visibility(obj):
            raise ValueError(f"Capture region cannot override animated render visibility on {obj.name}.")
        obj.hide_render = True
    context.view_layer.update()
    return [result for obj in sources if (result := replacements.get(obj, obj)) is not None]
