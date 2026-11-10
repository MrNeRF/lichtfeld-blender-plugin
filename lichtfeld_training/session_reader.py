# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded background readers. Never import or access bpy, widgets or scene APIs."""
from pathlib import Path
import queue
import threading
import time
from lichtfeld_sessions.transport import TERMINAL, SessionClient, read_json, read_snapshot, process_alive, validate_session, validate_result, session_liveness, artifact_path, verified_artifact
from .job_control import Controller
from lichtfeld_sessions.geometry import FLIP, IDENTITY, validate as validate_geometry, load as load_geometry
from .bridge_core import read_job_json


def load_record(root, record, should_cancel=lambda: False):
    if record.get("shared"):
        try:
            return read_snapshot(record["shared"]), record["shared"]["transport"]
        except FileNotFoundError:
            if "path" not in record:
                raise
    import numpy as np
    path = artifact_path(root, record["path"])
    with verified_artifact(path, record, should_cancel) as stream:
        if stream.readline() != b"ply\n" or stream.readline() != b"format binary_little_endian 1.0\n":
            raise ValueError("Expected a binary Gaussian PLY")
        fields, count = [], None
        for _ in range(256):
            line = stream.readline().decode("ascii").strip()
            if line == "end_header":
                break
            if line.startswith("element vertex "):
                count = int(line.split()[-1])
            elif line.startswith("property "):
                _, kind, name = line.split()
                if kind != "float":
                    raise ValueError("Unsupported Gaussian PLY property")
                fields.append(name)
        else:
            raise ValueError("PLY header limit exceeded")
        if not count or not 0 < count <= 10000000:
            raise ValueError("Invalid Gaussian count")
        values = np.fromfile(stream, dtype="<f4", count=count * len(fields))
        if values.size != count * len(fields) or not np.isfinite(values).all():
            raise ValueError("Invalid Gaussian payload")
    values = values.reshape(count, len(fields))
    def columns(names):
        return np.ascontiguousarray(values[:, [fields.index(name) for name in names]])
    rest = [name for name in fields if name.startswith("f_rest_")]
    if len(rest) not in (0, 9, 24, 45):
        raise ValueError("Unsupported SH layout")
    dc = columns([f"f_dc_{i}" for i in range(3)]).reshape(count, 1, 3)
    shn = columns([f"f_rest_{i}" for i in range(len(rest))]).reshape(count, 3, -1).transpose(0, 2, 1)
    return {"means": columns(["x", "y", "z"]), "scale": columns([f"scale_{i}" for i in range(3)]),
            "rotation": columns([f"rot_{i}" for i in range(4)]), "opacity": columns(["opacity"]),
            "sh": np.ascontiguousarray(np.concatenate((dc, shn), axis=1))}, "ply"


def blender_fields(arrays):
    import numpy as np
    rotation = arrays["rotation"].copy()
    rotation[np.linalg.norm(rotation, axis=1) < 1e-12] = (1, 0, 0, 0)
    rotation /= np.maximum(np.linalg.norm(rotation, axis=1, keepdims=True), 1e-12)
    radiance = np.empty((len(rotation), 4), dtype=np.float32)
    radiance[:, :3] = arrays["sh"][:, 0]
    radiance[:, 3] = 1 / (1 + np.exp(-np.clip(arrays["opacity"].reshape(-1), -80, 80)))
    fields = [("position", "FLOAT_VECTOR", "vector", arrays["means"]),
              ("scale", "FLOAT_VECTOR", "vector", np.exp(np.clip(arrays["scale"], -80, 80))),
              ("rotation", "QUATERNION", "value", rotation),
              ("radiance:base", "FLOAT4", "vector", radiance)]
    fields.extend((f"radiance:sh_{i}", "FLOAT_VECTOR", "vector", arrays["sh"][:, i + 1])
                  for i in range(arrays["sh"].shape[1] - 1))
    if arrays["sh"].shape[1] == 16:
        # EEVEE's material attribute budget drops the 16th requested attribute.
        # Pack the last four RGB coefficients in three FLOAT4 attributes, while
        # retaining all standard attributes for native Blender rendering/export.
        tail = arrays["sh"][:, 12:16].reshape(len(rotation), 3, 4)
        fields.extend((f"lichtfeld_rgb_tail_{i}", "FLOAT4", "vector", tail[:, i]) for i in range(3))
    return [(name, kind, prop, np.ascontiguousarray(value, dtype=np.float32).ravel())
            for name, kind, prop, value in fields]


