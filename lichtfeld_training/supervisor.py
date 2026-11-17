# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Detached supervisor for one Blender training job; never imports bpy."""

import os
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback

from jobs import release_source, captured_dataset, check_environment
from job_control import stop_requested, claim_training_start
from bridge_core import atomic_json, read_json, read_job_json, training_config, training_command, verify_artifact, digest, verified_training_outcome
from render_devices import arguments as render_arguments


_GEOMETRY_TEMP = re.compile(r"^lf_[0-9a-f]{32}_(?:Depth|Normal)_\d{6}_.+\.exr$")
_SPARSE_TEMP = re.compile(r"^(?:cameras|images|points3D)\.bin\.lf-[0-9a-f]{32}\.tmp$")


def cleanup_capture_temporary_files(job_root):
    """Remove only UUID-tagged intermediates owned by an interrupted capture.

    This runs after the renderer has exited because forced process termination
    skips its Python cleanup handlers. The capture root and directories are
    checked without following symlinks; captured images, recovery points,
    journal, maps, manifests and finalized sparse files are deliberately kept.
    """
    root = Path(job_root)
    if root.is_symlink() or not root.is_dir():
        return 0
    dataset = root / "dataset"
    if dataset.is_symlink() or not dataset.is_dir():
        return 0
    removed = 0
    geometry = dataset / "_geometry_passes"
    if not geometry.is_symlink() and geometry.is_dir():
        try:
            for path in geometry.iterdir():
                if _GEOMETRY_TEMP.fullmatch(path.name) and not path.is_symlink() and path.is_file():
                    path.unlink()
                    removed += 1
        except OSError:
            pass
        try:
            geometry.rmdir()
        except OSError:
            pass

    sparse = dataset / "sparse" / "0"
    sparse_parent = dataset / "sparse"
    if (not sparse_parent.is_symlink() and sparse_parent.is_dir() and
            not sparse.is_symlink() and sparse.is_dir()):
        try:
            for path in sparse.iterdir():
                if _SPARSE_TEMP.fullmatch(path.name) and not path.is_symlink() and path.is_file():
                    path.unlink()
                    removed += 1
        except OSError:
            pass
    return removed


def process_options():
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def stop_child(child):
    """Always reap a live child, even when graceful termination times out."""
    if child is None:
        return
    try:
        if child.poll() is None:
            child.terminate()
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=30)
    except subprocess.TimeoutExpired:
        try:
            child.kill()
        except ProcessLookupError:
            pass
        child.wait(timeout=30)


