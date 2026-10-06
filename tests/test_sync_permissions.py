from __future__ import annotations

from pathlib import Path

import pytest

from livability_demo import sync_container
from livability_demo.official_data_cli import check_output_writable


def test_identity_defaults_to_directory_owner(tmp_path: Path) -> None:
    owner = tmp_path.stat()
    assert sync_container.sync_identity(tmp_path, {}) == (owner.st_uid, owner.st_gid)


def test_explicit_identity_overrides_mount_owner(tmp_path: Path) -> None:
    assert sync_container.sync_identity(
        tmp_path, {"LIVABILITY_HOST_UID": "1000", "LIVABILITY_HOST_GID": "1001"}
    ) == (1000, 1001)


@pytest.mark.parametrize("values", [
    {"LIVABILITY_HOST_UID": "1000"},
    {"LIVABILITY_HOST_UID": "-1", "LIVABILITY_HOST_GID": "20"},
    {"LIVABILITY_HOST_UID": "name", "LIVABILITY_HOST_GID": "20"},
    {"LIVABILITY_HOST_UID": "4294967295", "LIVABILITY_HOST_GID": "20"},
])
def test_invalid_identity_fails_before_sync(tmp_path: Path, values: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        sync_container.sync_identity(tmp_path, values)


def test_privileges_drop_before_sync_without_chown(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(sync_container.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        sync_container.os, "setgroups", lambda groups: calls.append(("groups", groups))
    )
    monkeypatch.setattr(sync_container.os, "setgid", lambda gid: calls.append(("gid", gid)))
    monkeypatch.setattr(sync_container.os, "setuid", lambda uid: calls.append(("uid", uid)))
    sync_container.assume_identity(1000, 1001)
    assert calls == [("groups", []), ("gid", 1001), ("uid", 1000)]


def test_nonroot_cannot_silently_use_wrong_identity(monkeypatch) -> None:
    monkeypatch.setattr(sync_container.os, "geteuid", lambda: 501)
    monkeypatch.setattr(sync_container.os, "getegid", lambda: 20)
    with pytest.raises(PermissionError, match="1000:1000"):
        sync_container.assume_identity(1000, 1000)


def test_write_check_preserves_existing_data_and_leaves_no_probe(tmp_path: Path) -> None:
    original = tmp_path / "manifest.json"
    original.write_text("original")
    check_output_writable(tmp_path)
    assert original.read_text() == "original"
    assert list(tmp_path.iterdir()) == [original]


def test_permission_error_reports_identity_without_modifying_data(
    tmp_path: Path, monkeypatch
) -> None:
    original = tmp_path / "manifest.json"
    original.write_text("original")

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("livability_demo.official_data_cli.tempfile.TemporaryFile", denied)
    with pytest.raises(PermissionError, match="sync UID:GID=.*directory owner=.*Existing data"):
        check_output_writable(tmp_path)
    assert original.read_text() == "original"
