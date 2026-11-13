# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Filesystem/process work for operators. Never import bpy."""
import os
import hashlib
import json
import math
import tempfile
from pathlib import Path
import shutil
import subprocess
import uuid
if __package__:
    from .bridge_core import atomic_json, read_json, read_job_json, read_dataset_json, verify_artifact, preview_sh_limit, SCHEMA
    from .capture_settings import validate_view_count
else:
    from bridge_core import atomic_json, read_json, read_job_json, read_dataset_json, verify_artifact, preview_sh_limit, SCHEMA
    from capture_settings import validate_view_count


def verify_backend(path):
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        result = subprocess.run([path, "--version"], capture_output=True, timeout=20, **flags)
        text = (result.stdout + result.stderr).decode("utf-8", errors="replace").strip()
        if result.returncode or "LichtFeld" not in text:
            raise ValueError("The selected LichtFeld Studio executable cannot start")
        version = next((line for line in text.splitlines() if line.startswith("LichtFeld")), text.splitlines()[-1])
        probe = subprocess.run([path, "plugin", "run", str(Path(__file__).with_name("bridge_core.py"))],
                               capture_output=True, timeout=60, **flags)
    except subprocess.TimeoutExpired:
        raise ValueError("LichtFeld Studio took too long to verify. Check the selected installation and try again.") from None
    if probe.returncode or b"LFS_BLENDER_API_READY" not in probe.stdout:
        from lichtfeld_portal.gallery_logging import safe_text
        detail = safe_text((probe.stdout + probe.stderr).decode("utf-8", errors="replace"))[-700:].strip()
        raise ValueError("The training API compatibility check failed. Update LichtFeld Studio or choose another installation." + (" " + detail if detail else ""))
    return version + '. Training APIs verified.'


def prepare_optimizer(job):
    """Apply expert scalar parameters before launch; never override UI/protocol fields."""
    filename = job.get('optimizer_file')
    if not filename or job.get('resume'):
        return
    path = Path(filename)
    if path.stat().st_size > 512*1024:
        raise ValueError('Optimizer JSON exceeds 512 KiB')
    values = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(values,dict):
        raise ValueError('Optimizer JSON must be an object of native parameter names and values')
    if __package__:
        from .training_options import PROFILES, FIELDS, validate
    else:
        from training_options import PROFILES, FIELDS, validate
    profile = PROFILES[job.get('strategy','mrnf')]
    managed = set(FIELDS) | {
        'iterations','strategy','max_cap','sh_degree','eval_steps','save_steps',
        'enable_eval','eval_all','enable_save_eval_images','keep_all_checkpoints',
        'start_refine','stop_refine','grow_until_iter','mask_mode',
        'use_depth_loss','depth_loss_weight','depth_loss_mode','depth_auto_generate',
        'use_normal_loss','normal_loss_weight','normal_loss_space','normal_auto_generate',
        'normal_start_fraction','normal_end_fraction','headless','auto_train',
        'debug_python','debug_python_port',
    }
    def valid(value, expected):
        if isinstance(expected,bool):return isinstance(value,bool)
        if isinstance(expected,int):return type(value) is int and abs(value)<2**31
        if isinstance(expected,float):return type(value) in (int,float) and math.isfinite(value) and abs(value)<1e30
        if isinstance(expected,str):return isinstance(value,str) and len(value)<256
        if isinstance(expected,list):return isinstance(value,list) and len(value)==len(expected) and all(valid(v,e) for v,e in zip(value,expected))
        return False
    for name,value in values.items():
        if name in managed:
            raise ValueError(f'Use the plugin controls for managed parameter: {name}')
        if name not in profile or not valid(value,profile[name]):
            raise ValueError(f'Unknown native parameter or invalid value: {name}')
    errors = validate(job.get('strategy','mrnf'),{**job.get('optimizer',{}),**values})
    if errors:raise ValueError(' '.join(errors))
    job.setdefault('optimizer',{}).update(values)
    job['optimizer_overrides'] = values


