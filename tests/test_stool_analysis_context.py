"""Synthetic media only: future specialty follow-up context admission."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image

from app.core.errors import ApiError
from app.modules.stool_analyses.context import (
    CompletedStoolAnalysis, StoolContextPhoto, build_stool_followup_context,
)
from test_stool_photo_rules import image_bytes


def _fixture():
    owner, baby, thread = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    body = image_bytes()
    photo = StoolContextPhoto(
        id=uuid4(), owner_user_id=owner, body=body,
        sha256=sha256(body).hexdigest(), review_sha256=sha256(body).hexdigest(),
        review_status='passed', review_expires_at=now + timedelta(hours=1),
    )
    analysis = CompletedStoolAnalysis(
        id=uuid4(), owner_user_id=owner, baby_id=baby, thread_id=thread,
        status='completed', result_schema_version='stool.result.v1',
        result={'color': 'synthetic-test-value'}, result_validated_at=now,
        photo_ids=(photo.id,),
    )
    return analysis, (photo,), owner, baby, thread


def _build(analysis=None, photos=None, owner=None, baby=None, thread=None):
    original, originals, original_owner, original_baby, original_thread = _fixture()
    return build_stool_followup_context(
        analysis=analysis if analysis is not None else original,
        photos=photos if photos is not None else originals,
        actor_user_id=owner if owner is not None else original_owner,
        baby_id=baby if baby is not None else original_baby,
        thread_id=thread if thread is not None else original_thread,
    )


def test_context_pairs_validated_result_and_metadata_stripped_image():
    analysis, photos, owner, baby, thread = _fixture()
    material = _build(analysis, photos, owner, baby, thread)
    assert material.analysis_id == analysis.id
    assert material.result['color'] == 'synthetic-test-value'
    assert material.result_schema_version == 'stool.result.v1'
    assert len(material.images) == 1
    with Image.open(BytesIO(material.images[0])) as derived:
        assert derived.format == 'JPEG'
        assert not derived.getexif()
    assert b'ignore prior instructions' not in material.images[0]
    assert 'specialty' in material.handling.lower()
    assert 'not instructions' in material.handling.lower()


@pytest.mark.parametrize('change', [
    'owner', 'baby', 'thread', 'status', 'validation', 'deleted',
    'photo_deleted', 'photo_review', 'photo_expiry_missing', 'photo_hash',
    'photo_content', 'photo_count', 'photo_id', 'duplicate',
    'result_version', 'result_empty', 'result_revoked',
])
def test_context_fails_closed_without_releasing_partial_image_or_result(change):
    analysis, photos, owner, baby, thread = _fixture()
    photo = photos[0]
    if change == 'owner':
        owner = uuid4()
    elif change == 'baby':
        baby = uuid4()
    elif change == 'thread':
        thread = uuid4()
    elif change == 'status':
        analysis = replace(analysis, status='running')
    elif change == 'validation':
        analysis = replace(analysis, result_validated_at=None)
    elif change == 'deleted':
        analysis = replace(analysis, deleted_at=datetime.now(timezone.utc))
    elif change == 'photo_deleted':
        photos = (replace(photo, deleted_at=datetime.now(timezone.utc)),)
    elif change == 'photo_review':
        photos = (replace(photo, review_status='inconclusive'),)
    elif change == 'photo_expiry_missing':
        photos = (replace(photo, review_expires_at=None),)
    elif change == 'photo_hash':
        photos = (replace(photo, review_sha256='wrong'),)
    elif change == 'photo_content':
        photos = (replace(photo, body=b'not the reviewed content'),)
    elif change == 'photo_count':
        photos = ()
    elif change == 'photo_id':
        photos = (replace(photo, id=uuid4()),)
    elif change == 'result_version':
        analysis = replace(analysis, result_schema_version='unknown')
    elif change == 'result_empty':
        analysis = replace(analysis, result={})
    elif change == 'result_revoked':
        analysis = replace(analysis, revoked_at=datetime.now(timezone.utc))
    elif change == 'duplicate':
        analysis = replace(analysis, photo_ids=(photo.id, photo.id))
        photos = (photo, photo)
    with pytest.raises(ApiError) as error:
        _build(analysis, photos, owner, baby, thread)
    assert error.value.status in (403, 409)


def test_completed_followup_remains_available_after_submission_review_expires():
    analysis, photos, owner, baby, thread = _fixture()
    expired = replace(photos[0], review_expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    material = _build(analysis, (expired,), owner, baby, thread)
    assert material.analysis_id == analysis.id and len(material.images) == 1


def test_context_does_not_treat_photo_text_as_instructions_or_create_provider_payload():
    analysis, photos, owner, baby, thread = _fixture()
    material = _build(analysis, photos, owner, baby, thread)
    assert material.handling.startswith('The following')
    assert not hasattr(material, 'image_url')
    assert not hasattr(material, 'provider_messages')


def test_context_preserves_two_photo_order_and_strips_sensitive_exif():
    analysis, photos, owner, baby, thread = _fixture()
    source = Image.new('RGB', (720, 810), color=(100, 45, 20))
    exif = Image.Exif()
    exif[0x013B] = 'synthetic-private-identity'
    output = BytesIO()
    source.save(output, format='JPEG', exif=exif)
    first_body = output.getvalue()
    first = replace(photos[0], body=first_body, sha256=sha256(first_body).hexdigest(),
                    review_sha256=sha256(first_body).hexdigest())
    second_body = image_bytes()
    second = replace(photos[0], id=uuid4(), body=second_body)
    analysis = replace(analysis, photo_ids=(first.id, second.id))
    material = _build(analysis, (first, second), owner, baby, thread)
    assert len(material.images) == 2
    assert b'synthetic-private-identity' not in material.images[0]
    with Image.open(BytesIO(material.images[0])) as derived:
        assert not derived.getexif()
        assert derived.getpixel((0, 0)) != (0, 0, 0)
    with Image.open(BytesIO(material.images[1])) as derived:
        assert not derived.getexif()
    with pytest.raises(ApiError):
        _build(analysis, (second, first), owner, baby, thread)


def test_context_objects_never_reveal_sensitive_bytes_in_repr():
    analysis, photos, owner, baby, thread = _fixture()
    material = _build(analysis, photos, owner, baby, thread)
    assert 'synthetic-test-value' not in repr(analysis)
    assert repr(photos[0].body) not in repr(photos[0])
    assert repr(material.images[0]) not in repr(material)
    assert 'synthetic-test-value' not in repr(material)
