# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender: whole setup layout stability and idle validation redraws."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys, importlib
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
module='bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
bpy.ops.preferences.addon_enable(module=module)
ext=importlib.import_module(module)
s = bpy.context.scene.lichtfeld_training
with tempfile.TemporaryDirectory() as directory:
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(directory)/'panel-state.blend'))
    for name in ext.LichtFeldPanelState.__annotations__:
        bpy.ops.wm.context_toggle(data_path='window_manager.lichtfeld_panels.'+name)
        assert not bpy.data.is_dirty, name
        bpy.ops.wm.context_toggle(data_path='window_manager.lichtfeld_panels.'+name)
        assert not bpy.data.is_dirty, name
    if not bpy.app.background:
        bpy.ops.wm.context_set_int('EXEC_DEFAULT', True, data_path='scene.lichtfeld_training.iterations',value=s.iterations+1)
        assert bpy.data.is_dirty, 'A real training edit must still require saving'
s.job_directory = ""
s.running = False
bpy.context.window_manager.lichtfeld_panels.validation_details = False
s.scope = "COLLECTION"
bpy.context.window_manager.lichtfeld_panels.setup_output = True
s.camera_collection = bpy.data.collections.new("Stability cameras")
s.camera_collection["lichtfeld_generated"] = True

class Layout:
    def __init__(self, closed=False): self.fields = []; self.rows = []; self.labels = []; self.operations = []; self.closed=closed
    def box(self): return self
    def row(self, **kw): return self
    def split(self, **kw): return self
    def column(self, **kw): return self
    def separator(self, **kw): self.rows.append('separator')
    def panel(self, *args, **kw): self.rows.append('section'); return self,None if self.closed else self
    def panel_prop(self, owner, name):
        self.rows.append('section')
        return self, self if getattr(owner, name) and not self.closed else None
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
    with patch.object(ext.preflight, "cached", return_value=ready), patch.object(ext.preflight, "issues", return_value=errors), patch.object(ext, "preferences", return_value=None), patch.object(ext.preflight, 'environment', return_value={'backend': True, 'ok': True}):
        ext.draw_training(SimpleNamespace(layout=layout), context)
    # Capture checks only appears when there are additional messages to inspect.
    fields = []
    rows = layout.rows.copy()
    for name, index in layout.fields:
        if name == 'validation_details':
            del rows[index]
        else:
            fields.append((name, index))
    orders.append((fields, rows))
assert all(order == orders[0] for order in orders), orders
assert any(name == "iterations" for name, row in orders[0][0])
print("WHOLE_SETUP_LAYOUT_STABLE_PASS", len(orders[0][1]))

# Expert controls stay reachable while their active configuration remains visible.
for advanced in (False, True):
    bpy.context.window_manager.lichtfeld_panels.training_advanced=advanced;s.use_depth_loss=True;s.use_normal_loss=True
    layout=Layout()
    ext.draw_training_controls(layout,s,lambda box,text:box.label(text=text))
    fields=dict(layout.fields)
    assert 'iterations' in fields and 'max_points' in fields
    assert ('strategy' in fields)==advanced and 'sh_degree' in fields
    assert sum(name=='sh_degree' for name,row in layout.fields)==1
    assert ('depth_loss_weight' in fields)==advanced and ('normal_loss_weight' in fields)==advanced
    assert 'Maps: Depth + Normals' in layout.labels
bpy.context.window_manager.lichtfeld_panels.training_advanced=False;s.use_depth_loss=False;s.use_normal_loss=False
print('TRAINING_ADVANCED_CONTROLS_AND_ACTIVE_SUMMARY_PASS')

