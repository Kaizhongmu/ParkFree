"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const RING_CIRCUMFERENCE = 2 * Math.PI * 50;
const form = document.querySelector("#search-form");
const statusBox = document.querySelector("#request-status");
const submitButton = document.querySelector("#submit-button");
const instantSubmitButton = document.querySelector("#instant-submit-button");
const arrivalNow = document.querySelector("#arrival-now");
const arrivalField = document.querySelector("#arrival-time-field");
const arrivalInput = document.querySelector("#arrival-time");
const mapContent = document.querySelector("#map-content");
const mapShell = document.querySelector(".map-shell");
const mapEmpty = document.querySelector("#map-empty");
const mapEmptyNote = document.querySelector("#map-empty-note");
const mapDescription = document.querySelector("#map-description");
const candidateList = document.querySelector("#candidate-list");
const resultsPanel = document.querySelector(".results-panel");
const runtimeNotice = document.querySelector("#runtime-notice");
const destinationInput = document.querySelector("#destination");
const destinationSearchButton = document.querySelector("#destination-search-button");
const destinationSearchStatus = document.querySelector("#destination-search-status");
const destinationResults = document.querySelector("#destination-results");
const destinationMatchList = document.querySelector("#destination-match-list");
const destinationAttribution = document.querySelector("#destination-attribution");
const selectedDestinationPanel = document.querySelector("#selected-destination");
const coverageStatusBanner = document.querySelector("#coverage-status-banner");
const evaluatedLegend = document.querySelector("#evaluated-legend");
const provisionalLegend = document.querySelector("#provisional-legend");
const researchActivity = document.querySelector("#research-activity");
const researchSourceList = document.querySelector("#research-source-list");
const researchState = document.querySelector("#research-state");
const maxWalkInput = document.querySelector("#max-walk");
const maxWalkOutput = document.querySelector("#max-walk-output");

let activeSegmentId = null;
let activeSearch = null;
let activeDestinationSearch = null;
let selectedDestination = null;
let hasCurrentPlan = false;
let currentView = null;

form.addEventListener("input", () => {
  invalidatePendingSearch("Search settings changed. Submit again to build a current plan.");
});

maxWalkInput.addEventListener("input", () => {
  maxWalkOutput.value = `${maxWalkInput.value} min`;
});

destinationInput.addEventListener("input", () => {
  invalidateDestinationSearch();
  invalidatePendingSearch();
  clearDestinationDiscovery();
  showDestinationCoverageGate();
});

arrivalNow.addEventListener("change", () => {
  arrivalField.hidden = arrivalNow.checked;
  arrivalInput.required = !arrivalNow.checked;
});

document.querySelector("#locate-button").addEventListener("click", () => {
  const help = document.querySelector("#location-help");
  if (!navigator.geolocation) {
    help.textContent = "This browser does not provide location access. Enter coordinates manually.";
    return;
  }
  help.textContent = "Requesting your browser location…";
  navigator.geolocation.getCurrentPosition(
    (position) => {
      invalidatePendingSearch("Location changed. Submit again to build a current plan.");
      document.querySelector("#origin-lat").value = position.coords.latitude.toFixed(6);
      document.querySelector("#origin-lon").value = position.coords.longitude.toFixed(6);
      help.textContent = "Location added. It will be sent only when you submit this search.";
    },
    () => {
      help.textContent = "Location was unavailable. Enter coordinates manually.";
    },
    { enableHighAccuracy: false, maximumAge: 60000, timeout: 8000 },
  );
});

destinationSearchButton.addEventListener("click", async () => {
  const query = destinationInput.value.replace(/\s+/g, " ").trim();
  if (query.length < 2) {
    destinationInput.setCustomValidity("Enter at least two characters to find a US place.");
    destinationInput.reportValidity();
    destinationInput.setCustomValidity("");
    return;
  }

  invalidateDestinationSearch();
  invalidatePendingSearch("Destination search changed. Choose a new match and estimate mode.");
  clearDestinationDiscovery();
  destinationSearchButton.disabled = true;
  destinationSearchButton.textContent = "Finding places…";
  destinationSearchStatus.textContent = "Searching US destinations…";
  const search = { controller: new AbortController() };
  activeDestinationSearch = search;
  try {
    const response = await fetch("/v1/destinations/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
      signal: search.controller.signal,
    });
    const body = await response.json().catch(() => ({}));
    if (activeDestinationSearch !== search) return;
    if (!response.ok) throw new Error(readDestinationError(body, response.status));
    renderDestinationDiscovery(normalizeDestinationDiscovery(body), query);
  } catch (error) {
    if (activeDestinationSearch !== search || error?.name === "AbortError") return;
    const message = error instanceof Error ? error.message : "Destination search failed.";
    destinationSearchStatus.textContent = message;
    destinationSearchStatus.classList.add("error");
  } finally {
    if (activeDestinationSearch === search) {
      activeDestinationSearch = null;
      destinationSearchButton.disabled = false;
      destinationSearchButton.textContent = "Find this US place";
    }
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  if (!selectedDestination) {
    setStatus("Find and select the intended US destination first.", true);
    return;
  }
  const requestedMode = event.submitter?.dataset.researchMode === "RESEARCH"
    ? "RESEARCH" : "INSTANT";
  await runOnDemandParking(requestedMode);
});

function invalidateDestinationSearch() {
  if (!activeDestinationSearch) return;
  const staleSearch = activeDestinationSearch;
  activeDestinationSearch = null;
  staleSearch.controller.abort();
  destinationSearchButton.disabled = false;
  destinationSearchButton.textContent = "Find this US place";
}

function clearDestinationDiscovery() {
  selectedDestination = null;
  destinationMatchList.replaceChildren();
  destinationAttribution.textContent = "";
  destinationResults.hidden = true;
  selectedDestinationPanel.hidden = true;
  destinationSearchStatus.classList.remove("error");
}

