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
binding=SimpleNamespace(status=status,reader=SimpleNamespace(error='',commands=queue.Queue(),external=False))
class Layout:
    def __init__(self,records=None,parent=None):self.records=[] if records is None else records;self.parent=parent;self.enabled=True
    def child(self):return Layout(self.records,self)
    def row(self,**kw):return self.child()
    def column(self,**kw):return self.child()
    def box(self):return self.child()
    def label(self,**kw):pass
    def progress(self,**kw):pass
    def prop(self,*args,**kw):pass
    def operator(self,name,**kw):
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
print('SNAPSHOT_STAGING_NEVER_TOGGLES_CONTROLS_AND_READONLY_PRESERVED_PASS',flush=True)
s.job_directory='';s.attached_session='';ext.ui_runtime.clear()
