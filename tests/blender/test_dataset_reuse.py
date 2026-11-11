# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
import sys, tempfile, unittest
from unittest.mock import Mock, patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lichtfeld_training"))
import jobs
import supervisor
import job_control
from bridge_core import atomic_json, digest, training_command

def fixture(directory, views=4):
    root=Path(directory)/"original"
    dataset=root/"dataset"
    (dataset/"images").mkdir(parents=True)
    frames=[]
    for i in range(views):
        name=f"images/view_{i:06d}.png"
        (dataset/name).write_bytes(b"image fixture")
        frames.append({"file_path":name})
    (dataset/"pointcloud.ply").write_bytes(b"pointcloud fixture")
    atomic_json(dataset/"transforms.json",{"frames":frames})
    atomic_json(dataset/"manifest.json",{"views":views,"transforms_file":"transforms.json","transforms_sha256":digest(dataset/"transforms.json")})
    atomic_json(root/"job.json",{"schema":1,"id":"original","root":str(root),"dataset_root":str(dataset),
        "source_file":"SOURCE_NO_LONGER_EXISTS.blend","backend":"UNTRUSTED","blender":"UNTRUSTED",
        "scope":"COLLECTION","test_every":4,"iterations":100,"eval_interval":50,"max_points":1000,"sh_degree":0,"resume":"OLD.licht","resume_iteration":50})
    (root/"best.json").write_text('{"old":"best"}')
    return root

