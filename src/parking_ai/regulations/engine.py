from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum
from itertools import pairwise
from types import MappingProxyType
from zoneinfo import ZoneInfo

from parking_ai.domain.enums import (
    DayOfWeek,
    EvidenceReliabilityTier,
    EvidenceSourceType,
    FreeState,
    LegalState,
    ParkingRuleType,
    RegulationReasonCode,
)
from parking_ai.domain.evidence_policy import evidence_reliability_within_source_authority
from parking_ai.domain.schemas import (
    Evidence,
    LegalityEvaluation,
    ParkingRule,
    ParkingSegment,
    RuleException,
    UserProfile,
)

RULE_ENGINE_VERSION = "regulation-engine-v2"
DEFAULT_REGULATION_TIMEZONE = ZoneInfo("America/Chicago")

# A source-specific default avoids pretending that all evidence ages in the same way. Official
# code validity is expressed by rule effective dates rather than publication age. Every limit can
# be overridden (or disabled with None) when constructing an engine for another jurisdiction.
DEFAULT_EVIDENCE_MAX_AGE: Mapping[EvidenceSourceType, dt.timedelta | None] = MappingProxyType(
    {
        EvidenceSourceType.OFFICIAL_CODE: None,
        EvidenceSourceType.OFFICIAL_GIS: dt.timedelta(days=365),
        EvidenceSourceType.VERIFIED_SIGN: dt.timedelta(days=180),
        EvidenceSourceType.UNIVERSITY: dt.timedelta(days=365),
        EvidenceSourceType.OSM: dt.timedelta(days=180),
        EvidenceSourceType.COMMUNITY: dt.timedelta(days=30),
        EvidenceSourceType.WEB: dt.timedelta(days=90),
        EvidenceSourceType.IMAGERY_INFERENCE: dt.timedelta(days=90),
    }
)

_DAY_BY_WEEKDAY = {
    0: DayOfWeek.MON,
    1: DayOfWeek.TUE,
    2: DayOfWeek.WED,
    3: DayOfWeek.THU,
    4: DayOfWeek.FRI,
    5: DayOfWeek.SAT,
    6: DayOfWeek.SUN,
}

_TIER_RANK = {
    EvidenceReliabilityTier.A: 0,
    EvidenceReliabilityTier.B: 1,
    EvidenceReliabilityTier.C: 2,
    EvidenceReliabilityTier.D: 3,
}

_PERMIT_EXCEPTION_TYPES = frozenset({"PERMIT", "PERMIT_TYPE"})


class _Tier(IntEnum):
    A = 0
    B = 1
    C = 2
    D = 3


@dataclass(frozen=True)
class _QueryInterval:
    start_utc: dt.datetime
    end_utc: dt.datetime
    is_point: bool
    timezone: dt.tzinfo


@dataclass(frozen=True)
class _WindowMatch:
    valid: bool
    overlaps: bool
    covers: bool
    occurrences: tuple[tuple[dt.datetime, dt.datetime], ...] = ()


@dataclass(frozen=True, eq=False)
class _Assessment:
    rule: ParkingRule
    evidence_id: str
    tier: _Tier
    confidence: float
    legal_state: LegalState | None = None
    free_state: FreeState | None = None
    max_duration_min: int | None = None
    reasons: tuple[RegulationReasonCode, ...] = ()


