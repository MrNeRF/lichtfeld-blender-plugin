# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
import bpy,sys
import numpy as np
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
bpy.ops.preferences.addon_enable(module="lichtfeld_training")
import lichtfeld_training as ext
scene=bpy.context.scene;ui=ext.ui_runtime
key=ui.scene_key(scene)
def frame(index,degree):
    arrays={"means":np.full((1000,3),index*.01,dtype=np.float32),"scale":np.zeros((1000,3)),
        "rotation":np.tile(np.array([1,0,0,0],dtype=np.float32),(1000,1)),"opacity":np.zeros((1000,1)),"sh":np.zeros((1000,(degree+1)**2,3))}
    return {"count":1000,"record":{"id":str(index),"kind":"preview","iteration":index},"job":{"id":"buffers","sources":[]},
        "transport":"fixture","data":ext.ui_runtime.blender_fields(arrays)}
ids=[]
for index in range(8):
    for _ in ui.build(scene,frame(index,3),key):pass
    ids.append(scene.lichtfeld_training.result_object.data.as_pointer())
assert len(set(ids))==2,ids
assert len(ui._cloud_buffers)==1
for _ in ui.build(scene,frame(9,0),key):pass
assert not any(a.name.startswith("radiance:sh_") for a in scene.lichtfeld_training.result_object.data.attributes)
print("DOUBLE_BUFFER_REUSE_AND_SH_DOWNGRADE_PASS")
# A history change cancels native RNA work but retains its decoded CPU frame.
from types import SimpleNamespace
import time
snapshot = frame(10, 0)
acks = []
reader = SimpleNamespace(status={'state':'completed','job':snapshot['job']}, external=True, error='',
    take=lambda: (_ for _ in ()).throw(AssertionError('Retained frame was lost')),
    acknowledge=lambda *args, **kwargs: acks.append((args,kwargs)), close=lambda:None)
binding = ui.Binding.__new__(ui.Binding)
binding.key=key; binding.reader=reader; binding.frame=snapshot; binding.max_ms=0; binding.status={}
binding.stage=ui.build(scene,snapshot,key)
for _ in range(3):next(binding.stage)
ui._bindings[key]=binding
ui.before_history()
assert binding.stage is None and binding.frame is snapshot and binding.reader is reader
binding.tick(scene, time.perf_counter()+2)
assert binding.stage is None and binding.frame is None and len(acks)==1
assert acks[0][0] == ('10',) and not acks[0][1].get('error')
assert scene.lichtfeld_training.result_object['lichtfeld_iteration']==10
print('HISTORY_RETRIES_RETAINED_FRAME_WITHOUT_RECONNECT_PASS')
ui.clear()
assert not ui._cloud_buffers
bpy.ops.preferences.addon_disable(module="lichtfeld_training")
