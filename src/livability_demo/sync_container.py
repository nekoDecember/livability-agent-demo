"""Select the bind mount owner for the one-shot Compose data updater."""

from __future__ import annotations

import os
from pathlib import Path

from .config import Settings


def sync_identity(directory: Path, environ: dict[str, str]) -> tuple[int, int]:
    uid = environ.get("LIVABILITY_HOST_UID", "").strip()
    gid = environ.get("LIVABILITY_HOST_GID", "").strip()
    if bool(uid) != bool(gid):
        raise ValueError("LIVABILITY_HOST_UID and LIVABILITY_HOST_GID must be set together.")
    if uid:
        if not uid.isascii() or not uid.isdecimal() or not gid.isascii() or not gid.isdecimal():
            raise ValueError("LIVABILITY_HOST_UID/GID must be non-negative integer IDs.")
        values = int(uid), int(gid)
        if any(value >= 2**32 - 1 for value in values):
            raise ValueError("LIVABILITY_HOST_UID/GID exceed the supported ID range.")
        return values
    owner = directory.stat()
    return owner.st_uid, owner.st_gid


def assume_identity(uid: int, gid: int) -> None:
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    elif (os.geteuid(), os.getegid()) != (uid, gid):
        raise PermissionError(
            f"Cannot select sync UID:GID {uid}:{gid} from {os.geteuid()}:{os.getegid()}. "
            "Use the updater's Compose configuration or run the CLI as the directory owner."
        )


def main() -> None:
    settings = Settings()
    directory = settings.open_data_dir
    # Compose mounts the tracked open_data directory. Never chown or chmod it.
    uid, gid = sync_identity(directory, dict(os.environ))
    assume_identity(uid, gid)
    os.umask(0o022)  # The separate API user must be able to read published snapshots.
    print(f"Official data sync identity: {uid}:{gid}; directory: {directory}", flush=True)
    from .official_data_cli import main as sync_main

    sync_main()


if __name__ == "__main__":
    main()
