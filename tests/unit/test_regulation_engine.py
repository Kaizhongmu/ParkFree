from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from parking_ai.domain import (
    EVIDENCE_SOURCE_AUTHORITY_CEILING,
    DayOfWeek,
    Evidence,
    EvidenceReliabilityTier,
    EvidenceSourceType,
    EvidenceStoragePolicy,
    FreeState,
    LegalityEvaluation,
    LegalState,
    LineStringGeometry,
    ParkingRule,
    ParkingRuleType,
    ParkingSegment,
    RegulationReasonCode,
    UserProfile,
)
from parking_ai.domain.schemas import RuleException
from parking_ai.regulations import DeterministicRegulationEngine

CHICAGO = ZoneInfo("America/Chicago")
MONDAY = datetime(2026, 9, 21, 9, tzinfo=CHICAGO)


def segment() -> ParkingSegment:
    return ParkingSegment(
        segment_id="segment-1",
        geometry=LineStringGeometry(coordinates=[(-96.784, 32.842), (-96.783, 32.843)]),
        length_m=100,
        data_freshness=MONDAY,
    )


def evidence(
    evidence_id: str = "evidence-a",
    tier: EvidenceReliabilityTier | None = None,
    source_type: EvidenceSourceType = EvidenceSourceType.OFFICIAL_CODE,
    published_at: datetime | None = None,
    retrieved_at: datetime = datetime(2026, 1, 1, tzinfo=CHICAGO),
    observed_at: datetime | None = None,
    segment_ids: list[str] | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source_type=source_type,
        source_uri_or_identifier=f"source:{evidence_id}",
        published_at=published_at,
        observed_at=observed_at,
        retrieved_at=retrieved_at,
        raw_storage_policy=EvidenceStoragePolicy.REFERENCE_ONLY,
        reliability_tier=tier or EVIDENCE_SOURCE_AUTHORITY_CEILING[source_type],
        segment_ids=["segment-1"] if segment_ids is None else segment_ids,
    )


def rule(
    rule_type: ParkingRuleType,
    *,
    rule_id: str = "rule-1",
    evidence_id: str = "evidence-a",
    days: list[DayOfWeek] | None = None,
    start: time | None = None,
    end: time | None = None,
    confidence: float = 0.9,
    **kwargs: object,
) -> ParkingRule:
    return ParkingRule(
        rule_id=rule_id,
        segment_id="segment-1",
        rule_type=rule_type,
        days=[] if days is None else days,
        start_time=start,
        end_time=end,
        source_evidence_id=evidence_id,
        extraction_confidence=confidence,
        **kwargs,
    )


def evaluate(
    rules: list[ParkingRule],
    when: datetime = MONDAY,
    *,
    profile: UserProfile | None = None,
    evidence_items: list[Evidence] | None = None,
) -> LegalityEvaluation:
    engine = DeterministicRegulationEngine(rules, evidence_items or [evidence()])
    return engine.evaluate_legality(segment(), profile or UserProfile(), when)


def test_no_rules_remains_unknown_without_fabricated_provenance() -> None:
    result = DeterministicRegulationEngine([], []).evaluate_legality(
        segment(), UserProfile(), MONDAY
    )

    assert (result.legal_state, result.free_state) == (LegalState.UNKNOWN, FreeState.UNKNOWN)
    assert result.evidence_refs == []
    assert result.confidence == 0.0
    assert result.reason_codes == [RegulationReasonCode.INSUFFICIENT_EVIDENCE]


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (datetime(2026, 9, 21, 7, 59, tzinfo=CHICAGO), LegalState.UNKNOWN),
        (datetime(2026, 9, 21, 8, 0, tzinfo=CHICAGO), LegalState.ILLEGAL),
        (datetime(2026, 9, 21, 18, 0, tzinfo=CHICAGO), LegalState.UNKNOWN),
        (datetime(2026, 9, 20, 9, 0, tzinfo=CHICAGO), LegalState.UNKNOWN),
    ],
)
def test_weekday_and_half_open_time_boundaries(when: datetime, expected: LegalState) -> None:
    no_parking = rule(
        ParkingRuleType.NO_PARKING,
        days=[DayOfWeek.MON],
        start=time(8),
        end=time(18),
    )

    assert evaluate([no_parking], when).legal_state is expected


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (datetime(2026, 9, 21, 23, tzinfo=CHICAGO), LegalState.ILLEGAL),
        (datetime(2026, 9, 22, 1, tzinfo=CHICAGO), LegalState.ILLEGAL),
        (datetime(2026, 9, 22, 2, tzinfo=CHICAGO), LegalState.UNKNOWN),
    ],
)
def test_cross_midnight_rule_uses_start_day_as_anchor(when: datetime, expected: LegalState) -> None:
    overnight = rule(
        ParkingRuleType.NO_PARKING,
        days=[DayOfWeek.MON],
        start=time(22),
        end=time(2),
    )

    assert evaluate([overnight], when).legal_state is expected


