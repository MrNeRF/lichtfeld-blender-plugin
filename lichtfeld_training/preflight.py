# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded Blender-side readiness; filesystem/process checks stay on workers."""
import time
import json
from pathlib import Path
import bpy
from . import jobs,ui_runtime,training_options,capture_settings,capture_preview,scene_render
from .visibility import is_hidden,object_steps
_cache={}
_environment={}
_pending={}
_work=None
_next=0
_save_generation=0

def jobs_folder(prefs):
 return bpy.path.abspath(prefs.jobs_directory) if prefs.jobs_directory else bpy.utils.user_resource('DATAFILES',path='lichtfeld/jobs',create=False)

def connection_folder(prefs): return str(Path(jobs_folder(prefs)) / '.connections')

def is_surface(obj, helpers=()):
 return obj is not None and obj.type in {'MESH','CURVE','SURFACE','FONT','META'} and not obj.get('lichtfeld_result') and not obj.get('lichtfeld_path_guide') and obj not in helpers

def selection_objects(context):
 s=context.scene.lichtfeld_training
 if s.source_objects:
  return [item.object for item in s.source_objects if item.object and
          item.object.name in context.scene.objects and is_surface(item.object)]
 try:names=json.loads(s.source_selection or '[]')
 except (ValueError,TypeError):names=[]
 if not isinstance(names,list):names=[]
 remembered=[obj for name in names if isinstance(name,str) and (obj:=context.scene.objects.get(name)) is not None
             and is_surface(obj)]
 return remembered or [obj for obj in context.selected_objects if is_surface(obj)]


def remember_sources(settings, sources):
 settings.source_objects.clear()
 yield
 for obj in sources:
  settings.source_objects.add().object=obj
  yield
 settings.source_selection=json.dumps([obj.name for obj in sources])

def capture_objects(context):
 s=context.scene.lichtfeld_training
 if s.scope=='COLLECTION':return s.source_collection.all_objects if s.source_collection else ()
 if s.scope=='SELECTION':return selection_objects(context)
 return context.scene.objects

def camera_collections(settings):
 rows=getattr(settings,'capture_collections',())
 if rows:return tuple(dict.fromkeys(row.collection for row in rows if row.collection is not None))
 collection=getattr(settings,'camera_collection',None)
 return (collection,) if collection is not None else ()


def camera_objects(scene, settings):
 cameras=set()
 rows=getattr(settings,'capture_collections',())
 if any(row.collection is None for row in rows):
  raise ValueError('Choose a collection for each capture entry, or remove the empty entries.')
 collections=camera_collections(settings)
 members=set(scene.collection.children_recursive)
 members.add(scene.collection)
 if any(collection not in members for collection in collections):
  raise ValueError('Choose capture collections linked to this scene.')
 for collection in collections:
  for obj in object_steps(collection.all_objects):
   if obj is not None and obj.type=='CAMERA':
    cameras.add(obj)
   yield
 if len({camera.name for camera in cameras}) != len(cameras):
  raise ValueError('Capture cameras must have unique names across the included collections.')
 for camera in cameras:
  if bpy.data.objects.get(camera.name) != camera:
   raise ValueError(f'Camera name is ambiguous in this file: {camera.name}. Rename the camera or make the linked camera local with a unique name.')
  yield
 return sorted(cameras,key=lambda obj:(obj.name,obj.library.filepath if obj.library else ''))


def capture_helpers(settings):
 helper=getattr(settings,'rig_mesh',None)
 curve=getattr(settings,'rig_curve',None)
 collections=set(camera_collections(settings))
 active=getattr(settings,'camera_collection',None)
 if active:collections.add(active)
 committed=(collection.get(key) for collection in collections
            for key in ('lichtfeld_placement_mesh','lichtfeld_rig_curve'))
 return tuple(sorted({obj for obj in (helper,curve,*committed) if obj is not None},key=lambda obj:obj.as_pointer()))


def signature(context):
 s=context.scene.lichtfeld_training
 return (context.scene.as_pointer(),context.view_layer.as_pointer(),s.scope,s.source_collection.as_pointer() if s.source_collection else 0,
  s.camera_collection.as_pointer() if s.camera_collection else 0,
  tuple(row.collection.session_uid if row.collection else None for row in getattr(s,'capture_collections',())),
  context.scene.frame_current,
  tuple(obj.as_pointer() for obj in capture_helpers(s)),
  bpy.data.filepath,bpy.data.is_dirty,_save_generation,capture_settings.signature(context.scene,s),scene_render.signature(context.scene),
  tuple(sorted(obj.as_pointer() for obj in selection_objects(context))) if s.scope=="SELECTION" else (),
  s.rig_reference.mode,s.rig_reference.collection.as_pointer() if s.rig_reference.collection else 0,len(s.rig_reference.objects))

