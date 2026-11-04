# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Paused session geometry updates do not require another Gaussian publication."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch
import weakref

import numpy as np

SOURCE = Path(__file__).resolve().parents[2]/'lichtfeld_training'
package = types.ModuleType('session_update_test_package')
package.__path__ = [str(SOURCE)]
sys.modules[package.__name__] = package
reader = importlib.import_module(package.__name__+'.session_reader')
transport = importlib.import_module(package.__name__+'.session_transport')
geometry = importlib.import_module(package.__name__+'.session_geometry')
core = importlib.import_module(package.__name__+'.bridge_core')


def until(predicate):
    deadline = time.monotonic()+5
    while time.monotonic()<deadline:
        result = predicate()
        if result: return result
        time.sleep(.01)
    raise AssertionError('Session reader timed out')


def take(observer):
    return until(observer.take)


def arrays():
    return {'means':np.zeros((3,3),np.float32), 'scale':np.zeros((3,3),np.float32),
            'rotation':np.tile(np.array([1,0,0,0],np.float32),(3,1)),
            'opacity':np.zeros((3,1),np.float32), 'sh':np.zeros((3,1,3),np.float32)}


class GeometryUpdates(unittest.TestCase):
    def test_paused_external_repair_update_and_removal_do_not_reread_the_splat(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,LFS_TRAINING_SESSIONS_DIR=directory):
            publisher = transport.SessionPublisher({})
            publisher.update(geometry={'path':'view_geometry.json','bytes':1,'sha256':'bad'})
            publisher.publish(arrays(), 17)
            publisher.update(state='paused')
            observer = reader.Observer(publisher.root,external=True)
            try:
                first = take(observer)
                observer.acknowledge(first['record']['id'],geometry_ref=first['geometry_ref'])
                until(lambda:observer.last==first['record']['id'])
                prepare = Mock(side_effect=AssertionError('Geometry update prepared Gaussian data'))
                with patch.object(reader,'load_record',side_effect=AssertionError('Geometry update reread Gaussian data')), patch.object(observer,'prepare',prepare):
                    geometry.publish(publisher,{'schema':1,'cameras':[]})
                    observer.geometry_retry_at=0
                    repaired=take(observer)
                    self.assertTrue(repaired['geometry_only'])
                    self.assertEqual(repaired['record']['id'],first['record']['id'])
                    self.assertIsNone(repaired['data'])
                    self.assertEqual(repaired['count'],3)
                    self.assertFalse(observer.status['geometry_error'])
                    observer.acknowledge(repaired['record']['id'],geometry_ref=None)
                    until(lambda:observer.ack.empty())
                    self.assertTrue(observer.pending)
                    observer.acknowledge(repaired['record']['id'],geometry_ref=repaired['geometry_ref'])
                    until(lambda:observer.last_geometry_ref==repaired['geometry_ref'])
                    changed=np.eye(4);changed[0,3]=9
                    geometry.publish(publisher,{'schema':1,'cameras':[],'model_to_data':changed.tolist()})
                    updated=take(observer)
                    self.assertTrue(updated['geometry_only'])
                    self.assertEqual(updated['geometry']['model_to_data'][0][3],9)
                    observer.acknowledge(updated['record']['id'],geometry_ref=updated['geometry_ref'])
                    until(lambda:observer.last_geometry_ref==updated['geometry_ref'])
                    publisher.update(geometry=None)
                    removed=take(observer)
                    self.assertTrue(removed['geometry_only'])
                    self.assertIsNone(removed['geometry'])
                    observer.acknowledge(removed['record']['id'],geometry_ref=removed['geometry_ref'])
                    until(lambda:observer.last_geometry_ref is None)
                    prepare.assert_not_called()
            finally:
                observer.close();observer.thread.join(5)
                publisher.finish();publisher.thread.join(3);publisher.pool.close()

    def test_failed_geometry_only_install_restores_full_data_automatically(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,LFS_TRAINING_SESSIONS_DIR=directory):
            publisher=transport.SessionPublisher({})
            publisher.publish(arrays(),17)
            publisher.update(state='paused')
            observer=reader.Observer(publisher.root,external=True)
            try:
                first=take(observer)
                observer.acknowledge(first['record']['id'])
                until(lambda:observer.last==first['record']['id'])
                geometry.publish(publisher,{'schema':1,'cameras':[]})
                updated=take(observer)
                self.assertTrue(updated['geometry_only'])
                observer.acknowledge(updated['record']['id'],'Displayed object removed',geometry_ref=updated['geometry_ref'])
                until(lambda:observer.error=='Displayed object removed')
                observer.retry_at=0
                restored=take(observer)
                self.assertFalse(restored['geometry_only'])
                self.assertEqual(restored['data']['means'].shape,(3,3))
                observer.acknowledge(restored['record']['id'],geometry_ref=restored['geometry_ref'])
                until(lambda:not observer.pending and not observer.error)
            finally:
                observer.close();observer.thread.join(5)
                publisher.finish();publisher.thread.join(3);publisher.pool.close()

    def test_owned_paused_job_updates_geometry_with_the_same_saved_artifact(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,LFS_TRAINING_SESSIONS_DIR=str(Path(directory)/'sessions')):
            root=Path(directory)/'job';root.mkdir()
            publisher=transport.SessionPublisher({})
            core.atomic_json(root/'job.json',{'schema':1,'id':'job','root':str(root)})
            artifact=core.write_splat(root,arrays(),17,'best',{'psnr':30})
            state={'state':'paused','iteration':17,'preview':artifact,'session_directory':str(publisher.root)}
            core.atomic_json(root/'state.json',{'state':'paused','supervisor_pid':os.getpid(),'updated':time.time()})
            core.atomic_json(root/'runtime.json',state)
            observer=reader.Observer(root)
            try:
                first=take(observer)
                observer.acknowledge(first['record']['id'])
                until(lambda:observer.last==first['record']['id'])
                geometry.publish(publisher,{'schema':1,'cameras':[]})
                core.atomic_json(root/'runtime.json',{**state,'geometry':publisher.record['geometry']})
                updated=take(observer)
                self.assertTrue(updated['geometry_only'])
                self.assertIsNotNone(updated['geometry'])
                self.assertEqual(updated['record']['id'],artifact['id'])
            finally:
                observer.close();observer.thread.join(5)
                publisher.finish();publisher.thread.join(3);publisher.pool.close()

    def test_ack_releases_prepared_and_unused_source_arrays_while_monitoring_continues(self):
        class Payload: pass
        source_refs=[]
        def prepare(value):
            source_refs.append(weakref.ref(value['means']))
            return Payload()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,LFS_TRAINING_SESSIONS_DIR=directory):
            publisher=transport.SessionPublisher({})
            publisher.publish(arrays(),17)
            publisher.update(state='paused')
            observer=reader.Observer(publisher.root,external=True,prepare=prepare)
            try:
                first=take(observer)
                until(lambda:source_refs[0]() is None)
                prepared=weakref.ref(first['data'])
                identifier=first['record']['id']
                observer.acknowledge(identifier)
                del first
                until(lambda:observer.last==identifier and prepared() is None)
                self.assertTrue(observer.thread.is_alive())
            finally:
                observer.close();observer.thread.join(5)
                publisher.finish();publisher.thread.join(3);publisher.pool.close()

    def test_failed_preparation_releases_source_arrays_during_retry_delay(self):
        source_refs=[]
        def prepare(value):
            source_refs.append(weakref.ref(value['means']))
            raise ValueError('Preparation unavailable')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,LFS_TRAINING_SESSIONS_DIR=directory):
            publisher=transport.SessionPublisher({})
            publisher.publish(arrays(),17)
            observer=reader.Observer(publisher.root,external=True,prepare=prepare)
            try:
                until(lambda:observer.error=='Preparation unavailable')
                until(lambda:source_refs[0]() is None)
                self.assertFalse(observer.pending)
                self.assertIsNone(observer.inflight)
            finally:
                observer.close();observer.thread.join(5)
                publisher.finish();publisher.thread.join(3);publisher.pool.close()


if __name__=='__main__':unittest.main()
