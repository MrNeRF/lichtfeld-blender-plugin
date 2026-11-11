# Technical reference

## Process boundaries

The installed extension registers Blender properties, operators and panels.
Blender's UI thread owns `bpy` scene changes. Cooperative operations yield while
walking large scenes or staging preview buffers. Worker threads perform filesystem
checks, process supervision, hashing and snapshot reads. Workers never mutate
Blender data directly.

Capture runs a separate Blender process with a frozen render configuration and
an isolated saved scene. Training runs a separate LichtFeld process, which loads
the extension's callback adapter through the backend's script API. `backend_hook.py`
is an internal implementation file; users install the extension ZIP.

The adapter accesses the training model only at callback safe boundaries. It
hands owned CPU arrays to a single publication writer. The training model is not
shared directly with Blender. External previews use CPU mappings, not GPU buffer
sharing or rendered images. Studio is optional for extension-owned training.

## Job and dataset files

| File or directory | Purpose |
| --- | --- |
| `job.json` | Validated job configuration and source/capture settings |
| `state.json`, `runtime.json` | Supervisor and training status |
| `capture-render.json` | Frozen renderer/device request and capture progress |
| `capture.blend` | Isolated project used by the render worker |
| `capture-frames.json`, `capture-progress.json` | Capture calibration journal and completed-view progress |
| `dataset/images/` | Captured RGB images |
| `dataset/transforms.json` | Calibrated camera poses/intrinsics for ordinary capture |
| `dataset/capture_transforms.json` | Original capture calibration when using COLMAP observations |
| `dataset/sparse/0/` | COLMAP binary calibration, points and tracks for positive depth supervision |
| `dataset/pointcloud.ply` | Bounded initialization cloud |
| `dataset/depths/`, `dataset/normals/` | Optional numerical maps |
| `dataset/view_geometry.json` | Optional source-frame/model/camera metadata |
| `output/project.licht` | Saved model/optimizer checkpoint for continuation |
| `best.json` | Reference to the retained best/last completed splat artifact |

Not every job contains every file: dataset-only tasks have no trainer output;
training on saved images references its existing dataset. Stored result records
identify their actual artifact paths. Readers validate job identity, safe artifact
paths, shape/size limits and integrity before import. Temporary export names are
published atomically after a complete, validated capture.

Capture count validation has one owner in `capture_settings`, shared by readiness,
capture, initialization and saved-dataset validation. The supported range is
4–10,000 views. Readiness, job creation and framing use name order, including
cameras in nested collections.

`capture_settings.camera_pose` owns evaluated camera-transform validation for
readiness, job creation, framing copies and render calibration. It normalizes
orthogonal axes using Blender's scale-free camera-frame convention, preserving
world translation. Non-finite, degenerate, sheared and negative-determinant frames
are rejected with the camera name. Copy normalization never edits camera sources
or parent objects; the validation action only selects the named camera.

`rig_edit.commit` validates generated indices and aim-helper uniqueness before
mutating existing cameras, retaining the existing staged update/rollback path.
`rig_parameter_source` records which collection last supplied or received the
scene's rig controls. Selection alone never changes those controls; Apply and
viewport aim require the selected managed rig to match that source. Recipe
decoding and object-reference checks finish before Load changes the controls.

After a render-worker failure with complete image progress, the supervisor reaps
the worker before attempting `--finalize-capture` once against `capture.blend`.
The existing worker validates journal identity, count, camera calibration and
saved maps; the supervisor does not implement a second decoder. Cancellation
prevents the retry and remains available during finalization. Successful recovery
clears the earlier render error, and the existing saved-dataset validator checks
the resulting dataset before export or training.

Fresh training on saved images creates a separate job with the same immutable
capture input and a new optimizer/model. Checkpoint continuation is explicit and
preserves the source job's outputs. A single controller lease protects commands.
Command records carry job identity and expiry; stale commands cannot silently
control a replacement job.

Before the first training step, the script bootstrap and supervisor arbitrate
cancellation through one exclusive `startup.claim` file in the fresh job root.
The supervisor can terminate its child during startup only if it wins that
claim. Once the script wins, numerical dependencies use the package manager's
existing cancellation callback and training controls stay cooperative. Queued
commands are consumed at the training-start boundary before the first step.
Large dataset documents, including depth anchors, use the existing bounded
document reader rather than the smaller live-status message limit.

## Calibration and coordinate frames

Blender camera transforms use `-Z` forward and `+Y` up. Exported OpenCV cameras
use `+Z` forward and `-Y` up. World data uses the LichtFeld dataset convention:
the Blender world Y/Z signs are converted once at export. Source origin and
scene units are preserved. Calibration uses the effective resolution, pixel
aspect, lens, sensor and shift of the camera actually rendered.

Schema-1 view geometry declares `source_frame`, `data_to_source`, `model_to_data`
and OpenCV `camera_to_data` poses with `fx`, `fy`, `cx`, `cy`, width and height.
The preview returns through `data_to_source` in Blender. Studio applies its own
shared visualizer presentation to the splat, capture cameras and framing bounds.
That presentation does not rewrite the dataset. Rotations and SH coefficients
remain model-local, avoiding a second transform of directional color.