def resume_data(directory, iterations, *, backend, jobs_directory, connection_directory="", preview_options=None):
    # Executable authority comes only from preferences, never imported job data.
    if not isinstance(backend, str) or not backend.strip():
        raise ValueError("Choose the LichtFeld executable in Settings before continuing")
    previous = Path(directory).resolve()
    job = read_job(previous / "job.json")
    job.setdefault("full_preview", True)
    job.setdefault("preview_sh_degree", 1)
    job.setdefault("preview_mode", "LIVE")
    preview_options = preview_options or {}
    if set(preview_options) - {"preview_mode", "preview_seconds", "preview_points", "preview_sh_degree", "full_preview"}:
        raise ValueError("Continuation can only adjust preview settings; its optimizer state is restored")
    job.update(preview_options)
    preview_sh_limit(job)
    job["backend"] = backend
    job["connection_directory"] = connection_directory
    checkpoint = previous / "output/project.licht"
    if not checkpoint.is_file():
        raise ValueError("No saved checkpoint is available")
    step = read_json(previous / "runtime.json", {}).get("iteration", 0)
    if iterations <= step:
        raise ValueError(f"Increase Training steps above {step:,}")
    if not job.get('dataset_root'):
        origin = job
        visited = set()
        while origin.get('parent_job'):
            parent = origin['parent_job']
            if parent in visited:
                raise ValueError('The job ancestry contains a cycle')
            visited.add(parent)
            origin = read_job(Path(parent)/'job.json')
            if not origin:
                raise ValueError('The original captured dataset cannot be located')
        job['dataset_root'] = str(Path(origin['root'])/'dataset')
    previous_id = job["id"]
    identifier = uuid.uuid4().hex
    root = Path(jobs_directory).resolve() / identifier
    root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.mkdir(mode=0o700)
    try:
        job.update(id=identifier, root=str(root), parent_job=str(previous), resume=str(checkpoint),
                   iterations=iterations, resume_iteration=step)
        best = read_json(previous / "best.json")
        if best and best.get("score"):
            source = verify_artifact(previous, best)
            relative = f"artifacts/inherited-{uuid.uuid4().hex}.ply"
            destination = root / relative
            destination.parent.mkdir(mode=0o700)
            with source.open("rb") as src:
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            inherited = {**best, "id": uuid.uuid4().hex, "path": relative,
                         "origin_job": best.get("origin_job", str(previous))}
            verify_artifact(root, inherited)
            atomic_json(root / "best.json", inherited)
        return job, previous_id
    except BaseException:
        # Only this fresh UUID tree is owned by this preparation transaction.
        shutil.rmtree(root)
        raise


def read_job(path):
    job = read_job_json(path)
    if (not isinstance(job, dict) or type(job.get("schema")) is not int
            or job["schema"] != SCHEMA or not isinstance(job.get("id"), str)
            or not job["id"] or len(job["id"]) > 256):
        raise ValueError("Choose a LichtFeld Training job.json")
    for name in ("root", "dataset_root", "parent_job", "source_file", "scene"):
        if name in job and (not isinstance(job[name], str) or "\0" in job[name]):
            raise ValueError(f"Invalid training job field: {name}")
    return job


def load_job(path):
    path = Path(path).resolve()
    read_job(path)
    return str(path.parent)


def open_folder(path):
    if not Path(path).is_dir():
        raise ValueError("The job folder is not available")
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if os.sys.platform == "darwin" else "xdg-open", path])


