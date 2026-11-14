<picture>
  <source media="(prefers-color-scheme: dark)" srcset="lichtfeld_training/assets/lichtfeld-logo-white.png">
  <img src="lichtfeld_training/assets/lichtfeld-logo-black.png" alt="LichtFeld Studio" height="36">
</picture>

# LichtFeld for Blender

Browse your LichtFeld Portal assets and bring their splats into Blender, or create
a camera rig, capture a calibrated dataset, and train a navigable Gaussian splat
from your scene. Capture, training and Portal asset preparation use separate
processes, so the LichtFeld Studio window can remain closed.

**Version 0.1.0 · Blender 5.3.x · GPL-3.0-or-later**

## What you can do

- Browse your LichtFeld Portal gallery and import its visible splats into your scene.
- Use existing perspective cameras or generate an editable sphere, hemisphere,
  ring, curve-path, or mesh-based camera rig.
- Capture with Cycles or EEVEE using Blender's render settings and explicit devices.
- Export a dataset without starting a trainer, or capture and train in one operation.
- Start a fresh training run on saved images, or continue a saved checkpoint.
- Follow current or best-evaluated splats in a navigable viewport, with independent
  preview quality limits, including SH0.
- Observe compatible external trainers and their capture cameras in read-only mode.

This repository contains the Blender extension. The LichtFeld backend is installed
separately. Training uses its public
[training-hook API](https://github.com/MrNeRF/LichtFeld-Studio/blob/master/docs/plugins/api-reference.md#training-hooks);
the LichtFeld Studio observer interface is a separate feature.

## Requirements

| Component | Requirement |
| --- | --- |
| Blender | 5.3.x, with native Gaussian point-cloud support |
| Portal browsing | Online access enabled in Blender and a Portal account; no LichtFeld Studio installation needed |
| Portal import | LichtFeld Studio whose `convert` exports visible project splats with transforms, enabled crops and full-detail RAD sources, and the GPU required by that build |
| Capture renderer | Cycles or EEVEE; a working device configuration for that engine |
| Training backend | Headless LichtFeld with `plugin run` compatibility probing, the public `lichtfeld_sessions` package, camera principal-point properties, `SplatData.shN_rows`, and scoped training hooks with explicit progress/outcomes |
| Training hardware | The CUDA/GPU requirements of the selected LichtFeld build |
| Storage | A writable jobs directory with room for the captured project, images, maps, checkpoints, and splats |

Dataset-only capture does not require LichtFeld or a training GPU. Training and
external viewing need a compatible backend; an older executable may not provide
the required APIs. **Verify installation** checks that the selected executable
starts, verifies its native training APIs before any capture, and checks the jobs directory.
An executable without `plugin run` cannot be verified and is reported as needing an update. The adapter repeats that API check when a job starts and validates its hook payload before optimization proceeds.
First use may need network access to prepare the backend's isolated NumPy runtime.
The ZIP bundles LichtFeld's pure Python training-protocol and Portal client wheels.
NumPy, native libraries and backend executables are supplied separately.

The backend must expose `ScopedHandler.on_training_start`, `on_post_step`, and
`on_training_end`, plus `SplatData.shN_rows` and the read-only
`SceneNode.camera_center_x` / `camera_center_y` properties. Hook payloads must report `iterations`
and `is_paused`; TrainingEnd must report `completed`, `stopped`, or `failed` in
`terminal_state`. Incompatible backends produce an explicit update error.
SH0–SH3 preview budgets remain available with a compatible backend.

The extension publishes its own training sessions. Other compatible extension instances can observe them without a LichtFeld Studio observer UI. Ordinary trainers enable publication with an explicit `--python-script` calling `from lichtfeld_sessions.publisher import install; install(dataset="/path/to/dataset")`.

Viewing extension-owned training inside LichtFeld Studio additionally requires a build with its **External training** integration. In such a build, open an empty or model scene, refresh the trainer list and select **View training**. The preview and capture cameras are read-only; disconnecting or closing the observer leaves the headless trainer running. These features are not implied by the minimal training-adapter APIs.

With saved-result import support, **Import saved model** becomes available after training finishes. It imports the full best evaluated model, or the last valid saved model, as an editable local model independently of preview limits. Keep the job folder available until import completes.

Windows is the current native integration test platform. CPU contracts also run
on Linux; full Blender/GPU workflows on other platforms still need qualification.
The interface is currently in English.

## Install and configure

1. Obtain `lichtfeld_training-0.1.0.zip` from the
   `lichtfeld-training-extension` artifact of a successful
   [workflow run](https://github.com/MrNeRF/lichtfeld-blender-plugin/actions/workflows/contracts.yml),
   and extract the downloaded artifact container. To build the extension from
   source, run `python tools/package_blender_training.py` with Python 3.11 or newer.
   The installable archive is written to `dist/`.
2. In Blender, open **Edit → Preferences → Get Extensions**, then choose
   **Install from Disk…** from the menu and select the ZIP. Enable **LichtFeld**.
3. Open its preferences, select the actual LichtFeld **executable file**, and
   choose a permanent jobs directory. Select **Verify installation**.
4. In the 3D Viewport, press **N** and open the **LichtFeld** sidebar tab.
   Choose **Portal** or **Capture / Train**. The gear opens extension preferences;
   the question mark in Capture / Train opens the user guide.

Installing from source means building the ZIP above; this is a regular Blender
extension, not a startup script. Updating the ZIP does not require changing its
version number during development.
After updating an enabled extension, save your work and fully restart Blender
before using it. Python can retain older extension modules until the process
exits, even when the ZIP was installed successfully. If activation reports a
missing attribute after an update, restart Blender and enable the extension
again if necessary. Reinstalling repeatedly in the same process does not clear
those modules. Installed extensions use Blender's managed wheel environment;
there is no need to install the protocol manually.

## First capture and training

1. Open your scene, choose a static frame, and save the `.blend` file.
2. Choose **Activity → Capture and train**, then select the source: selected surface objects,
   a collection, or the entire scene.
3. Select a collection containing at least four perspective cameras, or choose
   **Create camera rig** in **Capture cameras**. Use **Check camera framing** before starting.
4. Set the output dimensions. Use **Render settings**, **Output settings**, and
   **Render devices** to review Blender's official configuration.
5. Review the training strategy, total steps, Gaussian budget, and any optional
   depth/normal supervision. Choose **Capture and start training**.
6. Follow capture progress, then the live training preview. Pause/stop and preview
   controls appear when relevant. The displayed snapshot step can lag the trainer.

The capture plan and start/export actions stay at the top. Expand the native
setup sections to edit content, cameras, rendering/output, or training/preview.

The capture uses the saved scene at the selected frame. Background Blender applies
capture settings to an isolated copy; it does not animate your rig to render
different views. Changes made after a job starts apply to a subsequent capture.

### Camera distance and aiming

The rig **Center** determines camera placement; **Look at** determines camera direction independently. Automatic fitting can increase the minimum distance factor to fit the reference at the selected lens and output aspect. For an exact radius, switch off **Fit subject automatically** and set **Distance from center**, then apply the rig changes. **Last distance** records the radius at the last successful generation; moving cameras manually does not update this historical value. Older rigs acquire this feedback when next regenerated.

For an applied sphere, hemisphere or rings rig, choose **Edit live** to adjust distance, orbit rotation and aim immediately. Drag a field or enter a number; Escape cancels the current gesture and Blender Undo restores a confirmed gesture. Native viewport gizmos adjust distance, orbit rotation and aim when viewport gizmos are enabled. **Finish live editing** keeps the current camera poses and returns to shape/count controls. Changing live distance switches automatic fitting off when finished; otherwise the applied framing and aim modes are preserved. Camera count, distribution and path/mesh placement retain the explicit Apply workflow.

Live editing keeps generated camera IDs and additional cameras intact. It requires the original local, unanimated generated cameras and their tracking target; apply pending parameters first. A native controller and simple drivers preserve a saved live rig without enabling Python scripts. The extension does not change Auto Key or insert keyframes. The live controls are unavailable while capture/training preparation or a native render owns the scene.

Use **Move aim target in viewport** to adjust the generated tracking target directly. Elevation, rotation, lens and aim offset remain under the rig details. These controls do not change which objects are captured.

In **Check framing**, changing **Camera number** updates the view automatically without rebuilding the temporary scene. Use **Update framing** after changing output dimensions or camera overrides. This checks framing without rendering or modifying the source cameras. To include changes to scene content or a different camera collection, return to capture setup and reopen framing.

## Choose the right operation

| Operation | Renders images? | Training behavior |
| --- | --- | --- |
| **Capture and train** | Yes | Start a fresh model after capture |
| **Generate dataset only** | Yes | Export to a chosen folder; no trainer starts |
| **Train saved images** / **New training · same images** | No | Start a fresh model using the current training settings |
| **Continue saved checkpoint…** | No | Restore the saved model and optimizer in a separate run |

A fresh run on saved images does not restore the old splat. Changing camera poses,
lighting, resolution, materials, or map contents requires a new capture. Each run
has a separate job directory and preserves previous results.

## Preview and saved results

The preview transfers Gaussian data through CPU shared memory; it remains fully
navigable. **Current / Best evaluated** chooses what to follow. **Freeze preview**
holds the displayed model while training continues; **Pause** pauses training.

Full-quality previews preserve the active point count and trained SH coefficients
when they fit the transport budget. Limited previews expose separate point and
SH0–SH3 limits. These limits affect the live view, not checkpoints or the saved
best/final model. CPU transfer, Blender updates, and EEVEE settling can reduce
the update rate compared with LichtFeld's native renderer.

Use **Open trainer color view** for the isolated RGB-compositing view. Ordinary
Blender scene shading, lighting, and display transforms can make a splat look
different. The final imported result is the best evaluated candidate available,
which may come from an earlier step than the last live preview.

## Job ownership

Closing Blender leaves an extension-owned headless job running. Reopen the saved
project to recover its remembered job, or use **Open job**. One controller can
issue commands for a job; other scenes/processes can observe it read-only.

**View trainer** finds active trainers on entry; **Refresh trainers** updates the list on request. There is no periodic discovery scan. Connecting attaches a read-only viewer to an independently started
trainer. Disconnecting that viewer does not stop its trainer. A training run owned
by the LichtFeld Studio GUI still stops when its owning window closes.

## Documentation

- [User guide](docs/user-guide.md): rigs, devices, maps, training, saved datasets,
  color views, and recovery.
- [Technical reference](docs/technical-reference.md): files, coordinate frames,
  numerical maps, preview transport, and execution boundaries.
- [Contributing](CONTRIBUTING.md): packaging, tests, and source conventions.
- [Credits and licensing](CREDITS.md): authorship, shared code, dependencies,
  conceptual references, and distribution scope.

Keep job data in the configured jobs directory. Datasets contain scene-derived
images, camera calibration, geometry, and project/path metadata; review those
contents when sharing a dataset or reporting a problem.

## License and credits

The extension is licensed under **GNU GPL version 3 or, at your option, any later
version**, matching LichtFeld Studio's base license. See [LICENSE](LICENSE) and
[CREDITS.md](CREDITS.md). The extension archive includes its complete Python source,
license, credits, and user documentation.

Gauss Cannon by Arash Keshmirian / Warpgate Labs is acknowledged for related camera
placement and camera-visible colored point-cloud workflows. Its code and artwork
are not bundled. LichtFeld Studio's NVIDIA GPU Library Exception belongs to the
separately distributed backend and is not extended to third-party material here.
