from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from ...core.errors import ApiError

register_heif_opener()
MAX_ORIGINAL_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 64_000_000
MIN_SHORT_EDGE = 720
HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis")


@dataclass(frozen=True)
class ImageMetadata:
    content_type: str
    width: int
    height: int


def _read_image(body: bytes) -> tuple[Image.Image, str]:
    if not body:
        raise ApiError(code="stool_photo_invalid_image", message="Image is empty or invalid.", status=422)
    if len(body) > MAX_ORIGINAL_BYTES:
        raise ApiError(code="payload_too_large", message="Original photo exceeds 10 MB.", status=413)
    try:
        with Image.open(BytesIO(body)) as source:
            image_format = source.format
            if image_format not in {"JPEG", "PNG", "HEIF"}:
                raise ApiError(code="stool_photo_unsupported_format", message="Use JPEG, PNG, or HEIC.", status=422)
            if image_format == "HEIF" and not (
                body[4:8] == b"ftyp" and any(brand in body[8:64] for brand in HEIC_BRANDS)
            ):
                raise ApiError(code="stool_photo_unsupported_format", message="Use JPEG, PNG, or HEIC.", status=422)
            width, height = source.size
            if width * height > MAX_PIXELS:
                raise ApiError(code="stool_photo_too_many_pixels", message="Image dimensions exceed the limit.", status=422)
            if min(width, height) < MIN_SHORT_EDGE:
                raise ApiError(code="stool_photo_low_resolution", message="Photo short edge must be at least 720 px.", status=422)
            source.load()
            return ImageOps.exif_transpose(source), image_format
    except ApiError:
        raise
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ApiError(code="stool_photo_invalid_image", message="Image is empty or invalid.", status=422) from exc


def validate_original(body: bytes) -> ImageMetadata:
    image, image_format = _read_image(body)
    try:
        return ImageMetadata(
            content_type={"JPEG": "image/jpeg", "PNG": "image/png", "HEIF": "image/heic"}[image_format],
            width=image.width,
            height=image.height,
        )
    finally:
        image.close()


def prepare_model_image(body: bytes) -> bytes:
    """Derived JPEG only; metadata and original remain separate/private."""
    image, _ = _read_image(body)
    try:
        output = BytesIO()
        image.convert("RGB").save(output, format="JPEG", quality=85, optimize=True)
        return output.getvalue()
    finally:
        image.close()


def prepare_bounded_model_image(
    body: bytes, *, max_side: int = 2048, max_bytes: int = 2_000_000,
) -> bytes:
    """Metadata-free model JPEG with explicit dimensions and byte ceiling."""
    if max_side < 720 or max_bytes < 100_000:
        raise ValueError("Invalid model image bounds")
    with Image.open(BytesIO(prepare_model_image(body))) as image:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        for quality in (85, 70, 55):
            output = BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            if len(output.getvalue()) <= max_bytes:
                return output.getvalue()
    raise ApiError(code="stool_photo_model_image_too_large", message="Photo cannot be prepared safely.", status=422)


def prepare_stool_thumbnail(body: bytes, *, content_type: str, width: int, height: int) -> bytes:
    """Private 512px JPEG preview. No source EXIF, original bytes or object key leave storage."""
    image, image_format = _read_image(body)
    try:
        actual_type = {"JPEG": "image/jpeg", "PNG": "image/png", "HEIF": "image/heic"}[image_format]
        if actual_type != content_type or image.width != width or image.height != height:
            raise ApiError(code="stool_photo_invalid_image", message="Image metadata changed.", status=422)
        image.thumbnail((512, 512), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.convert("RGB").save(output, format="JPEG", quality=78, optimize=True)
        return output.getvalue()
    finally:
        image.close()
