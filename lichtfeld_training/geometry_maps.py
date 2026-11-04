# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Float TIFF sidecars and explicit Blender-world to camera-OpenCV conversion."""
from pathlib import Path
import struct
import numpy as np


def unproject_depth(depth, camera_to_world, intrinsics):
    """Exact pinhole unprojection of the renderer's metric camera-Z pass."""
    height, width = depth.shape
    pose = np.asarray(camera_to_world, dtype=np.float32)
    z = np.where(np.isfinite(depth) & (depth > 0) & (depth < 1e9), depth, 0).astype(np.float32)
    x = ((np.arange(width, dtype=np.float32) + .5 - intrinsics['cx']) / intrinsics['fl_x'])[None, :]
    y = (-(height - np.arange(height, dtype=np.float32) - .5 - intrinsics['cy']) / intrinsics['fl_y'])[:, None]
    result = np.empty((height, width, 3), np.float32)
    for axis in range(3):
        result[:, :, axis] = z * (x * pose[axis, 0] + y * pose[axis, 1] - pose[axis, 2]) + pose[axis, 3]
    return result


def write_float_tiff(path, pixels):
    """Uncompressed IEEE float TIFF, top-left origin, no color transfer function."""
    data = np.asarray(pixels, dtype='<f4')
    if data.ndim == 2:
        data = data[:, :, None]
    height, width, channels = data.shape
    if channels not in (1, 3) or not np.isfinite(data).all():
        raise ValueError('Expected finite single-channel or RGB float data')
    # Blender pixel buffers start at the bottom-left; TIFF orientation 1 is top-left.
    payload = np.ascontiguousarray(data[::-1]).tobytes()
    tags = [(256,4,1,width), (257,4,1,height), (258,3,channels,32),
            (259,3,1,1), (262,3,1,1 if channels==1 else 2),
            (273,4,1,0), (274,3,1,1), (277,3,1,channels),
            (278,4,1,height), (279,4,1,len(payload)), (284,3,1,1),
            (339,3,channels,3)]
    extra = bytearray()
    offset = 8 + 2 + 12*len(tags) + 4
    entries = []
    for tag, kind, count, value in tags:
        if kind == 3 and count > 2:
            value = offset + len(extra)
            extra.extend(struct.pack('<'+'H'*count, *([32 if tag==258 else 3]*count)))
        entries.append([tag,kind,count,value])
    start = offset + len(extra)
    entries[5][3] = start
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open('wb') as stream:
        stream.write(b'II' + struct.pack('<HIH', 42, 8, len(tags)))
        for entry in entries:
            stream.write(struct.pack('<HHII', *entry))
        stream.write(struct.pack('<I',0))
        stream.write(extra)
        stream.write(payload)


def read_depth_tiff(path, width, height):
    """Read our own single-strip float TIFF, with bounded layout validation."""
    path = Path(path)
    if path.is_symlink() or not 0 < width <= 4096 or not 0 < height <= 4096:
        raise ValueError('Invalid saved depth map')
    if path.stat().st_size > width * height * 4 + 4096:
        raise ValueError('Saved depth map exceeds its size limit')
    data = path.read_bytes()
    if data[:4] != b'II*\0' or len(data) < 10:
        raise ValueError('Unsupported saved depth map')
    offset = struct.unpack_from('<I', data, 4)[0]
    if offset > 4096 or offset + 2 > len(data):
        raise ValueError('Invalid depth TIFF directory')
    count = struct.unpack_from('<H', data, offset)[0]
    if count > 64 or offset + 2 + count * 12 + 4 > len(data):
        raise ValueError('Invalid depth TIFF tags')
    tags = {}
    for index in range(count):
        tag, kind, size, value = struct.unpack_from('<HHII', data, offset + 2 + index * 12)
        tags[tag] = (kind, size, value)
    expected = {256: width, 257: height, 258: 32, 259: 1, 274: 1, 277: 1, 278: height,
                279: width * height * 4, 284: 1, 339: 3}
    if any(tags.get(tag, (None, None, None))[1:] != (1, value) for tag, value in expected.items()):
        raise ValueError('Saved depth TIFF layout changed')
    start = tags.get(273, (None, None, 0))[2]
    if start < offset + 2 + count * 12 + 4 or start + width * height * 4 != len(data):
        raise ValueError('Invalid saved depth TIFF data')
    return np.frombuffer(data, dtype='<f4', count=width * height, offset=start).reshape(height, width)


def convert(position, normal, camera_to_world):
    matrix = np.asarray(camera_to_world, dtype=np.float64)
    rotation = matrix[:3,:3]
    camera = (position - matrix[:3,3]) @ rotation
    depth = -camera[:,:,2]
    length = np.linalg.norm(normal, axis=2)
    valid = (np.isfinite(position).all(axis=2) & np.isfinite(normal).all(axis=2) &
             np.isfinite(depth) & (depth>0) & (length>1e-6))
    normals = normal @ rotation
    normals *= np.array([1,-1,-1])
    normals /= np.maximum(length[:,:,None], 1e-20)
    encoded = np.full_like(normals, .5, dtype=np.float32)
    encoded[valid] = np.clip(normals[valid]*.5+.5,0,1)
    return np.where(valid, depth, 0).astype(np.float32), encoded, valid
