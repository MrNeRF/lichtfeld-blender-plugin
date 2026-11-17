# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Portal subprocess boundary and worker cancellation."""
from contextlib import contextmanager
from dataclasses import dataclass
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import sys
from types import ModuleType, SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("portal_runtime_test",
    Path(__file__).resolve().parents[2] / "lichtfeld_training/portal_runtime.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PortalRuntimeTests(unittest.TestCase):
    def test_connection_bridge_uses_canonical_cancel_without_undoing_completion(self):
        bridge_spec = importlib.util.spec_from_file_location("portal_bridge_test",
            Path(__file__).resolve().parents[2] / "lichtfeld_training/portal_bridge.py")
        bridge = importlib.util.module_from_spec(bridge_spec)
        bridge_spec.loader.exec_module(bridge)
        @dataclass
        class Snapshot:
            linking: bool = False
            verification_uri_complete: str = ""
            error: str = ""
        def module_named(name, **values):
            result = ModuleType(name)
            result.__dict__.update(values)
            return result
        for scenario in ("resume", "cancel-resume", "cancel-approval", "completion-wins", "cancel-storage-failure"):
            with self.subTest(scenario=scenario):
                instances, threads = [], []
                class Account:
                    def __init__(self):
                        self.remaining = 0
                        self.started = self.canceled = self.enabled = False
                        instances.append(self)
                    def start_device_flow(self):
                        self.started = self.enabled = True
                        self.remaining = 0 if scenario == "completion-wins" else 2
                    def snapshot(self):
                        return Snapshot(linking=scenario == "cancel-approval" and self.started and not self.canceled,
                                        error='connection_storage_failed' if self.canceled and scenario == 'cancel-storage-failure' else '')
                    @property
                    def busy(self):
                        if scenario.startswith("cancel-"):
                            return not self.canceled
                        if self.remaining:
                            self.remaining -= 1
                            return True
                        return False
                    def cancel_device_flow(self):
                        self.canceled = True
                        self.enabled = scenario == 'cancel-storage-failure'
                    def sign_out(self, *, revoke=True):
                        pass
                class Gallery:
                    def download(self, *, reuse_completed=False):
                        pass
                modules = {
                    "lfs_plugins": module_named("lfs_plugins", __path__=[]),
                    "lfs_plugins.portal_account": module_named("lfs_plugins.portal_account", PortalAccountService=Account, _locked_sidecar=None),
                    "lfs_plugins.portal_gallery": module_named("lfs_plugins.portal_gallery", PortalGalleryClient=Gallery),
                    "lfs_plugins.portal_security": module_named("lfs_plugins.portal_security", checked_portal_url=None),
                    "lfs_plugins.private_directory": module_named("lfs_plugins.private_directory", mkdir_private=None),
                    "lfs_plugins.gallery_logging": module_named("lfs_plugins.gallery_logging", safe_text=str),
                }
                original_thread = threading.Thread
                def control_thread(*args, **kwargs):
                    thread = original_thread(*args, **kwargs)
                    threads.append(thread)
                    return thread
                read_fd, write_fd = os.pipe()
                with os.fdopen(read_fd, "r", encoding="utf-8") as reader, os.fdopen(write_fd, "w", encoding="utf-8") as writer:
                    writer.write('{"action":"connect"}\n')
                    if scenario != "resume":
                        writer.write('cancel\n')
                    writer.flush()
                    with patch.dict(sys.modules, modules), patch.object(sys, "stdin", reader), \
                            patch.object(bridge, "emit") as emit, patch.object(bridge.threading, "Thread", side_effect=control_thread):
                        try:
                            if scenario == 'cancel-storage-failure':
                                with self.assertRaises(SystemExit):
                                    bridge.main()
                            else:
                                bridge.main()
                        finally:
                            writer.close()
                            for thread in threads:
                                thread.join(timeout=2)
                                self.assertFalse(thread.is_alive())
                canceled = scenario.startswith("cancel-")
                self.assertEqual(instances[0].canceled, canceled)
                self.assertEqual(instances[0].enabled, not canceled or scenario == 'cancel-storage-failure')
                if scenario == 'cancel-storage-failure':
                    self.assertEqual(emit.call_args.args[0], 'error')
                    self.assertIn('could not be saved', emit.call_args.args[1])
                else:
                    self.assertEqual(emit.call_args.args, ("canceled" if canceled else "result", {}))

    def test_installed_runtime_returns_public_state_and_releases_child(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = module.Runtime(root, executable="selected-backend")
            account = {"email": "owner@example.test", "connected_since": "session", "signed_in": True}
            packets = [{"kind": "account", "value": account},
                       {"kind": "result", "value": {"rows": [{"id": "asset", "title": "Scène 日本語"}],
                                                     "identity": ["owner@example.test", "session"]}}]
            output = b"normal application log\n" + b"".join(
                b"LFS_PORTAL\t" + json.dumps(packet).encode() + b"\n" for packet in packets)
            child = Mock(stdin=io.BytesIO(), stdout=io.BytesIO(output))
            try:
                with patch.object(module.subprocess, "Popen", return_value=child) as launch:
                    runtime.start("refresh")
                    runtime.future.result(timeout=5)
                self.assertEqual(runtime.snapshot()["rows"][0]["title"], "Scène 日本語")
                self.assertEqual(runtime.snapshot()["account"].email, "owner@example.test")
                command = launch.call_args.args[0]
                self.assertEqual(command[:3], ["selected-backend", "--headless", "--python-script"])
                self.assertEqual(Path(command[3]).name, "portal_bridge.py")
                self.assertTrue(child.stdin.closed and child.stdout.closed)
                child.wait.assert_called_once_with(timeout=3)
                self.assertIsNone(runtime.portal_process)
            finally:
                runtime.close()

    def test_unsupported_installation_reports_failure_and_releases_child(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = module.Runtime(root, executable="older-backend")
            child = Mock(stdin=io.BytesIO(), stdout=io.BytesIO(b"Headless mode requires a dataset\n"))
            try:
                with patch.object(module.subprocess, "Popen", return_value=child):
                    runtime.start("initialize")
                    runtime.future.result(timeout=5)
                self.assertEqual(runtime.snapshot()["phase"], "failed")
                self.assertIn("Update the selected installation", runtime.snapshot()["message"])
                self.assertTrue(child.stdin.closed and child.stdout.closed)
            finally:
                runtime.close()

    def test_missing_installation_does_not_launch_a_client(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = module.Runtime(root)
            try:
                with patch.object(module.subprocess, "Popen") as launch:
                    runtime.start("initialize")
                    runtime.future.result(timeout=5)
                    launch.assert_not_called()
                self.assertEqual(runtime.snapshot()["phase"], "failed")
                self.assertIn("Settings", runtime.snapshot()["message"])
            finally:
                runtime.close()

    def test_stale_thumbnail_cannot_publish_during_new_refresh(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = module.Runtime(root)
            entered, release, listing, finish = (threading.Event() for _ in range(4))
            @contextmanager
            def remote(action, values, cancel):
                if action == "thumbnail":
                    entered.set()
                    release.wait(timeout=5)
                    yield {"path": "stale.png", "identity": ["owner", "session"]}
                else:
                    listing.set()
                    finish.wait(timeout=5)
                    yield {"rows": [], "identity": ["owner", "session"]}
            try:
                with patch.object(runtime, "_portal", remote):
                    self.assertTrue(runtime.start("thumbnail", "asset"))
                    self.assertTrue(entered.wait(timeout=5))
                    self.assertTrue(runtime.start("refresh"))
                    release.set()
                    self.assertTrue(listing.wait(timeout=5))
                    self.assertEqual(runtime.snapshot()["phase"], "refresh")
                    self.assertTrue(runtime.events.empty())
                    runtime.stop()
                    finish.set()
                    runtime.future.result(timeout=5)
                    self.assertEqual(runtime.snapshot()["phase"], "canceled")
                    self.assertTrue(runtime.events.empty())
            finally:
                release.set(); finish.set(); runtime.close()

    def test_canceled_connection_clears_the_code_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = module.Runtime(root)
            entered, release = threading.Event(), threading.Event()
            @contextmanager
            def remote(action, values, cancel):
                runtime.update(account=SimpleNamespace(signed_in=False, linking=True))
                entered.set()
                release.wait(timeout=5)
                if cancel.is_set():
                    raise InterruptedError("Connection canceled")
                yield {}
            try:
                with patch.object(runtime, "_portal", remote):
                    runtime.start("connect")
                    self.assertTrue(entered.wait(timeout=5))
                    runtime.stop()
                    release.set()
                    runtime.future.result(timeout=5)
                    self.assertEqual(runtime.snapshot()["phase"], "canceled")
                    self.assertIsNone(runtime.snapshot()["account"])
                    self.assertTrue(runtime.start("connect"))
                    runtime.future.result(timeout=5)
                    self.assertEqual(runtime.snapshot()["phase"], "idle")
            finally:
                release.set(); runtime.close()

    def test_connect_cancel_ack_and_concurrent_completion_keep_canonical_state(self):
        class Input(io.BytesIO):
            def close(self):
                self.received = self.getvalue()
                super().close()
        for terminal in ("canceled", "result"):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as root:
                runtime = module.Runtime(root, executable="backend")
                account = {"signed_in": terminal == "result", "linking": False}
                packets = [{"kind": "account", "value": account}, {"kind": terminal, "value": {}}]
                output = b"".join(b"LFS_PORTAL\t" + json.dumps(packet).encode() + b"\n" for packet in packets)
                child = Mock(stdin=Input(), stdout=io.BytesIO(output))
                child.poll.return_value = None
                def launch(*args, **kwargs):
                    runtime.cancel.set()
                    return child
                try:
                    with patch.object(module.subprocess, "Popen", side_effect=launch):
                        runtime.start("connect")
                        runtime.future.result(timeout=5)
                    self.assertIn(b"cancel\n", child.stdin.received)
                    self.assertEqual(runtime.snapshot()["phase"], "canceled" if terminal == "canceled" else "idle")
                    self.assertEqual(runtime.snapshot()["account"].signed_in, terminal == "result")
                    self.assertFalse(runtime.snapshot()["account"].linking)
                    child.terminate.assert_not_called()
                    child.kill.assert_not_called()
                finally:
                    runtime.close()

    def test_unresponsive_connect_is_not_reported_as_confirmed_cancel(self):
        for needs_kill in (False, True):
            with self.subTest(needs_kill=needs_kill), tempfile.TemporaryDirectory() as root:
                runtime = module.Runtime(root, executable="backend")
                released = threading.Event()
                class Output:
                    def __iter__(self):
                        released.wait(timeout=5)
                        return iter(())
                    def close(self):
                        released.set()
                child = Mock(stdin=io.BytesIO(), stdout=Output())
                child.poll.return_value = None
                if needs_kill:
                    child.wait.side_effect = [module.subprocess.TimeoutExpired("backend", 3),
                                             module.subprocess.TimeoutExpired("backend", 3), 0]
                    child.kill.side_effect = released.set
                else:
                    child.terminate.side_effect = released.set
                def launch(*args, **kwargs):
                    runtime.cancel.set()
                    runtime.update(account=SimpleNamespace(signed_in=False, linking=True))
                    return child
                try:
                    with patch.object(module.subprocess, "Popen", side_effect=launch), patch.object(module, "CONNECT_CANCEL_TIMEOUT", .01):
                        runtime.start("connect")
                        runtime.future.result(timeout=5)
                    self.assertEqual(runtime.snapshot()["phase"], "failed")
                    self.assertIn("not confirmed", runtime.snapshot()["message"])
                    self.assertIsNone(runtime.snapshot()["account"])
                    child.terminate.assert_called()
                    self.assertEqual(child.kill.called, needs_kill)
                    self.assertIsNone(runtime.portal_process)
                finally:
                    released.set(); runtime.close()

    def test_scene_invalidation_does_not_cancel_an_account_connection(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = module.Runtime(root)
            entered, release = threading.Event(), threading.Event()
            @contextmanager
            def remote(action, values, cancel):
                entered.set()
                release.wait(timeout=5)
                yield {}
            try:
                with patch.object(runtime, "_portal", remote):
                    runtime.start("connect")
                    self.assertTrue(entered.wait(timeout=5))
                    runtime.stop(cancel_login=False)
                    self.assertFalse(runtime.cancel.is_set())
                    release.set()
                    runtime.future.result(timeout=5)
                    self.assertEqual(runtime.snapshot()["phase"], "idle")
            finally:
                release.set(); runtime.close()

    def test_artifact_lease_stays_open_until_consumer_finishes(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = module.Runtime(root, executable="backend")
            packet = {"kind": "result", "value": {"project": "cached.licht"}}
            child = Mock(stdin=io.BytesIO(), stdout=io.BytesIO(b"LFS_PORTAL\t" + json.dumps(packet).encode() + b"\n"))
            try:
                with patch.object(module.subprocess, "Popen", return_value=child):
                    with runtime._portal("download", {}, threading.Event()) as response:
                        self.assertEqual(response["project"], "cached.licht")
                        self.assertFalse(child.stdin.closed)
                        child.wait.assert_not_called()
                    self.assertTrue(child.stdin.closed)
                    child.wait.assert_called_once()
            finally:
                runtime.close()