class DeterministicRegulationEngine:
    """Evaluate normalized parking rules without provider or probabilistic behavior.

    Rules and their evidence are copied at construction so a single evaluation observes an
    immutable rule set. The configured timezone is the segment-local schedule timezone.
    Requested duration is elapsed time and is therefore added on the UTC timeline.
    """

    def __init__(
        self,
        rules: Iterable[ParkingRule],
        evidence: Iterable[Evidence],
        *,
        rule_engine_version: str = RULE_ENGINE_VERSION,
        timezone: dt.tzinfo = DEFAULT_REGULATION_TIMEZONE,
        evidence_max_age: Mapping[EvidenceSourceType, dt.timedelta | None] | None = None,
    ) -> None:
        if not rule_engine_version:
            raise ValueError("rule_engine_version must not be empty")

        evidence_copies = [item.model_copy(deep=True) for item in evidence]
        invalid_timestamps = sorted(
            item.evidence_id
            for item in evidence_copies
            if (item.published_at is not None and item.published_at > item.retrieved_at)
            or (item.observed_at is not None and item.observed_at > item.retrieved_at)
        )
        if invalid_timestamps:
            raise ValueError(
                "evidence publication/observation timestamps cannot be after retrieval: "
                + ", ".join(invalid_timestamps)
            )
        elevated_authority = sorted(
            item.evidence_id
            for item in evidence_copies
            if not evidence_reliability_within_source_authority(
                item.source_type,
                item.reliability_tier,
            )
        )
        if elevated_authority:
            raise ValueError(
                "evidence reliability tier exceeds its source authority ceiling: "
                + ", ".join(elevated_authority)
            )
        evidence_by_id = {item.evidence_id: item for item in evidence_copies}
        if len(evidence_by_id) != len(evidence_copies):
            raise ValueError("evidence IDs must be unique")

        rule_copies = tuple(item.model_copy(deep=True) for item in rules)
        rule_ids = {item.rule_id for item in rule_copies}
        if len(rule_ids) != len(rule_copies):
            raise ValueError("rule IDs must be unique")
        missing_evidence = sorted(
            {item.source_evidence_id for item in rule_copies} - evidence_by_id.keys()
        )
        if missing_evidence:
            raise ValueError(f"rules reference missing evidence: {', '.join(missing_evidence)}")
        unbound_provenance = sorted(
            f"{item.rule_id}:{item.source_evidence_id}->{item.segment_id}"
            for item in rule_copies
            if item.segment_id not in evidence_by_id[item.source_evidence_id].segment_ids
        )
        if unbound_provenance:
            raise ValueError(
                "rules reference evidence not bound to their segment: "
                + ", ".join(unbound_provenance)
            )

        self._rules = rule_copies
        self._evidence_by_id = MappingProxyType(evidence_by_id)
        self._version = rule_engine_version
        self._timezone = timezone
        configured_max_age = dict(DEFAULT_EVIDENCE_MAX_AGE)
        if evidence_max_age is not None:
            configured_max_age.update(evidence_max_age)
        invalid_ages = [
            source.value
            for source, max_age in configured_max_age.items()
            if max_age is not None and max_age <= dt.timedelta(0)
        ]
        if invalid_ages:
            raise ValueError(
                "evidence maximum ages must be positive: " + ", ".join(sorted(invalid_ages))
            )
        self._evidence_max_age = MappingProxyType(configured_max_age)

    @property
    def rule_engine_version(self) -> str:
        return self._version

    def evaluate_legality(
        self,
        segment: ParkingSegment,
        user_profile: UserProfile,
        datetime: dt.datetime,
    ) -> LegalityEvaluation:
        query = _build_query_interval(
            datetime,
            user_profile.requested_parking_duration_min,
            self._timezone,
        )
        segment_rules = tuple(rule for rule in self._rules if rule.segment_id == segment.segment_id)

        matched_rules: list[tuple[ParkingRule, Evidence, _WindowMatch]] = []
        invalid_assessments: list[_Assessment] = []
        inactive_evidence: set[str] = set()
        inactive_seen = False
        for rule in segment_rules:
            evidence = self._evidence_by_id[rule.source_evidence_id]
            match = _match_rule_window(rule, query)
            if not match.valid:
                invalid_assessments.append(
                    _Assessment(
                        rule=rule,
                        evidence_id=evidence.evidence_id,
                        tier=_Tier(_TIER_RANK[evidence.reliability_tier]),
                        confidence=rule.extraction_confidence,
                        legal_state=LegalState.UNKNOWN,
                        free_state=(
                            FreeState.UNKNOWN if rule.rule_type is ParkingRuleType.PAID else None
                        ),
                        reasons=(RegulationReasonCode.INVALID_RULE_SCHEDULE,),
                    )
                )
                continue
            if not match.overlaps:
                inactive_seen = True
                inactive_evidence.add(evidence.evidence_id)
                continue
            matched_rules.append((rule, evidence, match))

        legal_slice_states: list[LegalState] = []
        free_slice_states: list[FreeState] = []
        legal_selected: set[_Assessment] = set()
        free_selected: set[_Assessment] = set()
        selected: set[_Assessment] = set()
        legal_conflict = False
        free_conflict = False
        legal_overridden = False
        free_overridden = False
        for slice_start, slice_end in _query_slices(query, matched_rules):
            assessments = list(invalid_assessments)
            for rule, evidence, match in matched_rules:
                if not _match_covers_slice(match, slice_start, slice_end, query.is_point):
                    continue
                freshness_issue = _evidence_freshness_issue(
                    evidence,
                    query.start_utc,
                    query.end_utc,
                    self._evidence_max_age,
                )
                if freshness_issue is not None:
                    assessments.append(
                        _unusable_evidence_assessment(rule, evidence, freshness_issue)
                    )
                else:
                    assessments.append(
                        _assess_active_rule(
                            rule=rule,
                            evidence=evidence,
                            match=_WindowMatch(
                                valid=True,
                                overlaps=True,
                                covers=not query.is_point,
                            ),
                            user_profile=user_profile,
                        )
                    )

            (
                slice_legal_state,
                slice_legal_selected,
                slice_legal_conflict,
                slice_legal_overridden,
            ) = _resolve_legal(assessments)
            (
                slice_free_state,
                slice_free_selected,
                slice_free_conflict,
                slice_free_overridden,
            ) = _resolve_free(assessments)
            legal_slice_states.append(slice_legal_state)
            free_slice_states.append(slice_free_state)
            legal_selected.update(slice_legal_selected)
            free_selected.update(slice_free_selected)
            selected.update(slice_legal_selected)
            selected.update(slice_free_selected)
            selected.update(
                item
                for item in assessments
                if item.legal_state is None and item.free_state is None and item.reasons
            )
            legal_conflict = legal_conflict or slice_legal_conflict
            free_conflict = free_conflict or slice_free_conflict
            legal_overridden = legal_overridden or slice_legal_overridden
            free_overridden = free_overridden or slice_free_overridden

        legal_state = _aggregate_legal_states(legal_slice_states)
        free_state = _aggregate_free_states(free_slice_states)
        active_limits = [item for item in legal_selected if item.max_duration_min is not None]
        selected.update(active_limits)
        max_duration = min(
            (item.max_duration_min for item in active_limits if item.max_duration_min is not None),
            default=None,
        )

        reasons = {reason for item in selected for reason in item.reasons}
        if legal_conflict or free_conflict:
            reasons.add(RegulationReasonCode.CONFLICTING_EVIDENCE)
        if legal_overridden or free_overridden:
            reasons.add(RegulationReasonCode.LOWER_TIER_EVIDENCE_OVERRIDDEN)
        if legal_state is LegalState.UNKNOWN and not legal_conflict:
            reasons.add(RegulationReasonCode.INSUFFICIENT_EVIDENCE)
        if free_state is FreeState.UNKNOWN and not free_conflict:
            reasons.add(RegulationReasonCode.INSUFFICIENT_EVIDENCE)
        if inactive_seen and not matched_rules and not invalid_assessments:
            reasons.add(RegulationReasonCode.RULE_INACTIVE)
        if not reasons:
            reasons.add(RegulationReasonCode.INSUFFICIENT_EVIDENCE)

        evidence_refs = sorted({item.evidence_id for item in selected})
        if not selected and inactive_seen:
            evidence_refs = sorted(inactive_evidence)
        confidence = 0.0
        if not legal_conflict and not free_conflict:
            decisive: set[_Assessment] = set()
            if legal_state is not LegalState.UNKNOWN:
                decisive.update(legal_selected)
            if free_state is not FreeState.UNKNOWN:
                decisive.update(free_selected)
            confidence = min((item.confidence for item in decisive), default=0.0)
        reason_codes = [reason for reason in RegulationReasonCode if reason in reasons]
        evaluation_id = _evaluation_id(
            segment=segment,
            user_profile=user_profile,
            query=query,
            legal_state=legal_state,
            free_state=free_state,
            max_duration_min=max_duration,
            confidence=confidence,
            evidence_refs=evidence_refs,
            reasons=reason_codes,
            version=self._version,
        )

        return LegalityEvaluation(
            evaluation_id=evaluation_id,
            segment_id=segment.segment_id,
            legal_state=legal_state,
            free_state=free_state,
            max_duration_min=max_duration,
            confidence=confidence,
            evidence_refs=evidence_refs,
            reason_codes=reason_codes,
            evaluated_at=datetime,
            rule_engine_version=self._version,
        )


