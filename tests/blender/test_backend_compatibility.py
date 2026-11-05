# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal native APIs, scene geometry and authoritative training outcomes."""
import ast
import importlib
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np

SOURCE = Path(__file__).resolve().parents[2] / 'lichtfeld_training'
package = types.ModuleType('backend_compat_test_package')
package.__path__ = [str(SOURCE)]
sys.modules[package.__name__] = package
core = importlib.import_module(package.__name__ + '.bridge_core')
geometry = importlib.import_module('lichtfeld_sessions.geometry')


class Tensor:
    def __init__(self, data): self.data = np.asarray(data)
    def cpu(self): return self
    def numpy(self): return self.data.copy()


class NativeCompatibility(unittest.TestCase):
    def test_module_capabilities_are_required_not_version_text(self):
        hooks = type('Hooks', (), {name: lambda self, callback: None for name in
                                 ('on_training_start', 'on_post_step', 'on_training_end')})
        module = types.SimpleNamespace(get_scene=lambda: None, ScopedHandler=hooks,
            scene=types.SimpleNamespace(SplatData=types.SimpleNamespace(shN_rows=lambda *args: None),
                SceneNode=types.SimpleNamespace(camera_center_x=0, camera_center_y=0)))
        core.require_backend_api(module)
        del module.scene.SceneNode.camera_center_x
        with self.assertRaisesRegex(RuntimeError, 'SceneNode.camera_center_x'):
            core.require_backend_api(module)
        module.scene.SceneNode.camera_center_x = 0
        module.scene.SplatData = object()
        with self.assertRaisesRegex(RuntimeError, 'SplatData.shN_rows'):
            core.require_backend_api(module)
        with self.assertRaisesRegex(RuntimeError, 'ScopedHandler.on_training_start'):
            core.require_backend_api(types.SimpleNamespace())

    def test_native_pose_and_camera_calibration_without_studio_modules(self):
        model = np.eye(4); model[:3, 3] = [2, -3, 4]
        camera_world = np.eye(4); camera_world[:3, 3] = [1, 2, 3]
        rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
        translation = np.array([3, 4, 5])
        camera = types.SimpleNamespace(has_camera=True, name='Camera', world_transform=camera_world,
            camera_R=Tensor(rotation), camera_T=Tensor(translation), camera_focal_x=50.,
            camera_focal_y=60., camera_center_x=31., camera_center_y=23., image_path='C:\\dataset\\images\\capture.png', camera_width=64, camera_height=48)
        scene = types.SimpleNamespace(training_model_node_name='model',
            get_node=lambda name: types.SimpleNamespace(world_transform=model),
            get_active_cameras=lambda: [types.SimpleNamespace(has_camera=False), camera])
        result = geometry.from_native_scene(scene)
        np.testing.assert_array_equal(result['model_to_data'], model)
        expected = np.eye(4); expected[:3, :3] = rotation.T; expected[:3, 3] = -rotation.T @ translation
        np.testing.assert_array_equal(result['cameras'][0]['camera_to_data'], camera_world @ expected)
        self.assertEqual(result['cameras'][0]['fy'], 60.)
        self.assertEqual(result['cameras'][0]['height'], 48)
        self.assertEqual(result['cameras'][0]['cx'], 31.)
        self.assertEqual(result['cameras'][0]['cy'], 23.)
        camera.camera_focal_x = float('nan')
        with self.assertRaisesRegex(ValueError, 'intrinsics'):
            geometry.from_native_scene(scene)

    def test_supervisor_uses_native_outcome_instead_of_last_command(self):
        for outcome, action in [('stopped', 'pause'), ('completed', 'stop')]:
            self.assertEqual(core.verified_training_outcome({'terminal_state': outcome, 'action': action}), outcome)
        for outcome in (None, 'unknown', 'failed', 'training'):
            with self.assertRaisesRegex(RuntimeError, 'verified terminal'):
                core.verified_training_outcome({'terminal_state': outcome})

    def bridge(self):
        tree = ast.parse((SOURCE / 'backend_hook.py').read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'TrainingBridge')
        namespace = {'time': time, 'math': __import__('math'), 'session_geometry': geometry,
                     'lf': Mock(), 'metric_at': lambda *args: None}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'backend_hook.py', 'exec'), namespace)
        bridge = namespace['TrainingBridge'].__new__(namespace['TrainingBridge'])
        bridge.api_checked = False
        bridge.geometry_checked = False
        bridge.geometry_retry_at = 0
        bridge.view_geometry = {'source_frame': 'blender-world', 'data_to_source': geometry.FLIP,
                                'cameras': []}
        bridge.supervision_checked = True
        bridge.session = Mock()
        bridge.session.stop.is_set.return_value = True
        bridge.finish_writer = Mock(return_value=False)
        bridge.cleanup_previews = Mock()
        bridge.commands = Mock()
        bridge.eval_steps = set()
        bridge.previews_enabled = False
        bridge.pending = None
        bridge.last_status = time.monotonic()
        bridge.stopped = False
        bridge.error = ''
        bridge.publish = Mock()
        return bridge, namespace

    def test_geometry_retry_recovers_and_clears_error(self):
        bridge, namespace = self.bridge()
        value = {'schema': 1, 'source_frame': 'blender-world', 'model_to_data': geometry.IDENTITY,
                 'data_to_source': geometry.FLIP, 'cameras': []}
        hook = {'iteration': 1, 'loss': .1, 'num_gaussians': 2, 'iterations': 4, 'is_paused': False}
        with patch.object(geometry, 'from_native_scene', side_effect=[ValueError('temporary'), value]), patch.object(geometry, 'publish') as publish:
            bridge.on_step(hook)
            self.assertFalse(bridge.geometry_checked)
            bridge.geometry_retry_at = 0
            bridge.on_step(hook)
            self.assertTrue(bridge.geometry_checked)
            self.assertEqual(publish.call_args.args[1]['source_frame'], 'blender-world')
            bridge.session.update.assert_called_with(geometry_error='')
            namespace['lf'].session.return_value.request_stop.assert_not_called()

    def test_missing_hook_fields_fail_before_training_steps(self):
        bridge, namespace = self.bridge()
        bridge.on_start({'iteration': 0})
        self.assertIn('iterations and is_paused', bridge.error)
        bridge.publish.assert_called_with('failed')
        namespace['lf'].session.return_value.request_stop.assert_called_once()

    def test_unknown_terminal_state_preserves_result_and_reports_failure(self):
        for outcome in (None, 'unknown', 'completed', 'stopped', 'failed'):
            bridge, namespace = self.bridge()
            bridge.root = Path('unused')
            bridge.best = {'path': 'retained.ply'}
            bridge.writer = Mock()
            bridge.on_end({'iteration': 4, 'terminal_state': outcome})
            expected = outcome if outcome in {'completed', 'stopped', 'failed'} else 'failed'
            bridge.session.finish.assert_called_once_with(expected, result=bridge.best)
            if outcome in (None, 'unknown'):
                self.assertIn('terminal_state', bridge.error)
            bridge.writer.shutdown.assert_called_once_with(wait=True)
