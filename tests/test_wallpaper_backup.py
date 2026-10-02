import asyncio
import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Lock

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from PIL import Image

from xeonolab import desktop, wallpaper_backup
from xeonolab.services import create_app
from xeonolab.wallpaper_backup import (
    WallpaperBackupError,
    create_desktop_wallpaper_backup,
    restore_desktop_wallpaper_backup,
    verify_desktop_wallpaper_backup,
)

from .conftest import auth


def _image(color):
    output = io.BytesIO()
    Image.new("RGB", (48, 32), color).save(output, format="PNG")
    return output.getvalue()


def _enable_wallpaper_scope(settings):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    settings.dev_tokens["bob"]["scope"] += " desktop:read"


def test_desktop_wallpaper_snapshot_restores_account_assets_and_preferences(settings, tmp_path):
    _enable_wallpaper_scope(settings)
    client = TestClient(create_app("desktop-web", settings))
    for username, color, timezone in (
        ("alice", (220, 20, 30), "Asia/Shanghai"),
        ("bob", (10, 70, 230), "Pacific/Auckland"),
    ):
        assert client.put(
            "/api/v1/users/me/wallpaper", headers=auth(username), content=_image(color)
        ).status_code == 200
        assert client.patch(
            "/api/v1/users/me/preferences",
            headers=auth(username),
            json={"wallpaper": "custom", "timezone": timezone},
        ).status_code == 200

    backup = create_desktop_wallpaper_backup(settings.data_dir, tmp_path.parent / f"{tmp_path.name}-desktop-backup")
    assert verify_desktop_wallpaper_backup(backup)["format"] == "desktop-wallpaper-backup-v1"
    restored_root = restore_desktop_wallpaper_backup(backup, tmp_path.parent / f"{tmp_path.name}-isolated-restore")
    restored_settings = replace(settings, data_dir=restored_root)
    restored = TestClient(create_app("desktop-web", restored_settings))
    for username, timezone, expected_pixel in (
        ("alice", "Asia/Shanghai", (220, 20, 30)),
        ("bob", "Pacific/Auckland", (10, 70, 230)),
    ):
        preferences = restored.get("/api/v1/users/me/preferences", headers=auth(username)).json()["data"]
        image = restored.get("/api/v1/users/me/wallpaper", headers=auth(username))
        assert preferences["wallpaper"] == "custom"
        assert preferences["timezone"] == timezone
        assert image.status_code == 200
        with Image.open(io.BytesIO(image.content)) as decoded:
            actual = decoded.getpixel((10, 10))
            assert all(abs(left - right) <= 4 for left, right in zip(actual, expected_pixel))


def test_wallpaper_backup_rejects_missing_assets_and_owner_tampering(settings, tmp_path):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    client.put("/api/v1/users/me/wallpaper", headers=auth(), content=_image((1, 2, 3)))
    backup = create_desktop_wallpaper_backup(settings.data_dir, tmp_path.parent / f"{tmp_path.name}-wallpaper-backup")
    record = verify_desktop_wallpaper_backup(backup)["wallpapers"][0]
    (backup / "wallpapers" / f"{record['asset_id']}.webp").unlink()
    with pytest.raises(WallpaperBackupError, match="missing|inventory"):
        verify_desktop_wallpaper_backup(backup)

    # Restore a clean backup, then change the owner-to-asset relation without changing
    # the manifest. Verification must catch the mismatched ownership mapping.
    backup = create_desktop_wallpaper_backup(settings.data_dir, tmp_path.parent / f"{tmp_path.name}-wallpaper-backup-2")
    with sqlite3.connect(backup / "desktop.sqlite") as db:
        db.execute("UPDATE desktop_wallpapers SET owner='bob'")
    with pytest.raises(WallpaperBackupError, match="inventory"):
        verify_desktop_wallpaper_backup(backup)


def test_wallpaper_backup_rejects_symlinks_and_existing_restore_path(settings, tmp_path):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    client.put("/api/v1/users/me/wallpaper", headers=auth(), content=_image((1, 2, 3)))
    backup = create_desktop_wallpaper_backup(
        settings.data_dir, tmp_path.parent / f"{tmp_path.name}-symlink-backup"
    )
    record = verify_desktop_wallpaper_backup(backup)["wallpapers"][0]
    asset = backup / "wallpapers" / f"{record['asset_id']}.webp"
    external = tmp_path.parent / f"{tmp_path.name}-external.webp"
    external.write_bytes(asset.read_bytes())
    asset.unlink()
    asset.symlink_to(external)
    with pytest.raises(WallpaperBackupError, match="symlink|unsafe"):
        verify_desktop_wallpaper_backup(backup)
    target = tmp_path.parent / f"{tmp_path.name}-existing-restore"
    target.mkdir()
    with pytest.raises(WallpaperBackupError, match="must not already exist"):
        restore_desktop_wallpaper_backup(backup, target)


