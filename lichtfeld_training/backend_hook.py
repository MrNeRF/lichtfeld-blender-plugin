# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal safe-boundary adapter loaded by LichtFeld's training callback API.

The user installs the Blender extension, never this file independently.
Only owned CPU arrays are passed to the writer; model access stays in hooks.
"""

import os
import math
from pathlib import Path
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_core import (atomic_json, read_json, read_job_json, read_dataset_json, training_config, metric_at,
                         better, write_splat, artifact_path)
import lichtfeld as lf
from session_transport import SessionPublisher, spatial_indices, MAX_BYTES
from job_control import validate_command
import session_geometry

try:
    import numpy as np
except ImportError:
    import importlib
    _job = read_job_json(os.environ["LFS_BLENDER_JOB"])
    atomic_json(Path(_job["root"]) / "runtime.json", {
        "job_id": _job["id"], "state": "preparing numerical runtime", "updated": time.time()})
    lf.packages.init()
    lf.packages.install("numpy")
    sys.path.insert(0, lf.packages.site_packages_dir())
    importlib.invalidate_caches()
    import numpy as np


class PreviewCapacityError(ValueError):
    """Requested preview cannot fit the shared-memory transport budget."""


class TrainingBridge:
    def __init__(self, job):
        self.job = job
        self.root = Path(job["root"])
        self.session = SessionPublisher({"label": job.get("name", "Blender training"),
                                         "job_root": str(self.root), "job_id": job["id"],
                                         "project": job.get("source_file", ""),
                                         "source": "blender", "backend": sys.executable})
        dataset = Path(job.get("dataset_root") or self.root / "dataset")
        geometry = read_dataset_json(dataset / "view_geometry.json", None)
        if geometry is None:
            transforms = read_dataset_json(dataset / "transforms.json", None) or read_dataset_json(dataset / "capture_transforms.json", None)
            if transforms:
                geometry = session_geometry.from_capture(transforms["frames"])
        if geometry:
            session_geometry.publish(self.session, geometry)
        self.view_geometry = geometry
        self.geometry_checked = False
        self.geometry_retry_at = 0
        self.writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="splat-writer")
        self.pending = None
        self.pending_kind = None
        self.preview_error = ""
        self.preview_fallback = False
        self.preview_note = ""
        self.best = read_json(self.root / "best.json")
        self.preview = None
        self.last_preview = 0.0
        self.base_interval = .1 if job.get("preview_mode") == "LIVE" else job["preview_seconds"]
        self.interval = self.base_interval
        self.last_status = 0.0
        self.command_id = ""
        self.command_error = ""
        self.error = ""
        self.paused = False
        self.stopped = False
        self.previews_enabled = True
        self.preview_history = []
        self.iteration = 0
        self.supervision_checked = False
        self.supervision = ""
        self.loss = 0.0
        self.count = 0
        self.capture_ms = 0.0
        self.eval_steps = set(training_config(job)["optimization"]["eval_steps"])
        self.hooks = lf.ScopedHandler()
        self.hooks.on_post_step(self.on_step)
        self.hooks.on_training_end(self.on_end)
        self.publish("training")

    def publish(self, state="training"):
        self.session.update(state=state, iteration=self.iteration, iterations=self.job["iterations"],
                            count=self.count, error=self.error, preview_error=self.preview_error,
                            preview_fallback=self.preview_fallback, preview_note=self.preview_note)
        atomic_json(self.root / "runtime.json", {
            "job_id": self.job["id"], "control_protocol": 1, "state": state, "iteration": self.iteration,
            "iterations": self.job["iterations"], "loss": self.loss, "count": self.count,
            "supervision": self.supervision,
            "best": self.best, "preview": self.preview, "error": self.error, "preview_error": self.preview_error,
            "preview_fallback": self.preview_fallback, "preview_note": self.preview_note,
            "command_id": self.command_id, "command_error": self.command_error, "previews_enabled": self.previews_enabled,
            "preview_seconds": self.interval, "capture_ms": self.capture_ms, "updated": time.time(),
            "session_directory": str(self.session.root), "session_id": self.session.id,
            "geometry": self.session.record.get("geometry"),
        })
        self.last_status = time.monotonic()

    def finish_writer(self, wait=False):
        if self.pending is None or (not wait and not self.pending.done()):
            return
        try:
            record = self.pending.result()
        except Exception as error:
            kind = self.pending_kind
            self.pending = None
            self.pending_kind = None
            if kind != "preview":
                raise
            self.preview_error = str(error)
            self.last_preview = time.monotonic()
            self.interval = max(2, self.base_interval)
            return True
        self.pending = None
        self.pending_kind = None
        self.preview_error = record.pop("preview_error", "")
        if record.pop("preview_fallback", False):
            self.preview_fallback = True
            self.preview_note = record.pop("preview_note", "Showing the configured limited preview; saved models remain full quality.")
        else:
            record.pop("preview_note", None)
        preview = record.pop("preview_shared", None)
        if preview:
            self.preview = {**preview, "shared": preview}
        if record["kind"] == "best":
            previous = self.best
            self.best = record
            atomic_json(self.root / "best.json", record)
            if previous and previous["path"] != record["path"]:
                try:
                    artifact_path(self.root, previous["path"]).unlink(missing_ok=True)
                except OSError:
                    pass  # A consumer may still hold the previous best on Windows.
        else:
            self.preview = record
            if "path" in record:
                self.preview_history.append(record)
            self.cleanup_previews()
        return True

    def write_candidate(self, arrays, iteration, kind, score=None):
        if kind == "preview":
            shared = self.session.publish(arrays, iteration, kind)
            return {**shared, "shared": shared}
        record = write_splat(self.root, arrays, iteration, kind, score)
        preview = arrays
        preview_fallback = False
        if not self.job.get("full_preview", False):
            preview = self.limited_preview(arrays)
        elif sum(value.nbytes for value in preview.values()) > MAX_BYTES:
            preview = self.limited_preview(arrays)
            preview_fallback = True
        try:
            if sum(value.nbytes for value in preview.values()) > MAX_BYTES:
                raise PreviewCapacityError("Configured preview exceeds the shared-memory limit.")
            record["preview_shared"] = self.session.publish(preview, iteration)
            if preview_fallback:
                record["preview_fallback"] = True
                record["preview_note"] = self.preview_limit_note()
        except Exception as error:
            # Saving a best model must not fail merely because its optional viewer cannot publish.
            record["preview_error"] = str(error)
        return record

    def limited_preview(self, arrays):
        count = len(arrays["means"])
        degree = self.job.get("preview_sh_degree", 1)
        indices = spatial_indices(arrays["means"], min(count, self.job["preview_points"]))
        preview = {key: value[indices] for key, value in arrays.items()}
        preview["sh"] = preview["sh"][:, :(degree + 1) ** 2]
        return preview

    def preview_limit_note(self):
        return (f"Full preview exceeds the shared-memory limit; showing the configured limited preview "
                f"(up to {self.job['preview_points']:,} points, SH{self.job.get('preview_sh_degree', 1)}). "
                "Training and saved models retain full quality.")

    def capture_preview(self):
        if not self.job.get("full_preview", False):
            arrays = self.capture(self.job["preview_points"], self.job.get("preview_sh_degree", 1), for_preview=True)
            self.preview_fallback = False
            self.preview_note = ""
            return arrays
        try:
            arrays = self.capture(for_preview=True)
        except PreviewCapacityError:
            arrays = self.capture(self.job["preview_points"], self.job.get("preview_sh_degree", 1), for_preview=True)
            self.preview_fallback = True
            self.preview_note = self.preview_limit_note()
            return arrays
        self.preview_fallback = False
        self.preview_note = ""
        return arrays

    def cleanup_previews(self):
        ack = read_json(self.root / "ack.json", {})
        acknowledged = next((i for i, item in enumerate(self.preview_history)
                             if item["id"] == ack.get("id")), -1)
        if acknowledged > 0:
            retained = []
            for item in self.preview_history[:acknowledged]:
                try:
                    if "path" in item:
                        artifact_path(self.root, item["path"]).unlink(missing_ok=True)
                except OSError:
                    retained.append(item)
            self.preview_history = retained + self.preview_history[acknowledged:]

    def capture(self, limit=None, degree=None, for_preview=False):
        model = lf.get_scene().training_model()
        if model is None:
            raise RuntimeError("No model is available at the training safe boundary")
        count = model.num_points
        indices = None
        deleted = model.has_deleted_mask()
        if deleted:
            mask = model.deleted.cpu()
            mask.sync()
            indices = np.flatnonzero(~mask.numpy().reshape(-1).astype(bool)).astype(np.int64)
            if len(indices) == count:
                indices = None
        active_count = len(indices) if indices is not None else count
        if limit and active_count > limit:
            samples = np.linspace(0, active_count - 1, min(active_count, limit * 4), dtype=np.int64)
            candidates = indices[samples] if indices is not None else samples
            positions = model.means_raw.index_select(0, lf.Tensor.from_numpy(candidates).cuda()).cpu()
            positions.sync()
            indices = candidates[spatial_indices(positions.numpy(), limit)]
        active_count = len(indices) if indices is not None else count
        active = min(model.active_sh_degree, degree) if degree is not None else model.active_sh_degree
        if for_preview and active_count * (11 + 3 * (active + 1) ** 2) * 4 > MAX_BYTES:
            raise PreviewCapacityError("Full preview exceeds the shared-memory limit.")
        cpu_selection = lf.Tensor.from_numpy(indices) if indices is not None else None
        selection = cpu_selection.cuda() if cpu_selection is not None else None
        def owned(tensor):
            # Full previews need no identity index array or redundant GPU gather.
            result = (tensor.index_select(0, selection) if selection is not None else tensor).cpu()
            result.sync()
            return result.numpy()

        active = model.active_sh_degree
        if degree is not None:
            active = min(active, degree)
        dc = owned(model.sh0_raw).reshape(active_count, 1, 3)
        if active:
            gather = getattr(model, "shN_rows", None)
            if callable(gather):
                rows = cpu_selection if cpu_selection is not None else lf.Tensor.from_numpy(np.arange(count, dtype=np.int64))
                rest = gather(rows, active).numpy()
            elif for_preview:
                raise RuntimeError("Update LichtFeld: this backend cannot extract bounded SH previews. "
                                   "Training and saved results continue; SH0 previews remain available.")
            else:
                # Preserve best/final export compatibility with earlier backends.
                rest = owned(model.shN_raw)[:, :(active + 1) ** 2 - 1, :]
            sh = np.concatenate((dc, rest), axis=1)
        else:
            sh = dc
        return {"means": owned(model.means_raw), "scale": owned(model.scaling_raw),
                "rotation": owned(model.rotation_raw), "opacity": owned(model.opacity_raw),
                "sh": sh}

    def commands(self):
        while True:
            command = read_json(self.root / "command.json", {})
            if isinstance(command, dict) and isinstance(command.get("id"), str) and command.get("id") != self.command_id and command.get("job_id") == self.job["id"]:
                self.command_id = command["id"]
                try:
                    action = validate_command(self.root, self.job["id"], command)
                    self.command_error = ""
                except ValueError as error:
                    self.command_error = str(error)
                    self.publish("paused" if self.paused else "training")
                    if not self.paused:
                        break
                    time.sleep(0.1)
                    continue
                if action == "pause":
                    self.paused = True
                elif action == "resume":
                    self.paused = False
                elif action == "stop":
                    self.stopped = True
                    self.paused = False
                    lf.session().request_stop()
                elif action == "preview_off":
                    self.previews_enabled = False
                elif action == "preview_on":
                    self.previews_enabled = True
                self.publish("paused" if self.paused else "training")
            if not self.paused:
                break
            time.sleep(0.1)

    def on_step(self, hook):
        try:
            self.iteration = hook["iteration"]
            if not self.geometry_checked and time.monotonic() >= self.geometry_retry_at:
                try:
                    from lfs_plugins.training_runtime.producer import capture_view_geometry
                    current_geometry = capture_view_geometry()
                    if self.view_geometry:
                        current_geometry["source_frame"] = self.view_geometry.get("source_frame", "blender-world")
                        current_geometry["data_to_source"] = self.view_geometry["data_to_source"]
                        if not current_geometry["cameras"]:
                            current_geometry["cameras"] = self.view_geometry["cameras"]
                    session_geometry.publish(self.session, current_geometry)
                    self.geometry_checked = True
                except Exception as error:
                    self.geometry_retry_at = time.monotonic() + 5
                    self.session.update(geometry_error=str(error))
            if not self.supervision_checked:
                self.supervision_checked = True
                if self.job.get('use_depth_loss') and self.job.get('depth_loss_weight',2)>0:
                    dataset = Path(self.job.get('dataset_root',str(self.root/'dataset')))
                    anchors = read_json(dataset/'depths/depth_anchors.json',{}).get('anchors',{})
                    active = sum(bool(item.get('depth',{}).get('valid')) for item in anchors.values())
                    manifest = read_json(dataset/'manifest.json',{})
                    expected = manifest.get('views',0)-len(manifest.get('validation',[]))
                    if not active or active < expected:
                        raise RuntimeError('Depth supervision could not be anchored for all training views. Check resolution, coverage and depth variation, or disable depth supervision.')
                    self.supervision = f'Depth active: {active} anchored views'
                if self.job.get('use_normal_loss') and self.job.get('normal_loss_weight',.005)>0:
                    self.supervision += (' · ' if self.supervision else '') + 'Normals: camera OpenCV'
            self.loss = float(hook["loss"])
            if not math.isfinite(self.loss):
                self.loss = 0.0
                raise RuntimeError("Training produced a non-finite loss")
            self.count = hook["num_gaussians"]
            if self.finish_writer():
                self.publish("stopping" if self.stopped else "training")
            self.cleanup_previews()
            self.commands()
            # The stock trainer drains post-step callbacks before its next mutation.
            # Exact iteration matching is essential; never use the last unrelated row.
            score = metric_at(self.root / "output/metrics.csv", self.iteration) if self.iteration in self.eval_steps else None
            if score:
                self.finish_writer(wait=True)
                if better({**score, "count": self.count},
                          {**self.best["score"], "count": self.best["count"]}
                          if self.best and self.best.get("score") else None):
                    arrays = self.capture()
                    self.pending_kind = "best"
                    self.pending = self.writer.submit(self.write_candidate, arrays,
                                                      self.iteration, "best", score)
            elif (self.previews_enabled and self.pending is None and
                  len(self.preview_history) < 3 and
                  time.monotonic() - self.last_preview >= self.interval and self.session.clients()):
                started = time.monotonic()
                try:
                    arrays = self.capture_preview()
                    capture_seconds = time.monotonic() - started
                    self.capture_ms = capture_seconds * 1000
                    self.interval = max(self.base_interval, capture_seconds / 0.05)
                    self.pending_kind = "preview"
                    self.pending = self.writer.submit(self.write_candidate, arrays,
                                                      self.iteration, "preview")
                except Exception as error:
                    self.preview_error = str(error)
                    self.interval = max(2, self.base_interval)
                self.last_preview = time.monotonic()
            if time.monotonic() - self.last_status >= 0.5:
                self.publish("stopping" if self.stopped else "training")
        except Exception as error:
            self.error = str(error)
            self.publish("failed")
            lf.session().request_stop()

    def on_end(self, hook):
        try:
            terminal = hook.get("terminal_state", "completed")
            if terminal == "failed" and not self.error:
                self.error = "Trainer failed; the last valid result is retained"
            self.finish_writer(wait=True)
            self.iteration = hook["iteration"]
            score = metric_at(self.root / "output/metrics.csv", self.iteration)
            model = lf.get_scene().training_model()
            if score and better({**score, "count": model.num_points},
                                {**self.best["score"], "count": self.best["count"]}
                                if self.best and self.best.get("score") else None):
                self.best = write_splat(self.root, self.capture(), self.iteration, "best", score)
                atomic_json(self.root / "best.json", self.best)
            if self.best is None:
                self.best = write_splat(self.root, self.capture(), self.iteration, "last")
                atomic_json(self.root / "best.json", self.best)
            self.publish("failed" if self.error else "finalizing")
            self.session.finish("failed" if self.error else terminal, result=self.best)
        except Exception as error:
            self.error = str(error)
            self.publish("failed")
        finally:
            self.writer.shutdown(wait=True)
            if not self.session.stop.is_set():
                self.session.finish("failed", error=self.error)


_bridge = TrainingBridge(read_job_json(os.environ["LFS_BLENDER_JOB"]))