function showDestinationCoverageGate() {
  setLoading(false);
  hasCurrentPlan = false;
  activeSegmentId = null;
  currentView = null;
  mapContent.replaceChildren();
  setMapEmptyVisibility(true);
  document.querySelector("#result-time").textContent = "No current plan";
  document.querySelector("#result-mode").textContent = "No estimate yet";
  document.querySelector("#results-title").textContent = "Choose a destination";
  document.querySelector("#result-subtitle").textContent =
    "Your live parking plan will appear here.";
  setProvisionalPresentation(false);
  clearResearchActivity();
  ["#summary-cards", "#route-section", "#candidate-section", "#warning-section"]
    .forEach((selector) => {
      document.querySelector(selector).hidden = true;
    });
  mapEmpty.textContent = "Find and select the intended US destination";
  mapEmptyNote.textContent = "Select a match, then choose fast or enhanced API research";
  mapDescription.textContent = "No destination has been selected for on-demand research.";
  document.querySelector("#map-source-caption").textContent =
    "Destination not selected · live coverage not requested";
  destinationSearchStatus.textContent =
    "Search for a US place, select the intended match, then choose an estimate mode.";
  resetCandidateDetail("Run a search to inspect curb evidence.");
}

function normalizeDestinationDiscovery(body) {
  if (!body || !["NO_MATCH", "UNIQUE", "AMBIGUOUS"].includes(body.status)) {
    throw new Error("Destination search returned an invalid response.");
  }
  if (!Array.isArray(body.matches) || !body.metadata) {
    throw new Error("Destination search returned an invalid response.");
  }
  const matches = body.matches.map(normalizeDestinationMatch);
  const expectedStatus = matches.length === 0
    ? "NO_MATCH"
    : matches.length === 1 ? "UNIQUE" : "AMBIGUOUS";
  if (body.status !== expectedStatus || typeof body.metadata.attribution !== "string") {
    throw new Error("Destination search returned an invalid response.");
  }
  return {
    status: body.status,
    matches,
    attribution: body.metadata.attribution,
    providerName: typeof body.metadata.provider_name === "string"
      ? body.metadata.provider_name
      : "destination provider",
    cacheHit: body.cache_hit === true,
  };
}

function normalizeDestinationMatch(match) {
  const coordinate = [match?.location?.longitude, match?.location?.latitude];
  if (
    typeof match?.match_id !== "string"
    || typeof match?.name !== "string"
    || typeof match?.formatted_address !== "string"
    || !validCoordinate(coordinate)
  ) {
    throw new Error("Destination search returned an invalid place candidate.");
  }
  return {
    matchId: match.match_id,
    name: match.name,
    formattedAddress: match.formatted_address,
    latitude: coordinate[1],
    longitude: coordinate[0],
  };
}

function renderDestinationDiscovery(discovery, query) {
  destinationSearchStatus.classList.remove("error");
  if (discovery.status === "NO_MATCH") {
    destinationSearchStatus.textContent =
      "No US place matched. Add a city, state, or ZIP code and try again.";
    destinationAttribution.textContent = `Search source: ${discovery.attribution}`;
    destinationResults.hidden = false;
    return;
  }

  destinationSearchStatus.textContent = discovery.status === "AMBIGUOUS"
    ? `Found ${discovery.matches.length} possible places. Choose the intended one.`
    : "One place matched. Select it to confirm the coordinates.";
  destinationMatchList.replaceChildren(
    ...discovery.matches.map((match) => destinationMatchButton({ ...match, query })),
  );
  destinationAttribution.textContent =
    `Search source: ${discovery.attribution} · ${discovery.providerName}`
    + (discovery.cacheHit ? " · cached result" : "");
  destinationResults.hidden = false;
  destinationMatchList.querySelector("button")?.focus();
}

function destinationMatchButton(match) {
  const item = element("li", "destination-match");
  const button = element("button", "destination-match-button");
  button.type = "button";
  button.dataset.matchId = match.matchId;
  button.setAttribute("aria-pressed", "false");
  button.append(
    element("strong", "", match.name),
    element("span", "", match.formattedAddress),
    element(
      "span",
      "destination-coordinate",
      `${formatNumber(match.latitude, 5)}, ${formatNumber(match.longitude, 5)}`,
    ),
  );
  button.addEventListener("click", () => selectDestinationMatch(match));
  item.append(button);
  return item;
}

function selectDestinationMatch(match) {
  selectedDestination = match;
  destinationInput.value = match.name;
  invalidatePendingSearch("Destination changed. Choose an estimate mode for the new place.");
  document.querySelectorAll(".destination-match-button").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.matchId === match.matchId));
  });
  document.querySelector("#selected-destination-name").textContent = match.formattedAddress;
  document.querySelector("#selected-destination-coordinates").textContent =
    `Coordinates: ${formatNumber(match.latitude, 6)}, ${formatNumber(match.longitude, 6)}`;
  selectedDestinationPanel.hidden = false;
  setLoading(false);
  renderDiscoveredDestination(match);
  if (form.checkValidity()) {
    destinationSearchStatus.textContent =
      "Place selected. Choose Estimate now or Research APIs, then estimate.";
    setStatus("Destination ready. Choose one of the two estimate modes.", false);
  } else {
    destinationSearchStatus.textContent =
      "Place selected. Correct the highlighted search fields, then build the parking plan.";
    setStatus("Place selected, but the current search settings need attention.", true);
  }
}

async function runOnDemandParking(researchMode) {
  if (!selectedDestination) return;
  if (!["INSTANT", "RESEARCH"].includes(researchMode)) {
    throw new Error("A valid estimate mode is required.");
  }
  invalidatePendingSearch();
  clearResults();
  setLoading(true);
  instantSubmitButton.setAttribute("aria-pressed", String(researchMode === "INSTANT"));
  submitButton.setAttribute("aria-pressed", String(researchMode === "RESEARCH"));
  document.querySelector("#result-mode").textContent = researchMode === "RESEARCH"
    ? "Research in progress"
    : "Instant estimate in progress";
  setStatus(
    researchMode === "RESEARCH"
      ? "Calling enhanced road and parking-tag APIs before estimating…"
      : "Fetching a fast official road snapshot before estimating…",
    false,
  );
  const search = { controller: new AbortController(), mode: researchMode };
  activeSearch = search;
  try {
    const response = await fetch("/v1/parking/on-demand", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildOnDemandRequest(selectedDestination, researchMode)),
      signal: search.controller.signal,
    });
    const body = await response.json().catch(() => ({}));
    if (activeSearch !== search) return;
    if (!response.ok) throw new Error(readOnDemandError(body, response.status));
    renderOnDemandCoverage(body, researchMode);
  } catch (error) {
    if (activeSearch !== search || error?.name === "AbortError") return;
    const message = error instanceof Error ? error.message : "On-demand research failed.";
    markSearchFailed();
    setStatus(message, true);
  } finally {
    if (activeSearch === search) {
      activeSearch = null;
      setLoading(false);
    }
  }
}

