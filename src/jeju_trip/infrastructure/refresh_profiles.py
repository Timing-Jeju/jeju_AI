"""승인 source contract에 연결된 bounded refresh profile을 검증한다."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jeju_trip.infrastructure.source_catalog import SourceCatalog


class RefreshProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    source_id: str
    endpoint: str
    rows_per_page: int = Field(gt=0, le=5000)
    normalizer: Literal[
        "tour_places",
        "tago_routes",
        "holidays",
        "tago_stops",
        "tago_jeju_stops",
        "tour_intro_opening_rules",
        "tago_route_stops",
    ]
    publisher: Literal["places", "routes", "route_stops", "holidays", "bus_stops", "opening_hours"]
    required_parameters: tuple[str, ...] = ()
    snapshot_mode: Literal["complete", "aggregate"] = "complete"
    query: dict[str, str] = Field(default_factory=dict)

    def materialize_query(self, parameters: dict[str, str]) -> dict[str, str]:
        missing = tuple(
            name
            for name in self.required_parameters
            if not (parameters.get(name) or self.query.get(name))
        )
        if missing:
            raise ValueError(f"REFRESH_PARAMETERS_MISSING:{','.join(missing)}")
        unknown = set(parameters) - set(self.required_parameters)
        if unknown:
            raise ValueError(f"REFRESH_PARAMETERS_UNKNOWN:{','.join(sorted(unknown))}")
        return {**self.query, **parameters}


class RefreshProfileCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_version: Literal["1"]
    profiles: tuple[RefreshProfile, ...]

    @classmethod
    def load(cls, path: Path, source_catalog: SourceCatalog) -> RefreshProfileCatalog:
        with path.open("rb") as stream:
            catalog = cls.model_validate(tomllib.load(stream))
        if len({profile.name for profile in catalog.profiles}) != len(catalog.profiles):
            raise ValueError("REFRESH_PROFILE_NAME_DUPLICATED")
        approved = {
            source.id for source in source_catalog.sources if source.license.status == "APPROVED"
        }
        unknown = {profile.source_id for profile in catalog.profiles} - approved
        if unknown:
            raise ValueError(f"REFRESH_PROFILE_SOURCE_NOT_APPROVED:{','.join(sorted(unknown))}")
        return catalog

    def require(self, name: str) -> RefreshProfile:
        try:
            return next(profile for profile in self.profiles if profile.name == name)
        except StopIteration as error:
            raise ValueError(f"REFRESH_PROFILE_UNKNOWN:{name}") from error
