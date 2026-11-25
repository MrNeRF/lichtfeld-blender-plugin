# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Classification of rendered surfaces in an oriented capture volume."""

import numpy as np


def local_transform(shape, world_to_unit):
    if shape not in {"BOX", "ELLIPSOID"}:
        raise ValueError("Unknown capture region shape")
    transform = np.asarray(world_to_unit, dtype=np.float64)
    if (transform.shape != (4, 4) or not np.isfinite(transform).all()
            or not np.array_equal(transform[3], [0, 0, 0, 1])
            or np.linalg.slogdet(transform[:3, :3])[0] == 0):
        raise ValueError("Capture region needs finite, nonzero dimensions")
    return transform.astype(np.float32)


def region_spec(context, obj):
    """Freeze the evaluated volume using the Empty's displayed bounds."""
    from mathutils import Matrix

    if context.scene.objects.get(obj.name) != obj:
        raise ValueError("Choose a Capture region Empty from this scene")
    evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
    shape = {"CUBE": "BOX", "SPHERE": "ELLIPSOID"}.get(evaluated.empty_display_type)
    if evaluated.type != 'EMPTY' or shape is None:
        raise ValueError("Capture region requires a Cube or Sphere Empty")
    world_from_unit = evaluated.matrix_world @ Matrix.Scale(evaluated.empty_display_size, 4)
    try:
        world_to_unit = [list(row) for row in world_from_unit.inverted()]
    except ValueError as exc:
        raise ValueError("Capture region needs finite, nonzero dimensions") from exc
    local_transform(shape, world_to_unit)
    return {"shape": shape, "world_to_unit": world_to_unit}


def render_layers(layer):
    if layer.exclude or layer.collection.hide_render:
        return
    yield layer
    for child in layer.children:
        yield from render_layers(child)


def uses_viewport_switch(tree, seen):
    if tree is None or tree in seen:
        return False
    seen.add(tree)
    return any(node.bl_idname == "GeometryNodeIsViewport"
               or uses_viewport_switch(getattr(node, "node_tree", None), seen)
               for node in tree.nodes)


def animated_render_visibility(obj, fields):
    from bpy_extras.anim_utils import action_get_channelbag_for_slot

    animation = obj.animation_data
    if animation is None:
        return False
    if any(curve.data_path in fields for curve in animation.drivers):
        return True
    actions = [(animation.action, animation.action_slot)]
    for track in animation.nla_tracks:
        if not track.mute:
            actions.extend((strip.action, strip.action_slot) for strip in track.strips if strip.type == "CLIP")
    for action, slot in actions:
        if action is None or slot is None:
            continue
        bag = action_get_channelbag_for_slot(action, slot)
        if bag is not None and any(curve.data_path in fields for curve in bag.fcurves):
            return True
    return False


_NON_SURFACE_TYPES = {"CAMERA", "LIGHT", "EMPTY", "ARMATURE", "LATTICE", "LIGHT_PROBE", "SPEAKER"}
_CONVERTIBLE_TYPES = {"MESH", "CURVE", "SURFACE", "FONT", "META"}


def check_instance_asset(collection, parent):
    """Require matching instance visibility before trusting viewport bounds."""
    if collection.hide_viewport != collection.hide_render:
        raise ValueError(f"Instance {parent.name} has different viewport and render visibility in {collection.name}.")
    if collection.hide_render:
        return
    for obj in collection.objects:
        if obj.hide_viewport != obj.hide_render:
            raise ValueError(f"Instance {parent.name} has different viewport and render visibility on {obj.name}.")
        if not obj.hide_render and obj.instance_type != "NONE":
            raise ValueError(f"Instance {parent.name} contains nested instances. Realize it before capture.")
    for child in collection.children:
        check_instance_asset(child, parent)


