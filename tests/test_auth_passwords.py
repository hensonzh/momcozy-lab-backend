from app.modules.auth.passwords import hash_password, verify_password


def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")

    assert first != second
    assert "correct horse" not in first
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong password", first)


def test_password_verifier_rejects_malformed_hash() -> None:
    assert not verify_password("password", "not-a-valid-hash")
