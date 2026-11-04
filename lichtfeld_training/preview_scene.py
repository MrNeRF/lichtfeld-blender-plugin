# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated, display-encoded compositing. No changes to the source scene's color pipeline.

Native Gaussian radiance decodes each splat before blending, whereas 3DGS blends
encoded RGB. An emission material evaluates the unmodified SH in encoded space;
Raw display prevents a second transfer. This fixes compositing, not differences
between Blender's rasterizer and the trainer's rasterizer.
"""
import bpy

SOURCE = 'lichtfeld_color_source'
RESULT = 'lichtfeld_color_result'
_return_shading = {}


def material_spec(degree):
    """Pure node instructions, applied incrementally by material_steps."""
    spec = []
    def node(kind, inputs=(), **properties):
        spec.append((kind, properties, inputs))
        return (len(spec)-1, 0)
    def math(op,a,b=0):return node('ShaderNodeMath', [(0,a),(1,b)], operation=op)
    def mul(a,b):return math('MULTIPLY',a,b)
    def sub(a,b):return math('SUBTRACT',a,b)
    def scale(vector,value):return node('ShaderNodeVectorMath',[(0,vector),(3,value)],operation='SCALE')
    def vector_add(a,b):return node('ShaderNodeVectorMath',[(0,a),(1,b)],operation='ADD')
    base=node('ShaderNodeAttribute',attribute_name='radiance:base')
    color=vector_add(scale((base[0],'Vector'),.28209479177387814),[.5,.5,.5])
    if degree:
        geometry=node('ShaderNodeNewGeometry')
        direction=node('ShaderNodeVectorTransform',[('Vector',(geometry[0],'Incoming'))],vector_type='VECTOR',convert_from='WORLD',convert_to='OBJECT')
        direction=scale(direction,-1)
        direction=node('ShaderNodeVectorMath',[(0,direction)],operation='NORMALIZE')
        xyz=node('ShaderNodeSeparateXYZ',[(0,direction)])
        x,y,z=((xyz[0],i) for i in range(3))
        xx,yy,zz=mul(x,x),mul(y,y),mul(z,z)
        basis=[mul(-.4886025119029199,y),mul(.4886025119029199,z),mul(-.4886025119029199,x)]
        if degree>=2:
            basis += [mul(1.0925484305920792,mul(x,y)),mul(-1.0925484305920792,mul(y,z)),
                mul(.31539156525252005,sub(mul(3,zz),1)),mul(-1.0925484305920792,mul(x,z)),
                mul(.5462742152960396,sub(xx,yy))]
        if degree>=3:
            basis += [mul(-.5900435899266435,mul(y,sub(mul(3,xx),yy))),
                mul(2.890611442640554,mul(mul(x,y),z)),
                mul(-.4570457994644658,mul(y,sub(mul(5,zz),1))),
                mul(.3731763325901154,mul(z,sub(mul(5,zz),3))),
                mul(-.4570457994644658,mul(x,sub(mul(5,zz),1))),
                mul(1.445305721320277,mul(z,sub(xx,yy))),
                mul(-.5900435899266435,mul(x,sub(xx,mul(3,yy))))]
        tail = []
        if degree == 3:
            packed = [node('ShaderNodeAttribute', attribute_name=f'lichtfeld_rgb_tail_{i}') for i in range(3)]
            xyzs = [node('ShaderNodeSeparateXYZ', [(0, (item[0], 'Vector'))]) for item in packed]
            def combine(x,y,z):return node('ShaderNodeCombineXYZ', [(0,x),(1,y),(2,z)])
            tail = [(packed[0][0], 'Vector'),
                combine((packed[0][0], 'Alpha'), (xyzs[1][0], 'X'), (xyzs[1][0], 'Y')),
                combine((xyzs[1][0], 'Z'), (packed[1][0], 'Alpha'), (xyzs[2][0], 'X')),
                combine((xyzs[2][0], 'Y'), (xyzs[2][0], 'Z'), (packed[2][0], 'Alpha'))]
        for i,value in enumerate(basis):
            if i >= 11 and degree == 3:
                coefficient = tail[i-11]
            else:
                attribute=node('ShaderNodeAttribute',attribute_name=f'radiance:sh_{i}')
                coefficient=(attribute[0], 'Vector')
            color=vector_add(color,scale(coefficient,value))
        # All sixteen coefficients contribute; no material attribute is dropped.
    color=node('ShaderNodeVectorMath',[(0,color),(1,[0,0,0])],operation='MAXIMUM')
    emission=node('ShaderNodeEmission',[(0,color),(1,1)])
    node('ShaderNodeOutputMaterial',[(0,emission)])
    return spec


def material_steps(degree):
    for index,material in enumerate(bpy.data.materials):
        if material.get('lichtfeld_encoded_material') == 1 and material.get('lichtfeld_sh_degree') == degree:
            return material
        if index % 32 == 31:yield
    material=bpy.data.materials.new(f'LichtFeld trainer colors SH{degree}')
    complete=False
    try:
        material.use_nodes=True
        material.node_tree.nodes.clear()
        nodes=[]
        yield
        for kind,properties,inputs in material_spec(degree):
            node=material.node_tree.nodes.new(kind)
            for name,value in properties.items():setattr(node,name,value)
            for socket,value in inputs:
                target=node.inputs[socket]
                if isinstance(value,tuple):
                    material.node_tree.links.new(nodes[value[0]].outputs[value[1]],target)
                elif target.type=='RGBA' and isinstance(value,list):target.default_value=(*value,1)
                else:target.default_value=value
            nodes.append(node)
            yield
        material['lichtfeld_encoded_material']=1
        material['lichtfeld_sh_degree']=degree
        complete=True
        return material
    finally:
        if not complete and material.users==0:bpy.data.materials.remove(material)


def source_scene(scene):
    source=scene.get(SOURCE)
    return source if isinstance(source,bpy.types.Scene) and source.name in bpy.data.scenes else None


def is_preview(scene):
    """Recognize retained color views even after Blender clears a deleted source pointer."""
    try:
        return isinstance(scene, bpy.types.Scene) and SOURCE in scene
    except ReferenceError:
        return False


def draw_orphaned(layout, context, wrapped):
    box=layout.box()
    box.label(text='Trainer color view · source scene removed',icon='ERROR')
    wrapped(box,'The original scene was deleted. This isolated view and its splat are preserved. Use the scene selector below to switch to another scene.')
    if context.window:
        box.label(text='Switch to a scene')
        box.template_ID(context.window,'scene')


def degree_for(cloud):
    count=sum(1 for a in cloud.attributes if a.name.startswith('radiance:sh_'))
    return {0:0,3:1,8:2,15:3}.get(count,0)


def ensure_tail_steps(cloud):
    """Upgrade saved pre-packing SH3 results without numerical work on the UI."""
    if degree_for(cloud) != 3:return
    names=[f'lichtfeld_rgb_tail_{i}' for i in range(3)]
    if all(cloud.attributes.get(name) and cloud.attributes[name].data_type=='FLOAT4' for name in names):return
    if not cloud.is_editable:raise ValueError('Make the result local before opening the trainer color view')
    import numpy as np
    from . import ui_runtime
    values=[]
    for i in range(11,15):
        array=np.empty(len(cloud.points)*3,dtype=np.float32)
        cloud.attributes[f'radiance:sh_{i}'].data.foreach_get('vector',array)
        values.append(array.reshape(-1,3))
        yield
    future=ui_runtime._executor.submit(lambda: np.stack(values,axis=1).reshape(-1,3,4))
    while not future.done():yield
    packed=future.result()
    for i,name in enumerate(names):
        attribute=cloud.attributes.get(name)
        if attribute and attribute.data_type!='FLOAT4':raise ValueError('A custom attribute conflicts with the reserved trainer color attributes')
        if attribute is None:attribute=cloud.attributes.new(name,'FLOAT4','POINT')
        yield
        # Packing and contiguous conversion stay on the numerical worker.
        future=ui_runtime._executor.submit(lambda i=i: np.ascontiguousarray(packed[:,i]).ravel())
        while not future.done():yield
        attribute.data.foreach_set('vector',future.result())
        yield
    cloud.update_tag()
    yield


def scene_result(scene):
    clone=scene.get(RESULT)
    return clone if isinstance(clone,bpy.types.Object) and clone.name in scene.objects else None


def rebind_data(source):
    """Swap complete data IDs together with the main result, before yielding."""
    result=source.lichtfeld_training.result_object
    if not result:return
    for scene in bpy.data.scenes:
        if source_scene(scene)!=source:continue
        clone=scene_result(scene)
        if clone:
            if clone.data!=result.data:clone.data=result.data
            clone.matrix_world=result.matrix_world.copy()


def sync_steps(source):
    """Update only a second Object, sharing the current complete PointCloud ID."""
    result=source.lichtfeld_training.result_object
    for scene in list(bpy.data.scenes):
        if source_scene(scene)!=source:continue
        clone=scene_result(scene)
        if not result:
            if clone:bpy.data.objects.remove(clone,do_unlink=True)
            yield
            continue
        yield from ensure_tail_steps(result.data)
        material=yield from material_steps(degree_for(result.data))
        if clone is None:
            clone=bpy.data.objects.new('LichtFeld trainer color result',result.data)
            clone[RESULT]=True
            clone.hide_select=True
            clone.lock_location=(True,True,True)
            clone.lock_rotation=(True,True,True)
            clone.lock_scale=(True,True,True)
            scene.collection.objects.link(clone)
            scene[RESULT]=clone
            yield
        elif clone.data!=result.data:
            clone.data=result.data
            yield
        clone.matrix_world=result.matrix_world.copy()
        if not clone.data.materials:
            clone.data.materials.append(None)
            yield
        clone.material_slots[0].link='OBJECT'
        clone.material_slots[0].material=material
        clone.hide_render=False
        yield


def display_curve_steps(source, target):
    """Copy bounded curve controls before enabling the target display override."""
    target.use_curve_mapping=False
    if not source.use_curve_mapping:return
    if target.curve_mapping is None:
        target.use_curve_mapping=True
        target.use_curve_mapping=False
    original,destination=source.curve_mapping,target.curve_mapping
    for prop in original.bl_rna.properties:
        if not prop.is_readonly and prop.type in {'FLOAT','INT','BOOLEAN','ENUM'}:
            value=getattr(original,prop.identifier)
            setattr(destination,prop.identifier,tuple(value) if getattr(prop,'is_array',False) else value)
            yield
    for before,after in zip(original.curves,destination.curves):
        while len(after.points)>2:
            after.points.remove(after.points[1])
            yield
        after.points[0].location=before.points[0].location[:]
        after.points[-1].location=before.points[-1].location[:]
        after.points[0].handle_type=before.points[0].handle_type
        after.points[-1].handle_type=before.points[-1].handle_type
        yield
        for point in list(before.points)[1:-1]:
            copy=after.points.new(*point.location)
            copy.handle_type=point.handle_type
            yield
        if hasattr(before,'extend'):after.extend=before.extend
    destination.update()
    yield


def display_steps(source, scene, source_transform):
    """Decode after compositing, then optionally apply the source's display look."""
    effect=next((e for e in scene.compositor_effects if e.node_group and e.node_group.get('lichtfeld_rgb_decode')),None)
    if source_transform and effect is None:
        graph=bpy.data.node_groups.new('LichtFeld encoded RGB decode','CompositorNodeTree')
        complete=False
        try:
            graph.interface.new_socket(name='Image',in_out='INPUT',socket_type='NodeSocketColor')
            graph.interface.new_socket(name='Image',in_out='OUTPUT',socket_type='NodeSocketColor')
            yield
            incoming=graph.nodes.new('NodeGroupInput')
            outgoing=graph.nodes.new('NodeGroupOutput')
            yield
            convert=graph.nodes.new('CompositorNodeConvertColorSpace')
            convert.from_color_space='sRGB'
            convert.to_color_space='scene_linear'
            graph.links.new(incoming.outputs['Image'],convert.inputs['Image'])
            graph.links.new(convert.outputs['Image'],outgoing.inputs['Image'])
            graph['lichtfeld_rgb_decode']=True
            yield
            effect=scene.compositor_effects.new('LichtFeld RGB decode')
            effect.node_group=graph
            effect.enable_for_render=False
            effect.enable_for_preview=False
            complete=True
        finally:
            if not complete and graph.users==0:bpy.data.node_groups.remove(graph)
    view=scene.view_settings
    if source_transform:
        yield from display_curve_steps(source.view_settings,view)
        scene.display_settings.display_device=source.display_settings.display_device
    else:
        scene.display_settings.display_device=scene.get('lichtfeld_rgb_display_device',scene.display_settings.display_device)
    view.view_transform=source.view_settings.view_transform if source_transform else 'Raw'
    view.look=source.view_settings.look if source_transform else 'None'
    view.exposure=source.view_settings.exposure if source_transform else 0
    view.gamma=source.view_settings.gamma if source_transform else 1
    view.use_curve_mapping=bool(source_transform and source.view_settings.use_curve_mapping)
    view.use_white_balance=bool(source_transform and source.view_settings.use_white_balance)
    if source_transform:
        view.white_balance_temperature=source.view_settings.white_balance_temperature
        view.white_balance_tint=source.view_settings.white_balance_tint
    if effect:
        effect.enable_for_render=source_transform
        effect.enable_for_preview=source_transform
    scene['lichtfeld_source_transform']=source_transform
    yield