def invalidate(scene=None):
 global _work,_next
 pointer=scene.as_pointer() if scene else None
 if pointer is None:_cache.clear()
 else:_cache.pop(pointer,None)
 if _work and (pointer is None or _work[0]==pointer):
  _work[1].close();_work=None
 _next=0

def environment(prefs,force=False,capture_only=False):
 key=(bpy.path.abspath(prefs.backend_path),jobs_folder(prefs),bpy.data.filepath,not capture_only)
 current=_environment.get(key,{})
 known=next((value for candidate,value in _environment.items() if key[0] and candidate[0]==key[0]
             and candidate[3] and value.get('backend') and value.get('backend_identity')),None)
 known_backend=bool(known) or any(candidate[0]==key[0] and candidate[3] and pending[1]
                                  for candidate,pending in _pending.items())
 if force:
  for candidate in tuple(_environment):
   if candidate==key or candidate[0]==key[0] or candidate[1]==key[1]:_environment.pop(candidate,None)
  for candidate in tuple(_pending):
   if candidate==key or candidate[0]==key[0] or candidate[1]==key[1]:_pending.pop(candidate,None)
  current={}
 if current.get('generation') != _save_generation and key not in _pending:
  generation=_save_generation
  token=object();_pending[key]=(token,known_backend)
  def done(value):
   if _pending.get(key,(None,))[0] is token:
    _environment[key]={**value,'generation':generation};_pending.pop(key,None)
  def fail(error):done({'ok':False,'message':str(error),'backend':False,'folder':False})
  version=known.get('backend_version') if known and not force else None
  identity=known.get('backend_identity') if known and not force else None
  ui_runtime.submit(lambda:jobs.check_environment(*key,backend_version=version,backend_identity=identity),done,fail)
 return current if current.get('generation') == _save_generation else {**current,'backend':known_backend or bool(current.get('backend')),'ok':False,'message':'Checking installation and job folder…','checking':True}

def camera_checks(context, *, single_render=False, poses=False, inspect=None):
 """Canonical camera enumeration and evaluated-pose validation, on the UI thread."""
 s=context.scene.lichtfeld_training
 issues={};frames=[];selection_error=''
 graph=context.evaluated_depsgraph_get()
 try:cameras=yield from camera_objects(context.scene,s)
 except ValueError as error:
  cameras=[];selection_error=str(error)
 count=len(cameras);generated=sum(bool(obj.get('lichtfeld_generated')) for obj in cameras)
 if single_render:
  index=min(s.capture_preview_index,count)-1
  cameras=cameras[index:index+1] if cameras else []
 for index,camera in enumerate(cameras):
  try:matrix=capture_settings.camera_pose(camera,graph)
  except ValueError as error:issues[str(error)]=camera.name
  else:
   if poses:frames.append((camera.name,tuple(tuple(row) for row in matrix)))
   if inspect is not None:inspect(camera,graph,matrix)
  yield (index+1,len(cameras)) if poses else None
 return {'cameras':[obj.name for obj in cameras],'camera_count':count,
         'generated_cameras':generated,'camera_issues':issues,'poses':frames,
         'camera_selection_error':selection_error}


def collect(context,capture_only=False,*,single_render=False):
 initial=signature(context)
 s=context.scene.lichtfeld_training
 sources=[];errors=[];visibility={};visibility_sources=[]
 helpers=capture_helpers(s)
 capture_format=None;render=None
 members={}
 for obj in object_steps(context.view_layer.objects):
  if obj is not None:members[obj.name]=obj
  yield
 try:
  capture_format=capture_settings.format_for(context.scene,s)
  capture_settings.camera_for(s)
  render=scene_render.snapshot(context.scene)
 except (ValueError,KeyError) as error:errors.append(str(error))
 for obj in object_steps(capture_objects(context)):
  if is_surface(obj, helpers):
   sources.append(obj.name);visibility[obj.name]=is_hidden(obj,context.view_layer,members=members)
  yield
 if not sources:errors.append('Choose at least one surface object.')
 checks=yield from camera_checks(context,single_render=single_render)
 cameras=checks['cameras'];camera_count=checks['camera_count']
 generated_cameras=checks['generated_cameras'];camera_issues=checks['camera_issues']
 if checks['camera_selection_error']:errors.append(checks['camera_selection_error'])
 errors.extend(camera_issues)
 reference_changed=False
 if s.camera_collection and s.camera_collection.get('lichtfeld_generated'):
  from . import rig_edit
  reference_changed=yield from rig_edit.reference_changed(s.rig_reference,s.camera_collection.lichtfeld_rig_reference)
 try:capture_settings.validate_view_count(len(cameras),single_render=single_render)
 except ValueError as error:errors.append(str(error))
 if s.scope=='SCENE':
  for obj in object_steps(context.scene.objects):
   if not obj.get('lichtfeld_result') and (obj.type in {'MESH','CURVE','SURFACE','FONT','META','VOLUME','POINTCLOUD'} or obj.type=='EMPTY' and obj.instance_type=='COLLECTION'):
    visibility_sources.append(obj.name)
    if obj.name not in visibility:visibility[obj.name]=is_hidden(obj,context.view_layer,members=members)
   yield
 if not visibility_sources:visibility_sources=list(sources)
 for helper in helpers:
  if helper.name in context.scene.objects:
   if helper.name not in visibility_sources:visibility_sources.append(helper.name)
   if helper.name not in visibility:visibility[helper.name]=is_hidden(helper,context.view_layer,members=members)
  yield
 capture_errors=list(dict.fromkeys(errors))
 errors.extend(training_options.validate(s.strategy,training_options.optimizer_values(s)))
 if s.scope=='SCENE' and s.mask_mode not in {'AUTO','none'}:errors.append('Whole-scene RGB capture has no foreground mask. Choose Automatic/None or capture objects/a collection.')
 if s.refine_start>=s.refine_end:errors.append('Refinement start must be before its end.')
 if s.growth_end>s.refine_end:errors.append('Growth must end before refinement ends.')
 if s.use_normal_loss and s.normal_start_fraction>=s.normal_end_fraction:errors.append('Normal supervision start must be before its end.')
 return {'sources':sources,'cameras':cameras,'camera_count':camera_count,'generated_cameras':generated_cameras,'camera_issues':camera_issues,'visibility':visibility,'visibility_sources':visibility_sources or sources,'capture_errors':capture_errors,'errors':capture_errors if capture_only else list(dict.fromkeys(errors)),
         'signature':initial,'frame':context.scene.frame_current,'reference_changed':reference_changed,'format':capture_format,'render':render}

