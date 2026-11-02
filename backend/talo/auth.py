"""Password hashing, JWT session cookie, CSRF token and login rate limiting."""
from __future__ import annotations

import collections
import secrets
import time

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, Response

SESSION_COOKIE, CSRF_COOKIE, TTL = "talo_session", "talo_csrf", 8 * 3600
_ph = PasswordHasher()
_DUMMY = _ph.hash("not-a-real-password")


def ph_hash(password: str) -> str:
    return _ph.hash(password)


def ph_verify(hashed: str | None, password: str) -> bool:
    """Constant-ish time: unknown users still cost one hash verification."""
    try:
        return _ph.verify(hashed or _DUMMY, password) and hashed is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


class Auth:
    def __init__(self, secret: str, secure_cookies: bool = False):
        self.secret, self.secure = secret, secure_cookies

    def issue(self, response: Response, user_id: int) -> None:
        token = jwt.encode({"sub": str(user_id), "exp": int(time.time()) + TTL}, self.secret, "HS256")
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                            secure=self.secure, max_age=TTL)
        response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(32), httponly=False,
                            samesite="lax", secure=self.secure, max_age=TTL)

    def clear(self, response: Response) -> None:
        response.delete_cookie(SESSION_COOKIE)
        response.delete_cookie(CSRF_COOKIE)

    def user_id(self, request: Request) -> int | None:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        try:
            return int(jwt.decode(token, self.secret, algorithms=["HS256"])["sub"])
        except (jwt.PyJWTError, KeyError, ValueError):
            return None


def check_csrf(request: Request) -> None:
    cookie, header = request.cookies.get(CSRF_COOKIE), request.headers.get("X-CSRF-Token")
    if not cookie or not header or not secrets.compare_digest(cookie.encode(), header.encode()):
        raise HTTPException(403, "CSRF check failed")


class LoginLimiter:
    """Blocks a key after `limit` failures within `window` seconds."""

    def __init__(self, limit: int = 5, window: int = 300):
        self.limit, self.window = limit, window
        self.fails: dict[str, collections.deque] = collections.defaultdict(collections.deque)

    def _prune(self, key: str) -> collections.deque:
        q, now = self.fails[key], time.monotonic()
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def check(self, key: str) -> None:
        if len(self._prune(key)) >= self.limit:
            raise HTTPException(429, "Too many failed attempts. Try again later.")

    def fail(self, key: str) -> None:
        self._prune(key).append(time.monotonic())

    def reset(self, key: str) -> None:
        self.fails.pop(key, None)
