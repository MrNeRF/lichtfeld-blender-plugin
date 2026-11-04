# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender event-loop heartbeat while the modal operator creates 1000 cameras."""
from pathlib import Path
import json
import sys
import time
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import lichtfeld_training as extension
bpy.ops.preferences.addon_enable(module="lichtfeld_training")
output = Path(sys.argv[sys.argv.index("--") + 1])
state = {"ticks": 0, "max_gap_ms": 0}


def start():
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.scene.lichtfeld_training.views = 1000
    assert bpy.ops.lichtfeld.create_rig() == {"RUNNING_MODAL"}
    state["started"] = state["last"] = time.perf_counter()
    bpy.app.timers.register(heartbeat, first_interval=.01)


def heartbeat():
    now = time.perf_counter()
    state["ticks"] += 1
    state["max_gap_ms"] = max(state["max_gap_ms"], (now - state["last"]) * 1000)
    state["last"] = now
    settings = bpy.context.scene.lichtfeld_training
    if not settings.operation_busy:
        state["cameras"] = sum(o.type == "CAMERA" for o in settings.camera_collection.objects)
        assert state["cameras"] == 1000 and state["ticks"] > 5, state
        state["elapsed"] = now - state["started"]
        output.write_text(json.dumps(state))
        print("RIG_EVENT_LOOP_HEARTBEAT_OK", state, flush=True)
        bpy.ops.wm.quit_blender()
        return None
    assert now - state["started"] < 60, state
    return .01


bpy.app.timers.register(start, first_interval=1)