function buildOnDemandRequest(match, researchMode) {
  const request = buildRequest();
  return {
    ...request,
    destination: { query: match.query, match_id: match.matchId },
    research_mode: researchMode,
  };
}

function readOnDemandError(body, code) {
  if (code === 409) return "That place selection changed upstream. Find and select it again.";
  if (code === 503) return "On-demand road research is not configured or is unavailable.";
  if (code === 422) return "Check the search settings and try the on-demand research again.";
  return typeof body.detail === "string" ? body.detail : "On-demand research failed.";
}

function renderOnDemandCoverage(result, requestedMode) {
  const segments = Array.isArray(result.candidate_segments) ? result.candidate_segments : [];
  const predictions = Array.isArray(result.availability_predictions)
    ? result.availability_predictions
    : [];
  const status = result.status;
  if (
    !result.destination
    || !["PROVISIONAL_LEADS", "NO_CANDIDATES", "PROVIDER_UNAVAILABLE"].includes(status)
    || !["INSTANT", "RESEARCH"].includes(result.research_mode)
    || result.research_mode !== requestedMode
    || !["NOT_REQUESTED", "APPLIED", "DEGRADED", "NOT_CONFIGURED", "FAILED"]
      .includes(result.enrichment_status)
  ) {
    throw new Error("On-demand research returned an invalid response.");
  }
  hasCurrentPlan = status === "PROVISIONAL_LEADS";
  document.querySelector("#results-title").textContent = result.destination.name;
  document.querySelector("#result-subtitle").textContent =
    `${result.destination_timezone || "Local timezone unresolved"} · live provisional coverage`;
  document.querySelector("#result-mode").textContent = result.research_mode === "RESEARCH"
    ? "Research snapshot"
    : "Instant snapshot";
  document.querySelector("#result-time").textContent = status === "PROVISIONAL_LEADS"
    ? "Live road coverage · provisional"
    : "No provisional candidates";

  if (status !== "PROVISIONAL_LEADS") {
    if (status === "PROVIDER_UNAVAILABLE") {
      markSearchFailed();
    } else {
      markNoCandidates();
    }
    renderResearchActivity(result);
    renderWarnings(result.warnings || []);
    setStatus(
      status === "PROVIDER_UNAVAILABLE"
        ? result.research_mode === "INSTANT"
          ? "The fast official-road source failed. This does not mean parking is unavailable."
          : "Enhanced research was attempted, but every configured road API failed. This does not mean parking is unavailable."
        : "No eligible road candidates were found in the bounded live snapshot.",
      status === "PROVIDER_UNAVAILABLE",
    );
    return;
  }

  const evaluatedAt = result.resolved_arrival_time;
  const predictionBySegment = new Map();
  predictions.forEach((prediction) => {
    if (typeof prediction?.segment_id !== "string" || predictionBySegment.has(prediction.segment_id)) {
      throw new Error("On-demand research returned invalid availability estimates.");
    }
    predictionBySegment.set(prediction.segment_id, prediction);
  });
  const decisions = segments.map((segment) => ({
    segment,
    legality: {
      segment_id: segment.segment_id,
      legal_state: "UNKNOWN",
      free_state: "UNKNOWN",
      confidence: 0,
      evidence_refs: segment.evidence_refs || [],
      reason_codes: ["INSUFFICIENT_EVIDENCE"],
      evaluated_at: evaluatedAt,
    },
    availability: predictionBySegment.get(segment.segment_id) || null,
    availability_is_conditional: predictionBySegment.has(segment.segment_id),
    eligible: false,
    exclusion_reason: "UNKNOWN_LEGALITY",
  }));
  const view = {
    destination: result.destination,
    candidate_decisions: decisions,
    route: { steps: [] },
    fallback: { location: null },
    provisional_mode: true,
    source_result: result,
  };
  currentView = view;
  renderMap(view);
  renderCandidates(view);
  renderOnDemandSummary(result, segments.length);
  renderResearchActivity(result);
  setProvisionalPresentation(true);
  document.querySelector("#route-section").hidden = true;
  renderWarnings(result.warnings || []);
  document.querySelector("#map-source-caption").textContent =
    `${(result.attribution || []).join(" · ")} · live provisional coverage`;
  const usedFallback = (result.provider_attempts || []).some(
    (attempt) => attempt.role === "FALLBACK" && attempt.outcome === "SUCCEEDED",
  );
  setStatus(
    predictions.length
      ? `${usedFallback ? "The enhanced source did not produce usable coverage; Census fallback found" : "Found"} ${segments.length} nearby curb leads in proximity order. The ${humanize(result.research_mode)} mode vacancy numbers are uncalibrated conditional priors; legality and free status remain UNKNOWN.`
      : `${usedFallback ? "Census fallback found" : "Found"} ${segments.length} nearby curb leads from request-time road APIs. Rules and free status remain UNKNOWN until trustworthy evidence is available.`,
    false,
  );
}

function renderOnDemandSummary(result, candidateCount) {
  const container = document.querySelector("#summary-cards");
  container.replaceChildren();
  const coverage = result.coverage || {};
  const probabilities = (result.availability_predictions || [])
    .map((prediction) => Number(prediction.probability))
    .filter(Number.isFinite);
  const values = [
    ["Requested mode", humanize(result.research_mode)],
    ["API enrichment", humanize(result.enrichment_status)],
    ["Curb leads", String(candidateCount)],
    ["Roads fetched", String(coverage.road_count ?? 0)],
    ["Parking-tagged roads", String(coverage.tagged_road_count ?? 0)],
    ["Road source", coverage.metadata?.provider_name || "Not reported"],
    ["Highest V0 prior", probabilities.length ? formatPercent(Math.max(...probabilities)) : "Not estimated"],
    ["Model basis", result.calibration_status === "UNCALIBRATED_HEURISTIC" ? "Uncalibrated" : "Not reported"],
    ["Destination timezone", result.destination_timezone || "Not resolved"],
  ];
  values.forEach(([label, value]) => {
    const card = element("div", "summary-card");
    card.append(element("span", "", label), element("strong", "", value));
    container.append(card);
  });
  container.hidden = false;
}

