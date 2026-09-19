import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from parking_ai.domain import Destination, DestinationAccessPoint, GeoPoint
from parking_ai.gis.adapters import load_osm_fixture
from parking_ai.gis.models import OSMFixture

DATA_DIRECTORY = Path(__file__).with_name("data")


class _RawAccessPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access_point_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class _RawDestination(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    destination_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    destination_type: str = Field(min_length=1, max_length=64)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    access_points: tuple[_RawAccessPoint, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_access_point_ids(self) -> "_RawDestination":
        access_point_ids = [point.access_point_id for point in self.access_points]
        if len(set(access_point_ids)) != len(access_point_ids):
            raise ValueError("destination fixture access-point IDs must be unique")
        return self


class SMUGISFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    destination: Destination
    osm: OSMFixture


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("fixture root must be a JSON object")
    return value


def load_smu_destination(
    path: Path = DATA_DIRECTORY / "smu_fondren_destination_v1.json",
) -> Destination:
    raw = _RawDestination.model_validate(_load_json_object(path))
    access_points = [
        DestinationAccessPoint(
            access_point_id=point.access_point_id,
            destination_id=raw.destination_id,
            name=point.name,
            location=GeoPoint(latitude=point.latitude, longitude=point.longitude),
        )
        for point in raw.access_points
    ]
    return Destination(
        destination_id=raw.destination_id,
        name=raw.name,
        location=GeoPoint(latitude=raw.latitude, longitude=raw.longitude),
        destination_type=raw.destination_type,
        access_points=access_points,
    )


def load_smu_gis_fixture(
    destination_path: Path = DATA_DIRECTORY / "smu_fondren_destination_v1.json",
    osm_path: Path = DATA_DIRECTORY / "smu_osm_fixture_v1.json",
) -> SMUGISFixture:
    return SMUGISFixture(
        destination=load_smu_destination(destination_path),
        osm=load_osm_fixture(osm_path),
    )
