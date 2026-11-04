# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Failure-path contracts; run with unittest discovery using Blender's Python."""
import importlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np

SOURCE = Path(__file__).resolve().parents[2] / "lichtfeld_training"
sys.path.insert(0, str(SOURCE))
import jobs
import bridge_core as core
import supervisor

# Import the bpy-free reader without executing the Blender add-on entry point.
package = types.ModuleType("failure_test_package")
package.__path__ = [str(SOURCE)]
sys.modules[package.__name__] = package
reader = importlib.import_module(package.__name__ + ".session_reader")


def previous_job(directory):
    root = Path(directory) / "previous"
    (root / "output").mkdir(parents=True)
    (root / "output/project.licht").write_bytes(b"checkpoint fixture")
    core.atomic_json(root / "job.json", {"schema": core.SCHEMA, "id": "previous",
        "root": str(root), "dataset_root": str(root / "dataset"),
        "backend": "UNTRUSTED_METADATA_EXECUTABLE", "connection_directory": "UNTRUSTED_INDEX",
        "iterations": 8, "eval_interval": 4, "max_points": 1000,
        "sh_degree": 0, "test_every": 4})
    arrays = {"means": np.zeros((1, 3)), "scale": np.zeros((1, 3)),
        "rotation": np.array([[1, 0, 0, 0]]), "opacity": np.zeros((1, 1)),
        "sh": np.zeros((1, 1, 3))}
    best = core.write_splat(root, arrays, 4, "best", {"psnr": 30})
    core.atomic_json(root / "best.json", best)
    return root


