# User guide

## Configure the extension

Install the extension ZIP from Blender Preferences, enable it, and open the
**LichtFeld** tab in the 3D Viewport sidebar (`N`). In extension preferences, select
the actual LichtFeld executable and a writable, permanent jobs folder. **Verify
installation** checks that the executable can start. Training additionally needs
the native adapter APIs listed in the [requirements](../README.md#requirements).
These are checked inside the trainer when a job starts; unsupported backends
report an update error. Dataset-only capture can be used without a backend.

Save the source `.blend` before capture. Blender creates an isolated saved copy
for the render process; the source directory must allow temporary staging files.
Keep linked assets accessible. A job records its capture configuration, so editing
the open scene after launch does not change that job's images.

## Choose an operation

| You want to… | Choose… |
| --- | --- |
| Render the scene and train a new splat | Capture and train |
| Produce calibrated images/maps for later use | Generate dataset only |
| Try different optimization settings on the same capture | Train saved images / New training · same images |
| Continue optimizing a saved model | Continue saved checkpoint… |

Fresh training on saved images creates a new model and optimizer in a separate
job. It does not restore the previous splat, even if a preview remains in the
viewport. Continuing a checkpoint restores its model, optimizer and saved
settings. Increasing its total steps extends/rescales the remaining schedule.
The continuation dialog proposes a new total from the saved job, shows its
evaluation interval, and keeps its training settings and preview quality.
Unrelated settings in the current scene do not replace them.

Camera, resolution, source, lighting and map changes require a new capture.
Training-only settings can be changed when starting a fresh run on saved images.
Previous jobs and their results remain available.
New runs and continuations use the **Training jobs** folder in Settings, even
when their source dataset is stored elsewhere.

During trainer startup, **Cancel startup** cancels loading or numerical-runtime
preparation and preserves existing checkpoints and saved models. Once training
starts, **Stop and save** requests a graceful stop at a training boundary and
saves a resumable checkpoint. Closing Blender leaves the separate trainer running.

## Source and capture cameras

Select surface objects, choose a source collection, or use the entire scene.
The selected static frame is used for every view. Animation is not played between
cameras. If the scene contains animation, choose the intended frame explicitly.
Generated capture cameras and imported results are excluded from source geometry.

A camera collection can contain existing and generated cameras, including nested
collections. Capture uses all its perspective cameras in name order. At least
four are required; unsupported projection types and transforms that cannot be
calibrated are reported by camera name before capture. The generated-camera count describes the generated
part of the rig; manually added cameras can make the total larger. Capture,
dataset export and training on saved images support 4–10,000 views. Generated
rigs support up to 1,000 cameras; the collection can include additional cameras.

If the renderer exits after every image has been captured, LichtFeld makes one
automatic attempt to finish the dataset from the frozen capture scene and saved
calibration. RGB images, masks and numerical maps are reused without rendering
or rewriting them. Incomplete or mismatched calibration stops recovery and
preserves the captured files. Partial captures and user-requested cancellations
are not retried automatically.

### Generate or edit a rig

| Distribution | Coverage |
| --- | --- |
| Sphere | Views distributed across the whole sphere |
| Hemisphere | A spherical cap with controllable elevation |
| Rings | Evenly spaced azimuths at the selected heights |
| Curve path | Positions spaced by world-space path length |
| Mesh | Area-based surface coverage or a camera per face |

Increasing the camera count redistributes positions within the chosen geometry.
Curve paths support poly, Bézier and NURBS splines; a closed path avoids repeating
the first endpoint. Mesh placement uses evaluated geometry and transformed normals;
the offset and viewing direction control which side is observed.

Select an existing generated rig and use **Load this rig's parameters** before
editing. Choosing a collection leaves the controls unchanged; **Apply rig parameters**
and the viewport aim action remain unavailable until that rig has been loaded.
Loading changes the controls only. Applying updates the chosen rig. Previously
saved scenes without a recorded parameter source also need one explicit Load.
Updating it preserves generated camera identities where possible and retains
additional custom cameras in the collection. Inspect coverage after changing
placement, count, lens, aspect ratio or target.

Duplicating a generated camera also copies its generated index. An update rejects
duplicate or invalid indices and multiple aim helpers before changing existing
cameras. Move the duplicate out of the rig collection before applying changes;
the rejected update does not delete it. Additional untagged custom cameras remain
untouched. Ring count and elevation controls are shown with the distribution;
aim controls are hidden when path tangents or mesh normals determine direction.

Aim at the subject center, an object's origin, the cursor, or an explicit position
with offsets. The viewport target helper makes aim adjustments visible. Moving
the target reorients cameras while retaining their positions. A path-following
orientation uses the path tangent instead of that target.

Automatic framing uses the effective horizontal and vertical field of view,
including the image shape, pixel aspect and capture lens overrides. Disable it
to choose the rig radius explicitly. It is a framing aid; occlusion, reflective
surfaces and sufficient overlap still need inspection.

## Images, renderer and devices

Use **Render settings**, **Output settings**, and **Render devices** to open
Blender's official controls. Capture supports Cycles and EEVEE. Review the actual
engine, sample count, denoising, device mode, backend and selected devices before
launch. Cycles can use CPU or selected GPU devices. EEVEE uses Blender's graphics
device. An unavailable requested device is reported instead of silently changing
the capture configuration.

Choose explicit width and height, or an aspect preset and orientation. The
official output percentage and pixel-aspect values participate in calibration.
Optional capture-camera overrides apply lens, sensor, shift and clipping settings
to copies of every capture camera. They preserve camera poses and the original rig.
Depth of field, motion blur and render borders are disabled for calibrated capture.

Camera copies use Blender's evaluated, scale-free camera frame. Orthogonal scale
on the camera or its parents is normalized automatically on the copies, including
animated and constrained transforms at the capture frame. The source cameras,
parents, animation and constraints are preserved. Framing preview and calibration
use the same pose as capture; you do not need to apply scale manually.

Shear, mirrored frames, zero/near-zero scale and non-finite transforms cannot be
represented by this calibrated camera model. **Validation details** names the
camera and offers **Select camera** to inspect it and its parents. This action
does not apply transforms. Correct the source transform or use an independent
camera with a valid pose; applying parent transforms automatically could change
other objects or animation. The panel shows the first five issues and the number
remaining; resolving those reveals the next issues.

Capture records the chosen settings for its background process. Changing them
after launch affects the next capture. High samples, complex volumes and large
textures can make the first image slow; progress includes preparation and device
information before view rendering begins.

### Depth and normal maps

Maps are numerical Float32 TIFF files. Depth stores camera-Z distance in scene
units, with zero for invalid/background samples. Normals use camera OpenCV axes,
encoded from `[-1, 1]` to `[0, 1]`. These files are not colorized visualizations.
A generic image viewer may clip metric values and show almost only black/white;
inspect their numeric range or a normalized display to judge depth variation.

Exporting maps and enabling a training loss are separate choices. With a positive
depth-loss weight, capture builds calibrated COLMAP observations and requires
sufficient visible depth correspondences and depth variation. A view with too
few valid correspondences fails explicitly. Improve coverage or initialization
coverage before retrying. A zero loss weight can export the maps for other uses.

The default initialization samples colored points from rendered views using
their calibration and depth. It rejects background and uncertain boundaries,
merges duplicates and limits the point budget. Surface-based initialization is
an alternative when positive depth supervision is disabled.

## Training and previews

Choose the strategy, total steps, Gaussian budget and SH degree in the training
settings. Advanced controls expose optimization and supervision options; expert
JSON accepts supported scalar settings and rejects invalid or reserved fields.
The job records the resulting configuration. Synthetic captures leave appearance
correction off by default; incompatible appearance modes cannot be combined.

During training, **Pause** pauses optimization and **Stop with checkpoint** asks
the trainer to finish with a recoverable checkpoint. Commands remain available
while a new preview is being prepared. **Freeze preview** holds the current view
while optimization continues. Choose **Current** for the newest model or **Best
evaluated** for the strongest evaluated candidate available so far.

The viewer receives Gaussian data through CPU shared memory, so it remains
navigable. Full-quality preview requests all active points and SH coefficients.
If that exceeds the transport budget, the job reports a reduced preview. Limited
mode exposes separate point and SH0–SH3 caps. SH0 removes view-dependent SH color.
These caps do not reduce the saved best model or checkpoint.

Live capture adapts to its measured cost. Blender also needs time to upload data
and settle EEVEE transparency; the preview settling control trades update rate
for stability. Training progress and the displayed snapshot step are distinct.
The final best evaluated result can come from an earlier step than the last live
preview. Freezing or disconnecting a viewer does not pause the trainer.

### Color comparison

**Open trainer color view** creates an isolated scene for encoded RGB compositing
of the splats. It follows the preview and supports trained SH color. Ordinary
Blender scene shading blends and lights splats differently and may look lighter.
The source-view-transform option applies the captured display look after
compositing; **Use capture lighting** controls lighting in ordinary material preview.

The trainer color view improves comparison but uses Blender's renderer. Gaussian
filtering, transparency and projection can still differ from native LichtFeld.
The source scene's display settings are retained independently.

## Saved datasets and recovery

Dataset-only export asks for a destination and creates a new uniquely named
dataset folder. It publishes the completed dataset after validation. Cancelling
removes the incomplete export owned by that operation, without replacing an
existing dataset. Reusing saved images does not launch another render process.

Each training job has its own folder. **Open job folder** opens it in the system
file manager. The project/scene remembers its current job; reopening the saved
project recovers monitoring automatically. **Open job** is available when that
remembered reference is missing or another job must be inspected.

Closing Blender leaves an extension-owned headless trainer running. One controller
owns its commands; another process can observe it read-only. **Connect trainer**
observes compatible independently launched trainers and their capture cameras.
Disconnecting that view leaves the trainer running. A trainer owned by the
LichtFeld Studio GUI still stops when its owning LichtFeld Studio window closes.

With an observer-enabled LichtFeld Studio build, open **External training** in an empty scene or a model scene, refresh the list and choose **View training**. A loaded training dataset must first be switched to Edit Mode or replaced with a new scene. The observer shows the step and quality actually installed, and groups the preview model and capture cameras under one scene entity. **Frame preview** uses normal navigation; **Disconnect** keeps the last read-only preview and **Remove preview** removes its group. The headless trainer continues independently.

If a run fails, retain its job folder and read the reported error and process log
before retrying. A valid captured dataset can be reused without rerendering.
Checkpoints and completed results remain separate from the transient live preview.
After training finishes, a compatible LichtFeld Studio observer can use **Import saved model** to load the full saved best evaluated model, or the last valid model if no evaluated candidate exists. It creates an editable local model with the result's coordinate presentation and full SH data. The observer hides its transient preview after a successful import. Keep the job folder available until import completes.
Job and dataset metadata can contain local paths and scene-derived content;
review them before sharing.
