# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""POSIX permission test, including a second UID when run as Linux root."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lichtfeld_training"))
import jobs
from bridge_core import SCHEMA, atomic_json, digest, read_json, verify_artifact


@unittest.skipUnless(os.name == "posix", "Requires POSIX permissions")
class PosixPermissions(unittest.TestCase):
    def test_shared_parent_does_not_expose_resumed_model(self):
        old_umask = os.umask(0o022)
        try:
            with tempfile.TemporaryDirectory() as directory:
                parent = Path(directory)
                parent.chmod(0o755)  # Deliberately searchable shared job parent.
                previous = parent / "previous"
                previous.mkdir(mode=0o700)
                (previous / "output").mkdir()
                (previous / "output/project.licht").write_bytes(b"checkpoint")
                atomic_json(previous / "job.json", {"schema": SCHEMA, "id": "previous",
                    "root": str(previous), "dataset_root": str(previous / "dataset")})
                (previous / "model.ply").write_bytes(b"private model")
                atomic_json(previous / "best.json", {"path": "model.ply", "id": "best",
                    "bytes": 13, "sha256": digest(previous / "model.ply"), "score": {"psnr": 30}})
                job, _ = jobs.resume_data(previous, 16, backend="trusted", jobs_directory=parent)
                root = Path(job["root"])
                model = verify_artifact(root, read_json(root / "best.json"))
                for path, mode in ((root, 0o700), (root / "artifacts", 0o700), (model, 0o600)):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
                if os.geteuid() == 0 and shutil.which("runuser"):
                    control = parent / "public-control"
                    control.write_text("readable")
                    command = ["runuser", "-u", "nobody", "--", sys.executable, "-c",
                               "import sys; open(sys.argv[1], 'rb').read()"]
                    self.assertEqual(subprocess.run(command + [str(control)], capture_output=True).returncode, 0)
                    result = subprocess.run(command + [str(model)], capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(b"PermissionError", result.stderr)
                    print("SECOND_UID_PRIVATE_RESUMED_MODEL_PASS", flush=True)
        finally:
            os.umask(old_umask)


if __name__ == "__main__":
    unittest.main()
