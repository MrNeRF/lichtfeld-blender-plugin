# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fresh training on a saved capture through real Blender operators, no .blend save."""
import bpy,sys,time,json,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
bpy.ops.preferences.addon_enable(module="lichtfeld_training")
import lichtfeld_training as ext
args=sys.argv[sys.argv.index("--")+1:]
parser=argparse.ArgumentParser()
for name in ("original","backend","area"):parser.add_argument(name,type=Path)
parser.add_argument("--points",type=int,default=10000)
parser.add_argument("--steps",type=int,default=2000)
parser.add_argument("--limited-sh",type=int,choices=range(4))
parser.add_argument("--check-controls", action="store_true")
parser.add_argument("--depth", action="store_true")
parser.add_argument("--expected-center", nargs=3, type=float)
options=parser.parse_args(args)
original,backend,area=(path.resolve() for path in (options.original,options.backend,options.area))
area.mkdir(parents=True,exist_ok=True)
prefs=ext.preferences();prefs.backend_path=str(backend);prefs.jobs_directory=str(original.parent)
s=bpy.context.scene.lichtfeld_training
s.dataset_job_directory=str(original);s.iterations=options.steps;s.eval_interval=max(100,options.steps//4);s.max_points=options.points;s.sh_degree=3
s.preview_mode="LIVE";s.full_preview=options.limited_sh is None;s.preview_points=1000
if options.limited_sh is not None:
    s.preview_sh_degree=options.limited_sh
    s.eval_interval=options.steps
s.use_depth_loss=options.depth

assert not bpy.data.filepath
assert bpy.ops.lichtfeld.restart_training()=={"FINISHED"}
deadline=time.monotonic()+600
job=None;frames={};live_records={};max_ms=0
import_ticks=0;control_checks=0
while time.monotonic()<deadline:
    if options.check_controls:
        current=ext.ui_runtime.peek(bpy.context.scene)
        if current:
            # Model a busy UI frame whose RNA budget has already been spent.
            # The next real snapshot remains staged while controls are exercised.
            current.tick(bpy.context.scene,deadline=time.perf_counter())
        if current and current.status.get('state')=='training' and s.result_object:
            assert ext.allowed(bpy.context,'compare'),'Snapshot staging blocked source comparison'
            if current.status.get('best'):
                assert ext.allowed(bpy.context,'import_best'),'Snapshot staging blocked best selection'
            control_checks+=1
            if s.importing:
                import_ticks+=1
                assert bpy.ops.lichtfeld.model_view(mode='CURRENT')=={'FINISHED'}
    ext.ui_runtime.tick()
    if s.job_directory:
        job=Path(s.job_directory)
        runtime=ext.read_json(job/"runtime.json",{})
        preview=runtime.get("preview")
        if preview:live_records[preview["id"]]=preview
        if s.result_object:
            obj=s.result_object
            frames[obj.get("lichtfeld_artifact")]=(obj.get("lichtfeld_iteration"),obj.get("lichtfeld_count"),obj.data.as_pointer())
        state=ext.read_json(job/"state.json",{})
        if state.get("state")=="failed":raise AssertionError(state)
        if state.get("state")=="completed" and s.result_object and s.result_object.get("lichtfeld_artifact")==state["result"]["id"] and not s.importing:
            break
    time.sleep(.01)
else:raise AssertionError("Reuse training timed out")
meta=ext.read_json(job/"job.json")
assert meta["reuse_dataset"] and not meta.get("resume")
assert Path(meta["dataset_root"])==original/"dataset"
assert not (job/"render.log").exists() and not (job/"capture.blend").exists()
assert not (job/"dataset").exists()
assert len(live_records)>=3, len(live_records)
if options.limited_sh is None:
    assert all(record["count"]>s.preview_points for record in live_records.values()),"Full preview was capped"
    expected_active_degree = 1 if options.steps >= 1000 else 0
    assert max(record["sh_degree"] for record in live_records.values())>=expected_active_degree
else:
    assert all(record['count']<=s.preview_points and record['sh_degree']<=options.limited_sh for record in live_records.values())
    assert s.result_object.get('lichtfeld_count')>s.preview_points
    assert s.result_object.get('lichtfeld_sh_degree')==state['result']['sh_degree']
    if options.limited_sh==0 and options.steps>=2000:assert s.result_object.get('lichtfeld_sh_degree')>0
    print('LIMITED_PREVIEW_FULL_SAVED_RESULT_PASS',options.limited_sh,flush=True)
summary={"job":str(job),"preview_generations":len(live_records),"displayed_generations":len(frames),"ui_max_ms":s.ui_max_ms,
         "preview_intervals":[record["created"] for record in live_records.values()],"result_step":state["result"]["iteration"]}
if options.check_controls:
    assert import_ticks>0 and control_checks>0,(import_ticks,control_checks)
    summary.update(import_ticks=import_ticks,control_checks=control_checks)
if options.expected_center:
    import numpy as np
    obj=s.result_object;values=np.empty(len(obj.data.points)*3,np.float32)
    obj.data.attributes['position'].data.foreach_get('vector',values)
    matrix=np.asarray(obj.matrix_world)
    center=values.reshape(-1,3).mean(axis=0)@matrix[:3,:3].T+matrix[:3,3]
    assert np.linalg.norm(center-options.expected_center)<5,(center,options.expected_center)
    summary.update(displayed_world_center=center.tolist())
(area/"result.json").write_text(json.dumps(summary),encoding="utf-8")
print("FRESH_DATASET_WITHOUT_RENDER_PASS", {k:v for k,v in summary.items() if k != "preview_intervals"}, flush=True)
bpy.ops.wm.save_as_mainfile(filepath=str(area/"reuse-result.blend"))
readers=[binding.reader for binding in ext.ui_runtime._bindings.values()]
ext.ui_runtime.clear()
for reader in readers:reader.thread.join(5)