def _build_query_interval(
    arrival: dt.datetime,
    duration_min: int | None,
    timezone: dt.tzinfo,
) -> _QueryInterval:
    if arrival.tzinfo is None or arrival.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    start_utc = arrival.astimezone(dt.UTC)
    if duration_min is None:
        end_utc = start_utc
        is_point = True
    else:
        end_utc = start_utc + dt.timedelta(minutes=duration_min)
        is_point = False
    return _QueryInterval(start_utc, end_utc, is_point, timezone)


def _match_rule_window(rule: ParkingRule, query: _QueryInterval) -> _WindowMatch:
    if (rule.start_time is None) != (rule.end_time is None):
        return _WindowMatch(valid=False, overlaps=False, covers=False)

    start_local_date = query.start_utc.astimezone(query.timezone).date()
    end_local_date = query.end_utc.astimezone(query.timezone).date()
    first_anchor = start_local_date - dt.timedelta(days=1)
    last_anchor = end_local_date
    occurrences: list[tuple[dt.datetime, dt.datetime]] = []

    anchor = first_anchor
    while anchor <= last_anchor:
        if _rule_applies_on_anchor(rule, anchor):
            anchored_occurrences = _occurrence_utc(rule, anchor, query.timezone)
            if anchored_occurrences is None:
                return _WindowMatch(valid=False, overlaps=False, covers=False)
            occurrences.extend(anchored_occurrences)
        anchor += dt.timedelta(days=1)

    overlapping = [
        occurrence
        for occurrence in occurrences
        if _intervals_overlap(occurrence[0], occurrence[1], query)
    ]
    if not overlapping:
        return _WindowMatch(valid=True, overlaps=False, covers=False)
    return _WindowMatch(
        valid=True,
        overlaps=True,
        covers=_occurrences_cover_query(overlapping, query),
        occurrences=tuple(sorted(overlapping)),
    )