function renderDiscoveredDestination(match) {
  hasCurrentPlan = false;
  activeSegmentId = null;
  currentView = null;
  mapContent.replaceChildren();
  setMapEmptyVisibility(false);
  const coordinate = [match.longitude, match.latitude];
  const [x, y] = makeProjection([coordinate])(coordinate);
  mapContent.append(
    svgElement("circle", {
      cx: x, cy: y, r: "12", class: "map-discovered-destination",
    }),
    svgElement("text", {
      x: x + 18, y: y - 14, class: "map-label",
    }, match.name),
  );
  mapDescription.textContent =
    `${match.name} was selected. Choose a fast estimate or enhanced API research.`;
  document.querySelector("#map-source-caption").textContent =
    `${destinationAttribution.textContent} · estimate mode not selected`;
  document.querySelector("#result-time").textContent = "Place selected · choose a mode";
  document.querySelector("#result-mode").textContent = "Ready to estimate";
  document.querySelector("#results-title").textContent = match.name;
  document.querySelector("#result-subtitle").textContent = match.formattedAddress;
  ["#summary-cards", "#route-section", "#candidate-section", "#warning-section"]
    .forEach((selector) => {
      document.querySelector(selector).hidden = true;
    });
  centerMapViewport();
  resetCandidateDetail("Choose Instant or Research to inspect curb evidence.");
}

function readDestinationError(body, code) {
  if (code === 503) {
    return "Place search is not configured or is temporarily unavailable.";
  }
  if (code === 422) return "Check the destination name and try again.";
  return typeof body.detail === "string" ? body.detail : "Destination search failed. Try again.";
}

function invalidatePendingSearch(message = null) {
  const hadCurrentSearchOrPlan = activeSearch !== null || hasCurrentPlan;
  if (activeSearch) {
    const staleSearch = activeSearch;
    activeSearch = null;
    staleSearch.controller.abort();
    setLoading(false);
  }
  if (message && hadCurrentSearchOrPlan) {
    markSearchChanged();
    setStatus(message, false);
  }
}

function buildRequest() {
  const permits = document.querySelector("#permits").value
    .split(",")
    .map((permit) => permit.trim())
    .filter(Boolean);
  return {
    origin: {
      lat: Number(document.querySelector("#origin-lat").value),
      lon: Number(document.querySelector("#origin-lon").value),
    },
    arrival_time: arrivalNow.checked ? "now" : new Date(arrivalInput.value).toISOString(),
    parking_duration_minutes: Number(document.querySelector("#duration").value),
    free_only: document.querySelector("#free-only").checked,
    max_walk_minutes: Number(document.querySelector("#max-walk").value),
    vehicle_profile: {
      type: document.querySelector("#vehicle-type").value.trim(),
      permit_types: permits,
    },
    max_candidates: Number(document.querySelector("#max-candidates").value),
  };
}

function setLoading(isLoading) {
  const disabled = isLoading || selectedDestination === null;
  instantSubmitButton.disabled = disabled;
  submitButton.disabled = disabled;
  form.setAttribute("aria-busy", String(isLoading));
  resultsPanel.setAttribute("aria-busy", String(isLoading));
}

function setStatus(message, isError) {
  statusBox.classList.toggle("error", isError);
  statusBox.setAttribute("role", isError ? "alert" : "status");
  statusBox.setAttribute("aria-live", isError ? "assertive" : "polite");
  statusBox.setAttribute("aria-atomic", "true");
  statusBox.textContent = message;
}

function clearResults() {
  hasCurrentPlan = false;
  activeSegmentId = null;
  currentView = null;
  mapContent.replaceChildren();
  setMapEmptyVisibility(true);
  mapEmpty.textContent = "Building a fresh candidate map…";
  mapEmptyNote.textContent = "Previous results have been cleared";
  document.querySelector("#result-time").textContent = "Searching…";
  setProvisionalPresentation(false);
  clearResearchActivity();
  ["#summary-cards", "#route-section", "#candidate-section", "#warning-section"]
    .forEach((selector) => {
      document.querySelector(selector).hidden = true;
    });
  resetCandidateDetail("Building a fresh candidate sequence.");
}

function markSearchFailed() {
  hasCurrentPlan = false;
  currentView = null;
  mapContent.replaceChildren();
  ["#summary-cards", "#route-section", "#candidate-section", "#warning-section"]
    .forEach((selector) => {
      document.querySelector(selector).hidden = true;
    });
  document.querySelector("#result-time").textContent = "No current plan";
  setProvisionalPresentation(false);
  clearResearchActivity();
  mapEmpty.textContent = "Search did not complete";
  mapEmptyNote.textContent = "Review the message beside the search form, then try again";
  setMapEmptyVisibility(true);
  resetCandidateDetail("The search did not complete.");
}

function markNoCandidates() {
  hasCurrentPlan = false;
  activeSegmentId = null;
  currentView = null;
  mapContent.replaceChildren();
  ["#summary-cards", "#route-section", "#candidate-section", "#warning-section"]
    .forEach((selector) => {
      document.querySelector(selector).hidden = true;
    });
  document.querySelector("#result-time").textContent = "Search complete · no candidates";
  setProvisionalPresentation(false);
  clearResearchActivity();
  mapEmpty.textContent = "No curb leads found in this road snapshot";
  mapEmptyNote.textContent = "This does not prove that parking is unavailable";
  setMapEmptyVisibility(true);
  resetCandidateDetail("No curb candidates were returned.");
}

function markSearchChanged() {
  hasCurrentPlan = false;
  activeSegmentId = null;
  currentView = null;
  mapContent.replaceChildren();
  ["#summary-cards", "#route-section", "#candidate-section", "#warning-section"]
    .forEach((selector) => {
      document.querySelector(selector).hidden = true;
    });
  document.querySelector("#result-time").textContent = "No current plan";
  setProvisionalPresentation(false);
  clearResearchActivity();
  mapEmpty.textContent = "Search settings changed";
  mapEmptyNote.textContent = "Submit again to build a plan for the current inputs";
  setMapEmptyVisibility(true);
  resetCandidateDetail("Search settings changed. Submit again.");
}

function renderSearch(result) {
  hasCurrentPlan = true;
  activeSegmentId = null;
  currentView = result;
  document.querySelector("#results-title").textContent = result.destination.name;
  document.querySelector("#result-subtitle").textContent = "Evidence-backed parking plan";
  document.querySelector("#result-mode").textContent = "Evaluated plan";
  document.querySelector("#result-time").textContent = formatDate(result.resolved_arrival_time);
  setProvisionalPresentation(false);
  clearResearchActivity();
  renderSummary(result);
  renderMap(result);
  renderRoute(result);
  renderCandidates(result);
  renderWarnings(result.warnings || []);
}

