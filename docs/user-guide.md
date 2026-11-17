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

Open the **Capture / Train** tab, then choose the **Task**: capture and train, generate a dataset only, or train saved images. The secondary **Open job…** and **View trainer…** buttons below the setup open existing work without changing it; **Back to setup** returns to it. **Back to previous job** is available below Task when a previous job was retained. The header's gear button opens configuration, and its question mark opens this guide.

**Quality** applies image size, render samples, training budget and the desired rig camera count. Existing cameras change only when the rig is applied. Individual controls remain available. Task stays available when LichtFeld Studio is not configured, so you can still choose dataset-only capture.

Setup stays inside one **LichtFeld** panel: its header collapses or moves the complete workflow. **Capture**, **Cameras**, **Render and output**, and **Training** are sections within it. The outer panel starts expanded in a clean layout; Blender restores its saved layout when reopening a project. Internal section expansion is session UI state: opening or closing sections does not modify the scene or require another save. Choose what to render first, then select existing cameras or create a rig. Creating/updating has one action in the camera section. Expand **Rig settings** to adjust an existing rig; **Geometry and lens** contains elevation, lens and custom aiming. Choosing a center reference changes camera placement, not the captured content.

Expand **Cameras** to load, create, apply or inspect a rig. Section headers only expand or collapse their settings; actions remain visible as buttons inside the section. Pending rig settings can be reverted alongside Apply; this reloads the saved rig controls without moving cameras or restoring the capture format. The camera status replaces its existing line during background checking and Apply. **Capture checks** provides full error text. **Capture details** inside **Render and output** contains render diagnostics and extra capture controls.

During a setup operation, the camera status reports **Updating setup…** until the operation finishes. It replaces the existing status line without shifting the controls.

The capture/export action remains after the settings, including before cameras are chosen. Its status explains what is missing; configure LichtFeld Studio there when the chosen task requires it. Save is offered only when the scene needs saving. The applied reference and camera status belong to **Cameras**, the static frame to **Capture**, and dimensions, devices, View Layer and optical overrides to **Render and output**. Pending controls do not replace camera poses until Apply. Framing is available after a camera collection has been chosen.

**Training** exposes steps, Gaussian budget and SH degree directly. Its indented **Advanced** section contains the method and expert training controls. **Live preview** configures update timing, preview limits and settling; these settings do not reduce the quality of saved results. Hover over a section title or control for its explanation. Capture rules and technical details are kept in these tooltips rather than repeated as long paragraphs in the panel.

Expand the content/camera sections, **Render and output**,
or **Training** to edit the corresponding controls. Closing these
sections keeps the final capture/export action available. Dataset-only
capture hides training controls. Training saved images hides scene capture controls
and offers its own start action after the training settings.

**Check framing** opens the temporary framing scene. It checks
camera selection, output size and optical overrides. Opening framing does not
start a render; use **Save and render test image** for an RGB check. **Back to capture setup** restores the original scene.
Changing **Camera number** updates the view automatically, reusing the same preview scene. **Update framing** applies output settings and optical overrides without rebuilding scene visibility. If you change capture content or replace the camera collection, return to setup and reopen framing. Source cameras stay unchanged.
Each update uses the source scene's capture frame, even if you moved the temporary preview's timeline. Pending changes wait for rendering or setup work to finish and use the latest camera, output size and optical overrides.

For an actual RGB check, choose the camera in framing and press **Save and render test image**. This explicitly saves the source project without temporary framing scenes, then restores framing while rendering that camera with the dataset settings. The PNG opens in Blender's Image Editor; **View last** reopens it from the same action row. **Cancel test render** replaces the render action while the test runs. If the project has never been saved, **Save scene…** first opens Blender's Save As dialog on the source scene; after saving, reopen **Check framing**. During preparation, the source setup briefly replaces framing and shows the running test with a Cancel action. If saving or capture validation fails, rendering does not start and the source setup remains open; validation errors stay visible in the panel so you can resolve them and retry through Check framing. The test also works with a single camera and does not need LichtFeld Studio. No reusable dataset, supervision maps, initial points or training is created, and previous training jobs remain unchanged.

| You want to… | Choose… |
| --- | --- |
| Render the scene and train a new splat | Capture and train |
| Produce calibrated images/maps for later use | Generate dataset only |
| Try different optimization settings on the same capture | Train saved images / Train again · same images |
| Continue optimizing a saved model | Continue from checkpoint… |

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

