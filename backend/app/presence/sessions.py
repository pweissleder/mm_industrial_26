"""Client session leases: one active controlling session per profile, kept in this process.

Claiming a profile issues an opaque, unguessable token. Heartbeats extend the lease; a lease that
runs out frees the profile (its last position stays, marked offline). A reload may reclaim its own
still-valid lease with its token; a stale token can never act after another client claimed the
profile, because every claim gets a new token and a higher `generation`.
"""

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


class ProfileOccupiedError(RuntimeError):
    """Another client holds a valid lease on the profile."""


class SessionExpiredError(LookupError):
    """The token is unknown, released, expired, or superseded by a newer claim."""


@dataclass
class ClientSession:
    token: str
    profile_id: str
    generation: int
    expires_at: float  # on the lease clock (monotonic)


class SessionLeases:
    def __init__(self, lease_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.lease_seconds = lease_seconds
        self._clock = clock
        self._by_profile: dict[str, ClientSession] = {}
        self._by_token: dict[str, ClientSession] = {}
        self._generations: dict[str, int] = {}
        self._lock = threading.Lock()

    def _expire(self, now: float) -> list[ClientSession]:
        """Drop expired leases (lock held); returns them so callers can report offline."""
        gone = [s for s in self._by_profile.values() if s.expires_at <= now]
        for session in gone:
            del self._by_profile[session.profile_id]
            self._by_token.pop(session.token, None)
        return gone

    def claim(self, profile_id: str, token: str | None = None) -> ClientSession:
        """Take the profile; with the holder's own valid `token` this renews that lease instead."""
        with self._lock:
            now = self._clock()
            self._expire(now)
            held = self._by_profile.get(profile_id)
            if held is not None:
                if token is not None and secrets.compare_digest(held.token, token):
                    held.expires_at = now + self.lease_seconds
                    return held
                raise ProfileOccupiedError(profile_id)
            generation = self._generations.get(profile_id, 0) + 1
            self._generations[profile_id] = generation
            session = ClientSession(
                token=secrets.token_urlsafe(32),
                profile_id=profile_id,
                generation=generation,
                expires_at=now + self.lease_seconds,
            )
            self._by_profile[profile_id] = session
            self._by_token[session.token] = session
            return session

    def current(self, token: str | None) -> ClientSession:
        """The valid session for `token`, else `SessionExpiredError`."""
        with self._lock:
            self._expire(self._clock())
            session = self._by_token.get(token or "")
            if session is None:
                raise SessionExpiredError("No active session for this client")
            return session

    def heartbeat(self, token: str | None) -> ClientSession:
        with self._lock:
            now = self._clock()
            self._expire(now)
            session = self._by_token.get(token or "")
            if session is None:
                raise SessionExpiredError("No active session for this client")
            session.expires_at = now + self.lease_seconds
            return session

    def release(self, token: str | None) -> ClientSession | None:
        """End the session (idempotent); returns it if it was still active."""
        with self._lock:
            session = self._by_token.pop(token or "", None)
            if session is not None:
                self._by_profile.pop(session.profile_id, None)
            return session

    def occupied(self) -> set[str]:
        with self._lock:
            self._expire(self._clock())
            return set(self._by_profile)

    def seconds_left(self, session: ClientSession) -> float:
        return max(0.0, session.expires_at - self._clock())
