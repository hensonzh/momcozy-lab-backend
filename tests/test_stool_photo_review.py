import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.modules.stool_photos.review import (
    DisabledReviewProvider, ReviewOutcome, review_photo, require_approved_photos,
    DecisionsReviewProvider,
)


class FakeProvider:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    async def review(self, image):
        self.calls += 1
        assert image.startswith(b"\xff\xd8")
        return self.outcome


class FakeStorage:
    def __init__(self, body):
        self.body = body
        self.reads = 0

    async def get_bytes(self, *, key):
        self.reads += 1
        return self.body


def photo(owner, status="pending", **kwargs):
    from datetime import datetime, timedelta, timezone
    values = dict(
        id=uuid4(), owner_user_id=owner, object_key="private/stool/original",
        sha256="ignored", review_status=status, deleted_at=None, purged_at=None,
        review_sha256="ignored", review_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        policy_version="test-v1",
    )
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_disabled_provider_never_approves():
    from test_stool_photo_rules import image_bytes
    provider = DisabledReviewProvider()
    result = asyncio.run(review_photo(image_bytes(), provider))
    assert result.status == "failed"
    assert result.reason_codes == ("review_unavailable",)


def test_provider_rejects_irrelevant_photo_without_medical_assessment():
    from test_stool_photo_rules import image_bytes
    provider = FakeProvider(ReviewOutcome("rejected", ("not_stool",), "test-v1"))
    result = asyncio.run(review_photo(image_bytes(), provider))
    assert result.status == "rejected"
    assert result.reason_codes == ("not_stool",)
    assert provider.calls == 1


def test_eligibility_requires_every_owned_active_passed_photo():
    owner = uuid4()
    first, second = photo(owner, "passed"), photo(owner, "passed")
    assert require_approved_photos([first, second], owner) == (first, second)
    for bad in [[], [first, first], [first, photo(uuid4(), "passed")],
                [first, photo(owner, "inconclusive")]]:
        with pytest.raises(ApiError):
            require_approved_photos(bad, owner)
    second.deleted_at = "deleted"
    with pytest.raises(ApiError):
        require_approved_photos([first, second], owner)


def test_review_without_expiry_or_with_purged_original_never_qualifies():
    from datetime import datetime, timedelta, timezone
    owner = uuid4()
    for change in (
        {"review_expires_at": None},
        {"purged_at": datetime.now(timezone.utc)},
        {"review_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
    ):
        with pytest.raises(ApiError) as captured:
            require_approved_photos([photo(owner, "passed", **change)], owner)
        assert captured.value.code == "stool_photos_not_approved"
    # SQLite may round-trip timestamps without tzinfo; interpret stored UTC consistently.
    naive_expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
    assert require_approved_photos([photo(owner, "passed", review_expires_at=naive_expiry)], owner)


def test_decisions_adapter_returns_inconclusive_without_calibrated_release_policy():
    async def fake_post(payload):
        assert payload["model"] == "gpt-6-luna"
        assert len(payload["questions"]) >= 4
        return {"answers": [
            {"name": q["name"], "type": "predicate", "probability": 0.99}
            for q in payload["questions"]
        ]}
    provider = DecisionsReviewProvider(post=fake_post, policy_version="candidate-v1")
    result = asyncio.run(provider.review(b"\xff\xd8fake"))
    assert result.status == "inconclusive"
    assert result.reason_codes == ("policy_not_calibrated",)


def test_passed_review_rejects_changed_content_and_expiry():
    from datetime import datetime, timedelta, timezone
    owner = uuid4()
    p = photo(owner, "passed")
    p.review_sha256 = "wrong"
    with pytest.raises(ApiError):
        require_approved_photos([p], owner)
    p.review_sha256 = p.sha256
    p.review_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(ApiError):
        require_approved_photos([p], owner)


def test_review_is_eligible_until_but_not_at_180_day_expiry():
    from datetime import datetime, timedelta, timezone

    owner = uuid4()
    reviewed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expiry = reviewed_at + timedelta(days=180)
    approved = photo(owner, "passed", review_expires_at=expiry)
    assert require_approved_photos([approved], owner, now=expiry - timedelta(microseconds=1)) == (approved,)
    with pytest.raises(ApiError) as error:
        require_approved_photos([approved], owner, now=expiry)
    assert error.value.code == "stool_photos_not_approved"
