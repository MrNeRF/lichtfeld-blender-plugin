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

    def test_protocol_rebuild_preserves_existing_distribution_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.fixture(directory)
            source = Path(directory)/'studio'
            for name in ('src/python/lichtfeld_sessions/__init__.py', 'src/python/_lfs_path_io.py', 'LICENSE'):
                path = source/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('# Protocol build fixture\n')
            wheel = repository/'lichtfeld_training'/packager.PROTOCOL_WHEEL
            original = wheel.read_bytes()
            with zipfile.ZipFile(wheel) as archive:
                payloads = {name: archive.read(name) for name in archive.namelist()}

            def generate(arguments, *, cwd, **kwargs):
                output = Path(cwd)/'dist'/wheel.name
                output.parent.mkdir()
                with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
                    for name, data in reversed(list(payloads.items())):
                        archive.writestr(name, data)

            with mock.patch.object(packager, '__file__', str(repository/'tools/package_blender_training.py')), \
                    mock.patch.object(packager.subprocess, 'run', side_effect=generate):
                # Equivalent contents need no new identity despite ZIP changes.
                packager.build_protocol(source)
                self.assertEqual(wheel.read_bytes(), original)
                payloads['lichtfeld_sessions/__init__.py'] += b'\n# Changed implementation\n'
                with self.assertRaisesRegex(ValueError, 'same distribution version'):
                    packager.build_protocol(source)
                self.assertEqual(wheel.read_bytes(), original)
                # A new distribution can be generated when no old identity exists.
                wheel.unlink()
                packager.build_protocol(source)
                with zipfile.ZipFile(wheel) as archive:
                    self.assertEqual(archive.read('lichtfeld_sessions/__init__.py'),
                                     payloads['lichtfeld_sessions/__init__.py'])

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
