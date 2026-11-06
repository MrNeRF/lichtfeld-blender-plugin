# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert Blender capture and preview frames through the shared schema."""
from pathlib import Path

from lichtfeld_sessions.geometry import FLIP, IDENTITY, SCHEMA, matrix, validate


def from_capture(frames):
    import numpy as np

    flip = np.asarray(FLIP)
    value = {"schema": SCHEMA, "source_frame": "blender-world", "data_frame": "lichtfeld-data",
             "data_to_source": FLIP, "model_to_data": IDENTITY, "cameras": []}
    for frame in frames:
        value["cameras"].append({"name": Path(frame["file_path"]).name,
            "camera_to_data": (flip @ matrix(frame["transform_matrix"]) @ flip).tolist(),
            "width": frame["w"], "height": frame["h"],
            **{new: frame[old] for new, old in (("fx", "fl_x"), ("fy", "fl_y"), ("cx", "cx"), ("cy", "cy"))}})
    return validate(value)


def source_model_transform(value=None):
    value = value or {}
    return matrix(value.get("data_to_source", FLIP)) @ matrix(value.get("model_to_data", IDENTITY))
