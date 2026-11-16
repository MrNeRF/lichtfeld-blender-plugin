# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Trusted local adapter, executed only by LichtFeld Studio's Python runtime."""
import hashlib
import inspect
import json
import sys
import time
import uuid
from dataclasses import asdict

PREFIX = 'LFS_PORTAL\t'


def emit(kind, value):
    # This pipe carries public account state and local artifact paths, never tokens.
    sys.__stdout__.write(PREFIX + json.dumps({'kind': kind, 'value': value}) + '\n')
    sys.__stdout__.flush()


def main():
    from _lfs_path_io import Path, native_path
    from lfs_plugins.portal_account import PortalAccountService, _locked_sidecar
    from lfs_plugins.portal_gallery import PortalGalleryClient
    from lfs_plugins.portal_security import checked_portal_url
    from lfs_plugins.private_directory import mkdir_private
    from lfs_plugins.gallery_logging import safe_text
    from lichtfeld_sessions.transport import atomic_json, read_json

    request = json.loads(sys.stdin.readline())
    action = request['action']
    account = PortalAccountService()

    def publish():
        snapshot = account.snapshot()
        emit('account', asdict(snapshot))
        return snapshot

    try:
        if ('reuse_completed' not in inspect.signature(PortalGalleryClient.download).parameters
                or 'revoke' not in inspect.signature(PortalAccountService.sign_out).parameters):
            raise ValueError('Update the selected LichtFeld Studio installation: its Portal client is incompatible.')
        snapshot = publish()
        if action == 'connect':
            account.start_device_flow()
            previous = None
            opened = None
            while True:
                snapshot = account.snapshot()
                if snapshot != previous:
                    publish()
                    previous = snapshot
                if snapshot.linking and snapshot.verification_uri_complete and opened != snapshot.verification_uri_complete:
                    opened = checked_portal_url(account, snapshot.verification_uri_complete)
                    emit('browser', opened)
                if not snapshot.linking and not account.busy:
                    break
                time.sleep(.2)
        elif action == 'sign_out':
            account.sign_out(revoke=bool(request.get('online', True)))
            publish()
        elif action in ('refresh', 'thumbnail', 'download'):
            if not account.sync_profile():
                raise ValueError('Could not verify Portal access. Connect your LichtFeld account again.')
            snapshot = publish()
            identity = (snapshot.email, snapshot.connected_since)
            expected = request.get('identity')
            if expected is not None and tuple(expected) != identity:
                raise ValueError('The Portal account changed. Refresh the gallery again.')
            client = PortalGalleryClient(account, expected_session=identity)
            root = Path(request['cache_directory'])
            owner = hashlib.sha256(repr((account.base_url, identity[0])).encode('utf-8')).hexdigest()
            if action == 'refresh':
                rows = client.list_scenes(owner_wide=True) or []
                emit('result', {'rows': [{'id': str(uuid.UUID(row['id'])),
                    'title': str(row.get('title') or 'Untitled')[:256],
                    'description': str(row.get('description') or '')[:4096]} for row in rows], 'identity': identity})
                return
            identifier = str(uuid.UUID(request['identifier']))
            if action == 'thumbnail':
                _, _, data = client.thumbnail(identifier)
                folder = root / 'thumbnails' / owner
                mkdir_private(folder, parents=True, exist_ok=True)
                path = folder / (identifier + '.png')
                import tempfile
                with tempfile.NamedTemporaryFile(dir=native_path(folder, force_extended=True), delete=False) as stream:
                    temporary = Path(stream.name)
                    try:
                        stream.write(data)
                        stream.close()
                        temporary.replace(path)
                    finally:
                        temporary.unlink(missing_ok=True)
                emit('result', {'path': str(path), 'identity': identity})
                return
            cache = root / 'assets' / owner / identifier
            mkdir_private(cache, parents=True, exist_ok=True)
            with _locked_sidecar(cache / 'download.lock', blocking=False):
                project, checkpoint = cache / 'asset.licht', cache / 'download.json'
                try:
                    saved = read_json(checkpoint)
                except (OSError, ValueError):
                    saved = None
                client.download(identifier, project, checkpoint=saved if isinstance(saved, dict) else None,
                    reuse_completed=True,
                    on_checkpoint=lambda value: atomic_json(checkpoint, value),
                    on_progress=lambda done, total: emit('progress', {'done': done, 'total': total}),
                    on_message=lambda message: emit('message', safe_text(message)),
                    on_processing=lambda state: emit('message', 'The Portal is preparing this asset…'))
                emit('result', {'project': str(project), 'identity': identity})
                # Keep the existing canonical cache lock through conversion and decoding.
                # The host closes stdin when its artifact use ends, including cancellation.
                sys.stdin.readline()
            return
        elif action == 'clear_cache':
            import shutil
            root = Path(request['cache_directory']).resolve()
            skipped = False
            for asset in root.glob('assets/*/*'):
                if not asset.is_dir() or not asset.resolve().is_relative_to(root):
                    continue
                try:
                    with _locked_sidecar(asset / 'download.lock', blocking=False):
                        for name in ('asset.licht', '.asset.licht.part', 'download.json', 'last-export.log'):
                            (asset / name).unlink(missing_ok=True)
                        for preparation in asset.glob('prepare-*'):
                            if preparation.is_dir() and preparation.resolve().is_relative_to(root):
                                shutil.rmtree(preparation)
                except OSError:
                    skipped = True
            for image in root.glob('thumbnails/*/*.png'):
                if image.resolve().is_relative_to(root):
                    try:
                        image.unlink(missing_ok=True)
                    except OSError:
                        skipped = True
            emit('result', {'message': 'Some cached files are in use. Try again later.' if skipped else 'Portal cache cleared'})
            return
        elif action != 'initialize':
            raise ValueError('Unknown Portal action')
        emit('result', {})
    except Exception as error:
        emit('error', safe_text(error))
        raise SystemExit(1)
    finally:
        if account.snapshot().linking:
            account.cancel_device_flow()


if __name__ == '__main__':
    main()
