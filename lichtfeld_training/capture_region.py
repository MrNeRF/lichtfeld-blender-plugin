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

    objects = tuple(obj for obj in context.view_layer.objects if not obj.hide_render)
    for obj in objects:
        for modifier in obj.modifiers:
            if modifier.show_viewport != modifier.show_render:
                modifier.show_viewport = modifier.show_render
            if modifier.type in {"SUBSURF", "MULTIRES"} and modifier.levels != modifier.render_levels:
                modifier.levels = modifier.render_levels
    context.view_layer.update()
    graph = context.evaluated_depsgraph_get()
    if any(entry.is_instance for entry in graph.object_instances):
        raise ValueError("Capture region requires realized geometry. Realize scene instances before capture.")

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
        obj.hide_render = True
    context.view_layer.update()
    return [result for obj in sources if (result := replacements.get(obj, obj)) is not None]