def test_no_parking_anywhere_in_requested_stay_is_illegal() -> None:
    no_parking = rule(
        ParkingRuleType.NO_PARKING,
        start=time(10),
        end=time(11),
    )

    crosses_into = evaluate(
        [no_parking],
        datetime(2026, 9, 21, 9, 30, tzinfo=CHICAGO),
        profile=UserProfile(requested_parking_duration_min=60),
    )
    crosses_out = evaluate(
        [no_parking],
        datetime(2026, 9, 21, 10, 30, tzinfo=CHICAGO),
        profile=UserProfile(requested_parking_duration_min=60),
    )
    departs_at_start = evaluate(
        [no_parking],
        datetime(2026, 9, 21, 9, 30, tzinfo=CHICAGO),
        profile=UserProfile(requested_parking_duration_min=30),
    )

    assert crosses_into.legal_state is LegalState.ILLEGAL
    assert crosses_out.legal_state is LegalState.ILLEGAL
    assert departs_at_start.legal_state is LegalState.UNKNOWN


def test_overlapping_no_parking_wins_and_time_limit_is_reported() -> None:
    rules = [
        rule(ParkingRuleType.NO_PARKING, rule_id="no-parking"),
        rule(
            ParkingRuleType.TIME_LIMIT,
            rule_id="limit",
            max_duration_min=120,
            payment_required=False,
        ),
    ]

    result = evaluate(
        rules,
        profile=UserProfile(requested_parking_duration_min=30),
    )

    assert result.legal_state is LegalState.ILLEGAL
    assert result.free_state is FreeState.FREE
    assert result.max_duration_min == 120
    assert RegulationReasonCode.ACTIVE_NO_PARKING in result.reason_codes
    assert RegulationReasonCode.TIME_LIMIT_APPLIES in result.reason_codes


@pytest.mark.parametrize(
    ("duration", "expected"),
    [(30, LegalState.LEGAL), (60, LegalState.LEGAL), (61, LegalState.ILLEGAL)],
)
def test_time_limit_uses_requested_duration(duration: int, expected: LegalState) -> None:
    limit = rule(ParkingRuleType.TIME_LIMIT, max_duration_min=60)

    result = evaluate(
        [limit],
        profile=UserProfile(requested_parking_duration_min=duration),
    )

    assert result.legal_state is expected
    assert result.max_duration_min == 60
    assert result.free_state is FreeState.UNKNOWN


def test_time_limit_can_explicitly_establish_free_state() -> None:
    limit = rule(
        ParkingRuleType.TIME_LIMIT,
        max_duration_min=60,
        payment_required=False,
    )

    result = evaluate(
        [limit],
        profile=UserProfile(requested_parking_duration_min=61),
    )

    assert (result.legal_state, result.free_state) == (LegalState.ILLEGAL, FreeState.FREE)
    assert RegulationReasonCode.NO_PAYMENT_REQUIRED in result.reason_codes


def test_positive_rule_covering_only_part_of_stay_does_not_authorize_remainder() -> None:
    limit = rule(
        ParkingRuleType.TIME_LIMIT,
        start=time(9),
        end=time(10),
        max_duration_min=120,
    )

    result = evaluate(
        [limit],
        profile=UserProfile(requested_parking_duration_min=90),
    )

    assert result.legal_state is LegalState.UNKNOWN


def test_permit_only_evaluates_user_permits() -> None:
    permit_only = rule(
        ParkingRuleType.PERMIT_ONLY,
        permit_required=True,
        permit_type="SMU-A",
    )

    denied = evaluate([permit_only], profile=UserProfile(requested_parking_duration_min=30))
    allowed = evaluate(
        [permit_only],
        profile=UserProfile(permit_types=["smu-a"], requested_parking_duration_min=30),
    )

    assert denied.legal_state is LegalState.ILLEGAL
    assert RegulationReasonCode.PERMIT_REQUIRED in denied.reason_codes
    assert allowed.legal_state is LegalState.LEGAL
    assert RegulationReasonCode.VALID_PERMIT_EXCEPTION in allowed.reason_codes