def check_environment(backend, directory, source="", require_backend=True, *, backend_version=None, backend_identity=None):
    if require_backend and not backend:
        return {"ok":False,"backend":False,"folder":False,"message":"Choose the LichtFeld executable in Settings."}
    identity = None
    if require_backend:
        info = Path(backend).stat()
        identity = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
        version = backend_version if backend_version is not None and identity == backend_identity else verify_backend(backend)
        info = Path(backend).stat()
        if identity != [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]:
            raise ValueError("LichtFeld Studio changed during verification. Check the installation again.")
    else:
        version = "Dataset capture folder is writable"
    try:
        root=Path(directory)
        root.mkdir(mode=0o700,parents=True,exist_ok=True)
        with tempfile.TemporaryFile(dir=root) as probe:probe.write(b"check")
        result = {"ok":True,"backend":True,"folder":True,"message":version,"directory":str(root),
                  "backend_version":version,"backend_identity":identity}
        if source:
            path = Path(source)
            info = path.stat()
            with tempfile.TemporaryFile(dir=path.parent) as probe:
                probe.write(b"capture")
            result["source_identity"] = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
    except (OSError, ValueError) as error:
        return {"ok":False,"backend":True,"folder":False,"message":str(error),
                "backend_version":version,"backend_identity":identity}
    return result


def private_snapshot_file(path):
    """Create privately even in a shared Windows project directory."""
    if os.name!='nt':return os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    import ctypes,msvcrt
    adv=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    descriptor=ctypes.c_void_p()
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[ctypes.c_wchar_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;OW)',1,ctypes.byref(descriptor),None):
        raise ctypes.WinError(ctypes.get_last_error())
    class Attributes(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('descriptor',ctypes.c_void_p),('inherit',ctypes.c_int)]
    attributes=Attributes(ctypes.sizeof(Attributes),descriptor,False)
    kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.POINTER(Attributes),ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p]
    kernel.CreateFileW.restype=ctypes.c_void_p;kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    try:
        handle=kernel.CreateFileW(str(path),0x40000000,1,ctypes.byref(attributes),1,0x102,None)
        if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
        try:return msvcrt.open_osfhandle(handle,os.O_WRONLY|os.O_BINARY)
        except Exception:kernel.CloseHandle(handle);raise
    finally:kernel.LocalFree(descriptor)


def freeze_source(job):
    source=Path(job['source_file'])
    staged=source.with_name('.lichtfeld-capture-'+job['id']+'.blend')
    digest=hashlib.sha256()
    created=False
    try:
        src=source.open("rb")
        try:
            fd=private_snapshot_file(staged)
            created=True
        except Exception:
            src.close()
            raise
        with src,os.fdopen(fd,"wb") as dst:
            before=os.fstat(src.fileno())
            identity = [before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns]
            if job.get("source_identity") and identity != job["source_identity"]:
                raise ValueError("The saved scene changed after validation. Save and start again.")
            for block in iter(lambda:src.read(4*1024*1024),b''):
                digest.update(block);dst.write(block)
            after=os.fstat(src.fileno())
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                raise ValueError("The saved source changed while preparing capture")
        job["capture_staging_file"]=str(staged)
        job["source_sha256"]=digest.hexdigest()
        return job
    except Exception:
        if created:staged.unlink(missing_ok=True)
        raise


def release_source(job):
    staged=job.get('capture_staging_file')
    if not staged:return
    original=Path(job['source_file'])
    expected=original.with_name('.lichtfeld-capture-'+job['id']+'.blend')
    if Path(staged)!=expected:return
    try:expected.unlink(missing_ok=True)
    except OSError:pass


def remember_job(directory,job):
    key=hashlib.sha256((os.path.normcase(os.path.abspath(job['source_file']))+'\0'+job['scene']).encode()).hexdigest()
    atomic_json(Path(directory)/(key+'.json'),{'job_root':job['root'],'source_file':job['source_file'],'scene':job['scene']})


def remembered_job(directory,source,scene):
    key=hashlib.sha256((os.path.normcase(os.path.abspath(source))+'\0'+scene).encode()).hexdigest()
    record=read_json(Path(directory)/(key+'.json'),{})
    root=record.get('job_root')
    if root and (Path(root)/'job.json').is_file():return root
    return None


