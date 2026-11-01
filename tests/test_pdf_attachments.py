"""PDF attachments in chat: the text layer, scans as page images, and
password-protected statements (Indian bank e-statements usually are).

fixtures/statement_locked.pdf is the LINES statement below, AES-256 encrypted
with the password "1234".
"""

import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path

import pytest

pytest.importorskip("pypdfium2")

from backend.agent import tools  # noqa: E402
from backend.routers import chat  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
USER_ID = 7
LINES = ["HDFC BANK - Statement of account", "01/10/2026 SWIGGY ORDER 450.00 DR",
         "03/10/2026 SALARY OCT 85000.00 CR", "05/10/2026 UBER TRIP 230.00 DR"]


def _text_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with a real text layer — what a bank e-statement is."""
    content = "BT /F1 11 Tf 56 760 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _scan_pdf(pages: int) -> bytes:
    """Image-only pages, like a phone scan: no text layer at all."""
    from PIL import Image, ImageDraw

    imgs = []
    for n in range(pages):
        img = Image.new("RGB", (850, 1100), "white")
        ImageDraw.Draw(img).text((60, 60), f"RECEIPT page {n + 1} TOTAL 450", fill="black")
        imgs.append(img)
    out = BytesIO()
    imgs[0].save(out, format="PDF", save_all=True, append_images=imgs[1:])
    return out.getvalue()


@pytest.fixture
def jail(tmp_path, monkeypatch):
    """Uploads live under data/uploads/<user_id>/ — point that at a temp dir."""
    monkeypatch.setattr(tools, "UPLOAD_DIR", tmp_path)
    user_dir = tmp_path / str(USER_ID)
    user_dir.mkdir()
    return user_dir


def _inspect(path: Path, **extra) -> dict:
    return asyncio.run(tools.inspect_file(USER_ID, {"path": str(path), **extra}))


def _meta(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def _locked(jail: Path) -> Path:
    path = jail / "locked.pdf"
    path.write_bytes((FIXTURES / "statement_locked.pdf").read_bytes())
    return path


def test_upload_route_accepts_pdf():
    assert ".pdf" in chat.ALLOWED_SUFFIXES


def test_tool_schema_offers_an_optional_password():
    _, _, schema, _ = next(s for s in tools.TOOL_SPECS if s[0] == "inspect_file")
    assert "password" in schema["properties"]
    assert schema["required"] == ["path"]


def test_text_pdf_comes_back_as_text(jail):
    path = jail / "statement.pdf"
    path.write_bytes(_text_pdf(LINES))
    out = _meta(_inspect(path))
    assert out["kind"] == "pdf"
    assert (out["pages"], out["pages_read"], out["truncated"]) == (1, 1, False)
    assert "SWIGGY ORDER 450.00" in out["text"]
    assert "\r" not in out["text"]
    assert "add_transactions" in out["next_step"]


def test_long_text_is_capped_and_flagged(jail, monkeypatch):
    monkeypatch.setattr(tools, "PDF_MAX_TEXT_CHARS", 40)
    path = jail / "statement.pdf"
    path.write_bytes(_text_pdf(LINES))
    out = _meta(_inspect(path))
    assert out["truncated"] is True
    assert len(out["text"]) == 40


def test_scan_comes_back_as_capped_page_images(jail):
    from PIL import Image

    path = jail / "scan.pdf"
    path.write_bytes(_scan_pdf(tools.PDF_MAX_IMAGE_PAGES + 2))
    result = _inspect(path)
    meta = _meta(result)
    images = [c for c in result["content"] if c["type"] == "image"]
    assert meta["scanned"] is True
    assert meta["pages"] == tools.PDF_MAX_IMAGE_PAGES + 2
    assert len(images) == meta["pages_shown"] == tools.PDF_MAX_IMAGE_PAGES
    first = Image.open(BytesIO(base64.b64decode(images[0]["data"])))
    assert images[0]["mimeType"] == "image/jpeg" and first.format == "JPEG"
    assert max(first.size) <= tools.IMAGE_MAX_DIMENSION


def test_locked_pdf_asks_for_the_password(jail):
    out = _meta(_inspect(_locked(jail)))
    assert out["password_required"] is True
    assert "text" not in out


def test_locked_pdf_opens_with_the_password(jail):
    out = _meta(_inspect(_locked(jail), password="1234"))
    assert "SALARY OCT 85000.00" in out["text"]


def test_wrong_password_is_an_error(jail):
    result = _inspect(_locked(jail), password="nope")
    assert result.get("is_error") is True
    assert "Wrong PDF password" in result["content"][0]["text"]


def test_not_really_a_pdf_is_an_error_not_a_crash(jail):
    path = jail / "fake.pdf"
    path.write_bytes(b"this is not a pdf")
    result = _inspect(path)
    assert result.get("is_error") is True
    assert "Could not open the PDF" in result["content"][0]["text"]