def _rule_applies_on_anchor(rule: ParkingRule, anchor: dt.date) -> bool:
    if rule.effective_start_date is not None and anchor < rule.effective_start_date:
        return False
    if rule.effective_end_date is not None and anchor > rule.effective_end_date:
        return False
    return not rule.days or _DAY_BY_WEEKDAY[anchor.weekday()] in rule.days


def _occurrence_utc(
    rule: ParkingRule,
    anchor: dt.date,
    timezone: dt.tzinfo,
) -> list[tuple[dt.datetime, dt.datetime]] | None:
    if rule.start_time is None and rule.end_time is None:
        start_local = dt.datetime.combine(anchor, dt.time.min)
        end_local = dt.datetime.combine(anchor + dt.timedelta(days=1), dt.time.min)
    elif rule.start_time is not None and rule.end_time is not None:
        start_local = dt.datetime.combine(anchor, rule.start_time)
        end_date = anchor
        if rule.end_time <= rule.start_time:
            end_date += dt.timedelta(days=1)
        end_local = dt.datetime.combine(end_date, rule.end_time)
    else:
        return None

    occurrences = _wall_interval_utc_occurrences(start_local, end_local, timezone)
    return occurrences or None


def _wall_interval_utc_occurrences(
    start_local: dt.datetime,
    end_local: dt.datetime,
    timezone: dt.tzinfo,
) -> list[tuple[dt.datetime, dt.datetime]]:
    # A local wall-time interval is a predicate over real instants. Partition a generous UTC
    # search window into constant-offset eras, map the wall interval through each era's offset,
    # and clip it to that era. This handles gaps and repeated hours without inventing coverage.
    search_start = (start_local - dt.timedelta(days=2)).replace(tzinfo=dt.UTC)
    search_end = (end_local + dt.timedelta(days=2)).replace(tzinfo=dt.UTC)
    transitions = _utc_offset_transitions(search_start, search_end, timezone)
    era_boundaries = [search_start, *transitions, search_end]
    occurrences: list[tuple[dt.datetime, dt.datetime]] = []
    for era_start, era_end in pairwise(era_boundaries):
        offset = _utc_offset(era_start, timezone)
        mapped_start = (start_local - offset).replace(tzinfo=dt.UTC)
        mapped_end = (end_local - offset).replace(tzinfo=dt.UTC)
        occurrence_start = max(mapped_start, era_start)
        occurrence_end = min(mapped_end, era_end)
        if occurrence_start < occurrence_end:
            occurrences.append((occurrence_start, occurrence_end))

    merged: list[tuple[dt.datetime, dt.datetime]] = []
    for occurrence_start, occurrence_end in sorted(occurrences):
        if merged and occurrence_start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], occurrence_end))
        else:
            merged.append((occurrence_start, occurrence_end))
    return merged


