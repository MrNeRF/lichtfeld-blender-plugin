# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Visible colored initialization, bounded by per-view and global point budgets."""
from pathlib import Path
import math
import os
import uuid
import zipfile
import numpy as np
if __package__:
    from .capture_settings import validate_view_count
else:
    from capture_settings import validate_view_count


def view_budget(limit, views):
    validate_view_count(views)
    if type(limit) is not int or not 4 <= limit <= 250000:
        raise ValueError("Invalid initialization budget or capture view count")
    return max(256, min(4096, math.ceil(4 * limit / views)))


def instance_sample_plan(limit, instances):
    """Deterministically distribute a strict global surface-sample budget."""
    if type(limit) is not int or limit < 1 or type(instances) is not int or instances < 1:
        raise ValueError('Surface sampling needs a positive budget and instance count')
    selected = min(limit, instances)
    if instances <= limit:
        base, remainder = divmod(limit, instances)
        for index in range(instances):
            yield index, base + (index < remainder)
    else:
        # Evenly cover the evaluated dependency-graph sequence without retaining
        # an index list proportional to a potentially enormous instance count.
        for rank in range(limit):
            yield (rank * instances) // limit, 1


def valid_pixels(position, depth, rgba):
    height, width = depth.shape
    if position.shape != (height, width, 3) or rgba.shape != (height, width, 4):
        raise ValueError("Initialization passes have inconsistent dimensions")
    valid = (np.isfinite(depth) & (depth > 0) & (depth < 1e9) &
             np.isfinite(position).all(axis=2) & (rgba[:, :, 3] >= 250))
    # RGB is antialiased while numerical passes can describe a single surface.
    # Avoid foreground/background mixtures and depth discontinuities at outlines.
    base = valid.copy()
    for a, b in ((np.s_[1:, :], np.s_[:-1, :]), (np.s_[:-1, :], np.s_[1:, :]),
                 (np.s_[:, 1:], np.s_[:, :-1]), (np.s_[:, :-1], np.s_[:, 1:])):
        with np.errstate(invalid='ignore'):
            valid[a] &= base[b] & (np.abs(depth[a] - depth[b]) <= np.maximum(.05 * depth[a], 1e-5))
    return valid


def pixel_indices(valid, limit, index=0):
    """Select valid-pixel ranks without an int64 index for every image pixel."""
    counts = np.count_nonzero(valid, axis=1)
    cumulative = np.cumsum(counts)
    total = int(cumulative[-1])
    if total > limit:
        rng = np.random.default_rng(142857 + index)
        ranks = ((np.arange(limit) + rng.random(limit)) * total / limit).astype(np.int64)
    else:
        ranks = np.arange(total, dtype=np.int64)
    rows = np.searchsorted(cumulative, ranks, side='right')
    selected = np.empty(len(ranks), np.int64)
    unique, starts, lengths = np.unique(rows, return_index=True, return_counts=True)
    for row, start, length in zip(unique, starts, lengths):
        columns = np.flatnonzero(valid[row])
        prior = cumulative[row-1] if row else 0
        selected[start:start+length] = row * valid.shape[1] + columns[ranks[start:start+length]-prior]
    return selected


def samples(position, depth, rgba, matrix, frame, limit, index=0, valid=None):
    """Positions and encoded PNG colors refer to the same rendered pixel centers."""
    height, width = depth.shape
    if valid is None:
        valid = valid_pixels(position, depth, rgba)
    indices = pixel_indices(valid, limit, index)
    points = position.reshape(-1, 3)[indices]
    pose = np.asarray(matrix, dtype=np.float64)
    local = (points - pose[:3, 3]) @ pose[:3, :3]
    z = -local[:, 2]
    rows, cols = np.divmod(indices, width)
    with np.errstate(divide="ignore", invalid="ignore"):
        u = local[:, 0] / z * frame['fl_x'] + frame['cx']
        v = -local[:, 1] / z * frame['fl_y'] + frame['cy']
    consistent = ((z > 0) & np.isfinite(u) & np.isfinite(v) &
                  (np.abs(u - cols - .5) <= 1.25) &
                  (np.abs(v - (height - rows - .5)) <= 1.25))
    return points[consistent].astype(np.float32), rgba.reshape(-1, 4)[indices[consistent], :3].copy()