def captured_dataset(directory, options=None):
    """Validate saved capture independently of the current .blend and checkpoint."""
    from pathlib import Path
    if __package__:
        from .bridge_core import artifact_path, digest
    else:
        from bridge_core import artifact_path, digest
    previous = Path(directory).resolve()
    if previous.name == "job.json":
        previous = previous.parent
    job = read_job(previous / "job.json")
    origin = job
    visited = set()
    for _ in range(64):
        candidate = Path(origin.get("dataset_root") or Path(origin.get("root", str(previous))) / "dataset").resolve()
        if (candidate / "manifest.json").is_file():
            break
        parent = origin.get("parent_job")
        if not parent or parent in visited:
            raise ValueError("The saved dataset is missing. Choose its original job folder.")
        visited.add(parent)
        origin = read_job(Path(parent) / "job.json")
    else:
        raise ValueError("The dataset ancestry is too deep")
    manifest = read_dataset_json(candidate / "manifest.json", {})
    views = manifest.get("views")
    validate_view_count(views)
    transforms_path = artifact_path(candidate, manifest.get("transforms_file", "transforms.json"))
    if not transforms_path.is_file() or digest(transforms_path) != manifest.get("transforms_sha256"):
        raise ValueError("The saved camera poses are missing or changed")
    frames = read_dataset_json(transforms_path, {}).get("frames", [])
    if not isinstance(frames, list) or len(frames) != views:
        raise ValueError("The saved view count does not match its camera poses")
    for frame in frames:
        for field in ("file_path", "mask_path"):
            relative = frame.get(field)
            if (field == "file_path" or relative) and (not isinstance(relative, str) or
                    not artifact_path(candidate, relative).is_file()):
                raise ValueError("A saved image or foreground mask is missing")
    if not (candidate / "pointcloud.ply").is_file():
        raise ValueError("The saved initialization point cloud is missing")
    options = options or {}
    if options.get("use_depth_loss") and options.get("depth_loss_weight", 2) > 0 and manifest.get("dataset_format") != "COLMAP":
        raise ValueError("This dataset has no training depth anchors. Capture with depth supervision enabled first.")
    if job.get("scope") == "SCENE" and options.get("mask_mode", "AUTO") not in {"AUTO", "none"}:
        raise ValueError("Whole-scene captures have no foreground mask. Choose Automatic or None.")
    for enabled, folder, extension in (("use_depth_loss", "depths", ".tiff"),
                                        ("use_normal_loss", "normals", ".tiff")):
        if options.get(enabled) and not job.get(enabled):
            raise ValueError("This dataset has no " + folder + ". Capture those maps first.")
        if options.get(enabled):
            # Capture uses the image basename for each supervision map.
            for frame in frames:
                name = Path(frame["file_path"]).stem + extension
                if not (candidate / folder / name).is_file():
                    raise ValueError("A saved supervision map is missing: " + folder + "/" + name)
    coverage = [frame.get("mask_foreground_fraction") for frame in frames]
    if coverage and all(type(value) in (int, float) and 0 <= value <= 1 for value in coverage):
        manifest = {**manifest, "foreground_fraction": sum(coverage) / len(coverage)}
    return job, previous, candidate, manifest