# Modal Apply/source preparation changes enabled state, not panel geometry.
# The old conditional Preparing scene box shifted every editable field.
rig=s.camera_collection
busy_ready={'checking':False,'reference_changed':True,'cameras':[],'environment':{}}
for source in ('UNLOADED','LOADED','NEW'):
    s.camera_collection=None if source=='NEW' else rig
    s.rig_parameter_source=rig if source=='LOADED' else None
    for closed in (False,True):
        bpy.context.window_manager.lichtfeld_panels.setup_content = bpy.context.window_manager.lichtfeld_panels.setup_cameras = bpy.context.window_manager.lichtfeld_panels.setup_output = bpy.context.window_manager.lichtfeld_panels.setup_training = not closed
        for edit in ((False,True) if source=='LOADED' and not closed else (False,)):
            bpy.context.window_manager.lichtfeld_panels.rig_generator=edit
            busy_orders=[]
            for busy in (False,True,False):
                s.operation_busy=busy
                layout=Layout(closed=closed)
                with patch.object(ext.preflight,'cached',return_value=busy_ready),patch.object(ext.preflight,'issues',return_value=[]),patch.object(ext,'preferences',return_value=None), patch.object(ext.preflight, 'environment', return_value={'backend': True, 'ok': True}):
                    ext.draw_training(SimpleNamespace(layout=layout),context)
                busy_orders.append((layout.fields,layout.rows,layout.operations))
                if busy:
                    assert 'setup_cameras' in dict(layout.fields)
                    if source!='NEW' and not closed:
                        assert 'Updating setup…' in layout.labels
                        assert 'Rig/framing changes need Apply' not in layout.labels
                        assert 'Capture uses current cameras' not in layout.labels
                        assert 'Load selected rig before editing' not in layout.labels
                elif source!='NEW' and not closed:
                    expected='Rig/framing changes need Apply' if source=='LOADED' else 'Load selected rig before editing'
                    assert expected in layout.labels
            assert all(order==busy_orders[0] for order in busy_orders),(source,closed,edit)
s.camera_collection=rig;bpy.context.window_manager.lichtfeld_panels.rig_generator=False
bpy.context.window_manager.lichtfeld_panels.setup_content = bpy.context.window_manager.lichtfeld_panels.setup_cameras = bpy.context.window_manager.lichtfeld_panels.setup_output = bpy.context.window_manager.lichtfeld_panels.setup_training = True
print('MODAL_APPLY_LAYOUT_STABLE_PASS')

s.rig_parameter_source=s.camera_collection
s.camera_collection['lichtfeld_rig_recipe']=ext.rig_recipe(s)
for checking,pending,expected in ((False,True,'Rig/framing changes need Apply'),
    (True,False,'Checking cameras…'),(False,False,'0 capture cameras')):
    ready={'checking':checking,'reference_changed':pending,'cameras':[],'environment':{}}
    layout=Layout()
    with patch.object(ext.preflight,'cached',return_value=ready),patch.object(ext.preflight,'issues',return_value=[]),patch.object(ext,'preferences',return_value=None), patch.object(ext.preflight, 'environment', return_value={'backend': True, 'ok': True}):
        ext.draw_training(SimpleNamespace(layout=layout),context)
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
    with patch.object(ext.preflight,'cached',return_value=ready),patch.object(ext.preflight,'issues',return_value=[]),patch.object(ext,'preferences',return_value=None), patch.object(ext.preflight, 'environment', return_value={'backend': True, 'ok': True}):
        ext.draw_training(SimpleNamespace(layout=layout),context)
    operations=dict(layout.operations)
    assert operator in operations
    assert sum(name==operator for name,row in layout.operations)==1
    if task!='REUSE':
        assert 'Rig reference: Cube' in layout.labels
        assert 'Rig/framing changes need Apply' in layout.labels
        assert dict(layout.fields)['scope']<operations['lichtfeld.capture_framing']<operations[operator]
    assert ('iterations' in dict(layout.fields))==(task!='EXPORT')
    assert ('scope' in dict(layout.fields))==(task!='REUSE')
s.dataset_mode='CAPTURE'
layout=Layout(closed=True)
bpy.context.window_manager.lichtfeld_panels.setup_content = bpy.context.window_manager.lichtfeld_panels.setup_cameras = bpy.context.window_manager.lichtfeld_panels.setup_output = bpy.context.window_manager.lichtfeld_panels.setup_training = False
with patch.object(ext.preflight,'cached',return_value=ready),patch.object(ext.preflight,'issues',return_value=[]),patch.object(ext,'preferences',return_value=None), patch.object(ext.preflight, 'environment', return_value={'backend': True, 'ok': True}):
    ext.draw_training(SimpleNamespace(layout=layout),context)
