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
    def __init__(self, closed=False): self.fields = []; self.rows = []; self.labels = []; self.operations = []; self.closed=closed
    def box(self): return self
    def row(self, **kw): return self
    def column(self, **kw): return self
    def panel(self, *args, **kw): self.rows.append('section'); return self,None if self.closed else self
    def label(self, **kw): self.rows.append("label"); self.labels.append(kw.get('text',''))
    def prop(self, settings, name, **kw):
        self.fields.append((name, len(self.rows)))
        self.rows.append("prop")
    def operator(self, name, **kw):
        self.operations.append((name,len(self.rows)))
        self.rows.append("operator")
        return SimpleNamespace()
context = SimpleNamespace(scene=bpy.context.scene, view_layer=bpy.context.view_layer, region=SimpleNamespace(width=240))
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

reference=s.camera_collection.lichtfeld_rig_reference
reference.mode='OBJECTS';reference.objects.add().object=bpy.data.objects['Cube']
s.rig_reference.mode='SCENE'  # Pending controls must not impersonate applied poses.
ready={'checking':False,'reference_changed':True,'cameras':['a','b','c','d'],'environment':{},
       'format':{'width':576,'height':1024},'render':{'engine':'CYCLES','samples':16,'request':{'backend':'CPU','mode':'CPU'}}}
for task,operator in (('CAPTURE','lichtfeld.start_training'),('EXPORT','lichtfeld.export_dataset'),('REUSE','lichtfeld.restart_training')):
    s.dataset_mode=task
    layout=Layout()
    with patch.object(ext.preflight,'cached',return_value=ready),patch.object(ext.preflight,'issues',return_value=[]),patch.object(ext,'preferences',return_value=None):
        ext.LF_PT_training.draw(SimpleNamespace(layout=layout),context)
    operations=dict(layout.operations)
    assert operator in operations and operations[operator]<dict(layout.fields).get('iterations',float('inf'))
    if task!='REUSE':
        assert 'Rig: Cube' in layout.labels and '4 views · 576 × 1024 px' in layout.labels
        assert 'Rig changes need Apply' in layout.labels
        assert operations['lichtfeld.capture_framing']<dict(layout.fields)['scope']
    assert ('iterations' in dict(layout.fields))==(task!='EXPORT')
    assert ('scope' in dict(layout.fields))==(task!='REUSE')
s.dataset_mode='CAPTURE'
layout=Layout(closed=True)
with patch.object(ext.preflight,'cached',return_value=ready),patch.object(ext.preflight,'issues',return_value=[]),patch.object(ext,'preferences',return_value=None):
    ext.LF_PT_training.draw(SimpleNamespace(layout=layout),context)
assert 'lichtfeld.start_training' in dict(layout.operations) and 'lichtfeld.capture_framing' in dict(layout.operations)
assert 'lichtfeld.create_rig' in dict(layout.operations)
assert 'iterations' not in dict(layout.fields) and 'scope' not in dict(layout.fields)
print('CAPTURE_PLAN_APPLIED_REFERENCE_TASK_ACTIONS_AND_COLLAPSED_SECTIONS_PASS')

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