function renderSummary(result) {
  const container = document.querySelector("#summary-cards");
  container.replaceChildren();
  const route = result.route;
  const values = [
    ["Expected time", `${formatNumber(route.expected_time_to_park_min, 1)} min`],
    ["Route success", formatPercent(route.success_probability)],
    ["Unknown curbs", String((result.unknown_segment_ids || []).length)],
  ];
  values.forEach(([label, value]) => {
    const card = element("div", "summary-card");
    card.append(element("span", "", label), element("strong", "", value));
    container.append(card);
  });
  container.hidden = false;
}

function renderMap(result) {
  mapContent.replaceChildren();
  setMapEmptyVisibility(false);
  const decisions = result.candidate_decisions || [];
  const routeOrder = candidateOrderMap(result);
  mapDescription.textContent = result.provisional_mode
    ? `${decisions.length} provisional curb leads numbered in proximity order. Conditional vacancy does not verify legality or price.`
    : `${decisions.length} evaluated parking curbs with ${routeOrder.size} recommended route stops.`;
  const coordinates = decisions.flatMap((decision) => coordinatesForDecision(decision));
  const destinationCoordinate = [
    result.destination.location.longitude,
    result.destination.location.latitude,
  ];
  if (validCoordinate(destinationCoordinate)) coordinates.push(destinationCoordinate);
  const project = makeProjection(coordinates);

  const routeCenters = new Map(
    decisions.map((decision) => {
      const points = coordinatesForDecision(decision).map(project);
      return [decision.segment.segment_id, points[Math.floor(points.length / 2)]];
    }).filter(([, point]) => point),
  );
  const routeGuidePoints = (result.route.steps || [])
    .map((step) => routeCenters.get(step.segment_id))
    .filter(Boolean);
  if (routeGuidePoints.length > 1) {
    mapContent.append(svgElement("polyline", {
      points: routeGuidePoints.map(([x, y]) => `${x},${y}`).join(" "),
      class: "route-guide",
      "aria-label": "Schematic connection between recommended route stops",
    }));
  }

  decisions.forEach((decision) => {
    const points = offsetProjectedPoints(
      coordinatesForDecision(decision).map(project),
      decision.segment.side,
    );
    if (points.length < 2) return;
    const segmentGroup = svgElement("g", {
      class: "map-segment-group",
      tabindex: "0",
      role: "button",
      "aria-pressed": "false",
      "aria-label": candidateAriaLabel(decision, routeOrder.get(decision.segment.segment_id)),
      "data-segment-id": decision.segment.segment_id,
    });
    const pathPoints = points.map(([x, y]) => `${x},${y}`).join(" ");
    const hitPath = svgElement("polyline", {
      points: pathPoints,
      class: "map-segment-hit",
      "aria-hidden": "true",
    });
    const visiblePath = svgElement("polyline", {
      points: pathPoints,
      class: `map-segment ${decisionClass(decision)}`,
      "aria-hidden": "true",
    });
    segmentGroup.append(hitPath, visiblePath);
    segmentGroup.addEventListener("click", () => selectSegment(decision.segment.segment_id));
    segmentGroup.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectSegment(decision.segment.segment_id);
      }
    });
    mapContent.append(segmentGroup);

    const order = routeOrder.get(decision.segment.segment_id);
    if (order) {
      const [x, y] = points[Math.floor(points.length / 2)];
      mapContent.append(
        svgElement("circle", { cx: x, cy: y, r: "13", class: "route-marker" }),
        svgElement("text", { x, y, class: "route-number" }, String(order)),
      );
    }
  });

  const destinationPoint = project(destinationCoordinate);
  mapContent.append(
    svgElement("circle", {
      cx: destinationPoint[0], cy: destinationPoint[1], r: "10", class: "map-destination",
    }),
    svgElement("text", {
      x: destinationPoint[0] + 15, y: destinationPoint[1] - 12, class: "map-label",
    }, result.destination.name),
  );

  if (result.fallback.location) {
    const fallbackCoordinate = [
      result.fallback.location.longitude,
      result.fallback.location.latitude,
    ];
    const [x, y] = validCoordinate(fallbackCoordinate)
      ? project(fallbackCoordinate)
      : [Number.NaN, Number.NaN];
    if (Number.isFinite(x) && x >= 25 && x <= 875 && y >= 25 && y <= 555) {
      mapContent.append(
        svgElement("rect", {
          x: x - 9, y: y - 9, width: "18", height: "18", rx: "3",
          class: "map-fallback", transform: `rotate(45 ${x} ${y})`,
        }),
        svgElement("text", { x: x + 15, y: y + 4, class: "map-label" }, "Fallback"),
      );
    }
  }
  centerMapViewport();
}

function candidateOrderMap(result) {
  const routeSteps = result.route?.steps || [];
  if (routeSteps.length) {
    return new Map(routeSteps.map((step, index) => [step.segment_id, index + 1]));
  }
  if (result.provisional_mode) {
    return new Map(
      (result.candidate_decisions || []).map((decision, index) => [
        decision.segment.segment_id,
        index + 1,
      ]),
    );
  }
  return new Map();
}

function setMapEmptyVisibility(isVisible) {
  const visibility = isVisible ? "visible" : "hidden";
  mapEmpty.setAttribute("visibility", visibility);
  mapEmptyNote.setAttribute("visibility", visibility);
  mapEmpty.setAttribute("aria-hidden", String(!isVisible));
  mapEmptyNote.setAttribute("aria-hidden", String(!isVisible));
}

function centerMapViewport() {
  mapShell.scrollLeft = Math.max(0, (mapShell.scrollWidth - mapShell.clientWidth) / 2);
}