def open_steps(source, window, area):
    if not source.lichtfeld_training.result_object:raise ValueError('Wait for a complete training preview first')
    scene=next((s for s in bpy.data.scenes if source_scene(s)==source),None)
    if scene is None:
        scene=bpy.data.scenes.new('LichtFeld trainer color view')
        scene[SOURCE]=source
        scene['lichtfeld_rgb_display_device']=scene.display_settings.display_device
        scene.render.engine='BLENDER_EEVEE'
        scene.eevee.taa_samples=64
        scene.view_settings.view_transform='Raw'
        scene.view_settings.look='None'
        scene.view_settings.exposure=0
        scene.view_settings.gamma=1
        scene.render.film_transparent=False
        world=bpy.data.worlds.new('LichtFeld trainer black background')
        world.color=(0,0,0)
        scene.world=world
        yield
    yield from sync_steps(source)
    # Do not steal the window back if the user changed scenes during preparation.
    if window.scene != source:return
    # Camera view needs an evaluated, independent camera in the isolated scene.
    if source.camera:
        with bpy.context.temp_override(window=window,scene=source):
            evaluated=source.camera.evaluated_get(bpy.context.evaluated_depsgraph_get())
            data=evaluated.data.copy()
            data.animation_data_clear()
            data.dof.use_dof=False
            matrix=evaluated.matrix_world.copy()
        camera=scene.camera
        if camera and camera.get('lichtfeld_color_camera'):
            old=camera.data
            camera.data=data
            if old.users==0:bpy.data.cameras.remove(old)
        else:
            camera=bpy.data.objects.new('LichtFeld preview camera',data)
            camera['lichtfeld_color_camera']=True
            scene.collection.objects.link(camera)
        camera.matrix_world=matrix
        scene.camera=camera
        for name in ('resolution_x','resolution_y','resolution_percentage','pixel_aspect_x','pixel_aspect_y'):
            setattr(scene.render,name,getattr(source.render,name))
        yield
    # Switching the scene preserves the navigable viewport's orbit, zoom and pan.
    if area and area.type=='VIEW_3D':
        space=area.spaces.active
        _return_shading[(window.as_pointer(), area.as_pointer())] = (space.shading.type,space.shading.use_compositor)
        scene['lichtfeld_return_compositor']=space.shading.use_compositor
        scene['lichtfeld_return_shading']=space.shading.type
        space.shading.type='RENDERED'
        space.shading.use_compositor='ALWAYS'
    window.scene=scene
    yield


def close_view(context):
    scene=context.scene
    source=source_scene(scene)
    if not source:raise ValueError('The source scene no longer exists')
    context.window.scene=source
    if context.area and context.area.type=='VIEW_3D':
        shading=context.area.spaces.active.shading
        shading.type,shading.use_compositor=_return_shading.pop((context.window.as_pointer(),context.area.as_pointer()),(scene.get('lichtfeld_return_shading','MATERIAL'),scene.get('lichtfeld_return_compositor','ALWAYS')))
