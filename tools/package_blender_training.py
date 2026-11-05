#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the installable Blender extension without native dependencies."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile

RUNTIME_FILES = frozenset("""
__init__.py backend_hook.py blender_manifest.toml bridge_core.py
capture_observations.py capture_passes.py capture_preview.py capture_seed.py
capture_settings.py geometry_maps.py job_control.py job_panel.py jobs.py
mrnf_defaults.json preflight.py preview_scene.py render_devices.py render_worker.py
rig_edit.py rig_geometry.py rig_mesh.py rig_path.py scene_render.py
scene_render_worker.py session_cameras.py session_geometry.py session_reader.py
wheels/lichtfeld_training_protocol-0.1.0-py3-none-any.whl supervisor.py training_options.py training_profiles.json
ui_help.py ui_runtime.py ui_text.py visibility.py workflow.py
""".split())
DOCUMENT_FILES = ("LICENSE", "CREDITS.md", "README.md", "CONTRIBUTING.md",
                  "docs/user-guide.md", "docs/technical-reference.md")


def build_protocol(lichtfeld_source):
    """Build the bundled dependency from its canonical LichtFeld Studio checkout."""
    source = Path(lichtfeld_source).resolve()
    destination = Path(__file__).resolve().parents[1] / "lichtfeld_training/wheels"
    with tempfile.TemporaryDirectory() as directory:
        staging = Path(directory)
        shutil.copytree(source / "src/python/lichtfeld_sessions", staging / "lichtfeld_sessions",
                        ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("src/python/_lfs_path_io.py", "LICENSE"):
            shutil.copyfile(source / name, staging / Path(name).name)
        (staging / "pyproject.toml").write_text('''[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "lichtfeld-training-protocol"
version = "0.1.0"
description = "Local training sessions shared with LichtFeld Studio"
requires-python = ">=3.11"
license = "GPL-3.0-or-later"
license-files = ["LICENSE"]
authors = [{name = "LichtFeld Studio Authors"}]

[tool.setuptools]
packages = ["lichtfeld_sessions"]
py-modules = ["_lfs_path_io"]
''', encoding="utf-8")
        subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation",
                        "--outdir", str(staging / "dist")], cwd=staging, check=True,
                       env={**os.environ, "SOURCE_DATE_EPOCH": "315532800"})
        wheel = staging / "dist/lichtfeld_training_protocol-0.1.0-py3-none-any.whl"
        destination.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(prefix=wheel.name + "-", suffix=".tmp", dir=destination)
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(wheel.read_bytes())
            temporary.replace(destination / wheel.name)
        finally:
            temporary.unlink(missing_ok=True)


def package(output, *, repository=None):
    repository = Path(repository or Path(__file__).resolve().parents[1]).resolve()
    source = repository / "lichtfeld_training"
    paths = [path for path in source.rglob("*") if "__pycache__" not in path.parts]
    if any(path.is_symlink() for path in paths):
        raise ValueError("Package sources must not contain symlinks")
    actual = {path.relative_to(source).as_posix() for path in paths if path.is_file()}
    if actual != RUNTIME_FILES:
        raise ValueError(f"Runtime inventory changed: missing={sorted(RUNTIME_FILES-actual)}, "
                         f"unexpected={sorted(actual-RUNTIME_FILES)}. Review the package inventory.")
    files = {name: source / name for name in RUNTIME_FILES}
    files.update({name: repository / name for name in DOCUMENT_FILES})
    payloads = {}
    for name, path in sorted(files.items()):
        if path.is_symlink() or not path.resolve().is_relative_to(repository):
            raise ValueError(f"Package source must stay inside the repository: {name}")
        data = path.read_bytes()
        if not data:
            raise ValueError(f"Package source is empty: {name}")
        if path.suffix == ".py":
            compile(data, name, "exec")
        elif path.suffix == ".json":
            json.loads(data)
        payloads[name] = data
    manifest = tomllib.loads((source / "blender_manifest.toml").read_text(encoding="utf-8"))
    declared_wheels = {name.removeprefix("./") for name in manifest.get("wheels", [])}
    if declared_wheels != {name for name in RUNTIME_FILES if name.endswith(".whl")}:
        raise ValueError("Manifest wheel dependencies differ from the reviewed package inventory")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", manifest["id"]) or not re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", manifest["version"]):
        raise ValueError("Invalid extension ID or version")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"{manifest['id']}-{manifest['version']}.zip"
    descriptor, filename = tempfile.mkstemp(prefix=destination.name+"-", suffix=".tmp", dir=output)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as stream, zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(payloads.items()):
                entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--lichtfeld-source", type=Path,
                        help="Rebuild the protocol wheel from this LichtFeld Studio checkout before packaging")
    arguments = parser.parse_args()
    if arguments.lichtfeld_source:
        build_protocol(arguments.lichtfeld_source)
    print(package(arguments.output).resolve())