class FailurePaths(unittest.TestCase):
    def test_damaged_optional_render_details_never_stop_monitoring(self):
        with tempfile.TemporaryDirectory() as directory:
            root = previous_job(directory)
            core.atomic_json(root/'state.json', {'state':'rendering', 'supervisor_pid':os.getpid(), 'updated':time.time()})
            (root/'capture-render.json').write_text('{damaged', encoding='utf-8')
            observer = reader.Observer(root)
            try:
                for iteration in (1,2):
                    core.atomic_json(root/'runtime.json', {'state':'rendering','iteration':iteration})
                    deadline = time.monotonic()+4
                    while observer.status.get('iteration') != iteration and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertEqual(observer.status.get('iteration'), iteration, observer.error)
                    self.assertEqual(observer.status.get('capture_render'), {})
                    self.assertFalse(observer.error)
            finally:
                observer.close()
                observer.thread.join(4)

    def test_bad_camera_metadata_does_not_block_external_model_and_recovers(self):
        transport = importlib.import_module(package.__name__ + '.session_transport')
        geometry = importlib.import_module(package.__name__ + '.session_geometry')
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, LFS_TRAINING_SESSIONS_DIR=root):
            publisher = transport.SessionPublisher({})
            publisher.update(geometry={'path':'view_geometry.json', 'bytes':1, 'sha256':'bad'})
            payload = {'means':np.zeros((1,3),np.float32), 'scale':np.zeros((1,3),np.float32),
                       'rotation':np.array([[1,0,0,0]],np.float32), 'opacity':np.zeros((1,1),np.float32),
                       'sh':np.zeros((1,1,3),np.float32)}
            publisher.publish(payload, 1)
            observer = reader.Observer(publisher.root, external=True)
            frame = None
            try:
                deadline = time.monotonic()+5
                while frame is None and time.monotonic()<deadline:
                    frame = observer.take()
                    time.sleep(.01)
                self.assertIsNotNone(frame, observer.error)
                self.assertFalse(observer.error)
                self.assertTrue(observer.status['geometry_error'])
                self.assertEqual(frame['geometry_revision'], '')
                observer.acknowledge(frame['record']['id'])
                geometry.publish(publisher, {'schema':1, 'cameras':[]})
                observer.geometry_retry_at = 0
                deadline = time.monotonic()+5
                while (observer.geometry is None or observer.status.get('geometry_error')) and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertIsNotNone(observer.geometry)
                self.assertFalse(observer.status.get('geometry_error'))
            finally:
                observer.close()
                observer.thread.join(5)
                publisher.finish()
                publisher.pool.close()

    def test_resume_rebinds_execution_and_preserves_checkpoint_and_best(self):
        with tempfile.TemporaryDirectory() as directory:
            old = previous_job(directory)
            for metadata_backend in ("UNTRUSTED", ["alternate", "representation"]):
                value = core.read_json(old / "job.json")
                value["backend"] = metadata_backend
                core.atomic_json(old / "job.json", value)
                with patch.object(jobs.subprocess, "run") as run:
                    job, parent = jobs.resume_data(old, 16, 4, backend="TRUSTED_PREFERENCES", preview_options={"full_preview":False,"preview_sh_degree":0})
                run.assert_not_called()
                self.assertEqual(job["preview_sh_degree"], 0)
                self.assertEqual(job["sh_degree"], 0)
                self.assertEqual(job["backend"], "TRUSTED_PREFERENCES")
                self.assertEqual(core.training_command(job, "hook.py")[0], "TRUSTED_PREFERENCES")
                self.assertEqual(job["connection_directory"], "")
                self.assertEqual(parent, "previous")
                self.assertEqual(job["resume"], str(old / "output/project.licht"))
                best = core.read_json(Path(job["root"]) / "best.json")
                self.assertEqual(best["sha256"], core.read_json(old / "best.json")["sha256"])
                core.verify_artifact(job["root"], best)

    def test_resume_without_trusted_backend_fails_before_creating_a_job(self):
        with tempfile.TemporaryDirectory() as directory:
            old = previous_job(directory)
            for value in ("", "  ", None, ["binary"]):
                with self.assertRaises(ValueError):
                    jobs.resume_data(old, 16, 4, backend=value)
            self.assertEqual(list(Path(directory).iterdir()), [old])

    def test_resume_rolls_back_partial_copy_and_metadata_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            old = previous_job(directory)
            def broken_copy(src, dst):
                dst.write(b"partial")
                raise OSError("disk full")
            for target, effect in (("copy", broken_copy), ("metadata", OSError("disk full"))):
                context = patch.object(jobs.shutil, "copyfileobj", side_effect=effect) if target == "copy" else patch.object(jobs, "atomic_json", side_effect=effect)
                with context, self.assertRaises(OSError):
                    jobs.resume_data(old, 16, 4, backend="TRUSTED")
                self.assertEqual(list(Path(directory).iterdir()), [old])
                core.verify_artifact(old, core.read_json(old / "best.json"))

    def test_resume_and_model_creation_request_private_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            old = previous_job(directory)
            real_mkdir, real_open = Path.mkdir, os.open
            modes, files = [], []
            def mkdir(path, mode=0o777, parents=False, exist_ok=False):
                modes.append((path, mode))
                return real_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)
            def opening(path, flags, mode=0o777, **kw):
                files.append((Path(path), flags, mode))
                return real_open(path, flags, mode, **kw)
            with patch.object(Path, "mkdir", mkdir), patch.object(os, "open", opening):
                job, _ = jobs.resume_data(old, 16, 4, backend="TRUSTED")
            root = Path(job["root"])
            self.assertIn((root, 0o700), modes)
            self.assertIn((root / "artifacts", 0o700), modes)
            inherited = core.verify_artifact(root, core.read_json(root / "best.json"))
            self.assertTrue(any(p == inherited and mode == 0o600 and flags & os.O_EXCL for p, flags, mode in files))
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(inherited.stat().st_mode), 0o600)

    def test_new_model_file_requests_private_exclusive_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            real_open = os.open
            requests = []
            def opening(path, flags, mode=0o777, **kw):
                requests.append((Path(path), flags, mode))
                return real_open(path, flags, mode, **kw)
            with patch.object(os, "open", opening):
                root = previous_job(directory)
            partials = [(p, flags, mode) for p, flags, mode in requests if p.suffix == ".partial"]
            self.assertEqual(len(partials), 1)
            self.assertTrue(partials[0][1] & os.O_EXCL)
            self.assertEqual(partials[0][2], 0o600)
            self.assertFalse(list(root.rglob("*.partial")))

    def test_imported_job_rejects_bad_schema_and_path_types(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job.json"
            for value in ([], {"schema": True, "id": "a"}, {"schema": 1, "id": []},
                          {"schema": 1, "id": "a", "dataset_root": []}):
                core.atomic_json(path, value)
                with self.assertRaises(ValueError):
                    jobs.load_job(path)

    def test_supervisor_reaps_real_child_when_state_and_log_writes_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = previous_job(directory)
            job = core.read_json(root / "job.json")
            job.update(backend=sys.executable, resume=str(root / "output/project.licht"))
            core.atomic_json(root / "job.json", job)
            (root / "supervisor.log").mkdir()  # Make logging fail independently.
            spawn = subprocess.Popen
            children = []
            def start(*args, **kw):
                child = spawn([sys.executable, "-c", "import time; time.sleep(120)"],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                children.append(child)
                return child
            def writing(path, value):
                if children:
                    raise OSError("disk full")
                return core.atomic_json(path, value)
            try:
                with patch.object(supervisor.subprocess, "run", return_value=types.SimpleNamespace(returncode=0, stdout=b"LichtFeld", stderr=b"")), patch.object(supervisor.subprocess, "Popen", side_effect=start), patch.object(supervisor, "atomic_json", side_effect=writing):
                    supervisor.supervise(root / "job.json")
                self.assertEqual(len(children), 1)
                self.assertIsNotNone(children[0].poll())
                self.assertFalse((root / "supervisor.lock").exists())
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=10)

    def test_unresponsive_child_is_killed_and_reaped(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("trainer", 30), 0]
        supervisor.stop_child(child)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 2)

    def test_import_best_can_repeat_acknowledged_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = previous_job(directory)
            best = core.read_json(root / "best.json")
            core.atomic_json(root / "state.json", {"state": "completed", "result": best})
            observer = reader.Observer(root)
            def until(test):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    result = test()
                    if result:
                        return result
                    time.sleep(.01)
                self.fail("Reader timeout: " + observer.error)
            try:
                first = until(observer.take)
                observer.acknowledge(first["record"]["id"])
                until(lambda: observer.last == best["id"])
                self.assertIsNone(observer.take())
                observer.commands.put("import_best")
                second = until(observer.take)
                self.assertEqual(second["record"]["id"], best["id"])
                observer.acknowledge(best["id"])
                until(lambda: not observer.pending)
                time.sleep(.2)
                self.assertIsNone(observer.take())  # Automatic deduplication remains.
            finally:
                observer.close()
                observer.thread.join(timeout=5)  # Tests only; never UI code.
                self.assertFalse(observer.thread.is_alive())