def eevee_indirect_material(material):
    """Keep the surface shader for shadow and probe passes."""
    import bpy

    if material is not None and material.node_tree is None:
        material = None
    if material and any(node.type == "OUTPUT_MATERIAL" and node.target != "CYCLES"
                        and node.inputs["Volume"].is_linked for node in material.node_tree.nodes):
        # EEVEE volume shading is performed for the camera, separately from the surface.
        return None
    exterior = material.copy() if material else bpy.data.materials.new("Capture region · Indirect")
    nodes = exterior.node_tree.nodes
    links = exterior.node_tree.links
    camera = nodes.new("ShaderNodeLightPath")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    for output in tuple(nodes):
        if output.type != "OUTPUT_MATERIAL" or output.target == "CYCLES":
            continue
        surface = output.inputs["Surface"]
        if not surface.is_linked:
            links.new(transparent.outputs[0], surface)
            continue
        original = surface.links[0].from_socket
        mix = nodes.new("ShaderNodeMixShader")
        links.new(camera.outputs["Is Camera Ray"], mix.inputs[0])
        links.new(original, mix.inputs[1])
        links.new(transparent.outputs[0], mix.inputs[2])
        links.new(mix.outputs[0], surface)
    return exterior


def clip_capture_scene(context, shape, transform, sources):
    """Clip evaluated surfaces in the disposable capture scene."""
    import bmesh
    import bpy
    from idprop.types import IDPropertyArray
    from mathutils import Matrix, Vector

    cycles = context.scene.render.engine == "CYCLES"
    eevee = context.scene.render.engine == "BLENDER_EEVEE"
    preserve_lighting = cycles or eevee
    indirect_materials = {}

    def hide_eevee_exterior(exterior):
        materials = [slot.material for slot in exterior.material_slots] or [None]
        for material in materials:
            if material not in indirect_materials:
                indirect_materials[material] = eevee_indirect_material(material)
        if any(indirect_materials[material] is None for material in materials):
            bpy.data.objects.remove(exterior)
            return False
        if not exterior.material_slots:
            if exterior.data.users > 1:
                exterior.data = exterior.data.copy()
            exterior.data.materials.append(None)
        for slot, material in zip(exterior.material_slots, materials, strict=True):
            slot.link = "OBJECT"
            slot.material = indirect_materials[material]
        return True

    planes = [(Vector(axis), 1.0) for axis in
              ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]
    if shape == "ELLIPSOID":
        # An inscribed convex polyhedron keeps every retained point inside the sphere.
        hull = bmesh.new()
        try:
            bmesh.ops.create_icosphere(hull, subdivisions=3, radius=1.0)
            hull.normal_update()
            planes.extend((face.normal.copy(), face.normal.dot(face.verts[0].co)) for face in hull.faces)
        finally:
            hull.free()

    layers = tuple(render_layers(context.view_layer.layer_collection))
    collections = tuple(dict.fromkeys(layer.collection for layer in layers))
    objects = tuple(dict.fromkeys(obj for collection in collections for obj in collection.objects
                                  if not obj.hide_render))
    viewport_settings = []
    hidden_objects = []

    def for_evaluation(owner, field, value):
        original = getattr(owner, field)
        if value != original:
            if not owner.id_data.is_editable or owner.is_property_readonly(field):
                raise ValueError(f'Capture region cannot adjust {field} on linked data {owner.id_data.name}.')
            setattr(owner, field, value)
            viewport_settings.append((owner, field, original))

    # Check geometry with render-dependent bounds before using a viewport bbox.
    for obj in objects:
        if obj.instance_type == "COLLECTION" and obj.instance_collection:
            check_instance_asset(obj.instance_collection, obj)
        for modifier in obj.modifiers:
            if not modifier.show_render:
                continue
            if modifier.type == "NODES" and uses_viewport_switch(modifier.node_group, set()):
                raise ValueError(f"Capture region cannot clip {obj.name} using Geometry Nodes Is Viewport.")
            if modifier.type == "PARTICLE_SYSTEM":
                raise ValueError(f"Capture region cannot clip particle systems on {obj.name}. Realize them before capture.")

    for layer in layers:
        for_evaluation(layer, 'hide_viewport', False)
    for collection in collections:
        for_evaluation(collection, "hide_viewport", False)
    for obj in objects:
        for_evaluation(obj, "hide_viewport", False)
        if obj.hide_get(view_layer=context.view_layer):
            obj.hide_set(False, view_layer=context.view_layer)
            hidden_objects.append(obj)
        if obj.type in {"CURVE", "SURFACE", "FONT"}:
            for axis in ("u", "v"):
                resolution = getattr(obj.data, f"render_resolution_{axis}")
                if resolution:
                    for_evaluation(obj.data, f"resolution_{axis}", resolution)
        if obj.type == "META":
            for_evaluation(obj.data, "resolution", obj.data.render_resolution)
        for modifier in obj.modifiers:
            for_evaluation(modifier, "show_viewport", modifier.show_render)
            if not modifier.show_render:
                continue
            if modifier.type in {"SUBSURF", "MULTIRES"}:
                for_evaluation(modifier, "levels", modifier.render_levels)
            if modifier.type == "SCREW":
                for_evaluation(modifier, "steps", modifier.render_steps)
            if modifier.type == "OCEAN":
                for_evaluation(modifier, "viewport_resolution", modifier.resolution)
    render = context.scene.render
    if render.use_simplify:
        for_evaluation(render, "simplify_subdivision", render.simplify_subdivision_render)
    context.view_layer.update()
    graph = context.evaluated_depsgraph_get()
    evaluated_components = {}
    for obj in objects:
        if not any(modifier.type == "NODES" and modifier.show_render for modifier in obj.modifiers):
            continue
        geometry = obj.evaluated_get(graph).evaluated_geometry()
        if geometry is None:
            continue
        if geometry.instance_references():
            raise ValueError(f"Capture region requires realized geometry on {obj.name}.")
        # Mesh conversion does not preserve these GeometrySet components.
        evaluated_components[obj] = [label for field, label in (
            ("curves", "curves"), ("pointcloud", "points"), ("volume", "volume"),
            ("grease_pencil", "Grease Pencil"),
        ) if getattr(geometry, field) is not None]
    region_matrix = Matrix(transform.tolist())

    def crossing_bounds(evaluated, to_region):
        corners = [to_region @ Vector(point) for point in evaluated.bound_box]
        crossing = []
        for normal, limit in planes:
            distances = [normal.dot(point) - limit for point in corners]
            if min(distances) > 0:
                return None
            if max(distances) > 0:
                crossing.append((normal, limit))
        return crossing

    # A direct collection instance can be kept or hidden as a whole. Mixing
    # inside and outside children would require realizing the collection.
    replacements = {}
    exteriors = {}
    instance_bounds = {}
    render_objects = set(objects)
    for entry in graph.object_instances:
        if not entry.is_instance or not entry.parent or entry.parent.original not in render_objects:
            continue
        parent = entry.parent.original
        instance = entry.object
        if instance.type in _NON_SURFACE_TYPES:
            continue
        # Blender exposes evaluated components as self instances. Real Geometry
        # Nodes instances were checked on the evaluated geometry above.
        if (instance.original == parent and parent.instance_type == "NONE"
                and (parent in evaluated_components or parent.type in {"CURVE", "SURFACE", "FONT", "META"})):
            continue
        if parent.type != "EMPTY" or parent.instance_type != "COLLECTION":
            raise ValueError(f"Capture region requires realized geometry on {parent.name}.")
        if instance.hide_render:
            continue
        if instance.type not in (_CONVERTIBLE_TYPES - {"META"}) or instance.modifiers:
            raise ValueError(f"Capture region requires realized geometry on {parent.name}.")
        bounds = crossing_bounds(instance, region_matrix @ entry.matrix_world)
        if bounds:
            raise ValueError(f"Instance {parent.name} crosses the capture region. Realize it before capture.")
        instance_bounds.setdefault(parent, set()).add(bounds is None)
    for parent, outside in instance_bounds.items():
        if len(outside) != 1:
            raise ValueError(f"Instance {parent.name} spans the capture region. Realize it before capture.")
        if True in outside:
            replacements[parent] = None

    def keep_outside(obj):
        replacements[obj] = None
        if not eevee or obj.type not in (_CONVERTIBLE_TYPES - {"META"}):
            return
        # A live node graph can assign materials independently of the object slots.
        if obj.particle_systems or any(modifier.type == "NODES" and modifier.show_render
                                       for modifier in obj.modifiers):
            return
        evaluated = obj.evaluated_get(graph)
        source_materials = {slot.material for slot in obj.material_slots}
        if any(slot.material not in source_materials for slot in evaluated.material_slots):
            return
        exterior = obj.copy()
        exterior.name = f"{obj.name} · Indirect lighting"
        if hide_eevee_exterior(exterior):
            exteriors[obj] = exterior

    for obj in objects:
        if obj.type in _NON_SURFACE_TYPES:
            continue
        evaluated = obj.evaluated_get(graph)
        world = evaluated.matrix_world.copy()
        to_region = region_matrix @ world
        crossing_planes = crossing_bounds(evaluated, to_region)
        if crossing_planes is None:
            keep_outside(obj)
        if not crossing_planes:
            continue

        if unsupported := evaluated_components.get(obj):
            components = ", ".join(unsupported)
            raise ValueError(f"Capture region cannot preserve {components} generated on {obj.name}. "
                             "Convert the evaluated output to a mesh before capture.")
        if obj.type not in _CONVERTIBLE_TYPES:
            raise ValueError(f"Capture region cannot clip {obj.name} ({obj.type}). Convert it to a mesh first.")
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=graph)
        if hasattr(evaluated.data, "texspace_size"):
            # Freeze the original bounds used by Generated coordinates before
            # changing the vertex buffer on the clipped mesh.
            mesh.use_auto_texspace = False
            mesh.texspace_location = evaluated.data.texspace_location
            mesh.texspace_size = evaluated.data.texspace_size
        surface = bmesh.new()
        exterior_mesh = None
        try:
            surface.from_mesh(mesh)
            if preserve_lighting:
                bmesh.ops.delete(surface, geom=[vertex for vertex in surface.verts if not vertex.link_faces],
                                 context="VERTS")
            marker = surface.faces.layers.int.new("LichtFeld capture interior") if preserve_lighting else None
            inside = list(surface.faces) if preserve_lighting else None
            stable_inside = []
            if inside and shape == "ELLIPSOID":
                # Faces in the hull's inscribed sphere cannot meet any cutting plane.
                inner_radius = min(limit for _, limit in planes) - 0.000001
                inner_vertices = {vertex for vertex in surface.verts
                                  if (to_region @ vertex.co).length_squared <= inner_radius * inner_radius}
                stable_inside = [face for face in inside if all(vertex in inner_vertices for vertex in face.verts)]
                stable = set(stable_inside)
                inside = [face for face in inside if face not in stable]
            for normal, limit in crossing_planes:
                if not surface.faces or inside is not None and not inside:
                    break
                local_normal = to_region.to_3x3().transposed() @ normal
                if local_normal.length_squared == 0:
                    continue
                local_limit = limit - normal.dot(to_region.translation)
                if inside is None:
                    geometry = [*surface.verts, *surface.edges, *surface.faces]
                else:
                    # Only faces straddling the plane need a split. Faces fully outside
                    # stay intact and never participate in subsequent cuts.
                    epsilon = local_normal.length * 0.000001
                    vertices = {vertex for face in inside for vertex in face.verts}
                    distances = {vertex: local_normal.dot(vertex.co) - local_limit for vertex in vertices}
                    retained = []
                    crossing = []
                    for face in inside:
                        sides = [distances[vertex] for vertex in face.verts]
                        if max(sides) <= epsilon:
                            retained.append(face)
                        elif min(sides) < -epsilon:
                            crossing.append(face)
                    inside = retained
                    if not crossing:
                        continue
                    geometry = list(dict.fromkeys(element for face in crossing
                                                  for element in (*face.verts, *face.edges, face)))
                result = bmesh.ops.bisect_plane(
                    surface, geom=geometry,
                    dist=0.000001, plane_co=local_normal * (local_limit / local_normal.length_squared),
                    plane_no=local_normal.normalized(), clear_outer=not preserve_lighting,
                )
                if inside is not None:
                    faces = (element for element in result["geom"] if isinstance(element, bmesh.types.BMFace))
                    inside.extend(face for face in dict.fromkeys((*crossing, *faces)) if face.is_valid
                                  and local_normal.dot(face.calc_center_median()) <= local_limit + epsilon)
            if inside is not None:
                inside = [face for face in (*stable_inside, *inside) if face.is_valid]
                if not inside:
                    bpy.data.meshes.remove(mesh)
                    keep_outside(obj)
                    continue
                # Mark the interior for the copy, then remove the marker from both outputs.
                for face in inside:
                    face[marker] = 1
                exterior = surface.copy()
                try:
                    exterior_marker = exterior.faces.layers.int[marker.name]
                    bmesh.ops.delete(exterior, geom=[face for face in exterior.faces if face[exterior_marker]],
                                     context="FACES")
                    exterior.faces.layers.int.remove(exterior_marker)
                    if exterior.faces:
                        exterior_mesh = mesh.copy()
                        exterior.to_mesh(exterior_mesh)
                finally:
                    exterior.free()
                bmesh.ops.delete(surface, geom=[face for face in surface.faces if not face[marker]],
                                 context="FACES")
                surface.faces.layers.int.remove(marker)
            has_faces = bool(surface.faces)
            if has_faces:
                surface.to_mesh(mesh)
        finally:
            surface.free()
        if not has_faces:
            bpy.data.meshes.remove(mesh)
            replacements[obj] = None
            continue
        clipped = bpy.data.objects.new(f"{obj.name} · Capture region", mesh)
        clipped.color = obj.color
        for key, value in obj.items():
            if isinstance(value, (bool, int, float, str)):
                clipped[key] = value
            elif isinstance(value, IDPropertyArray):
                clipped[key] = value.to_list()
        for index, slot in enumerate(obj.material_slots):
            if slot.link == "OBJECT" and index < len(mesh.materials) and mesh.materials[index] != slot.material:
                mesh.materials[index] = slot.material
        for field in ("visible_camera", "visible_diffuse", "visible_glossy", "visible_transmission",
                      "visible_volume_scatter", "visible_shadow", "is_holdout", "is_shadow_catcher",
                      "pass_index", "shadow_terminator_offset", "shadow_terminator_geometry_offset",
                      "is_caustics_caster", "is_caustics_receiver", "hide_probe_volume",
                      "hide_probe_sphere", "hide_probe_plane"):
            if not hasattr(obj, field) or not hasattr(clipped, field):
                continue
            value = getattr(obj, field)
            if getattr(clipped, field) != value:
                setattr(clipped, field, value)
        clipped.matrix_world = world
        replacements[obj] = clipped
        if exterior_mesh is not None:
            for index, material in enumerate(mesh.materials):
                if exterior_mesh.materials[index] != material:
                    exterior_mesh.materials[index] = material
            exterior = clipped.copy()
            exterior.data = exterior_mesh
            exterior.name = f"{obj.name} · Indirect lighting"
            if cycles:
                exterior.visible_camera = False
            if cycles or hide_eevee_exterior(exterior):
                exteriors[obj] = exterior
            else:
                bpy.data.meshes.remove(exterior_mesh)

    # Restore viewport settings before rendering so the evaluated view layer
    # does not keep render-resolution modifiers and hidden meshes alive.
    for obj in hidden_objects:
        obj.hide_set(True, view_layer=context.view_layer)
    for owner, field, value in reversed(viewport_settings):
        setattr(owner, field, value)

    # Keep the source IDs linked for depsgraph dependencies (rigs and constraints).
    visibility_fields = {"hide_render", "visible_camera"} if cycles else {"hide_render"}
    if blocked := next((obj for obj in replacements if animated_render_visibility(obj, visibility_fields)), None):
        raise ValueError(f"Capture region cannot override animated render visibility on {blocked.name}.")
    for obj, clipped in replacements.items():
        field = "visible_camera" if cycles and clipped is None else "hide_render"
        if not obj.is_editable or obj.is_property_readonly(field):
            raise ValueError(f"Capture region cannot cut or hide linked object {obj.name}. Make it local first.")
        if (clipped is not None or obj in exteriors) and any(not collection.is_editable for collection in obj.users_collection):
            raise ValueError(f"Capture region cannot replace {obj.name} in a linked collection. Make the collection local first.")
    for obj, clipped in replacements.items():
        if clipped is None and cycles:
            obj.visible_camera = False
            continue
        obj.hide_render = True
        if clipped is not None:
            name = obj.name
            obj.name = f"{name} · Capture source"
            clipped.name = name
        pieces = tuple(piece for piece in (clipped, exteriors.get(obj)) if piece is not None)
        if not pieces:
            continue
        for piece in pieces:
            if obj.lightgroup:
                piece.lightgroup = obj.lightgroup
            for field in ("receiver_collection", "blocker_collection"):
                collection = getattr(obj.light_linking, field)
                if collection is not None:
                    setattr(piece.light_linking, field, collection)
        for collection in obj.users_collection:
            source_link = collection.collection_objects[collection.objects.find(obj.name)]
            state = source_link.light_linking.link_state
            for piece in pieces:
                collection.objects.link(piece)
                target_link = collection.collection_objects[collection.objects.find(piece.name)]
                if target_link.light_linking.link_state != state:
                    target_link.light_linking.link_state = state
    context.view_layer.update()
    return [result for obj in sources if (result := replacements.get(obj, obj)) is not None]
