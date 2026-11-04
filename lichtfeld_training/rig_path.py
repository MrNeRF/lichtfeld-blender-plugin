# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Evaluate a single Blender curve without changing the original object."""
import bpy
from .rig_geometry import sample_path


def positions(context, curve, count):
    if not curve or curve.type != 'CURVE' or len(curve.data.splines) != 1:
        raise ValueError('Choose a Curve with exactly one spline (Bezier, Poly or NURBS)')
    temporary = curve.copy()
    temporary.data = curve.data.copy()
    data = temporary.data
    data.bevel_depth = 0
    data.bevel_object = None
    data.extrude = 0
    data.dimensions = '3D'
    context.scene.collection.objects.link(temporary)
    evaluated = None
    try:
        yield None
        evaluated = temporary.evaluated_get(context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        if not mesh or len(mesh.vertices) < 2:
            raise ValueError('The evaluated curve has no usable path')
        neighbors = [[] for _ in mesh.vertices]
        for edge in mesh.edges:
            a, b = edge.vertices
            neighbors[a].append(b)
            neighbors[b].append(a)
            yield None
        if any(not links or len(links)>2 for links in neighbors):
            raise ValueError('The evaluated curve must be a single unbranched path')
        endpoints = [i for i, links in enumerate(neighbors) if len(links)==1]
        cyclic = not endpoints
        if endpoints and len(endpoints)!=2:
            raise ValueError('The evaluated curve contains disconnected paths')
        current, previous = (endpoints[0] if endpoints else 0), None
        points, visited = [], set()
        while current not in visited:
            visited.add(current)
            points.append(tuple(evaluated.matrix_world @ mesh.vertices[current].co))
            candidates = [i for i in neighbors[current] if i != previous]
            if not candidates:
                break
            previous, current = current, candidates[0]
            yield None
        if len(visited)!=len(mesh.vertices):
            raise ValueError('The evaluated curve contains disconnected paths')
        for point in sample_path(points, count, cyclic):
            yield point
    finally:
        if evaluated:
            evaluated.to_mesh_clear()
        bpy.data.objects.remove(temporary, do_unlink=True)
        if not data.users:
            bpy.data.curves.remove(data)
