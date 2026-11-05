# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real RNA stress: two scenes, transform stability, cancellation and 1M SH3."""
from pathlib import Path
import sys
import time
import threading
import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import lichtfeld_training as extension
from lichtfeld_training import ui_runtime as ui
from lichtfeld_training.session_reader import blender_fields
from lichtfeld_training.session_geometry import source_model_transform
bpy.ops.preferences.addon_enable(module="lichtfeld_training")


def frame(count, generation):
    result = []
    def prepare():
        arrays = {"means": np.ones((count, 3), dtype=np.float32),
                  "scale": np.full((count, 3), -4, dtype=np.float32),
                  "rotation": np.tile(np.array([1, 0, 0, 0], dtype=np.float32), (count, 1)),
                  "opacity": np.zeros((count, 1), dtype=np.float32),
                  "sh": np.zeros((count, 16, 3), dtype=np.float32)}
        result.append({"count": count, "record": {"id": generation, "iteration": 12},
                       "job": {"id": "same-session", "sources": []},
                       "transport": "test", "data": blender_fields(arrays)})
    worker = threading.Thread(target=prepare)
    worker.start()
    worker.join()  # Fixture setup; interactive code never waits.
    return result[0]


first = bpy.context.scene
second = bpy.data.scenes.new("Second independent scene")
first_key = ui.scene_key(first)
second_key = ui.scene_key(second)
assert first_key != second_key
maximum = 0
for count in (100000, 1000000):
    snapshot = frame(count, str(count))
    for scene in (first, second):
        old = scene.lichtfeld_training.result_object
        if old:
            old.location.x = 42
        work = ui.build(scene, snapshot, ui.scene_key(scene))
        operations = 0
        while True:
            started = time.perf_counter()
            try:
                next(work)
                operations += 1
            except StopIteration:
                break
            maximum = max(maximum, (time.perf_counter() - started) * 1000)
        result = scene.lichtfeld_training.result_object
        assert len(result.data.points) == count
        if old:
            assert result == old
        # Published source poses remain authoritative across live refreshes.
        np.testing.assert_allclose(result.matrix_world,source_model_transform(snapshot.get('geometry')))
        print("STAGED", count, scene.name, operations, flush=True)
    assert first.lichtfeld_training.result_object != second.lichtfeld_training.result_object
    before = len(bpy.data.pointclouds)
    work = ui.build(first, snapshot, first_key)
    next(work)
    next(work)
    work.close()
    assert len(bpy.data.pointclouds) == before
print("STAGING_MULTISCENE_CANCEL_OK MAX_RNA_MS", maximum, flush=True)
bpy.ops.preferences.addon_disable(module="lichtfeld_training")
