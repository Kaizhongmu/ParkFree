from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from parking_ai.database.models import SearchSessionModel
from parking_ai.orchestrator.persistence import SQLAlchemySearchSessionRepository
from parking_ai.orchestrator.search import IdempotencyConflictError


def test_phase6_metadata_has_replay_snapshots_and_uniqueness() -> None:
    table = SearchSessionModel.__table__
    expected_columns = {
        "idempotency_key_hash",
        "request_hash",
        "replayable",
        "snapshot_schema_version",
        "request_snapshot",
        "candidate_decisions_snapshot",
        "route_matrix_snapshot",
        "optimizer_snapshot",
        "route_id",
        "route_snapshot",
        "response_snapshot",
        "artifact_hash",
        "route_matrix_provider_version",
    }

    assert expected_columns <= set(table.c.keys())
    assert table.c.idempotency_key_hash.nullable is True
    assert table.c.route_id.nullable is True
    assert table.c.replayable.nullable is False
    constraint_names = {constraint.name for constraint in table.constraints}
    assert "uq_search_sessions_idempotency_key_hash" in constraint_names
    assert "uq_search_sessions_route_id" in constraint_names
    assert "ck_search_sessions_replayable_snapshot_complete" in constraint_names


def test_phase6_route_step_metadata_keeps_legacy_rows_compatible() -> None:
    table = SearchSessionModel.metadata.tables["search_route_steps"]

    assert table.c.legality_evaluation_id.nullable is True
    assert table.c.availability_prediction_id.nullable is True
    assert table.c.availability_target_window_seconds.nullable is True
    assert "ck_search_route_steps_availability_target_window_positive" in {
        constraint.name for constraint in table.constraints
    }


def test_repository_returns_none_for_unseen_idempotency_hash() -> None:
    session = MagicMock(spec=Session)
    session.scalar.return_value = None
    repository = SQLAlchemySearchSessionRepository(session)

    assert repository.get_replay("a" * 64, "b" * 64) is None


def test_repository_rejects_idempotency_key_reuse_for_different_request() -> None:
    session = MagicMock(spec=Session)
    session.scalar.return_value = SearchSessionModel(
        session_id="stored-session",
        request_hash="b" * 64,
        idempotency_key_hash="a" * 64,
    )
    repository = SQLAlchemySearchSessionRepository(session)

    with pytest.raises(IdempotencyConflictError, match="different request"):
        repository.get_replay("a" * 64, "c" * 64)


@pytest.mark.parametrize("invalid_hash", ["a" * 63, "A" * 64, "g" * 64])
def test_repository_rejects_noncanonical_hashes(invalid_hash: str) -> None:
    repository = SQLAlchemySearchSessionRepository(MagicMock(spec=Session))

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        repository.get_replay(invalid_hash, "b" * 64)
