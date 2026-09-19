from enum import StrEnum


class DomainEnum(StrEnum):
    """String enum whose serialized value is stable across API and persistence layers."""


class LegalState(DomainEnum):
    LEGAL = "LEGAL"
    ILLEGAL = "ILLEGAL"
    UNKNOWN = "UNKNOWN"


class FreeState(DomainEnum):
    FREE = "FREE"
    PAID = "PAID"
    UNKNOWN = "UNKNOWN"


class PhysicalState(DomainEnum):
    PARKABLE = "PARKABLE"
    NOT_PARKABLE = "NOT_PARKABLE"
    UNKNOWN = "UNKNOWN"


class SegmentSide(DomainEnum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    UNKNOWN = "UNKNOWN"


class EvidenceReliabilityTier(DomainEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class EvidenceStoragePolicy(DomainEnum):
    PERSIST = "PERSIST"
    EPHEMERAL = "EPHEMERAL"
    REFERENCE_ONLY = "REFERENCE_ONLY"


class EvidenceSourceType(DomainEnum):
    OFFICIAL_CODE = "OFFICIAL_CODE"
    OFFICIAL_GIS = "OFFICIAL_GIS"
    VERIFIED_SIGN = "VERIFIED_SIGN"
    UNIVERSITY = "UNIVERSITY"
    OSM = "OSM"
    COMMUNITY = "COMMUNITY"
    WEB = "WEB"
    IMAGERY_INFERENCE = "IMAGERY_INFERENCE"


class ParkingRuleType(DomainEnum):
    NO_PARKING = "NO_PARKING"
    TIME_LIMIT = "TIME_LIMIT"
    PAID = "PAID"
    PERMIT_ONLY = "PERMIT_ONLY"
    LOADING = "LOADING"
    STREET_CLEANING = "STREET_CLEANING"
    EVENT_RESTRICTION = "EVENT_RESTRICTION"
    OTHER = "OTHER"


class DayOfWeek(DomainEnum):
    MON = "MON"
    TUE = "TUE"
    WED = "WED"
    THU = "THU"
    FRI = "FRI"
    SAT = "SAT"
    SUN = "SUN"


class SearchSessionStatus(DomainEnum):
    CREATED = "CREATED"
    PLANNED = "PLANNED"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"
