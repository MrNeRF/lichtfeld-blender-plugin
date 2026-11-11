# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native API compatibility, geometry recovery and authoritative training outcomes."""
import ast
import importlib
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[2] / 'lichtfeld_training'
package = types.ModuleType('backend_compat_test_package')
package.__path__ = [str(SOURCE)]
sys.modules[package.__name__] = package
core = importlib.import_module(package.__name__ + '.bridge_core')
control = importlib.import_module(package.__name__ + '.job_control')
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
                     'lf': Mock(), 'metric_at': lambda *args: None, 'Path': Path,
                     'read_dataset_json': core.read_dataset_json, 'read_json': core.read_json,
                     'validate_command': control.validate_command, 'atomic_json': core.atomic_json,
                     'claim_training_start': control.claim_training_start, 'stop_requested': control.stop_requested}
        bootstrap = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'numerical_runtime')
        exec(compile(ast.Module(body=[bootstrap, node], type_ignores=[]), 'backend_hook.py', 'exec'), namespace)
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

    def test_queued_stop_is_consumed_before_the_first_step(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge, namespace = self.bridge()
            bridge.root = Path(directory)
            bridge.job = {'id': 'job'}
            bridge.command_id = ''
            bridge.paused = False
            bridge.commands = types.MethodType(type(bridge).commands, bridge)
            owner = control.Controller(bridge.root)
            try:
                self.assertTrue(owner.acquire())
                core.atomic_json(bridge.root / 'command.json', owner.command('job', 'stop'))
                bridge.on_start({'iteration': 0, 'iterations': 10, 'is_paused': False, 'num_gaussians': 2})
                self.assertTrue(bridge.stopped)
                self.assertTrue(bridge.api_checked)
                self.assertFalse(bridge.error)
                namespace['lf'].session.return_value.request_stop.assert_called_once()
                bridge.publish.assert_called_with('stopping')
            finally:
                owner.close()

    def test_runtime_preparation_cancels_through_its_package_owner(self):
        for case in ('supervisor_won', 'queued', 'installing', 'install_error'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _, namespace = self.bridge()
                owner = control.Controller(root)
                try:
                    self.assertTrue(owner.acquire())
                    if case == 'supervisor_won':
                        self.assertTrue(control.claim_training_start(root))
                    if case == 'queued':
                        core.atomic_json(root / 'command.json', owner.command('job', 'stop'))
                    def install(_lf, *, should_cancel):
                        if case == 'installing':
                            core.atomic_json(root / 'command.json', owner.command('job', 'stop'))
                            self.assertTrue(should_cancel())
                            # Cancellation stays latched while the package owner cleans up.
                            (root / 'command.json').unlink()
                            self.assertTrue(should_cancel())
                        raise RuntimeError('package operation ended')
                    namespace['ensure_numpy'] = Mock(side_effect=install)
                    expected = RuntimeError if case == 'install_error' else InterruptedError
                    with self.assertRaises(expected):
                        namespace['numerical_runtime']({'id': 'job', 'root': str(root)})
                    if case in {'supervisor_won', 'queued'}:
                        namespace['ensure_numpy'].assert_not_called()
                    else:
                        namespace['ensure_numpy'].assert_called_once()
                finally:
                    owner.close()

    def test_large_depth_sidecar_uses_dataset_bound_and_keeps_coverage_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'depths').mkdir()
            candidate = dict(valid=True, scale=1., shift=0., floor=.01, corr=.98, samples=4096)
            anchor = dict(candidate, model=1, depth=candidate, disparity=candidate)
            anchors = {f'view_{i:06d}.png': anchor.copy() for i in range(1000)}
            path = root / 'depths/depth_anchors.json'
            path.write_text(json.dumps({'anchors': anchors}, indent=2), encoding='utf-8')
            self.assertGreater(path.stat().st_size, 262144)
            core.atomic_json(root / 'manifest.json', {'views': 1000, 'validation': []})
            for active in (True, False):
                bridge, namespace = self.bridge()
                bridge.root = root
                bridge.job = {'use_depth_loss': True, 'dataset_root': str(root)}
                bridge.geometry_checked = True
                bridge.supervision_checked = False
                if not active:
                    path.write_text(json.dumps({'anchors': {}}, indent=2), encoding='utf-8')
                bridge.on_step({'iteration': 1, 'loss': .1, 'num_gaussians': 2,
                                'iterations': 4, 'is_paused': False})
                if active:
                    self.assertFalse(bridge.error)
                    self.assertEqual(bridge.supervision, 'Depth active: 1000 anchored views')
                    namespace['lf'].session.return_value.request_stop.assert_not_called()
                else:
                    self.assertIn('could not be anchored', bridge.error)
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