def _utc_offset_transitions(
    start_utc: dt.datetime,
    end_utc: dt.datetime,
    timezone: dt.tzinfo,
) -> list[dt.datetime]:
    transitions: list[dt.datetime] = []
    cursor = start_utc
    cursor_offset = _utc_offset(cursor, timezone)
    step = dt.timedelta(minutes=30)
    while cursor < end_utc:
        probe = min(cursor + step, end_utc)
        probe_offset = _utc_offset(probe, timezone)
        if probe_offset != cursor_offset:
            transitions.append(_find_utc_offset_transition(cursor, probe, cursor_offset, timezone))
        cursor = probe
        cursor_offset = probe_offset
    return transitions


def _find_utc_offset_transition(
    lower_utc: dt.datetime,
    upper_utc: dt.datetime,
    lower_offset: dt.timedelta,
    timezone: dt.tzinfo,
) -> dt.datetime:
    resolution = dt.timedelta(microseconds=1)
    while upper_utc - lower_utc > resolution:
        midpoint = lower_utc + (upper_utc - lower_utc) / 2
        if _utc_offset(midpoint, timezone) == lower_offset:
            lower_utc = midpoint
        else:
            upper_utc = midpoint
    return upper_utc


def _utc_offset(value_utc: dt.datetime, timezone: dt.tzinfo) -> dt.timedelta:
    offset = value_utc.astimezone(timezone).utcoffset()
    if offset is None:
        raise ValueError("regulation timezone must provide a UTC offset")
    return offset


def _query_slices(
    query: _QueryInterval,
    matched_rules: Sequence[tuple[ParkingRule, Evidence, _WindowMatch]],
) -> list[tuple[dt.datetime, dt.datetime]]:
    if query.is_point:
        return [(query.start_utc, query.start_utc)]
    boundaries = {query.start_utc, query.end_utc}
    for _, _, match in matched_rules:
        for occurrence_start, occurrence_end in match.occurrences:
            boundaries.add(max(query.start_utc, occurrence_start))
            boundaries.add(min(query.end_utc, occurrence_end))
    ordered = sorted(boundaries)
    return list(pairwise(ordered))


def _match_covers_slice(
    match: _WindowMatch,
    slice_start: dt.datetime,
    slice_end: dt.datetime,
    is_point: bool,
) -> bool:
    if is_point:
        return any(start <= slice_start < end for start, end in match.occurrences)
    return any(start <= slice_start and slice_end <= end for start, end in match.occurrences)


def _evidence_freshness_issue(
    evidence: Evidence,
    query_start_utc: dt.datetime,
    query_end_utc: dt.datetime,
    max_age_by_source: Mapping[EvidenceSourceType, dt.timedelta | None],
) -> RegulationReasonCode | None:
    retrieved_utc = evidence.retrieved_at.astimezone(dt.UTC)
    published_utc = (
        evidence.published_at.astimezone(dt.UTC) if evidence.published_at is not None else None
    )
    observed_utc = (
        evidence.observed_at.astimezone(dt.UTC) if evidence.observed_at is not None else None
    )
    if (
        retrieved_utc > query_start_utc
        or (published_utc is not None and published_utc > query_start_utc)
        or (observed_utc is not None and observed_utc > query_start_utc)
    ):
        return RegulationReasonCode.EVIDENCE_NOT_YET_AVAILABLE
    freshness_utc = observed_utc or retrieved_utc
    age = query_end_utc - freshness_utc
    max_age = max_age_by_source.get(evidence.source_type)
    if max_age is not None and age > max_age:
        return RegulationReasonCode.STALE_EVIDENCE
    return None


def _unusable_evidence_assessment(
    rule: ParkingRule,
    evidence: Evidence,
    reason: RegulationReasonCode,
) -> _Assessment:
    return _new_assessment(
        rule,
        evidence,
        legal_state=LegalState.UNKNOWN,
        free_state=(
            FreeState.UNKNOWN
            if rule.rule_type is ParkingRuleType.PAID or rule.payment_required is not None
            else None
        ),
        reasons=(reason,),
    )


def _aggregate_legal_states(states: Sequence[LegalState]) -> LegalState:
    if LegalState.ILLEGAL in states:
        return LegalState.ILLEGAL
    if states and all(state is LegalState.LEGAL for state in states):
        return LegalState.LEGAL
    return LegalState.UNKNOWN


def _aggregate_free_states(states: Sequence[FreeState]) -> FreeState:
    if FreeState.PAID in states:
        return FreeState.PAID
    if states and all(state is FreeState.FREE for state in states):
        return FreeState.FREE
    return FreeState.UNKNOWN


