import pytest

from app.services.password_policy import PasswordPolicyError, validate_password_policy


def test_accepts_password_matching_policy() -> None:
    validate_password_policy("StrongPass1!")


def test_rejects_password_without_required_character_groups() -> None:
    with pytest.raises(PasswordPolicyError) as error:
        validate_password_policy("password")

    assert "uppercase" in str(error.value)
    assert "number" in str(error.value)
    assert "special" in str(error.value)


def test_rejects_short_password() -> None:
    with pytest.raises(PasswordPolicyError) as error:
        validate_password_policy("Aa1!")

    assert "at least 8" in str(error.value)
