from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from parking_ai.domain import EvidenceStoragePolicy, GeoPoint
from parking_ai.geocoding import (
    NOMINATIM_ATTRIBUTION,
    GeocodingBounds,
    GeocodingProviderError,
    GeocodingRequest,
    GeocodingResponseError,
    GeocodingStatus,
    NominatimGeocoder,
    stable_geocoding_match_id,
)

FIXED_NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def nominatim_item(
    *,
    osm_id: int = 100,
    name: str = "Fondren Library",
    display_name: str = "Fondren Library, Dallas, Texas, United States",
    latitude: str = "32.8414",
    longitude: str = "-96.7841",
    country_code: str = "us",
) -> dict[str, object]:
    return {
        "lat": latitude,
        "lon": longitude,
        "display_name": display_name,
        "name": name,
        "addresstype": "library",
        "type": "library",
        "osm_type": "way",
        "osm_id": osm_id,
        "address": {"country_code": country_code},
    }


class RecordingTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        self.calls.append(
            {
                "url": url,
                "params": dict(params),
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.responses.pop(0)


class RaisingTransport:
    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        del url, params, headers, timeout_seconds
        raise RuntimeError("network is unavailable")


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def geocoder(
    transport: RecordingTransport | RaisingTransport,
    *,
    fake_time: FakeTime | None = None,
    cache_ttl_seconds: float = 60.0,
    cache_max_entries: int = 16,
    wall_clock: object | None = None,
    search_url: str | None = None,
) -> NominatimGeocoder:
    kwargs: dict[str, object] = {
        "transport": transport,
        "user_agent": "ParkFree/0.1 (contact: test@example.com)",
        "timeout_seconds": 2.5,
        "cache_ttl_seconds": cache_ttl_seconds,
        "cache_max_entries": cache_max_entries,
        "wall_clock": wall_clock or (lambda: FIXED_NOW),
    }
    if fake_time is not None:
        kwargs.update(clock=fake_time.monotonic, sleep=fake_time.sleep)
    if search_url is not None:
        kwargs["search_url"] = search_url
    return NominatimGeocoder(**kwargs)  # type: ignore[arg-type]


def test_request_is_normalized_bounded_and_us_only() -> None:
    bounds = GeocodingBounds(west=-97.0, south=32.0, east=-96.0, north=33.0)
    request = GeocodingRequest(
        query="  Fondren   Library  ",
        country_code="US",
        language="en-US",
        limit=10,
        viewbox=bounds,
        bounded=True,
    )

    assert request.query == "Fondren Library"
    assert request.country_code == "US"
    with pytest.raises(ValidationError):
        GeocodingRequest(query="test", country_code="CA")  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="requires a viewbox"):
        GeocodingRequest(query="test", bounded=True)
    with pytest.raises(ValidationError):
        GeocodingBounds(west=-96.0, south=32.0, east=-97.0, north=33.0)
    with pytest.raises(ValidationError):
        GeocodingRequest(query="test", limit=11)


def test_stable_match_id_is_normalized_and_sensitive_to_meaningful_changes() -> None:
    location = GeoPoint(latitude=32.8414, longitude=-96.7841)
    first = stable_geocoding_match_id(
        name="Fondren  Library",
        formatted_address="Fondren Library, Dallas, TX",
        location=location,
        destination_type="Library",
    )
    normalized_equivalent = stable_geocoding_match_id(
        formatted_address=" fondren library, dallas, tx ",
        location=location,
        destination_type="library",
        name="fondren library",
    )
    changed = stable_geocoding_match_id(
        name="Fondren Library",
        formatted_address="Fondren Library, Dallas, TX",
        location=GeoPoint(latitude=32.8415, longitude=-96.7841),
        destination_type="library",
    )

    assert first == normalized_equivalent
    assert first.startswith("geo_")
    assert first != changed


def test_adapter_returns_explicit_ambiguous_matches_and_provider_metadata() -> None:
    transport = RecordingTransport(
        [
            [
                nominatim_item(),
                nominatim_item(
                    osm_id=101,
                    name="Fondren Science Building",
                    display_name="Fondren Science Building, Dallas, Texas, United States",
                    latitude="32.8420",
                ),
            ]
        ]
    )
    request = GeocodingRequest(
        query="Fondren",
        language="en-US",
        limit=2,
        viewbox=GeocodingBounds(west=-97.0, south=32.0, east=-96.0, north=33.0),
        bounded=True,
    )

    result = geocoder(transport).geocode(request)

    assert result.status is GeocodingStatus.AMBIGUOUS
    assert len(result.matches) == 2
    assert result.matches[0].location == GeoPoint(latitude=32.8414, longitude=-96.7841)
    assert result.matches[0].source_reference == "nominatim:way:100"
    assert result.metadata.attribution == NOMINATIM_ATTRIBUTION
    assert result.metadata.raw_storage_policy is EvidenceStoragePolicy.EPHEMERAL
    assert result.metadata.normalized_storage_policy is EvidenceStoragePolicy.PERSIST
    assert result.metadata.retrieved_at == FIXED_NOW
    assert not result.cache_hit

    call = transport.calls[0]
    assert call["url"] == "https://nominatim.openstreetmap.org/search"
    assert call["timeout_seconds"] == 2.5
    assert call["params"] == {
        "addressdetails": "1",
        "bounded": "1",
        "countrycodes": "us",
        "format": "jsonv2",
        "limit": "2",
        "q": "Fondren",
        "viewbox": "-97.0,33.0,-96.0,32.0",
    }
    assert call["headers"] == {
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "User-Agent": "ParkFree/0.1 (contact: test@example.com)",
    }


def test_no_match_and_duplicate_content_have_explicit_statuses() -> None:
    no_match_transport = RecordingTransport([[]])
    no_match = geocoder(no_match_transport).geocode(GeocodingRequest(query="not found"))
    assert no_match.status is GeocodingStatus.NO_MATCH
    assert no_match.matches == ()

    item = nominatim_item()
    duplicate_transport = RecordingTransport([[item, item]])
    duplicate = geocoder(duplicate_transport).geocode(GeocodingRequest(query="Fondren", limit=2))
    assert duplicate.status is GeocodingStatus.UNIQUE
    assert len(duplicate.matches) == 1


def test_cache_key_normalizes_query_and_ttl_expiration_is_bounded() -> None:
    fake_time = FakeTime()
    transport = RecordingTransport([[nominatim_item()], [nominatim_item()]])
    adapter = geocoder(transport, fake_time=fake_time, cache_ttl_seconds=10.0)

    first = adapter.geocode(GeocodingRequest(query="Fondren   Library"))
    second = adapter.geocode(GeocodingRequest(query=" fondren library "))
    fake_time.now = 10.0
    expired = adapter.geocode(GeocodingRequest(query="FONDREN LIBRARY"))

    assert not first.cache_hit
    assert second.cache_hit
    assert not expired.cache_hit
    assert len(transport.calls) == 2


def test_cache_and_match_identity_normalize_unicode_nfc() -> None:
    fake_time = FakeTime()
    transport = RecordingTransport([[nominatim_item(name="Café", display_name="Café, Dallas")]])
    adapter = geocoder(transport, fake_time=fake_time)

    composed = adapter.geocode(GeocodingRequest(query="Café"))
    decomposed = adapter.geocode(GeocodingRequest(query="Cafe\u0301"))
    composed_id = stable_geocoding_match_id(
        name="Café",
        formatted_address="Café, Dallas",
        location=GeoPoint(latitude=32.8414, longitude=-96.7841),
        destination_type="library",
    )
    decomposed_id = stable_geocoding_match_id(
        name="Cafe\u0301",
        formatted_address="Cafe\u0301, Dallas",
        location=GeoPoint(latitude=32.8414, longitude=-96.7841),
        destination_type="library",
    )

    assert composed.matches[0].match_id == decomposed.matches[0].match_id
    assert composed_id == decomposed_id
    assert decomposed.cache_hit
    assert len(transport.calls) == 1


def test_operator_search_url_controls_transport_and_metadata_without_query_injection() -> None:
    transport = RecordingTransport([[nominatim_item()]])
    adapter = geocoder(
        transport,
        search_url="http://localhost:8080/nominatim/search",
    )

    result = adapter.geocode(GeocodingRequest(query="Fondren"))

    assert transport.calls[0]["url"] == "http://localhost:8080/nominatim/search"
    assert transport.calls[0]["params"] == {
        "addressdetails": "1",
        "countrycodes": "us",
        "format": "jsonv2",
        "limit": "5",
        "q": "Fondren",
    }
    assert result.metadata.source_uri == "http://localhost:8080/nominatim/search"


def test_cache_evicts_least_recently_used_entry_at_configured_capacity() -> None:
    fake_time = FakeTime()
    transport = RecordingTransport(
        [[nominatim_item(osm_id=100)], [nominatim_item(osm_id=101)], [nominatim_item(osm_id=102)]]
    )
    adapter = geocoder(transport, fake_time=fake_time, cache_max_entries=1)

    adapter.geocode(GeocodingRequest(query="first"))
    adapter.geocode(GeocodingRequest(query="second"))
    adapter.geocode(GeocodingRequest(query="first"))

    assert len(transport.calls) == 3


def test_uncached_requests_are_serialized_at_one_request_per_second() -> None:
    fake_time = FakeTime()
    transport = RecordingTransport([[nominatim_item()], [nominatim_item(osm_id=101)]])
    adapter = geocoder(transport, fake_time=fake_time)

    adapter.geocode(GeocodingRequest(query="first"))
    fake_time.now = 0.25
    adapter.geocode(GeocodingRequest(query="second"))

    assert fake_time.sleeps == [0.75]
    assert len(transport.calls) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"not": "a list"},
        [nominatim_item(country_code="ca")],
        [nominatim_item(latitude="91")],
        [nominatim_item(longitude="not-a-number")],
        [nominatim_item(), nominatim_item(osm_id=101)],
    ],
)
def test_invalid_or_over_limit_provider_results_fail_closed(payload: object) -> None:
    adapter = geocoder(RecordingTransport([payload]))

    with pytest.raises(GeocodingResponseError):
        adapter.geocode(GeocodingRequest(query="Fondren", limit=1))


