# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""COLMAP observations connect exact synthetic depth to its visible surface points."""
from pathlib import Path
import numpy as np
import struct
import uuid
import os
from mathutils import Matrix


def write_visible(output, frames, points, colors):
    """One globally bounded colored cloud, shared by all valid image tracks."""
    from geometry_maps import read_depth_tiff
    root = Path(output) / 'sparse' / '0'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    observations = []
    for index, frame in enumerate(frames):
        width, height = frame['w'], frame['h']
        depth = read_depth_tiff(Path(output) / 'depths' / f'view_{index:06d}.tiff', width, height)
        pose = np.asarray(frame['transform_matrix'], dtype=np.float64)
        local = (points - pose[:3, 3]) @ pose[:3, :3]
        z = -local[:, 2]
        with np.errstate(divide='ignore', invalid='ignore'):
            u = local[:, 0] / z * frame['fl_x'] + frame['cx']
            v = -local[:, 1] / z * frame['fl_y'] + frame['cy']
        visible = np.flatnonzero((z > 0) & np.isfinite(u) & np.isfinite(v) &
                                 (u >= .5) & (u < width - .5) & (v >= .5) & (v < height - .5))
        x, y = u[visible] - .5, v[visible] - .5
        cols, rows = x.astype(np.int32), y.astype(np.int32)
        dx, dy = x - cols, y - rows
        values = np.stack((depth[rows, cols], depth[rows, cols+1], depth[rows+1, cols], depth[rows+1, cols+1]), axis=1)
        valid = np.isfinite(values).all(axis=1) & (values > 0).all(axis=1)
        weights = np.column_stack(((1-dx)*(1-dy), dx*(1-dy), (1-dx)*dy, dx*dy))
        with np.errstate(divide='ignore', invalid='ignore'):
            inverse_depth = (weights / values).sum(axis=1)
            valid &= np.abs(inverse_depth - 1/z[visible]) <= .01 / z[visible]
        indices = visible[valid]
        order = np.lexsort((u[indices], v[indices]))
        indices = indices[order]
        if len(indices) > 4096:
            indices = indices[np.linspace(0, len(indices)-1, 4096, dtype=np.int32)]
        if len(indices) < 256:
            raise ValueError(f'View {index+1} has only {len(indices)} visible depth correspondences. Increase the initial point budget or improve capture coverage (256 are required).')
        observations.append((indices, np.column_stack((u[indices], v[indices]))))
    counts = np.bincount(np.concatenate([item[0] for item in observations]), minlength=len(points))
    point_indices = np.concatenate([item[0] for item in observations])
    tracks = np.empty(len(point_indices), dtype=[('image', '<u4'), ('index', '<u4')])
    offset = 0
    for index, (indices, xy) in enumerate(observations):
        tracks['image'][offset:offset+len(indices)] = index+1
        tracks['index'][offset:offset+len(indices)] = np.arange(len(indices))
        offset += len(indices)
    tracks = tracks[np.argsort(point_indices, kind='stable')]
    offsets = np.r_[0, np.cumsum(counts)]
    # Unpublished files stay private to capture; the dataset manifest is last.
    with (root/'cameras.bin').open('wb') as cameras, (root/'images.bin').open('wb') as images:
        cameras.write(struct.pack('<Q', len(frames)))
        images.write(struct.pack('<Q', len(frames)))
        basis = np.diag([1,-1,-1])
        for index, (frame, (indices, xy)) in enumerate(zip(frames, observations)):
            identifier = index+1
            pose = np.asarray(frame['transform_matrix'], dtype=np.float64)
            rotation = basis @ pose[:3,:3].T @ basis
            translation = -basis @ pose[:3,:3].T @ pose[:3,3]
            q = Matrix(rotation.tolist()).to_quaternion()
            cameras.write(struct.pack('<iiQQdddd',identifier,1,frame['w'],frame['h'],
                                      *(frame[k] for k in ('fl_x','fl_y','cx','cy'))))
            images.write(struct.pack('<i7di',identifier,*q,*translation,identifier))
            images.write(Path(frame['file_path']).name.encode('utf-8')+b'\0')
            images.write(struct.pack('<Q',len(indices)))
            values = np.empty(len(indices),dtype=[('xy','<f8',(2,)),('id','<i8')])
            values['xy'],values['id'] = xy,indices+1
            images.write(values.tobytes())
    used = np.flatnonzero(counts)
    records = np.empty(len(used),dtype=[('id','<u8'),('xyz','<f8',(3,)),('rgb','u1',(3,)),('error','<f8'),('tracks','<u8')])
    records['id'],records['xyz'],records['rgb'] = used+1,points[used]*basis.diagonal(),colors[used]
    records['error'],records['tracks'] = 0,counts[used]
    with (root/'points3D.bin').open('wb') as stream:
        stream.write(struct.pack('<Q',len(used)))
        for record, index in zip(records,used):
            stream.write(record.tobytes())
            stream.write(tracks[offsets[index]:offsets[index+1]].tobytes())
    return {'points': len(used), 'tracks': len(tracks),
            'minimum_view_observations': min(len(item[0]) for item in observations)}


