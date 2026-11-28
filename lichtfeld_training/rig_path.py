# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Evaluate a single Blender curve without changing the original object."""
import bpy
import math
from mathutils import Matrix, Vector
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty
from .rig_geometry import sample_path, guide_path, station_view_count

_station_overlay = None


def tangent(points, index, cyclic, reverse=False):
    if reverse:
        return points[index-1]-points[index] if index or cyclic else points[index]-points[1]
    if index+1 < len(points):
        return points[index+1]-points[index]
    return points[0]-points[index] if cyclic else points[index]-points[index-1]


def station_frames(points, guide_rotation, cyclic=False, reverse=False):
    """Path-facing frames stay upright relative to the guide, without roll drift."""
    for index in range(len(points)):
        forward = tangent(points, index, cyclic, reverse)
        if forward.length < 1e-8:
            raise ValueError('The path has a zero tangent; adjust the curve or station spacing')
        forward.normalize()
        right = forward.cross(guide_rotation @ Vector((0,0,1)))
        if right.length < 1e-5:
            raise ValueError('The path is vertical relative to the guide; choose Guide axes facing or adjust the curve in Edit Mode')
        right.normalize()
        yield Matrix((right, right.cross(forward), -forward)).transposed().to_quaternion()


def positions(context, curve, count, *, spacing=None, control_points=False):
    if control_points:
        if not curve or curve.type != 'CURVE' or any(s.type != 'POLY' for s in curve.data.splines):
            raise ValueError('Choose a Poly curve containing station points')
        matrix = curve.evaluated_get(context.evaluated_depsgraph_get()).matrix_world
        for spline in curve.data.splines:
            for point in spline.points:
                yield tuple(matrix @ point.co.to_3d())
        return
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
        for point in sample_path(points, count, cyclic, spacing=spacing):
            yield point
    finally:
        if evaluated:
            evaluated.to_mesh_clear()
        bpy.data.objects.remove(temporary, do_unlink=True)
        if not data.users:
            bpy.data.curves.remove(data)


