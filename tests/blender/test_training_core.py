# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU contracts for job isolation, exact candidate selection and PLY payloads."""

import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import os
import sys
import types

import numpy as np

SOURCE = Path(__file__).resolve().parents[2] / "lichtfeld_training/bridge_core.py"
sys.path.insert(0, str(SOURCE.parent))
spec = importlib.util.spec_from_file_location("training_core", SOURCE)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class TrainingCoreTest(unittest.TestCase):
    def test_candidate_must_match_iteration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.csv"
            path.write_text("iteration,psnr,ssim\n100,30,0.9\n200,29,0.8\n")
            self.assertIsNone(core.metric_at(path, 201))
            self.assertEqual(core.metric_at(path, 100)["psnr"], 30)

    def test_best_is_not_necessarily_latest_and_ties_are_deterministic(self):
        best = {"psnr": 30, "count": 1000, "iteration": 100}
        self.assertFalse(core.better({**best, "psnr": 29, "iteration": 200}, best))
        self.assertTrue(core.better({**best, "count": 900}, best))
        self.assertFalse(core.better({**best, "psnr": float("nan")}, best))

    def test_artifact_integrity_and_path_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                core.artifact_path(root, "../other.ply")
            path = root / "model.ply"
            path.write_bytes(b"complete")
            record = {"path": "model.ply", "bytes": 8, "sha256": core.digest(path)}
            self.assertEqual(core.verify_artifact(root, record), path.resolve())
            path.write_bytes(b"corrupt!")
            with self.assertRaises(ValueError):
                core.verify_artifact(root, record)

    def test_atomic_state_never_accepts_nan(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            core.atomic_json(path, {"state": "ready"})
            with self.assertRaises(ValueError):
                core.atomic_json(path, {"loss": float("nan")})
            self.assertEqual(core.read_json(path), {"state": "ready"})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_ply_preserves_channel_major_sh_and_raw_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            arrays = {"means": np.array([[1, 2, 3]], dtype=np.float32),
                      "scale": np.array([[-1, -2, -3]], dtype=np.float32),
                      "rotation": np.array([[1, 0, 0, 0]], dtype=np.float32),
                      "opacity": np.array([[0.25]], dtype=np.float32),
                      "sh": np.arange(12, dtype=np.float32).reshape(1, 4, 3)}
            record = core.write_splat(directory, arrays, 100, "best", {"psnr": 30})
            path = core.verify_artifact(directory, record)
            data = path.read_bytes().split(b"end_header\n", 1)[1]
            values = struct.unpack("<26f", data)
            self.assertEqual(values[:3], (1, 2, 3))
            self.assertEqual(values[6:9], (0, 1, 2))
            self.assertEqual(values[9:18], (3, 6, 9, 4, 7, 10, 5, 8, 11))
            self.assertEqual(values[18:], (0.25, -1, -2, -3, 1, 0, 0, 0))

    def test_schedule_always_evaluates_terminal_and_command_is_shell_free(self):
        self.assertEqual(core.evaluation_steps(150, 100), [100, 150])
        job = {"root": "D:/jobs/a folder", "backend": "D:/Licht Feld/app.exe"}
        args = core.training_command(job, "a hook.py")
        self.assertEqual(args[0], job["backend"])
        self.assertIn("D:/jobs/a folder/dataset", [value.replace("\\", "/") for value in args])
        self.assertIn("--python-script", args)
        self.assertEqual(args[args.index("--centralize") + 1], "off")
        job["test_every"] = 4
        args = core.training_command(job, "a hook.py")
        self.assertEqual(args[args.index("--test-every") + 1], "4")

    def test_invalid_geometry_never_publishes_a_partial_artifact(self):
        arrays = {"means": np.array([[float("nan"), 0, 0]]),
                  "scale": np.zeros((1, 3)), "rotation": np.array([[1, 0, 0, 0]]),
                  "opacity": np.zeros((1, 1)), "sh": np.zeros((1, 1, 3))}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                core.write_splat(directory, arrays, 1, "preview")
            self.assertEqual(list(Path(directory).rglob("*.ply")), [])

    def test_full_profile_disables_appearance_and_reserves_validation(self):
        job = {"iterations": 123, "eval_interval": 40, "max_points": 1000,
               "sh_degree": 1, "test_every": 4, "scope": "SELECTION"}
        config = core.training_config(job)
        self.assertIn("means_lr", config["optimization"])
        self.assertEqual(config["optimization"]["eval_steps"], [40, 80, 120, 123])
        self.assertEqual(config["optimization"]["mask_mode"], "alpha_consistent")
        self.assertFalse(config["optimization"]["use_ppisp"])
        self.assertFalse(config["optimization"]["eval_all"])

    def test_hook_captures_exact_model_and_retains_earlier_best(self):
        class Tensor:
            def __init__(self, array): self.array = np.asarray(array)
            def cuda(self): return self
            def cpu(self): return self
            def sync(self): pass
            def index_select(self, dim, indices): return Tensor(np.take(self.array, indices.array, axis=dim))
            def numpy(self): return self.array.copy()

        model = types.SimpleNamespace(
            num_points=2, active_sh_degree=0, has_deleted_mask=lambda: False,
            means_raw=Tensor([[4., 0, 0], [4., 1, 0]]), scaling_raw=Tensor(np.zeros((2, 3))),
            rotation_raw=Tensor([[1., 0, 0, 0], [1., 0, 0, 0]]),
            opacity_raw=Tensor(np.zeros((2, 1))), sh0_raw=Tensor(np.zeros((2, 1, 3))))
        fake = types.ModuleType("lichtfeld")
        fake.ScopedHandler = type('Hooks', (), {name: lambda self, cb: None for name in
                                             ('on_training_start', 'on_post_step', 'on_training_end')})
        fake.scene = types.SimpleNamespace(SplatData=types.SimpleNamespace(shN_rows=lambda *args: None),
            SceneNode=types.SimpleNamespace(camera_center_x=0, camera_center_y=0))
        fake.get_scene = lambda: types.SimpleNamespace(training_model=lambda: model)
        fake.Tensor = types.SimpleNamespace(from_numpy=Tensor)
        fake.session = lambda: types.SimpleNamespace(request_stop=lambda: None)
        prior = sys.modules.get("lichtfeld")
        old_job = os.environ.get("LFS_BLENDER_JOB")
        old_registry = os.environ.get("LFS_TRAINING_SESSIONS_DIR")
        bridge = None
        try:
            sys.modules["lichtfeld"] = fake
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                os.environ["LFS_TRAINING_SESSIONS_DIR"] = str(root / "sessions")
                (root / "output").mkdir()
                core.atomic_json(root / "job.json", {
                    "id": "test", "root": str(root), "iterations": 8, "eval_interval": 4,
                    "max_points": 1000, "sh_degree": 0, "test_every": 4,
                    "preview_seconds": 15, "preview_points": 1000})
                os.environ["LFS_BLENDER_JOB"] = str(root / "job.json")
                spec = importlib.util.spec_from_file_location("training_hook_test", SOURCE.with_name("backend_hook.py"))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                bridge = module._bridge
                metrics = root / "output/metrics.csv"
                metrics.write_text("iteration,psnr,ssim\n4,30,0.9\n")
                bridge.on_step({"iteration": 4, "loss": 1, "num_gaussians": 2, "iterations": 8, "is_paused": False})
                model.means_raw.array[:] = 8
                metrics.write_text("iteration,psnr,ssim\n4,30,0.9\n8,29,0.8\n")
                bridge.on_step({"iteration": 8, "loss": 1, "num_gaussians": 2, "iterations": 8, "is_paused": False})
                bridge.finish_writer(wait=True)
                record = core.read_json(root / "best.json")
                self.assertEqual(record["iteration"], 4)
                data = core.verify_artifact(root, record).read_bytes().split(b"end_header\n", 1)[1]
                self.assertEqual(struct.unpack_from("<f", data)[0], 4)
                records = []
                for index in range(3):
                    relative = f"artifacts/preview-{index}.ply"
                    (root / relative).write_bytes(b"old-preview")
                    records.append({"id": str(index), "path": relative})
                bridge.preview_history = records
                core.atomic_json(root / "ack.json", {"id": "2"})
                bridge.cleanup_previews()
                self.assertEqual(bridge.preview_history, [records[2]])
                self.assertFalse((root / records[0]["path"]).exists())
                bridge.writer.shutdown(wait=True)
                bridge.session.finish()
                bridge.session.pool.close()
                bridge = None
        finally:
            if bridge:
                bridge.writer.shutdown(wait=True)
                bridge.session.finish()
                bridge.session.pool.close()
            if prior is None:
                sys.modules.pop("lichtfeld", None)
            else:
                sys.modules["lichtfeld"] = prior
            if old_job is None:
                os.environ.pop("LFS_BLENDER_JOB", None)
            else:
                os.environ["LFS_BLENDER_JOB"] = old_job
            if old_registry is None:
                os.environ.pop("LFS_TRAINING_SESSIONS_DIR", None)
            else:
                os.environ["LFS_TRAINING_SESSIONS_DIR"] = old_registry


if __name__ == "__main__":
    unittest.main()


class OptimizerConfigurationTest(unittest.TestCase):
    def test_visible_settings_and_schedule_survive_config(self):
        base={'iterations':1000,'eval_interval':250,'max_points':10000,'sh_degree':2,'test_every':4,
              'optimizer':{'means_lr':.001,'use_exposure_correction':True,'keep_all_checkpoints':True},
              'optimizer_schedule':{'refine_start':.1,'refine_end':.8,'growth_end':.6},
              'strategy':'mcmc','use_depth_loss':True,'use_normal_loss':True}
        value=core.training_config(base)['optimization']
        self.assertEqual(value['strategy'],'mcmc')
        self.assertEqual(value['means_lr'],.001)
        self.assertTrue(value['use_exposure_correction'])
        self.assertTrue(value['keep_all_checkpoints'])
        self.assertEqual((value['start_refine'],value['stop_refine'],value['grow_until_iter']),(100,800,600))
        self.assertEqual(value['normal_loss_space'],'camera-opencv')
        self.assertEqual(value['depth_loss_mode'],'ssi-depth')
        self.assertFalse(value['depth_auto_generate'])
        resumed=core.training_config({**base,'iterations':2000})['optimization']
        self.assertEqual(resumed['stop_refine'],1600)