assert 'lichtfeld.start_training' in dict(layout.operations)
assert 'lichtfeld.capture_framing' not in dict(layout.operations)
assert 'lichtfeld.create_rig' not in dict(layout.operations)
assert 'iterations' not in dict(layout.fields) and 'scope' not in dict(layout.fields)
bpy.context.window_manager.lichtfeld_panels.setup_content = bpy.context.window_manager.lichtfeld_panels.setup_cameras = bpy.context.window_manager.lichtfeld_panels.setup_output = bpy.context.window_manager.lichtfeld_panels.setup_training = True
# Rig actions live in the expanded section, never in its disclosure header.
for loaded,action in ((False,'lichtfeld.read_rig'),(True,'lichtfeld.create_rig')):
    s.rig_parameter_source=s.camera_collection if loaded else None
    layout=Layout()
    with patch.object(ext.preflight,'cached',return_value=ready),patch.object(ext.preflight,'issues',return_value=[]),patch.object(ext,'preferences',return_value=None), patch.object(ext.preflight, 'environment', return_value={'backend': True, 'ok': True}):
        ext.draw_training(SimpleNamespace(layout=layout),context)
    assert sum(name==action for name,row in layout.operations)==1
    assert sum(name=='lichtfeld.capture_framing' for name,row in layout.operations)==1
    if not loaded:assert not any(name=='lichtfeld.create_rig' for name,row in layout.operations)
    else:assert any(name=='lichtfeld.read_rig' for name,row in layout.operations),'Revert must remain accessible'
print('CAPTURE_PLAN_APPLIED_REFERENCE_TASK_ACTIONS_AND_COLLAPSED_SECTIONS_PASS')

# Distance feedback follows native scene units; aiming remains directly available.
bpy.context.window_manager.lichtfeld_panels.rig_generator=True;bpy.context.window_manager.lichtfeld_panels.rig_details=False;s.rig_mode='SPHERE'
s.camera_collection['lichtfeld_generated_radius']=3.25
s.camera_collection['lichtfeld_fit_limited']=True
for system,scale,expected in (('NONE',.01,'3.25'),('METRIC',.01,'3.25 cm'),('IMPERIAL',1,"10.66'")):
    bpy.context.scene.unit_settings.system=system
    bpy.context.scene.unit_settings.scale_length=scale
    layout=Layout()
    ext.draw_capture_cameras(layout,context,s,ready,False,lambda box,text:box.label(text=text))
    assert f'Last distance: {expected}' in layout.labels
    assert 'rig_aim_mode' in dict(layout.fields) and 'rig_aim_offset' not in dict(layout.fields)
    assert any('increased by automatic fitting' in label for label in layout.labels)
s.rig_mode='PATH';s.rig_orientation='TANGENT'
layout=Layout()
ext.draw_capture_cameras(layout,context,s,ready,False,lambda box,text:box.label(text=text))
assert not any(label.startswith('Last distance:') for label in layout.labels)
assert 'rig_aim_mode' not in dict(layout.fields)
print('RIG_DISTANCE_UNITS_AND_DIRECT_AIM_CONTROLS_PASS')

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
# A launch owns a fresh validation even when busy-state polling has discarded
# the display cache. A changed scene must still reject the collected snapshot.
work=pre.collect(bpy.context)
while True:
    try:next(work)
    except StopIteration as stop:
        capture=stop.value
        break
pre.invalidate(bpy.context.scene)
with patch.object(pre,'environment',return_value={'ok':True}), \
     patch.object(ext.scene_render,'readiness',return_value={'ok':True}):
    current=pre.cached(bpy.context,ext.preferences(),capture=capture)
    assert current['cameras']==capture['cameras'] and current['sources']==capture['sources']
    assert not current.get('checking')
    bpy.context.scene.frame_set(bpy.context.scene.frame_current+1)
    assert pre.cached(bpy.context,ext.preferences(),capture=capture)['checking']
print('FRESH_CAPTURE_SURVIVES_DISPLAY_CACHE_INVALIDATION_PASS')

