# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Worker-only job controller ownership and expiring commands."""
import math
import os
import stat
from pathlib import Path
import time
import uuid
if __package__:
    from .bridge_core import atomic_json, read_json
else:
    from bridge_core import atomic_json, read_json

ACTIONS = {"pause", "resume", "stop", "preview_on", "preview_off"}
COMMAND_SECONDS = 30


class Controller:
    """Kernel lock, released even on process death; never delete the lock inode."""
    def __init__(self, root):
        self.root = Path(root)
        self.id = uuid.uuid4().hex
        self.fd = None

    def acquire(self):
        if self.fd is not None:
            return True
        path = self.root / "controller.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            opened, named = os.fstat(fd), path.lstat()
            if (not stat.S_ISREG(named.st_mode) or not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
                raise ValueError("The job controller lock is not a regular local file")
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd
        try:
            atomic_json(self.root / "controller.json", {"id": self.id, "pid": os.getpid()})
        except BaseException:
            self.close()
            raise
        return True

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)

    def command(self, job_id, action):
        if action not in ACTIONS or self.fd is None:
            raise ValueError("This viewer does not control the trainer")
        now = time.time()
        return {"job_id": job_id, "id": uuid.uuid4().hex, "action": action,
                "controller": self.id, "created": now, "expires": now + COMMAND_SECONDS}


def validate_command(root, job_id, command, now=None):
    """Called at the training safe boundary, including while paused."""
    now = time.time() if now is None else now
    if not isinstance(command, dict) or command.get("job_id") != job_id:
        raise ValueError("Command belongs to another training job")
    if not isinstance(command.get("action"), str) or command.get("action") not in ACTIONS or not isinstance(command.get("id"), str) or not command["id"]:
        raise ValueError("Invalid training command")
    owner = read_json(Path(root) / "controller.json", {})
    if not isinstance(owner, dict) or not owner.get("id") or command.get("controller") != owner["id"]:
        raise ValueError("Training control changed; reconnect before sending commands")
    created, expires = command.get("created"), command.get("expires")
    if (type(created) not in (int, float) or type(expires) not in (int, float)
            or not 0 <= created <= 2**63 - 1 or not 0 <= expires <= 2**63 - 1
            or not math.isfinite(created) or not math.isfinite(expires)
            or not created <= now < expires or not 0 < expires - created <= COMMAND_SECONDS + 1):
        raise ValueError("Training command expired; send it again")
    return command["action"]
