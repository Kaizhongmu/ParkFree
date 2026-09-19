from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GeoPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class LineStringGeometry(BaseModel):
    """Provider-independent GeoJSON-compatible LineString."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["LineString"] = "LineString"
    coordinates: list[tuple[float, float]] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_coordinates(self) -> "LineStringGeometry":
        for longitude, latitude in self.coordinates:
            if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                raise ValueError("LineString coordinates must be valid longitude/latitude pairs")
        return self
