# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded Blender-side readiness; filesystem/process checks stay on workers."""
import time
import json
from pathlib import Path
import bpy
from . import jobs,ui_runtime,training_options,capture_settings,capture_preview,scene_render
from .visibility import is_hidden
_cache={}
_environment={}
_pending={}
_work=None
_next=0
_save_generation=0

def jobs_folder(prefs):
 return bpy.path.abspath(prefs.jobs_directory) if prefs.jobs_directory else bpy.utils.user_resource('DATAFILES',path='lichtfeld/jobs',create=False)

def connection_folder(prefs): return str(Path(jobs_folder(prefs)) / '.connections')

def selection_objects(context):
 s=context.scene.lichtfeld_training
 types={'MESH','CURVE','SURFACE','FONT','META'}
 if s.source_objects:
  return [item.object for item in s.source_objects if item.object and
          item.object.name in context.scene.objects and item.object.type in types and not item.object.get('lichtfeld_result')]
 try:names=json.loads(s.source_selection or '[]')
 except (ValueError,TypeError):names=[]
 if not isinstance(names,list):names=[]
 remembered=[obj for name in names if isinstance(name,str) and (obj:=context.scene.objects.get(name)) is not None
             and obj.type in types and not obj.get('lichtfeld_result')]
 return remembered or [obj for obj in context.selected_objects if obj.type in types and not obj.get('lichtfeld_result')]


def remember_sources(settings, sources):
 settings.source_objects.clear()
 yield
 for obj in sources:
  settings.source_objects.add().object=obj
  yield
 settings.source_selection=json.dumps([obj.name for obj in sources])

def capture_helpers(settings):
 helper=getattr(settings,'rig_mesh',None)
 curve=getattr(settings,'rig_curve',None)
 collection=getattr(settings,'camera_collection',None)
 committed=collection.get('lichtfeld_placement_mesh') if collection else None
 committed_curve=collection.get('lichtfeld_rig_curve') if collection else None
 return tuple(sorted(set(obj for obj in (helper,committed,curve,committed_curve) if obj is not None),key=lambda obj:obj.as_pointer()))


def signature(context):
 s=context.scene.lichtfeld_training
 return (context.scene.as_pointer(),context.view_layer.as_pointer(),s.scope,s.source_collection.as_pointer() if s.source_collection else 0,
  s.camera_collection.as_pointer() if s.camera_collection else 0,context.scene.frame_current,
  tuple(obj.as_pointer() for obj in capture_helpers(s)),
  bpy.data.filepath,bpy.data.is_dirty,_save_generation,capture_settings.signature(context.scene,s),scene_render.signature(context.scene),
  tuple(sorted(obj.as_pointer() for obj in selection_objects(context))) if s.scope=="SELECTION" else ())

def environment(prefs,force=False,capture_only=False):
 key=(bpy.path.abspath(prefs.backend_path),jobs_folder(prefs),bpy.data.filepath,not capture_only)
 if force:_environment.pop(key,None)
 current=_environment.get(key,{})
 if current.get('generation') != _save_generation and key not in _pending:
  generation=_save_generation
  _pending[key]=True
  def done(value):_environment[key]={**value,'generation':generation};_pending.pop(key,None)
  def fail(error):done({'ok':False,'message':str(error),'backend':False,'folder':False})
  version=current.get('message') if current.get('backend') and current.get('ok') else None
  ui_runtime.submit(lambda:jobs.check_environment(*key,backend_version=version),done,fail)
 return current if current.get('generation') == _save_generation else {'ok':False,'message':'Checking installation and job folder…','checking':True}

def collect(context,capture_only=False):
 initial=signature(context)
 s=context.scene.lichtfeld_training
 sources=[];cameras=[];errors=[];visibility={};visibility_sources=[];generated_cameras=0
 helpers=capture_helpers(s)
 members={}
 for obj in context.view_layer.objects:
  if obj is not None:members[obj.name]=obj
  yield
 try:
  capture_settings.format_for(context.scene,s)
  capture_settings.camera_for(s)
  scene_render.snapshot(context.scene)
 except (ValueError,KeyError) as error:errors.append(str(error))
 if s.scope=='COLLECTION':
  objects=s.source_collection.all_objects if s.source_collection else ()
 elif s.scope=="SELECTION":objects=selection_objects(context)
 else:objects=context.scene.objects
 for obj in objects:
  selected=True
  if selected and obj.type in {'MESH','CURVE','SURFACE','FONT','META'} and not obj.get('lichtfeld_result') and obj not in helpers:
   sources.append(obj.name);visibility[obj.name]=is_hidden(obj,context.view_layer,members=members)
  yield
 if not sources:errors.append('Choose at least one surface object.')
 if s.camera_collection:
  for obj in s.camera_collection.all_objects:
   if obj.type=='CAMERA':
    cameras.append(obj.name)
    generated_cameras+=bool(obj.get('lichtfeld_generated'))
    if obj.data.type!='PERSP':errors.append('All capture cameras must be Perspective.')
    if any(abs(v-1)>1e-4 for v in obj.matrix_world.decompose()[2]):errors.append('Apply camera/parent scale before capture.')
   yield
 cameras.sort()
 try:capture_settings.validate_view_count(len(cameras))
 except ValueError as error:errors.append(str(error))
 if s.scope=='SCENE':
  for obj in context.scene.objects:
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
 return {'sources':sources,'cameras':cameras,'generated_cameras':generated_cameras,'visibility':visibility,'visibility_sources':visibility_sources or sources,'capture_errors':capture_errors,'errors':capture_errors if capture_only else list(dict.fromkeys(errors)),
         'signature':initial,'frame':context.scene.frame_current}

def synchronous(context,prefs):
 work=collect(context)
 while True:
  try:next(work)
  except StopIteration as stop:result=stop.value;break
 env=jobs.check_environment(bpy.path.abspath(prefs.backend_path),jobs_folder(prefs))
 result['environment']=env
 return result

def cached(context,prefs,capture_only=False):
 result=_cache.get(context.scene.as_pointer())
 if not result or result['signature']!=signature(context):
  return {'errors':['Checking source and cameras…'],'sources':[],'cameras':[], 'checking':True,'environment':environment(prefs,capture_only=capture_only)}
 errors=list(result.get('capture_errors',result['errors']) if capture_only else result['errors'])
 devices=scene_render.readiness(context.scene)
 if not devices['ok']:errors.append(devices.get('message','Check Render devices'))
 return {**result,'errors':errors,'checking':devices.get('checking',False),'environment':environment(prefs,capture_only=capture_only)}

def issues(result,require_saved=True):
 errors=list(result['errors'])
 env=result['environment']
 if not env.get('ok'):errors.append(env.get('message','Check the installation.'))
 if require_saved and not bpy.data.filepath:errors.append('Save the .blend before capture.')
 elif require_saved and bpy.data.is_dirty:errors.append('Save the latest changes before capture.')
 return errors

def tick(context,prefs):
 global _work,_next
 if not context.scene:return
 if capture_preview.is_preview(context.scene):return
 s=context.scene.lichtfeld_training
 if s.running or s.operation_busy or s.attached_session or s.dataset_mode=="REUSE":return
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
