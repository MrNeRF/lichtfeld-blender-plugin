# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Portal I/O and materialization. This module must never import bpy."""
from concurrent.futures import Future
import atexit
from lichtfeld_portal.path_io import Path, TemporaryDirectory, native_path
import hashlib
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import uuid


class Runtime:
    @staticmethod
    def cache_owner(account, session):
        return hashlib.sha256(repr((account.base_url, session[0])).encode("utf-8")).hexdigest()

    def __init__(self, directory, cache_directory=None):
        self.directory = Path(directory)
        self.cache_directory = Path(cache_directory) if cache_directory is not None else self.directory / "cache"
        self.tasks = queue.Queue(maxsize=1)
        self.process = None
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.events = queue.SimpleQueue()
        self.account = None
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
        process = self.process
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

    def _account(self):
        if self.account is None:
            from lichtfeld_portal import PortalAccountService
            from lichtfeld_portal.private_directory import mkdir_private
            import tomllib
            version = tomllib.loads(Path(__file__).with_name("blender_manifest.toml").read_text(encoding="utf-8"))["version"]
            mkdir_private(self.directory, parents=True, exist_ok=True)
            mkdir_private(self.cache_directory, parents=True, exist_ok=True)
            account = PortalAccountService(
                credentials_path=self.directory / "account" / "credentials.json",
                client_name="LichtFeld Blender", client_version=version,
                user_agent="LichtFeld-Blender/" + version,
                open_browser=lambda url: self.events.put(("browser", url)),
                dispatch=lambda callback: self.events.put(("callback", callback)),
                publish_state=lambda value: self.update(account=value))
            self.account = account
            if self.closed:
                account.cancel_device_flow()
                raise InterruptedError("Portal closed")
        return self.account

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
            account = self._account()
            if cancel.is_set():
                raise InterruptedError("Operation canceled")
            if action == "connect":
                account.start_device_flow()
            elif action == "sign_out":
                account.sign_out(revoke=arguments[0] if arguments else True)
                self.update(rows=(), bump_generation=True)
            elif action == "clear_cache":
                self._clear_cache(cancel)
            elif action == "refresh":
                if not account.sync_profile():
                    raise ValueError("Could not verify Portal access. Check your connection or reconnect your account.")
                client = self._client(account)
                rows = client.list_scenes(owner_wide=True) or []
                if cancel.is_set():
                    raise InterruptedError("Operation canceled")
                # IDs cross filesystem and UI boundaries only after UUID validation.
                rows = tuple({"id": str(uuid.UUID(row["id"])), "title": str(row.get("title") or "Untitled")[:256],
                              "description": str(row.get("description") or "")[:4096]} for row in rows)
                current = account.snapshot()
                if (current.email, current.connected_since) != client.expected_session:
                    raise ValueError("The Portal account changed. Refresh the gallery again.")
                self.update(rows=rows, rows_identity=client.expected_session, bump_generation=True)
            elif action == "thumbnail":
                client = self._client(account)
                _, _, data = client.thumbnail(arguments[0])
                if cancel.is_set():
                    raise InterruptedError("Thumbnail canceled")
                owner = self.cache_owner(account, client.expected_session)
                folder = self.cache_directory / "thumbnails" / owner
                from lichtfeld_portal.private_directory import mkdir_private
                mkdir_private(folder, parents=True, exist_ok=True)
                path = folder / (str(uuid.UUID(arguments[0])) + ".png")
                with tempfile.NamedTemporaryFile(dir=native_path(folder, force_extended=True), delete=False) as stream:
                    temporary = Path(stream.name)
                    try:
                        stream.write(data)
                        stream.close()
                        temporary.replace(path)
                    finally:
                        temporary.unlink(missing_ok=True)
                self.events.put(("thumbnail", (client.expected_session, arguments[0], str(path))))
            elif action == "import":
                self._download(account, *arguments, cancel=cancel)
                return
            if cancel.is_set():
                raise InterruptedError("Operation canceled")
            if serial == self.serial:
                self.update(phase="idle")
        except Exception as error:
            from lichtfeld_portal.gallery_logging import safe_text
            if serial == self.serial:
                if action == "thumbnail":
                    self.update(phase="idle")
                else:
                    message = "Not enough memory to prepare this asset" if isinstance(error, MemoryError) else safe_text(error)
                    self.update(phase="canceled" if cancel.is_set() else "failed", message=message[:700])

    @staticmethod
    def _client(account):
        from lichtfeld_portal import PortalGalleryClient
        snapshot = account.snapshot()
        if not snapshot.signed_in or not snapshot.email or not snapshot.connected_since:
            raise ValueError("Connect your Portal account first.")
        return PortalGalleryClient(account, expected_session=(snapshot.email, snapshot.connected_since))

    def _backend(self, arguments, log, cancel, timeout):
        """Supervise both capability probing and export on the worker thread."""
        if cancel.is_set():
            raise InterruptedError("Import canceled")
        process = subprocess.Popen(arguments, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.process = process
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancel.wait(0.1) or time.monotonic() > deadline:
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

    def _download(self, account, identifier, executable, *, cancel):
        from .session_reader import read_ply, blender_fields
        from lichtfeld_sessions.transport import atomic_json, read_json
        from lichtfeld_portal.private_directory import mkdir_private, locked_sidecar
        client = self._client(account)
        if not Path(executable).is_file():
            raise ValueError("Choose the LichtFeld Studio executable in Settings before importing.")
        with tempfile.TemporaryFile() as probe:
            code = self._backend([executable, "licht", "--help"], probe, cancel, 15)
            probe.seek(0)
            if code or b"--export-ply" not in probe.read():
                raise ValueError("Update LichtFeld Studio: this installation does not support Portal asset export (--export-ply).")
        owner = self.cache_owner(account, client.expected_session)
        cache = self.cache_directory / "assets" / owner / str(uuid.UUID(identifier))
        mkdir_private(cache, parents=True, exist_ok=True)
        with locked_sidecar(cache / "download.lock", blocking=False):
            project, checkpoint_path = cache / "asset.licht", cache / "download.json"
            try:
                checkpoint = read_json(checkpoint_path)
            except (OSError, ValueError):
                checkpoint = None
            if not isinstance(checkpoint, dict):
                checkpoint = None
            def progress(done, total):
                checking = bool(total and done >= total)
                self.update(phase="checking" if checking else "import",
                    message="Checking downloaded project…" if checking else "Downloading project…",
                    progress=done / total if total else 0.0, total_bytes=total)
            with TemporaryDirectory(prefix="prepare-", dir=native_path(cache, force_extended=True)) as directory:
                root = Path(directory)
                output = root / "asset.ply"
                client.download(identifier, project, cancel=cancel,
                    checkpoint=checkpoint, on_checkpoint=lambda value: atomic_json(checkpoint_path, value),
                    reuse_completed=True,
                    on_progress=progress,
                    on_processing=lambda state: self.update(message="The Portal is preparing this asset…"),
                    on_message=lambda message: self.update(message=str(message)))
                if cancel.is_set():
                    raise InterruptedError("Import canceled")
                self.update(phase="converting", message="Preparing splats with LichtFeld Studio…", progress=0.0)
                # The canonical closed-project exporter applies visibility and transforms.
                # No scene is opened, no project scripts are executed, no training starts.
                log_path = cache / "last-export.log"
                with log_path.open("wb") as log:
                    code = self._backend([executable, "licht", str(project), "--export-ply", str(output)], log, cancel, 900)
                    if code:
                        log.flush()
                        with log_path.open("rb") as details:
                            details.seek(max(0, log_path.stat().st_size - 1200))
                            message = details.read(1200).decode("utf-8", errors="replace")
                        from lichtfeld_portal.gallery_logging import safe_text
                        self.update(details=safe_text(message))
                        raise ValueError("LichtFeld Studio could not prepare this project. Copy error details for the backend's reason.")
                self.update(phase="decoding", message="Preparing Blender data…")
                with output.open("rb") as stream:
                    arrays, _ = read_ply(stream, cancel.is_set)
                fields = blender_fields(arrays)
                if cancel.is_set():
                    raise InterruptedError("Import canceled")
                current = account.snapshot()
                if not current.signed_in or (current.email, current.connected_since) != client.expected_session:
                    raise ValueError("The Portal account changed. Import the asset again.")
            self.events.put(("import", (client.expected_session, cancel, identifier, len(arrays["means"]), fields)))
            self.update(phase="ready", message="Ready to add to the scene")

    def _clear_cache(self, cancel):
        from lichtfeld_portal.private_directory import locked_sidecar
        root = self.cache_directory.resolve()
        skipped = False
        for asset in self.cache_directory.glob("assets/*/*"):
            if cancel.is_set():
                raise InterruptedError("Cache cleanup canceled")
            if not asset.resolve().is_relative_to(root) or not asset.is_dir():
                continue
            try:
                with locked_sidecar(asset / "download.lock", blocking=False):
                    for name in ("asset.licht", ".asset.licht.part", "download.json", "last-export.log"):
                        (asset / name).unlink(missing_ok=True)
                    for preparation in asset.glob("prepare-*"):
                        if preparation.is_dir() and preparation.resolve().is_relative_to(root):
                            shutil.rmtree(preparation)
            except OSError:
                skipped = True
        for image in self.cache_directory.glob("thumbnails/*/*.png"):
            if image.resolve().is_relative_to(root):
                try:
                    image.unlink(missing_ok=True)
                except OSError:
                    skipped = True
        self.update(message="Some cached files are in use. Try again after other imports finish." if skipped else "Portal cache cleared")
        self.events.put(("cache_cleared", None))

    def stop(self, *, cancel_login=True):
        self.cancel.set()
        if (self.future is None or self.future.done()) and self.snapshot()["phase"] not in ("idle", "canceled", "failed"):
            self.update(phase="canceled", message="Operation canceled")
        account = self.account
        if account and cancel_login and account.snapshot().linking:
            account.cancel_device_flow()

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
