# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch
SOURCE = Path(__file__).resolve().parents[2] / "lichtfeld_training"
sys.path.insert(0, str(SOURCE))
import job_control as control
import supervisor
from bridge_core import atomic_json, read_json
package = types.ModuleType("job_control_test_package")
package.__path__ = [str(SOURCE)]
sys.modules[package.__name__] = package
reader = importlib.import_module(package.__name__ + ".session_reader")
reader_control = importlib.import_module(package.__name__ + ".job_control")


def until(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.01)
    raise AssertionError("Controller timeout")


class JobControlTests(unittest.TestCase):
    def test_two_independent_controllers_are_exclusive_and_handoff(self):
        with tempfile.TemporaryDirectory() as root:
            first, second = control.Controller(root), control.Controller(root)
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                command = first.command("job", "pause")
                self.assertEqual(control.validate_command(root, "job", command), "pause")
                first.close()
                self.assertTrue(second.acquire())
                with self.assertRaisesRegex(ValueError, "control changed"):
                    control.validate_command(root, "job", command)
            finally:
                first.close()
                second.close()

    @unittest.skipUnless(os.name == "posix", "POSIX symlink fixture")
    def test_controller_does_not_follow_an_imported_lock_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "unrelated"
            target.write_bytes(b"untouched")
            (root / "controller.lock").symlink_to(target)
            owner = control.Controller(root)
            with self.assertRaises(OSError):
                owner.acquire()
            self.assertEqual(target.read_bytes(), b"untouched")
            self.assertIsNone(owner.fd)

    def test_dead_process_releases_kernel_lock(self):
        with tempfile.TemporaryDirectory() as root:
            script = ("import sys,time;sys.path.insert(0,sys.argv[1]);from job_control import Controller;"
                      "c=Controller(sys.argv[2]);assert c.acquire();print('READY',flush=True);time.sleep(60)")
            child = subprocess.Popen([sys.executable, "-c", script, str(SOURCE), root], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            contender = control.Controller(root)
            try:
                self.assertEqual(child.stdout.readline().strip(), b"READY")
                self.assertFalse(contender.acquire())
                child.kill()
                child.communicate(timeout=5)
                self.assertTrue(contender.acquire())
            finally:
                contender.close()
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)

    def test_expiry_owner_and_shape_are_checked_at_all_command_sinks(self):
        with tempfile.TemporaryDirectory() as root:
            owner = control.Controller(root)
            try:
                owner.acquire()
                command = owner.command("job", "stop")
                atomic_json(Path(root) / "command.json", command)
                self.assertTrue(supervisor.stop_requested(Path(root), "job"))
                for replacement in ({"expires": command["created"] - 1}, {"controller": "other"},
                                    {"action": []}, {"created": float("nan")}, {"created": True}, {"created": 10**1000},
                                    {"job_id": "other"}):
                    bad = {**command, **replacement}
                    with self.assertRaises(ValueError):
                        control.validate_command(root, "job", bad)
                atomic_json(Path(root) / "command.json", {**command, "expires": command["created"] - 1})
                self.assertFalse(supervisor.stop_requested(Path(root), "job"))
            finally:
                owner.close()

    def test_supervisor_cancel_and_script_handoff_have_one_winner(self):
        for case in ('cancel', 'handoff', 'expired', 'package_cancelled'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                job = {'id': 'job', 'root': str(root), 'backend': 'trusted', 'resume': 'checkpoint.licht'}
                atomic_json(root / 'job.json', job)
                checkpoint = root / 'checkpoint.licht'
                checkpoint.write_bytes(b'unchanged checkpoint')
                best = {'path': 'best.ply'}
                atomic_json(root / 'best.json', best)
                runtime = {'job_id': 'job', 'state': 'finalizing', 'terminal_state': 'stopped'}
                if case == 'package_cancelled':
                    runtime = {'job_id': 'job', 'state': 'stopped', 'message': 'Cancelled numerical runtime'}
                atomic_json(root / 'runtime.json', runtime)
                owner = control.Controller(root)
                try:
                    self.assertTrue(owner.acquire())
                    command = owner.command('job', 'stop')
                    if case == 'expired':
                        command['expires'] = command['created'] - 1
                    child = Mock(pid=123, returncode=1 if case == 'package_cancelled' else 0)
                    child.poll.side_effect = [None, None, 0] if case == 'cancel' else [None, 0, 0]
                    started = False
                    def launch(*args, **kwargs):
                        nonlocal started
                        started = True
                        atomic_json(root / 'command.json', command)
                        return child
                    def read_stop(path, job_id):
                        if started and case != 'cancel':
                            # The script wins exactly between the command read and
                            # the supervisor's attempted cancellation claim.
                            control.claim_training_start(root)
                        return control.stop_requested(path, job_id)
                    with patch.object(supervisor.subprocess, 'run', return_value=types.SimpleNamespace(
                            stdout=b'LichtFeld Studio', stderr=b'', returncode=0)), \
                         patch.object(supervisor.subprocess, 'Popen', side_effect=launch), \
                         patch.object(supervisor, 'training_config', return_value={}), \
                         patch.object(supervisor, 'training_command', return_value=['trusted']), \
                         patch.object(supervisor, 'verify_artifact'), \
                         patch.object(supervisor, 'stop_requested', side_effect=read_stop), \
                         patch.object(supervisor.time, 'sleep'):
                        supervisor.supervise(root / 'job.json')
                    state = read_json(root / 'state.json')
                    self.assertEqual(state['state'], 'stopped', state)
                    self.assertEqual(checkpoint.read_bytes(), b'unchanged checkpoint')
                    self.assertFalse((root / 'supervisor.lock').exists())
                    if case == 'cancel':
                        child.terminate.assert_called_once()
                        child.wait.assert_called_once()
                        self.assertIn('before training', state['message'])
                    else:
                        child.terminate.assert_not_called()
                        if case == 'package_cancelled':
                            self.assertEqual(state['message'], 'Cancelled numerical runtime')
                        else:
                            self.assertEqual(state['result'], best)
                finally:
                    owner.close()

    def test_observers_share_view_but_not_control_and_timeout_clears_backlog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_json(root / "job.json", {"id": "job"})
            atomic_json(root / "state.json", {"state": "training", "control_protocol": 1})
            atomic_json(root / "runtime.json", {"state": "training", "iteration": 1})
            with patch.object(reader_control, "COMMAND_SECONDS", .3):
                first, second = reader.Observer(root), reader.Observer(root)
                try:
                    until(lambda: first.status.get("control_available") != second.status.get("control_available")
                          and "control_available" in first.status and "control_available" in second.status)
                    owner, viewer = (first, second) if first.control_available else (second, first)
                    viewer.commands.put("pause")
                    until(lambda: viewer.command_error)
                    self.assertFalse((root / "command.json").exists())
                    owner.commands.put("pause")
                    command = until(lambda: read_json(root / "command.json"))
                    until(lambda: owner.command_pending)
                    owner.command_backlog.append("resume")
                    until(lambda: owner.command_error and owner.command_pending is None)
                    self.assertFalse(owner.command_backlog)
                    self.assertEqual(read_json(root / "command.json")["id"], command["id"])
                    with self.assertRaisesRegex(ValueError, "expired"):
                        reader_control.validate_command(root, "job", command)
                    owner.close()
                    owner.thread.join(5)
                    until(lambda: viewer.control_available)
                    viewer.commands.put("stop")
                    second_command = until(lambda: (value if (value := read_json(root / "command.json", {})).get("id", command["id"]) != command["id"] else None))
                    atomic_json(root / "runtime.json", {"state": "training", "command_id": second_command["id"]})
                    until(lambda: viewer.command_pending is None and not viewer.command_error)
                finally:
                    for observer in (first, second):
                        observer.close()
                        observer.thread.join(5)
                        self.assertFalse(observer.thread.is_alive())

    def test_external_reader_rejects_identity_and_protocol_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "expected"
            for value in ({"protocol": 2, "id": "expected", "pid": os.getpid()},
                          {"protocol": 1, "id": "wrong", "pid": os.getpid()}):
                atomic_json(root / "session.json", value)
                with patch.object(reader, "load_record") as load:
                    observer = reader.Observer(root, external=True)
                    try:
                        until(lambda: observer.error)
                        load.assert_not_called()
                    finally:
                        observer.close()
                        observer.thread.join(5)

    def test_repaired_session_metadata_clears_error_without_a_new_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "session"
            status = {"protocol": 1, "id": "session", "pid": os.getpid(),
                      "heartbeat": time.time(), "iteration": 1}
            atomic_json(root / "session.json", status)
            observer = reader.Observer(root, external=True)
            try:
                until(lambda: observer.status.get("id") == "session")
                observer.last = "already displayed"
                atomic_json(root / "session.json", {**status, "protocol": 2})
                until(lambda: observer.error)
                atomic_json(root / "session.json", status)
                until(lambda: not observer.error)
                self.assertEqual(observer.last, "already displayed")
            finally:
                observer.close()
                observer.thread.join(5)

    def test_old_control_protocol_remains_observable_without_executing_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_json(root / "job.json", {"id": "job"})
            atomic_json(root / "state.json", {"state": "training"})
            observer = reader.Observer(root)
            try:
                until(lambda: "control_available" in observer.status)
                self.assertFalse(observer.status["control_available"])
                self.assertIn("older job controls", observer.status["control_reason"])
                observer.commands.put("stop")
                until(lambda: observer.command_error)
                self.assertFalse((root / "command.json").exists())
            finally:
                observer.close()
                observer.thread.join(5)

    def test_pause_remains_available_during_import_but_not_for_another_controller(self):
        import workflow
        self.assertTrue(workflow.actions("training", busy=True)["pause"])
        for action in ("pause", "stop", "previews"):
            self.assertFalse(workflow.actions("training", control=False)[action])


if __name__ == "__main__":
    unittest.main()