class PreviewRegressions(unittest.TestCase):
    def test_settling_keeps_status_current_then_reads_latest_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = previous_job(directory)
            arrays = {"means": np.zeros((1,3)), "scale": np.zeros((1,3)),
                "rotation": np.array([[1,0,0,0]]), "opacity": np.zeros((1,1)), "sh": np.zeros((1,1,3))}
            frames = [core.write_splat(root,arrays,step,"preview") for step in (10,11,12)]
            core.atomic_json(root/"state.json",{"state":"training","updated":time.time()})
            def update(step,enabled=True):
                core.atomic_json(root/"runtime.json",{"state":"training","iteration":step,"preview":frames[step-10],"previews_enabled":enabled})
            update(10)
            observer = reader.Observer(root)
            def until(test):
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    value=test()
                    if value:return value
                    time.sleep(.005)
                self.fail("Settling timeout: "+observer.error)
            try:
                first=until(observer.take)
                observer.acknowledge(first["record"]["id"],display_seconds=.7)
                until(lambda:not observer.pending)
                update(11)
                until(lambda:observer.status.get("iteration")==11)
                update(12)
                until(lambda:observer.status.get("iteration")==12)
                self.assertIsNone(observer.take())
                latest=until(observer.take)
                self.assertEqual(latest["record"]["id"],frames[2]["id"])
                observer.acknowledge(latest["record"]["id"])
                until(lambda:not observer.pending)
                # Frozen previews ignore a newly evaluated model until resumed.
                update(12,False)
                until(lambda:not observer.status.get("previews_enabled",True))
                observer.commands.put("view_best")
                selected=until(observer.take)
                observer.acknowledge(selected["record"]["id"])
                until(lambda:not observer.pending)
                winner=core.write_splat(root,arrays,8,"best",{"psnr":40})
                core.atomic_json(root/"best.json",winner)
                until(lambda:observer.status.get("best",{}).get("id")==winner["id"])
                self.assertIsNone(observer.take())
                update(12,True)
                resumed=until(observer.take)
                self.assertEqual(resumed["record"]["id"],winner["id"])
            finally:
                observer.close()
                observer.thread.join(5)

    def test_best_view_follows_evaluations_until_current_is_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            root = previous_job(directory)
            arrays = {"means": np.zeros((1,3)), "scale": np.zeros((1,3)),
                "rotation": np.array([[1,0,0,0]]), "opacity": np.zeros((1,1)), "sh": np.zeros((1,1,3))}
            best = core.read_json(root / "best.json")
            live = core.write_splat(root, arrays, 10, "preview")
            core.atomic_json(root / "state.json", {"state":"training", "updated":time.time()})
            core.atomic_json(root / "runtime.json", {"state":"training", "iteration":10, "preview":live})
            observer = reader.Observer(root)
            def until(test):
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    value = test()
                    if value:return value
                    time.sleep(.01)
                self.fail("Viewer policy timeout: " + observer.error)
            def take(identifier):
                frame = until(observer.take)
                self.assertEqual(frame["record"]["id"], identifier)
                observer.acknowledge(identifier)
                until(lambda: not observer.pending)
            try:
                take(live["id"])
                observer.commands.put("view_best")
                take(best["id"])
                self.assertEqual(observer.status["viewer_mode"], "best")
                newer = core.write_splat(root, arrays, 11, "preview")
                core.atomic_json(root / "runtime.json", {"state":"training", "iteration":11, "preview":newer})
                until(lambda: observer.status.get("iteration") == 11)
                time.sleep(.15)
                self.assertIsNone(observer.take())
                winner = core.write_splat(root, arrays, 6, "best", {"psnr":35})
                core.atomic_json(root / "best.json", winner)
                take(winner["id"])
                observer.commands.put("view_current")
                take(newer["id"])
                self.assertEqual(observer.status["viewer_mode"], "current")
            finally:
                observer.close()
                observer.thread.join(5)

    def test_owned_status_is_published_only_when_complete(self):
        import threading
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "output").mkdir()
            (root / "output/project.licht").write_bytes(b"checkpoint")
            core.atomic_json(root / "job.json", {"id": "atomic-status", "iterations": 100})
            core.atomic_json(root / "state.json", {"state": "completed"})
            core.atomic_json(root / "runtime.json", {"iteration": 100})
            core.atomic_json(root / "best.json", {"iteration": 50})
            entered, release = threading.Event(), threading.Event()
            original = reader.read_json
            def delayed(path, default=None):
                if Path(path).name == "best.json":
                    entered.set()
                    release.wait(3)  # Worker-only deterministic scheduling barrier.
                return original(path, default)
            with patch.object(reader, "read_json", side_effect=delayed):
                observer = reader.Observer(root)
                try:
                    self.assertTrue(entered.wait(2))
                    self.assertEqual(observer.status, {"state": "connecting"})
                    release.set()
                    deadline = time.monotonic() + 2
                    while observer.status.get("state") != "completed" and time.monotonic() < deadline:
                        time.sleep(.005)
                    first = observer.status
                    self.assertTrue(first["checkpoint_available"])
                    self.assertEqual(first["best"]["iteration"], 50)
                    self.assertIn("control_available", first)
                    entered.clear()
                    release.clear()
                    self.assertTrue(entered.wait(2))
                    self.assertIs(observer.status, first)
                    self.assertTrue(observer.status["checkpoint_available"])
                    self.assertIn("pending_command", observer.status)
                finally:
                    release.set()
                    observer.close()
                    observer.thread.join(5)


    def test_owned_status_survives_transient_metadata_read_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core.atomic_json(root / "job.json", {"id": "stable", "iterations": 100})
            core.atomic_json(root / "state.json", {"state": "training", "updated": time.time()})
            core.atomic_json(root / "runtime.json", {"state": "paused", "iteration": 37})
            missed = False
            original = reader.read_json
            def read(path, default=None):
                if missed and Path(path).name in {"state.json", "runtime.json", "job.json"}:
                    return default
                return original(path, default)
            with patch.object(reader, "read_json", side_effect=read):
                observer = reader.Observer(root, False)
                try:
                    deadline = time.monotonic() + 3
                    while observer.status.get("iteration") != 37 and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertEqual(observer.status["state"], "paused")
                    missed = True
                    time.sleep(.35)
                    self.assertEqual(observer.status["state"], "paused")
                    self.assertEqual(observer.status["iteration"], 37)
                    self.assertEqual(observer.status["job"]["id"], "stable")
                finally:
                    observer.close()
                    observer.thread.join(5)

    def test_best_preview_preserves_full_geometry_and_sh_when_requested(self):
        # Load the hook class without initializing the real trainer or its callbacks.
        import ast
        tree = ast.parse((SOURCE / "backend_hook.py").read_text(encoding="utf-8"))
        node = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "TrainingBridge")
        write = Mock(return_value={"kind": "best"})
        spatial = importlib.import_module(package.__name__ + ".session_transport").spatial_indices
        namespace = {"write_splat": write, "spatial_indices": spatial, "MAX_BYTES": 2 * 1024**3,
                    "PreviewCapacityError": type("PreviewCapacityError", (ValueError,), {})}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "backend_hook.py", "exec"), namespace)
        bridge = namespace["TrainingBridge"].__new__(namespace["TrainingBridge"])
        bridge.root = Path("unused")
        bridge.session = Mock()
        arrays = {"means": np.arange(30).reshape(10, 3), "sh": np.zeros((10, 16, 3))}
        for full, degree, count, coefficients in ((True, 0, 10, 16), (False, 0, 3, 1), (False, 1, 3, 4), (False, 2, 3, 9), (False, 3, 3, 16)):
            bridge.job = {"full_preview": full, "preview_points": 3, "preview_sh_degree": degree}
            bridge.write_candidate(arrays, 42, "best", {"psnr": 30})
            published = bridge.session.publish.call_args.args[0]
            self.assertEqual(len(published["means"]), count)
            self.assertEqual(published["sh"].shape[1], coefficients)
            self.assertEqual(bridge.session.publish.call_args.args[1], 42)
            self.assertIs(write.call_args.args[1], arrays)
            self.assertEqual(write.call_args.args[1]["sh"].shape[1], 16)


