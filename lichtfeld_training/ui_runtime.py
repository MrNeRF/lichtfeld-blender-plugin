# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Only bounded Blender RNA work lives here. All I/O and numerical work is queued."""
from concurrent.futures import ThreadPoolExecutor
import time
import logging
import math
import uuid
import bpy
from mathutils import Matrix
from .session_reader import Observer, blender_fields
from .visibility import set_hidden, source_steps
from .session_geometry import source_model_transform

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lichtfeld-io")
_tasks = []
_bindings = {}
_owners = {}
_actions = []
_rotation = 0
_cloud_buffers = {}


def schedule(generator, failure=None):
    _actions.append((generator, failure))


def submit(function, callback=None, failure=None):
    future = _executor.submit(function)
    _tasks.append((future, callback, failure))
    return future


def counter(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid training counter in session metadata")
    return int(value)


def report_notice(level, message):
    """Report from a fresh operator, never through an expired caller's RNA."""
    try:
        bpy.ops.lichtfeld.report_notice('EXEC_DEFAULT', level=level, message=str(message))
    except (RuntimeError, AttributeError):
        # ERROR reports can raise after Blender has already shown the report;
        # shutdown can also remove the reporting operator before a late callback.
        logging.getLogger(__name__).warning('%s: %s', level, message)


def report_failure(callback, error):
    try:
        if callback:
            callback(str(error))
        else:
            logging.getLogger(__name__).error("LichtFeld operation failed: %s", error)
    except ReferenceError:
        # Its scene was removed while the worker was finishing.
        pass
    except Exception:
        logging.getLogger(__name__).exception("LichtFeld failure callback failed")


def scene_key(scene):
    identifier = scene.get("lichtfeld_scene_uuid")
    pointer = scene.as_pointer()
    if not identifier or identifier in _owners and _owners[identifier] != pointer:
        identifier = uuid.uuid4().hex
        scene["lichtfeld_scene_uuid"] = identifier
    _owners[identifier] = pointer
    return identifier


def build(scene, frame, key):
    """Yield after every native bulk operation. Incomplete data is never displayed."""
    expected = scene.get("lichtfeld_expected_job")
    if expected and frame["job"]["id"] != expected:
        return
    if frame.get("geometry_only"):
        result = scene.lichtfeld_training.result_object
        if (result is None or result.get("lichtfeld_job_id") != frame["job"]["id"] or
                result.get("lichtfeld_artifact") != frame["record"]["id"]):
            raise RuntimeError("The displayed snapshot changed; reloading its model automatically")
        result.matrix_world = Matrix(source_model_transform(frame.get("geometry")).tolist())
        yield
        yield from sync_geometry_steps(scene, frame)
        return
    cloud = None
    committed = False
    old = None
    reusable = frame["record"].get("kind") == "preview"
    schema = (frame["count"], tuple((name, kind) for name, kind, prop, values in frame["data"]))
    try:
        buffered = _cloud_buffers.pop(key, None)
        if buffered:
            signature, candidate = buffered
            if signature == schema and candidate.users == 0 and reusable:
                cloud = candidate
            elif candidate.users == 0:
                bpy.data.pointclouds.remove(candidate)
            yield
        if cloud is None:
            cloud = bpy.data.pointclouds.new("LichtFeld prepared snapshot")
            cloud.type = "GAUSSIAN_SPLAT"
            yield
            cloud.resize(frame["count"])
            yield
        for name, kind, property_name, values in frame["data"]:
            attribute = cloud.attributes.get(name) or cloud.attributes.new(name, kind, "POINT")
            yield
            attribute.data.foreach_set(property_name, values)
            yield
        cloud.update_tag()
        yield
        job = frame["job"]
        existing = scene.lichtfeld_training.result_object
        previous_result = existing if existing and existing.get("lichtfeld_job_id") != job["id"] else None
        if previous_result:
            existing = None
        if existing is None:
            for index, obj in enumerate(scene.objects):
                if obj.get("lichtfeld_job_id") == job["id"] and len(obj.users_scene) == 1:
                    existing = obj
                    break
                if index % 50 == 0:
                    yield
        if existing:
            old = existing.data
            existing.data = cloud
            result = existing
        else:
            result = bpy.data.objects.new("LichtFeld Result", cloud)
            scene.collection.objects.link(result)
        result.matrix_world = Matrix(source_model_transform(frame.get("geometry")).tolist())
        committed = True
        result["lichtfeld_binding"] = key
        result["lichtfeld_result"] = True
        result["lichtfeld_job_id"] = job["id"]
        result["lichtfeld_iteration"] = frame["record"]["iteration"]
        result["lichtfeld_artifact"] = frame["record"]["id"]
        result["lichtfeld_transport"] = frame["transport"]
        result["lichtfeld_count"] = frame["count"]
        result["lichtfeld_kind"] = frame["record"].get("kind", "final")
        result["lichtfeld_sh_degree"] = int(round(math.sqrt(1 + sum(name.startswith("radiance:sh_") for name, kind in schema[1])) - 1))
        scene.lichtfeld_training.result_object = result
        from . import preview_scene
        preview_scene.rebind_data(scene)
        layer = scene.view_layers.get(job.get("view_layer", "")) or scene.view_layers[0]
        if previous_result and previous_result.name in layer.objects:
            set_hidden(previous_result, True, layer)
        if result.name in layer.objects:
            set_hidden(result, not scene.lichtfeld_training.show_result, layer)
        yield
        yield from source_steps(scene, job, scene.lichtfeld_training.show_result)
        yield from sync_geometry_steps(scene, frame)
        if old and old.users == 0:
            if reusable and len(old.points) == frame["count"] and all(
                    old.attributes.get(name) and old.attributes[name].data_type == kind
                    for name, kind, prop, values in frame["data"]) and len(old.attributes) == len(cloud.attributes):
                _cloud_buffers[key] = (schema, old)
            else:
                bpy.data.pointclouds.remove(old)
            old = None
            yield
    finally:
        if old is not None and old.users == 0:
            bpy.data.pointclouds.remove(old)
        if cloud and not committed and cloud.users == 0:
            bpy.data.pointclouds.remove(cloud)


def sync_geometry_steps(scene, frame):
    from . import preview_scene
    yield from preview_scene.sync_steps(scene)
    if frame["job"]["id"].startswith("session:"):
        from .session_cameras import install_steps
        yield from install_steps(scene, frame.get("geometry"), frame.get("geometry_revision", ""))


def assign(settings, name, value):
    """Only changed RNA values should invalidate Blender UI/dependency state."""
    previous = getattr(settings, name)
    if isinstance(previous, float) and isinstance(value, (int, float)):
        if math.isclose(previous, value, rel_tol=1e-6, abs_tol=1e-7):
            return
    if previous != value:
        setattr(settings, name, value)


class Binding:
    def __init__(self, directory, external, key):
        self.key = key
        self.reader = Observer(directory, external, prepare=blender_fields)
        self.stage = None
        self.frame = None
        self.max_ms = 0
        self.status = {}

    def close(self):
        if self.stage:
            try:
                self.stage.close()
            except Exception as error:
                report_failure(None, error)
            finally:
                self.stage = None
        self.frame = None
        self.reader.close()
        buffered = _cloud_buffers.pop(self.key, None)
        if buffered and buffered[1].users == 0:
            bpy.data.pointclouds.remove(buffered[1])

    def tick(self, scene, deadline=None):
        settings = scene.lichtfeld_training
        status = self.reader.status
        self.status = status
        phase = status.get("state", "connecting")
        if status.get("job", {}).get("id"):
            scene["lichtfeld_expected_job"] = status["job"]["id"]
        running = not self.reader.external and phase not in {"completed", "stopped", "failed", "controller_lost"}
        if phase == "controller_lost":
            running = status.get("backend_alive", False)
        assign(settings, "running", running or settings.operation_busy)
        assign(settings, "paused", phase == "paused")
        assign(settings, "previews_enabled", status.get("previews_enabled", True))
        assign(settings, "status", phase.replace("_", " ").capitalize())
        step = counter(status.get("iteration", 0))
        total = counter(status.get("iterations", status.get("job", {}).get("iterations", 0)))
        details = [f"Training step {step:,}" + (f" / {total:,}" if total else "")]
        if status.get("job", {}).get("capture_only"):
            details = [status.get("message", "Dataset capture only; no training")]
            if status.get("export_directory"):
                assign(settings, "dataset_job_directory", status["export_directory"])
                details.append("Dataset: " + status["export_directory"])
        if status.get("supervision"):
            details.append(status["supervision"])
        if status.get("preview_error"):
            details.append("Preview: " + status["preview_error"])
        best = status.get("best") or status.get("result")
        if best:
            label = "Best evaluated" if best.get("score") else "Last/final valid"
            details.append(f"{label} snapshot: step {counter(best.get('iteration')):,}")
        if settings.result_object:
            details.append(f"Shown: step {counter(settings.result_object.get('lichtfeld_iteration', 0)):,}")
            shown = counter(settings.result_object.get("lichtfeld_count", 0))
            available = counter((best or {}).get("count", 0) if phase in {"completed", "stopped", "failed"}
                                else status.get("count", 0))
            details.append(f"{shown:,} / {available:,} Gaussians" if available else f"{shown:,} Gaussians")
            if phase in {"training", "paused"} and not status.get("job", {}).get("full_preview", False):
                degree = status.get("job", {}).get("preview_sh_degree", 1)
                details.append(f"Limited preview: SH limit {degree}; saved results keep full quality")
        if status.get("checkpoint_available"):
            details.append("Resume checkpoint available (.licht)")
        if not self.reader.external and "control_available" in status and not status["control_available"]:
            details.append("Read only: " + str(status.get("control_reason") or "Control unavailable"))
        if status.get("command_error"):
            details.append("Control: " + str(status["command_error"]))
        if status.get("pending_command"):
            details.append("Requested: " + status["pending_command"])
        if phase == "finalizing capture":
            details = ["Images rendered; preparing initialization points and camera metadata"]
        if phase == "rendering":
            capture = status.get("capture", {})
            capture_total = capture.get("total", len(status.get("job", {}).get("cameras", [])))
            details = [f"Rendering view {capture.get('done', 0)} of {capture_total}"]
            assign(settings, "progress", min(1, counter(capture.get("done", 0)) / max(1, counter(capture_total))))
        else:
            assign(settings, "progress", min(1, step / max(1, total)) if total else 0)
        if phase in {"finalizing capture", "exporting dataset"}:
            assign(settings, "progress", 1.0)
        if phase == "completed" and status.get("job", {}).get("capture_only"):
            assign(settings, "progress", 1.0)
        error = self.reader.error or status.get("error")
        if error:
            details.append("Connection/import: " + str(error))
        assign(settings, "detail", "\n".join(details))
        # User-requested scene operations take priority. Keep monitoring and
        # trainer controls alive while the staged snapshot waits its turn.
        if settings.operation_busy:
            return
        if self.stage is None:
            frame = self.reader.take()
            if frame:
                self.frame = frame
                self.stage = build(scene, frame, self.key)
        assign(settings, "importing", self.stage is not None)
        if self.stage is not None:
            deadline = deadline if deadline is not None else time.perf_counter() + 0.003
            while time.perf_counter() < deadline:
                started = time.perf_counter()
                try:
                    next(self.stage)
                except StopIteration:
                    generation = self.frame["record"]["id"]
                    reference = self.frame.get("geometry_ref")
                    self.stage = None
                    self.frame = None
                    assign(settings, "importing", False)
                    self.reader.acknowledge(generation, display_seconds=settings.preview_hold_seconds, geometry_ref=reference)
                    break
                except Exception as error:
                    self.reader.acknowledge(self.frame["record"]["id"], str(error), geometry_ref=self.frame.get("geometry_ref"))
                    self.stage = None
                    self.frame = None
                    break
                finally:
                    self.max_ms = max(self.max_ms, (time.perf_counter() - started) * 1000)
        assign(settings, "ui_max_ms", self.max_ms)


def peek(scene):
    token = scene.get("lichtfeld_scene_uuid")
    return _bindings.get(token) if token else None


def binding(scene):
    settings = scene.lichtfeld_training
    directory = settings.attached_session or settings.job_directory
    external = bool(settings.attached_session)
    token = scene_key(scene)
    key = token + "|" + str(external) + "|" + directory
    previous = _bindings.get(token)
    if previous and previous.key != key:
        previous.close()
        _bindings.pop(token)
        previous = None
    if not directory:
        return None
    if previous is None:
        previous = _bindings[token] = Binding(directory, external, key)
    return previous


def tick():
    global _rotation
    dirty = bool(_actions)
    deadline = time.perf_counter() + 0.003
    while _actions and time.perf_counter() < deadline:
        try:
            next(_actions[0][0])
        except StopIteration:
            _actions.pop(0)
        except Exception as error:
            _, failure = _actions.pop(0)
            report_failure(failure, error)
    for future, callback, failure in list(_tasks):
        if not future.done():
            continue
        dirty = True
        _tasks.remove((future, callback, failure))
        try:
            value = future.result()  # Already done: never waits.
            if callback:
                callback(value)
        except Exception as error:
            report_failure(failure, error)
    active = set()
    scenes = list(bpy.data.scenes)
    if scenes:
        _rotation = (_rotation + 1) % len(scenes)
        scenes = scenes[_rotation:] + scenes[:_rotation]
    for scene in scenes:
        try:
            if scene.lichtfeld_training.attached_session or scene.lichtfeld_training.job_directory:
                current = binding(scene)
                active.add(scene["lichtfeld_scene_uuid"])
                current.tick(scene, deadline)
                signature = (scene.lichtfeld_training.detail, scene.lichtfeld_training.status,
                             scene.lichtfeld_training.importing, current.max_ms)
                dirty = dirty or signature != getattr(current, "draw_signature", None)
                current.draw_signature = signature
        except Exception as error:
            fail(scene, str(error))
            dirty = True
    for key in list(_bindings):
        if key not in active:
            _bindings.pop(key).close()
    if dirty:
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    return 0.02


def reset_for_new_training(scene, on_ready=None):
    """Detach the old run before setup; deleted old previews cannot reappear."""
    settings = scene.lichtfeld_training
    previous = peek(scene)
    job = dict(previous.status.get("job", {})) if previous else {}
    token = scene.get("lichtfeld_scene_uuid")
    if previous:
        previous.close()
        _bindings.pop(token, None)
    if settings.job_directory:
        settings.previous_job = settings.job_directory
    settings.job_directory = ""
    scene["lichtfeld_expected_job"] = "__preparing_new_training__"
    settings.result_object = None
    settings.running = False
    settings.importing = False
    settings.paused = False
    settings.progress = 0
    settings.status = "Preparing scene"
    settings.detail = ""
    settings.operation_busy = True
    settings.show_result = True
    def prepare():
        try:
            from .session_cameras import remove_steps
            yield from remove_steps(scene)
            for obj in scene.objects:
                if obj.get("lichtfeld_result"):
                    for layer in scene.view_layers:
                        set_hidden(obj, True, layer)
                yield
            if job:
                yield from source_steps(scene, job, False, restore_only=True)
                from .visibility import forget
                forget(scene, job)
            settings.operation_busy = False
            settings.status = "Ready for new training"
            if on_ready:
                on_ready()
        finally:
            settings.operation_busy = False
    schedule(prepare(), lambda error: report_failure(None, error))


def reconnect(scene):
    """Explicit replacement, including a stopped reader at the same job path."""
    token = scene_key(scene)
    previous = _bindings.pop(token, None)
    if previous:
        previous.close()
    return binding(scene)


def clear():
    from .visibility import clear as clear_visibility
    clear_visibility()
    for value in _bindings.values():
        value.close()
    _bindings.clear()
    for _, cloud in _cloud_buffers.values():
        if cloud.users == 0:
            bpy.data.pointclouds.remove(cloud)
    _cloud_buffers.clear()
    _owners.clear()
    _tasks.clear()
    for action, _ in _actions:
        action.close()
    _actions.clear()


def fail(scene, error):
    try:
        current = peek(scene)
        if current:
            current.close()
            current.reader.error = error
            current.reader.status = {"state": "failed", "error": error}
            current.status = current.reader.status
        scene.lichtfeld_training.operation_busy = False
        scene.lichtfeld_training.importing = False
        scene.lichtfeld_training.status = "Failed"
        scene.lichtfeld_training.running = False
        scene.lichtfeld_training.detail = error
    except ReferenceError:
        pass
