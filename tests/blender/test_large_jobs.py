# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Large-scene job startup, bounded documents and non-blocking monitoring."""
import importlib
import ast
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[2]/'lichtfeld_training'
sys.path.insert(0,str(SOURCE))
import bridge_core as core
import jobs
from lichtfeld_sessions import transport
import supervisor

package=types.ModuleType('large_job_test_package')
package.__path__=[str(SOURCE)]
sys.modules[package.__name__]=package
reader=importlib.import_module(package.__name__+'.session_reader')
visibility=importlib.import_module(package.__name__+'.visibility')

def fixture(root):
    names=[f'GEO-factory-detailed-instance-{i:06d} [static]' for i in range(14001)]
    job={'schema':1,'id':root.name,'root':str(root),'iterations':100,'sources':names,
         'visibility_sources':names,'source_visibility':dict.fromkeys(names,False)}
    core.atomic_json(root/'job.json',job)
    return job

def until(condition):
    end=time.monotonic()+4
    while time.monotonic()<end:
        if condition(): return
        time.sleep(.01)
    raise AssertionError('Background reader did not publish the expected status')

class LargeJobs(unittest.TestCase):
    def test_ply_large_count_is_not_rejected_by_an_arbitrary_ten_million_limit(self):
        import numpy as np
        with tempfile.TemporaryFile() as stream:
            stream.write(b'ply\nformat binary_little_endian 1.0\nelement vertex 10000001\nproperty float x\nend_header\n')
            stream.seek(0)
            # Simulate a large file and stop before allocating it: the count must
            # reach the decoder, while a short payload still fails normally.
            with patch.object(reader.os, 'fstat', return_value=types.SimpleNamespace(st_size=100_000_000)), \
                 patch.object(np, 'fromfile', return_value=np.zeros(1, dtype=np.float32)) as decode:
                with self.assertRaisesRegex(ValueError, 'Invalid Gaussian payload'):
                    reader.read_ply(stream)
                self.assertEqual(decode.call_args.kwargs['count'], 10_000_001)

    def test_ply_truncated_large_payload_is_rejected_before_allocation(self):
        import numpy as np
        with tempfile.TemporaryFile() as stream:
            stream.write(b'ply\nformat binary_little_endian 1.0\nelement vertex 10000001\nproperty float x\nend_header\n')
            stream.seek(0)
            with patch.object(np, 'fromfile') as decode:
                with self.assertRaisesRegex(ValueError, 'Invalid Gaussian payload'):
                    reader.read_ply(stream)
                decode.assert_not_called()

    def test_ply_count_cannot_overflow_blender_four_component_foreach_set(self):
        import numpy as np
        with tempfile.TemporaryFile() as stream:
            stream.write(b'ply\nformat binary_little_endian 1.0\nelement vertex 536870912\nproperty float x\nend_header\n')
            stream.seek(0)
            with patch.object(np, 'fromfile') as decode:
                with self.assertRaisesRegex(ValueError, "Blender's supported range"):
                    reader.read_ply(stream)
                decode.assert_not_called()

    def test_capture_validation_signature_is_bounded_and_checked_before_launch(self):
        tree=ast.parse((SOURCE/'__init__.py').read_text(encoding='utf-8'))
        cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='LF_OT_start')
        method=next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name=='execute')
        for change in (False,True):
            settings=types.SimpleNamespace(operation_busy=False,detail='',running=False)
            scene=types.SimpleNamespace(lichtfeld_training=settings)
            context=types.SimpleNamespace(scene=scene)
            state={'signature':'saved','reads':0,'clock':0,'launches':0,'work':None}
            def clock(): state['clock']+=.00001;return state['clock']
            def signature(_): state['reads']+=1;return state['signature']
            def collect(*args,**kw):
                for _ in range(14001): yield
                if change: state['signature']='changed'
                return {'fixture':'complete'}
            def launch(*args): state['launches']+=1
            def schedule(generator,failure): state['work']=generator
            namespace={'bpy':types.SimpleNamespace(app=types.SimpleNamespace(background=False)),
                'time':types.SimpleNamespace(perf_counter=clock),
                'preflight':types.SimpleNamespace(collect=collect,signature=signature),
                'ui_runtime':types.SimpleNamespace(schedule=schedule),
                'LF_OT_start':types.SimpleNamespace(launch=launch),'LF_OT_export_dataset':type('Export',(),{})}
            exec(compile(ast.Module(body=[method],type_ignores=[]),'<capture execute>','exec'),namespace)
            self.assertEqual(namespace['execute'](object(),context),{'FINISHED'})
            if change:
                with self.assertRaisesRegex(ValueError,'capture setup changed'): list(state['work'])
                self.assertEqual(state['launches'],0)
            else:
                list(state['work'])
                self.assertEqual(state['launches'],1)
            self.assertLess(state['reads'],10)
            self.assertFalse(settings.operation_busy)

    def test_reset_without_displayed_splat_does_not_touch_scene_visibility(self):
        scene=types.SimpleNamespace(as_pointer=lambda:1,view_layers=types.SimpleNamespace(get=lambda _:object()),objects=[])
        visibility.clear()
        self.assertEqual(list(visibility.source_steps(scene,{'id':'unstarted'},False,restore_only=True)),[])

    def test_many_sources_use_one_layer_index_and_restore_only_changed_visibility(self):
        class Object:
            type='MESH'
            library=None
            def __init__(self,index): self.name=str(index);self.hidden=False;self.session_uid=index+1
            def get(self,key): return False
            def hide_get(self,**kw): return self.hidden
            def hide_set(self,value,**kw): self.hidden=value
        class Objects(list):
            def get(self,name): raise AssertionError('Repeated native layer name scan')
        objects=Objects(Object(i) for i in range(5000))
        layer=types.SimpleNamespace(objects=objects)
        scene=types.SimpleNamespace(as_pointer=lambda:2,view_layers=types.SimpleNamespace(get=lambda _:layer),objects=objects)
        job={'id':'visible','scope':'SCENE','source_visibility':{obj.name:False for obj in objects}}
        visibility.clear()
        native_objects={(obj.name,None):obj for obj in objects}
        fake_bpy=types.SimpleNamespace(data=types.SimpleNamespace(objects=native_objects))
        with patch.dict(sys.modules,{'bpy':fake_bpy}):
            list(visibility.source_steps(scene,job,True))
            self.assertTrue(all(obj.hidden for obj in objects))
            list(visibility.source_steps(scene,job,False,restore_only=True))
            self.assertFalse(any(obj.hidden for obj in objects))

    def test_scene_job_can_exceed_status_limit_without_weakening_status_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            expected=fixture(root)
            self.assertGreater((root/'job.json').stat().st_size,transport.MAX_METADATA_CHARS)
            with self.assertRaisesRegex(ValueError,'size limit'): core.read_json(root/'job.json')
            self.assertEqual(jobs.read_job(root/'job.json'),expected)
            self.assertIsNone(transport.read_json(root/'job.json'))

    def test_camera_documents_use_their_own_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'transforms.json'
            expected={'frames':[{'file_path':f'images/view_{i:06d}.png','transform_matrix':[[1,0,0,0]]*4} for i in range(1000)]}
            # Camera metadata may contain explicit lens and matrix information.
            for frame in expected['frames']: frame['calibration']='x'*300
            core.atomic_json(path,expected)
            self.assertEqual(core.read_dataset_json(path),expected)
            with self.assertRaises(ValueError): core.read_json(path)

    def test_explicit_limits_still_reject_oversized_or_invalid_requests(self):
        self.assertEqual(transport.bounded_metadata(io.StringIO('1234'),4),'1234')
        with self.assertRaises(ValueError): transport.bounded_metadata(io.StringIO('12345'),4)
        for limit in (True,0,-1,transport.MAX_DOCUMENT_CHARS+1):
            with self.assertRaises(ValueError): transport.bounded_metadata(io.StringIO(''),limit)

    def test_reader_caches_large_job_and_refreshes_after_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            job=fixture(root)
            core.atomic_json(root/'state.json',{'state':'rendering','supervisor_pid':os.getpid(),
                'updated':time.time(),'control_protocol':1})
            original=reader.read_job_json
            with patch.object(reader,'read_job_json',wraps=original) as read:
                observer=reader.Observer(root)
                try:
                    until(lambda: observer.status.get('control_available'))
                    self.assertEqual(len(observer.status['job']['sources']),14001)
                    time.sleep(.35)
                    self.assertEqual(read.call_count,1)
                    job['iterations']=200
                    core.atomic_json(root/'job.json',job)
                    until(lambda: observer.status.get('job',{}).get('iterations')==200)
                    self.assertEqual(read.call_count,2)
                    self.assertFalse(observer.error)
                finally:
                    observer.close();observer.thread.join(4)

    def test_supervisor_and_reader_report_invalid_startup_instead_of_waiting(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'job.json').write_text('{invalid',encoding='utf-8')
            with patch.object(supervisor.subprocess,'Popen') as spawn:
                supervisor.supervise(root/'job.json')
            spawn.assert_not_called()
            status=core.read_json(root/'state.json')
            self.assertEqual(status['state'],'failed')
            self.assertIn('Cannot read training job',status['error'])
            observer=reader.Observer(root)
            try:
                until(lambda: observer.status.get('state')=='failed')
                self.assertIn('Cannot read training job',observer.status['error'])
                self.assertFalse(observer.control_available)
                # Correcting the document allows the existing reader to recover.
                fixture(root)
                core.atomic_json(root/'state.json',{'state':'rendering','supervisor_pid':os.getpid(),
                    'updated':time.time(),'control_protocol':1})
                until(lambda: observer.status.get('control_available'))
            finally:
                observer.close();observer.thread.join(4)

if __name__=='__main__': unittest.main()