def test_no_parking_accepts_only_allowlisted_explicit_permit_exception() -> None:
    exception = RuleException(exception_type="PERMIT", parameters={"permit_type": "SMU-A"})
    no_parking = rule(ParkingRuleType.NO_PARKING, exceptions=[exception])

    result = evaluate([no_parking], profile=UserProfile(permit_types=["smu-a"]))

    assert result.legal_state is LegalState.UNKNOWN
    assert RegulationReasonCode.VALID_PERMIT_EXCEPTION in result.reason_codes


def test_unspecified_required_permit_type_does_not_accept_an_arbitrary_permit() -> None:
    permit_only = rule(
        ParkingRuleType.PERMIT_ONLY,
        permit_required=True,
        permit_type=None,
    )

    result = evaluate(
        [permit_only],
        profile=UserProfile(permit_types=["UNRELATED"], requested_parking_duration_min=30),
    )

    assert result.legal_state is LegalState.UNKNOWN
    assert RegulationReasonCode.INSUFFICIENT_EVIDENCE in result.reason_codes


def test_no_parking_permit_fields_define_an_explicit_exception() -> None:
    no_parking = rule(
        ParkingRuleType.NO_PARKING,
        permit_required=True,
        permit_type="SMU-A",
    )

    denied = evaluate([no_parking], profile=UserProfile())
    allowed = evaluate([no_parking], profile=UserProfile(permit_types=["SMU-A"]))

    assert denied.legal_state is LegalState.ILLEGAL
    assert allowed.legal_state is LegalState.UNKNOWN


def test_no_parking_exception_is_neutral_when_another_rule_allows_the_stay() -> None:
    rules = [
        rule(
            ParkingRuleType.NO_PARKING,
            rule_id="excepted-prohibition",
            exceptions=[
                RuleException(
                    exception_type="PERMIT",
                    parameters={"permit_type": "SMU-A"},
                )
            ],
        ),
        rule(
            ParkingRuleType.TIME_LIMIT,
            rule_id="positive-limit",
            max_duration_min=120,
        ),
    ]

    result = evaluate(
        rules,
        profile=UserProfile(permit_types=["SMU-A"], requested_parking_duration_min=30),
    )

    assert result.legal_state is LegalState.LEGAL
    assert result.max_duration_min == 120
    assert RegulationReasonCode.VALID_PERMIT_EXCEPTION in result.reason_codes
    assert RegulationReasonCode.CONFLICTING_EVIDENCE not in result.reason_codes


def test_paid_and_explicit_free_windows_remain_separate_from_legality() -> None:
    paid = rule(
        ParkingRuleType.PAID,
        rule_id="paid",
        start=time(8),
        end=time(18),
        payment_required=True,
    )
    explicitly_free = rule(
        ParkingRuleType.PAID,
        rule_id="free",
        start=time(18),
        end=time(8),
        payment_required=False,
    )

    profile = UserProfile(requested_parking_duration_min=30)
    daytime = evaluate([paid, explicitly_free], MONDAY, profile=profile)
    evening = evaluate(
        [paid, explicitly_free],
        datetime(2026, 9, 21, 19, tzinfo=CHICAGO),
        profile=profile,
    )

    assert (daytime.legal_state, daytime.free_state) == (LegalState.LEGAL, FreeState.PAID)
    assert (evening.legal_state, evening.free_state) == (LegalState.LEGAL, FreeState.FREE)
    assert RegulationReasonCode.PAYMENT_REQUIRED in daytime.reason_codes
    assert RegulationReasonCode.NO_PAYMENT_REQUIRED in evening.reason_codes


def test_inactive_paid_window_does_not_imply_free() -> None:
    paid = rule(
        ParkingRuleType.PAID,
        start=time(8),
        end=time(18),
        payment_required=True,
    )

    result = evaluate([paid], datetime(2026, 9, 21, 19, tzinfo=CHICAGO))

    assert (result.legal_state, result.free_state) == (LegalState.UNKNOWN, FreeState.UNKNOWN)


