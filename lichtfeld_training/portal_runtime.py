# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Portal I/O and materialization. This module must never import bpy."""
from concurrent.futures import Future
from contextlib import contextmanager
import atexit
from _lfs_path_io import Path, TemporaryDirectory, native_path
from types import SimpleNamespace
import json
import queue
import subprocess
import tempfile
import threading
import time


PROJECT_IMPORT_CAPABILITIES = {b"visible-project-splats-v1"}


class Runtime:
    def __init__(self, directory, cache_directory=None, *, executable=''):
        self.directory = Path(directory)
        self.cache_directory = Path(cache_directory) if cache_directory is not None else self.directory / "cache"
        self.tasks = queue.Queue(maxsize=1)
        self.process = None
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.events = queue.SimpleQueue()
        self.executable = executable
        self.portal_process = None
        self.closed = False
        self.future = None
        self.serial = 0
        self.state = {"phase": "idle", "message": "", "progress": 0.0, "rows": (), "generation": 0, "revision": 0}
        self.thread = threading.Thread(target=self._work, daemon=True, name="lichtfeld-portal")
        self.thread.start()
        atexit.register(self._exit_process)

    def _work(self):
        while True:
            task = self.tasks.get()
            if task is None:
                return
            future, action, arguments, cancel, serial = task
            if not future.set_running_or_notify_cancel():
                continue
            try:
                self._run(action, arguments, cancel, serial)
            except BaseException as error:
                future.set_exception(error)
            else:
                future.set_result(None)

    def _exit_process(self):
        """Only the owned export child is terminated; training stays independent."""
        self.cancel.set()
        for process in (self.process, self.portal_process):
            if process is not None:
                try:
                    if process.poll() is None:
                        process.terminate()
                except OSError:
                    pass  # The child may have exited between poll and terminate.

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def update(self, *, bump_generation=False, **values):
        with self.lock:
            if not self.closed:
                self.state.update(values)
                if bump_generation:
                    self.state["generation"] += 1
                self.state["revision"] += 1

    @contextmanager
    def _portal(self, action, values, cancel):
        if not self.executable:
            raise ValueError('Choose a compatible LichtFeld Studio installation in Settings to use Portal.')
        request = dict(values, action=action, cache_directory=str(self.cache_directory))
        process = subprocess.Popen([self.executable, '--headless', '--python-script',
            str(Path(__file__).with_name('portal_bridge.py'))], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.portal_process = process
        packets = queue.Queue()
        def read():
            try:
                for line in process.stdout:
                    if line.startswith(b'LFS_PORTAL\t'):
                        try:
                            packets.put(json.loads(line[len(b'LFS_PORTAL\t'):]))
                        except (ValueError, UnicodeError) as error:
                            packets.put({'kind': 'error', 'value': str(error)})
            except (OSError, ValueError):
                pass  # Shutdown can close the pipe while the reader is waiting.
            finally:
                packets.put({'kind': 'eof'})
        reader = threading.Thread(target=read, daemon=True, name='lichtfeld-portal-pipe')
        reader.start()
        try:
            process.stdin.write((json.dumps(request)+'\n').encode('utf-8'))
            process.stdin.flush()
            while True:
                if cancel.is_set():
                    raise InterruptedError('Portal operation canceled')
                try:
                    packet = packets.get(timeout=.1)
                except queue.Empty:
                    continue
                if cancel.is_set():
                    raise InterruptedError('Portal operation canceled')
                kind, value = packet['kind'], packet.get('value')
                if kind == 'account':
                    self.update(account=SimpleNamespace(**value))
                elif kind == 'browser':
                    self.events.put(('browser', value))
                elif kind == 'progress':
                    done, total = value['done'], value['total']
                    checking = bool(total and done >= total)
                    self.update(phase='checking' if checking else 'import',
                        message='Checking downloaded project…' if checking else 'Downloading project…',
                        progress=done/total if total else 0., total_bytes=total)
                elif kind == 'message':
                    self.update(message=value)
                elif kind == 'error':
                    raise ValueError(value)
                elif kind == 'eof':
                    raise ValueError('LichtFeld Studio could not run the Portal client. Update the selected installation.')
                elif kind == 'result':
                    yield value
                    return
        finally:
            try:
                process.stdin.close()  # Releases an artifact lease in the canonical cache owner.
            except OSError:
                pass
            if cancel.is_set() and process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
            process.stdout.close()
            reader.join(timeout=1)
            self.portal_process = None

    def start(self, action, *arguments):
        with self.lock:
            if self.closed or self.state["phase"] not in ("idle", "failed", "canceled", "thumbnail"):
                return False
            if self.future and not self.future.done() and self.state["phase"] != "thumbnail":
                return False
            if self.tasks.full():
                return False
            self.cancel.set()
            self.cancel = threading.Event()
            cancel = self.cancel
            self.serial += 1
            serial = self.serial
            self.state["phase"] = action
            if action != "thumbnail":
                self.state.update(message="", progress=0.0, details="", total_bytes=0)
            self.state["revision"] += 1
            self.future = Future()
            self.tasks.put_nowait((self.future, action, arguments, cancel, serial))
        return True

    def _run(self, action, arguments, cancel, serial):
        try:
            if action == 'import':
                self._download(*arguments, cancel=cancel)
                return
            snapshot = self.snapshot().get('account')
            identity = (snapshot.email, snapshot.connected_since) if snapshot and snapshot.signed_in else None
            values = {'identity': identity}
            if action == 'sign_out':
                values['online'] = arguments[0] if arguments else True
            elif action == 'thumbnail':
                values['identifier'] = arguments[0]
            with self._portal(action, values, cancel) as response:
                if cancel.is_set():
                    raise InterruptedError('Operation canceled')
                if serial != self.serial:
                    return
                if action == 'refresh':
                    self.update(rows=tuple(response['rows']), rows_identity=tuple(response['identity']), bump_generation=True)
                elif action == 'thumbnail':
                    self.events.put(('thumbnail', (tuple(response['identity']), arguments[0], response['path'])))
                elif action == 'sign_out':
                    self.update(rows=(), bump_generation=True)
                elif action == 'clear_cache':
                    self.update(message=response['message'])
                    self.events.put(('cache_cleared', None))
            if cancel.is_set():
                raise InterruptedError('Operation canceled')
            if serial == self.serial:
                self.update(phase='idle')
        except Exception as error:
            if serial == self.serial:
                if action == 'thumbnail':
                    self.update(phase='idle')
                else:
                    values = {'account': None} if action == 'connect' and cancel.is_set() else {}
                    self.update(phase='canceled' if cancel.is_set() else 'failed', message=str(error)[:700], details=str(error), **values)

    def _backend(self, arguments, log, cancel, timeout):
        """Supervise both capability probing and export on the worker thread."""
        if cancel.is_set():
            raise InterruptedError("Import canceled")
        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.process = process
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
            while process.poll() is None:
                if cancel.wait(0.1) or deadline is not None and time.monotonic() > deadline:
                    raise InterruptedError("Import canceled" if cancel.is_set() else "Asset preparation timed out")
            if cancel.is_set():
                raise InterruptedError("Import canceled")
            return process.returncode
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            self.process = None

    def _download(self, identifier, executable, *, cancel):
        from .session_reader import read_ply, blender_fields
        if not Path(executable).is_file():
            raise ValueError('Choose the LichtFeld Studio executable in Settings before importing.')
        with tempfile.TemporaryFile() as probe:
            code = self._backend([executable, 'convert', '--capabilities'], probe, cancel, 20)
            probe.seek(0)
            if code or not PROJECT_IMPORT_CAPABILITIES.issubset(probe.read().splitlines()):
                raise ValueError('Update LichtFeld Studio: convert cannot export visible Portal splats with crops and RAD sources.')
        account = self.snapshot().get('account')
        if not account or not account.signed_in:
            raise ValueError('Connect your LichtFeld account first.')
        identity = (account.email, account.connected_since)
        with self._portal('download', {'identifier': identifier, 'identity': identity}, cancel) as response:
            project = Path(response['project'])
            with TemporaryDirectory(prefix='prepare-', dir=native_path(project.parent, force_extended=True)) as directory:
                output = Path(directory) / 'asset.ply'
                self.update(phase='converting', message='Preparing splats with LichtFeld Studio…', progress=0.)
                with (project.parent / 'last-export.log').open('w+b') as log:
                    code = self._backend([executable, 'convert', '--no-provenance', str(project), str(output)], log, cancel, None)
                    if code or not output.is_file():
                        raise ValueError(f'LichtFeld Studio could not convert this project. See {project.parent / "last-export.log"}')
                self.update(phase='decoding', message='Preparing Blender data…')
                with output.open('rb') as stream:
                    arrays, _ = read_ply(stream, cancel.is_set)
                fields = blender_fields(arrays)
                if cancel.is_set():
                    raise InterruptedError('Import canceled')
        self.update(phase='ready', message='Ready to add to the scene')
        self.events.put(('import', (identity, cancel, identifier, len(arrays['means']), fields)))

    def stop(self, *, cancel_login=True):
        self.cancel.set()
        if (self.future is None or self.future.done()) and self.snapshot()["phase"] not in ("idle", "canceled", "failed"):
            self.update(phase="canceled", message="Operation canceled")

    def close(self):
        with self.lock:
            self.closed = True
        self.stop()
        self._exit_process()
        try:
            queued = self.tasks.get_nowait()
            if queued is not None:
                queued[0].cancel()
        except queue.Empty:
            pass
        self.tasks.put_nowait(None)
        atexit.unregister(self._exit_process)
