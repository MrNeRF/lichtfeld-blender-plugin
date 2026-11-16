# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Portal subprocess boundary and worker cancellation."""
from contextlib import contextmanager
from dataclasses import dataclass
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import sys
from types import ModuleType
import threading
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("portal_runtime_test",
    Path(__file__).resolve().parents[2] / "lichtfeld_training/portal_runtime.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PortalRuntimeTests(unittest.TestCase):
    def test_resumed_account_finishes_profile_sync_before_connect_returns(self):
        bridge_spec = importlib.util.spec_from_file_location("portal_bridge_test",
            Path(__file__).resolve().parents[2] / "lichtfeld_training/portal_bridge.py")
        bridge = importlib.util.module_from_spec(bridge_spec)
        bridge_spec.loader.exec_module(bridge)
        @dataclass
        class Snapshot:
            linking: bool = False
            verification_uri_complete: str = ""
        instances = []
        class Account:
            def __init__(self):
                self.remaining = 0
                self.canceled = False
                instances.append(self)
            def start_device_flow(self):
                self.remaining = 2
            def snapshot(self):
                return Snapshot()
            @property
            def busy(self):
                if self.remaining:
                    self.remaining -= 1
                    return True
                return False
            def cancel_device_flow(self):
                self.canceled = True
            def sign_out(self, *, revoke=True):
                pass
        class Gallery:
            def download(self, *, reuse_completed=False):
                pass
        def module_named(name, **values):
            result = ModuleType(name)
            result.__dict__.update(values)
            return result
        modules = {
            "lfs_plugins": module_named("lfs_plugins", __path__=[]),
            "lfs_plugins.portal_account": module_named("lfs_plugins.portal_account", PortalAccountService=Account, _locked_sidecar=None),
            "lfs_plugins.portal_gallery": module_named("lfs_plugins.portal_gallery", PortalGalleryClient=Gallery),
            "lfs_plugins.portal_security": module_named("lfs_plugins.portal_security", checked_portal_url=None),
            "lfs_plugins.private_directory": module_named("lfs_plugins.private_directory", mkdir_private=None),
            "lfs_plugins.gallery_logging": module_named("lfs_plugins.gallery_logging", safe_text=str),
        }
        with patch.dict(sys.modules, modules), patch.object(sys, "stdin", io.StringIO('{"action":"connect"}\n')), \
                patch.object(bridge, "emit") as emit, patch.object(bridge.time, "sleep"):
            bridge.main()
        self.assertEqual(instances[0].remaining, 0)
        self.assertFalse(instances[0].canceled)
        self.assertEqual(emit.call_args.args, ("result", {}))

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
