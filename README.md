<div align="center"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="lichtfeld_training/assets/lichtfeld-logo-white.png">
  <img src="lichtfeld_training/assets/lichtfeld-logo-black.png" alt="LichtFeld Studio" height="60">
</picture></div>

<div align="center">

# LichtFeld for Blender

**3D Gaussian Splatting in Blender, powered by [LichtFeld Studio](https://lichtfeld.io)**

Train a Gaussian splat from your Blender scene, or bring your LichtFeld Portal splats into Blender. Create a camera rig, capture a calibrated dataset with Cycles or EEVEE, and follow training live in the viewport. Capture, training and Portal asset preparation use separate processes, so the LichtFeld Studio window can remain closed.

[![Discord](https://img.shields.io/badge/Discord-Join%20Us-7289DA?logo=discord&logoColor=white)](https://discord.gg/TbxJST2BbC)
[![Website](https://img.shields.io/badge/Website-LichtFeld%20Studio-blue)](https://lichtfeld.io)
[![X](https://img.shields.io/badge/X-Follow-111111?logo=x&logoColor=white)](https://twitter.com/janusch_patas)
[![YouTube](https://img.shields.io/badge/YouTube-Subscribe-FF0000?logo=youtube&logoColor=white)](https://www.youtube.com/@lichtfeldstudio)
[![Papers](https://img.shields.io/badge/Papers-Awesome%203DGS-orange)](https://mrnerf.github.io/awesome-3D-gaussian-splatting/)

[![GitHub Sponsors](https://img.shields.io/badge/GitHub%20Sponsors-Support-EA4AAA?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/MrNeRF)
[![PayPal](https://img.shields.io/badge/PayPal-Support-00457C?logo=paypal&logoColor=white)](https://paypal.me/MrNeRF)
[![Donorbox](https://img.shields.io/badge/Donorbox-Support-27A9E1)](https://donorbox.org/lichtfeld-studio)

[**Download**](https://github.com/MrNeRF/lichtfeld-blender-plugin/releases) •
[**User Guide**](docs/user-guide.md) •
[**LichtFeld Studio**](https://github.com/MrNeRF/LichtFeld-Studio) •
[**Gaussian Splat Editor**](https://lichtfeld.io/gaussian-splat-editor/) •
[**Portal**](https://portal.lichtfeld.io/) •
[**Join Discord**](https://discord.gg/TbxJST2BbC)

**Version 0.1.0-beta.2 · Blender 5.3.x · GPL-3.0-or-later**

<a href="https://youtu.be/dkKiWb-cRwc"><img src="https://img.youtube.com/vi/dkKiWb-cRwc/maxresdefault.jpg" alt="LichtFeld Blender Plugin - Create splats from within Blender 5.3" width="720"></a>

**▶ [Watch the video: Create splats from within Blender 5.3](https://youtu.be/dkKiWb-cRwc)**

</div>

## What you can do

- Browse your LichtFeld Portal gallery and import its visible splats into your scene.
- Use existing perspective cameras or generate an editable sphere, hemisphere, ring, curve-path, or mesh-based camera rig.
- Capture with Cycles or EEVEE using Blender's render settings and explicit devices.
- Render one capture camera as an RGB test before generating the full dataset.
- Export a dataset without starting a trainer, or capture and train in one operation.
- Start a fresh training run on saved images, or continue a saved checkpoint.
- Follow current or best-evaluated splats in a navigable viewport, with independent preview quality limits, including SH0.
- Observe compatible external trainers and their capture cameras in read-only mode.

This repository contains the Blender extension. The LichtFeld backend is installed separately. Training uses its public [training-hook API](https://github.com/MrNeRF/LichtFeld-Studio/blob/master/docs/plugins/api-reference.md#training-hooks); the LichtFeld Studio observer interface is a separate feature.

## Requirements

**A LichtFeld Portal account is only needed for Portal browsing and import. You can capture datasets and train splats locally without signing in.**

All workflows use Blender 5.3.x with native Gaussian point-cloud support. Local training and Portal require [LichtFeld Studio 0.5.4.3](https://github.com/MrNeRF/LichtFeld-Studio/releases/tag/v0.5.4.3) or later.

| Workflow | What you need |
| --- | --- |
| Generate a dataset only | Cycles or EEVEE with a working render device, and a writable jobs folder with room for the capture. Runs in Blender without LichtFeld Studio or a training GPU. |
| Train locally | A Studio build that passes **Verify installation**, the CUDA-capable GPU required by your Studio build, and a writable jobs folder for checkpoints and splats. To capture new images as part of training, you also need the dataset capture requirements above. |
| Browse and import from the Portal | The configured **LichtFeld executable**, a Portal account, and **Allow Online Access** enabled in Blender. Import uses the CUDA-capable GPU required by your Studio build. |

For training, **Verify installation** checks the executable, its native training APIs and the jobs folder before capture. An older build without dataset-free `--headless --python-script` support needs an update. First use may need network access to prepare the backend's isolated NumPy runtime. See the [technical compatibility requirements](docs/technical-reference.md#compatibility-and-packaging). The ZIP includes the pure Python training-protocol wheel. NumPy, native libraries and backend executables are supplied separately.

Viewing an external trainer requires a compatible backend. The extension publishes its own training sessions. Other compatible extension instances can observe them without a LichtFeld Studio observer UI. Ordinary trainers enable publication with an explicit `--python-script` calling `from lichtfeld_sessions.publisher import install; install(dataset="/path/to/dataset")`.

Viewing extension-owned training inside LichtFeld Studio additionally requires a build with its **External training** integration. In such a build, open an empty or model scene, refresh the trainer list and select **View training**. The preview and capture cameras are read-only; disconnecting or closing the observer leaves the headless trainer running. These features are not implied by the minimal training-adapter APIs.

With saved-result import support, **Import saved model** becomes available after training finishes. It imports the full best evaluated model, or the last valid saved model, as an editable local model independently of preview limits. Keep the job folder available until import completes.

Windows is the current native integration test platform. CPU contracts also run on Linux; full Blender/GPU workflows on other platforms still need qualification. The interface is currently in English.

## Install and configure

1. Download `lichtfeld_training-<version>.zip` from the [releases page](https://github.com/MrNeRF/lichtfeld-blender-plugin/releases). To build the extension from source, run `python tools/package_blender_training.py` with Python 3.11 or newer. The installable archive is written to `dist/`.
2. In Blender, open **Edit → Preferences → Get Extensions**, then choose **Install from Disk…** from the menu and select the ZIP. Enable **LichtFeld**.
3. In the 3D Viewport, press **N** and open the **LichtFeld** sidebar tab. The gear opens extension preferences and the question mark opens the user guide.

   - **Dataset only**. Set **Training jobs** to a permanent folder for capture files, then choose **Generate dataset only** under **Capture / Train**.
   - **Local training**. In extension preferences, set **Training jobs** and select the **LichtFeld executable** file, then select **Verify installation**. Open **Capture / Train** to start.
   - **Portal**. Select the **LichtFeld executable** file in extension preferences. Enable **Allow Online Access** in Blender, then open **Portal** and connect your account. **Verify installation** is the training check.

Installing from source means building the ZIP above; this is a regular Blender extension, not a startup script. Updating the ZIP does not require changing its version number during development. After updating an enabled extension, save your work and fully restart Blender before using it. Python can retain older extension modules until the process exits, even when the ZIP was installed successfully. If activation reports a missing attribute after an update, restart Blender and enable the extension again if necessary. Reinstalling repeatedly in the same process does not clear those modules. Installed extensions use Blender's managed wheel environment; there is no need to install the protocol manually.

## First capture and training

1. Open your scene, choose a static frame, and save the `.blend` file.
2. Choose **Task → Capture and train**, then select the source: selected surface objects, a collection, or the entire scene.
3. Select a collection containing at least four perspective cameras, or choose **Create camera rig** in **Cameras**. Use **Check framing** before starting.
4. Set the output dimensions. Use **Render settings**, **Output settings**, and **Render devices** to review Blender's official configuration.
5. Review the training strategy, total steps, Gaussian budget, and any optional depth/normal supervision. Choose **Capture and start training**.
6. Follow capture progress, then the live training preview. Pause/stop and preview controls appear when relevant. The displayed snapshot step can lag the trainer.

The setup stays inside one LichtFeld panel. Expand its sections to edit content, cameras, rendering/output or training. The start/export action and any blocking issue appear below the settings, including when the sections are collapsed.

The capture uses the saved scene at the selected frame. Background Blender applies capture settings to an isolated copy; it does not animate your rig to render different views. Changes made after a job starts apply to a subsequent capture.

See [camera setup and framing](docs/user-guide.md#source-and-capture-cameras) for rig distance, aiming, live editing and test renders.

## Choose the right operation

| Operation | Renders images? | Training behavior |
| --- | --- | --- |
| **Capture and train** | Yes | Start a fresh model after capture |
| **Generate dataset only** | Yes | Export to a chosen folder; no trainer starts |
| **Train saved images** / **Train again · same images** | No | Start a fresh model using the current training settings |
| **Continue from checkpoint…** | No | Restore the saved model and optimizer in a separate run |

A fresh run on saved images does not restore the old splat. Changing camera poses, lighting, resolution, materials, or map contents requires a new capture. Each run has a separate job directory and preserves previous results.

## Preview and saved results

The preview transfers Gaussian data through CPU shared memory; it remains fully navigable. **Current / Best evaluated** chooses what to follow. **Freeze preview** holds the displayed model while training continues; **Pause** pauses training.

Full-quality previews preserve the active point count and trained SH coefficients when they fit the transport budget. Limited previews expose separate point and SH0–SH3 limits. These limits affect the live view, not checkpoints or the saved best/final model. CPU transfer, Blender updates, and EEVEE settling can reduce the update rate compared with LichtFeld's native renderer.

Use **Trainer colors** for the isolated RGB-compositing view. Ordinary Blender scene shading, lighting, and display transforms can make a splat look different. The final imported result is the best evaluated candidate available, which may come from an earlier step than the last live preview.

## Job ownership

Closing Blender leaves an extension-owned headless job running. Reopen the saved project to recover its remembered job, or use **Open job**. One controller can issue commands for a job; other scenes/processes can observe it read-only.

**View trainer** finds active trainers on entry; **Refresh trainers** updates the list on request. There is no periodic discovery scan. Connecting attaches a read-only viewer to an independently started trainer. Disconnecting that viewer does not stop its trainer. A training run owned by the LichtFeld Studio GUI still stops when its owning window closes.

## Documentation

- [User guide](docs/user-guide.md): rigs, devices, maps, training, saved datasets, color views, and recovery.
- [Technical reference](docs/technical-reference.md): files, coordinate frames, numerical maps, preview transport, and execution boundaries.
- [Contributing](CONTRIBUTING.md): packaging, tests, and source conventions.
- [Credits and licensing](CREDITS.md): authorship, shared code, dependencies, conceptual references, and distribution scope.

Keep job data in the configured jobs directory. Datasets contain scene-derived images, camera calibration, geometry, and project/path metadata; review those contents when sharing a dataset or reporting a problem.

## License and credits

The extension is licensed under **GNU GPL version 3 or, at your option, any later version**, matching LichtFeld Studio's base license. See [LICENSE](LICENSE) and [CREDITS.md](CREDITS.md). The extension archive includes its complete Python source, license, credits, and user documentation.

Gauss Cannon by Arash Keshmirian / Warpgate Labs is acknowledged for related camera placement and camera-visible colored point-cloud workflows. Its code and artwork are not bundled. LichtFeld Studio's NVIDIA GPU Library Exception belongs to the separately distributed backend and is not extended to third-party material here.
