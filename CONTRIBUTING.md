# Contributing

Use Blender 5.3.x and Python 3.11 or newer for packaging. Keep the extension and
its documentation separate from the LichtFeld backend repository. Backend changes
and this extension's job-control protocol have distinct owners and release cycles.

## Source conventions

- Keep `bpy` mutations on Blender's UI thread. Use yielding operations for large
  scene walks and staging; filesystem/process work belongs on workers.
- Access the training model at callback safe boundaries and pass owned arrays
  to background publication. Bound queues and release resources on every exit path.
- Preserve existing datasets/results. New captures and training runs use new
  job identities; restarting from images and resuming a checkpoint stay explicit.
- Add user-facing descriptions for operators/properties. Keep actions stable
  while a snapshot is being read or imported.
- Use English user/developer documentation. Keep local reports, screenshots,
  qualification logs and development histories outside the repository.
- Preserve copyright and SPDX notices. Document new dependencies or incorporated
  source in [CREDITS.md](CREDITS.md), including its actual license/permissions.

The current development version remains `0.1.0`; change it only for an intentional
release. Do not add source assets, downloaded scenes or native binaries to the
installable archive. The reviewed pure Python protocol wheel is its shared
LichtFeld dependency.

Keep additions focused on concrete behavior. Reuse existing workflow owners and
test suites; avoid duplicate protocol logic, redundant layers and test scaffolding
that merely repeats implementation details.

## Shared training protocol

`lichtfeld_sessions` is owned by LichtFeld Studio in
`src/python/lichtfeld_sessions`. Update it there and generate its wheel with
`python -m build --wheel --outdir /path/outside/source/wheels`. Replace the wheel
in `lichtfeld_training/wheels`, reviewing the source changes in Studio and the
wheel contents. Do not edit the wheel or copy its modules into this repository.
The distribution also includes LichtFeld Studio's existing `_lfs_path_io` module
for the shared Windows filesystem boundary; its source remains in LichtFeld Studio.
Blender installs it through the manifest's `wheels` entry; detached plain Python
workers import the same pure Python wheel directly when it is not installed.

For source-mode tests, install that local dependency first:

```sh
python -m pip install "numpy>=2,<3" lichtfeld_training/wheels/lichtfeld_training_protocol-0.1.0-py3-none-any.whl
```

## Build the extension

```sh
python tools/package_blender_training.py --output /path/outside/source/packages
blender --command extension validate /path/outside/source/packages/lichtfeld_training-0.1.0.zip
```

The packager checks the reviewed runtime inventory and Python syntax before
replacing an output ZIP. It includes license/credits and the user documentation.
Install it in an isolated Blender profile for package checks; do not overwrite
a user's installed extension or preferences during tests.

## Tests

Pure Python contracts require NumPy and do not open Blender:

```sh
python -m unittest discover -s tests/blender -p "test_*.py"
python -m unittest discover -s tests/packaging -p "test_*.py"
```

GitHub Actions runs these contracts on Windows and Linux with Python 3.11 and
3.13. After they pass, the package job builds the installable source ZIP and
uploads the `lichtfeld-training-extension` workflow artifact. GPU/Blender fixtures
remain separate from these CPU-only checks.

For a real capture/training smoke test, supply a compatible backend and a separate
scratch directory:

```sh
blender --background --factory-startup --python tests/blender/training_smoke.py -- /path/to/scratch /path/to/LichtFeld-Studio --steps 32
```

The fixture creates its own small scene and records the actual job path in the
scratch directory. `--geometry` enables depth/normal supervision; `--installed`
loads the installed extension instead of the source module. GUI fixtures need
a visible disposable Blender process. Some integration fixtures consume earlier
fixture outputs; inspect their arguments/setup before running them.

Keep all test-generated projects, images, logs, runtime environments and job
folders in an explicitly selected scratch area. Never drive, stop or replace an
existing user's Blender/Studio/trainer as part of a fixture. Targeted regression
tests should check observable contracts and failure recovery, not UI text alone.