function makeProjection(coordinates) {
  const safeCoordinates = coordinates.filter(validCoordinate);
  if (safeCoordinates.length === 0) safeCoordinates.push([0, 0]);
  const xs = safeCoordinates.map(([x]) => x);
  const ys = safeCoordinates.map(([, y]) => y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const width = Math.max(maxX - minX, 0.0005);
  const height = Math.max(maxY - minY, 0.0005);
  const projectionMinX = (minX + maxX - width) / 2;
  const projectionMinY = (minY + maxY - height) / 2;
  const scale = Math.min(780 / width, 460 / height);
  const drawWidth = width * scale;
  const drawHeight = height * scale;
  const offsetX = (900 - drawWidth) / 2;
  const offsetY = (580 - drawHeight) / 2;
  return ([longitude, latitude]) => [
    offsetX + (longitude - projectionMinX) * scale,
    offsetY + (projectionMinY + height - latitude) * scale,
  ];
}

function coordinatesForDecision(decision) {
  const coordinates = decision?.segment?.geometry?.coordinates;
  return Array.isArray(coordinates) ? coordinates.filter(validCoordinate) : [];
}

function validCoordinate(coordinate) {
  if (!Array.isArray(coordinate) || coordinate.length !== 2) return false;
  const [longitude, latitude] = coordinate;
  return Number.isFinite(longitude) && Number.isFinite(latitude)
    && longitude >= -180 && longitude <= 180
    && latitude >= -90 && latitude <= 90;
}

function offsetProjectedPoints(points, side) {
  if (points.length < 2 || (side !== "LEFT" && side !== "RIGHT")) return points;
  const [startX, startY] = points[0];
  const [endX, endY] = points[points.length - 1];
  const deltaX = endX - startX;
  const deltaY = endY - startY;
  const length = Math.hypot(deltaX, deltaY);
  if (length === 0) return points;
  const direction = side === "LEFT" ? 1 : -1;
  const offset = 12 * direction;
  const offsetX = (deltaY / length) * offset;
  const offsetY = (-deltaX / length) * offset;
  return points.map(([x, y]) => [x + offsetX, y + offsetY]);
}

function renderRoute(result) {
  const section = document.querySelector("#route-section");
  const list = document.querySelector("#route-list");
  list.replaceChildren();
  const decisionById = new Map(
    result.candidate_decisions.map((decision) => [decision.segment.segment_id, decision]),
  );
  (result.route.steps || []).forEach((step, index) => {
    const decision = decisionById.get(step.segment_id);
    const item = element("li", "route-item");
    item.append(element("span", "route-order", String(index + 1)));
    const copy = element("div");
    copy.append(element("p", "route-name", segmentName(decision?.segment, step.segment_id)));
    const meta = element("p", "route-meta");
    meta.append(
      element("span", "", `${formatNumber(step.drive_eta_min, 1)} min drive`),
      element("span", "", `${formatNumber(step.walk_min, 1)} min walk`),
      element("span", "", `${step.free_state.toLowerCase()} parking`),
      element("span", "", evidenceLabel(step.evidence_refs)),
    );
    copy.append(meta);
    item.append(copy);
    item.append(element("span", "route-probability", formatPercent(step.availability_probability)));
    list.append(item);
  });
  if (!result.route.steps.length) {
    list.append(element("li", "route-item", "No eligible curb was recommended."));
  }

  document.querySelector("#route-model").textContent =
    `${result.versions.optimizer} · ${result.versions.availability_model}`;
  const fallback = document.querySelector("#fallback-card");
  const fallbackContent = [
    element("h4", "", "Configured fallback"),
    element("p", "", result.fallback.description),
  ];
  if (result.fallback.location) {
    fallbackContent.push(element(
      "p",
      "fallback-location",
      `Location: ${formatNumber(result.fallback.location.latitude, 5)}, ${formatNumber(result.fallback.location.longitude, 5)}`,
    ));
  }
  fallback.replaceChildren(...fallbackContent);
  section.hidden = false;
}

function renderCandidates(result) {
  candidateList.replaceChildren();
  const routeOrder = candidateOrderMap(result);
  const orderedDecisions = [...result.candidate_decisions];
  if (!result.provisional_mode && routeOrder.size) {
    orderedDecisions.sort((left, right) => {
      const leftOrder = routeOrder.get(left.segment.segment_id) ?? Number.POSITIVE_INFINITY;
      const rightOrder = routeOrder.get(right.segment.segment_id) ?? Number.POSITIVE_INFINITY;
      return leftOrder - rightOrder
        || left.segment.segment_id.localeCompare(right.segment.segment_id);
    });
  }
  orderedDecisions.forEach((decision) => {
    const id = decision.segment.segment_id;
    const stateClass = decisionClass(decision);
    const card = element("article", `candidate-card ${stateClass}`);
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-pressed", "false");
    card.dataset.segmentId = id;
    card.setAttribute("aria-label", candidateAriaLabel(decision, routeOrder.get(id)));
    card.addEventListener("click", () => selectSegment(id));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectSegment(id);
      }
    });
    const order = routeOrder.get(id);
    const orderBadge = element("span", "candidate-order", order ? String(order) : "—");
    const copy = element("div", "candidate-copy");
    const top = element("div", "candidate-top");
    top.append(
      element("p", "candidate-name", segmentName(decision.segment, id)),
      element("span", `state-pill ${stateClass}`, stateLabel(decision)),
    );
    const meta = element("p", "candidate-meta");
    meta.append(
      element("span", "", `${formatNumber(decision.segment.length_m, 0)} m curb`),
      element("span", "", decision.segment.side.toLowerCase()),
      element("span", "", availabilityLabel(decision)),
      element(
        "span",
        "",
        `Curb usability ${humanize(decision.segment.physical_state || "UNKNOWN")}`,
      ),
      element("span", "", `${formatPercent(decision.legality.confidence)} evidence confidence`),
      element(
        "span",
        "",
        `${result.provisional_mode ? "Arrival" : "Evaluated"} ${formatDate(decision.legality.evaluated_at)}`,
      ),
    );
    copy.append(top, meta);
    const reasons = (decision.legality.reason_codes || []).map(humanize).join(", ");
    const provenance = element("p", "provenance");
    provenance.append(
      element("strong", "", "Evidence: "),
      document.createTextNode(evidenceLabel(decision.legality.evidence_refs)),
      document.createElement("br"),
      element("strong", "", "Why: "),
      document.createTextNode(reasons || "No reason supplied"),
      document.createElement("br"),
      element("strong", "", "Search status: "),
      document.createTextNode(
        decision.exclusion_reason ? humanize(decision.exclusion_reason) : "Eligible for route",
      ),
    );
    if (decision.availability_is_conditional && decision.availability) {
      provenance.append(
        document.createElement("br"),
        element("strong", "", "Prediction: "),
        document.createTextNode(
          `${decision.availability.model_version} · conditional on legal, usable curb`,
        ),
      );
      provenance.append(
        document.createElement("br"),
        element("strong", "", "Model basis: "),
        document.createTextNode(
          (decision.availability.reason_codes || []).map(humanize).join(", ")
            || "Uncalibrated heuristic prior",
        ),
      );
    }
    copy.append(provenance);
    const probability = decision.availability
      ? formatPercent(decision.availability.probability)
      : "Not estimated";
    card.append(
      orderBadge,
      copy,
      element("span", "candidate-probability", probability),
    );
    candidateList.append(card);
  });
  document.querySelector("#candidate-count").textContent = result.provisional_mode
    ? `${result.candidate_decisions.length} provisional · proximity order`
    : `${result.candidate_decisions.length} evaluated`;
  document.querySelector("#candidate-title").textContent = result.provisional_mode
    ? "Inspect in proximity order"
    : "Evaluated curb details";
  document.querySelector("#candidate-section").hidden = false;
  if (orderedDecisions.length) {
    activeSegmentId = orderedDecisions.some(
      (decision) => decision.segment.segment_id === activeSegmentId,
    )
      ? activeSegmentId
      : orderedDecisions[0].segment.segment_id;
    syncCandidateSelection();
  } else {
    resetCandidateDetail("No curb candidates were returned.");
  }
}