class LF_OT_path_guide(bpy.types.Operator):
    bl_idname = 'lichtfeld.create_path_guide'
    bl_label = 'Create path guide'
    bl_description = 'Create an editable Blender curve; existing guides and cameras stay unchanged until you apply the rig'
    bl_options = {'REGISTER', 'UNDO'}

    shape: EnumProperty(name='Shape', description='Create an editable orbit, rising helix or spherical spiral', items=[
        ('ORBIT', 'Orbit / arc', 'A full closed orbit or an open arc'),
        ('HELIX', 'Helix', 'Constant-radius turns rising by the chosen height'),
        ('SPIRAL', 'Spherical spiral', 'A path between elevation limits, advancing uniformly through spherical area bands; coverage is not guaranteed')])
    center: FloatVectorProperty(name='Center', subtype='TRANSLATION', description='World-space center; initially the 3D cursor position')
    radius: FloatProperty(name='Radius', default=3, min=.01, max=1000000,
        subtype='DISTANCE', unit='LENGTH', description='Distance from the center; the spherical spiral lies on this sphere')
    start: FloatProperty(name='Start angle', subtype='ANGLE', description='Starting azimuth around the world Z axis')
    sweep: FloatProperty(name='Arc angle', default=2*math.pi, min=.01, max=2*math.pi,
        subtype='ANGLE', description='360 degrees creates a closed orbit without a duplicate endpoint')
    turns: FloatProperty(name='Turns', default=3, min=.1, max=10, description='Number of turns around the world Z axis')
    minimum: FloatProperty(name='Minimum elevation', default=0, min=-math.pi/2, max=math.pi/2,
        subtype='ANGLE', description='Starting elevation on the sphere')
    maximum: FloatProperty(name='Maximum elevation', default=math.radians(85), min=-math.pi/2, max=math.pi/2,
        subtype='ANGLE', description='Ending elevation on the sphere; must exceed minimum elevation')
    height: FloatProperty(name='Rise', default=3, subtype='DISTANCE', unit='LENGTH',
        description='Total vertical rise from the center; negative values descend')

    @classmethod
    def poll(cls, context):
        from . import rig_edit
        s = context.scene.lichtfeld_training
        if context.mode != 'OBJECT':
            cls.poll_message_set('Return to Object Mode to create a path guide')
            return False
        if s.running or s.operation_busy or rig_edit.live_root(s):
            cls.poll_message_set('Finish the current capture, training or rig edit before creating a guide')
            return False
        if rig_edit.render_busy(context):
            cls.poll_message_set('Finish Blender\'s render or locked job before creating a guide')
            return False
        return True

    def invoke(self, context, event):
        self.center = context.scene.cursor.location
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        for name in ('shape', 'center', 'radius', 'start'):
            layout.prop(self, name)
        if self.shape == 'ORBIT':
            layout.prop(self, 'sweep')
        else:
            layout.prop(self, 'turns')
            if self.shape == 'HELIX':
                layout.prop(self, 'height')
            else:
                layout.prop(self, 'minimum')
                layout.prop(self, 'maximum')

    def execute(self, context):
        curve = obj = None
        try:
            points, closed = guide_path(self.shape, self.radius, self.start, self.sweep,
                                        self.turns, self.minimum, self.maximum, self.height)
            curve = bpy.data.curves.new('LichtFeld Path Guide', 'CURVE')
            curve.dimensions = '3D'
            spline = curve.splines.new('POLY')
            spline.points.add(len(points)-1)
            for point, co in zip(spline.points, points):
                point.co = (*co, 1)
            spline.use_cyclic_u = closed
            obj = bpy.data.objects.new('LichtFeld Path Guide', curve)
            obj['lichtfeld_path_guide'] = True
            obj.location = self.center
            obj.hide_render = True
            context.scene.collection.objects.link(obj)
            settings = context.scene.lichtfeld_training
            settings.rig_curve = obj
            if settings.rig_path_sampling == 'POINTS':
                settings.rig_path_sampling = 'COUNT'
        except (ValueError, RuntimeError) as error:
            if obj:
                bpy.data.objects.remove(obj, do_unlink=True)
            if curve and not curve.users:
                bpy.data.curves.remove(curve)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Path guide created. Edit the curve in Blender, then apply rig settings to place cameras')
        return {'FINISHED'}


def station_centers(context, settings):
    from .preflight import camera_collections

    space = context.space_data
    guide = settings.rig_curve if settings.rig_mode == 'PATH' and settings.rig_path_sampling == 'POINTS' else None
    show_draft = bool(guide and guide.get('lichtfeld_floor_sampling') and guide.visible_get(viewport=space))
    centers = {}
    if show_draft:
        matrix = guide.matrix_world
        for spline in guide.data.splines:
            if spline.type == 'POLY':
                for point in spline.points:
                    center = matrix @ point.co.to_3d()
                    centers[tuple(center)] = center

    edited = settings.camera_collection
    collections = dict.fromkeys((*camera_collections(settings), edited))
    for collection in collections:
        if collection is None or (show_draft and collection == edited):
            continue
        for camera in collection.all_objects:
            if camera.type != 'CAMERA':
                continue
            center = camera.matrix_world.translation
            key = tuple(center)
            if key not in centers and camera.visible_get(viewport=space):
                centers[key] = center
    return centers.values()


def draw_station_overlay():
    context = bpy.context
    space = context.space_data
    view = context.region_data
    if (context.mode != 'OBJECT' or not view or not space.overlay.show_overlays or
            not space.overlay.show_extras or view.view_perspective == 'CAMERA'):
        return
    settings = context.scene.lichtfeld_training
    centers = station_centers(context, settings)
    if not centers:
        return

    import gpu
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from gpu_extras.batch import batch_for_shader

    region = context.region
    radius = 5 * context.preferences.system.ui_scale
    vertices = []
    for center in centers:
        screen = location_3d_to_region_2d(region, view, center)
        if screen is None:
            continue
        x, y = screen
        if 0 <= x <= region.width and 0 <= y <= region.height:
            vertices.extend(((x-radius,y), (x+radius,y), (x,y-radius), (x,y+radius)))
    if vertices:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        shader.bind()
        shader.uniform_float('color', (1, .65, .15, 1))
        batch_for_shader(shader, 'LINES', {"pos": vertices}).draw(shader)


