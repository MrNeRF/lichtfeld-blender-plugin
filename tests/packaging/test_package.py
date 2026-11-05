# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Installable archives include complete licensing and reject accidental runtime files."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('package_extension', ROOT/'tools/package_blender_training.py')
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class PackageContracts(unittest.TestCase):
    def fixture(self, directory):
        repository = Path(directory)/'source'
        for name in packager.RUNTIME_FILES:
            target = repository/'lichtfeld_training'/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/'lichtfeld_training'/name, target)
        for name in packager.DOCUMENT_FILES:
            target = repository/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, target)
        return repository

    def test_archive_has_all_runtime_license_and_linked_docs_without_private_files(self):
        with tempfile.TemporaryDirectory() as area:
            repository = self.fixture(area)
            (repository/'VALIDATION.md').write_text('private local history')
            (repository/'jobs').mkdir()
            (repository/'jobs/secret.json').write_text('{}')
            destination = packager.package(Path(area)/'packages', repository=repository)
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(set(archive.namelist()), packager.RUNTIME_FILES | set(packager.DOCUMENT_FILES))
                self.assertIn(b'GNU GENERAL PUBLIC LICENSE', archive.read('LICENSE'))
                self.assertIn(b'Warpgate', archive.read('CREDITS.md'))
                self.assertTrue(all(not name.startswith('/') and '..' not in Path(name).parts for name in archive.namelist()))

    def test_archive_is_reproducible_across_source_mtimes(self):
        with tempfile.TemporaryDirectory() as area:
            repository = self.fixture(area)
            destination = packager.package(Path(area)/'packages', repository=repository)
            before = hashlib.sha256(destination.read_bytes()).hexdigest()
            for source in repository.rglob('*'):
                if source.is_file(): os.utime(source, (1700000000, 1700000000))
            self.assertEqual(hashlib.sha256(packager.package(destination.parent, repository=repository).read_bytes()).hexdigest(), before)

    def test_invalid_sources_and_failed_write_preserve_previous_package(self):
        with tempfile.TemporaryDirectory() as area:
            repository = self.fixture(area)
            destination = packager.package(Path(area)/'packages', repository=repository)
            before = destination.read_bytes()
            debug = repository/'lichtfeld_training/debug-local.py'
            debug.write_text("SECRET = 'local'")
            with self.assertRaisesRegex(ValueError, 'inventory'): packager.package(destination.parent, repository=repository)
            debug.unlink()
            manifest = repository/'lichtfeld_training/blender_manifest.toml'
            original_manifest = manifest.read_text(encoding='utf-8')
            manifest.write_text(original_manifest.replace('wheels/lichtfeld_training_protocol-', 'wheels/missing-'), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Manifest wheel dependencies'):
                packager.package(destination.parent, repository=repository)
            manifest.write_text(original_manifest, encoding='utf-8')
            module = repository/'lichtfeld_training/workflow.py'
            original = module.read_bytes()
            module.write_text('def broken(:')
            with self.assertRaises(SyntaxError): packager.package(destination.parent, repository=repository)
            module.write_bytes(original)
            with mock.patch.object(packager.zipfile.ZipFile, 'writestr', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'): packager.package(destination.parent, repository=repository)
            self.assertEqual(destination.read_bytes(), before)
            self.assertEqual(list(destination.parent.iterdir()), [destination])


if __name__ == '__main__': unittest.main()