class Observer:
    """One reader per scene binding; at most one unconsumed frame, sticky errors."""
    def __init__(self, directory, external=False, prepare=None, bootstrap=None, reject_same_process=False):
        self.root = Path(directory)
        self.external = external
        self.prepare = prepare or (lambda arrays: arrays)
        self.bootstrap = bootstrap
        self.reject_same_process = reject_same_process
        self.stop = threading.Event()
        self.frames = queue.Queue(maxsize=1)
        self.ack = queue.SimpleQueue()
        self.commands = queue.SimpleQueue()
        self.status = {"state": "connecting"}
        self.error = ""
        self.status_error = False
        self.pending = False
        self.last = None
        self.last_geometry_ref = None
        self.last_count = 0
        self.force_best = False
        self.follow_best = False
        self.next_frame_at = 0.0
        self.retry_at = 0
        self.failures = 0
        self.inflight = None
        self.geometry = None
        self.geometry_ref = None
        self.geometry_error = ""
        self.geometry_retry_at = 0
        self.command_pending = None
        self.command_deadline = 0
        self.command_backlog = []
        self.control_available = False
        self.control_reason = ""
        self.command_error = ""
        self.thread = threading.Thread(target=self._run, daemon=True, name="training-reader")
        self.thread.start()

    def take(self):
        try:
            return self.frames.get_nowait()
        except queue.Empty:
            return None

    def acknowledge(self, generation, error=None, display_seconds=0, *, geometry_ref=...):
        self.ack.put((generation, geometry_ref, error, display_seconds))

    def close(self):
        self.stop.set()  # Never join or wait on a UI thread.

    def _run(self):
        client = None
        controller = None
        cached_metadata = {}
        cached_stamps = {}

        def metadata(name, changed_only=False, max_bytes=None):
            stamp = None
            if changed_only:
                try:
                    info = (self.root / name).stat()
                except OSError:
                    return cached_metadata.get(name, {})
                stamp = info.st_mtime_ns, info.st_size
                if stamp == cached_stamps.get(name):
                    return cached_metadata.get(name, {})
                if max_bytes is not None and info.st_size > max_bytes:
                    return cached_metadata.get(name, {})
            value = read_job_json(self.root / name) if name == 'job.json' else read_json(self.root / name, None)
            if isinstance(value, dict) and value:
                cached_metadata[name] = value
                if stamp is not None:
                    cached_stamps[name] = stamp
            return cached_metadata.get(name, {})

        try:
            while not self.stop.is_set():
                record = None
                result = None
                reading_status = True
                try:
                    if self.bootstrap:
                        if time.monotonic() < self.retry_at:
                            self.stop.wait(.1)
                            continue
                        self.bootstrap()
                        self.bootstrap = None
                    while not self.ack.empty():
                        generation, geometry_ref, error, display_seconds = self.ack.get_nowait()
                        frame = self.inflight
                        if (frame is None or generation != frame["record"]["id"] or
                                geometry_ref is not ... and geometry_ref != frame["geometry_ref"]):
                            continue
                        if hasattr(frame["data"], "release"):
                            frame["data"].release()
                        self.inflight = None
                        self.pending = False
                        if error:
                            if frame.get("geometry_only"):
                                self.last = None  # Restore a missing/replaced display from the immutable payload.
                            self.error = str(error)
                            self.failures += 1
                            self.retry_at = time.monotonic() + min(30, 2 ** min(self.failures, 5))
                        else:
                            self.last = generation
                            self.last_geometry_ref = frame["geometry_ref"]
                            self.last_count = frame["count"]
                            self.next_frame_at = time.monotonic() + max(0, min(5, display_seconds))
                            self.error = ""
                            self.failures = 0
                        frame = None  # Drop large prepared CPU buffers on this worker after acknowledgement.
                    if self.external:
                        if client is None:
                            client = SessionClient(self.root, "External renderer")
                        status = validate_session(client.status(), self.root.name)
                        if self.reject_same_process:
                            import os
                            if status.get("pid") == os.getpid():
                                raise ValueError("Use the local training viewport for a trainer in this process")
                        liveness = session_liveness(status)
                        alive = liveness == "responsive"
                        state = status.get("state", "connecting")
                        if not alive and state not in TERMINAL:
                            state = "disconnected"
                        job = {"id": "session:" + status.get("id", self.root.name), "sources": []}
                        root = Path(status.get("job_root", self.root))
                        self.status = {**status, "state": state, "liveness": liveness, "job": job, "root": str(root)}
                        result = status.get("result") if state in TERMINAL else None
                        snapshot = status.get("snapshot")
                        if result is not None:
                            result = validate_result(result)
                            presentation = validate_geometry(result["presentation"])
                            root = Path(result["root"])
                            self.status["root"] = str(root)
                            # A terminal result has no mandatory snapshot id and
                            # must use its full artifact, never a limited preview.
                            record = {**result, "id": result["sha256"]}
                            record.pop("shared", None)
                        else:
                            record = {**snapshot, "shared": snapshot} if snapshot else None
                    else:
                        state = metadata("state.json")
                        runtime = metadata("runtime.json")
                        try:
                            job = metadata("job.json", changed_only=True)
                        except (ValueError, UnicodeError, RecursionError) as error:
                            job = {}
                            state = {'state': 'failed', 'error': 'Cannot read training job: ' + str(error)}
                        directory = runtime.get("session_directory")
                        if directory and client is None:
                            client = SessionClient(directory, "Blender owner")
                        if client:
                            client.touch()
                        if controller is None and job.get("id"):
                            controller = Controller(self.root)
                        protocol = runtime.get("control_protocol", state.get("control_protocol"))
                        previous_control = self.control_available
                        if protocol != 1:
                            if controller:
                                controller.close()
                            self.control_available = False
                            self.control_reason = ("This trainer uses older job controls. Continue from its saved checkpoint to use current controls."
                                                   if job.get("id") else "Waiting for job control")
                        else:
                            self.control_available = bool(controller and controller.acquire())
                            self.control_reason = "" if self.control_available else "Another Blender window controls this job. Controls become available automatically when it releases them."
                        if self.control_available and not previous_control:
                            self.command_error = ""
                        while not self.commands.empty():
                            action = self.commands.get_nowait()
                            if action in {"import_best", "view_best"}:
                                self.force_best = True
                                self.follow_best = True
                                self.next_frame_at = 0
                            elif action == "view_current":
                                self.follow_best = False
                                self.next_frame_at = 0
                                self.force_best = False
                            elif not self.control_available:
                                self.command_error = self.control_reason
                            else:
                                if action == "stop":
                                    self.command_backlog.clear()
                                    self.command_pending = None
                                if len(self.command_backlog) < 8:
                                    self.command_backlog.append(action)
                        if self.command_pending:
                            if runtime.get("command_id") == self.command_pending["id"]:
                                self.command_error = runtime.get("command_error", "")
                                self.command_pending = None
                            elif time.monotonic() >= self.command_deadline:
                                self.command_error = "The trainer did not confirm the command in time. Check its state and send it again."
                                self.command_pending = None
                                self.command_backlog.clear()
                        if self.control_available and self.command_pending is None and self.command_backlog and job.get("id"):
                            from lichtfeld_sessions.transport import atomic_json
                            command = controller.command(job["id"], self.command_backlog[0])
                            atomic_json(self.root / "command.json", command)
                            self.command_backlog.pop(0)
                            self.command_pending = command
                            self.command_deadline = time.monotonic() + command["expires"] - command["created"]
                            self.command_error = ""
                        phase = state.get("state", "preparing")
                        terminal = phase in {"completed", "stopped", "failed"}
                        if terminal:
                            self.command_pending = None
                            self.command_backlog.clear()
                        phase = phase if terminal else runtime.get("state", phase)
                        heartbeat = read_json(self.root / "heartbeat.json", {})
                        if not terminal and time.time() - heartbeat.get("updated", state.get("updated", time.time())) > 15:
                            if not process_alive(state.get("supervisor_pid")):
                                phase = "controller_lost"
                        # Build privately: file reads/stat release the GIL. Publishing
                        # early exposes missing checkpoint/best/control keys to the UI.
                        snapshot = {**runtime, **state, "state": phase, "job": job,
                                       "capture": metadata("capture-progress.json"),
                                       "backend_alive": process_alive(state.get("backend_pid")), "root": str(self.root)}
                        snapshot["best"] = metadata("best.json")
                        try:
                            render = metadata('capture-render.json', changed_only=True, max_bytes=262144)
                            snapshot['capture_render'] = render if isinstance(render, dict) else {}
                        except (OSError, ValueError, TypeError):
                            # Optional diagnostics must not interrupt training status,
                            # controls or model updates when a file is damaged.
                            snapshot['capture_render'] = {}
                        snapshot["viewer_mode"] = "best" if self.follow_best else "current"
                        snapshot["checkpoint_available"] = (self.root / "output/project.licht").is_file()
                        try:
                            snapshot["dataset_ready"] = (Path(job.get("dataset_root") or self.root / "dataset") / "manifest.json").is_file()
                        except (OSError, ValueError, TypeError):
                            snapshot["dataset_ready"] = False  # Optional recovery capability never stops live previews.
                        snapshot["control_available"] = self.control_available
                        snapshot["control_reason"] = self.control_reason
                        snapshot["command_error"] = self.command_error
                        snapshot["pending_command"] = self.command_pending.get("action") if self.command_pending else None
                        self.status = snapshot
                        record = (snapshot["best"] if self.force_best or self.follow_best else
                                  state.get("result") if terminal else runtime.get("preview"))
                        if not terminal and not runtime.get("previews_enabled", True) and not self.force_best:
                            record = None
                        root = self.root
                    reference = self.status.get("geometry")
                    if reference != self.geometry_ref and time.monotonic() >= self.geometry_retry_at:
                        try:
                            geometry_root = self.root if self.external else Path(runtime.get("session_directory", self.root))
                            self.geometry = load_geometry(geometry_root, reference)
                            self.geometry_ref = reference
                            self.geometry_error = ""
                        except Exception as error:
                            self.geometry_error = str(error)
                            self.geometry_retry_at = time.monotonic() + 2
                    self.status["geometry_error"] = self.geometry_error or self.status.get("geometry_error", "")
                    if self.status_error:
                        self.error = ""
                        self.status_error = False
                        self.failures = 0
                        self.retry_at = 0
                    reading_status = False
                    ready = (time.monotonic() >= self.next_frame_at or self.force_best or
                             self.status.get("state") in TERMINAL)
                    geometry_changed = self.geometry_ref != self.last_geometry_ref
                    if ready and record and not self.pending and (self.force_best or record["id"] != self.last or geometry_changed) and time.monotonic() >= self.retry_at:
                        geometry_only = not self.force_best and record["id"] == self.last
                        if geometry_only:
                            data, count, transport = None, self.last_count, None
                        else:
                            arrays, transport = load_record(root, record, self.stop.is_set)
                            try:
                                data, count = self.prepare(arrays), len(arrays["means"])
                            finally:
                                del arrays
                        frame_geometry = self.geometry
                        if self.external and result is not None:
                            frame_geometry = {**(self.geometry or {}), "data_to_source": FLIP,
                                              "model_to_data": IDENTITY, **presentation}
                        frame = {"record": record, "job": job, "transport": transport,
                                 "count": count, "geometry": frame_geometry,
                                 "geometry_ref": self.geometry_ref, "geometry_only": geometry_only,
                                 "geometry_revision": (self.geometry_ref or {}).get("revision", ""),
                                 "data": data}
                        data = None
                        if self.stop.is_set():
                            break
                        self.frames.put_nowait(frame)
                        self.inflight = frame
                        self.pending = True
                        self.force_best = False
                except FileNotFoundError:
                    if record and record.get("path"):
                        self.error = "The saved result is no longer available. Check its training folder."
                        self.retry_at = time.monotonic() + 2
                    # A live shared generation can expire before it is opened.
                except Exception as error:
                    self.status_error = reading_status
                    self.error = str(error)
                    self.failures += 1
                    self.retry_at = time.monotonic() + min(30, 2 ** min(self.failures, 5))
                self.stop.wait(0.1)
        finally:
            if self.inflight and hasattr(self.inflight["data"], "release"):
                self.inflight["data"].release()
            self.inflight = None
            self.geometry = None
            while not self.frames.empty():
                self.frames.get_nowait()
            if controller:
                controller.close()
            if client:
                try:
                    client.close()
                except OSError:
                    pass
