"""Isolated PNG attachment preview (issue #152): decoding, metadata and ACL."""

import io

from PIL import Image, PngImagePlugin

from .conftest import auth

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def make_png(text=False):
    image = Image.new("RGBA", (32, 24), (10, 20, 30, 255))
    buffer = io.BytesIO()
    if text:
        info = PngImagePlugin.PngInfo()
        info.add_text("PreviewMeta", "should-not-leak")
        image.save(buffer, format="PNG", pnginfo=info)
    else:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def seed_table(library):
    table = library.post(
        "/api/v1/tables",
        headers=auth(),
        json={"path": "/预览", "name": "png", "columns": [{"name": "title", "type": "string", "nullable": False}]},
    )
    tid = table.json()["data"]["table_id"]
    row = library.post(
        f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"title": "preview"}}
    )
    return tid, row.json()["data"]["_row_id"]


def upload(library, tid, rid, name, content, media_type):
    result = library.post(
        "/api/v1/attachments",
        params={"table_id": tid, "row_id": rid},
        headers=auth(),
        files={"upload": (name, content, media_type)},
    )
    assert result.status_code == 201, result.text
    return result.json()["data"]["attachment_id"]


def test_png_preview_is_reencoded_without_metadata(library):
    tid, rid = seed_table(library)
    aid = upload(library, tid, rid, "image.png", make_png(text=True), "image/png")
    preview = library.get(f"/api/v1/attachments/{aid}/preview", params={"table_id": tid}, headers=auth())
    assert preview.status_code == 200, preview.text
    assert preview.headers["content-type"] == "image/png"
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert preview.headers["cache-control"] == "no-store"
    assert "sandbox" in preview.headers["content-security-policy"]
    assert b"PreviewMeta" not in preview.content, "source PNG text chunks never reach the browser"
    decoded = Image.open(io.BytesIO(preview.content))
    assert decoded.size == (32, 24)
    decoded.verify()


def test_preview_rejects_unsupported_corrupt_and_unauthorized_attachments(library):
    tid, rid = seed_table(library)
    pdf = upload(library, tid, rid, "proof.pdf", b"%PDF-test", "application/pdf")
    assert (
        library.get(f"/api/v1/attachments/{pdf}/preview", params={"table_id": tid}, headers=auth()).status_code
        == 415
    )
    corrupt = upload(library, tid, rid, "broken.png", PNG_MAGIC + b"truncated", "image/png")
    broken = library.get(f"/api/v1/attachments/{corrupt}/preview", params={"table_id": tid}, headers=auth())
    assert broken.status_code == 422
    assert broken.json()["error"]["code"] == "PREVIEW_DECODE_FAILED"

    library.app.state.settings.dev_tokens["preview-denied"] = {
        "sub": "preview-denied", "scope": "xelibrary:read", "agent_kind": "human"
    }
    assert (
        library.get(
            f"/api/v1/attachments/{pdf}/preview", params={"table_id": tid}, headers=auth("preview-denied")
        ).status_code
        in (403, 404)
    )
    missing = library.get("/api/v1/attachments/nope/preview", params={"table_id": tid}, headers=auth())
    assert missing.status_code in (403, 404)