class ObservationWriter:
    def __init__(self, output):
        self.root = Path(output)/'sparse'/'0'
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.temporary = {name: self.root/f'{name}.bin.lf-{uuid.uuid4().hex}.tmp'
                          for name in ('cameras','images','points3D')}
        self.final = {name: self.root/f'{name}.bin' for name in self.temporary}
        self.streams = {}
        self.closed = False
        try:
            for name, path in self.temporary.items():
                stream = path.open('x+b')
                self.streams[name] = stream
                stream.write(struct.pack('<Q',0))
        except BaseException:
            self.abort()
            raise
        self.cameras, self.images, self.points = (self.streams[name] for name in ('cameras','images','points3D'))
        self.next_id = 1
        self.count = 0

    def write(self, index, position, valid, matrix, frame, rgba=None):
        if self.closed:
            raise ValueError('Observation capture is already closed')
        if valid.sum() < 256:
            raise ValueError('Depth supervision needs at least 256 valid foreground pixels per view. Increase image size or camera coverage.')
        height,width = valid.shape
        flat = np.flatnonzero(valid)
        if len(flat)>4096:
            flat = flat[np.linspace(0,len(flat)-1,4096,dtype=int)]
        rows,cols = np.divmod(flat,width)
        points = position.reshape(-1,3)[flat]*np.array([1,-1,-1])
        basis = np.diag([1,-1,-1])
        pose = np.asarray(matrix,dtype=np.float64)
        rotation = basis @ pose[:3,:3].T @ basis
        translation = -basis @ pose[:3,:3].T @ pose[:3,3]
        q = Matrix(rotation.tolist()).to_quaternion()
        camera_id = index+1
        self.cameras.write(struct.pack('<iiQQdddd',camera_id,1,width,height,
            *(frame[k] for k in ('fl_x','fl_y','cx','cy'))))
        self.images.write(struct.pack('<i7di',camera_id,*q,*translation,camera_id))
        self.images.write(f'view_{index:06d}.png'.encode()+b'\0')
        self.images.write(struct.pack('<Q',len(points)))
        ids = np.arange(self.next_id,self.next_id+len(points),dtype=np.uint64)
        observations = np.empty(len(points),dtype=[('xy','<f8',(2,)),('id','<i8')])
        observations['xy'][:,0] = cols+.5
        observations['xy'][:,1] = height-rows-.5
        observations['id'] = ids
        self.images.write(observations.tobytes())
        records = np.empty(len(points),dtype=[('id','<u8'),('xyz','<f8',(3,)),
            ('rgb','u1',(3,)),('error','<f8'),('tracks','<u8'),('image','<u4'),('index','<u4')])
        records['id']=ids;records['xyz']=points;records['rgb']=128 if rgba is None else rgba.reshape(-1,4)[flat,:3];records['error']=0
        records['tracks']=1;records['image']=camera_id;records['index']=np.arange(len(points))
        self.points.write(records.tobytes())
        self.next_id += len(points)
        self.count += 1

    def close(self):
        if self.closed:
            return
        try:
            for stream,count in ((self.cameras,self.count),(self.images,self.count),(self.points,self.next_id-1)):
                stream.seek(0);stream.write(struct.pack('<Q',count))
            for stream in self.streams.values():
                stream.close()
            self.closed = True
            for name, temporary in self.temporary.items():
                os.replace(temporary, self.final[name])
        except BaseException:
            self.abort()
            raise

    def abort(self):
        """Close this writer and remove only its uniquely named pending files."""
        for stream in getattr(self, 'streams', {}).values():
            if not stream.closed:
                stream.close()
        for path in getattr(self, 'temporary', {}).values():
            path.unlink(missing_ok=True)
        self.closed = True