**Capture** chooses what is rendered. **Center** in the camera section chooses
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

For **Curve path**, choose **Camera count** for a fixed number of views, or
**Maximum spacing** for evenly spaced views no farther apart along the evaluated
curve than the chosen distance. Open paths include both ends; closed paths do not
duplicate the seam. Distances include the curve object's world scale. Spacing
that would produce fewer than 4 or more than 1,000 generated cameras is rejected
before changing the rig, with the proposed count in the error. The applied camera
count appears in **Cameras**. With **Follow path**, **Look backward**
looks toward the previous camera along the path without moving or renumbering
cameras. It is hidden and ignored with **Look at target**.

**Create path guide…** opens a small dialog for an orbit/arc, helix or spherical
spiral. **Center** initially uses the 3D cursor, independently of the rig's
reference center; set the center and radius to suit your subject. An orbit of
360 degrees is closed; a smaller angle makes an open arc. Helices use nonzero total rise
and turns. Spherical spirals use turns and elevation limits, advancing through
equal spherical-area bands; neither that shape nor camera spacing guarantees
visual overlap or complete scene coverage.

The guide is an ordinary, unanimated Blender Curve: select **LichtFeld Path Guide**
in the Outliner and use Blender's Object/Edit Mode tools to move, rotate, scale
or reshape it. Creating another guide keeps the previous guide and existing
cameras. The selected path is excluded from capture; generated guides remain
helpers, excluded from subject bounds and capture even after another guide is
chosen, and are disabled for rendering. Select the intended subject again when using a first
rig's selection-based reference. Choose **Apply rig changes** after editing the
curve, its transform, sampling or direction. Apply remains available even with
the rig controls collapsed. Camera aiming is
independent: choose the target or **Follow path**. Curve tilt does not control
camera roll. Projection onto arbitrary meshes is not part of these guide tools.

For sphere, hemisphere and ring rigs, **Geometry and lens → Position jitter**
adds small static position variations. The value is the maximum displacement as
a percentage of rig radius (0–25%): 5% on a 4 m rig moves each camera by at most 0.2 m.
It defaults to zero. With a nonzero value, **Seed** chooses a reproducible pattern
by generated camera index; changing camera names, redrawing or applying again
does not reshuffle it. Set the amount to zero, or use the field's native
**Reset to Default Value** context-menu action, to disable jitter. This keeps the
seed in the current controls; loading an applied rig's parameters at zero jitter
restores the default seed. Apply the rig to update camera poses.

Jitter rotates and scales with the radial rig, including its live distance and
orbit controls. Finishing live editing and applying again retains the pattern.
The usual target controls continue to aim the cameras. These are static offsets,
not animation; captured images and calibration use the resulting camera poses.
Variation does not guarantee better training, coverage or clearance from geometry.

**Rotation jitter** adds a bounded angular offset after target aiming, including
roll. It defaults to zero and is measured in degrees. The same Seed selects an
independent angular pattern, so enabling rotation does not reshuffle positions.
Automatic framing increases distance to reserve the requested angular margin;
if that angle exceeds the lens's half field of view, reduce it or widen the lens.
Manual distance, moved aim targets and position jitter still require checking
coverage with **Check framing**. Live aiming retains the angular offsets.
Path and mesh rigs do not expose these controls. Per-camera aim variation is not included.

For indoor routes, choose **Curve path → Orientation → Panorama stations**.
Each sampled curve position becomes a station with several normal perspective
cameras. **Stations** chooses positions, **Views/station** chooses directions,
and the panel shows their product before Apply. With spacing-based sampling the
station total is known only after Apply. Quality presets change the station
count, not the views per station; for example, 144 stations × 8 views creates
1,152 cameras. Custom cameras in the collection remain additional capture views.

Choose **Facing** to orient the sectors:

- **Guide axes** keeps the common orientation. **Start azimuth** starts at guide
  +X and advances toward +Y, with guide +Z up. This preserves existing rigs.
- **Along path** centers each partial sector on the next sampled point, with
  guide +Z as the up reference. **Look backward** uses the previous point.
  Helices and spirals keep this up reference rather than accumulating roll.
  A vertical tangent has no upright frame: adjust the curve in Edit Mode or
  choose another facing mode if it is rejected.
- **Toward target** centers each partial sector on the existing **Look at**
  target, with world +Z up. After Apply, moving the aim helper or its followed
  object updates all sector directions immediately, without extra helpers.
  With Horizontal band, every view retains the elevation toward the target, including rear views.

