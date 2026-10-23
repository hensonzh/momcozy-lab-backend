from io import BytesIO

import pytest
from PIL import Image

from app.core.errors import ApiError
from app.modules.stool_photos.rules import (
    prepare_bounded_model_image, prepare_model_image, validate_original,
)


def image_bytes(size=(720, 900), image_format="JPEG"):
    image = Image.new("RGB", size, (180, 110, 50))
    out = BytesIO()
    image.save(out, format=image_format)
    return out.getvalue()


def test_validates_short_edge_on_decoded_original_and_derives_private_jpeg():
    original = image_bytes((900, 720), "PNG")
    meta = validate_original(original)
    assert (meta.content_type, meta.width, meta.height) == ("image/png", 900, 720)
    derived = prepare_model_image(original)
    assert derived.startswith(b"\xff\xd8")
    with Image.open(BytesIO(derived)) as image:
        assert image.format == "JPEG"
        assert image.size == (900, 720)
        assert "exif" not in image.info


@pytest.mark.parametrize("body,reason", [
    (b"bad", "stool_photo_invalid_image"),
    (image_bytes((719, 900)), "stool_photo_low_resolution"),
    (image_bytes((720, 900), "GIF"), "stool_photo_unsupported_format"),
    (b"x" * (10 * 1024 * 1024 + 1), "payload_too_large"),
])
def test_rejects_invalid_originals_without_attempting_review(body, reason):
    with pytest.raises(ApiError) as exc:
        validate_original(body)
    assert exc.value.code == reason


def test_rejects_truncated_jpeg_even_if_header_and_dimensions_are_valid():
    with pytest.raises(ApiError) as exc:
        validate_original(image_bytes()[:200])
    assert exc.value.code == "stool_photo_invalid_image"


def test_heic_original_is_decoded_and_private_derived_jpeg_has_no_metadata():
    from pillow_heif import from_pillow
    source = Image.new("RGB", (800, 900), "green")
    out = BytesIO()
    from_pillow(source).save(out, format="HEIF")
    body = out.getvalue()
    assert validate_original(body).content_type == "image/heic"
    with Image.open(BytesIO(prepare_model_image(body))) as derived:
        assert derived.format == "JPEG" and derived.size == (800, 900)
        assert "exif" not in derived.info


def test_exif_rotation_changes_display_orientation_and_is_stripped():
    source = Image.new("RGB", (720, 900), "blue")
    exif = Image.Exif()
    exif[274] = 6
    out = BytesIO()
    source.save(out, format="JPEG", exif=exif)
    original = out.getvalue()
    assert (validate_original(original).width, validate_original(original).height) == (900, 720)
    with Image.open(BytesIO(prepare_model_image(original))) as derived:
        assert derived.size == (900, 720)
        assert "exif" not in derived.info


def test_bounded_model_image_caps_followup_dimensions_and_bytes():
    derived = prepare_bounded_model_image(image_bytes((3000, 2400), "PNG"))
    assert len(derived) <= 2_000_000
    with Image.open(BytesIO(derived)) as image:
        assert max(image.size) <= 2048
        assert image.format == "JPEG"


def test_rejects_decompression_bomb_dimensions_before_loading_pixels():
    import struct
    import zlib
    valid = image_bytes((720, 900), "PNG")
    ihdr = struct.pack(">II", 9000, 8000) + valid[24:29]
    crc = zlib.crc32(b"IHDR" + ihdr).to_bytes(4, "big")
    oversized = valid[:16] + ihdr + crc + valid[33:]
    with pytest.raises(ApiError) as exc:
        validate_original(oversized)
    assert exc.value.code == "stool_photo_too_many_pixels"