def supervise(job_path):
    job_path = Path(job_path)
    try:
        job = read_job_json(job_path)
        if not isinstance(job, dict) or not isinstance(job.get('root'), str) or not isinstance(job.get('id'), str):
            raise ValueError('Invalid training job configuration')
    except Exception as error:
        # Even errors before controller creation must finish the visible job.
        try:
            atomic_json(job_path.parent / 'state.json', {
                'job_id': job_path.parent.name, 'state': 'failed',
                'error': 'Cannot read training job: ' + str(error), 'updated': time.time()})
        except OSError:
            pass
        return
    root = Path(job["root"])
    root.mkdir(parents=True, exist_ok=True)
    owner = root / "supervisor.lock"
    # O_EXCL prevents two supervisors from writing into the same job.
    descriptor = os.open(owner, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(descriptor, str(os.getpid()).encode("ascii"))
    os.close(descriptor)
    child = None

    def state(phase, **values):
        atomic_json(root / "state.json", {"job_id": job["id"], "control_protocol": 1, "state": phase,
                                         "supervisor_pid": os.getpid(), "updated": time.time(), **values})

    def heartbeat():
        atomic_json(root / "heartbeat.json", {"updated": time.time(), "supervisor_pid": os.getpid()})

    try:
        if not job.get("capture_only"):
            state("validating backend")
            checked = check_environment(job["backend"], str(root.parent),
                                        backend_version=job.get("backend_version"),
                                        backend_identity=job.get("backend_identity"))
            if not checked.get("ok"):
                raise ValueError(checked.get("message", "Could not verify LichtFeld Studio"))
            job["backend_version"] = checked.get("backend_version")
            job["backend_identity"] = checked.get("backend_identity")
            atomic_json(root / "backend.json", {"executable": job["backend"], "version": checked.get("backend_version"),
                                               "profile_baseline": "23fb4b466"})
        if job.get("reuse_dataset"):
            captured_dataset(root, job)
        if not job.get("resume") and not job.get("reuse_dataset"):
            source = Path(job.get("capture_staging_file", job["source_file"]))
            if not source.is_file():
                raise RuntimeError("The saved source .blend is no longer available")
            if job.get("source_sha256") and digest(source) != job["source_sha256"]:
                raise ValueError("The immutable capture source was modified")
            job["source_sha256"] = digest(source)
            atomic_json(job_path, job)
            with (root / "render.log").open("wb") as log:
                for recovering in (False, True):
                    if stop_requested(root, job['id']):
                        state('stopped', message='Dataset capture stopped')
                        return
                    state('finalizing capture' if recovering else 'rendering',
                          message='Recovering completed images; no views will be rendered again' if recovering else '')
                    child = subprocess.Popen(
                        [job["blender"], "--background", "--factory-startup", "--disable-autoexec",
                         *(render_arguments(job['render_settings']['request']) if job.get('render_settings') else []),
                         str(root / 'capture.blend' if recovering else source), "--python-exit-code", "1",
                         "--python", str(Path(__file__).with_name("render_worker.py")),
                         "--", str(job_path), *(['--finalize-capture'] if recovering else [])],
                        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **process_options())
                    try:
                        finalizing = recovering
                        while child.poll() is None:
                            heartbeat()
                            capture_progress = read_json(root / "capture-progress.json", {})
                            if not finalizing and capture_progress.get("stage") == "initializing points":
                                state("finalizing capture", message="Images rendered; preparing initialization points and camera metadata")
                                finalizing = True
                            if stop_requested(root, job["id"]):
                                raise InterruptedError('Dataset capture stopped')
                            time.sleep(0.2)
                        if not child.returncode:
                            break
                        progress = read_json(root / "capture-progress.json", {})
                        complete = bool(job.get('cameras')) and progress.get('done') == len(job['cameras'])
                        if not recovering and complete and (root / 'capture.blend').is_file():
                            continue  # The worker owns journal/image validation, not the supervisor.
                        render_error = read_json(root / 'capture-render.json', {}).get('error')
                        if render_error:
                            raise RuntimeError(render_error)
                        if complete:
                            raise RuntimeError("All images were rendered, but dataset finalization failed. Images are preserved; see render.log.")
                        raise RuntimeError("Dataset rendering failed. Completed images are preserved; see render.log.")
                    finally:
                        render_child = child
                        try:
                            stop_child(child)
                        finally:
                            child = None
                            try:
                                if render_child.poll() is not None:
                                    cleanup_capture_temporary_files(root)
                            except Exception:
                                # Never delete files while a renderer may still own them.
                                pass
            release_source(job)
            captured_dataset(root, {name: job[name] for name in
                                   ('use_depth_loss', 'depth_loss_weight', 'use_normal_loss') if name in job})
        if stop_requested(root, job["id"]):
            state("stopped", message="Stopped before training started")
            return
        if job.get("capture_only"):
            from jobs import export_captured_dataset
            state("exporting dataset")
            destination = export_captured_dataset(job, heartbeat=heartbeat,
                cancelled=lambda: stop_requested(root, job["id"]))
            state("completed", export_directory=str(destination), message="Dataset exported. No training started.")
            return
        state("starting")
        environment_check = check_environment(job["backend"], str(root.parent),
                                              backend_version=job.get("backend_version"),
                                              backend_identity=job.get("backend_identity"))
        if not environment_check.get("ok"):
            raise ValueError(environment_check.get("message", "Could not verify LichtFeld Studio"))
        atomic_json(root / "training.json", training_config(job))
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment.pop("OCIO", None)
        environment["LFS_BLENDER_JOB"] = str(job_path)
        environment["PYTHONUTF8"] = "1"
        installation = hashlib.sha256(job["backend"].encode("utf-8")).hexdigest()[:16]
        environment["LFS_HOME"] = str(root.parent / "backend-runtime" / installation)
        with (root / "training.log").open("wb") as log:
            child = subprocess.Popen(
                training_command(job, Path(__file__).with_name("backend_hook.py")),
                cwd=str(Path(job["backend"]).parent), env=environment,
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                **process_options())
            state("starting", backend_pid=child.pid)
            starting = True
            while child.poll() is None:
                heartbeat()
                if starting:
                    if stop_requested(root, job["id"]) and claim_training_start(root):
                        stop_child(child)
                        child = None
                        state("stopped", message="Stopped before training started; saved results are unchanged")
                        return
                    if read_json(root / 'runtime.json', {}).get('hook_api_checked') is True:
                        starting = False
                        state("training", backend_pid=child.pid)
                time.sleep(0.5)
        runtime = read_json(root / "runtime.json", {})
        best = read_json(root / "best.json")
        if runtime.get('state') == 'stopped' and runtime.get('job_id') == job['id']:
            state('stopped', message=runtime.get('message', 'Stopped before training started'))
            return
        if child.returncode or runtime.get("error"):
            raise RuntimeError(runtime.get("error") or
                               f"LichtFeld exited with code {child.returncode}. See training.log.")
        if runtime.get("state") != "finalizing" or not best:
            raise RuntimeError("Training ended without a verified bridge result. Check training.log.")
        verify_artifact(root, best)
        outcome = verified_training_outcome(runtime)
        state(outcome, result=best,
              message="Best evaluated model" if best.get("score") else "Last valid model (not evaluated)")
    except InterruptedError as error:
        state("stopped", message=str(error))
    except Exception as error:
        # Reporting is best effort: a full/unavailable disk must never skip cleanup.
        try:
            state("failed", error=str(error), result=read_json(root / "best.json"))
        except Exception:
            pass
        try:
            with (root / "supervisor.log").open("a", encoding="utf-8") as log:
                log.write(traceback.format_exc())
        except OSError:
            pass
    finally:
        try:
            stop_child(child)
        finally:
            release_source(job)
            owner.unlink(missing_ok=True)


if __name__ == "__main__":
    supervise(Path(sys.argv[1]).resolve())