def test_same_tier_payment_conflict_is_axis_local() -> None:
    rules = [
        rule(ParkingRuleType.PAID, rule_id="paid", payment_required=True),
        rule(ParkingRuleType.PAID, rule_id="free", payment_required=False),
    ]

    result = evaluate(rules, profile=UserProfile(requested_parking_duration_min=30))

    assert result.legal_state is LegalState.LEGAL
    assert result.free_state is FreeState.UNKNOWN
    assert result.confidence == 0.0
    assert RegulationReasonCode.CONFLICTING_EVIDENCE in result.reason_codes


def test_same_tier_permit_claims_with_opposite_requirements_are_unknown() -> None:
    rules = [
        rule(
            ParkingRuleType.PERMIT_ONLY,
            rule_id="permit-required",
            permit_required=True,
            permit_type="SMU-A",
        ),
        rule(
            ParkingRuleType.PERMIT_ONLY,
            rule_id="permit-not-required",
            permit_required=False,
        ),
    ]

    result = evaluate(rules, profile=UserProfile(requested_parking_duration_min=30))

    assert result.legal_state is LegalState.UNKNOWN
    assert RegulationReasonCode.CONFLICTING_EVIDENCE in result.reason_codes


def test_higher_tier_evidence_wins_per_axis() -> None:
    rules = [
        rule(
            ParkingRuleType.PAID,
            rule_id="official-free",
            evidence_id="official",
            payment_required=False,
        ),
        rule(
            ParkingRuleType.PAID,
            rule_id="community-paid",
            evidence_id="community",
            payment_required=True,
        ),
    ]
    evidence_items = [
        evidence(
            "official",
            EvidenceReliabilityTier.A,
            EvidenceSourceType.OFFICIAL_CODE,
        ),
        evidence(
            "community",
            EvidenceReliabilityTier.C,
            EvidenceSourceType.COMMUNITY,
            retrieved_at=MONDAY - timedelta(days=1),
        ),
    ]

    result = evaluate(
        rules,
        profile=UserProfile(requested_parking_duration_min=30),
        evidence_items=evidence_items,
    )

    assert (result.legal_state, result.free_state) == (LegalState.LEGAL, FreeState.FREE)
    assert result.evidence_refs == ["official"]
    assert RegulationReasonCode.LOWER_TIER_EVIDENCE_OVERRIDDEN in result.reason_codes


def test_evidence_precedence_is_resolved_per_time_slice() -> None:
    rules = [
        rule(
            ParkingRuleType.TIME_LIMIT,
            rule_id="official-limit",
            evidence_id="official",
            start=time(9),
            end=time(10),
            max_duration_min=180,
        ),
        rule(
            ParkingRuleType.NO_PARKING,
            rule_id="community-prohibition",
            evidence_id="community",
            start=time(10),
            end=time(11),
        ),
    ]
    evidence_items = [
        evidence(
            "official",
            EvidenceReliabilityTier.A,
            EvidenceSourceType.OFFICIAL_CODE,
        ),
        evidence(
            "community",
            EvidenceReliabilityTier.C,
            EvidenceSourceType.COMMUNITY,
            retrieved_at=MONDAY - timedelta(days=1),
        ),
    ]

    result = evaluate(
        rules,
        datetime(2026, 9, 21, 9, 30, tzinfo=CHICAGO),
        profile=UserProfile(requested_parking_duration_min=60),
        evidence_items=evidence_items,
    )

    assert result.legal_state is LegalState.ILLEGAL
    assert result.max_duration_min == 180
    assert result.evidence_refs == ["community", "official"]
    assert RegulationReasonCode.ACTIVE_NO_PARKING in result.reason_codes


def test_effective_date_is_inclusive_and_expired_rule_is_inactive() -> None:
    no_parking = rule(
        ParkingRuleType.NO_PARKING,
        effective_start_date=date(2026, 9, 1),
        effective_end_date=date(2026, 9, 21),
    )

    assert evaluate([no_parking], MONDAY).legal_state is LegalState.ILLEGAL
    assert (
        evaluate([no_parking], datetime(2026, 9, 22, 9, tzinfo=CHICAGO)).legal_state
        is LegalState.UNKNOWN
    )


@pytest.mark.parametrize(
    ("rule_type", "vehicle_type", "expected"),
    [
        (ParkingRuleType.LOADING, "passenger", LegalState.ILLEGAL),
        (ParkingRuleType.LOADING, "delivery", LegalState.ILLEGAL),
        (ParkingRuleType.STREET_CLEANING, "passenger", LegalState.ILLEGAL),
        (ParkingRuleType.EVENT_RESTRICTION, "passenger", LegalState.ILLEGAL),
        (ParkingRuleType.OTHER, "passenger", LegalState.UNKNOWN),
    ],
)
def test_remaining_rule_types_fail_closed(
    rule_type: ParkingRuleType, vehicle_type: str, expected: LegalState
) -> None:
    result = evaluate([rule(rule_type)], profile=UserProfile(vehicle_type=vehicle_type))

    assert result.legal_state is expected


