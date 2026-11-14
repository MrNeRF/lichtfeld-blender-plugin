# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exit with a busy Portal worker and verify its owned child does not survive."""
import importlib
from pathlib import Path
import subprocess
import time
import bpy

NAME = "bl_ext.user_default.lichtfeld_training"
bpy.ops.preferences.addon_enable(module=NAME)
runtime_module = importlib.import_module(NAME + ".portal_runtime")
runtime = runtime_module.Runtime(Path(bpy.utils.user_resource("CONFIG")) / "shutdown-test")
runtime._run = lambda *args: time.sleep(15)
runtime.start("refresh")
python = Path(bpy.app.binary_path).parent / "5.3/python/bin/python.exe"
runtime.process = subprocess.Popen([str(python), "-c", "import time; time.sleep(30)"])
print("PORTAL_SHUTDOWN_CHILD", runtime.process.pid, flush=True)
print("PORTAL_SHUTDOWN_STARTED", time.monotonic(), flush=True)
bpy.ops.wm.quit_blender()