function renderWarnings(warnings) {
  const section = document.querySelector("#warning-section");
  const list = document.querySelector("#warning-list");
  list.replaceChildren();
  warnings.forEach((warning) => list.append(element("li", "", String(warning))));
  section.hidden = warnings.length === 0;
}

function renderResearchActivity(result) {
  const attempts = Array.isArray(result.provider_attempts) ? result.provider_attempts : [];
  const failed = attempts.filter((attempt) => attempt.outcome === "FAILED");
  const succeeded = attempts.filter((attempt) => attempt.outcome === "SUCCEEDED");
  const empty = attempts.filter((attempt) => attempt.outcome === "EMPTY");
  const fallbackSucceeded = attempts.some(
    (attempt) => attempt.role === "FALLBACK" && attempt.outcome === "SUCCEEDED",
  );
  const state = succeeded.length > 0
    ? fallbackSucceeded ? "Fallback used" : "Road data ready"
    : empty.length > 0 ? "No roads returned" : "Road sources failed";
  researchState.textContent = state;
  researchState.classList.toggle("error", succeeded.length === 0);
  researchState.classList.toggle("partial", fallbackSucceeded || empty.length > 0);
  researchSourceList.replaceChildren();
  researchSourceList.append(
    researchSourceItem(
      `Estimate mode: ${humanize(result.research_mode)}`,
      result.enrichment_status,
      result.research_mode === "INSTANT"
        ? "Used the fast official-road path; enhanced parking-tag research was not requested."
        : result.enrichment_status === "APPLIED"
          ? "The enhanced road and parking-tag source supplied the candidate snapshot."
          : result.enrichment_status === "DEGRADED"
            ? "Enhanced research was attempted, then safely fell back to the fast road source."
            : result.enrichment_status === "NOT_CONFIGURED"
              ? "Enhanced research is not configured; the fast road source was used."
              : "Enhanced research did not produce a usable road snapshot.",
    ),
    researchSourceItem(
      "Destination identity",
      "SUCCEEDED",
      "Selected place was revalidated through the destination API.",
    ),
  );
  attempts.forEach((attempt) => {
    const role = attempt.role === "FALLBACK" ? "Fallback road source" : "Primary road source";
    const detail = attempt.outcome === "SUCCEEDED"
      ? `${role} supplied the road snapshot used for this result.`
      : attempt.outcome === "EMPTY"
        ? `${role} returned no roads; the system continued to the next configured source.`
        : `${role} failed; the system continued to the next configured source.`;
    researchSourceList.append(
      researchSourceItem(attempt.provider_name || "Road coverage API", attempt.outcome, detail),
    );
  });
  researchSourceList.append(
    researchSourceItem(
      "Destination timezone",
      result.destination_timezone
        ? "SUCCEEDED"
        : result.status === "PROVIDER_UNAVAILABLE" ? "PENDING" : "FAILED",
      result.destination_timezone
        ? `${result.destination_timezone} was resolved offline for arrival-time features.`
        : result.status === "PROVIDER_UNAVAILABLE"
          ? "Not run because no road snapshot was available."
          : "Timezone resolution did not contribute to this result.",
    ),
    researchSourceItem(
      "Parking regulation evidence",
      "NOT_CONFIGURED",
      "No nationwide regulation API is configured; legality and price remain UNKNOWN.",
    ),
  );
  researchActivity.hidden = false;
}

function researchSourceItem(name, outcome, detail) {
  const className = outcome === "SUCCEEDED"
    ? "succeeded" : outcome === "FAILED" ? "failed" : "pending";
  const icon = outcome === "SUCCEEDED" ? "✓" : outcome === "FAILED" ? "!" : "—";
  const item = element("li", `research-source-item ${className}`);
  const copy = element("div");
  copy.append(
    element("p", "research-source-name", name),
    element("p", "research-source-detail", detail),
  );
  item.append(
    element("span", "research-source-icon", icon),
    copy,
    element("span", "research-source-outcome", humanize(outcome)),
  );
  return item;
}

function clearResearchActivity() {
  researchSourceList.replaceChildren();
  researchState.textContent = "";
  researchState.classList.remove("error", "partial");
  researchActivity.hidden = true;
}

function setProvisionalPresentation(isProvisional) {
  coverageStatusBanner.hidden = !isProvisional;
  evaluatedLegend.hidden = isProvisional;
  provisionalLegend.hidden = !isProvisional;
}

function selectSegment(segmentId) {
  activeSegmentId = segmentId;
  syncCandidateSelection();
  document.querySelector(
    `.candidate-card[data-segment-id="${CSS.escape(activeSegmentId)}"]`,
  )?.scrollIntoView({ block: "nearest", behavior: "auto" });
}

function syncCandidateSelection() {
  document.querySelectorAll("[data-segment-id]").forEach((node) => {
    const isActive = activeSegmentId === node.dataset.segmentId;
    node.classList.toggle("is-active", isActive);
    node.setAttribute("aria-pressed", String(isActive));
    if (node.classList.contains("map-segment-group")) {
      node.classList.toggle("is-muted", activeSegmentId !== null && !isActive);
    }
  });
  const decision = currentView?.candidate_decisions?.find(
    (candidate) => candidate.segment.segment_id === activeSegmentId,
  );
  const order = currentView ? candidateOrderMap(currentView).get(activeSegmentId) : null;
  renderCandidateDetail(decision, order);
}

