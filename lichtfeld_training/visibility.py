# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Visibility operations are restricted to objects with a base in that layer."""
def set_hidden(obj, hidden, layer, *, members=None):
    if obj is None or (layer.objects if members is None else members).get(obj.name) != obj:
        return False
    try:
        if obj.hide_get(view_layer=layer) != bool(hidden):
            obj.hide_set(hidden, view_layer=layer)
    except RuntimeError as error:
        if "View Layer" not in str(error) or "cannot be hidden" not in str(error):
            raise
        return False
    return True


def is_hidden(obj, layer, *, members=None):
    return obj.hide_get(view_layer=layer) if (layer.objects if members is None else members).get(obj.name) == obj else False


_baselines = {}


def source_steps(scene, job, show_splat, *, restore_only=False):
    """Hide renderable bases including collection instancers, in bounded slices."""
    layer = scene.view_layers.get(job.get("view_layer", "")) or scene.view_layers[0]
    key = (scene.as_pointer(), job.get("id", ""))
    if restore_only and not _baselines.get(key):
        return  # A capture without a displayed model never changed visibility.
    baseline = _baselines.setdefault(key, {})
    names = set(baseline) if restore_only else set(job.get("visibility_sources", job.get("sources", [])))
    entire_scene = job.get("scope") == "SCENE" and not restore_only
    # ViewLayer RNA name lookups may scan its bases. Build one bounded index
    # instead of repeating that scan for every source in a large scene.
    members = {}
    for obj in layer.objects:
        if obj is not None:
            members[obj.name] = obj
        yield
    captured = job.get("source_visibility", {})
    for obj in scene.objects:
        renderable = obj.type in {"MESH", "CURVE", "SURFACE", "FONT", "META", "VOLUME", "POINTCLOUD"} or (obj.type == "EMPTY" and obj.instance_type == "COLLECTION")
        if not obj.get("lichtfeld_result") and (obj.name in names or entire_scene and renderable):
            if members.get(obj.name) == obj:
                if obj.name not in baseline:
                    baseline[obj.name] = captured[obj.name] if obj.name in captured else is_hidden(obj, layer, members=members)
                set_hidden(obj, show_splat or baseline[obj.name], layer, members=members)
        yield


def clear():
    _baselines.clear()



def forget(scene, job):
    _baselines.pop((scene.as_pointer(), job.get("id", "")), None)