def restart_data(directory, options, *, backend, jobs_directory, connection_directory=""):
    """Fresh model/optimizer, original capture, separate outputs; no source execution."""
    if not isinstance(backend, str) or not backend.strip():
        raise ValueError("Choose the LichtFeld executable in Settings")
    if __package__:
        from .workflow import TRAINING_FIELDS
        from .training_options import validate
    else:
        from workflow import TRAINING_FIELDS
        from training_options import validate
    unexpected = set(options) - set(TRAINING_FIELDS)
    if unexpected:
        raise ValueError("Capture and execution settings cannot override a reused dataset")
    original, previous, dataset, manifest = captured_dataset(directory, options)
    job = {**original, **options}
    errors = validate(job.get("strategy", "mrnf"), job.get("optimizer", {}))
    schedule = job.get("optimizer_schedule", {})
    if schedule.get("refine_start", 0) >= schedule.get("refine_end", .95):
        errors.append("Refinement start must precede its end")
    if schedule.get("growth_end", .5) > schedule.get("refine_end", .95):
        errors.append("Growth must end before refinement ends")
    if job.get("use_normal_loss") and job.get("normal_start_fraction", .08) >= job.get("normal_end_fraction", 1):
        errors.append("Normal supervision start must precede its end")
    if errors:
        raise ValueError(" ".join(errors))
    for key in ("resume", "resume_iteration", "capture_staging_file", "source_identity", "source_sha256", "optimizer_overrides", "capture_only", "export_parent"):
        job.pop(key, None)
    identifier = uuid.uuid4().hex
    root = Path(jobs_directory).resolve() / identifier
    job.update(id=identifier, root=str(root), parent_job=str(previous),
               dataset_root=str(dataset), reuse_dataset=True, backend=backend,
               connection_directory=connection_directory, dataset_views=manifest["views"])
    # Validate the native configuration before allocating the new job directory.
    if __package__:
        from .bridge_core import training_config
    else:
        from bridge_core import training_config
    training_config(job)
    root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.mkdir(mode=0o700)
    return job, original["id"]


def check_export_destination(job):
    """Reject unavailable exports before spending time rendering their images."""
    try:
        parent = Path(job['export_parent']).resolve(strict=True)
    except OSError as error:
        raise ValueError('Choose an existing, writable destination folder for the dataset') from error
    if not parent.is_dir():
        raise ValueError('Choose an existing destination folder')
    source = Path(job.get('dataset_root') or Path(job['root'])/'dataset').resolve()
    if parent.is_relative_to(source):
        raise ValueError('Choose a destination outside the source dataset')
    with tempfile.TemporaryFile(dir=parent) as probe:
        probe.write(b'capture export')
    return parent


def export_captured_dataset(job, heartbeat=lambda: None, cancelled=lambda: False):
    """Publish a standalone, reusable capture without overwriting existing data."""
    original, previous, source, manifest = captured_dataset(job['root'])
    parent = check_export_destination(job)
    identifier = uuid.uuid4().hex
    destination = parent / ('lichtfeld-dataset-' + identifier[:12])
    staging = parent / ('.lichtfeld-export-' + identifier + '.partial')
    staging.mkdir(mode=0o700)
    try:
        for directory, dirs, files in os.walk(source, followlinks=False):
            directory = Path(directory)
            if directory.is_symlink() or any((directory / name).is_symlink() or not (directory / name).resolve().is_relative_to(source) for name in dirs + files):
                raise ValueError('Dataset export does not follow symbolic links')
            relative = directory.relative_to(source)
            target = staging / relative
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            for name in files:
                if relative == Path('.') and name == 'job.json':
                    continue
                if cancelled():
                    raise InterruptedError('Dataset export cancelled')
                with (directory / name).open('rb') as src, (target / name).open('xb') as dst:
                    while block := src.read(4 * 1024 * 1024):
                        if cancelled():
                            raise InterruptedError('Dataset export cancelled')
                        dst.write(block)
                        heartbeat()
        exported = {**original, 'root': str(destination), 'dataset_root': str(destination),
                    'connection_directory': '', 'capture_only': True}
        for key in ('capture_staging_file', 'source_identity', 'source_sha256', 'export_parent', 'resume'):
            exported.pop(key, None)
        atomic_json(staging / 'job.json', exported)
        # Validate the copied files before publishing. Resolve against staging here.
        check = {**exported, 'root': str(staging), 'dataset_root': str(staging)}
        atomic_json(staging / 'job.json', check)
        captured_dataset(staging)
        atomic_json(staging / 'job.json', exported)
        if cancelled():
            raise InterruptedError('Dataset export cancelled')
        if destination.exists():
            raise FileExistsError('The export destination already exists')
        staging.rename(destination)
        return destination
    finally:
        if staging.exists():
            shutil.rmtree(staging)
