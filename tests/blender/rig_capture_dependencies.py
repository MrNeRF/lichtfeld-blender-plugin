# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Export evaluated camera poses after moving an external rig target."""
import bpy,sys,json,subprocess
from pathlib import Path
from mathutils import Vector
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root));bpy.ops.preferences.addon_enable(module="lichtfeld_training")
import lichtfeld_training as ext
area=Path(sys.argv[sys.argv.index("--")+1]).resolve();area.mkdir(parents=True,exist_ok=True)
s=bpy.context.scene.lichtfeld_training;s.views=4;s.rig_mode="SPHERE"
s.rig_distance=5
assert bpy.ops.lichtfeld.tight_framing()=={"FINISHED"}
assert s.rig_auto_fit and abs(s.rig_distance-1.05)<1e-5
bpy.context.scene.view_settings.use_curve_mapping=True
bpy.context.scene.view_settings.use_white_balance=True
assert bpy.ops.lichtfeld.create_rig()=={"FINISHED"}
# The target is outside the isolated source/camera capture list.
s.rig_aim.location+=Vector((.2,0,.3))
bpy.context.view_layer.update()
cameras=sorted((o for o in s.camera_collection.objects if o.type=="CAMERA"),key=lambda o:o.name)
graph=bpy.context.evaluated_depsgraph_get()
expected=[[list(row) for row in o.evaluated_get(graph).matrix_world] for o in cameras]
source=area/"source.blend";bpy.ops.wm.save_as_mainfile(filepath=str(source))
job={"schema":1,"id":"rig-dependencies","root":str(area),"source_file":str(source),"scene":bpy.context.scene.name,
    "view_layer":bpy.context.view_layer.name,"frame":1,"scope":"SELECTION","sources":["Cube"],"cameras":[o.name for o in cameras],
    "engine":"BLENDER_EEVEE","samples":1,"resolution":64,"seed_points":1000,"test_every":4}
ext.atomic_json(area/"job.json",job)
with (area/"render.log").open("wb") as log:
    code=subprocess.run([bpy.app.binary_path,"-b","--factory-startup","--disable-autoexec",str(source),"--python-exit-code","1",
        "--python",str(root/"lichtfeld_training/render_worker.py"),"--",str(area/"job.json")],stdout=log,stderr=subprocess.STDOUT).returncode
assert code==0,(area/"render.log").read_text(errors="replace")[-3000:]
frames=ext.read_json(area/"dataset/transforms.json")["frames"]
for frame,matrix in zip(frames,expected):
    assert max(abs(a-b) for row,x in zip(frame["transform_matrix"],matrix) for a,b in zip(row,x))<1e-5
manifest=ext.read_json(area/"dataset/manifest.json")
assert manifest['display_overrides_baked'] is False
assert manifest['source_display']['use_curve_mapping'] and manifest['source_display']['use_white_balance']
print("EVALUATED_RIG_TARGET_CAPTURE_POSES_PASS")
print("TIGHT_FRAMING_AND_CAPTURE_COLOR_PROFILE_PASS")
