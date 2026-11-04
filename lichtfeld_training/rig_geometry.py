# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure deterministic camera distributions and arc-length path sampling."""
import bisect
import math


def directions(count, mode, minimum=0.0, maximum=90.0, rings=3, phase=0.0):
    if count < 4:
        raise ValueError("Use at least four cameras")
    if mode == "SPHERE":
        minimum, maximum = -90.0, 90.0
    if mode in {"SPHERE", "HEMISPHERE"}:
        if not -90 <= minimum < maximum <= 90:
            raise ValueError("Minimum elevation must be below maximum elevation")
        low, high = math.sin(math.radians(minimum)), math.sin(math.radians(maximum))
        for index in range(count):
            z = low + (high - low) * (index + 0.5) / count
            angle = phase + index * math.pi * (3 - math.sqrt(5))
            radius = math.sqrt(max(0, 1 - z*z))
            yield (radius * math.cos(angle), radius * math.sin(angle), z)
    elif mode == "RINGS":
        if not -89.9 <= minimum <= maximum <= 89.9:
            raise ValueError("Ring elevations must stay between -89.9 and 89.9 degrees")
        if rings > 1 and minimum == maximum:
            raise ValueError("Multiple rings require different elevations")
        if not 1 <= rings <= count // 4:
            raise ValueError("Each ring needs at least four cameras")
        for ring in range(rings):
            size = count // rings + (ring < count % rings)
            elevation = math.radians((minimum + maximum)/2 if rings == 1 else
                                     minimum + (maximum-minimum)*ring/(rings-1))
            for index in range(size):
                angle = phase + 2*math.pi*(index + (ring % 2)*0.5)/size
                yield (math.cos(elevation)*math.cos(angle),
                       math.cos(elevation)*math.sin(angle), math.sin(elevation))
    else:
        raise ValueError("Unknown camera distribution")


def sample_path(points, count, cyclic=False):
    if count < 2:
        raise ValueError("The path needs at least two samples")
    if any(not math.isfinite(value) for point in points for value in point):
        raise ValueError("Path coordinates must be finite")
    if len(points) < 2:
        raise ValueError("The path needs at least two distinct points")
    points = list(points)
    if cyclic and points[-1] != points[0]:
        points.append(points[0])
    distance = [0.0]
    for a, b in zip(points, points[1:]):
        distance.append(distance[-1] + math.dist(a, b))
    if distance[-1] < 1e-8:
        raise ValueError("The path has zero length")
    for index in range(count):
        position = distance[-1] * index / (count if cyclic else count - 1)
        segment = min(len(points)-2, max(0, bisect.bisect_right(distance, position)-1))
        length = distance[segment+1]-distance[segment]
        fraction = (position-distance[segment])/length if length else 0
        a, b = points[segment:segment+2]
        yield tuple(x + fraction*(y-x) for x, y in zip(a, b))