def test_concurrent_replacement_waits_for_consistent_snapshot(settings, tmp_path, monkeypatch):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    route = "/api/v1/users/me/wallpaper"
    assert client.put(route, headers=auth(), content=_image((5, 10, 15))).status_code == 200
    entered, release = Event(), Event()
    original_copy = wallpaper_backup.shutil.copyfile

    def pause_asset_copy(source, destination, *args, **kwargs):
        if str(source).endswith(".webp"):
            entered.set()
            assert release.wait(5)
        return original_copy(source, destination, *args, **kwargs)

    monkeypatch.setattr(wallpaper_backup.shutil, "copyfile", pause_asset_copy)
    backup_path = tmp_path.parent / f"{tmp_path.name}-concurrent-backup"
    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshot = pool.submit(create_desktop_wallpaper_backup, settings.data_dir, backup_path)
        assert entered.wait(2)
        replacement = pool.submit(client.put, route, headers=auth(), content=_image((30, 40, 50)))
        assert replacement.done() is False
        release.set()
        assert replacement.result(timeout=3).status_code == 200
        created = snapshot.result(timeout=3)
    verify_desktop_wallpaper_backup(created)
    restored = restore_desktop_wallpaper_backup(
        created, tmp_path.parent / f"{tmp_path.name}-concurrent-restore"
    )
    restored_client = TestClient(create_app("desktop-web", replace(settings, data_dir=restored)))
    restored_image = restored_client.get(route, headers=auth())
    assert restored_image.status_code == 200
    with Image.open(io.BytesIO(restored_image.content)) as decoded:
        old_color = decoded.getpixel((10, 10))
    assert all(abs(left - right) <= 5 for left, right in zip(old_color, (5, 10, 15)))


@pytest.mark.anyio
async def test_image_processing_runs_off_loop_and_capacity_returns_429(settings, monkeypatch, tmp_path):
    for token in ("alice", "bob", "opencode"):
        settings.dev_tokens[token]["scope"] += " desktop:read"
    app = create_app("desktop-web", settings)
    entered, release = Event(), Event()
    counter_lock = Lock()
    started = 0
    original = desktop.process_wallpaper_image

    def slow_process(payload, temporary):
        nonlocal started
        with counter_lock:
            started += 1
            if started == 2:
                entered.set()
        assert release.wait(5)
        return original(payload, temporary)

    monkeypatch.setattr(desktop, "process_wallpaper_image", slow_process)

    async def verify_responsiveness():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(client.put(
                "/api/v1/users/me/wallpaper", headers=auth("alice"), content=_image((3, 5, 7))
            ))
            second = asyncio.create_task(client.put(
                "/api/v1/users/me/wallpaper", headers=auth("bob"), content=_image((7, 5, 3))
            ))
            assert await asyncio.to_thread(entered.wait, 2)
            assert (await client.get("/health/ready")).status_code == 200
            busy = await client.put(
                "/api/v1/users/me/wallpaper", headers=auth("opencode"), content=_image((4, 6, 8))
            )
            assert busy.status_code == 429
            assert busy.json()["error"]["code"] == "WALLPAPER_PROCESSING_BUSY"
            release.set()
            first_response, second_response = await asyncio.gather(first, second)
            assert first_response.status_code == second_response.status_code == 200

    await verify_responsiveness()
    assert not list((settings.data_dir / "wallpapers").glob("*.tmp"))


def test_failed_image_encoder_cleans_temp_and_releases_capacity(settings, monkeypatch):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    app = create_app("desktop-web", settings)
    client = TestClient(app)
    payload = _image((8, 9, 10))

    def fail_save(*args, **kwargs):
        raise OSError("simulated encoder failure")

    monkeypatch.setattr(Image.Image, "save", fail_save)
    failed = client.put("/api/v1/users/me/wallpaper", headers=auth(), content=payload)
    assert failed.status_code == 422
    assert not list((settings.data_dir / "wallpapers").iterdir())
    monkeypatch.undo()
    recovered = client.put("/api/v1/users/me/wallpaper", headers=auth(), content=payload)
    assert recovered.status_code == 200