def register_station_overlay():
    global _station_overlay
    if not bpy.app.background:
        _station_overlay = bpy.types.SpaceView3D.draw_handler_add(draw_station_overlay, (), 'WINDOW', 'POST_PIXEL')


def unregister_station_overlay():
    global _station_overlay
    if _station_overlay is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_station_overlay, 'WINDOW')
        _station_overlay = None


def inside_floor_obstacle(scene, depsgraph, station, up):
    # A solid that starts below the floor can surround the station without
    # crossing the floor-to-station ray. Check for exits on the same mesh.
    hit, _, normal, _, obj, matrix = scene.ray_cast(depsgraph, station, up)
    if not hit or normal.z <= 0 or obj.type != 'MESH' or obj.display_type in {'WIRE', 'BOUNDS'}:
        return False
    try:
        inverse = matrix.inverted()
    except ValueError:
        return False
    found, _, opposite, _ = obj.evaluated_get(depsgraph).ray_cast(
        inverse @ station, inverse.to_3x3() @ -up)
    return found and (inverse.to_3x3().transposed() @ opposite).z < 0


def floor_stations(context, floor, spacing, height, clearance, *, budget=None):
    depsgraph = context.evaluated_depsgraph_get()
    floor = floor.evaluated_get(depsgraph)
    matrix = floor.matrix_world
    inverse = matrix.inverted()
    bounds = [matrix @ Vector(p) for p in floor.bound_box]
    low = [min(p[i] for p in bounds) for i in range(2)]
    high = [max(p[i] for p in bounds) for i in range(3)]
    width, depth = (high[i]-low[i] for i in range(2))
    if width <= 0 or depth <= 0:
        return
    if budget is None:
        columns, rows = math.ceil(width/spacing), math.ceil(depth/spacing)
    else:
        columns = min(budget, max(1, round(math.sqrt(budget * width / depth))))
        rows = budget // columns
    if columns * rows > 1000:
        raise ValueError('Increase spacing or choose a smaller floor area (at most 1,000 samples)')
    up = Vector((0, 0, 1))
    down = inverse.to_3x3() @ -up
    normal_transform = inverse.to_3x3().transposed()
    directions = [Vector((math.cos(i*math.tau/16), math.sin(i*math.tau/16), 0)) for i in range(16)]
    height_margin = min(.02, height/4)
    for i in range(columns):
        for j in range(rows):
            x = low[0] + (i+.5)*(high[0]-low[0])/columns
            y = low[1] + (j+.5)*(high[1]-low[1])/rows
            hit, point, normal, _ = floor.ray_cast(inverse @ Vector((x, y, high[2]+height)), down)
            if not hit or abs((normal_transform @ normal).normalized().z) < .7:
                continue
            base = matrix @ point
            station = base + up*height
            if context.scene.ray_cast(depsgraph, base+up*height_margin, up, distance=height-height_margin)[0]:
                continue
            if clearance and any(context.scene.ray_cast(depsgraph, station, direction, distance=clearance)[0]
                                 for direction in directions):
                continue
            if not inside_floor_obstacle(context.scene, depsgraph, station, up):
                yield station