def synchronous(context,prefs):
 work=collect(context)
 while True:
  try:next(work)
  except StopIteration as stop:result=stop.value;break
 env=jobs.check_environment(bpy.path.abspath(prefs.backend_path),jobs_folder(prefs))
 result['environment']=env
 return result

def cached(context,prefs,capture_only=False,*,capture=None):
 # A cooperative launch already collected a fresh snapshot. Busy-state polling
 # may invalidate the display cache while that collection is in progress.
 result=capture if capture is not None else _cache.get(context.scene.as_pointer())
 if not result or result['signature']!=signature(context):
  return {'errors':['Checking source and cameras…'],'sources':[],'cameras':[], 'checking':True,'environment':environment(prefs,capture_only=capture_only)}
 errors=list(result.get('capture_errors',result['errors']) if capture_only else result['errors'])
 devices=scene_render.readiness(context.scene)
 if not devices['ok']:errors.append(devices.get('message','Check Render devices'))
 return {**result,'errors':errors,'checking':devices.get('checking',False),'render_device':devices.get('effective'),'environment':environment(prefs,capture_only=capture_only)}

def camera_status(context):
 # Read the canonical readiness snapshot; drawing must not launch another scan.
 result=_cache.get(context.scene.as_pointer())
 return result if result and result['signature']==signature(context) else None


def save_issues(require_saved=True):
 if require_saved and not bpy.data.filepath:return ['Save the .blend before capture.']
 if require_saved and bpy.data.is_dirty:return ['Save the latest changes before capture.']
 return []


def issues(result,require_saved=True):
 errors=list(result['errors'])
 env=result['environment']
 if not env.get('ok'):errors.append(env.get('message','Check the installation.'))
 errors.extend(save_issues(require_saved))
 return errors

def tick(context,prefs):
 from . import rig_analysis
 if rig_analysis.is_running():return
 global _work,_next
 if not context.scene:return
 if capture_preview.is_preview(context.scene):return
 s=context.scene.lichtfeld_training
 if s.running or s.operation_busy or s.attached_session or s.dataset_mode=="REUSE":
  invalidate(context.scene)
  return
 scene_render.tick(context.scene)
 environment(prefs,capture_only=s.dataset_mode=="EXPORT")
 if _work and _work[0]!=context.scene.as_pointer():
  _work[1].close();_work=None;_next=0
 if _work is None:
  if time.monotonic()<_next:return
  _work=(context.scene.as_pointer(),collect(context))
 deadline=time.perf_counter()+.002
 while _work and time.perf_counter()<deadline:
  pointer,work=_work
  try:next(work)
  except StopIteration as stop:
   changed = _cache.get(pointer) != stop.value
   _cache[pointer]=stop.value;_work=None;_next=time.monotonic()+.4
   if changed:
    rig_analysis.refresh_report()
    for screen in bpy.data.screens:
     for area in screen.areas:
      if area.type=='VIEW_3D':area.tag_redraw()
  except (ReferenceError,RuntimeError):_work=None;_next=0

def saved():
 global _save_generation,_work,_next
 _save_generation+=1
 if _work:_work[1].close()
 _work=None;_next=0;_cache.clear()


def clear():
 global _work,_next
 if _work:_work[1].close()
 _work=None;_next=0;_cache.clear();_pending.clear();_environment.clear()
