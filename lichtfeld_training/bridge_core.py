# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Job protocol and codecs. No Blender or LichtFeld imports in this module."""

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid

SCHEMA = 1
TERMINAL = {"completed", "stopped", "failed"}
MAX_JOB_CHARS = 32 * 1024 * 1024
MAX_DATASET_CHARS = 32 * 1024 * 1024


def atomic_json(path, value):
    if __package__:
        from .session_transport import replace_metadata
    else:
        from session_transport import replace_metadata
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(8):
            try:
                replace_metadata(temporary, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.005 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path, default=None, *, max_chars=262144):
    if __package__:
        from .session_transport import read_text_shared
    else:
        from session_transport import read_text_shared
    try:
        return json.loads(read_text_shared(path, max_chars=max_chars))
    except (FileNotFoundError, PermissionError):
        return default


def read_job_json(path, default=None):
    """Scene object lists are documents, rather than small live status messages."""
    return read_json(path, default, max_chars=MAX_JOB_CHARS)


def read_dataset_json(path, default=None):
    return read_json(path, default, max_chars=MAX_DATASET_CHARS)


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def artifact_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("Artifact path escapes the training job")
    return path


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def verify_artifact(root, record):
    path = artifact_path(root, record["path"])
    if path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]:
        raise ValueError("Splat is incomplete or failed its integrity check")
    return path


def evaluation_steps(iterations, interval):
    if iterations < 1 or interval < 1:
        raise ValueError("Iterations and evaluation interval must be positive")
    return sorted(set(range(interval, iterations + 1, interval)) | {iterations})


def preview_sh_limit(job):
    degree = job.get("preview_sh_degree", 1)
    if type(degree) is not int or not 0 <= degree <= 3:
        raise ValueError("Preview SH limit must be an integer from 0 to 3")
    return degree