def _intervals_overlap(
    occurrence_start: dt.datetime,
    occurrence_end: dt.datetime,
    query: _QueryInterval,
) -> bool:
    if query.is_point:
        return occurrence_start <= query.start_utc < occurrence_end
    return occurrence_start < query.end_utc and query.start_utc < occurrence_end


def _occurrences_cover_query(
    occurrences: Sequence[tuple[dt.datetime, dt.datetime]],
    query: _QueryInterval,
) -> bool:
    if query.is_point:
        # A missing requested duration is not evidence that the rule covers the whole stay.
        # Definite prohibitions and payment requirements may still apply at arrival, but a
        # point query cannot establish positive LEGAL or FREE conclusions.
        return False
    cursor = query.start_utc
    for start, end in sorted(occurrences):
        if end <= cursor:
            continue
        if start > cursor:
            return False
        cursor = max(cursor, end)
        if cursor >= query.end_utc:
            return True
    return False


def _assess_active_rule(
    *,
    rule: ParkingRule,
    evidence: Evidence,
    match: _WindowMatch,
    user_profile: UserProfile,
) -> _Assessment:
    if rule.rule_type is ParkingRuleType.NO_PARKING:
        if _has_no_parking_exception(rule, user_profile):
            return _new_assessment(
                rule,
                evidence,
                reasons=(RegulationReasonCode.VALID_PERMIT_EXCEPTION,),
            )
        return _new_assessment(
            rule,
            evidence,
            legal_state=LegalState.ILLEGAL,
            reasons=(RegulationReasonCode.ACTIVE_NO_PARKING,),
        )

    if rule.rule_type is ParkingRuleType.TIME_LIMIT:
        if rule.max_duration_min is None:
            return _new_assessment(
                rule,
                evidence,
                legal_state=LegalState.UNKNOWN,
                free_state=_explicit_payment_state(rule, match),
                reasons=(RegulationReasonCode.UNSUPPORTED_RULE, *_payment_reasons(rule, match)),
            )
        requested = user_profile.requested_parking_duration_min
        if requested is not None and requested > rule.max_duration_min:
            return _new_assessment(
                rule,
                evidence,
                legal_state=LegalState.ILLEGAL,
                free_state=_explicit_payment_state(rule, match),
                max_duration_min=rule.max_duration_min,
                reasons=(
                    RegulationReasonCode.TIME_LIMIT_APPLIES,
                    RegulationReasonCode.TIME_LIMIT_EXCEEDED,
                    *_payment_reasons(rule, match),
                ),
            )
        return _new_assessment(
            rule,
            evidence,
            legal_state=LegalState.LEGAL if match.covers else LegalState.UNKNOWN,
            free_state=_explicit_payment_state(rule, match),
            max_duration_min=rule.max_duration_min,
            reasons=(
                RegulationReasonCode.TIME_LIMIT_APPLIES,
                *_payment_reasons(rule, match),
            ),
        )

    if rule.rule_type is ParkingRuleType.PAID:
        if rule.payment_required is None:
            return _new_assessment(
                rule,
                evidence,
                legal_state=LegalState.LEGAL if match.covers else LegalState.UNKNOWN,
                free_state=FreeState.UNKNOWN,
                reasons=(RegulationReasonCode.INSUFFICIENT_EVIDENCE,),
            )
        if rule.payment_required:
            return _new_assessment(
                rule,
                evidence,
                legal_state=LegalState.LEGAL if match.covers else LegalState.UNKNOWN,
                free_state=FreeState.PAID,
                reasons=(RegulationReasonCode.PAYMENT_REQUIRED,),
            )
        return _new_assessment(
            rule,
            evidence,
            legal_state=LegalState.LEGAL if match.covers else LegalState.UNKNOWN,
            free_state=FreeState.FREE if match.covers else FreeState.UNKNOWN,
            reasons=(RegulationReasonCode.NO_PAYMENT_REQUIRED,),
        )

    if rule.rule_type is ParkingRuleType.PERMIT_ONLY:
        permit_required = rule.permit_required is not False
        if permit_required and rule.permit_type is None and user_profile.permit_types:
            return _new_assessment(
                rule,
                evidence,
                legal_state=LegalState.UNKNOWN,
                free_state=_explicit_payment_state(rule, match),
                reasons=(
                    RegulationReasonCode.INSUFFICIENT_EVIDENCE,
                    *_payment_reasons(rule, match),
                ),
            )
        has_permit = (not permit_required) or _has_required_permit(rule, user_profile)
        if has_permit:
            return _new_assessment(
                rule,
                evidence,
                legal_state=LegalState.LEGAL if match.covers else LegalState.UNKNOWN,
                free_state=_explicit_payment_state(rule, match),
                reasons=(
                    RegulationReasonCode.VALID_PERMIT_EXCEPTION,
                    *_payment_reasons(rule, match),
                ),
            )
        return _new_assessment(
            rule,
            evidence,
            legal_state=LegalState.ILLEGAL,
            free_state=_explicit_payment_state(rule, match),
            reasons=(RegulationReasonCode.PERMIT_REQUIRED, *_payment_reasons(rule, match)),
        )

    if rule.rule_type is ParkingRuleType.LOADING:
        if _has_vehicle_exception(rule.exceptions, user_profile):
            return _new_assessment(
                rule,
                evidence,
                legal_state=LegalState.LEGAL if match.covers else LegalState.UNKNOWN,
                free_state=_explicit_payment_state(rule, match),
                reasons=(
                    RegulationReasonCode.VALID_VEHICLE_EXCEPTION,
                    *_payment_reasons(rule, match),
                ),
            )
        return _new_assessment(
            rule,
            evidence,
            legal_state=LegalState.ILLEGAL,
            free_state=_explicit_payment_state(rule, match),
            reasons=(RegulationReasonCode.LOADING_ONLY, *_payment_reasons(rule, match)),
        )

    if rule.rule_type is ParkingRuleType.STREET_CLEANING:
        return _new_assessment(
            rule,
            evidence,
            legal_state=LegalState.ILLEGAL,
            reasons=(RegulationReasonCode.ACTIVE_STREET_CLEANING,),
        )

    if rule.rule_type is ParkingRuleType.EVENT_RESTRICTION:
        return _new_assessment(
            rule,
            evidence,
            legal_state=LegalState.ILLEGAL,
            reasons=(RegulationReasonCode.ACTIVE_EVENT_RESTRICTION,),
        )

    return _new_assessment(
        rule,
        evidence,
        legal_state=LegalState.UNKNOWN,
        free_state=_explicit_payment_state(rule, match),
        reasons=(RegulationReasonCode.UNSUPPORTED_RULE, *_payment_reasons(rule, match)),
    )