def spread_floor_stations(candidates, fixed, count):
    """Select well-separated candidates; fixed positions already occupy part of the budget."""
    if count <= 0:
        return {}
    if len(candidates) <= count:
        return candidates
    if not fixed:
        # Start in the middle of the usable area, including on irregular floors.
        centroid = sum(candidates.values(), Vector((0, 0, 0))) / len(candidates)
        first = min(candidates, key=lambda key: (candidates[key] - centroid).length_squared)
        selected = {first: candidates[first]}
        distances = {key: (point - candidates[first]).length_squared
                     for key, point in candidates.items() if key != first}
    else:
        selected = {}
        distances = {key: min((point - anchor).length_squared for anchor in fixed.values())
                     for key, point in candidates.items()}
    while len(selected) < count:
        key = max(distances, key=distances.get)
        point = selected[key] = candidates[key]
        del distances[key]
        for other in distances:
            distances[other] = min(distances[other], (candidates[other] - point).length_squared)
    return selected


def _station_key(position):
    return tuple(round(value, 5) for value in position)


def _stored_points(guide, name):
    values = guide.get(name, ())
    return {_station_key(values[index:index+3]) for index in range(0, len(values), 3)}


class LF_OT_floor_stations(bpy.types.Operator):
    bl_idname = 'lichtfeld.propose_floor_stations'
    bl_label = 'Propose stations from floor'
    bl_description = 'Propose editable panorama stations or update their spacing while keeping manual edits'
    bl_options = {'REGISTER', 'UNDO'}

    sampling: EnumProperty(name='Sampling', items=[
        ('SPACING', 'Spacing', 'Choose the distance between proposed stations'),
        ('COUNT', 'Station count', 'Choose a target number of stations')], options={'SKIP_SAVE'})
    count: IntProperty(name='Stations', default=12, min=1, max=1000, options={'SKIP_SAVE'})
    spacing: FloatProperty(name='Spacing', default=1, min=.05, subtype='DISTANCE')
    height: FloatProperty(name='Height above floor', default=1.45, min=.05, subtype='DISTANCE')
    clearance: FloatProperty(name='Obstacle clearance', default=.30, min=0, subtype='DISTANCE')
    refresh: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return LF_OT_path_guide.poll(context)

    def invoke(self, context, event):
        settings = context.scene.lichtfeld_training
        if self.refresh and settings.rig_curve:
            preset = settings.rig_curve.get('lichtfeld_floor_sampling')
            if preset:
                self.spacing, self.height, self.clearance = preset
                saved_count = settings.rig_curve.get('lichtfeld_floor_station_count', 0)
                if saved_count:
                    self.sampling = 'COUNT'
                    self.count = saved_count
        elif not self.refresh and context.scene.unit_settings.system != 'NONE':
            scale = context.scene.unit_settings.scale_length
            for name in ('spacing', 'height', 'clearance'):
                if not self.properties.is_property_set(name):
                    setattr(self, name, getattr(self, name) / scale)
        return context.window_manager.invoke_props_dialog(self, title="Update floor stations" if self.refresh else self.bl_label)

    def draw(self, context):
        self.layout.use_property_split = True
        self.layout.use_property_decorate = False
        self.layout.prop(self, 'sampling')
        for name in ('count' if self.sampling == 'COUNT' else 'spacing', 'height', 'clearance'):
            self.layout.prop(self, name)

    def execute(self, context):
        from .capture_settings import validate_view_count
        settings = context.scene.lichtfeld_training
        guide = settings.rig_curve if self.refresh else None
        floor = guide.get('lichtfeld_station_floor') if guide else context.active_object
        try:
            if self.refresh and not guide:
                raise ValueError('Choose an indoor station guide to update')
            if not floor or floor.type != 'MESH' or floor.name not in context.scene.objects:
                raise ValueError('The original floor is missing. Select a floor and make a new proposal'
                                 if self.refresh else 'Select a floor mesh in this scene')
            existing, fixed, excluded = {}, {}, set()
            inverse = None
            if guide:
                if any(spline.type != 'POLY' for spline in guide.data.splines):
                    raise ValueError('Indoor station guides must contain only Poly points')
                inverse = guide.evaluated_get(context.evaluated_depsgraph_get()).matrix_world.inverted()
                existing = {_station_key(point.co.to_3d()): point.co.to_3d()
                            for spline in guide.data.splines for point in spline.points}
                generated = _stored_points(guide, 'lichtfeld_generated_stations')
                fixed = {key: point for key, point in existing.items() if key not in generated}
                excluded = _stored_points(guide, 'lichtfeld_excluded_stations') | (generated - existing.keys())
            requested = self.count if self.sampling == 'COUNT' else None
            views = (1 if self.refresh and settings.rig_orientation != 'PANORAMA' else
                     station_view_count(settings.rig_station_views, settings.rig_station_shape, settings.rig_station_rows))
            if requested is not None:
                validate_view_count(max(requested, len(fixed)) * views)
            proposed = {}
            if requested is None or len(fixed) < requested:
                proposed = self.propose(context, floor, inverse, fixed, excluded, requested)
            points = {**proposed, **fixed}
            order = dict.fromkeys((*existing, *points))
            if not points:
                raise ValueError('No usable stations found. Choose a walkable floor surface or adjust the sampling')
            validate_view_count(len(points) * views)
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        curve = bpy.data.curves.new('LichtFeld Indoor Stations', 'CURVE')
        curve.dimensions = '3D'
        for key in order:
            if key in points:
                spline = curve.splines.new('POLY')
                spline.points[0].co = (*points[key], 1)
        if guide:
            previous = guide.data
            guide.data = curve
            if not previous.users:
                bpy.data.curves.remove(previous)
        else:
            guide = bpy.data.objects.new(curve.name, curve)
            guide['lichtfeld_path_guide'] = True
            guide.hide_render = True
            guide.show_in_front = True
            context.scene.collection.objects.link(guide)
        guide['lichtfeld_station_floor'] = floor
        guide['lichtfeld_floor_sampling'] = [self.spacing, self.height, self.clearance]
        if requested is not None:
            guide['lichtfeld_floor_station_count'] = requested
        elif 'lichtfeld_floor_station_count' in guide:
            del guide['lichtfeld_floor_station_count']
        for name, positions in (('lichtfeld_generated_stations', proposed),
                                ('lichtfeld_excluded_stations', sorted(excluded))):
            if positions:
                guide[name] = [value for key in positions for value in key]
            elif name in guide:
                del guide[name]
        if not self.refresh:
            settings.rig_mode = 'PATH'
            settings.rig_curve = guide
            settings.rig_path_sampling = 'POINTS'
            settings.rig_path_link = False
            settings.rig_orientation = 'PANORAMA'
            settings.rig_station_frame = 'GUIDE'
        count = len(points)
        station_label = 'station' if count == 1 else 'stations'
        if requested is not None and count < requested:
            self.report({'WARNING'}, f'Found {count} of {requested} stations in the sampled floor area')
        elif requested is not None and count > requested:
            self.report({'WARNING'}, f'Kept all {count} edited stations. Remove points to reach {requested}')
        else:
            self.report({'INFO'}, f'{count} {station_label} ready. Apply the rig to update cameras')
        return {'FINISHED'}

    def propose(self, context, floor, inverse, fixed, excluded, count):
        def candidates(budget):
            positions = {}
            for world in floor_stations(context, floor, self.spacing, self.height, self.clearance, budget=budget):
                point = inverse @ world if inverse is not None else world
                key = _station_key(point)
                if key not in excluded and key not in fixed:
                    positions[key] = (point, world)
            return positions

        if count is None:
            return {key: point for key, (point, world) in candidates(None).items()}
        needed = count - len(fixed)
        budget = min(1000, max(64, count * 6))
        proposed = candidates(budget)
        if len(proposed) < needed and budget < 1000:
            retry = min(1000, math.ceil(budget * needed / max(len(proposed), 1) * 1.5))
            proposed.update(candidates(retry))
        matrix = inverse.inverted() if inverse is not None else None
        anchors = {key: matrix @ point for key, point in fixed.items()} if matrix is not None else fixed
        selected = spread_floor_stations({key: world for key, (point, world) in proposed.items()}, anchors, needed)
        return {key: proposed[key][0] for key in selected}


CLASSES = (LF_OT_path_guide, LF_OT_floor_stations)
