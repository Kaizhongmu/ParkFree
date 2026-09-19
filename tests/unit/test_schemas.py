from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from parking_ai.domain import (
    AvailabilityPrediction,
    Evidence,
    EvidenceReliabilityTier,
    EvidenceSourceType,
    EvidenceStoragePolicy,
    FreeState,
    LegalState,
    LineStringGeometry,
    ParkingSegment,
    PhysicalState,
    SegmentSide,
)

NOW = datetime(2026, 9, 19, 12, tzinfo=UTC)


def valid_segment_data() -> dict[str, object]:
    return {
        "segment_id": "segment-1",
        "geometry": {
            "type": "LineString",
            "coordinates": [[-96.784, 32.842], [-96.783, 32.843]],
        },
        "street_name": "University Boulevard",
        "side": "LEFT",
        "length_m": 42.5,
        "estimated_capacity": 5,
        "road_type": "residential",
        "physical_state": "PARKABLE",
        "legal_state": "UNKNOWN",
        "free_state": "UNKNOWN",
        "legal_confidence": 0.0,
        "data_freshness": NOW,
    }


def test_valid_parking_segment() -> None:
    segment = ParkingSegment.model_validate(valid_segment_data())

    assert segment.segment_id == "segment-1"
    assert segment.side is SegmentSide.LEFT
    assert segment.physical_state is PhysicalState.PARKABLE
    assert segment.legal_state is LegalState.UNKNOWN
    assert segment.free_state is FreeState.UNKNOWN
    assert isinstance(segment.geometry, LineStringGeometry)


@pytest.mark.parametrize("probability", [-0.01, 1.01])
def test_availability_probability_must_be_between_zero_and_one(probability: float) -> None:
    with pytest.raises(ValidationError):
        AvailabilityPrediction(
            segment_id="segment-1",
            probability=probability,
            model_version="test-v0",
            predicted_at=NOW,
        )


@pytest.mark.parametrize(
    ("probability", "interval"),
    [(0.5, (0.7, 0.4)), (0.8, (0.2, 0.7)), (0.5, (-0.1, 0.8)), (0.5, (0.2, 1.1))],
)
def test_invalid_uncertainty_interval(probability: float, interval: tuple[float, float]) -> None:
    with pytest.raises(ValidationError):
        AvailabilityPrediction(
            segment_id="segment-1",
            probability=probability,
            interval=interval,
            model_version="test-v0",
            predicted_at=NOW,
        )


def test_enum_validation_rejects_unknown_value() -> None:
    data = valid_segment_data()
    data["side"] = "CURBSIDE"

    with pytest.raises(ValidationError):
        ParkingSegment.model_validate(data)


def test_timezone_aware_datetime_is_required() -> None:
    data = valid_segment_data()
    data["data_freshness"] = datetime(2026, 9, 19, 12)

    with pytest.raises(ValidationError, match="timezone"):
        ParkingSegment.model_validate(data)


def test_evidence_tier_and_storage_policy_validation() -> None:
    evidence = Evidence(
        evidence_id="evidence-1",
        source_type=EvidenceSourceType.OFFICIAL_CODE,
        source_uri_or_identifier="city-code-section-1",
        retrieved_at=NOW,
        raw_storage_policy=EvidenceStoragePolicy.REFERENCE_ONLY,
        reliability_tier=EvidenceReliabilityTier.A,
    )

    assert evidence.reliability_tier is EvidenceReliabilityTier.A
    assert evidence.raw_storage_policy is EvidenceStoragePolicy.REFERENCE_ONLY

    invalid_data = evidence.model_dump()
    invalid_data["reliability_tier"] = "E"
    invalid_data["raw_storage_policy"] = "CACHE_FOREVER"
    with pytest.raises(ValidationError) as exc_info:
        Evidence.model_validate(invalid_data)
    assert exc_info.value.error_count() == 2
