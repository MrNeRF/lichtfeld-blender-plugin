# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import sys,queue,importlib
from types import SimpleNamespace
from unittest.mock import patch
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
module='bl_ext.user_default.lichtfeld_training' if '--installed' in sys.argv else 'lichtfeld_training'
if module not in bpy.context.preferences.addons:bpy.ops.preferences.addon_enable(module=module)
ext=importlib.import_module(module)
s=bpy.context.scene.lichtfeld_training
cloud=bpy.data.pointclouds.new('Visible complete snapshot');result=bpy.data.objects.new('Result',cloud);bpy.context.scene.collection.objects.link(result)
s.result_object=result;s.running=True;s.status='Training';s.job_directory='/fixture/job'
status={'state':'training','control_available':True,'iteration':100,'iterations':1000,'best':{'iteration':80},'job':{'id':'fixture'}}
binding=SimpleNamespace(status=status,reader=SimpleNamespace(error='',status_error=False,commands=queue.Queue(),external=False))
class Layout:
    def __init__(self,records=None,parent=None):
        self.records=[] if records is None else records
        self.parent=parent
        self.enabled=True
        self.operator_context=parent.operator_context if parent else 'EXEC_DEFAULT'
    def child(self):return Layout(self.records,self)
    def row(self,**kw):return self.child()
    def split(self,**kw):return self.child()
    def column(self,**kw):return self.child()
    def box(self):return self.child()
    def panel(self,*args,**kw):return self.child(),self.child()
    def panel_prop(self,owner,name):return self.child(),self.child() if getattr(owner,name) else None
    def label(self,**kw):self.records.append(('label',kw.get('text'),True))
    def progress(self,**kw):pass
    def separator(self,**kw):pass
    def prop(self,*args,**kw):pass
    def operator(self,name,**kw):
        if name=='lichtfeld.camera_report':
            assert self.operator_context=='INVOKE_DEFAULT', 'Sidebar must invoke the camera popup'
        item=self;enabled=True
        while item:enabled=enabled and item.enabled;item=item.parent
        self.records.append((name,kw.get('text'),enabled))
        return SimpleNamespace()
context=SimpleNamespace(scene=bpy.context.scene,region=SimpleNamespace(width=320),preferences=bpy.context.preferences)
records=[]
with patch.object(ext.ui_runtime,'peek',return_value=binding),patch.object(ext.ui_runtime,'binding',return_value=binding):
    for importing in (False,True,False,True):
        s.importing=importing;layout=Layout()
        ext.draw_training(SimpleNamespace(layout=layout),context)
        records.append(layout.records)
        assert ext.allowed(bpy.context,'compare') and ext.allowed(bpy.context,'import_best')
        assert bpy.ops.lichtfeld.import_result()=={'FINISHED'}
    assert all(r==records[0] for r in records),records
    assert all(enabled for name,text,enabled in records[0] if name in {'lichtfeld.model_view','lichtfeld.compare_result','lichtfeld.trainer_color_view'})
    binding.reader.external=True;s.attached_session='/external'
    assert not ext.allowed(bpy.context,'pause') and not ext.allowed(bpy.context,'stop')
    s.attached_session='';s.running=False;s.status='Failed';status.update(state='failed',dataset_ready=False)
    layout=Layout();ext.draw_training(SimpleNamespace(layout=layout),context)
    assert next(enabled for name,text,enabled in layout.records if name=='lichtfeld.use_dataset') is False
    assert next(enabled for name,text,enabled in layout.records if name=='lichtfeld.new_capture') is True
    bpy.context.window_manager.lichtfeld_panels.job_details=True
    result['lichtfeld_iteration']=80
    status.update(state='completed',iteration=100,iterations=100,
                  error='Training failure',preview_error='Preview failure',
                  job={'iterations':100,'cameras':['a','b'],
                       'capture_format':{'width':640,'height':480}})
    binding.reader.error='Monitoring failure';binding.reader.status_error=True
    layout=Layout();ext.draw_training(SimpleNamespace(layout=layout),context)
    labels=[text for name,text,enabled in layout.records if name=='label']
    assert 'Reached step 100' in labels and any(text.startswith('Shown: step 80') for text in labels)
    assert all(text in labels for text in ('Job needs attention','Training failure',
        'Preview update needs attention','Preview failure','Monitoring needs attention','Monitoring failure'))
    assert '640 × 480 px' in labels and 'Training budget: 100 steps' in labels
    del status['iteration']
    layout=Layout();ext.draw_training(SimpleNamespace(layout=layout),context)
    assert not any(name=='label' and text.startswith('Reached step') for name,text,enabled in layout.records)
    status.pop('error');status.pop('preview_error')
    binding.reader.status_error=False
    binding.reader.error='Snapshot changed'
    s.running=True
    layout=Layout();ext.draw_training(SimpleNamespace(layout=layout),context)
    labels=[text for name,text,enabled in layout.records if name=='label']
    assert 'Preview update needs attention' in labels and 'Snapshot changed' in labels
    assert 'Monitoring needs attention' not in labels
    assert 'Preview updates retry automatically.' in labels
    status['job']={'id':'session:fixture','sources':[]}
    layout=Layout();ext.draw_training(SimpleNamespace(layout=layout),context)
    assert not any(name=='label' and text=='Settings used for this job' for name,text,enabled in layout.records)
