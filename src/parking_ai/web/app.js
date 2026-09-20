"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const form = document.querySelector("#search-form");
const statusBox = document.querySelector("#request-status");
const submitButton = document.querySelector("#submit-button");
const arrivalNow = document.querySelector("#arrival-now");
const arrivalField = document.querySelector("#arrival-time-field");
const arrivalInput = document.querySelector("#arrival-time");
const mapContent = document.querySelector("#map-content");
const mapEmpty = document.querySelector("#map-empty");
const mapEmptyNote = document.querySelector("#map-empty-note");
const mapDescription = document.querySelector("#map-description");
const candidateList = document.querySelector("#candidate-list");

let activeSegmentId = null;
let lastRequestBody = null;
let lastRequestKey = null;

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

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;

  const payload = buildRequest();
  const requestBody = JSON.stringify(payload);
  if (requestBody !== lastRequestBody) {
    lastRequestBody = requestBody;
    lastRequestKey = createRequestKey();
  }
  clearResults();
  setLoading(true);
  setStatus("Evaluating curb rules and building a route…", false);
  try {
    const response = await fetch("/v1/parking/search", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": lastRequestKey,
      },
      body: requestBody,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(readError(body, response.status));
    renderSearch(body);
    setStatus(`Plan ready for ${body.destination.name}.`, false);
    // A completed search is no longer a retry candidate. A later submit, especially one using
    // the logical `now` value, must create a fresh session rather than replaying stale results.
    lastRequestBody = null;
    lastRequestKey = null;
  } catch (error) {
    const message = error instanceof Error ? error.message : "The parking search failed.";
    markSearchFailed();
    setStatus(message, true);
  } finally {
    setLoading(false);
  }
});

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
    destination: { query: document.querySelector("#destination").value.trim() },
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

function createRequestKey() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function readError(body, code) {
  if (code === 503) {
    return "Search is not configured yet. Ask the operator to configure the database and guaranteed fallback.";
  }
  if (code === 404) return "That destination is not available in the local search area.";
  if (code === 422) return "Check the search fields and try again.";
  return typeof body.detail === "string" ? body.detail : "The parking search failed. Try again.";
}

function setLoading(isLoading) {
  submitButton.disabled = isLoading;
  form.setAttribute("aria-busy", String(isLoading));
  submitButton.querySelector("span").textContent = isLoading ? "Building plan…" : "Build parking plan";
}

function setStatus(message, isError) {
  statusBox.textContent = message;
  statusBox.classList.toggle("error", isError);
  statusBox.setAttribute("role", isError ? "alert" : "status");
  statusBox.setAttribute("aria-live", isError ? "assertive" : "polite");
}

function clearResults() {
  activeSegmentId = null;
  mapContent.replaceChildren();
  setMapEmptyVisibility(true);
  mapEmpty.textContent = "Building a fresh candidate map…";
  mapEmptyNote.textContent = "Previous results have been cleared";
  document.querySelector("#result-time").textContent = "Searching…";
  ["#summary-cards", "#route-section", "#candidate-section", "#warning-section"]
    .forEach((selector) => {
      document.querySelector(selector).hidden = true;
    });
}

function markSearchFailed() {
  document.querySelector("#result-time").textContent = "No current plan";
  mapEmpty.textContent = "Search did not complete";
  mapEmptyNote.textContent = "Review the message beside the search form, then try again";
  setMapEmptyVisibility(true);
}

function renderSearch(result) {
  activeSegmentId = null;
  document.querySelector("#result-time").textContent = formatDate(result.resolved_arrival_time);
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
  const routeOrder = new Map(
    (result.route.steps || []).map((step, index) => [step.segment_id, index + 1]),
  );
  mapDescription.textContent = `${decisions.length} evaluated parking curbs with ${routeOrder.size} recommended route stops.`;
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
    const points = coordinatesForDecision(decision).map(project);
    if (points.length < 2) return;
    const path = svgElement("polyline", {
      points: points.map(([x, y]) => `${x},${y}`).join(" "),
      class: `map-segment ${decisionClass(decision)}`,
      tabindex: "0",
      role: "button",
      "aria-pressed": "false",
      "aria-label": candidateAriaLabel(decision, routeOrder.get(decision.segment.segment_id)),
      "data-segment-id": decision.segment.segment_id,
    });
    path.addEventListener("click", () => selectSegment(decision.segment.segment_id));
    path.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectSegment(decision.segment.segment_id);
      }
    });
    mapContent.append(path);

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
}

function setMapEmptyVisibility(isVisible) {
  const visibility = isVisible ? "visible" : "hidden";
  mapEmpty.setAttribute("visibility", visibility);
  mapEmptyNote.setAttribute("visibility", visibility);
  mapEmpty.setAttribute("aria-hidden", String(!isVisible));
  mapEmptyNote.setAttribute("aria-hidden", String(!isVisible));
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
  const scale = Math.min(780 / width, 460 / height);
  const drawWidth = width * scale;
  const drawHeight = height * scale;
  const offsetX = (900 - drawWidth) / 2;
  const offsetY = (580 - drawHeight) / 2;
  return ([longitude, latitude]) => [
    offsetX + (longitude - minX) * scale,
    offsetY + (maxY - latitude) * scale,
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
    element("h4", "", "Guaranteed fallback"),
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
  const routeOrder = new Map(
    result.route.steps.map((step, index) => [step.segment_id, index + 1]),
  );
  result.candidate_decisions.forEach((decision) => {
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
      element("span", "", `${formatPercent(decision.legality.confidence)} evidence confidence`),
      element("span", "", `Evaluated ${formatDate(decision.legality.evaluated_at)}`),
    );
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
    card.append(top, meta, provenance);
    candidateList.append(card);
  });
  document.querySelector("#candidate-count").textContent =
    `${result.candidate_decisions.length} evaluated`;
  document.querySelector("#candidate-section").hidden = false;
}

function renderWarnings(warnings) {
  const section = document.querySelector("#warning-section");
  const list = document.querySelector("#warning-list");
  list.replaceChildren();
  warnings.forEach((warning) => list.append(element("li", "", humanize(warning))));
  section.hidden = warnings.length === 0;
}

function selectSegment(segmentId) {
  activeSegmentId = activeSegmentId === segmentId ? null : segmentId;
  document.querySelectorAll("[data-segment-id]").forEach((node) => {
    const isActive = activeSegmentId === node.dataset.segmentId;
    node.classList.toggle("is-active", isActive);
    node.setAttribute("aria-pressed", String(isActive));
    if (node.classList.contains("map-segment")) {
      node.classList.toggle("is-muted", activeSegmentId !== null && !isActive);
    }
  });
  if (activeSegmentId) {
    document.querySelector(`.candidate-card[data-segment-id="${CSS.escape(activeSegmentId)}"]`)
      ?.scrollIntoView({ block: "nearest", behavior: "auto" });
  }
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
  return `${formatPercent(decision.availability.probability)} available${band}`;
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
