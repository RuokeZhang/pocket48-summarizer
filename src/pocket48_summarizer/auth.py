from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import Request

from .config import Settings
from .db import Database
from .errors import AppError
from .models import SessionRecord, UserRecord
from .repository import utcnow

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 128 * 1024 * 1024
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: UserRecord
    session: SessionRecord | None
    csrf_token: str


class AuthRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _user(row: sqlite3.Row | None) -> UserRecord | None:
        if row is None:
            return None
        return UserRecord.model_validate(dict(row))

    def get_user_by_id(self, user_id: str) -> UserRecord | None:
        with self.database.connect() as connection:
            return self._user(
                connection.execute(
                    "SELECT * FROM users WHERE id = ?", (user_id,)
                ).fetchone()
            )

    def get_user_by_username(self, normalized: str) -> UserRecord | None:
        with self.database.connect() as connection:
            return self._user(
                connection.execute(
                    """
                    SELECT * FROM users
                    WHERE username_normalized = ?
                    """,
                    (normalized,),
                ).fetchone()
            )

    def list_users(self) -> list[UserRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM users
                WHERE id != 'local'
                ORDER BY created_at
                """
            ).fetchall()
            return [
                user for row in rows if (user := self._user(row)) is not None
            ]

    def create_user(
        self,
        username: str,
        normalized: str,
        password_hash: str,
        *,
        is_admin: bool,
    ) -> UserRecord:
        user_id = str(uuid.uuid4())
        now = utcnow()
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO users (
                        id, username, username_normalized, password_hash,
                        is_admin, is_active, failed_login_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, 1, 0, ?)
                    """,
                    (
                        user_id,
                        username,
                        normalized,
                        password_hash,
                        int(is_admin),
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise AppError(
                "username_exists", "用户名已经存在", False
            ) from exc
        user = self.get_user_by_id(user_id)
        if user is None:
            raise AppError(
                "user_create_failed", "创建用户后无法读取记录", False
            )
        return user

    def update_password(self, user_id: str, password_hash: str) -> None:
        with self.database.connect() as connection:
            updated = connection.execute(
                """
                UPDATE users
                SET password_hash = ?, failed_login_count = 0,
                    locked_until = NULL
                WHERE id = ? AND id != 'local'
                """,
                (password_hash, user_id),
            ).rowcount
            if updated != 1:
                raise AppError("user_not_found", "用户不存在", False)
            connection.execute(
                "DELETE FROM user_sessions WHERE user_id = ?", (user_id,)
            )

    def set_active(self, user_id: str, active: bool) -> None:
        with self.database.connect() as connection:
            updated = connection.execute(
                """
                UPDATE users SET is_active = ?
                WHERE id = ? AND id != 'local'
                """,
                (int(active), user_id),
            ).rowcount
            if updated != 1:
                raise AppError("user_not_found", "用户不存在", False)
            if not active:
                connection.execute(
                    "DELETE FROM user_sessions WHERE user_id = ?", (user_id,)
                )

    def record_login_failure(self, user_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT failed_login_count FROM users WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
            if row is None:
                return
            failures = int(row["failed_login_count"]) + 1
            locked_until = None
            if failures >= MAX_FAILED_LOGINS:
                locked_until = (
                    datetime.now(UTC) + timedelta(minutes=LOCKOUT_MINUTES)
                ).isoformat()
                failures = 0
            connection.execute(
                """
                UPDATE users
                SET failed_login_count = ?, locked_until = ?
                WHERE id = ?
                """,
                (failures, locked_until, user_id),
            )

    def record_login_success(self, user_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE users
                SET failed_login_count = 0, locked_until = NULL,
                    last_login_at = ?
                WHERE id = ?
                """,
                (utcnow(), user_id),
            )

    def create_session(
        self,
        user_id: str,
        token_hash: str,
        csrf_token_hash: str,
        expires_at: str,
    ) -> str:
        session_id = str(uuid.uuid4())
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO user_sessions (
                    id, user_id, token_hash, csrf_token_hash,
                    created_at, expires_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    user_id,
                    token_hash,
                    csrf_token_hash,
                    now,
                    expires_at,
                    now,
                ),
            )
        return session_id

    def get_session(self, token_hash: str, now: str) -> SessionRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    s.id AS session_id,
                    s.user_id AS session_user_id,
                    s.token_hash,
                    s.csrf_token_hash,
                    s.created_at AS session_created_at,
                    s.expires_at,
                    s.last_seen_at,
                    u.*
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ?
                  AND u.is_active = 1
                """,
                (token_hash, now),
            ).fetchone()
            if row is None:
                return None
            user = UserRecord.model_validate(
                {
                    "id": row["id"],
                    "username": row["username"],
                    "username_normalized": row["username_normalized"],
                    "password_hash": row["password_hash"],
                    "is_admin": row["is_admin"],
                    "is_active": row["is_active"],
                    "failed_login_count": row["failed_login_count"],
                    "locked_until": row["locked_until"],
                    "created_at": row["created_at"],
                    "last_login_at": row["last_login_at"],
                }
            )
            return SessionRecord(
                id=row["session_id"],
                user_id=row["session_user_id"],
                token_hash=row["token_hash"],
                csrf_token_hash=row["csrf_token_hash"],
                created_at=row["session_created_at"],
                expires_at=row["expires_at"],
                last_seen_at=row["last_seen_at"],
                user=user,
            )

    def touch_session(self, session_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE user_sessions SET last_seen_at = ? WHERE id = ?
                """,
                (utcnow(), session_id),
            )

    def delete_session(self, token_hash: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM user_sessions WHERE token_hash = ?",
                (token_hash,),
            )

    def delete_expired_sessions(self, now: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM user_sessions WHERE expires_at <= ?", (now,)
            )


class AuthService:
    def __init__(self, settings: Settings, repository: AuthRepository) -> None:
        self.settings = settings
        self.repository = repository
        self._fake_hash = hash_password("not-a-real-user-password")

    def optional_context(self, request: Request) -> AuthContext | None:
        if not self.settings.auth_required:
            user = self.repository.get_user_by_id("local")
            if user is None:
                raise AppError(
                    "local_user_missing", "本地用户记录缺失", False
                )
            return AuthContext(user=user, session=None, csrf_token="")
        token = request.cookies.get(self.settings.session_cookie_name)
        csrf_token = request.cookies.get(self.settings.csrf_cookie_name, "")
        if not token:
            return None
        session = self.repository.get_session(
            token_digest(token), utcnow()
        )
        if session is None:
            return None
        if (
            not csrf_token
            or not hmac.compare_digest(
                token_digest(csrf_token), session.csrf_token_hash
            )
        ):
            csrf_token = ""
        self.repository.touch_session(session.id)
        return AuthContext(
            user=session.user,
            session=session,
            csrf_token=csrf_token,
        )

    def authenticate(self, request: Request) -> AuthContext:
        context = self.optional_context(request)
        if context is None:
            raise AppError(
                "authentication_required", "请先登录或重新登录", False
            )
        return context

    def login(self, username: str, password: str) -> tuple[UserRecord, str, str]:
        normalized = normalize_username(username)
        user = self.repository.get_user_by_username(normalized)
        password_hash = (
            user.password_hash if user and user.password_hash else self._fake_hash
        )
        valid_password = verify_password(password, password_hash)
        if user and user.locked_until:
            try:
                locked_until = datetime.fromisoformat(user.locked_until)
            except ValueError:
                locked_until = datetime.now(UTC)
            if locked_until > datetime.now(UTC):
                raise AppError(
                    "login_locked",
                    "登录失败次数过多，请稍后再试",
                    False,
                )
        if not user or not user.is_active or not valid_password:
            if user:
                self.repository.record_login_failure(user.id)
            raise AppError(
                "invalid_credentials", "用户名或密码错误", False
            )
        self.repository.record_login_success(user.id)
        self.repository.delete_expired_sessions(utcnow())
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires = (
            datetime.now(UTC)
            + timedelta(days=self.settings.session_ttl_days)
        ).isoformat()
        self.repository.create_session(
            user.id,
            token_digest(session_token),
            token_digest(csrf_token),
            expires,
        )
        return user, session_token, csrf_token

    def logout(
        self,
        request: Request,
        context: AuthContext,
        form_token: str | None = None,
    ) -> None:
        self.require_csrf(request, context, form_token)
        token = request.cookies.get(self.settings.session_cookie_name)
        if token:
            self.repository.delete_session(token_digest(token))

    def require_csrf(
        self,
        request: Request,
        context: AuthContext,
        form_token: str | None = None,
    ) -> None:
        if not self.settings.auth_required:
            return
        supplied = request.headers.get("X-CSRF-Token") or form_token or ""
        cookie_token = request.cookies.get(self.settings.csrf_cookie_name, "")
        if (
            not supplied
            or not context.csrf_token
            or not hmac.compare_digest(supplied, cookie_token)
            or not hmac.compare_digest(supplied, context.csrf_token)
        ):
            raise AppError(
                "csrf_failed", "请求安全令牌无效，请刷新页面后重试", False
            )

    @staticmethod
    def quota_day_start_utc() -> str:
        china = ZoneInfo("Asia/Shanghai")
        now_china = datetime.now(china)
        start_china = now_china.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start_china.astimezone(UTC).isoformat()


def normalize_username(username: str) -> str:
    username = username.strip()
    if not USERNAME_RE.fullmatch(username):
        raise AppError(
            "invalid_username",
            "用户名需为 3–32 位字母、数字、点、下划线或连字符",
            False,
        )
    return username.casefold()


def validate_password(password: str) -> None:
    if len(password) < 12 or len(password) > 128:
        raise AppError(
            "invalid_password",
            "密码长度必须为 12–128 个字符",
            False,
        )


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )
    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_value, digest_value = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_value.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
