import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from parking_ai.domain import (
    Evidence,
    EvidenceReliabilityTier,
    EvidenceSourceType,
    EvidenceStoragePolicy,
    LineStringGeometry,
    PhysicalState,
    SegmentSide,
)
from parking_ai.gis.models import OSMFixture, RoadFeature, SideReferenceDirection


class _RawNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    fixture_boundary: bool = False


class _RawWay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    way_id: str = Field(min_length=1)
    node_refs: tuple[str, ...] = Field(min_length=2)
    tags: dict[str, str]
    candidate_sides: tuple[SegmentSide, ...] = (
        SegmentSide.LEFT,
        SegmentSide.RIGHT,
    )
    parking_candidate: bool | None = None
    physical_state: PhysicalState = PhysicalState.UNKNOWN

    @model_validator(mode="after")
    def validate_tags(self) -> "_RawWay":
        if not self.tags.get("highway"):
            raise ValueError("OSM fixture way requires a highway tag")
        return self


class _RawFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    observed_at: datetime
    source_uri: str = Field(min_length=1)
    license: str = Field(min_length=1)
    attribution: str = Field(min_length=1)
    nodes: tuple[_RawNode, ...]
    ways: tuple[_RawWay, ...]

    @model_validator(mode="after")
    def validate_unique_way_ids(self) -> "_RawFixture":
        way_ids = [way.way_id for way in self.ways]
        if len(set(way_ids)) != len(way_ids):
            raise ValueError("OSM fixture way IDs must be unique")
        return self


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw_bytes = path.read_bytes()
    value = json.loads(raw_bytes)
    if not isinstance(value, dict):
        raise ValueError("OSM fixture root must be a JSON object")
    return value, raw_bytes


def load_osm_fixture(path: Path) -> OSMFixture:
    """Load a local OSM-style fixture and hide its node/way representation."""

    value, raw_bytes = _read_json(path)
    raw = _RawFixture.model_validate(value)
    nodes = {node.node_id: node for node in raw.nodes}
    if len(nodes) != len(raw.nodes):
        raise ValueError("OSM fixture node IDs must be unique")

    reference_counts = Counter(node_ref for way in raw.ways for node_ref in way.node_refs)
    roads: list[RoadFeature] = []
    for way in raw.ways:
        try:
            way_nodes = [nodes[node_ref] for node_ref in way.node_refs]
        except KeyError as error:
            raise ValueError(f"OSM fixture way references missing node {error.args[0]}") from error

        break_indexes = frozenset(
            index
            for index, node in enumerate(way_nodes)
            if index in {0, len(way_nodes) - 1}
            or node.fixture_boundary
            or reference_counts[node.node_id] > 1
        )
        roads.append(
            RoadFeature(
                feature_id=way.way_id,
                geometry=LineStringGeometry(
                    coordinates=[(node.longitude, node.latitude) for node in way_nodes]
                ),
                street_name=way.tags.get("name"),
                road_type=way.tags["highway"],
                break_indexes=break_indexes,
                candidate_sides=way.candidate_sides,
                side_reference_direction=SideReferenceDirection.SOURCE_GEOMETRY,
                parking_candidate=way.parking_candidate,
                physical_state=way.physical_state,
            )
        )

    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    evidence = Evidence(
        evidence_id="ev_smu_osm_fixture_v1",
        source_type=EvidenceSourceType.OSM,
        source_uri_or_identifier=raw.source_uri,
        publisher="OpenStreetMap contributors",
        observed_at=raw.observed_at,
        retrieved_at=raw.observed_at,
        raw_storage_policy=EvidenceStoragePolicy.PERSIST,
        normalized_claims=[],
        reliability_tier=EvidenceReliabilityTier.B,
        extractor_version="osm-fixture-adapter-v1",
        content_hash=content_hash,
    )
    return OSMFixture(
        schema_version=raw.schema_version,
        observed_at=raw.observed_at,
        source_uri=raw.source_uri,
        license=raw.license,
        attribution=raw.attribution,
        evidence=evidence,
        roads=tuple(roads),
    )
