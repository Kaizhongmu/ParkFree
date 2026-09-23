from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from alembic import command

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_BEFORE_BINDING = "0003_v1a_evidence_review_queue"
MIGRATION_WITH_BINDING = "0004_rule_provenance_binding"
CONSTRAINT_NAME = "fk_parking_rules_evidence_segment_binding"


def _alembic_config(database_url: str) -> Config:
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _insert_segment(connection: Connection, segment_id: str, longitude: float) -> None:
    connection.execute(
        text(
            """
            INSERT INTO street_segments (
                segment_id, geometry, street_name, side, length_m, road_type,
                physical_state, legal_state, free_state, legal_confidence,
                data_freshness
            ) VALUES (
                :segment_id,
                ST_GeomFromText(:geometry, 4326),
                'Provenance Test Street', 'LEFT', 50.0, 'residential',
                'UNKNOWN', 'UNKNOWN', 'UNKNOWN', 0.0, now()
            )
            """
        ),
        {
            "segment_id": segment_id,
            "geometry": f"LINESTRING({longitude} 32.842, {longitude + 0.0001} 32.843)",
        },
    )


def _insert_evidence(connection: Connection, evidence_id: str) -> None:
    connection.execute(
        text(
            """
            INSERT INTO parking_sources (
                evidence_id, source_type, source_uri_or_identifier, retrieved_at,
                raw_storage_policy, normalized_claims, reliability_tier
            ) VALUES (
                :evidence_id, 'OFFICIAL_CODE', :identifier, now(),
                'REFERENCE_ONLY', '[]'::jsonb, 'A'
            )
            """
        ),
        {
            "evidence_id": evidence_id,
            "identifier": f"fixture://provenance/{evidence_id}",
        },
    )


def _insert_rule(
    connection: Connection,
    *,
    rule_id: str,
    evidence_id: str,
    segment_id: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO parking_rules (
                rule_id, segment_id, rule_type, days, exceptions,
                source_evidence_id, extraction_confidence
            ) VALUES (
                :rule_id, :segment_id, 'NO_PARKING', '[]'::jsonb, '[]'::jsonb,
                :evidence_id, 1.0
            )
            """
        ),
        {
            "rule_id": rule_id,
            "evidence_id": evidence_id,
            "segment_id": segment_id,
        },
    )


def test_rule_provenance_constraint_rejects_invalid_insert_and_update(engine: Engine) -> None:
    evidence_id = "provenance-direct-evidence"
    bound_segment_id = "provenance-direct-bound"
    unbound_segment_id = "provenance-direct-unbound"
    valid_rule_id = "provenance-direct-valid-rule"

    try:
        with engine.begin() as connection:
            _insert_segment(connection, bound_segment_id, -96.784)
            _insert_segment(connection, unbound_segment_id, -96.783)
            _insert_evidence(connection, evidence_id)

            # The constraint is initially deferred, so publication may stage the rule
            # before its matching evidence/segment binding in the same transaction.
            _insert_rule(
                connection,
                rule_id=valid_rule_id,
                evidence_id=evidence_id,
                segment_id=bound_segment_id,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO parking_source_segments (evidence_id, segment_id)
                    VALUES (:evidence_id, :segment_id)
                    """
                ),
                {"evidence_id": evidence_id, "segment_id": bound_segment_id},
            )

        with engine.connect() as connection:
            transaction = connection.begin()
            _insert_rule(
                connection,
                rule_id="provenance-direct-invalid-rule",
                evidence_id=evidence_id,
                segment_id=unbound_segment_id,
            )
            with pytest.raises(IntegrityError) as captured:
                transaction.commit()
            assert captured.value.orig.sqlstate == "23503"

        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text(
                    """
                    UPDATE parking_rules
                    SET segment_id = :unbound_segment_id
                    WHERE rule_id = :rule_id
                    """
                ),
                {
                    "unbound_segment_id": unbound_segment_id,
                    "rule_id": valid_rule_id,
                },
            )
            with pytest.raises(IntegrityError) as captured:
                transaction.commit()
            assert captured.value.orig.sqlstate == "23503"
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM parking_rules WHERE source_evidence_id = :evidence_id"),
                {"evidence_id": evidence_id},
            )
            connection.execute(
                text("DELETE FROM parking_sources WHERE evidence_id = :evidence_id"),
                {"evidence_id": evidence_id},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM street_segments
                    WHERE segment_id IN (:bound_segment_id, :unbound_segment_id)
                    """
                ),
                {
                    "bound_segment_id": bound_segment_id,
                    "unbound_segment_id": unbound_segment_id,
                },
            )


def test_migration_preflight_rejects_legacy_orphan_without_mutation(
    engine: Engine,
    database_url: str,
) -> None:
    config = _alembic_config(database_url)
    evidence_id = "provenance-preflight-evidence"
    segment_id = "provenance-preflight-segment"
    rule_id = "provenance-preflight-rule"

    command.downgrade(config, MIGRATION_BEFORE_BINDING)
    try:
        with engine.begin() as connection:
            _insert_segment(connection, segment_id, -96.782)
            _insert_evidence(connection, evidence_id)
            _insert_rule(
                connection,
                rule_id=rule_id,
                evidence_id=evidence_id,
                segment_id=segment_id,
            )

        with pytest.raises(IntegrityError) as captured:
            command.upgrade(config, MIGRATION_WITH_BINDING)
        assert captured.value.orig.sqlstate == "23503"

        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == MIGRATION_BEFORE_BINDING
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM parking_rules WHERE rule_id = :rule_id"),
                    {"rule_id": rule_id},
                )
                == 1
            )
        assert CONSTRAINT_NAME not in {
            foreign_key["name"] for foreign_key in inspect(engine).get_foreign_keys("parking_rules")
        }
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM parking_rules WHERE rule_id = :rule_id"),
                {"rule_id": rule_id},
            )
            connection.execute(
                text("DELETE FROM parking_sources WHERE evidence_id = :evidence_id"),
                {"evidence_id": evidence_id},
            )
            connection.execute(
                text("DELETE FROM street_segments WHERE segment_id = :segment_id"),
                {"segment_id": segment_id},
            )
        command.upgrade(config, MIGRATION_WITH_BINDING)
