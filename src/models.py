"""Data models for the Disneyland Reservation Bloodhound."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Park(Enum):
    DISNEYLAND = "disneyland"
    CALIFORNIA_ADVENTURE = "california_adventure"
    EITHER = "either"

    @classmethod
    def from_str(cls, value: str) -> "Park":
        return cls(value.lower().strip())


class AvailabilityStatus(Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    ERROR = "error"


@dataclass
class AvailabilityResult:
    date: str
    park: Park
    status: AvailabilityStatus
    checked_at: datetime = field(default_factory=datetime.now)
    source: str = "unknown"  # "api" or "browser"
    message: str = ""

    @property
    def is_available(self) -> bool:
        return self.status == AvailabilityStatus.AVAILABLE


@dataclass
class TokenInfo:
    access_token: str
    captured_at: datetime = field(default_factory=datetime.now)
    expires_in_seconds: int = 900  # 15 min default

    @property
    def is_expired(self) -> bool:
        elapsed = (datetime.now() - self.captured_at).total_seconds()
        return elapsed >= self.expires_in_seconds

    def age_minutes(self) -> float:
        return (datetime.now() - self.captured_at).total_seconds() / 60


@dataclass
class BookingTarget:
    date: str  # YYYY-MM-DD
    park: Park
    party_size: int

    def __str__(self) -> str:
        park_name = self.park.value.replace("_", " ").title()
        return f"{park_name} on {self.date} for {self.party_size} guest(s)"