def _new_assessment(
    rule: ParkingRule,
    evidence: Evidence,
    *,
    legal_state: LegalState | None = None,
    free_state: FreeState | None = None,
    max_duration_min: int | None = None,
    reasons: tuple[RegulationReasonCode, ...] = (),
) -> _Assessment:
    return _Assessment(
        rule=rule,
        evidence_id=evidence.evidence_id,
        tier=_Tier(_TIER_RANK[evidence.reliability_tier]),
        confidence=rule.extraction_confidence,
        legal_state=legal_state,
        free_state=free_state,
        max_duration_min=max_duration_min,
        reasons=reasons,
    )


def _has_required_permit(rule: ParkingRule, user_profile: UserProfile) -> bool:
    user_permits = {permit.casefold() for permit in user_profile.permit_types}
    if rule.permit_type is None:
        return False
    return rule.permit_type.casefold() in user_permits


def _has_no_parking_exception(rule: ParkingRule, user_profile: UserProfile) -> bool:
    if (
        rule.permit_required is True
        and rule.permit_type is not None
        and _has_required_permit(rule, user_profile)
    ):
        return True
    return _has_explicit_exception(rule.exceptions, user_profile)


def _has_explicit_exception(exceptions: Sequence[RuleException], user_profile: UserProfile) -> bool:
    user_permits = {permit.casefold() for permit in user_profile.permit_types}
    for exception in exceptions:
        if exception.exception_type.upper() not in _PERMIT_EXCEPTION_TYPES:
            continue
        permit_type = exception.parameters.get("permit_type")
        if isinstance(permit_type, str) and permit_type.casefold() in user_permits:
            return True
    return False


def _has_vehicle_exception(exceptions: Sequence[RuleException], user_profile: UserProfile) -> bool:
    vehicle_type = user_profile.vehicle_type.casefold()
    for exception in exceptions:
        if exception.exception_type.upper() != "VEHICLE_TYPE":
            continue
        one_type = exception.parameters.get("vehicle_type")
        if isinstance(one_type, str) and one_type.casefold() == vehicle_type:
            return True
        many_types = exception.parameters.get("vehicle_types")
        if isinstance(many_types, list) and any(
            isinstance(item, str) and item.casefold() == vehicle_type for item in many_types
        ):
            return True
    return False


