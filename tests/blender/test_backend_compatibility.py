# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native API compatibility, geometry recovery and authoritative training outcomes."""
import ast
import importlib
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[2] / 'lichtfeld_training'
package = types.ModuleType('backend_compat_test_package')
package.__path__ = [str(SOURCE)]
sys.modules[package.__name__] = package
core = importlib.import_module(package.__name__ + '.bridge_core')
geometry = importlib.import_module('lichtfeld_sessions.geometry')


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
        bridge.next_control_check = 0.0
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
        value = {'schema': 1, 'source_frame': 'blender-world',
                 'model_to_data': [[2, 0, 0, 3], [0, 3, 0, 4], [0, 0, 4, 5], [0, 0, 0, 1]],
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
        bridge.root = Path('unused')
        bridge.best = {'path': 'retained.ply'}
        bridge.writer = Mock()
        bridge.on_end({'iteration': 4, 'terminal_state': 'completed'})
        presentation = bridge.session.finish.call_args.kwargs['result']['presentation']
        self.assertEqual(presentation['model_to_data'], value['model_to_data'])
        self.assertEqual(presentation['data_to_source'], value['data_to_source'])

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
            bridge.session.finish.assert_called_once()
            self.assertEqual(bridge.session.finish.call_args.args, (expected,))
            result = bridge.session.finish.call_args.kwargs['result']
            self.assertEqual(result['path'], bridge.best['path'])
            self.assertEqual(Path(result['root']), bridge.root.resolve())
            self.assertEqual(result['presentation']['schema'], 1)
            if outcome in (None, 'unknown'):
                self.assertIn('terminal_state', bridge.error)
            bridge.writer.shutdown.assert_called_once_with(wait=True)

    def test_final_write_failure_still_publishes_retained_best(self):
        bridge, namespace = self.bridge()
        bridge.root = Path('unused')
        bridge.best = {'path': 'retained.ply'}
        bridge.writer = Mock()
        bridge.finish_writer.side_effect = OSError('Final write failed')
        bridge.on_end({'iteration': 4, 'terminal_state': 'completed'})
        bridge.session.finish.assert_called_once()
        self.assertEqual(bridge.session.finish.call_args.args, ('failed',))
        self.assertEqual(bridge.session.finish.call_args.kwargs['result']['path'], 'retained.ply')
        self.assertEqual(bridge.session.finish.call_args.kwargs['error'], 'Final write failed')
