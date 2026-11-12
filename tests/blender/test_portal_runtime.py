# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Portal worker lifecycle: stale thumbnails cannot overwrite newer actions."""
import importlib.util
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("portal_runtime_test",
    Path(__file__).resolve().parents[2] / "lichtfeld_training/portal_runtime.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PortalRuntimeTests(unittest.TestCase):
    def test_account_reopens_existing_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = module.Runtime(root)
            try:
                account = runtime._account()
                self.assertIs(runtime._account(), account)
                self.assertFalse(account.snapshot().signed_in)
            finally:
                runtime.close()

    def account(self):
        snapshot = SimpleNamespace(email="owner@example.test", connected_since="session", signed_in=True, linking=False)
        return SimpleNamespace(sync_profile=lambda: True, cancel_device_flow=lambda: None,
                               snapshot=lambda: snapshot, base_url="https://portal.lichtfeld.io")

    def test_refresh_validates_ids_and_keeps_unicode_titles(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = module.Runtime(root)
            runtime.account = self.account()
            rows = [{"id": "ef4cb85d-b868-4e51-a0de-bac3bfa269ed", "title": "Scène 日本語"}]
            with patch.object(runtime, "_client", return_value=SimpleNamespace(list_scenes=lambda **_: rows,
                               expected_session=("owner@example.test", "session"))):
                self.assertTrue(runtime.start("refresh"))
                runtime.future.result(timeout=5)
                self.assertEqual(runtime.snapshot()["rows"][0]["title"], "Scène 日本語")
                rows[0]["id"] = "../outside"
                self.assertTrue(runtime.start("refresh"))
                runtime.future.result(timeout=5)
                self.assertEqual(runtime.snapshot()["phase"], "failed")
            runtime.close()

    def test_stale_thumbnail_cannot_clear_refresh_phase(self):
        with tempfile.TemporaryDirectory() as root:
            entered, release, listing, finish = (threading.Event() for _ in range(4))
            runtime = module.Runtime(root)
            runtime.account = self.account()

            def thumbnail(*_):
                entered.set()
                release.wait(timeout=5)
                return 200, "tag", b"not published after cancellation"

            def scenes(**_):
                listing.set()
                finish.wait(timeout=5)
                return []

            try:
                with patch.object(runtime, "_client", return_value=SimpleNamespace(thumbnail=thumbnail, list_scenes=scenes,
                                  expected_session=("owner@example.test", "session"))):
                    self.assertTrue(runtime.start("thumbnail", "ef4cb85d-b868-4e51-a0de-bac3bfa269ed"))
                    self.assertTrue(entered.wait(timeout=5))
                    self.assertTrue(runtime.start("refresh"))
                    release.set()
                    self.assertTrue(listing.wait(timeout=5))
                    self.assertEqual(runtime.snapshot()["phase"], "refresh")
                    self.assertTrue(runtime.events.empty())
                    runtime.stop()
                    self.assertFalse(runtime.start("refresh"))
                    finish.set()
                    runtime.future.result(timeout=5)
                    self.assertEqual(runtime.snapshot()["phase"], "canceled")
            finally:
                release.set()
                finish.set()
                runtime.close()

    def test_account_change_cannot_publish_the_previous_gallery(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = module.Runtime(root)
            runtime.account = self.account()

            def scenes(**_):
                runtime.account.snapshot().email = "different@example.test"
                return [{"id": "ef4cb85d-b868-4e51-a0de-bac3bfa269ed", "title": "Previous account's asset"}]

            try:
                with patch.object(runtime, "_client", return_value=SimpleNamespace(list_scenes=scenes,
                                  expected_session=("owner@example.test", "session"))):
                    runtime.start("refresh")
                    runtime.future.result(timeout=5)
                    self.assertEqual(runtime.snapshot()["rows"], ())
                    self.assertEqual(runtime.snapshot()["phase"], "failed")
            finally:
                runtime.close()
