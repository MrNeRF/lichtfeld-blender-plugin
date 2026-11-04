# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only helper mesh snapshots; numeric placement runs on a worker."""
import numpy as np


def calculate(vertices, triangles, polygon_ids, centers, normals, matrix, count, distribution, orientation, offset):
    if (type(count) is not int or not 4 <= count <= 1000 or distribution not in {'SURFACE', 'FACES'} or
            orientation not in {'INWARD', 'OUTWARD', 'TARGET'} or not np.isfinite(offset)):
        raise ValueError("Invalid mesh camera placement parameters")
    transform = np.asarray(matrix, dtype=np.float64)
    if not np.isfinite(transform).all() or abs(np.linalg.det(transform[:3, :3])) < 1e-12:
        raise ValueError("The placement mesh has a singular or invalid transform")
    world = vertices @ transform[:3, :3].T + transform[:3, 3]
    directions = normals @ np.linalg.inv(transform[:3, :3])
    lengths = np.linalg.norm(directions, axis=1)
    if not np.isfinite(world).all() or not np.isfinite(directions).all():
        raise ValueError("The placement mesh has non-finite geometry")
    directions /= np.maximum(lengths[:, None], 1e-30)
    if distribution == 'FACES':
        if not 4 <= len(centers) <= 1000 or np.any(lengths < 1e-12):
            raise ValueError("One-camera-per-face placement needs 4–1,000 non-degenerate faces")
        points = centers @ transform[:3, :3].T + transform[:3, 3]
        normal = directions
    else:
        xyz = world[triangles]
        area = np.linalg.norm(np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]), axis=1)
        area[lengths[polygon_ids] < 1e-12] = 0
        if not np.isfinite(area).all() or area.sum() <= 1e-12:
            raise ValueError("The placement mesh has no usable surface")
        cdf = np.cumsum(area) / area.sum()
        selected = np.minimum(np.searchsorted(cdf, (np.arange(count) + .5) / count), len(xyz) - 1)
        sequence = np.arange(count) + 1
        u = np.sqrt(np.mod(sequence * .7548776662466927, 1))
        v = np.mod(sequence * .5698402909980532, 1)
        points = ((1-u[:, None]) * xyz[selected, 0] + (u*(1-v))[:, None] * xyz[selected, 1] +
                  (u*v)[:, None] * xyz[selected, 2])
        normal = directions[polygon_ids[selected]]
    points = points + normal * offset
    forward = -normal if orientation == 'INWARD' else normal
    return list(zip(points.tolist(), forward.tolist()))


def positions(context, helper, count, distribution, orientation, offset):
    if not helper or helper.type != 'MESH' or helper.name not in context.scene.objects:
        raise ValueError("Choose a placement mesh in this scene")
    from . import ui_runtime
    evaluated = helper.evaluated_get(context.evaluated_depsgraph_get())
    try:
        mesh = evaluated.to_mesh()
        if not mesh or not mesh.polygons:
            raise ValueError("The placement mesh has no faces")
        if len(mesh.vertices) > 200000 or len(mesh.polygons) > 200000:
            raise ValueError("Use a simplified placement mesh (at most 200,000 vertices/faces)")
        mesh.calc_loop_triangles()
        vertices = np.empty(len(mesh.vertices) * 3, np.float64)
        mesh.vertices.foreach_get('co', vertices)
        yield None
        triangles = np.empty(len(mesh.loop_triangles) * 3, np.int32)
        mesh.loop_triangles.foreach_get('vertices', triangles)
        polygon_ids = np.empty(len(mesh.loop_triangles), np.int32)
        mesh.loop_triangles.foreach_get('polygon_index', polygon_ids)
        yield None
        centers, normals = np.empty(len(mesh.polygons) * 3, np.float64), np.empty(len(mesh.polygons) * 3, np.float64)
        mesh.polygons.foreach_get('center', centers)
        mesh.polygons.foreach_get('normal', normals)
        matrix = np.array(evaluated.matrix_world, dtype=np.float64)
    finally:
        evaluated.to_mesh_clear()
    future = ui_runtime.submit(lambda: calculate(vertices.reshape(-1, 3), triangles.reshape(-1, 3), polygon_ids,
                                                centers.reshape(-1, 3), normals.reshape(-1, 3), matrix,
                                                count, distribution, orientation, offset))
    try:
        while not future.done():
            yield None
        for item in future.result():  # Completed numeric work; never waits on UI.
            yield item
    finally:
        future.cancel()
