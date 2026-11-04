# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Qualify all supported SH degrees and stable-object preview replacement."""

from pathlib import Path
import sys
import tempfile
import bpy
import numpy as np
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import lichtfeld_training as extension
from lichtfeld_training.bridge_core import atomic_json, write_splat
from lichtfeld_training.session_transport import SnapshotPool

bpy.ops.preferences.addon_enable(module="lichtfeld_training")
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    atomic_json(root / "job.json", {"schema": 1, "id": "adapter", "sources": []})
    settings = bpy.context.scene.lichtfeld_training
    settings.job_directory = directory
    selected = list(bpy.context.selected_objects)
    result = None
    pool = SnapshotPool(root / "buffers")
    for degree in range(4):
        sh = np.arange(2 * (degree + 1) ** 2 * 3, dtype=np.float32).reshape(2, -1, 3) / 100
        arrays = {"means": np.array([[1, 2, 3], [-1, 4, 2]], dtype=np.float32),
                  "scale": np.array([[-1, -2, -3], [-2, -3, -4]], dtype=np.float32),
                  "rotation": np.array([[1, 0, 0, 0], [0.9238795, 0.3826834, 0, 0]], dtype=np.float32),
                  "opacity": np.array([[0], [1]], dtype=np.float32), "sh": sh}
        record = write_splat(root, arrays, degree + 1, "best", {"psnr": 30})
        if result:
            expected = result.matrix_world.copy()
        extension.import_splat(bpy.context.scene, root, record)
        current = next(obj for obj in bpy.context.scene.objects if obj.get("lichtfeld_job_id") == "adapter")
        assert current.type == "POINTCLOUD" and current.data.type == "GAUSSIAN_SPLAT"
        if result:
            assert current == result
            np.testing.assert_allclose(current.matrix_world, expected, atol=1e-6)
        data = current.data
        np.testing.assert_allclose(data.attributes["position"].data[0].vector, arrays["means"][0], atol=1e-6)
        np.testing.assert_allclose(data.attributes["scale"].data[0].vector, np.exp(arrays["scale"][0]), atol=1e-6)
        np.testing.assert_allclose(data.attributes["radiance:base"].data[0].vector[:3], sh[0, 0], atol=1e-6)
        np.testing.assert_allclose(data.attributes["radiance:base"].data[0].vector[3], 0.5, atol=1e-6)
        for index in range((degree + 1) ** 2 - 1):
            np.testing.assert_allclose(data.attributes[f"radiance:sh_{index}"].data[0].vector,
                                       sh[0, index + 1], atol=1e-6)
        assert list(bpy.context.selected_objects) == selected
        shared = pool.write(arrays, degree + 1)
        assert extension.import_splat(bpy.context.scene, root, {**record, "shared": shared})
        assert current == next(obj for obj in bpy.context.scene.objects if obj.get("lichtfeld_job_id") == "adapter")
        for index in range((degree + 1) ** 2 - 1):
            np.testing.assert_allclose(current.data.attributes[f"radiance:sh_{index}"].data[0].vector,
                                       sh[0, index + 1], atol=1e-6)
        np.testing.assert_allclose(current.data.attributes["radiance:base"].data[0].vector,
                                   [*sh[0, 0], 0.5], atol=1e-6)
        np.testing.assert_allclose(current.data.attributes["scale"].data[0].vector,
                                   np.exp(arrays["scale"][0]), atol=1e-6)
        np.testing.assert_allclose(current.data.attributes["rotation"].data[0].value,
                                   arrays["rotation"][0], atol=1e-6)
        result = current
        result.matrix_world = Matrix.Translation((5, 0, 0)) @ result.matrix_world
        print("ADAPTER_SH_VERIFIED", degree, flush=True)
    pool.close()
print("STABLE_OBJECT_AND_SH0_TO_SH3_OK", flush=True)
