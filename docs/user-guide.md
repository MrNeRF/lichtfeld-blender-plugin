# User guide

## Configure the extension

Install the extension ZIP from Blender Preferences, enable it, and open the
**LichtFeld** tab in the 3D Viewport sidebar (`N`). In extension preferences, select
the actual LichtFeld executable and a writable, permanent jobs folder. **Verify
installation** checks that the executable starts and verifies the native adapter APIs listed in the [requirements](../README.md#requirements), in the backend's own Python runtime. It does not open LichtFeld Studio's GUI or start training. Unsupported installations leave the compact setup view visible. Dataset-only capture can be used without a backend.

Choose **Capture / Train** for the capture workflow or **Portal** to browse online
assets. Without a tab selected, a scene with a rig, training job or temporary
training view opens Capture / Train. Background job recovery does not override
an explicit tab choice; you can switch at any time without stopping either workflow.

Save the source `.blend` before capture. Blender creates an isolated saved copy
for the render process; the source directory must allow temporary staging files.
Keep linked assets accessible. A job records its capture configuration, so editing
the open scene after launch does not change that job's images.

## Choose an operation

Expand **Capture and training**, then choose the **Activity**: capture and train, generate a dataset only, or train saved images. **Open job** and **View trainer** open existing work without changing this setup; **Back to setup** returns to it. **Return to previous job** is available when a previous job was retained. The gear button opens configuration, and the question mark opens this guide.

Setup follows **1 · Content and quality → 2 · Cameras → 3 · Capture and train** (or **Generate dataset**). Choose what to render first, then select existing cameras or create a rig. Creating/updating has one action in the camera section. Distance is directly accessible; expand **Advanced rig settings** for elevation, lens and custom aiming. Choosing a center reference changes camera placement, not the captured content.

When Cameras is collapsed, its header keeps the applicable Load/Create/Apply action and framing accessible. Pending rig settings can be reverted alongside Apply; this reloads the saved rig controls without moving cameras or restoring the capture format. The validation status uses one line so background checking does not move the start button; expand **Validation details** for full error text. **Advanced capture settings** contains render diagnostics and extra capture controls.

During a setup operation, the Cameras header reads **Updating…**, including when collapsed. The capture summary also reports **Updating setup…** until the operation finishes; both replace existing lines without shifting the controls.

Once a camera collection is chosen, the compact summary and capture/export action appear after the settings. Expand **Capture details** for the View Layer, applied rig reference, renderer, lens and static frame. Pending controls remain visible; they do not replace camera poses until Apply. Save is offered only when the scene needs saving, and framing is available after a camera collection has been chosen.

Expand the content/camera sections, **Render and output**,
or **Training and preview** to edit the corresponding controls. These are native
Blender sections; closing them keeps the final capture/export action available. Dataset-only
capture hides training controls. Training saved images hides scene capture controls
and offers its own start action above the training settings.

**Check camera framing** opens the existing temporary framing scene. It checks
camera selection, output size and optical overrides; it does not render an RGB
test image. **Back to capture setup** restores the original scene.
Changing **Camera number** updates the view automatically, reusing the same preview scene. **Update framing** applies output settings and optical overrides without rebuilding scene visibility. If you change capture content or replace the camera collection, return to setup and reopen framing. Source cameras stay unchanged.
Each update uses the source scene's capture frame, even if you moved the temporary preview's timeline. Pending changes wait for rendering or setup work to finish and use the latest camera, output size and optical overrides.

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

For generated spheres, hemispheres and rings, **Edit live** opens immediate distance, orbit rotation and aim controls. Entry preserves the current applied rig; apply or revert pending parameters first. Drag a value or type it, use Esc to cancel that gesture, and use Blender Undo/Redo for confirmed gestures. Native viewport gizmos adjust distance, orbit rotation and aim when viewport gizmos are enabled. When following an object, the numerical aim controls edit its offset.

**Finish live editing** preserves the current camera poses, uses their exact radius instead of automatic fitting when distance was changed, and returns to the existing shape/count controls. Path and mesh rigs retain explicit Apply. Custom additional cameras are unaffected. Manually reparented, animated, linked or shared generated cameras must be made suitable or regenerated before live editing. Saving a live rig preserves native controller/driver evaluation without enabling Python autoexecution; Auto Key is unchanged and the extension does not insert keyframes.

**Content and quality** chooses what is rendered. **Center** in the camera section chooses
the surfaces used to position the cameras and calculate **Reference center**.
These are independent: choose **Remembered objects** and **Use selected objects as center** on a character, then capture
the **Entire scene** without changing the rig subject. Later viewport selections
do not replace the remembered rig objects. A reference can also be a collection
or the entire scene. Changing either choice does not move cameras until you apply
rig parameters. The reference itself remains part of the capture when included
in the chosen capture content; placement meshes and path curves remain helpers.

A new rig with **Capture content (first rig)** adopts its current capture surfaces once and remembers
them. Existing older rigs have no recorded reference: their cameras remain usable
for capture, but choose a reference explicitly before applying rig changes. Loading
a rig restores its saved reference without changing capture content. Renaming a
reference object preserves its identity. A deleted or unavailable remembered object
blocks Apply until you choose the reference again; it never falls back to the
current selection. Apply reevaluates current reference bounds.
The panel flags reference changes that still need Apply. Load restores the saved
reference and clears that pending change without moving cameras.

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

Select an existing generated rig and use **Load selected rig** before
editing when its controls have not been loaded. Choosing a collection leaves the controls unchanged; **Apply rig changes**
and the viewport aim action remain unavailable until that rig has been loaded.
Loading changes the controls only. Applying updates the chosen rig. Previously
saved scenes without a recorded parameter source also need one explicit Load.
For an existing camera collection without generated-rig metadata, **Create a new rig** creates and selects a separate generated collection; it does not append cameras to the original collection.
Updating it preserves generated camera identities where possible and retains
additional custom cameras in the collection. Inspect coverage after changing
placement, count, lens, aspect ratio or target.

Rig creation and updates are unavailable while Blender is rendering or a job has
locked the interface. If a render starts during an update, rig work pauses until
the render finishes, including rollback and temporary-data cleanup. Cancellation
requested before the update commits is deferred; cleanup waits for
the render to finish. An update that has already committed finishes its remaining
cleanup afterward. The extension does not start or cancel Blender renders.

Duplicating a generated camera also copies its generated index. An update rejects
duplicate or invalid indices and multiple aim helpers before changing existing
cameras. Move the duplicate out of the rig collection before applying changes;
the rejected update does not delete it. Additional untagged custom cameras remain
untouched. Ring count and elevation controls are shown with the distribution;
aim controls are hidden when path tangents or mesh normals determine direction.

Aim at the reference center, an object's origin, the cursor, or an explicit position
with offsets. The viewport target helper makes aim adjustments visible. Moving
the target reorients cameras while retaining their positions. A path-following
orientation uses the path tangent instead of that target.

Automatic framing uses the effective horizontal and vertical field of view,
including the image shape, pixel aspect and capture lens overrides. Disable it
to choose the rig radius explicitly. It is a framing aid; occlusion, reflective
surfaces and sufficient overlap still need inspection.

**Last distance** is the radius recorded at the last successful rig generation, in the scene's units. If automatic fitting increased the minimum distance, the panel explains why; disable **Fit subject automatically** and set **Distance from center** for an exact radius. Apply updates camera positions. Manual camera moves do not change this historical value, and older rigs show it after their next generation. **Look at** and **Move aim target in viewport** are directly accessible; only the aim offset is under advanced rig settings.

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
owns its commands; another process can observe it read-only. **View trainer** searches once when opened; **Refresh trainers** requests another search. Loading a project or leaving a scene in this view does not start periodic discovery. Monitoring an already connected trainer continues automatically. **Connect to trainer** observes compatible independently launched trainers and their capture cameras.
Disconnecting that view leaves the trainer running. A trainer owned by the
LichtFeld Studio GUI still stops when its owning LichtFeld Studio window closes.

The trainer list shows when a search is in progress. Switching project files clears the previous search results; use **Refresh trainers** to search in a project opened directly in this view.

With an observer-enabled LichtFeld Studio build, open **External training** in an empty scene or a model scene, refresh the list and choose **View training**. A loaded training dataset must first be switched to Edit Mode or replaced with a new scene. The observer shows the step and quality actually installed, and groups the preview model and capture cameras under one scene entity. **Frame preview** uses normal navigation; **Disconnect** keeps the last read-only preview and **Remove preview** removes its group. The headless trainer continues independently.

If a run fails, retain its job folder and read the reported error and process log
before retrying. A valid captured dataset can be reused without rerendering.
Checkpoints and completed results remain separate from the transient live preview.
After training finishes, a compatible LichtFeld Studio observer can use **Import saved model** to load the full saved best evaluated model, or the last valid model if no evaluated candidate exists. It creates an editable local model with the result's coordinate presentation and full SH data. The observer hides its transient preview after a successful import. Keep the job folder available until import completes.
Job and dataset metadata can contain local paths and scene-derived content;
review them before sharing.
# Portal Gallery

The extension is named **LichtFeld**. Its sidebar has two tabs: **Portal** for your online assets, and **Capture / Train** for cameras, datasets and training. Only the selected workflow is shown. If no compatible LichtFeld Studio executable is configured, Capture / Train shows a compact setup view. **Generate dataset only** remains available without LichtFeld Studio, and existing jobs and read-only trainer connections remain accessible. The installation check verifies the required training APIs in LichtFeld Studio's own Python runtime without starting training or loading user plugins, through `plugin run`, before any images are rendered. An installation without `plugin run` cannot be verified and is reported as needing an update. The training adapter repeats the same check at job startup.

Choose **Portal → Browse gallery…** in the LichtFeld sidebar, or use
**File → Import → LichtFeld Portal Gallery…**. The gallery is independent of the
training panel and does not change the selected training job.

1. Enable Blender's online access if it is disabled.
2. Choose **Connect account** and approve the connection in your browser. If the
   browser does not open, use **Open browser** and **Copy code**. No password is
   entered or stored in the Blender scene.
3. Browse the project cards in your Portal gallery. Search by title, or use **Page** when the catalog has more than six projects. This shows remote projects, not LichtFeld Studio's local Project Manager library.
4. Choose **Import** on the project card. Download and conversion run in the background;
   closing the browser keeps the operation running. Reopen it to see progress or
   cancel. Import waits while Blender is rendering or preparing a capture. From
   a temporary preview, the displayed destination is its source scene; the
   temporary scene itself is never modified.

Interrupted representation downloads can resume when the Portal provides the
same representation. A completed download is rechecked before reuse, so retrying
asset preparation does not need another full transfer. Cached files belong to
Blender's extension user storage and are separate from credentials and training jobs.

Import requires a LichtFeld Studio installation whose `convert` command exports
the visible splats of a `.licht` project with their scene transforms, configured
in the extension's Settings. An older installation is reported before the
download starts. Browsing and account connection do not need LichtFeld Studio
running. The native converter requires a working CUDA-capable GPU; this
requirement applies to preparing an import, not to browsing the gallery.
The converter combines the project's visible splats and their transforms
into one Gaussian object; existing Blender objects and training results remain.
The native Gaussian attributes, including the published SH detail, are retained.
The current Blender
scene's display transform remains in effect. Environment and project cameras
are not imported.
Enabled crops are applied to their source splats before merging; they are not
imported as editable Blender objects. External RAD sources are included at full
detail and must be accessible to LichtFeld Studio and fit in memory.
Imported assets use Blender's Z-up convention, matching LichtFeld Studio's native
PLY drag-out into Blender. This generic orientation differs from returning a
Blender-origin training result to its original capture scene.

Blender has its own Portal authorization. Use **Sign out** to forget that authorization and connect a different account; LichtFeld Studio stays connected. Offline sign-out only removes the local credentials and makes no network requests. Account controls are also available in the extension preferences. Use **Clear Portal cache** in the extension settings
to remove retained downloads and thumbnails; files in use by another import are
kept. Imported objects and training jobs are unaffected. Upload, automatic
synchronization and installation from LichtFeld Studio are not part of this
first integration.