# Every advanced group remains reachable after replacing the old disclosure
# buttons. Preview quality and camera overrides are still job inputs.
s.dataset_mode='CAPTURE';s.mode='NEW';s.camera_collection=rig;s.rig_parameter_source=rig
s.rig_mode='HEMISPHERE';s.full_preview=False
for name in ('rig_generator','rig_details','training_advanced','preview_advanced',
             'capture_advanced','format_presets_open','camera_overrides_open'):
    setattr(bpy.context.window_manager.lichtfeld_panels,name,True)
for name in ('override_optics','override_shift','override_clipping'):
    setattr(s,name,True)
layout=Layout()
with patch.object(ext.preflight,'cached',return_value=ready), \
     patch.object(ext.preflight,'issues',return_value=[]), \
     patch.object(ext.preflight,'environment',return_value={'backend':True}), \
     patch.object(ext,'preferences',return_value=None):
    ext.draw_training(SimpleNamespace(layout=layout),context)
fields=dict(layout.fields)
assert all(name in fields for name in ('rig_min','rig_max','rig_lens','rig_aim_offset',
    'capture_lens','capture_shift_x','capture_clip_start','capture_aspect','seed_points',
    'strategy','eval_interval','optimizer_file','preview_mode','preview_points',
    'preview_sh_degree','preview_hold_seconds'))
assert 'rig_position_variation' in fields and 'rig_rotation_variation' in fields and 'rig_variation_seed' not in fields
assert sum(name=='sh_degree' for name,row in layout.fields)==1
print('NATIVE_DISCLOSURES_KEEP_ADVANCED_CAPTURE_TRAINING_AND_PREVIEW_REACHABLE_PASS')
for mode in ('TARGET','TANGENT'):
    s.rig_mode='PATH';s.rig_orientation=mode
    layout=Layout()
    ext.draw_capture_cameras(layout,context,s,ready,False,lambda *args:None)
    names=dict(layout.fields)
    assert ('rig_path_reverse' in names)==(mode=='TANGENT')
    assert 'rig_path_link' in names
    assert 'rig_position_variation' not in names and 'rig_rotation_variation' not in names
for amount,rotation in ((0,0),(5,0),(0,.03),(5,.03)):
    s.rig_mode='RINGS';s.rig_position_variation=amount;s.rig_rotation_variation=rotation
    layout=Layout()
    ext.draw_capture_cameras(layout,context,s,ready,False,lambda *args:None)
    names=dict(layout.fields)
    assert 'rig_position_variation' in names and 'rig_rotation_variation' in names
    assert ('rig_variation_seed' in names)==bool(amount or rotation)
s.rig_mode='PATH';s.rig_orientation='PANORAMA';s.rig_path_sampling='COUNT';s.views=4;s.rig_station_views=8
layout=Layout()
ext.draw_capture_cameras(layout,context,s,ready,False,lambda *args:None)
names=dict(layout.fields)
assert all(name in names for name in ('rig_station_views','rig_station_sweep','rig_phase'))
assert all(name not in names for name in ('rig_aim_mode','rig_aim_offset','rig_path_reverse','rig_position_variation','rig_rotation_variation'))
assert '4 stations × 8 = 32 cameras' in layout.labels
s.rig_path_sampling='SPACING'
layout=Layout()
ext.draw_capture_cameras(layout,context,s,ready,True,lambda *args:None)
assert 'Station total updates on Apply' in layout.labels
assert not any('= 32' in text for text in layout.labels)
for facing in ('GUIDE','PATH','TARGET'):
    s.rig_station_frame=facing
    layout=Layout()
    ext.draw_capture_cameras(layout,context,s,ready,True,lambda *args:None)
    fields=dict(layout.fields)
    assert 'rig_station_frame' in fields
    assert ('rig_path_reverse' in fields)==(facing=='PATH')
    assert ('rig_aim_mode' in fields)==(facing=='TARGET')
    assert ('rig_aim_offset' in fields)==(facing=='TARGET')
print('PATH_REVERSE_ONLY_SHOWN_FOR_PATH_VIEWING_PASS')
bpy.ops.preferences.addon_disable(module=module)