function renderCandidateDetail(decision, order) {
  if (!decision) {
    resetCandidateDetail("Select a numbered curb candidate to inspect it.");
    return;
  }

  const segment = decision.segment;
  const stateClass = decisionClass(decision);
  const probability = decision.availability?.probability;
  document.querySelector("#detail-title").textContent = segmentName(segment, segment.segment_id);
  document.querySelector("#detail-side").textContent =
    `${humanize(segment.side)} curb · Segment ${segment.segment_id}`;
  document.querySelector("#detail-order").textContent = order ? `#${order}` : "Not routed";
  document.querySelector("#detail-length").textContent = `${formatNumber(segment.length_m, 0)} m`;
  document.querySelector("#detail-capacity").textContent = segment.estimated_capacity === null
    || segment.estimated_capacity === undefined
    ? "Unknown"
    : `${formatNumber(segment.estimated_capacity, 0)} spaces`;

  const statePill = document.querySelector("#detail-state");
  statePill.className = `state-pill ${stateClass}`;
  statePill.textContent = stateLabel(decision);
  updateDetailRing(probability);

  const evidenceItems = [];
  const evidenceRefs = decision.legality.evidence_refs || [];
  evidenceItems.push([
    evidenceRefs.length ? "Supporting evidence" : "Evidence coverage",
    evidenceLabel(evidenceRefs),
  ]);
  evidenceItems.push([
    "Regulation evaluation",
    (decision.legality.reason_codes || []).map(humanize).join(", ") || "No reason supplied",
  ]);
  evidenceItems.push([
    "Curb usability",
    humanize(segment.physical_state || "UNKNOWN"),
  ]);
  if (decision.availability) {
    const interval = decision.availability.interval;
    const intervalText = interval
      ? ` Range ${formatPercent(interval[0])} to ${formatPercent(interval[1])}.`
      : "";
    evidenceItems.push([
      "Availability basis",
      `${(decision.availability.reason_codes || []).map(humanize).join(", ") || "Conditional estimate"}.${intervalText}`,
    ]);
  } else {
    evidenceItems.push(["Availability basis", "No conditional vacancy estimate was returned."]);
  }
  document.querySelector("#detail-evidence").replaceChildren(
    ...evidenceItems.map(([title, detail]) => detailEvidenceItem(title, detail)),
  );

  const versions = currentView?.versions || {};
  document.querySelector("#detail-rule-model").textContent =
    versions.rule_engine || (currentView?.provisional_mode ? "Evidence pending" : "Not reported");
  document.querySelector("#detail-availability-model").textContent =
    decision.availability?.model_version || versions.availability_model || "Not estimated";
  document.querySelector("#detail-optimizer-model").textContent =
    versions.optimizer || (currentView?.provisional_mode ? "Not run" : "Not reported");
}

function detailEvidenceItem(title, detail) {
  const item = element("li");
  item.append(element("strong", "", title), document.createTextNode(detail));
  return item;
}

function updateDetailRing(probability) {
  const value = Number(probability);
  const estimated = probability !== null && probability !== undefined
    && Number.isFinite(value) && value >= 0 && value <= 1;
  document.querySelector("#detail-probability").textContent = estimated
    ? formatPercent(value)
    : "--";
  document.querySelector("#ring-progress").style.strokeDashoffset = String(
    estimated ? RING_CIRCUMFERENCE * (1 - value) : RING_CIRCUMFERENCE,
  );
  document.querySelector(".probability-ring").setAttribute(
    "aria-label",
    estimated ? `${formatPercent(value)} conditional vacancy chance` : "Availability not estimated",
  );
}

function resetCandidateDetail(message) {
  document.querySelector("#detail-title").textContent = "No candidate selected";
  document.querySelector("#detail-side").textContent = message;
  document.querySelector("#detail-order").textContent = "--";
  document.querySelector("#detail-length").textContent = "--";
  document.querySelector("#detail-capacity").textContent = "--";
  const statePill = document.querySelector("#detail-state");
  statePill.className = "state-pill unknown";
  statePill.textContent = "Not evaluated";
  document.querySelector("#detail-evidence").replaceChildren(
    detailEvidenceItem("Waiting for a current search", message),
  );
  document.querySelector("#detail-rule-model").textContent = "Pending";
  document.querySelector("#detail-availability-model").textContent = "Pending";
  document.querySelector("#detail-optimizer-model").textContent = "Pending";
  updateDetailRing(null);
}

function decisionClass(decision) {
  if (decision.legality.legal_state === "ILLEGAL") return "illegal";
  if (decision.legality.legal_state === "UNKNOWN" || decision.legality.free_state === "UNKNOWN") {
    return "unknown";
  }
  if (decision.legality.free_state === "PAID") return "paid";
  return "legal";
}

function stateLabel(decision) {
  if (decision.legality.legal_state === "ILLEGAL") return "Illegal";
  if (decision.legality.legal_state === "UNKNOWN") return "Unknown legality";
  if (decision.legality.free_state === "UNKNOWN") return "Unknown price";
  return decision.legality.free_state === "PAID" ? "Legal · paid" : "Legal · free";
}

function availabilityLabel(decision) {
  if (!decision.availability) return "Availability not estimated";
  const interval = decision.availability.interval;
  const band = interval
    ? ` (${formatPercent(interval[0])}–${formatPercent(interval[1])})`
    : "";
  const prefix = decision.availability_is_conditional
    ? "Conditional vacancy chance"
    : "Available";
  return `${prefix} ${formatPercent(decision.availability.probability)}${band}`;
}

function candidateAriaLabel(decision, routeOrder) {
  const route = routeOrder ? `, route stop ${routeOrder}` : "";
  return `${segmentName(decision.segment, decision.segment.segment_id)}, ${stateLabel(decision)}, ${availabilityLabel(decision)}${route}`;
}

function segmentName(segment, fallbackId) {
  if (!segment) return fallbackId;
  return segment.street_name || `Curb ${segment.segment_id.slice(0, 8)}`;
}

function evidenceLabel(refs) {
  return refs && refs.length ? refs.join(", ") : "No supporting source reference";
}

function formatPercent(value) {
  if (value === null || value === undefined) return "Not estimated";
  return `${Math.round(Number(value) * 100)}%`;
}

function formatNumber(value, digits) {
  return Number(value).toFixed(digits);
}

function formatDate(value) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium", timeStyle: "short",
  }).format(new Date(value));
}

function humanize(value) {
  return String(value).replaceAll("_", " ").toLowerCase().replace(/^./, (letter) => letter.toUpperCase());
}

function element(tag, className = "", text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== null) node.textContent = text;
  return node;
}

function svgElement(tag, attributes, text = null) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, String(value)));
  if (text !== null) node.textContent = text;
  return node;
}

centerMapViewport();
showDestinationCoverageGate();
runtimeNotice.hidden = true;