class DatasetReuse(unittest.TestCase):
    def test_finalization_retries_only_complete_captures_and_honors_stop(self):
        for case in ('recover', 'partial', 'cancel', 'retry_failed', 'missing_capture', 'first_ok', 'cancel_recovery'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = fixture(directory)
                source = root/'source.blend'
                source.write_bytes(b'frozen source')
                if case != 'missing_capture':
                    (root/'capture.blend').write_bytes(b'frozen capture')
                job = jobs.read_job(root/'job.json')
                job.pop('resume')
                (Path(directory)/'exports').mkdir()
                job.update(source_file=str(source), capture_only=True, export_parent=str(Path(directory)/'exports'),
                           cameras=['A', 'B', 'C', 'D'])
                atomic_json(root/'job.json', job)
                atomic_json(root/'capture-progress.json', {'done': 3 if case == 'partial' else 4, 'total': 4})
                owner = job_control.Controller(root)
                launches = []
                def launch(arguments, **kwargs):
                    self.assertFalse(launches and launches[-1][1].poll() is None)
                    recovery = '--finalize-capture' in arguments
                    child = Mock(returncode=0 if case == 'first_ok' or recovery and case != 'retry_failed' else 1)
                    child.poll.return_value = child.returncode
                    child.wait.return_value = child.returncode
                    launches.append((arguments, child))
                    if case == 'cancel' or case == 'cancel_recovery' and recovery:
                        self.assertTrue(owner.acquire())
                        atomic_json(root/'command.json', owner.command('original', 'stop'))
                    if case == 'cancel_recovery' and recovery:
                        child.poll.return_value = None
                        child.wait.side_effect = lambda **kwargs: setattr(child.poll, 'return_value', 0)
                    return child
                try:
                    before = {p.name: p.read_bytes() for p in (root/'dataset/images').iterdir()}
                    with patch.object(supervisor.subprocess, 'Popen', side_effect=launch), patch.object(supervisor.time, 'sleep'):
                        supervisor.supervise(root/'job.json')
                    state = jobs.read_json(root/'state.json')
                    expected = 'completed' if case in {'recover', 'first_ok'} else 'stopped' if case in {'cancel', 'cancel_recovery'} else 'failed'
                    self.assertEqual(state['state'], expected, state)
                    self.assertEqual(len(launches), 2 if case in {'recover', 'retry_failed', 'cancel_recovery'} else 1)
                    if len(launches) == 2:
                        capture = Path(launches[1][0][launches[1][0].index('--python-exit-code')-1])
                        self.assertTrue(capture.samefile(root/'capture.blend'))
                        self.assertIn('--finalize-capture', launches[1][0])
                    self.assertFalse((root/'supervisor.lock').exists())
                    self.assertEqual(before, {p.name: p.read_bytes() for p in (root/'dataset/images').iterdir()})
                finally:
                    owner.close()

    def test_capture_over_one_thousand_views_can_be_reused_and_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            old = fixture(directory, views=1001)
            job, _ = jobs.restart_data(old, {}, backend='TRUSTED', jobs_directory=Path(directory)/'jobs')
            self.assertEqual(job['dataset_views'], 1001)
            (Path(directory)/'exports').mkdir()
            output = jobs.export_captured_dataset({'root': str(old), 'export_parent': str(Path(directory)/'exports')})
            _, _, _, manifest = jobs.captured_dataset(output)
            self.assertEqual(manifest['views'], 1001)
            self.assertEqual(len(list((output/'images').glob('*.png'))), 1001)

    def test_standalone_export_is_reusable_and_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            old=fixture(directory)
            destination=Path(directory)/'exports';destination.mkdir()
            (destination/'keep.txt').write_text('keep')
            job={'root':str(old),'export_parent':str(destination)}
            first=jobs.export_captured_dataset(job)
            second=jobs.export_captured_dataset(job)
            self.assertNotEqual(first,second)
            self.assertEqual((destination/'keep.txt').read_text(),'keep')
            exported,_,data,manifest=jobs.captured_dataset(first)
            self.assertEqual(data,first)
            self.assertEqual(manifest['views'],4)
            self.assertEqual((first/'images/view_000000.png').read_bytes(),b'image fixture')
            fresh,_=jobs.restart_data(first,{},backend='TRUSTED',jobs_directory=Path(directory)/'jobs')
            self.assertTrue(Path(fresh['root']).parent.samefile(Path(directory)/'jobs'))
            self.assertNotIn('capture_only',fresh)
            self.assertNotIn('export_parent',fresh)
            self.assertFalse(list(destination.glob('*.partial')))

    def test_export_rejects_destination_inside_its_source(self):
        with tempfile.TemporaryDirectory() as directory:
            old=fixture(directory)
            with self.assertRaisesRegex(ValueError,'outside'):
                jobs.export_captured_dataset({'root':str(old),'export_parent':str(old/'dataset/images')})
            self.assertEqual(len(list((old/'dataset/images').iterdir())),4)

    def test_cancelled_export_leaves_no_partial_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            old=fixture(directory)
            destination=Path(directory)/'exports';destination.mkdir()
            with self.assertRaises(InterruptedError):
                jobs.export_captured_dataset({'root':str(old),'export_parent':str(destination)},cancelled=lambda:True)
            self.assertEqual(list(destination.iterdir()),[])
            self.assertTrue((old/'dataset/manifest.json').is_file())

    def test_export_capture_validation_does_not_require_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            from unittest.mock import patch
            with patch.object(jobs,'verify_backend',side_effect=AssertionError('must not launch trainer')):
                result=jobs.check_environment('',directory,require_backend=False)
            self.assertTrue(result['ok'])

    def test_preview_sh_limit_does_not_change_native_training_degree(self):
        from bridge_core import training_config
        with tempfile.TemporaryDirectory() as directory:
            old=fixture(directory)
            for degree in range(4):
                job,_=jobs.restart_data(old,{"sh_degree":3,"preview_sh_degree":degree,"full_preview":False},backend="TRUSTED",jobs_directory=directory)
                self.assertEqual(training_config(job)["optimization"]["sh_degree"],3)
                self.assertEqual(job["preview_sh_degree"],degree)
            for degree in (-1,4,True,1.5,"0"):
                with self.assertRaises(ValueError):
                    jobs.restart_data(old,{"preview_sh_degree":degree},backend="TRUSTED",jobs_directory=directory)

    def test_fresh_training_reuses_images_without_source_checkpoint_or_old_result(self):
        with tempfile.TemporaryDirectory() as directory:
            old=fixture(directory)
            job, previous=jobs.restart_data(old,{"iterations":20,"eval_interval":10,"sh_degree":1},backend="TRUSTED",jobs_directory=directory)
            self.assertEqual(previous,"original")
            self.assertNotIn("resume",job)
            self.assertNotIn('resume_iteration', job)
            self.assertTrue(job["reuse_dataset"])
            self.assertEqual(job["backend"],"TRUSTED")
            self.assertFalse((Path(job["root"])/"best.json").exists())
            self.assertTrue((old/"best.json").exists())
            args=training_command(job,"hook.py")
            self.assertEqual(args[args.index("-d")+1],str((old/"dataset").resolve()))
            self.assertNotIn("--resume",args)
            # Repeated experiments resolve the same immutable captured dataset.
            atomic_json(Path(job["root"])/"job.json",job)
            second,_=jobs.restart_data(job["root"],{"iterations":8},backend="TRUSTED",jobs_directory=directory)
            self.assertEqual(second["dataset_root"],job["dataset_root"])
            self.assertNotEqual(second["root"],job["root"])

    def test_missing_modified_or_escaping_capture_fails_without_new_job(self):
        with tempfile.TemporaryDirectory() as directory:
            old=fixture(directory)
            cases=[{"use_depth_loss":True},{"backend":"EVIL"},{"resolution":4096}]
            for options in cases:
                with self.assertRaises(ValueError):jobs.restart_data(old,options,backend="TRUSTED",jobs_directory=directory)
                self.assertEqual(len(list(Path(directory).iterdir())),1)
            (old/"dataset/images/view_000000.png").unlink()
            with self.assertRaises(ValueError):jobs.restart_data(old,{},backend="TRUSTED",jobs_directory=directory)
            (old/"dataset/images/view_000000.png").write_bytes(b"fixture")
            atomic_json(old/"dataset/transforms.json",{"frames":[]})
            with self.assertRaises(ValueError):jobs.restart_data(old,{},backend="TRUSTED",jobs_directory=directory)
