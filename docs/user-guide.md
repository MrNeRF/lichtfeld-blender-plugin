# User guide

Use this guide to [configure LichtFeld](#configure-the-extension), [choose a task](#choose-an-operation), [set up capture cameras](#source-and-capture-cameras), or [browse Portal Gallery](#portal-gallery). If a job already exists, see [saved datasets and recovery](#saved-datasets-and-recovery).

## Configure the extension

Install the extension ZIP from Blender Preferences, enable **LichtFeld**, and open its tab in the 3D Viewport sidebar (`N`). The gear opens the extension preferences. See the [requirements by workflow](../README.md#requirements) for supported versions and hardware.

- **Generate a dataset**. Set **Training jobs** to a writable folder for capture files, then choose **Generate dataset only** under **Capture / Train**. This workflow runs in Blender without LichtFeld Studio, a training GPU, or a Portal account.
- **Train locally**. Set **Training jobs**, select the **LichtFeld executable** file, and click **Verify installation**. This checks the executable and its training APIs without opening Studio's window or starting training. The first run may need internet access to prepare Python dependencies. Training uses no Portal account.
- **Use the Portal**. Select the **LichtFeld executable** file and follow the [Portal Gallery setup](#portal-gallery) to connect your account.

Choose **Capture / Train** for the capture workflow or **Portal** to browse online assets. **Capture / Train** opens by default. Background job recovery does not override an explicit tab choice; you can switch at any time without stopping either workflow.

Save the source `.blend` before capture. Blender creates an isolated saved copy for the render process; the source directory must allow temporary staging files. Keep linked assets accessible. Capture opens the saved scene in a separate Blender process with factory preferences and automatic Python execution disabled. If a scene depends on scripts or installed add-ons at render time, check a test image before capturing the whole dataset. A job records its capture configuration, so editing the open scene after launch does not change that job's images.

## First capture

1. Open **Capture / Train** and choose **Capture and train**.
2. In **Capture**, choose what to render. In **Cameras**, pick a camera collection or create a rig around your subject.
3. Use **Check framing** to inspect a few cameras. Save the scene, then choose **Capture and start training**.

## Choose an operation

Open the **Capture / Train** tab, then choose the **Task**: capture and train, generate a dataset only, or train saved images. The secondary **Open job…** and **View trainer…** buttons below the setup open existing work without changing it; **Back to setup** returns to it. **Back to previous job** is available below Task when a previous job was retained. The header's gear button opens configuration, and its question mark opens the user guide.

**Preset** applies image size, render samples, training budget and the desired rig camera count. Draft uses a 512 px long side, Balanced 1024 px and High 1536 px. Choose the aspect ratio under **Size presets**. **Scene settings** uses the current values. If you adjust a selected preset, its name is shown with **modified** until the values match again. Existing cameras change only when the rig is applied. Individual controls remain available. Task stays available when LichtFeld Studio is not configured, so you can still choose dataset-only capture.

The capture setup has four sections. **Capture** selects the content and shows the source frame. **Cameras** manages capture collections and rigs. **Render and output** contains image dimensions, devices, the View Layer and camera overrides. **Training** exposes steps, Gaussian budget and SH degree, with **Advanced** and **Live preview** for additional controls.

The start or export action stays below the settings, with the reason if it is blocked. **Generate dataset only** hides training controls. **Train saved images** shows dataset and training controls. Opening or closing setup sections does not require resaving the scene.

| You want to… | Choose… |
| --- | --- |
| Render the scene and train a new splat | Capture and train |
| Produce calibrated images/maps for later use | Generate dataset only |
| Try different optimization settings on the same capture | Train saved images / Train again · same images |
| Continue optimizing a saved model | Continue from checkpoint… |

Fresh training on saved images creates a new model and optimizer in a separate job. It does not restore the previous splat, even if a preview remains in the viewport. Continuing a checkpoint restores its model, optimizer and saved settings. Increasing its total steps extends/rescales the remaining schedule. The continuation dialog proposes a new total and shows the saved checkpoint schedule. Unrelated settings in the current scene do not replace the saved training and preview settings.

New training jobs start with evaluation off. To compare checkpoints on reserved views and keep the best-scoring model, enable **Evaluate model** under **Training → Advanced**. **Save every** sets the checkpoint interval in steps and also controls how often evaluation runs when enabled. With evaluation off, the job still saves checkpoints and publishes the last model as its result.

Camera, resolution, source, lighting and map changes require a new capture. Training-only settings can be changed when starting a fresh run on saved images. Previous jobs and their results remain available. New runs and continuations use the **Training jobs** folder in Settings, even when their source dataset is stored elsewhere.

During trainer startup, **Cancel startup** cancels loading or numerical-runtime preparation and preserves existing checkpoints and saved models. Once training starts, **Stop and save** requests a graceful stop at a training boundary and saves a resumable checkpoint. Closing Blender leaves the separate trainer running.

## Source and capture cameras

Select surface objects, choose a source collection, or use the entire scene. The selected static frame is used for every view. Animation is not played between cameras. If the scene contains animation, choose the intended frame explicitly. Generated capture cameras and imported results are excluded from source geometry.

A capture collection can contain existing and generated cameras, including cameras in nested collections. Use **Combine camera sets** to include cameras from multiple collections. Capture uses each distinct camera once, in natural name order (Camera 2 before Camera 10). Unsupported projection types and transforms are reported by camera name.

Capture, dataset export and training on saved images support 4–10,000 views. Regular rig generators support up to 1,000 camera positions. Panorama stations can generate several views per position, up to the 10,000-view limit. Manually added cameras count toward the total too.

If the renderer exits after every image has been captured, LichtFeld makes one automatic attempt to finish the dataset from the frozen capture scene and saved calibration. RGB images, masks and numerical maps are reused without rendering or rewriting them. Incomplete or mismatched calibration stops recovery and preserves the captured files. Partial captures and user-requested cancellations are not retried automatically.

### Generate or edit a rig

For generated spheres, hemispheres and rings, **Edit live** opens immediate distance, orbit rotation and aim controls. Entry preserves the current applied rig; apply or revert pending parameters first. Drag a value or type it, use Esc to cancel that gesture, and use Blender Undo/Redo for confirmed gestures. Native viewport gizmos adjust distance, orbit rotation and aim when viewport gizmos are enabled. When following an object, the numerical aim controls edit its offset.

**Finish live editing** preserves the current camera poses, uses their exact radius instead of automatic fitting when distance was changed, and returns to the existing shape/count controls. Path and mesh rigs retain explicit Apply. Custom additional cameras are unaffected. Manually reparented, animated, linked or shared generated cameras must be made suitable or regenerated before live editing. Saving a live rig preserves native controller/driver evaluation without enabling Python autoexecution; Auto Key is unchanged and the extension does not insert keyframes.

**Capture** chooses what is rendered. **Center** in the camera section chooses the surfaces used to position the cameras and calculate **Reference center**. These are independent: choose **Remembered objects** and **Use selected objects as center** on a character, then capture the **Entire scene** without changing the rig subject. Later viewport selections do not replace the remembered rig objects. A reference can also be a collection or the entire scene. Changing either choice does not move cameras until you apply rig parameters. The reference itself remains part of the capture when included in the chosen capture content; placement meshes and path curves remain helpers.

A new rig with **Capture content** adopts its current capture surfaces once and remembers them. Existing older rigs have no recorded reference: their cameras remain usable for capture, but choose a reference explicitly before applying rig changes. Loading a rig restores its saved reference without changing capture content. Renaming a reference object preserves its identity. A deleted or unavailable remembered object blocks Apply until you choose the reference again; it never falls back to the current selection. Apply reevaluates current reference bounds. The panel flags reference changes that still need Apply. Load restores the saved reference and clears that pending change without moving cameras.

| Distribution | Coverage |
| --- | --- |
| Sphere | Views distributed across the whole sphere |
| Hemisphere | A spherical cap with controllable elevation |
| Rings | Evenly spaced azimuths at the selected heights |
| Curve path | Positions spaced by world-space path length |
| Mesh | Area-based surface coverage or a camera per face |

Increasing the camera count redistributes positions within the chosen geometry. Curve paths support poly, Bézier and NURBS splines; a closed path avoids repeating the first endpoint. Mesh placement uses evaluated geometry and transformed normals; the offset and viewing direction control which side is observed.

For **Curve path**, choose **Camera count** for a fixed number of views, or **Maximum spacing** for evenly spaced views no farther apart along the evaluated curve than the chosen distance. Open paths include both ends; closed paths do not duplicate the seam. Distances include the curve object's world scale. Spacing that would produce fewer than 4 or more than 1,000 generated cameras is rejected before changing the rig, with the proposed count in the error. The applied camera count appears in **Cameras**. With **Follow path**, **Look backward** looks toward the previous camera along the path without moving or renumbering cameras. It is hidden and ignored with **Look at target**.

**Create path guide…** opens a small dialog for an orbit/arc, helix or spherical spiral. **Center** initially uses the 3D cursor, independently of the rig's reference center; set the center and radius to suit your subject. An orbit of 360 degrees is closed; a smaller angle makes an open arc. Helices use nonzero total rise and turns. Spherical spirals use turns and elevation limits, advancing through equal spherical-area bands; neither that shape nor camera spacing guarantees visual overlap or complete scene coverage.

The guide is an ordinary, unanimated Blender Curve: select **LichtFeld Path Guide** in the Outliner and use Blender's Object/Edit Mode tools to move, rotate, scale or reshape it. Creating another guide keeps the previous guide and existing cameras. The selected path is excluded from capture; generated guides remain helpers, excluded from subject bounds and capture even after another guide is chosen, and are disabled for rendering. Select the intended subject again when using a first rig's selection-based reference. Choose **Apply rig changes** after editing the curve, its transform, sampling or direction. Apply remains available even with the rig controls collapsed. Camera aiming is independent: choose the target or **Follow path**. Curve tilt does not control camera roll. Projection onto arbitrary meshes is not part of these guide tools.

For sphere, hemisphere and ring rigs, **Geometry and lens → Position jitter** adds small static position variations. The value is the maximum displacement as a percentage of rig radius (0–25%): 5% on a 4 m rig moves each camera by at most 0.2 m. It defaults to zero. With a nonzero value, **Seed** chooses a reproducible pattern by generated camera index; changing camera names, redrawing or applying again does not reshuffle it. Set the amount to zero, or use the field's native **Reset to Default Value** context-menu action, to disable jitter. This keeps the seed in the current controls; loading an applied rig's parameters at zero jitter restores the default seed. Apply the rig to update camera poses.

Jitter rotates and scales with the radial rig, including its live distance and orbit controls. Finishing live editing and applying again retains the pattern. The usual target controls continue to aim the cameras. These are static offsets, not animation; captured images and calibration use the resulting camera poses. Variation does not guarantee better training, coverage or clearance from geometry.

**Rotation jitter** adds a bounded angular offset after target aiming, including roll. It defaults to zero and is measured in degrees. The same Seed selects an independent angular pattern, so enabling rotation does not reshuffle positions. Automatic framing increases distance to reserve the requested angular margin; if that angle exceeds the lens's half field of view, reduce it or widen the lens. Manual distance, moved aim targets and position jitter still require checking coverage with **Check framing**. Live aiming retains the angular offsets. Path and mesh rigs do not expose these controls. Per-camera aim variation is not included.

For indoor routes, choose **Curve path → Orientation → Panorama stations**. Each sampled curve position becomes a station with several normal perspective cameras. **Stations** chooses positions, **Views/station** chooses directions, and the panel shows their product before Apply. With spacing-based sampling the station total is known only after Apply. Presets change the station count, not the views per station; for example, 144 stations × 8 views creates 1,152 cameras. Custom cameras in the collection remain additional capture views.

Choose **Facing** to orient the sectors:

- **Guide axes** keeps the common orientation. **Start azimuth** starts at guide +X and advances toward +Y, with guide +Z up. This preserves existing rigs.
- **Along path** centers each partial sector on the next sampled point, with guide +Z as the up reference. **Look backward** uses the previous point. Helices and spirals keep this up reference rather than accumulating roll. A vertical tangent has no upright frame: adjust the curve in Edit Mode or choose another facing mode if it is rejected.
- **Toward target** centers each partial sector on the existing **Look at** target, with world +Z up. After Apply, moving the aim helper or its followed object updates all sector directions immediately, without extra helpers. With Horizontal band, every view retains the elevation toward the target, including rear views.

For path/target facing, **Yaw offset** rotates the sector from that reference. With Horizontal band or Panorama grid, a full 360° turn starts in the facing direction plus this offset; partial sectors include both end directions around it. With an even view count the sector center can fall between two views. **Sweep** describes optical axes, not image coverage. Guide/path frames must be non-mirrored, nondegenerate and free of shear. Apply guide scale or inspect parents if a frame is rejected.

**Follow guide** moves and rotates the rig immediately. Target-facing cameras continue to aim independently at their target; the other modes follow the guide frame. Apply updates changed curve geometry, counts or facing settings. Changing station or direction counts retains camera identity by generated index, not by a persistent station identity.

These are separate perspective images, not a stitched or equirectangular panorama. The sweep describes camera axes, not image coverage: lens, output aspect and obstacles still matter. Multiple views at one station add viewing directions but no translational baseline; the curve supplies the different station positions. **Coverage** chooses the viewing directions at each station:

- **Horizontal band** keeps the existing single-elevation sweep.
- **Panorama grid** creates regular elevation rows with matching azimuths, like dividing a panorama into perspective views. Choose **Views/row**, **Rows**, elevation limits and **Sweep**. For example, 8 views × 3 rows gives 24 views per station. A single row uses the midpoint elevation. Elevations stay within −89.9° to +89.9° to avoid overlapping cameras at the exact poles.
- **Vertical band (uniform)** distributes directions uniformly within elevation limits and a horizontal Sweep; unlike the grid, it has no rows.
- **Hemisphere (uniform)** distributes directions across the half sphere in front of the chosen facing direction.
- **Sphere (uniform)** distributes directions around the full sphere, above and below.

For Panorama grid and the uniform vertical/spherical distributions, elevation and up are relative to the facing frame: guide axes, the path tangent, or the line toward the target. They are not necessarily the world horizon on a sloping path or an elevated target. For a panorama with common, level rows, use **Guide axes** and a level guide. **Along path** and **Toward target** deliberately tilt the whole grid to follow the path or aim direction, including its reference horizon. Horizontal band retains its existing guide/world-up yaw behavior. Increase **Views/station** (or views per row and rows for Panorama grid) and check framing for the desired overlap: a spherical direction distribution alone does not guarantee complete image coverage at every focal length.

With station sampling set to **Count**, use **Suggest view count…** for a Horizontal band or Panorama grid. Set the maximum azimuth step to preview the proposed horizontal count and the total across stations and rows. Confirm to set the count, then apply the rig when ready. The step describes camera axes. Check framing to assess image overlap with your lens and output format.

Enable **Follow guide** and apply the rig to link generated cameras to the guide's movement, rotation and uniform scaling in Object Mode. This uses Blender's native constraints, so it continues working after saving and reopening the scene. With **Look at target**, the cameras keep aiming at the independent target while their positions follow the guide. Additional custom cameras stay independent. Curve control-point edits still require **Apply rig changes**; scaling a guide does not automatically change the number of cameras or resample the requested maximum spacing. Apply when you want a new distribution.

Nonuniform scaling can introduce shear with **Follow path**. Capture rejects that pose and asks you to apply rig changes; Apply resamples the transformed curve and restores valid camera poses. To detach cameras, disable **Follow guide** and apply before deleting the guide. Deleting a linked guide without detaching breaks the native constraint and cameras return to their last applied poses; Blender Undo restores the guide and its link.

Select an existing generated rig and use **Load selected rig** before editing when its controls have not been loaded. Choosing a collection leaves the controls unchanged; **Apply rig changes** and the viewport aim action remain unavailable until that rig has been loaded. Loading changes the controls only. Applying updates the chosen rig. Previously saved scenes without a recorded parameter source also need one explicit Load. For an existing camera collection without generated-rig metadata, **Create camera rig** creates and selects a separate generated collection; it does not append cameras to the original collection. Updating it preserves generated camera identities where possible and retains additional custom cameras in the collection. Inspect coverage after changing placement, count, lens, aspect ratio or target.

To add coverage, open **Cameras → Rig settings** and set up the next rig. For example, add a low orbit to an existing hemisphere by changing the elevation range and camera count. Choose **Create new rig** to create and select its own collection. **Apply to current rig** updates the rig you are already editing. If Blender warns about repeated poses, change the new rig before adding it to the capture.

Open **Capture sets** and click **Include edited rig**. Use **Add collection** to pick another existing camera collection. The previous capture sets remain selected throughout creation. If you delete the last included collection, choose a camera collection in **Cameras → Capture sets** or use **Include edited rig**. **Capture → Content** still controls what to render. To edit an older rig, select it and choose **Load selected rig** to restore its saved settings. A new viewport aim target starts at the new rig's reference center with the current aim offset.

To capture more than one rig, expand **Combine camera sets** in **Cameras** and choose **Add collection**. The list starts with the current collection. Add the other collections and check the total camera count. A camera linked to multiple included collections is captured once. The **Edit rig** selector changes which rig you edit without changing the capture list. Remove the last entry to return to single-collection capture.

**Check framing**, **Check camera poses** and dataset capture use the same camera list. The optional reference-framing diagnostic uses a single rig reference and is skipped when more than one collection is included.

Rig creation and updates are unavailable while Blender is rendering or a job has locked the interface. If a render starts during an update, rig work pauses until the render finishes, including rollback and temporary-data cleanup. Cancellation requested before the update commits is deferred; cleanup waits for the render to finish. An update that has already committed finishes its remaining cleanup afterward. The extension does not start or cancel Blender renders.

Duplicating a generated camera also copies its generated index. An update rejects duplicate or invalid indices and multiple aim helpers before changing existing cameras. Move the duplicate out of the rig collection before applying changes; the rejected update does not delete it. Additional untagged custom cameras remain untouched. Ring count and elevation controls are shown with the distribution; aim controls are hidden when path tangents or mesh normals determine direction.

Aim at the reference center, an object's origin, the cursor, or an explicit position with offsets. The viewport target helper makes aim adjustments visible. Moving the target reorients cameras while retaining their positions. A path-following orientation uses the path tangent instead of that target.

Automatic framing uses the effective horizontal and vertical field of view, including the image shape, pixel aspect and capture lens overrides. Disable it to choose the rig radius explicitly. It is a framing aid; occlusion, reflective surfaces and sufficient overlap still need inspection.

**Last distance** is the radius recorded at the last successful rig generation, in the scene's units. If automatic fitting increased the minimum distance, the panel explains why; disable **Auto fit** and set **Distance** for an exact radius. Apply updates camera positions. Manual camera moves do not change this historical value, and older rigs show it after their next generation. **Look at** and **Move aim target in viewport** are directly accessible; the aim offset is inside **Geometry and lens**.

**Auto fit** calculates a distance that fits the reference in the frame. Leave **Min. factor** at 1 for automatic framing, or increase it to set a minimum radius in multiples of the reference bounding radius. For a closer or exact distance, turn off **Auto fit** and set **Distance**. Create or apply the rig to update the cameras.

### Inspect framing and render one view

**Check framing** opens the temporary framing scene. If a capture camera is selected and active, framing opens on that camera. It checks camera selection, output size and optical overrides. Opening framing does not start a render; use **Render test** for an RGB check. **Back to capture setup** restores the original scene. Changing **Camera number** updates the view automatically, reusing the same preview scene. If you orbited away, choosing another camera returns that viewport to camera view. The camera number is session navigation. **Apply preview settings** applies output settings and optical overrides without rebuilding scene visibility. If you change capture content or replace the camera collection, return to setup and reopen framing. The framing preview starts with the source render engine and Render Region. Changes to the region in the preview stay in that session while you switch cameras or apply preview settings. **Render test** and dataset capture use the full camera frame. Use **Exclude from capture** on an obstructed camera. It stays in the rig and you can inspect it, render a test image, or choose **Include again**. Capture, dataset export and pose checks use the included cameras. Exclusion follows the camera object when you save, rename it or apply rig changes that reuse it. Scenes that share the same camera object also share its exclusion. Each update uses the source scene's capture frame, even if you moved the temporary preview's timeline. Pending changes wait for rendering or setup work to finish and use the latest camera, output size and optical overrides.

For an actual RGB check, choose the camera in framing and press **Render test**.

The command saves the source project without temporary framing scenes, then restores framing while rendering that camera with the dataset settings. The last PNG and its logs remain available in the job folder. A successful replacement can remove the previous test job. Save a separate copy if you want to keep that image. Blender opens an Image Editor when the test starts. Its header shows the sample count when available and offers **Cancel**, which restores the previous PNG if one is available. The final PNG appears in the same window when ready. **Last image** reopens it from framing. **Cancel test render** replaces the render action while the test runs. Undo or Redo in Blender also cancels an in-progress test render.

The framing viewport starts with the source scene’s **Film → Transparent** setting. This lets an HDRI light the preview without appearing in the viewport background. Changes to that setting inside framing stay in the temporary scene. Use **Render test** to check the actual capture output. Entire scene captures render the world background, and isolated object captures use transparency for the foreground mask.

If the project has never been saved, **Save scene…** first opens Blender's Save As dialog on the source scene; after saving, reopen **Check framing**. During preparation, the source setup briefly replaces framing and shows the running test with a Cancel action. If saving or capture validation fails, rendering does not start and the source setup remains open; validation errors stay visible in the panel so you can resolve them and retry through Check framing.

The test also works with a single camera and does not need LichtFeld Studio. No reusable dataset, supervision maps, initial points or training is created, and previous training jobs remain unchanged.

### Camera checks

Open **Check camera poses** to review the cameras currently in the capture collection, including nested collections and custom cameras. The check reads evaluated poses, so parent and tracking constraints are included. It reports unsupported/invalid camera poses and exact repetitions of an earlier pose. Panorama cameras sharing a position while facing different directions are distinct. Different lenses can produce different images at the same pose; this is a diagnostic, not a recommendation to delete cameras.

**Camera issues** follows the current capture readiness. **Repeated poses** shows the last explicit check, or **not checked** before the first run. Use **Select** beside a camera to inspect it. Each repeated pose names its matching camera.

Use **Run check** or **Run again** to update repeated poses after editing cameras. Reopening the result does not start another scan. One transient result is kept for the current scene, camera collection and frame. Undo/redo, opening a file or disabling the extension clears it. No results are saved in the project. The check itself changes no cameras, selection, save state or undo history. While the check runs, scene input is held to keep the scan consistent; press Esc or right-click to cancel. Rerun after editing the scene; results are snapshots and unapplied rig settings are not used. Small position/rotation differences are not considered exact matches.

Set **Minimum distance** above zero to check how close valid camera centres are to visible, evaluated mesh surfaces in the capture scope. The report counts the cameras below the threshold and lets you select the five closest. Zero runs the pose check alone. The distance is measured in world space, including object scale and evaluated viewport modifiers. Visibility follows the active view layer, including meshes visible there but disabled for rendering. This diagnostic does not include instances, non-mesh objects or render-only geometry, and does not determine whether a camera is inside a mesh, what it can see or how well it will train. If the geometry exceeds the check's budget, narrow the capture scope and rerun.

Enable **Check rig reference framing** to flag cameras where the applied rig reference bounds lie completely beyond one image edge or clipping plane. The check uses the current output aspect ratio and evaluated camera pose, and honours capture clipping overrides. The report shows up to five examples, prioritizing different causes. It groups the examples by finding and suggests what to inspect, such as aim, lens or clipping. Clipping can come from the camera or **Common clipping** in capture setup. Use **Select** beside an example to find the camera. The check leaves all camera settings untouched. It is a conservative check of bounds. The automatic check skips capture lens or shift overrides. Use **Check framing** in capture setup to inspect those overrides and to review partially visible or occluded views. The automatic check also skips references with active Geometry Nodes modifiers because evaluated bounds can omit instances. Cameras that intentionally face away from the reference can also be reported.

Enable **Estimate surface coverage** to check 96 deterministic samples spread by surface area across visible, evaluated meshes in the capture scope. A view counts when the sample is within its image and clipping range and an opaque mesh surface does not block the ray. The report separates unseen samples from samples with limited angles and those seen from a direction at least 15° away from the first view of the same face side. Views less than 5° above the surface are ignored. It also names up to three objects with the most unseen samples. Small details may receive few or no samples. The diagnostic has the same mesh and projection limits as the distance and framing checks. It does not evaluate transparency, materials or reconstruction quality.

## Images, renderer and devices

Use **Render settings**, **Output settings**, and **Render devices** to open Blender's official controls. Capture supports Cycles and EEVEE. Review the actual engine, sample count, denoising, device mode, backend and selected devices before launch. Cycles can use CPU or selected GPU devices. EEVEE uses Blender's graphics device. An unavailable requested device is reported instead of silently changing the capture configuration.

Choose explicit width and height, or an aspect preset and orientation. The official output percentage and pixel-aspect values participate in calibration. Optional capture-camera overrides apply lens, sensor, shift and clipping settings to copies of every capture camera. They preserve camera poses and the original rig. Changing the output size preserves the scene’s Render Region. Depth of field, motion blur and render borders are disabled for calibrated capture.

Camera copies use Blender's evaluated, scale-free camera frame. Orthogonal scale on the camera or its parents is normalized automatically on the copies, including animated and constrained transforms at the capture frame. The source cameras, parents, animation and constraints are preserved. Framing preview and calibration use the same pose as capture; you do not need to apply scale manually.

Shear, mirrored frames, zero/near-zero scale and non-finite transforms cannot be represented by this calibrated camera model. Open **Review camera issues** and use **Select** beside the affected camera to inspect it and its parents. Correct the source transform or use an independent camera with a valid pose. Applying parent transforms can also change other objects or animation.

Capture records the chosen settings for its background process. Changing them after launch affects the next capture. High samples, complex volumes and large textures can make the first image slow; progress includes preparation and device information before view rendering begins.

### Depth and normal maps

Maps are numerical Float32 TIFF files. Depth stores camera-Z distance in scene units, with zero for invalid/background samples. Normals use camera OpenCV axes, encoded from `[-1, 1]` to `[0, 1]`. These files are not colorized visualizations. A generic image viewer may clip metric values and show almost only black/white; inspect their numeric range or a normalized display to judge depth variation.

Exporting maps and enabling a training loss are separate choices. With a positive depth-loss weight, capture builds calibrated COLMAP observations and requires sufficient visible depth correspondences and depth variation. A view with too few valid correspondences fails explicitly. Improve coverage or initialization coverage before retrying. A zero loss weight can export the maps for other uses.

The default initialization samples colored points from rendered views using their calibration and depth. It rejects background and uncertain boundaries, merges duplicates and limits the point budget. Surface-based initialization is an alternative when positive depth supervision is disabled.

## Training and previews

New scenes use LichtFeld Studio's MRNF defaults: 30,000 steps, a 5,000,000-Gaussian budget, SH degree 3, 100,000 initial points and exposure correction enabled. Changing Method loads that strategy’s learning rates and refinement schedule. Training steps and Gaussian budget stay at their current values. Choosing a preset explicitly replaces its listed budgets; stored scene settings are not reset on load. The plugin evaluates every 7,000 steps and at the final step to select the best evaluated result. For a new capture, evaluation reserves every eighth image, or every fourth image when there are fewer than 16 views, starting with the first image. For example, a 48-view capture reserves six images and trains on 42. Retraining on saved images keeps that dataset split.

Training setup keeps the step budget, maximum point count and SH degree directly accessible. The current method and enabled depth/normal supervision remain visible as a summary. Expand **Advanced** for the training method, supervision and the remaining expert parameters.

Choose the strategy, total steps, Gaussian budget and SH degree in the training settings. Advanced controls expose optimization and supervision options; expert JSON accepts supported scalar settings and rejects invalid or reserved fields. The job records the resulting configuration. Appearance correction follows the selected native strategy defaults; incompatible appearance modes cannot be combined.

During training, **Pause** pauses optimization and **Stop and save** asks the trainer to finish with a recoverable checkpoint. Commands remain available while a new preview is being prepared. **Freeze preview** holds the current view while optimization continues. When evaluation is enabled, choose **Current** for the newest model or **Best evaluated** for the highest-scoring candidate so far.

When a job ends, **Reached step** reports its recorded training progress, while **Shown: step** identifies the model currently in the viewport. The best evaluated model may come from an earlier step. Older jobs without recorded progress do not display an assumed final step. **Details and files** shows the settings recorded for that job, independently of edits to the current scene.

Job errors, preview update errors and monitoring errors are reported separately. A preview problem does not mean optimization has failed. If opening another job or continuing a checkpoint fails, the previously displayed job remains available.

The viewer receives Gaussian data through CPU shared memory, so it remains navigable. Full-quality preview requests all active points and SH coefficients. If that exceeds the transport budget, the job reports a reduced preview. Limited mode exposes separate point and SH0–SH3 caps. SH0 removes view-dependent SH color. These caps do not reduce the saved best model or checkpoint.

Live capture adapts to its measured cost. Blender also needs time to upload data and settle EEVEE transparency; the preview settling control trades update rate for stability. Training progress and the displayed snapshot step are distinct. The final best evaluated result can come from an earlier step than the last live preview. Freezing or disconnecting a viewer does not pause the trainer.

### Color comparison

**Trainer colors** creates an isolated scene for encoded RGB compositing of the splats. It follows the preview and supports trained SH color. Ordinary Blender scene shading blends and lights splats differently and may look lighter. The source-view-transform option applies the captured display look after compositing; **Use capture lighting** controls lighting in ordinary material preview.

The trainer color view improves comparison but uses Blender's renderer. Gaussian filtering, transparency and projection can still differ from native LichtFeld. The source scene's display settings are retained independently.

## Saved datasets and recovery

**Generate dataset only** publishes a standalone copy in the chosen destination. During export, allow space for the temporary capture and the published copy. After publication, the job points to the published dataset and removes its temporary image and map files.

Dataset-only export asks for a destination and creates a new uniquely named dataset folder. It publishes the completed dataset after validation. Cancelling removes the incomplete export owned by that operation, without replacing an existing dataset. Reusing saved images does not launch another render process.

Each training job has its own folder. **Open job folder** opens it in the system file manager. Reopening a project restores the job saved in its scene. If the saved scene has no job, LichtFeld can reconnect automatically when you reopen the same saved capture file. If the file was replaced, or an older job lacks the capture file information needed for automatic recovery, use **Open job** to select the job explicitly. Save the scene to keep that selection on reopening.

Closing Blender leaves an extension-owned headless trainer running. One controller owns its commands; another process can observe it read-only. **View trainer** searches once when opened; **Refresh trainers** requests another search. Loading a project or leaving a scene in this view does not start periodic discovery. Monitoring an already connected trainer continues automatically. **Connect to trainer** observes compatible independently launched trainers and their capture cameras. Disconnecting that view leaves the trainer running. A trainer owned by the LichtFeld Studio GUI still stops when its owning LichtFeld Studio window closes.

The trainer list shows when a search is in progress. Switching project files clears the previous search results; use **Refresh trainers** to search in a project opened directly in this view.

With an observer-enabled LichtFeld Studio build, open **External training** in an empty scene or a model scene, refresh the list and choose **View training**. A loaded training dataset must first be switched to Edit Mode or replaced with a new scene. The observer shows the step and quality actually installed, and groups the preview model and capture cameras under one scene entity. **Frame preview** uses normal navigation; **Disconnect** keeps the last read-only preview and **Remove preview** removes its group. The headless trainer continues independently.

If a run fails, retain its job folder and read the reported error and process log before retrying. A valid captured dataset can be reused without rerendering. Checkpoints and completed results remain separate from the transient live preview. After training finishes, a compatible LichtFeld Studio observer can use **Import saved model** to load the full saved best evaluated model, or the last valid model if no evaluated candidate exists. It creates an editable local model with the result's coordinate presentation and full SH data. The observer hides its transient preview after a successful import. Keep the job folder available until import completes. Job and dataset metadata can contain local paths and scene-derived content; review them before sharing.

## Portal Gallery

Choose **Portal → Browse gallery…** in the LichtFeld sidebar, or use **File → Import → LichtFeld Portal Gallery…**. The gallery is independent of the training panel and does not change the selected training job.

1. Select the **LichtFeld executable** file in extension preferences and enable **Allow Online Access** in Blender. See the [Portal requirements](../README.md#requirements).
2. The gallery reuses your LichtFeld Studio account. If it is not connected, choose **Connect account** and approve the connection in your browser. If the browser does not open, use **Open browser** and **Copy code**. No password is entered or stored in the Blender scene.
3. Browse the project cards in your Portal gallery. Search by title, or use **Page** when the catalog has more than six projects. This shows remote projects, not LichtFeld Studio's local Project Manager library.
4. Choose **Import** on the project card. Download and conversion run in the background; closing the browser keeps the operation running. Reopen it to see progress or cancel. Import waits while Blender is rendering or preparing a capture. From a temporary preview, the displayed destination is its source scene; the temporary scene itself is never modified.

Interrupted representation downloads can resume when the Portal provides the same representation. A completed download is rechecked before reuse, so retrying asset preparation does not need another full transfer. Cached files belong to Blender's extension user storage and are separate from credentials and training jobs.

**Cancel connection** waits for the account service to confirm cancellation. If the connection has already completed, it stays connected. If cancellation cannot be confirmed, the gallery reports it explicitly so you can check the account in LichtFeld Studio before retrying.

You can browse and import with the LichtFeld Studio window closed. Import uses Studio's native converter and requires a working CUDA-capable GPU. Before downloading, the extension checks that Studio's converter can export the project's visible splats. The converter combines the project's visible splats and their transforms into one Gaussian object; existing Blender objects and training results remain. The native Gaussian attributes, including the published SH detail, are retained. The current Blender scene's display transform remains in effect. Environment and project cameras are not imported. Enabled crops are applied to their source splats before merging; they are not imported as editable Blender objects. External RAD sources are included at full detail and must be accessible to LichtFeld Studio and fit in memory. Imported assets use Blender's Z-up convention, matching LichtFeld Studio's native PLY drag-out into Blender. This generic orientation differs from returning a Blender-origin training result to its original capture scene.

The Portal account is shared with LichtFeld Studio. **Sign out** removes that shared connection; imported objects remain in Blender. Offline sign-out only removes the local credentials and makes no network requests. Account controls are also available in the extension preferences. Use **Clear Portal cache** in the extension settings to remove retained downloads and thumbnails; files in use by another import are kept. Imported objects and training jobs are unaffected. Upload, automatic synchronization and installation from LichtFeld Studio are not part of this first integration.