For path/target facing, **Yaw offset** rotates the sector from that reference.
With Horizontal band or Panorama grid, a full 360° turn starts in the facing direction plus this offset; partial sectors
include both end directions around it. With an even view count the sector
center can fall between two views. **Sweep** describes optical axes, not
image coverage. Guide/path frames must be non-mirrored, nondegenerate and free
of shear. Apply guide scale or inspect parents if a frame is rejected.

**Follow guide** moves and rotates the rig immediately. Target-facing cameras
continue to aim independently at their target; the other modes follow the guide
frame. Apply updates changed curve geometry, counts or facing settings. Changing
station or direction counts retains camera identity by generated index, not by
a persistent station identity.

These are separate perspective images, not a stitched or equirectangular panorama.
The sweep describes camera axes, not image coverage: lens, output aspect and
obstacles still matter. Multiple views at one station add viewing directions but
no translational baseline; the curve supplies the different station positions.
**Coverage** chooses the viewing directions at each station:

- **Horizontal band** keeps the existing single-elevation sweep.
- **Panorama grid** creates regular elevation rows with matching azimuths, like
  dividing a panorama into perspective views. Choose **Views/row**, **Rows**,
  elevation limits and **Sweep**. For example, 8 views × 3 rows gives 24 views per
  station. A single row uses the midpoint elevation. Elevations stay within
  −89.9° to +89.9° to avoid overlapping cameras at the exact poles.
- **Vertical band (uniform)** distributes directions uniformly within elevation
  limits and a horizontal Sweep; unlike the grid, it has no rows.
- **Hemisphere (uniform)** distributes directions across the half sphere in front
  of the chosen facing direction.
- **Sphere (uniform)** distributes directions around the full sphere, above and below.

For Panorama grid and the uniform vertical/spherical distributions, elevation and up are relative to the
facing frame: guide axes, the path tangent, or the line toward the target. They
are not necessarily the world horizon on a sloping path or an elevated target.
For a panorama with common, level rows, use **Guide axes** and a level guide.
**Along path** and **Toward target** deliberately tilt the whole grid to follow
the path or aim direction, including its reference horizon.
Horizontal band retains its existing guide/world-up yaw behavior. Increase
**Views/station** (or views per row and rows for Panorama grid) and check framing for the desired overlap: a spherical direction
distribution alone does not guarantee complete image coverage at every focal length.

Enable **Follow guide** and apply the rig to link generated cameras
to the guide's movement, rotation and uniform scaling in Object Mode. This uses
Blender's native constraints, so it continues working after saving and reopening
the scene. With **Look at target**, the cameras keep aiming at the independent
target while their positions follow the guide. Additional custom cameras stay
independent. Curve control-point edits still require **Apply rig changes**;
scaling a guide does not automatically change the number of cameras or resample
the requested maximum spacing. Apply when you want a new distribution.

Nonuniform scaling can introduce shear with **Follow path**. Capture rejects that
pose and asks you to apply rig changes; Apply resamples the transformed curve and
restores valid camera poses. To detach cameras, disable **Follow guide**
and apply before deleting the guide. Deleting a linked guide without detaching
breaks the native constraint and cameras return to their last applied poses;
Blender Undo restores the guide and its link.

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

**Last distance** is the radius recorded at the last successful rig generation, in the scene's units. If automatic fitting increased the minimum distance, the panel explains why; disable **Auto fit** and set **Distance** for an exact radius. Apply updates camera positions. Manual camera moves do not change this historical value, and older rigs show it after their next generation. **Look at** and **Move aim target in viewport** are directly accessible; the aim offset is inside **Geometry and lens**.

**Fit cameras closer**, beside the distance controls, enables **Auto fit** and sets **Distance factor** to 1.05. It changes the setup parameters only: use **Create camera rig** or **Apply rig changes** to move the cameras, then check framing. Automatic fitting can still increase the final distance to keep the reference in view.

### Camera checks

Open **Capture checks → Camera checks** to review the cameras currently in
the capture collection, including nested collections and custom cameras. The
check reads evaluated poses, so parent and tracking constraints are included.
It reports unsupported/invalid camera poses and exact repetitions of an earlier
pose. Panorama cameras sharing a position while facing different directions
are distinct. Different lenses can produce different images at the same pose;
this is a diagnostic, not a recommendation to delete cameras.

