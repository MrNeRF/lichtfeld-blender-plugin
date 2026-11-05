# Credits and licensing

## LichtFeld Training for Blender

Copyright © 2026 LichtFeld Studio Authors.

The source, packaging tools, tests, and documentation in this repository are
licensed under the GNU General Public License, version 3 or, at your option,
any later version, except where a file explicitly states otherwise. The full
license is in [LICENSE](LICENSE); source files use
`SPDX-License-Identifier: GPL-3.0-or-later`.

## LichtFeld Studio

[LichtFeld Studio](https://github.com/MrNeRF/LichtFeld-Studio) provides the separate
Gaussian training backend and native renderer. Its authors and contributors also
provide the shared external-session protocol implementation and native training
defaults used by this integration.

The shared session transport, coordinate geometry and bounded capture helpers
are owned by Studio's public `lichtfeld_sessions` package. Its generated
GPL-3.0-or-later pure Python wheel is bundled in `lichtfeld_training/wheels`,
including its license and package metadata. The extension owns its job-aware
reader in `session_reader.py` and imports the public shared package.
`mrnf_defaults.json` and `training_profiles.json` reflect LichtFeld's native
optimization settings. Preserve this attribution when redistributing those files.

Studio is distributed under its own
[GPL license](https://github.com/MrNeRF/LichtFeld-Studio/blob/master/LICENSE) and
[NVIDIA GPU Library Exception](https://github.com/MrNeRF/LichtFeld-Studio/blob/master/LICENSE-EXCEPTION.txt).
That additional permission applies as stated by its copyright holders. This
extension does not issue a new NVIDIA linking exception or apply Studio's
exception to other people's code. No LichtFeld executable, CUDA toolkit, GPU
driver, or NVIDIA runtime library is included in the extension archive.

## Gauss Cannon

[Gauss Cannon](https://github.com/warpgatelabs/gauss-cannon), created/maintained by
[Arash Keshmirian](https://github.com/keshmirian) at
[Warpgate Labs](https://warp-gate.com), is related work for mesh-driven camera
placement and camera-visible colored point-cloud initialization. Its extension
manifest declares GPL-3.0-or-later.

We acknowledge those workflow concepts. LichtFeld Training implements its own
editable camera distributions, evaluated-mesh sampling, captured-depth
unprojection, bounded initialization, job supervision, and preview integration.
Gauss Cannon modules, UI, artwork, and documentation are not vendored or shipped
as part of this extension.

This acknowledgement is not a relicensing notice. Any future incorporation of
third-party source must retain its applicable license and copyright notices;
Studio's additional GPU permission cannot grant rights over that source.

## Runtime dependencies and file formats

| Project | Use | Distribution in this ZIP |
| --- | --- | --- |
| [Blender](https://www.blender.org/about/license/) | Host API, Cycles/EEVEE capture, point-cloud display | Installed separately; no Blender binaries are included |
| [NumPy](https://numpy.org/doc/stable/license.html) | Numerical capture, maps, initialization, and preview conversion | Supplied by Blender or installed in the separate backend environment; no NumPy wheel is bundled |
| [COLMAP](https://colmap.github.io/format.html) | Documented camera/image/point binary dataset format | File-format interoperability; no COLMAP executable or library is bundled |

Dependencies retain their own licenses and notices. The package contains the
extension's Python source and configuration, not those projects' binary releases.
No public demo scenes, third-party model files, training datasets, logos, or
screenshots are included.

The extension's software license does not grant rights to input scenes, textures,
models, or other assets. Those materials and any applicable rights in exported
content retain their own terms.

## License references

- [Blender extension licenses](https://docs.blender.org/manual/en/latest/advanced/extensions/licenses.html).
- [GNU GPL version 3](https://www.gnu.org/licenses/gpl-3.0.html), including section 7
  on additional permissions.
- [LichtFeld Studio training hooks](https://github.com/MrNeRF/LichtFeld-Studio/blob/master/docs/plugins/api-reference.md#training-hooks).
