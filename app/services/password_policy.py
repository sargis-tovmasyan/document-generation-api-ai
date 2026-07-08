from __future__ import annotations

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 256


class PasswordPolicyError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_password_policy(password: str) -> None:
    errors: list[str] = []

    if len(password) < PASSWORD_MIN_LENGTH:
        errors.append(f"Password must be at least {PASSWORD_MIN_LENGTH} characters long")
    if len(password) > PASSWORD_MAX_LENGTH:
        errors.append(f"Password must be at most {PASSWORD_MAX_LENGTH} characters long")
    if not any(character.isupper() for character in password):
        errors.append("Password must contain at least one uppercase letter")
    if not any(character.islower() for character in password):
        errors.append("Password must contain at least one lowercase letter")
    if not any(character.isdigit() for character in password):
        errors.append("Password must contain at least one number")
    if not any(not character.isalnum() for character in password):
        errors.append("Password must contain at least one special character")

    if errors:
        raise PasswordPolicyError(errors)
