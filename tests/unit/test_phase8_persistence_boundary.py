from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from parking_ai.agents import EvidenceExtractionResult
from parking_ai.evidence import persist_approved_evidence


def test_raw_extraction_result_cannot_cross_persistence_boundary() -> None:
    session = MagicMock(spec=Session)
    quarantined = EvidenceExtractionResult(
        result_id="extract-quarantined",
        service_kind="REGULATION",
        disposition="QUARANTINED",
        extractor_version="fixture-v1",
        review_reasons=["INVALID_EXTRACTOR_OUTPUT"],
        validation_errors=["claims.0:missing"],
    )

    with pytest.raises(TypeError, match="ApprovedEvidenceBundle"):
        persist_approved_evidence(session, quarantined)  # type: ignore[arg-type]
    session.execute.assert_not_called()
