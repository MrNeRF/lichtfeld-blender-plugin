# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Evaluate a single Blender curve without changing the original object."""
import bpy
import math
from bpy.props import EnumProperty, FloatProperty, FloatVectorProperty
from .rig_geometry import sample_path, guide_path


def positions(context, curve, count, *, spacing=None):
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
            context.scene.lichtfeld_training.rig_curve = obj
        except (ValueError, RuntimeError) as error:
            if obj:
                bpy.data.objects.remove(obj, do_unlink=True)
            if curve and not curve.users:
                bpy.data.curves.remove(curve)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Path guide created. Edit the curve in Blender, then apply rig settings to place cameras')
        return {'FINISHED'}


CLASSES = (LF_OT_path_guide,)
