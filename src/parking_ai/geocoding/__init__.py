"""Provider-independent geocoding contracts and bounded adapters."""

from parking_ai.geocoding.models import (
    Geocoder,
    GeocodingBounds,
    GeocodingMatch,
    GeocodingProviderMetadata,
    GeocodingRequest,
    GeocodingResult,
    GeocodingStatus,
    normalized_geocoding_cache_key,
    stable_geocoding_match_id,
)
from parking_ai.geocoding.nominatim import (
    NOMINATIM_ATTRIBUTION,
    NOMINATIM_LICENSE,
    NOMINATIM_PROVIDER_VERSION,
    GeocodingError,
    GeocodingProviderError,
    GeocodingResponseError,
    GeocodingTransport,
    NominatimGeocoder,
    UrllibJsonTransport,
)

__all__ = [
    "NOMINATIM_ATTRIBUTION",
    "NOMINATIM_LICENSE",
    "NOMINATIM_PROVIDER_VERSION",
    "Geocoder",
    "GeocodingBounds",
    "GeocodingError",
    "GeocodingMatch",
    "GeocodingProviderError",
    "GeocodingProviderMetadata",
    "GeocodingRequest",
    "GeocodingResponseError",
    "GeocodingResult",
    "GeocodingStatus",
    "GeocodingTransport",
    "NominatimGeocoder",
    "UrllibJsonTransport",
    "normalized_geocoding_cache_key",
    "stable_geocoding_match_id",
]
