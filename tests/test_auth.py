from datetime import UTC, datetime, timedelta

import pytest

from pocket48_summarizer.auth import (
    AuthRepository,
    AuthService,
    hash_password,
    normalize_username,
    token_digest,
    verify_password,
)
from pocket48_summarizer.errors import AppError


def test_password_hash_round_trip():
    encoded = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password value", encoded)


@pytest.mark.parametrize(
    "username", ["ab", "contains space", "x" * 33, "中文用户名"]
)
def test_rejects_invalid_username(username):
    with pytest.raises(AppError):
        normalize_username(username)


def test_login_creates_session_and_locks_after_failures(settings, repository):
    auth_repository = AuthRepository(repository.database)
    user = auth_repository.create_user(
        "invited-user",
        "invited-user",
        hash_password("a secure invited password"),
        is_admin=False,
    )
    service = AuthService(settings, auth_repository)
    logged_in, token, csrf = service.login(
        "INVITED-USER", "a secure invited password"
    )
    assert logged_in.id == user.id
    assert token and csrf

    for _ in range(5):
        with pytest.raises(AppError, match="用户名或密码"):
            service.login("invited-user", "incorrect password")
    locked = auth_repository.get_user_by_id(user.id)
    assert locked and locked.locked_until
    assert datetime.fromisoformat(locked.locked_until) > datetime.now(UTC)


def test_expired_session_is_not_returned(settings, repository):
    auth_repository = AuthRepository(repository.database)
    user = auth_repository.create_user(
        "session-user",
        "session-user",
        hash_password("a secure session password"),
        is_admin=False,
    )
    auth_repository.create_session(
        user.id,
        "token-hash",
        "csrf-hash",
        (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )
    assert auth_repository.get_session("token-hash", datetime.now(UTC).isoformat()) is None


def test_password_reset_revokes_existing_sessions(settings, repository):
    auth_repository = AuthRepository(repository.database)
    user = auth_repository.create_user(
        "reset-user",
        "reset-user",
        hash_password("the original secure password"),
        is_admin=False,
    )
    service = AuthService(settings, auth_repository)
    _, token, _ = service.login("reset-user", "the original secure password")
    assert auth_repository.get_session(
        token_digest(token), datetime.now(UTC).isoformat()
    )

    auth_repository.update_password(
        user.id, hash_password("the replacement secure password")
    )
    assert (
        auth_repository.get_session(
            token_digest(token), datetime.now(UTC).isoformat()
        )
        is None
    )