Attached cameras are owned preview helpers. They are independent of the source
capture rig and never become input training cameras automatically. Geometry
references are bounded and hash-checked before camera construction.

An acknowledged Gaussian generation and its validated geometry reference are
tracked separately. A repaired, changed or removed reference updates the model
pose and attached cameras while training is paused, without rereading the splat
or replacing its PointCloud. The isolated color view shares that pose update.
If the displayed object was removed, the next retry restores the immutable model
automatically. Camera replacement preserves helpers explicitly linked into user
collections or scenes, and retains unrelated objects placed in a helper collection.

## Numerical image data

Depth is camera-Z distance, not a display-normalized or colorized image. Its TIFF
samples are uncompressed Float32, with zero for invalid/background pixels.
Normals use camera OpenCV coordinates and linear encoding `0.5 * (normal + 1)`;
invalid normals use the neutral value. Artistic compositing and display transforms
are excluded from these numerical passes.

Rendered-view initialization unprojects valid depth at calibrated pixel centers
and assigns RGB from the corresponding image. Alpha/background and unstable
silhouette samples are excluded. Spatial sampling/merging bounds the seed cloud.
With positive SSI depth supervision, COLMAP tracks tie measured depths to visible
surface points; at least 256 valid correspondences per view and useful depth
variation are required. Exporting depth with a zero loss weight does not enable
that training mode. Normal supervision uses the backend's camera-OpenCV convention.

RGB capture uses Standard encoded sRGB without baking the artistic display look
twice. Source look/exposure settings are recorded for display comparison. Ordinary
Blender shading and the trainer color view intentionally use different compositing
paths; neither changes the stored source image calibration or numerical maps.

## Preview transport and display

External-session protocol 1 publishes immutable arrays for means, log scaling,
quaternions, opacity logits and canonical SH coefficients. Windows uses named
memory mappings; POSIX uses temporary mapped files. Metadata and payloads are
validated before opening/importing them. One pending reader frame bounds backlog;
acknowledgements allow publishers to retire old resources.

Blender stages attributes in buffers before swapping the completed preview into
the stable result object. Expensive reading/conversion stays on workers, and
Blender mutations are spread over UI ticks. Live capture targets a 5% amortized
capture budget with a minimum 0.1-second interval. This is a scheduling target,
not a guaranteed viewport rate. Device transfer, CPU/GPU upload, scene complexity
and EEVEE settling also affect latency.

Full preview requests the active model fidelity; configured point/SH limits are
used when full transport exceeds its byte budget or when limited mode is selected.
SH0 is supported. Best/final saved artifacts retain the captured model fidelity.
Current progress, snapshot progress and best-evaluated progress are separate.

At training end, the session's `result` descriptor identifies the completed full
PLY by its absolute output root, relative artifact path, byte size, SHA-256,
iteration, Gaussian count and SH degree. Its compact `presentation` retains the
coordinate metadata independently of the last preview. It selects the best
evaluated model, or the last valid model when no evaluated candidate exists.
The file is published after best-candidate rotation has finished. Compatible
LichtFeld Studio observers can verify it and use the normal asynchronous loader
to create an editable local model. Retain the job folder until import finishes.
Terminal session state and the result descriptor are published together. A
failed final write retains an earlier valid best result for recovery.

The required backend's `SplatData.shN_rows` API extracts the selected rows and
bands before host transfer. The adapter rejects backends without that API.
SH0 remains a preview budget, not a compatibility workaround for older backends.

The trainer color scene shares preview point-cloud data and uses encoded RGB
compositing with SH0–SH3. Ordinary native Blender shading follows Blender's own
lighting and color pipeline. There is no cross-process GPU-memory sharing in
this implementation.

## Compatibility and packaging

The manifest targets Blender 5.3.x and keeps the development version at 0.1.0.
The shared protocol wheel has its own version. Its identity changes when the
distributed contract changes, so Blender replaces an older installed dependency
when updating the extension.
Supported capture engines are Cycles and EEVEE. Runtime dependencies come from
Blender or the separate LichtFeld environment. First use can install NumPy into
the backend's isolated environment; subsequent jobs reuse that environment.

The extension owns job control, the supervised callback adapter and the Blender
reader. Transport, camera schema and bounded model capture come from LichtFeld Studio's public
`lichtfeld_sessions` package, bundled as a generated pure Python wheel. Camera
poses, focal lengths, principal points, dimensions and model transforms are
collected directly through public native scene APIs at a training boundary.
Blender capture/display conversions stay in the extension. The adapter never
imports LichtFeld Studio's internal producer or reader modules.
The module/API check runs before numerical-runtime setup, and hook payloads are
validated at TrainingStart. Missing or unknown terminal outcomes fail the job
while retaining any valid result; the supervisor uses the native outcome rather
than inferring stop/completion from the last command file.

Ordinary LichtFeld Studio/headless trainers publish through an explicit
`--python-script` calling `lichtfeld_sessions.publisher.install()`.
The supervised extension child uses its own job adapter as the single publisher.
This protocol does not install a LichtFeld Studio observer UI.
The session/job helpers must preserve protocol compatibility
and the attribution in [CREDITS.md](../CREDITS.md). The ZIP contains reviewed
runtime files, configuration, license, credits and user documentation. It excludes
jobs, demos, generated reports, build caches and test output.
