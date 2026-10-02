import asyncio
import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Lock
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from PIL import Image

from xeonolab import desktop, wallpaper_backup
from xeonolab.common import APIError
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


def test_over_budget_backup_is_rejected_before_copy_and_leaves_no_target(settings, tmp_path, monkeypatch):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    client.put("/api/v1/users/me/wallpaper", headers=auth(), content=_image((3, 4, 5)))
    destination = tmp_path.parent / f"{tmp_path.name}-over-budget"

    def should_not_snapshot(*args, **kwargs):
        raise AssertionError("copy/snapshot should not start after a budget rejection")

    monkeypatch.setattr(wallpaper_backup, "_snapshot_db", should_not_snapshot)
    with pytest.raises(WallpaperBackupError, match="exceeds the configured data budget"):
        create_desktop_wallpaper_backup(
            settings.data_dir, destination, max_total_bytes=0, free_space_reserve_bytes=0
        )
    assert not destination.exists()
    assert not list(destination.parent.glob(f".{destination.name}.*"))
    monkeypatch.setattr(wallpaper_backup.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(WallpaperBackupError, match="insufficient destination free space"):
        create_desktop_wallpaper_backup(
            settings.data_dir,
            destination,
            max_total_bytes=512 * 1024 * 1024,
            free_space_reserve_bytes=0,
        )
    assert not destination.exists()
    assert not list(destination.parent.glob(f".{destination.name}.*"))


def test_backup_rechecks_each_asset_pixel_frame_and_byte_limit(tmp_path, monkeypatch):
    animated = tmp_path / "animated.webp"
    frame = Image.new("RGB", (8, 8), (1, 2, 3))
    frame.save(
        animated,
        format="WEBP",
        save_all=True,
        append_images=[Image.new("RGB", (8, 8), (200, 100, 50))],
    )
    with pytest.raises(WallpaperBackupError, match="invalid wallpaper (encoding|image)"):
        wallpaper_backup._verify_asset(animated)

    too_many_pixels = tmp_path / "too-many-pixels.webp"
    Image.new("RGB", (8, 8), (20, 40, 60)).save(too_many_pixels, format="WEBP")
    monkeypatch.setattr(wallpaper_backup, "WALLPAPER_MAX_PIXELS", 63)
    with pytest.raises(WallpaperBackupError, match="16 MP"):
        wallpaper_backup._verify_asset(too_many_pixels)

    too_many_bytes = tmp_path / "too-many-bytes.webp"
    too_many_bytes.write_bytes(b"0" * (wallpaper_backup.WALLPAPER_MAX_BYTES + 1))
    with pytest.raises(WallpaperBackupError, match="5 MiB"):
        wallpaper_backup._verify_asset(too_many_bytes)


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


def test_encoded_webp_has_a_hard_output_size_limit(monkeypatch, tmp_path):
    payload = _image((11, 12, 13))
    original_save = Image.Image.save

    def oversized_save(image, target, *args, **kwargs):
        original_save(image, target, *args, **kwargs)
        target.write(b"x" * (wallpaper_backup.WALLPAPER_MAX_BYTES + 1))

    monkeypatch.setattr(Image.Image, "save", oversized_save)
    destination = tmp_path / "too-large.webp"
    with pytest.raises(APIError) as error:
        desktop.process_wallpaper_image(payload, destination)
    assert error.value.code == "WALLPAPER_TOO_LARGE_AFTER_ENCODING"
    assert not destination.exists()


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


@pytest.mark.anyio
@pytest.mark.parametrize("worker_phase", ["decode", "publish"])
async def test_cancelled_worker_finishes_before_slot_and_temp_cleanup(settings, monkeypatch, worker_phase):
    for token in ("alice", "bob", "opencode"):
        settings.dev_tokens[token]["scope"] += " desktop:read"
    app = create_app("desktop-web", settings)
    entered, release, finished = Event(), Event(), Event()
    counter_lock = Lock()
    started = 0
    completed = 0

    def pause(original):
        def wait_for_test_release(*args):
            nonlocal started, completed
            with counter_lock:
                started += 1
                if started == 2:
                    entered.set()
            assert release.wait(5)
            result = original(*args)
            with counter_lock:
                completed += 1
                if completed == 2:
                    finished.set()
            return result
        return wait_for_test_release

    target = "process_wallpaper_image" if worker_phase == "decode" else "publish_wallpaper"
    monkeypatch.setattr(desktop, target, pause(getattr(desktop, target)))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(client.put(
            "/api/v1/users/me/wallpaper", headers=auth("alice"), content=_image((2, 4, 6))
        ))
        second = asyncio.create_task(client.put(
            "/api/v1/users/me/wallpaper", headers=auth("bob"), content=_image((6, 4, 2))
        ))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            first.cancel()
            second.cancel()
            await asyncio.sleep(0.05)
            held = [app.state.wallpaper_processing_slots.acquire(blocking=False) for _ in range(2)]
            assert held == [False, False]
        finally:
            release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert await asyncio.to_thread(finished.wait, 2)
        acquired = [False, False]
        for _ in range(100):
            acquired = [app.state.wallpaper_processing_slots.acquire(blocking=False) for _ in range(2)]
            if acquired == [True, True]:
                break
            for got_slot in acquired:
                if got_slot:
                    app.state.wallpaper_processing_slots.release()
            await asyncio.sleep(0.01)
        assert acquired == [True, True]
        for _ in acquired:
            app.state.wallpaper_processing_slots.release()
        assert list((settings.data_dir / "wallpapers").glob("*.tmp")) == []
        follow_up = await client.put(
            "/api/v1/users/me/wallpaper", headers=auth("alice"), content=_image((12, 13, 14))
        )
        assert follow_up.status_code == 200


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
