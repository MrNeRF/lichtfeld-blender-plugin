# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Background Blender compositor for numerical geometry passes (Blender 5.3)."""
from pathlib import Path
import uuid
import bpy
import numpy as np
from geometry_maps import convert, write_float_tiff, unproject_depth


class GeometryPasses:
    def __init__(self, scene, layer, output, observations=False, seed_limit=None, views=0, normals=True):
        self.scene, self.layer, self.output = scene, layer, Path(output).resolve()
        self.seed_limit = seed_limit
        self.views = views
        self.normals = normals
        self.observations = None
        self.directory = self.output / '_geometry_passes'
        self.token = uuid.uuid4().hex
        self.prefix = f'lf_{self.token}_'
        self.tree = None
        self.nodes = {}
        self.closed = False
        self.directory_existed = self.directory.exists()
        self.previous_group = scene.compositing_node_group
        self.previous_compositing = scene.render.use_compositing
        self.previous_precision = scene.render.compositor_precision
        self.previous_passes = (layer.use_pass_z, layer.use_pass_position, layer.use_pass_normal)
        try:
            self.directory.mkdir(mode=0o700, exist_ok=True)
            if observations:
                from capture_observations import ObservationWriter
                self.observations = ObservationWriter(output)
            layer.use_pass_z = True
            # Recover world positions from camera-Z and the actual projection, saving
            # a large position EXR per view while preserving lens shift/pixel aspect.
            layer.use_pass_position = False
            layer.use_pass_normal = normals
            tree = bpy.data.node_groups.new('LichtFeld geometry capture', 'CompositorNodeTree')
            self.tree = tree
            image = tree.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
            render = tree.nodes.new('CompositorNodeRLayers')
            render.scene = scene
            render.layer = layer.name
            result = tree.nodes.new('NodeGroupOutput')
            tree.links.new(render.outputs['Image'], result.inputs[image.identifier])
            for name in (('Depth', 'Normal') if normals else ('Depth',)):
                node = tree.nodes.new('CompositorNodeOutputFile')
                node.directory = str(self.directory)
                node.format.media_type = 'IMAGE'
                node.format.file_format = 'OPEN_EXR'
                node.format.color_mode = 'RGBA'
                node.format.color_depth = '32'
                node.save_as_render = False
                node.file_output_items.new('RGBA', name)
                tree.links.new(render.outputs[name if name != 'Depth' or 'Depth' in render.outputs else 'Z'], node.inputs[name])
                self.nodes[name] = node
            scene.compositing_node_group = tree
            scene.render.use_compositing = True
            scene.render.compositor_precision = 'FULL'
        except BaseException:
            self.close(commit=False)
            raise

    def prepare(self, index):
        self.index = index
        for name, node in self.nodes.items():
            node.file_name = f'{self.prefix}{name}_{index:06d}_'

    def read(self, name, channels=3):
        paths = list(self.directory.glob(f'{self.prefix}{name}_{self.index:06d}_*.exr'))
        if len(paths)!=1:
            raise ValueError(f'Expected one {name} pass, found {len(paths)}')
        image = bpy.data.images.load(str(paths[0]), check_existing=False)
        try:
            image.colorspace_settings.name = 'Non-Color'
            width, height = image.size
            values = np.empty(width*height*4, dtype=np.float32)
            image.pixels.foreach_get(values)
            return values.reshape(height,width,4)[:,:,:channels].copy()
        finally:
            bpy.data.images.remove(image)
            paths[0].unlink()

    def export(self, matrix, use_depth, use_normals, frame, rgba=None):
        distance = self.read('Depth', 1)[:, :, 0]
        position = unproject_depth(distance, matrix, frame)
        valid = np.isfinite(distance) & (distance > 0) & (distance < 1e9) & np.isfinite(position).all(axis=2)
        if rgba is not None:
            from capture_seed import valid_pixels
            valid = valid_pixels(position, distance, rgba)
        result = {'geometry_valid_pixels': int(valid.sum())}
        if self.seed_limit is not None:
            from capture_seed import samples, save_view, view_budget
            points, colors = samples(position, distance, rgba, matrix, frame,
                                     view_budget(self.seed_limit, self.views), self.index, valid)
            save_view(self.output, self.index, points, colors)
            result['initialization_samples'] = len(points)
        if use_depth or use_normals:
            if self.normals:
                _, normal, normal_valid = convert(position, self.read('Normal'), matrix)
                valid &= normal_valid
            else:
                normal = None
            depth = distance.copy()
            depth[~valid] = 0
            if normal is not None:
                normal[~valid] = .5
            if not valid.any():
                raise ValueError('Geometry passes contain no valid visible foreground')
            if self.observations:
                self.observations.write(self.index, position, valid, matrix, frame, rgba)
            stem = f'view_{self.index:06d}.tiff'
            if use_depth:
                write_float_tiff(self.output/'depths'/stem, depth)
            if use_normals:
                write_float_tiff(self.output/'normals'/stem, normal)
            result.update(depth_convention='camera-z', normal_convention='camera-opencv-linear-encoded')
        return result

    def close(self, *, commit=True):
        """Release our compositor and files, preserving every unrelated path."""
        if self.closed:
            return
        self.closed = True
        try:
            if self.observations:
                if commit:
                    self.observations.close()
                else:
                    self.observations.abort()
        finally:
            if self.scene.compositing_node_group == self.tree:
                self.scene.compositing_node_group = self.previous_group
            self.scene.render.use_compositing = self.previous_compositing
            self.scene.render.compositor_precision = self.previous_precision
            self.layer.use_pass_z, self.layer.use_pass_position, self.layer.use_pass_normal = self.previous_passes
            if self.tree is not None and self.tree.name in bpy.data.node_groups:
                bpy.data.node_groups.remove(self.tree)
            for path in self.directory.glob(self.prefix + '*.exr'):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            try:
                if not self.directory_existed:
                    self.directory.rmdir()
            except OSError:
                # The directory may predate this pass or contain files owned by
                # a different render operation. Never remove those files.
                pass
