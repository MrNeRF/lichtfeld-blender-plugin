# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure deterministic camera distributions and arc-length path sampling."""
import bisect
import math
import random


def seeded_offset(index, seed, amplitude, phase=0.0, stream=0):
    """Static, index-stable displacement within a ball of the given radius."""
    if not math.isfinite(amplitude) or amplitude < 0:
        raise ValueError('Variation must be finite and nonnegative')
    if amplitude == 0:
        return (0.0, 0.0, 0.0)
    rng = random.Random((stream << 96) + (seed << 32) + index)
    radius = amplitude * rng.random() ** (1/3)
    z = 2*rng.random()-1
    angle = 2*math.pi*rng.random() + phase
    planar = math.sqrt(max(0, 1-z*z))
    return (radius*planar*math.cos(angle), radius*planar*math.sin(angle), radius*z)


def guide_path(shape, radius, start, sweep, turns, minimum, maximum, height):
    """Regular editable paths; spherical bands advance uniformly in sin(elevation)."""
    if shape not in {'ORBIT', 'HELIX', 'SPIRAL'}:
        raise ValueError('Unknown path guide shape')
    if not all(math.isfinite(v) for v in (radius, start, sweep, turns, minimum, maximum, height)) or radius <= 0:
        raise ValueError('Guide dimensions must be finite and radius positive')
    # Blender stores RNA angles as float32, including the 360/90 degree bounds.
    if math.isclose(sweep, 2 * math.pi, rel_tol=0, abs_tol=1e-6):
        sweep = 2 * math.pi
    if not 0 < sweep <= 2 * math.pi or turns <= 0:
        raise ValueError('Use a positive sweep and number of turns')
    if math.isclose(minimum, -math.pi/2, rel_tol=0, abs_tol=1e-6):
        minimum = -math.pi/2
    if math.isclose(maximum, math.pi/2, rel_tol=0, abs_tol=1e-6):
        maximum = math.pi/2
    if shape == 'SPIRAL' and not -math.pi / 2 <= minimum < maximum <= math.pi / 2:
        raise ValueError('Minimum elevation must be below maximum elevation')
    if shape == 'HELIX' and abs(height) < 1e-8:
        raise ValueError('A helix needs a nonzero rise; use Orbit / arc for a flat path')
    closed = shape == 'ORBIT' and math.isclose(sweep, 2 * math.pi, abs_tol=1e-6)
    angle_range = sweep if shape == 'ORBIT' else 2 * math.pi * turns
    segments = max(4, math.ceil(angle_range / math.radians(5)))
    if shape == 'SPIRAL':
        # Bound the elevation step even close to the poles, where linear z
        # produces a much larger angular step than at the equator.
        segments = max(segments, math.ceil((math.sin(maximum)-math.sin(minimum)) /
                                         (1-math.cos(math.radians(5)))))
    points = []
    for index in range(segments + (not closed)):
        t = index / segments
        angle = start + angle_range * t
        z = (math.sin(minimum) + (math.sin(maximum) - math.sin(minimum)) * t
             if shape == 'SPIRAL' else 0)
        r = radius * math.sqrt(max(0, 1 - z*z)) if shape == 'SPIRAL' else radius
        points.append((r * math.cos(angle), r * math.sin(angle),
                       radius*z if shape == 'SPIRAL' else height*t if shape == 'HELIX' else 0))
    return points, closed


def directions(count, mode, minimum=0.0, maximum=90.0, rings=3, phase=0.0, *, sweep=2*math.pi):
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
        if not math.isfinite(sweep) or not math.isfinite(phase):
            raise ValueError('Ring angles must be finite')
        if math.isclose(sweep, 2*math.pi, rel_tol=0, abs_tol=1e-6):
            sweep = 2*math.pi  # RNA rounds the 360-degree bound to float32.
        if not 0 < sweep <= 2*math.pi:
            raise ValueError('Use an angular sweep greater than zero and no larger than 360 degrees')
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
                angle = (phase + 2*math.pi*(index + (ring % 2)*0.5)/size if sweep == 2*math.pi else
                         phase + sweep*index/(size-1))
                yield (math.cos(elevation)*math.cos(angle),
                       math.cos(elevation)*math.sin(angle), math.sin(elevation))
    else:
        raise ValueError("Unknown camera distribution")


def sample_path(points, count, cyclic=False, *, spacing=None):
    if spacing is None and count < 2:
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
    if spacing is not None:
        if not math.isfinite(spacing) or spacing <= 0:
            raise ValueError("Camera spacing must be positive and finite")
        intervals = distance[-1] / spacing
        if not math.isfinite(intervals):
            raise ValueError('Spacing would exceed 1,000 cameras; increase the spacing')
        count = math.ceil(intervals) + (not cyclic)
        if not 4 <= count <= 1000:
            raise ValueError(f"Spacing would create {count:,} cameras; adjust it to create 4–1,000 cameras")
    for index in range(count):
        position = distance[-1] * index / (count if cyclic else count - 1)
        segment = min(len(points)-2, max(0, bisect.bisect_right(distance, position)-1))
        length = distance[segment+1]-distance[segment]
        fraction = (position-distance[segment])/length if length else 0
        a, b = points[segment:segment+2]
        yield tuple(x + fraction*(y-x) for x, y in zip(a, b))
