"""PresenceService: who is here, who controls which profile, and where people last were.

- `UserProfile`: the configured people (`profiles.py`).
- `ClientSession`: a passwordless lease on one profile, held by one client (`sessions.py`).
- `UserPresence`: a person's last-known validated position (`positions.py`), online while a
  session holds the profile, offline otherwise. Disconnecting never deletes a position.

Positions are validated against the live building (`NavigationService.place`). A saved position
whose building changed is revalidated on read; if it no longer resolves it is reported
`available: false` with the reason, never moved to a guessed place.
"""

import logging
import math
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from app.navigation.errors import NavigationError
from app.presence.positions import PositionStore, StoredPosition
from app.presence.profiles import UserProfile
from app.presence.sessions import (
    ClientSession,
    ProfileOccupiedError,
    SessionExpiredError,
    SessionLeases,
)

JSON = dict[str, Any]
log = logging.getLogger(__name__)
# How far outside its room a person's point may be: standing still, and while walking.
PLACE_TOLERANCE_M = 0.05
WALKING_TOLERANCE_M = 1.5
SOURCES = ("manual", "simulation", "qr", "hypothesis")
MAX_LABEL = 200


class PresenceError(ValueError):
    def __init__(self, code: str, message: str, status: int, details: JSON | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details or {}

    def as_dict(self) -> JSON:
        return {"code": self.code, "message": self.message, "details": self.details}


class Places(Protocol):
    """The part of NavigationService presence needs."""

    def place(self, building_id: str, spec: Any, tolerance_m: float = ...) -> JSON: ...

    def has_building(self, building_id: Any) -> bool: ...


class PresenceService:
    def __init__(
        self,
        profiles: list[UserProfile],
        leases: SessionLeases,
        positions: PositionStore,
        places: Places,
    ) -> None:
        self.profiles = {p.id: p for p in profiles}
        self.leases = leases
        self.positions = positions
        self.places = places
        # Called with the ids of buildings whose people changed (live updates, `live.py`).
        self.listeners: list[Callable[[Iterable[str | None]], None]] = []

    def _changed(self, *building_ids: str | None) -> None:
        for listener in self.listeners:
            listener(building_ids)

    def building_of(self, profile_id: str) -> str | None:
        stored = self.positions.get(profile_id)
        return stored.building_id if stored else None

    # ------------------------------------------------------------ profiles and sessions

    def directory(self) -> JSON:
        occupied = self.leases.occupied()
        return {
            # Everyone, everywhere: `building_id` is each person's current location (or None).
            "profiles": [
                {
                    **p.as_dict(),
                    "occupied": p.id in occupied,
                    "building_id": stored.building_id
                    if (stored := self.positions.get(p.id))
                    else None,
                }
                for p in self.profiles.values()
            ],
            "lease_seconds": self.leases.lease_seconds,
        }

    def _session_view(self, session: ClientSession) -> JSON:
        stored = self.positions.get(session.profile_id)
        return {
            "token": session.token,
            "profile": self.profiles[session.profile_id].as_dict(),
            "generation": session.generation,
            "lease_seconds": self.leases.lease_seconds,
            "expires_in": round(self.leases.seconds_left(session), 3),
            "presence": None if stored is None else self._presence(stored, online=True),
        }

    def claim(self, profile_id: Any, token: str | None) -> JSON:
        if not isinstance(profile_id, str) or profile_id not in self.profiles:
            raise PresenceError(
                "unknown_profile", "No such person", 404, {"profile_id": profile_id}
            )
        try:
            session = self.leases.claim(profile_id, token)
        except ProfileOccupiedError:
            raise PresenceError(
                "profile_occupied",
                f"{self.profiles[profile_id].name} is in use on another device",
                409,
                {"profile_id": profile_id},
            ) from None
        self._changed(self.building_of(profile_id))  # now online there
        return self._session_view(session)

    def session(self, token: str | None) -> ClientSession:
        try:
            return self.leases.current(token)
        except SessionExpiredError:
            raise PresenceError(
                "session_expired", "Choose your person again: this session has ended", 401
            ) from None

    def heartbeat(self, token: str | None) -> JSON:
        try:
            session = self.leases.heartbeat(token)
        except SessionExpiredError:
            raise PresenceError(
                "session_expired", "Choose your person again: this session has ended", 401
            ) from None
        return {
            "profile_id": session.profile_id,
            "generation": session.generation,
            "expires_in": round(self.leases.seconds_left(session), 3),
        }

    def release(self, token: str | None) -> None:
        session = self.leases.release(token)
        if session is not None:
            self._changed(self.building_of(session.profile_id))  # now offline there

    def current(self, token: str | None) -> JSON:
        return self._session_view(self.session(token))

    # ------------------------------------------------------------ positions

    def seed(self, open_building: Callable[[str], str]) -> list[str]:
        """Place every configured person who never had a position at their `start`.

        `open_building(dataset)` loads the dataset (idempotently) and returns its building id.
        A person who cleared their position is not placed again. Returns the seeded ids.
        """
        seeded = []
        for profile in self.profiles.values():
            start = profile.start
            if start is None or self.positions.has_record(profile.id):
                continue
            try:
                building_id = open_building(start["dataset"])
                place = self.places.place(building_id, start["endpoint"])
            except (LookupError, ValueError) as error:  # NavigationError is a ValueError
                log.warning("Not placing %s at start: %s", profile.id, error)
                continue
            position = {
                **place,
                "label": start.get("label"),
                "heading": None,
                "source": "manual",
                "moving": False,
            }
            self.positions.put(profile.id, building_id, position)
            seeded.append(profile.id)
        return seeded

    def set_position(self, token: str | None, body: Any) -> JSON:
        """Make `building_id` the person's current location, at a validated place there.

        Without `endpoint` the person just enters the building (opening it): they keep their
        place if they were already there, else they are there without a map position. A person
        is only ever in one building, so this removes them from the previous one.
        """
        session = self.session(token)
        if not isinstance(body, dict):
            raise PresenceError("invalid_input", "The request must be an object", 422)
        building_id = body.get("building_id")
        label = body.get("label")
        heading = body.get("heading")
        source = body.get("source", "manual")
        moving = body.get("moving", False)
        if label is not None and (not isinstance(label, str) or len(label) > MAX_LABEL):
            raise PresenceError("invalid_input", "label must be a short text", 422)
        if heading is not None and (
            isinstance(heading, bool)
            or not isinstance(heading, int | float)
            or not math.isfinite(heading)
        ):
            raise PresenceError("invalid_input", "heading must be a finite number", 422)
        if source not in SOURCES:
            raise PresenceError("invalid_input", "source must be one of " + ", ".join(SOURCES), 422)
        if not isinstance(moving, bool):
            raise PresenceError("invalid_input", "moving must be a boolean", 422)
        if body.get("endpoint") is None:
            if not self.places.has_building(building_id):
                raise PresenceError(
                    "unknown_building",
                    "This building is not loaded",
                    404,
                    {"building_id": building_id},
                )
            stored = self.positions.get(session.profile_id)
            if stored is not None and stored.building_id == building_id:
                return self._presence(stored, online=True)
            left = stored.building_id if stored else None
            stored = self.positions.put(session.profile_id, building_id, None)  # type: ignore[arg-type]
            self._changed(left, building_id)
            return self._presence(stored, online=True, checked=True)
        before = self.positions.get(session.profile_id)
        try:
            # Someone walking passes through doorways: allow their point to be near the room.
            tolerance = WALKING_TOLERANCE_M if moving else PLACE_TOLERANCE_M
            place = self.places.place(building_id, body.get("endpoint"), tolerance)  # type: ignore[arg-type]
        except NavigationError as error:
            status = 404 if error.code == "unknown_building" else 422
            raise PresenceError(error.code, error.message, status, error.details) from None
        position = {
            **place,
            "label": label,
            "heading": None if heading is None else float(heading),
            "source": source,
            "moving": moving,
        }
        stored = self.positions.put(session.profile_id, building_id, position)  # type: ignore[arg-type]
        self._changed(before.building_id if before else None, building_id)
        return self._presence(stored, online=True, checked=True)

    def clear_position(self, token: str | None) -> JSON:
        """Remove the person's map position; they stay in their current building."""
        session = self.session(token)
        stored = self.positions.clear(session.profile_id)
        self._changed(stored.building_id if stored else None)
        return {
            "profile_id": session.profile_id,
            "presence": None if stored is None else self._presence(stored, online=True),
        }

    def located(self, building_id: str) -> list[JSON]:
        """People with a usable place in this building, as places (search, routing, graph)."""
        rows = []
        for user in self.building_users(building_id)["users"]:
            at = user["position"]
            if at is None or not user["available"]:
                continue
            profile = user["profile"]
            rows.append(
                {
                    "id": profile["id"],
                    "name": profile["name"],
                    "color": profile["color"],
                    "status": user["status"],
                    "endpoint": at["endpoint"],
                    "floor_id": at["floor_id"],
                    "space_id": at["space_id"],
                    "position": at["position"],
                    "label": at.get("label"),
                    "moving": at.get("moving", False),
                    "revision": user["revision"],
                    "updated_at": user["updated_at"],
                }
            )
        return rows

    def locate(self, building_id: str, user_id: Any) -> JSON:
        """Where a person is, as a route endpoint; NavigationError when they can't be reached."""
        profile = self.profiles.get(user_id) if isinstance(user_id, str) else None
        if profile is None:
            raise NavigationError(
                "unknown_place", "There is no person with that ID", {"id": user_id}
            )
        here = next((p for p in self.located(building_id) if p["id"] == user_id), None)
        if here is not None:
            return here
        stored = self.positions.get(profile.id)
        if stored is None or stored.building_id != building_id:
            raise NavigationError(
                "person_elsewhere",
                f"{profile.name} is not in this building",
                {"id": user_id, "building_id": stored.building_id if stored else None},
            )
        raise NavigationError(
            "person_unplaced",
            f"{profile.name} is in this building but has not set where they are",
            {"id": user_id},
        )

    def building_users(self, building_id: str) -> JSON:
        """The people whose current location is this building (online and offline), with their
        map position when they have one, timestamps and revisions."""
        occupied = self.leases.occupied()
        users = [
            self._presence(stored, online=stored.profile_id in occupied)
            for stored in self.positions.in_building(building_id)
            if stored.profile_id in self.profiles
        ]
        return {"building_id": building_id, "users": users}

    def _presence(self, stored: StoredPosition, *, online: bool, checked: bool = False) -> JSON:
        """A presence as served; the position is revalidated when the building changed since it
        was saved. `position` is None for someone in the building without a map spot."""
        position = None if stored.position is None else dict(stored.position)
        available, reason = True, None
        if position is not None and not checked:
            try:
                place = self.places.place(stored.building_id, position.get("endpoint"))
            except NavigationError as error:
                available, reason = False, error.message
            else:
                if place["building_version"] != position.get("building_version"):
                    # Same IDs, new building version: take the place as it is now (an asset may
                    # have moved). A point that left its room fails above instead.
                    position.update(place)
        return {
            "profile": self.profiles[stored.profile_id].as_dict(),
            "building_id": stored.building_id,
            "status": "online" if online else "offline",
            "available": available,
            "unavailable_reason": reason,
            "position": position,
            "revision": stored.revision,
            "updated_at": stored.updated_at,
        }