def training_config(job):
    preview_sh_limit(job)
    steps = evaluation_steps(job["iterations"], job["eval_interval"])
    if job.get("optimizer") is not None:
        profiles = read_json(Path(__file__).with_name("training_profiles.json"))
        strategy = job.get("strategy", "mrnf")
        if strategy not in profiles:
            raise ValueError("Unsupported training strategy")
        optimization = dict(profiles[strategy])
        optimization.update(job["optimizer"])
    else:
        optimization = read_json(Path(__file__).with_name("mrnf_defaults.json"))
    optimization.update({
            "iterations": job["iterations"], "strategy": job.get("strategy", "mrnf"),
            "max_cap": job["max_points"], "sh_degree": job["sh_degree"],
            "eval_steps": steps, "save_steps": steps,
            "enable_eval": True, "eval_all": False,
            "enable_save_eval_images": False,
            "mask_mode": ("alpha_consistent" if job.get("scope", "SCENE") != "SCENE" else "none") if job.get("mask_mode","AUTO")=="AUTO" else job["mask_mode"],
        })
    if job.get("optimizer") is not None:
        schedule = job.get("optimizer_schedule", {})
        for field, fraction in (("start_refine", "refine_start"), ("stop_refine", "refine_end"), ("grow_until_iter", "growth_end")):
            if fraction in schedule:
                optimization[field] = max(0 if field == "start_refine" else 1,
                                          round(job["iterations"] * schedule[fraction]))
    else:
        optimization.update(use_ppisp=False, use_bilateral_grid=False,
                            use_exposure_correction=False, enable_sparsity=False,
                            keep_all_checkpoints=False,
                            stop_refine=max(1,int(job["iterations"]*.95)),
                            grow_until_iter=max(1,job["iterations"]//2))
    optimization.update(use_depth_loss=job.get("use_depth_loss", False),
        depth_loss_weight=job.get("depth_loss_weight", 2.0), depth_loss_mode="ssi-depth",
        depth_auto_generate=False, use_normal_loss=job.get("use_normal_loss", False),
        normal_auto_generate=False, normal_loss_space="camera-opencv",
        normal_loss_weight=job.get("normal_loss_weight", .005),
        normal_start_fraction=job.get("normal_start_fraction", .08) if job.get("use_normal_loss") else .08,
        normal_end_fraction=job.get("normal_end_fraction", 1.0) if job.get("use_normal_loss") else 1.0)
    return {
        "optimization": optimization,
        "dataset": {"test_every": job["test_every"], "centralize_dataset": "off", "min_track_length":1},
    }


def training_command(job, hook):
    root = Path(job["root"])
    args = [job["backend"], "--headless", "-o", str(root / "output"),
            "--config", str(root / "training.json"), "--eval", "--export", "ply",
            "--test-every", str(job.get("test_every", 8)), "--centralize", "off",
            "--python-script", str(hook), "--no-download"]
    if job.get("resume"):
        args += ["--resume", job["resume"]]
    else:
        args += ["-d", str(Path(job.get("dataset_root") or root / "dataset"))]
    return args


def metric_at(path, iteration):
    try:
        with Path(path).open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
    except FileNotFoundError:
        return None
    for row in reversed(rows):
        if int(row["iteration"]) == iteration:
            psnr = float(row["psnr"])
            if math.isfinite(psnr):
                return {"psnr": psnr, "ssim": float(row["ssim"]),
                        "iteration": iteration}
    return None


def better(candidate, previous):
    if not math.isfinite(candidate["psnr"]):
        return False
    if previous is None:
        return True
    delta = candidate["psnr"] - previous["psnr"]
    if abs(delta) > 1e-6:
        return delta > 0
    return (candidate["count"], candidate["iteration"]) < (
        previous["count"], previous["iteration"])


def write_splat(root, arrays, iteration, kind, score=None):
    """Write detached canonical parameters as GraphDeco binary PLY."""
    import numpy as np
    root = Path(root)
    count = len(arrays["means"])
    if not count:
        raise ValueError("The training model is empty")
    sh = arrays["sh"]
    degree = int(round(math.sqrt(sh.shape[1]))) - 1
    if sh.shape != (count, (degree + 1) ** 2, 3) or not 0 <= degree <= 3:
        raise ValueError("Unsupported spherical harmonic layout")
    fields = ["x", "y", "z", "nx", "ny", "nz"]
    fields += [f"f_dc_{i}" for i in range(3)]
    fields += [f"f_rest_{i}" for i in range((sh.shape[1] - 1) * 3)]
    fields += ["opacity"] + [f"scale_{i}" for i in range(3)]
    fields += [f"rot_{i}" for i in range(4)]
    values = np.concatenate((arrays["means"], np.zeros((count, 3), dtype=np.float32),
                             sh[:, 0, :], sh[:, 1:, :].transpose(0, 2, 1).reshape(count, -1),
                             arrays["opacity"].reshape(count, 1), arrays["scale"],
                             arrays["rotation"]), axis=1).astype("<f4", copy=False)
    if values.shape != (count, len(fields)) or not np.isfinite(values).all():
        raise ValueError("Invalid Gaussian parameters")
    relative = f"artifacts/{kind}-{iteration:08d}-{uuid.uuid4().hex[:10]}.ply"
    path = artifact_path(root, relative)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    created = False
    try:
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(fd, "wb") as stream:
            header = "ply\nformat binary_little_endian 1.0\n"
            header += f"element vertex {count}\n"
            header += "".join(f"property float {field}\n" for field in fields)
            stream.write((header + "end_header\n").encode("ascii"))
            values.tofile(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(partial, path)
    finally:
        if created:
            partial.unlink(missing_ok=True)
    return {"id": path.stem, "path": relative, "iteration": iteration, "kind": kind,
            "count": count, "sh_degree": degree, "bytes": path.stat().st_size,
            "sha256": digest(path), "score": score, "created": time.time()}