def save_view(output, index, points, colors):
    directory = Path(output) / 'capture_points'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f'view_{index:06d}.npz'
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            np.savez(stream, points=points, colors=colors)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_view(output, index, limit):
    path = Path(output) / 'capture_points' / f'view_{index:06d}.npz'
    if path.is_symlink() or not path.resolve().is_relative_to(Path(output).resolve()):
        raise ValueError("Initialization samples cannot be a symbolic link")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if ({entry.filename for entry in entries} != {'points.npy', 'colors.npy'} or len(entries) != 2 or
                    sum(entry.file_size for entry in entries) > limit * 15 + 2048):
                raise ValueError("Invalid initialization sample archive")
            arrays = []
            for name, expected in (('points.npy', np.dtype(np.float32)), ('colors.npy', np.dtype(np.uint8))):
                with archive.open(name) as stream:
                    version = np.lib.format.read_magic(stream)
                    if version not in {(1, 0), (2, 0)}:
                        raise ValueError('Unsupported initialization sample format')
                    header = (np.lib.format.read_array_header_1_0 if version == (1, 0) else
                              np.lib.format.read_array_header_2_0)
                    shape, fortran, dtype = header(stream, max_header_size=1024)
                    if (len(shape) != 2 or shape[1] != 3 or not 0 <= shape[0] <= limit or
                            dtype != expected or fortran):
                        raise ValueError('Invalid initialization sample dimensions or type')
                    length = shape[0] * 3 * dtype.itemsize
                    data = stream.read(length + 1)
                    if len(data) != length:
                        raise ValueError('Invalid initialization sample payload')
                    arrays.append(np.frombuffer(data, dtype=dtype).reshape(shape))
            points, colors = arrays
        if (points.ndim != 2 or points.shape[1:] != (3,) or len(points) > limit or
                colors.shape != points.shape or points.dtype != np.float32 or
                colors.dtype != np.uint8 or not np.isfinite(points).all()):
            raise ValueError("Invalid initialization samples")
        return points, colors
    except (FileNotFoundError, zipfile.BadZipFile) as error:
        raise ValueError(f"Visible-point samples for view {index + 1} are missing or incomplete. Existing rendered images are preserved.") from error


def merge(views, limit):
    points = np.concatenate([value[0] for value in views])
    colors = np.concatenate([value[1] for value in views])
    if len(points) < 4:
        raise ValueError("Too few visible surface points. Check camera coverage and transparent surfaces.")
    low, high = points.min(axis=0), points.max(axis=0)
    tolerance = max(float((high - low).max()) * 1e-6, 1e-7)
    keys = np.rint((points.astype(np.float64) - low) / tolerance).astype(np.int64)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    # Average repeated surface/color observations before choosing spatial coverage.
    points = np.column_stack([np.bincount(inverse, weights=points[:, i]) / counts for i in range(3)]).astype(np.float32)
    colors = np.rint(np.column_stack([np.bincount(inverse, weights=colors[:, i]) / counts for i in range(3)])).astype(np.uint8)
    if len(points) < 4:
        raise ValueError("Fewer than four distinct visible points. Check capture coverage.")
    if len(points) > limit:
        from lichtfeld_sessions.capture import spatial_indices
        chosen = spatial_indices(points, limit)
        points, colors = points[chosen], colors[chosen]
    return points, colors


def write_ply(path, points, colors):
    records = np.empty(len(points), dtype=[('xyz', '<f4', (3,)), ('rgb', 'u1', (3,))])
    records['xyz'], records['rgb'] = points, colors
    path = Path(path)
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write((f'ply\nformat binary_little_endian 1.0\nelement vertex {len(points)}\n'
                          'property float x\nproperty float y\nproperty float z\n'
                          'property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n').encode('ascii'))
            stream.write(records.tobytes())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def finalize(output, limit, count, return_cloud=False):
    budget = view_budget(limit, count)
    points, colors = merge([load_view(output, index, budget) for index in range(count)], limit)
    write_ply(Path(output) / 'pointcloud.ply', points, colors)
    info = {'method': 'visible_rendered', 'points': len(points), 'budget': limit,
            'color_space': 'encoded dataset RGB', 'samples_per_view': budget}
    return (info, points, colors) if return_cloud else info