**Camera issues** follows the current capture readiness. **Repeated poses** shows
the last explicit check, or **not checked** before the first run. Expand either
group and use **Select** beside a camera to inspect it. The repeated-pose details
name its matching camera and the frame/count of that snapshot.

Use **Run check** or **Run again** to update repeated poses after editing cameras.
Reopening the result does not start another scan. One transient result is kept
for the current scene, camera collection and frame. Undo/redo, opening a file
or disabling the extension clears it. No results are saved in the project. The check itself changes no
cameras, selection, save state or undo history. While the check runs, scene input
is held to keep the scan consistent;
press Esc or right-click to cancel. Rerun after editing the scene; results are snapshots
and unapplied rig settings are not used. Small position/rotation differences are
not considered exact matches. This check does not measure collisions, clearance,
visibility, overlap or training quality.

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
represented by this calibrated camera model. **Capture checks** names the
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

New scenes use LichtFeld Studio's MRNF defaults: 30,000 steps, a 5,000,000-Gaussian budget, SH degree 3, 100,000 initial points and exposure correction enabled. Learning rates and refinement parameters come from the bundled native strategy profiles. Choosing a Quality preset explicitly replaces its listed budgets; stored scene settings are not reset on load. The plugin evaluates every 7,000 steps and at the final step to select the best evaluated result; this periodic schedule is separate from LichtFeld Studio's optional evaluation settings.

Training setup keeps the step budget, maximum point count and SH degree directly accessible. The current method and enabled depth/normal supervision remain visible as a summary. Expand Advanced training for method, supervision and the remaining expert parameters.

Choose the strategy, total steps, Gaussian budget and SH degree in the training
settings. Advanced controls expose optimization and supervision options; expert
JSON accepts supported scalar settings and rejects invalid or reserved fields.
The job records the resulting configuration. Appearance correction follows the
selected native strategy defaults; incompatible appearance modes cannot be combined.

During training, **Pause** pauses optimization and **Stop and save** asks
the trainer to finish with a recoverable checkpoint. Commands remain available
while a new preview is being prepared. **Freeze preview** holds the current view
while optimization continues. Choose **Current** for the newest model or **Best
evaluated** for the strongest evaluated candidate available so far.

When a job ends, **Reached step** reports its recorded training progress, while
**Shown: step** identifies the model currently in the viewport. The best evaluated
model may come from an earlier step. Older jobs without recorded progress do not
display an assumed final step. **Details and files** shows the settings recorded
for that job, independently of edits to the current scene.

Job errors, preview update errors and monitoring errors are reported separately.
A preview problem does not mean optimization has failed. If opening another job
or continuing a checkpoint fails, the previously displayed job remains available.

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

The extension is named **LichtFeld**. Its sidebar has two tabs: **Portal** for your online assets, and **Capture / Train** for cameras, datasets and training. Only the selected workflow is shown. If no compatible LichtFeld Studio executable is configured, Capture / Train shows a compact setup view. **Generate dataset only** remains available without LichtFeld Studio, and existing jobs and read-only trainer connections remain accessible. The installation check verifies the required training APIs in LichtFeld Studio's own Python runtime without starting training or loading user plugins, through `--headless --python-script`, before any images are rendered. An installation without dataset-free headless script support cannot be verified and is reported as needing an update. The training adapter repeats the same check at job startup.

Choose **Portal → Browse gallery…** in the LichtFeld sidebar, or use
**File → Import → LichtFeld Portal Gallery…**. The gallery is independent of the
training panel and does not change the selected training job.

1. Configure a compatible LichtFeld Studio executable in Settings and enable Blender's online access if it is disabled.
2. The gallery reuses your LichtFeld Studio account. If it is not connected, choose **Connect account** and approve the connection in your browser. If the
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

**Cancel connection** waits for the account service to confirm cancellation. If the connection has already completed, it stays connected. If cancellation cannot be confirmed, the gallery reports it explicitly so you can check the account in LichtFeld Studio before retrying.

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

The Portal account is shared with LichtFeld Studio. **Sign out** removes that shared connection; imported objects remain in Blender. Offline sign-out only removes the local credentials and makes no network requests. Account controls are also available in the extension preferences. Use **Clear Portal cache** in the extension settings
to remove retained downloads and thumbnails; files in use by another import are
kept. Imported objects and training jobs are unaffected. Upload, automatic
synchronization and installation from LichtFeld Studio are not part of this
first integration.