def test_other_rule_can_only_supply_explicit_payment_state() -> None:
    other = rule(ParkingRuleType.OTHER, payment_required=False)

    result = evaluate([other], profile=UserProfile(requested_parking_duration_min=30))

    assert (result.legal_state, result.free_state) == (LegalState.UNKNOWN, FreeState.FREE)


@pytest.mark.parametrize(
    "overrides",
    [
        {"start": time(8), "end": None},
        {"start": time(8), "end": time(8)},
        {"days": [DayOfWeek.MON, DayOfWeek.MON]},
        {"start": time(8, tzinfo=ZoneInfo("UTC")), "end": time(9)},
    ],
)
def test_rule_schedule_validation_rejects_ambiguous_inputs(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        rule(ParkingRuleType.NO_PARKING, **overrides)


def test_naive_datetime_is_rejected() -> None:
    engine = DeterministicRegulationEngine([], [])

    with pytest.raises(ValueError, match="timezone"):
        engine.evaluate_legality(segment(), UserProfile(), datetime(2026, 9, 21, 9))


def test_engine_converts_aware_input_to_configured_local_timezone() -> None:
    limit = rule(
        ParkingRuleType.TIME_LIMIT,
        start=time(8),
        end=time(10),
        max_duration_min=60,
    )

    result = evaluate(
        [limit],
        datetime(2026, 9, 21, 14, tzinfo=ZoneInfo("UTC")),
        profile=UserProfile(requested_parking_duration_min=30),
    )

    assert result.legal_state is LegalState.LEGAL


def test_loading_requires_explicit_structured_vehicle_exception() -> None:
    loading = rule(
        ParkingRuleType.LOADING,
        exceptions=[
            RuleException(
                exception_type="VEHICLE_TYPE",
                parameters={"vehicle_types": ["delivery"]},
            )
        ],
    )

    result = evaluate(
        [loading],
        profile=UserProfile(vehicle_type="delivery", requested_parking_duration_min=30),
    )

    assert result.legal_state is LegalState.LEGAL
    assert RegulationReasonCode.VALID_VEHICLE_EXCEPTION in result.reason_codes


def test_elapsed_duration_across_spring_dst_gap_reaches_later_restriction() -> None:
    sunday_restriction = rule(
        ParkingRuleType.NO_PARKING,
        days=[DayOfWeek.SUN],
        start=time(3),
        end=time(4),
    )
    arrival = datetime(2026, 3, 8, 1, 30, tzinfo=CHICAGO)

    result = evaluate(
        [sunday_restriction],
        arrival,
        profile=UserProfile(requested_parking_duration_min=60),
    )

    assert result.legal_state is LegalState.ILLEGAL


@pytest.mark.parametrize("fold", [0, 1])
def test_both_fall_dst_folds_match_ambiguous_wall_time(fold: int) -> None:
    sunday_restriction = rule(
        ParkingRuleType.NO_PARKING,
        days=[DayOfWeek.SUN],
        start=time(1),
        end=time(2),
    )
    repeated_time = datetime(2026, 11, 1, 1, 30, tzinfo=CHICAGO, fold=fold)

    assert evaluate([sunday_restriction], repeated_time).legal_state is LegalState.ILLEGAL


@pytest.mark.parametrize("fold", [0, 1])
def test_short_rule_inside_repeated_fall_hour_applies_in_each_fold(fold: int) -> None:
    restriction = rule(
        ParkingRuleType.NO_PARKING,
        days=[DayOfWeek.SUN],
        start=time(1, 30),
        end=time(1, 45),
    )
    active_time = datetime(2026, 11, 1, 1, 40, tzinfo=CHICAGO, fold=fold)

    assert evaluate([restriction], active_time).legal_state is LegalState.ILLEGAL


@pytest.mark.parametrize(
    "inactive_time",
    [
        datetime(2026, 11, 1, 1, 50, tzinfo=CHICAGO, fold=0),
        datetime(2026, 11, 1, 1, 10, tzinfo=CHICAGO, fold=1),
    ],
)
def test_short_rule_inside_repeated_fall_hour_does_not_bridge_folds(
    inactive_time: datetime,
) -> None:
    restriction = rule(
        ParkingRuleType.NO_PARKING,
        days=[DayOfWeek.SUN],
        start=time(1, 30),
        end=time(1, 45),
    )

    assert evaluate([restriction], inactive_time).legal_state is LegalState.UNKNOWN


def test_fall_rule_with_only_ambiguous_start_does_not_bridge_to_second_fold() -> None:
    restriction = rule(
        ParkingRuleType.NO_PARKING,
        days=[DayOfWeek.SUN],
        start=time(1, 30),
        end=time(2),
    )
    before_second_start = datetime(2026, 11, 1, 1, 10, tzinfo=CHICAGO, fold=1)

    assert evaluate([restriction], before_second_start).legal_state is LegalState.UNKNOWN


def test_evaluation_is_deterministic_and_versioned() -> None:
    paid = rule(ParkingRuleType.PAID, payment_required=True, confidence=0.83)
    engine = DeterministicRegulationEngine([paid], [evidence()])

    profile = UserProfile(requested_parking_duration_min=30)
    first = engine.evaluate_legality(segment(), profile, MONDAY)
    second = engine.evaluate_legality(segment(), profile, MONDAY)

    assert first == second
    assert first.evaluation_id.startswith("eval_")
    assert first.rule_engine_version == "regulation-engine-v2"
    assert first.confidence == 0.83


def test_evaluation_id_changes_with_material_output_fields() -> None:
    profile = UserProfile(requested_parking_duration_min=30)
    shorter = DeterministicRegulationEngine(
        [rule(ParkingRuleType.TIME_LIMIT, max_duration_min=60, confidence=0.8)],
        [evidence()],
    ).evaluate_legality(segment(), profile, MONDAY)
    longer = DeterministicRegulationEngine(
        [rule(ParkingRuleType.TIME_LIMIT, max_duration_min=120, confidence=0.8)],
        [evidence()],
    ).evaluate_legality(segment(), profile, MONDAY)
    higher_confidence = DeterministicRegulationEngine(
        [rule(ParkingRuleType.TIME_LIMIT, max_duration_min=60, confidence=0.9)],
        [evidence()],
    ).evaluate_legality(segment(), profile, MONDAY)

    assert shorter.evaluation_id != longer.evaluation_id
    assert shorter.evaluation_id != higher_confidence.evaluation_id


def test_stale_non_authoritative_evidence_fails_closed() -> None:
    stale_evidence = evidence(
        source_type=EvidenceSourceType.OSM,
        retrieved_at=MONDAY - timedelta(days=181),
    )
    paid = rule(ParkingRuleType.PAID, payment_required=False)

    result = evaluate(
        [paid],
        profile=UserProfile(requested_parking_duration_min=30),
        evidence_items=[stale_evidence],
    )

    assert (result.legal_state, result.free_state) == (
        LegalState.UNKNOWN,
        FreeState.UNKNOWN,
    )
    assert result.confidence == 0.0
    assert result.evidence_refs == ["evidence-a"]
    assert RegulationReasonCode.STALE_EVIDENCE in result.reason_codes


def test_evidence_exactly_at_default_age_limit_remains_usable() -> None:
    boundary_evidence = evidence(
        source_type=EvidenceSourceType.OSM,
        retrieved_at=MONDAY - timedelta(days=180) + timedelta(minutes=30),
    )
    paid = rule(ParkingRuleType.PAID, payment_required=False)

    result = evaluate(
        [paid],
        profile=UserProfile(requested_parking_duration_min=30),
        evidence_items=[boundary_evidence],
    )

    assert (result.legal_state, result.free_state) == (LegalState.LEGAL, FreeState.FREE)


def test_evidence_must_remain_fresh_through_requested_stay() -> None:
    expires_at_arrival = evidence(
        source_type=EvidenceSourceType.OSM,
        retrieved_at=MONDAY - timedelta(days=180),
    )
    paid = rule(ParkingRuleType.PAID, payment_required=False)

    result = evaluate(
        [paid],
        profile=UserProfile(requested_parking_duration_min=30),
        evidence_items=[expires_at_arrival],
    )

    assert (result.legal_state, result.free_state) == (
        LegalState.UNKNOWN,
        FreeState.UNKNOWN,
    )
    assert RegulationReasonCode.STALE_EVIDENCE in result.reason_codes


def test_observed_at_takes_precedence_over_newer_retrieval_time() -> None:
    stale_observation = evidence(
        source_type=EvidenceSourceType.OSM,
        observed_at=MONDAY - timedelta(days=181),
        retrieved_at=MONDAY,
    )
    paid = rule(ParkingRuleType.PAID, payment_required=False)

    result = evaluate(
        [paid],
        profile=UserProfile(requested_parking_duration_min=30),
        evidence_items=[stale_observation],
    )

    assert (result.legal_state, result.free_state) == (
        LegalState.UNKNOWN,
        FreeState.UNKNOWN,
    )
    assert RegulationReasonCode.STALE_EVIDENCE in result.reason_codes


def test_old_official_code_uses_rule_effective_dates_instead_of_age_expiry() -> None:
    old_code = evidence(retrieved_at=MONDAY - timedelta(days=3650))
    paid = rule(ParkingRuleType.PAID, payment_required=False)

    result = evaluate(
        [paid],
        profile=UserProfile(requested_parking_duration_min=30),
        evidence_items=[old_code],
    )

    assert (result.legal_state, result.free_state) == (LegalState.LEGAL, FreeState.FREE)


def test_injected_evidence_age_policy_overrides_default() -> None:
    older_osm = evidence(
        source_type=EvidenceSourceType.OSM,
        retrieved_at=MONDAY - timedelta(days=181),
    )
    paid = rule(ParkingRuleType.PAID, payment_required=False)
    engine = DeterministicRegulationEngine(
        [paid],
        [older_osm],
        evidence_max_age={EvidenceSourceType.OSM: timedelta(days=365)},
    )

    result = engine.evaluate_legality(
        segment(), UserProfile(requested_parking_duration_min=30), MONDAY
    )

    assert (result.legal_state, result.free_state) == (LegalState.LEGAL, FreeState.FREE)


def test_partial_evidence_age_override_retains_other_source_defaults() -> None:
    old_community = evidence(
        source_type=EvidenceSourceType.COMMUNITY,
        retrieved_at=MONDAY - timedelta(days=365),
    )
    paid = rule(ParkingRuleType.PAID, payment_required=False)
    engine = DeterministicRegulationEngine(
        [paid],
        [old_community],
        evidence_max_age={EvidenceSourceType.OSM: timedelta(days=365)},
    )

    result = engine.evaluate_legality(
        segment(), UserProfile(requested_parking_duration_min=30), MONDAY
    )

    assert (result.legal_state, result.free_state) == (
        LegalState.UNKNOWN,
        FreeState.UNKNOWN,
    )
    assert RegulationReasonCode.STALE_EVIDENCE in result.reason_codes


@pytest.mark.parametrize(
    "future_evidence",
    [
        evidence(retrieved_at=MONDAY + timedelta(minutes=1)),
        evidence(
            source_type=EvidenceSourceType.OSM,
            observed_at=MONDAY + timedelta(minutes=1),
            retrieved_at=MONDAY + timedelta(minutes=2),
        ),
        evidence(
            published_at=MONDAY + timedelta(minutes=1),
            retrieved_at=MONDAY + timedelta(minutes=2),
        ),
        evidence(
            observed_at=MONDAY - timedelta(days=1),
            retrieved_at=MONDAY + timedelta(minutes=1),
        ),
    ],
)
def test_evidence_not_available_at_query_time_fails_closed(
    future_evidence: Evidence,
) -> None:
    paid = rule(ParkingRuleType.PAID, payment_required=False)

    result = evaluate(
        [paid],
        profile=UserProfile(requested_parking_duration_min=30),
        evidence_items=[future_evidence],
    )

    assert (result.legal_state, result.free_state) == (
        LegalState.UNKNOWN,
        FreeState.UNKNOWN,
    )
    assert RegulationReasonCode.EVIDENCE_NOT_YET_AVAILABLE in result.reason_codes


def test_engine_rejects_causally_invalid_evidence_even_if_validation_was_bypassed() -> None:
    invalid = evidence().model_copy(
        update={"published_at": MONDAY, "retrieved_at": MONDAY - timedelta(minutes=1)},
        deep=True,
    )

    with pytest.raises(ValueError, match="timestamps cannot be after retrieval"):
        DeterministicRegulationEngine([], [invalid])


def test_engine_rejects_source_authority_elevation_even_if_validation_was_bypassed() -> None:
    elevated = evidence(
        source_type=EvidenceSourceType.COMMUNITY,
    ).model_copy(update={"reliability_tier": EvidenceReliabilityTier.A}, deep=True)

    with pytest.raises(ValueError, match="exceeds its source authority ceiling"):
        DeterministicRegulationEngine([], [elevated])


def test_evaluation_is_independent_of_injected_rule_and_evidence_order() -> None:
    rules = [
        rule(
            ParkingRuleType.TIME_LIMIT,
            rule_id="limit",
            evidence_id="limit-evidence",
            max_duration_min=120,
        ),
        rule(
            ParkingRuleType.PAID,
            rule_id="paid",
            evidence_id="paid-evidence",
            payment_required=True,
        ),
    ]
    evidence_items = [evidence("limit-evidence"), evidence("paid-evidence")]
    profile = UserProfile(requested_parking_duration_min=30)

    forward = DeterministicRegulationEngine(rules, evidence_items).evaluate_legality(
        segment(), profile, MONDAY
    )
    reversed_input = DeterministicRegulationEngine(
        list(reversed(rules)), list(reversed(evidence_items))
    ).evaluate_legality(segment(), profile, MONDAY)

    assert forward == reversed_input


def test_constructor_rejects_missing_provenance() -> None:
    with pytest.raises(ValueError, match="missing evidence"):
        DeterministicRegulationEngine([rule(ParkingRuleType.NO_PARKING)], [])


@pytest.mark.parametrize("segment_ids", [[], ["segment-other"]])
def test_constructor_rejects_evidence_not_bound_to_rule_segment(
    segment_ids: list[str],
) -> None:
    with pytest.raises(
        ValueError,
        match=r"not bound to their segment: rule-1:evidence-a->segment-1",
    ):
        DeterministicRegulationEngine(
            [rule(ParkingRuleType.NO_PARKING)],
            [evidence(segment_ids=segment_ids)],
        )


def test_constructor_accepts_multi_segment_evidence_bound_to_rule_segment() -> None:
    result = evaluate(
        [rule(ParkingRuleType.NO_PARKING)],
        evidence_items=[evidence(segment_ids=["segment-other", "segment-1"])],
    )

    assert result.legal_state is LegalState.ILLEGAL
    assert result.evidence_refs == ["evidence-a"]


def test_missing_duration_never_establishes_positive_legality_or_free_state() -> None:
    rules = [
        rule(
            ParkingRuleType.PAID,
            rule_id="explicit-free",
            payment_required=False,
        ),
        rule(
            ParkingRuleType.PERMIT_ONLY,
            rule_id="permit",
            permit_required=True,
            permit_type="SMU-A",
        ),
    ]

    result = evaluate(rules, profile=UserProfile(permit_types=["SMU-A"]))

    assert (result.legal_state, result.free_state) == (
        LegalState.UNKNOWN,
        FreeState.UNKNOWN,
    )
    assert RegulationReasonCode.INSUFFICIENT_EVIDENCE in result.reason_codes


def test_missing_duration_keeps_definite_prohibition_and_payment_conservative() -> None:
    rules = [
        rule(ParkingRuleType.NO_PARKING, rule_id="no-parking"),
        rule(
            ParkingRuleType.PAID,
            rule_id="paid",
            payment_required=True,
        ),
    ]

    result = evaluate(rules)

    assert (result.legal_state, result.free_state) == (LegalState.ILLEGAL, FreeState.PAID)


def test_unknown_same_tier_rule_does_not_weaken_definite_prohibition() -> None:
    rules = [
        rule(ParkingRuleType.NO_PARKING, rule_id="no-parking"),
        rule(ParkingRuleType.OTHER, rule_id="unstructured"),
    ]

    result = evaluate(rules, profile=UserProfile(requested_parking_duration_min=30))

    assert result.legal_state is LegalState.ILLEGAL
    assert RegulationReasonCode.ACTIVE_NO_PARKING in result.reason_codes


def test_unknown_same_tier_payment_claim_does_not_hide_definite_paid_state() -> None:
    rules = [
        rule(ParkingRuleType.PAID, rule_id="paid", payment_required=True),
        rule(ParkingRuleType.PAID, rule_id="payment-unknown"),
    ]

    result = evaluate(rules, profile=UserProfile(requested_parking_duration_min=30))

    assert (result.legal_state, result.free_state) == (LegalState.LEGAL, FreeState.PAID)