def test_unexpected_transport_failure_is_typed_and_not_cached() -> None:
    adapter = geocoder(RaisingTransport())

    with pytest.raises(GeocodingProviderError, match="request failed"):
        adapter.geocode(GeocodingRequest(query="Fondren"))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"user_agent": "python"}, "identify"),
        ({"user_agent": "ParkFree/0.1\nBad"}, "line breaks"),
        ({"user_agent": "ParkFree/0.1", "timeout_seconds": 11.0}, "timeout_seconds"),
        ({"user_agent": "ParkFree/0.1", "cache_ttl_seconds": 86_401.0}, "cache_ttl"),
        ({"user_agent": "ParkFree/0.1", "cache_max_entries": 1_025}, "cache_max"),
    ],
)
def test_adapter_configuration_is_bounded(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        NominatimGeocoder(RecordingTransport([[]]), **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "search_url",
    [
        "",
        "ftp://example.com/search",
        "https:///search",
        "https://user:secret@example.com/search",
        "https://example.com/search?q=user-input",
        "https://example.com/search#fragment",
        "https://example.com\\@evil.example/search",
        "https://example.com/search\n",
        "https://example.com:invalid/search",
    ],
)
def test_operator_search_url_rejects_ambiguous_or_parameterized_values(search_url: str) -> None:
    with pytest.raises(ValueError, match="search_url"):
        NominatimGeocoder(
            RecordingTransport([[]]),
            user_agent="ParkFree/0.1",
            search_url=search_url,
        )


def test_naive_wall_clock_fails_closed() -> None:
    adapter = geocoder(
        RecordingTransport([[nominatim_item()]]),
        wall_clock=lambda: datetime(2026, 9, 26, 12, 0),
    )

    with pytest.raises(GeocodingResponseError, match="wall clock"):
        adapter.geocode(GeocodingRequest(query="Fondren"))