def _explicit_payment_state(rule: ParkingRule, match: _WindowMatch) -> FreeState | None:
    if rule.payment_required is True:
        return FreeState.PAID
    if rule.payment_required is False:
        return FreeState.FREE if match.covers else FreeState.UNKNOWN
    return None


def _payment_reasons(rule: ParkingRule, match: _WindowMatch) -> tuple[RegulationReasonCode, ...]:
    payment_state = _explicit_payment_state(rule, match)
    if payment_state is FreeState.PAID:
        return (RegulationReasonCode.PAYMENT_REQUIRED,)
    if payment_state is FreeState.FREE:
        return (RegulationReasonCode.NO_PAYMENT_REQUIRED,)
    return ()


def _resolve_legal(
    assessments: Sequence[_Assessment],
) -> tuple[LegalState, set[_Assessment], bool, bool]:
    candidates = [item for item in assessments if item.legal_state is not None]
    if not candidates:
        return LegalState.UNKNOWN, set(), False, False
    selected, overridden = _highest_tier(candidates)
    states = {item.legal_state for item in selected}
    if LegalState.ILLEGAL in states and LegalState.LEGAL in states:
        # Restrictions normally compose with permissions. A direct disagreement between
        # same-tier PERMIT_ONLY claims is instead unresolved evidence.
        relevant_types = {item.rule.rule_type for item in selected}
        if relevant_types == {ParkingRuleType.PERMIT_ONLY}:
            return LegalState.UNKNOWN, selected, True, overridden
        return LegalState.ILLEGAL, selected, False, overridden
    if LegalState.ILLEGAL in states:
        # An unresolved rule cannot weaken a definite active prohibition. This remains
        # conservative: the segment is excluded instead of being presented as uncertain.
        return LegalState.ILLEGAL, selected, False, overridden
    if LegalState.UNKNOWN in states:
        return LegalState.UNKNOWN, selected, len(states) > 1, overridden
    return LegalState.LEGAL, selected, False, overridden


def _resolve_free(
    assessments: Sequence[_Assessment],
) -> tuple[FreeState, set[_Assessment], bool, bool]:
    candidates = [item for item in assessments if item.free_state is not None]
    if not candidates:
        return FreeState.UNKNOWN, set(), False, False
    selected, overridden = _highest_tier(candidates)
    states = {item.free_state for item in selected}
    if FreeState.PAID in states and FreeState.FREE in states:
        return FreeState.UNKNOWN, selected, len(states) > 1, overridden
    if FreeState.PAID in states:
        # Any definite payment requirement is enough to reject a free-only recommendation;
        # an unresolved companion claim cannot safely erase it.
        return FreeState.PAID, selected, False, overridden
    if FreeState.UNKNOWN in states:
        return FreeState.UNKNOWN, selected, len(states) > 1, overridden
    return FreeState.FREE, selected, False, overridden


def _highest_tier(candidates: Sequence[_Assessment]) -> tuple[set[_Assessment], bool]:
    top_tier = min(item.tier for item in candidates)
    selected = {item for item in candidates if item.tier == top_tier}
    return selected, len(selected) != len(candidates)


def _evaluation_id(
    *,
    segment: ParkingSegment,
    user_profile: UserProfile,
    query: _QueryInterval,
    legal_state: LegalState,
    free_state: FreeState,
    max_duration_min: int | None,
    confidence: float,
    evidence_refs: Sequence[str],
    reasons: Sequence[RegulationReasonCode],
    version: str,
) -> str:
    payload = {
        "duration_min": user_profile.requested_parking_duration_min,
        "evidence_refs": list(evidence_refs),
        "free_state": free_state.value,
        "legal_state": legal_state.value,
        "max_duration_min": max_duration_min,
        "permit_types": sorted({permit.casefold() for permit in user_profile.permit_types}),
        "query_start_utc": query.start_utc.isoformat(),
        "reason_codes": [reason.value for reason in reasons],
        "rule_engine_version": version,
        "segment_id": segment.segment_id,
        "confidence": confidence,
        "vehicle_type": user_profile.vehicle_type.casefold(),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"eval_{digest[:32]}"
