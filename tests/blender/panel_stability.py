# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender: whole setup layout stability and idle validation redraws."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys, importlib
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
module='bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
bpy.ops.preferences.addon_enable(module=module)
ext=importlib.import_module(module)
s = bpy.context.scene.lichtfeld_training
s.job_directory = ""
s.running = False
s.validation_details = False
s.scope = "COLLECTION"
s.camera_collection = bpy.data.collections.new("Stability cameras")
s.camera_collection["lichtfeld_generated"] = True

class Layout:
    def __init__(self): self.fields = []; self.rows = []; self.labels = []
    def box(self): return self
    def row(self, **kw): return self
    def column(self, **kw): return self
    def label(self, **kw): self.rows.append("label"); self.labels.append(kw.get('text',''))
    def prop(self, settings, name, **kw):
        self.fields.append((name, len(self.rows)))
        self.rows.append("prop")
    def operator(self, name, **kw):
        self.rows.append("operator")
        return SimpleNamespace()
context = SimpleNamespace(scene=bpy.context.scene, region=SimpleNamespace(width=240))
orders = []
for checking, errors, changed in ((True, ["Checking source and cameras…"], False),
    (False, [], False), (False, ["Save the latest changes before capture."], True),
    (False, ["Invalid setup " * 20, "Another issue"], True), (False, [], False)):
    s.camera_collection["lichtfeld_rig_recipe"] = "changed" if changed else ext.rig_recipe(s)
    ready = {"checking": checking, "cameras": [], "environment": {"checking": checking}}
    layout = Layout()
    with patch.object(ext.preflight, "cached", return_value=ready), patch.object(ext.preflight, "issues", return_value=errors), patch.object(ext, "preferences", return_value=None):
        ext.LF_PT_training.draw(SimpleNamespace(layout=layout), context)
    orders.append((layout.fields, layout.rows))
assert all(order == orders[0] for order in orders), orders
assert any(name == "iterations" for name, row in orders[0][0])
print("WHOLE_SETUP_LAYOUT_STABLE_PASS", len(orders[0][1]))

s.rig_parameter_source=s.camera_collection
s.camera_collection['lichtfeld_rig_recipe']=ext.rig_recipe(s)
for checking,pending,expected in ((False,True,'Rig/framing changed: apply parameters'),
    (True,False,'Checking rig parameters…'),(False,False,'Capture uses the existing cameras')):
    ready={'checking':checking,'reference_changed':pending,'cameras':[],'environment':{}}
    layout=Layout()
    with patch.object(ext.preflight,'cached',return_value=ready),patch.object(ext.preflight,'issues',return_value=[]),patch.object(ext,'preferences',return_value=None):
        ext.LF_PT_training.draw(SimpleNamespace(layout=layout),context)
    assert expected in layout.labels,(expected,layout.labels)
print('REFERENCE_ONLY_PENDING_STATE_AND_CHECKING_NOTICE_PASS')

pre = ext.preflight
area = SimpleNamespace(type="VIEW_3D", tag_redraw=Mock())
fake_bpy = SimpleNamespace(data=SimpleNamespace(screens=[SimpleNamespace(areas=[area])]))
fake_scene = SimpleNamespace(lichtfeld_training=SimpleNamespace(running=False, operation_busy=False, attached_session="", dataset_mode="CAPTURE"), as_pointer=lambda: 123)
def collect(_):
    yield
    return {"sources": ["subject"], "cameras": ["a", "b", "c", "d"]}
pre.clear()
with patch.object(pre, "bpy", fake_bpy), patch.object(pre, "environment"), patch.object(pre.scene_render, 'tick'), patch.object(pre, "collect", side_effect=collect):
    for _ in range(20):
        pre._next = 0
        pre.tick(SimpleNamespace(scene=fake_scene), None)
assert area.tag_redraw.call_count == 1, area.tag_redraw.call_count
print("UNCHANGED_VALIDATION_NO_REDRAW_PASS")
pre.clear()
bpy.ops.preferences.addon_disable(module=module)
