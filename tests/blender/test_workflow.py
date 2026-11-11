# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]/"lichtfeld_training"
sys.path.insert(0,str(ROOT))
import workflow
import jobs

class WorkflowTests(unittest.TestCase):
 def test_status_labels_keep_internal_states_separate(self):
  self.assertEqual(workflow.status_label('controller_lost'),'Job interrupted')
  self.assertEqual(workflow.status_label('finalizing capture'),'Preparing dataset')
  self.assertEqual(workflow.phase('Controller lost'),'controller lost')
 def test_phase_controls(self):
  for state in workflow.CAPTURE_PHASES|workflow.STARTUP_PHASES:
   flags=workflow.actions(state)
   self.assertTrue(flags['stop'])
   self.assertFalse(flags['pause'])
  for state in workflow.TRAINING_PHASES:
   self.assertTrue(workflow.actions(state)['pause'])
   self.assertTrue(workflow.actions(state,busy=True)['stop'])
  for state in workflow.TERMINAL|{'saving','finalizing','connecting'}:
   self.assertFalse(workflow.actions(state)['pause'])
   self.assertFalse(workflow.actions(state)['stop'])
 def test_readonly(self):
  for state in workflow.TRAINING_PHASES|workflow.CAPTURE_PHASES|workflow.STARTUP_PHASES|workflow.TERMINAL:
   flags=workflow.actions(state,external=True,checkpoint=True,best=True)
   self.assertFalse(any(flags[k] for k in ('pause','stop','previews','import','continue')))
 def test_continue_requires_checkpoint_and_terminal(self):
  self.assertFalse(workflow.actions('training',checkpoint=True)['continue'])
  self.assertFalse(workflow.actions('completed')['continue'])
  self.assertFalse(workflow.actions('controller_lost',checkpoint=True)['continue'])
  self.assertTrue(workflow.actions('completed',checkpoint=True)['continue'])
 def test_snapshot_does_not_delete_collision(self):
  with tempfile.TemporaryDirectory() as directory:
   source=Path(directory)/'source.blend';source.write_bytes(b'original')
   staging=source.with_name('.lichtfeld-capture-test.blend');staging.write_bytes(b'owned by someone else')
   with self.assertRaises(OSError):jobs.freeze_source({'id':'test','source_file':str(source)})
   self.assertEqual(staging.read_bytes(),b'owned by someone else')
 def test_snapshot_is_immutable_and_cleanup_scoped(self):
  with tempfile.TemporaryDirectory() as directory:
   source=Path(directory)/'source.blend';source.write_bytes(b'original')
   job=jobs.freeze_source({'id':'test','source_file':str(source)})
   staged=Path(job['capture_staging_file']);source.write_bytes(b'new version')
   self.assertEqual(staged.read_bytes(),b'original')
   jobs.release_source({**job,'capture_staging_file':str(source)})
   self.assertTrue(source.exists())
   jobs.release_source(job);self.assertFalse(staged.exists())
 def test_connection_index(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)/'job';root.mkdir();(root/'job.json').write_text('{}')
   job={'source_file':str(Path(directory)/'source.blend'),'scene':'Scene','root':str(root)}
   index=Path(directory)/'connections';jobs.remember_job(index,job)
   self.assertEqual(jobs.remembered_job(index,job['source_file'],'Scene'),str(root))
   self.assertIsNone(jobs.remembered_job(index,job['source_file'],'Other Scene'))

 def test_snapshot_identity_rejects_replaced_source(self):
  with tempfile.TemporaryDirectory() as directory:
   source=Path(directory)/'source.blend';source.write_bytes(b'original')
   info=source.stat()
   identity=[info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns]
   source.write_bytes(b'replaced source')
   with self.assertRaises(ValueError):
    jobs.freeze_source({'id':'test','source_file':str(source),'source_identity':identity})
   self.assertFalse(source.with_name('.lichtfeld-capture-test.blend').exists())

 def test_optimizer_json_cannot_override_managed_fields(self):
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'options.json'
   for value in ('{"iterations": 100}', '{"not_a_parameter": 1}', '{"thin_structure_weight": "bad"}', '{"thin_structure_weight": NaN}'):
    path.write_text(value)
    with self.assertRaises(ValueError):jobs.prepare_optimizer({'strategy':'mrnf','optimizer_file':str(path)})
   path.write_text('{"thin_structure_weight": 0.75}')
   job={'strategy':'mrnf','optimizer_file':str(path),'optimizer':{}}
   jobs.prepare_optimizer(job)
   self.assertEqual(job['optimizer']['thin_structure_weight'],.75)