class PreviewFailureIsolation(unittest.TestCase):
    def bridge(self):
        import ast
        from concurrent.futures import Future
        tree=ast.parse((SOURCE/"backend_hook.py").read_text(encoding="utf-8"))
        node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="TrainingBridge")
        namespace={"time":time,"np":np,"MAX_BYTES":2*1024**3,"lf":Mock(),
                   "write_splat":Mock(return_value={"kind":"best","path":"best.ply"}),
                   "spatial_indices":lambda positions,limit:np.arange(min(len(positions),limit)),
                   "PreviewCapacityError":type("PreviewCapacityError",(ValueError,),{})}
        exec(compile(ast.Module(body=[node],type_ignores=[]),"backend_hook.py","exec"),namespace)
        cls=namespace["TrainingBridge"];bridge=cls.__new__(cls)
        bridge.base_interval=.1;bridge.error="";bridge.preview_error="";bridge.root=Path("unused")
        bridge.pending=Future();bridge.pending.set_exception(OSError("viewer mapping unavailable"))
        return bridge,namespace

    def test_optional_preview_failure_keeps_training_alive_and_required_best_failure_surfaces(self):
        bridge,ns=self.bridge();bridge.pending_kind="preview"
        self.assertTrue(bridge.finish_writer())
        self.assertIsNone(bridge.pending)
        self.assertEqual(bridge.error,"")
        self.assertIn("viewer mapping",bridge.preview_error)
        self.assertGreaterEqual(bridge.interval,2)
        bridge,ns=self.bridge();bridge.pending_kind="best"
        with self.assertRaises(OSError):bridge.finish_writer()

    def test_full_preview_limit_is_checked_before_gpu_copy(self):
        bridge,ns=self.bridge()
        model=ns["lf"].get_scene().training_model.return_value
        model.num_points=10000000;model.has_deleted_mask.return_value=False;model.active_sh_degree=3
        with self.assertRaisesRegex(ValueError,"shared-memory limit"):
            bridge.capture(for_preview=True)
        ns["lf"].Tensor.from_numpy.assert_not_called()

    def test_preview_sh_uses_bounded_native_gather_and_old_backend_keeps_sh0(self):
        bridge,ns=self.bridge()
        class Tensor:
            def __init__(self, value): self.value=value
            def cuda(self): return self
            def cpu(self): return self
            def sync(self): pass
            def numpy(self): return self.value
            def index_select(self, axis, rows): return Tensor(self.value[rows.value])
        class Model:
            num_points=32;active_sh_degree=3
            has_deleted_mask=lambda self:False
            means_raw=Tensor(np.arange(96,dtype=np.float32).reshape(32,3))
            sh0_raw=Tensor(np.zeros((32,1,3),np.float32))
            scaling_raw=Tensor(np.zeros((32,3),np.float32))
            rotation_raw=Tensor(np.zeros((32,4),np.float32))
            opacity_raw=Tensor(np.zeros((32,1),np.float32))
            @property
            def shN_raw(self):raise AssertionError('Unbounded SH decode')
        model=Model()
        model.shN_rows=Mock(side_effect=lambda rows,degree:Tensor(np.ones((len(rows.value),(degree+1)**2-1,3),np.float32)))
        ns['lf'].get_scene.return_value.training_model.return_value=model
        ns['lf'].Tensor.from_numpy.side_effect=Tensor
        result=bridge.capture(limit=5,degree=1,for_preview=True)
        self.assertEqual(result['sh'].shape,(5,4,3))
        self.assertEqual(len(model.shN_rows.call_args.args[0].value),5)
        self.assertEqual(model.shN_rows.call_args.args[1],1)
        del model.shN_rows
        with self.assertRaisesRegex(RuntimeError,'Update LichtFeld'):
            bridge.capture(limit=5,degree=1,for_preview=True)
        self.assertEqual(bridge.capture(limit=5,degree=0,for_preview=True)['sh'].shape,(5,1,3))

    def test_full_preview_retries_at_configured_limited_budget_and_reports_quality(self):
        bridge,ns=self.bridge()
        bridge.job={"full_preview":True,"preview_points":1000,"preview_sh_degree":1}
        limited={"means":np.zeros((1000,3)),"sh":np.zeros((1000,4,3))}
        capacity=ns["PreviewCapacityError"]("too large")
        bridge.capture=Mock(side_effect=[capacity,limited])
        self.assertIs(bridge.capture_preview(),limited)
        self.assertEqual(bridge.capture.call_args_list[0].kwargs,{"for_preview":True})
        self.assertEqual(bridge.capture.call_args_list[1].args,(1000,1))
        self.assertTrue(bridge.preview_fallback)
        self.assertIn("up to 1,000 points, SH1",bridge.preview_note)
        self.assertIn("saved models retain full quality",bridge.preview_note)

    def test_best_model_stays_full_when_its_optional_shared_preview_is_limited(self):
        bridge,ns=self.bridge();bridge.job={"full_preview":True,"preview_points":2,"preview_sh_degree":0}
        bridge.session=Mock()
        ns["MAX_BYTES"]=200
        arrays={"means":np.zeros((5,3),dtype=np.float32),"scale":np.zeros((5,3),dtype=np.float32),
                "rotation":np.zeros((5,4),dtype=np.float32),"opacity":np.zeros((5,1),dtype=np.float32),
                "sh":np.zeros((5,16,3),dtype=np.float32)}
        record=bridge.write_candidate(arrays,42,"best",{"psnr":30})
        self.assertIs(ns["write_splat"].call_args.args[1],arrays)
        preview=bridge.session.publish.call_args.args[0]
        self.assertEqual(len(preview["means"]),2)
        self.assertEqual(preview["sh"].shape[1],1)
        self.assertTrue(record["preview_fallback"])
        self.assertIn("saved models retain full quality",record["preview_note"])

    def test_saved_best_survives_shared_preview_publication_failure(self):
        bridge,ns=self.bridge();bridge.job={"full_preview":True}
        bridge.session=Mock();bridge.session.publish.side_effect=OSError("mapping unavailable")
        arrays={"means":np.zeros((10,3)),"sh":np.zeros((10,16,3))}
        result=bridge.write_candidate(arrays,42,"best",{"psnr":30})
        self.assertEqual(result["path"],"best.ply")
        self.assertIn("mapping unavailable",result["preview_error"])
