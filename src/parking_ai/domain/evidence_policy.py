"""Shared, provider-independent integrity policy for evidence metadata."""

from collections.abc import Mapping
from types import MappingProxyType

from parking_ai.domain.enums import EvidenceReliabilityTier, EvidenceSourceType

EVIDENCE_SOURCE_AUTHORITY_CEILING: Mapping[EvidenceSourceType, EvidenceReliabilityTier] = (
    MappingProxyType(
        {
            EvidenceSourceType.OFFICIAL_CODE: EvidenceReliabilityTier.A,
            EvidenceSourceType.OFFICIAL_GIS: EvidenceReliabilityTier.A,
            EvidenceSourceType.VERIFIED_SIGN: EvidenceReliabilityTier.A,
            EvidenceSourceType.UNIVERSITY: EvidenceReliabilityTier.B,
            EvidenceSourceType.OSM: EvidenceReliabilityTier.B,
            EvidenceSourceType.COMMUNITY: EvidenceReliabilityTier.C,
            EvidenceSourceType.WEB: EvidenceReliabilityTier.C,
            EvidenceSourceType.IMAGERY_INFERENCE: EvidenceReliabilityTier.D,
        }
    )
)

_RELIABILITY_RANK: Mapping[EvidenceReliabilityTier, int] = MappingProxyType(
    {
        EvidenceReliabilityTier.A: 0,
        EvidenceReliabilityTier.B: 1,
        EvidenceReliabilityTier.C: 2,
        EvidenceReliabilityTier.D: 3,
    }
)


def evidence_reliability_within_source_authority(
    source_type: EvidenceSourceType,
    reliability_tier: EvidenceReliabilityTier,
) -> bool:
    """Return whether a tier is no stronger than its source type's authority ceiling."""

    ceiling = EVIDENCE_SOURCE_AUTHORITY_CEILING[source_type]
    return _RELIABILITY_RANK[reliability_tier] >= _RELIABILITY_RANK[ceiling]