print('SNAPSHOT_STAGING_NEVER_TOGGLES_CONTROLS_AND_READONLY_PRESERVED_PASS',flush=True)
s.job_directory='';s.attached_session='';ext.ui_runtime.clear()

# Camera summaries must not hide unrelated job-folder or installation errors.
s.running=False;s.mode='NEW';s.dataset_mode='CAPTURE'
panels=bpy.context.window_manager.lichtfeld_panels
panels.setup_content=False;panels.setup_cameras=False
panels.setup_output=False;panels.setup_training=False;panels.validation_details=True
camera_error='Camera Unsupported: capture requires a Perspective camera'
s.camera_collection=bpy.data.collections.new('Camera checks fixture')
bpy.context.scene.collection.children.link(s.camera_collection)
for backend in (True,False):
    installation={'backend':backend,'ok':False,'message':'Job folder is unavailable' if backend else 'Choose your installation'}
    ready={'errors':[camera_error],'camera_issues':{camera_error:'Unsupported'},'environment':installation}
    layout=Layout()
    with patch.object(ext.ui_runtime,'peek',return_value=None),patch.object(ext.preflight,'environment',return_value=installation),patch.object(ext.preflight,'cached',return_value=ready):
        ext.draw_training(SimpleNamespace(layout=layout),context)
    labels=' '.join(text or '' for name,text,enabled in layout.records if name=='label')
    assert ('lichtfeld.camera_report', 'Check cameras…', True) in layout.records
    assert '1 camera issue' in labels and 'Poses not checked yet' in labels
    assert installation['message'] in labels
    assert camera_error not in labels
    assert sum(name=='lichtfeld.camera_report' for name,text,enabled in layout.records)==1
    assert not any(name=='lichtfeld.inspect_capture_camera' for name,text,enabled in layout.records)
    if not backend:assert all(message in labels for message in ext.preflight.save_issues())

installation = {'backend': True, 'ok': True}
ready = {'errors': [], 'camera_issues': {}, 'environment': installation}
layout = Layout()
with patch.object(ext.ui_runtime, 'peek', return_value=None), patch.object(ext.preflight, 'environment', return_value=installation), patch.object(ext.preflight, 'cached', return_value=ready), patch.object(ext.rig_analysis, 'last_result', return_value={'repeats': [('Capture 001', 'Repeated view')]}):
    ext.draw_training(SimpleNamespace(layout=layout), context)
assert ('lichtfeld.camera_report', 'Check cameras…', True) in layout.records
assert ('label', '1 repeated pose (last check)', True) in layout.records
assert not any(name == 'label' and 'camera issue' in (label or '') for name, label, _ in layout.records)
print('CAMERA_SUMMARY_PRESERVES_ENVIRONMENT_AND_SAVE_ERRORS_PASS',flush=True)
