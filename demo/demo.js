const SVG_NS = "http://www.w3.org/2000/svg";
const CIRCUMFERENCE = 2 * Math.PI * 50;

const scenarios = {
  fondren: {
    title: "Fondren Library",
    subtitle: "Dallas, Texas · sample snapshot",
    destination: { x: 570, y: 270, label: "Fondren Library" },
    confidence: "Moderate",
    expectedTime: 8.4,
    candidates: [
      {
        id: "PF-101",
        name: "University Blvd",
        side: "North curb",
        status: "legal",
        probability: 0.76,
        walk: 4.1,
        drive: 3.2,
        capacity: 6,
        free: true,
        path: "M295 185 C360 175 438 186 505 170",
        marker: [370, 180],
        evidenceTier: "Tier A",
        evidence: [
          ["Verified fixture sign", "No active restriction in the sample window"],
          ["Payment record", "No payment period active in this fixture"],
          ["Model context", "Weekday afternoon · six-space curb prior"],
        ],
      },
      {
        id: "PF-204",
        name: "Daniel Avenue",
        side: "East curb",
        status: "legal",
        probability: 0.61,
        walk: 5.0,
        drive: 4.5,
        capacity: 4,
        free: true,
        path: "M685 205 C702 270 696 337 710 393",
        marker: [698, 294],
        evidenceTier: "Tier B",
        evidence: [
          ["Campus parking fixture", "Public curb outside the sample permit boundary"],
          ["Rule evaluation", "Free state retained separately from legality"],
          ["Model context", "Residential curb · moderate demand prior"],
        ],
      },
      {
        id: "PF-319",
        name: "Airline Road",
        side: "West curb",
        status: "unknown",
        probability: 0.48,
        walk: 6.2,
        drive: 5.3,
        capacity: 5,
        free: true,
        path: "M410 390 C474 404 540 397 602 412",
        marker: [505, 403],
        evidenceTier: "Tier C",
        evidence: [
          ["Road geometry", "Curb shape is known; regulation coverage is incomplete"],
          ["Safety rule", "Unknown evidence is never converted to legal"],
          ["Model context", "Vacancy estimate is conditional on a usable curb"],
        ],
      },
      {
        id: "PF-411",
        name: "Binkley Avenue",
        side: "South curb",
        status: "paid",
        probability: 0.83,
        walk: 3.7,
        drive: 4.0,
        capacity: 8,
        free: false,
        path: "M430 115 C500 120 575 110 638 105",
        marker: [555, 112],
        evidenceTier: "Tier A",
        evidence: [
          ["Meter schedule", "Payment is required in the sample arrival window"],
          ["Rule evaluation", "Legal and paid are represented independently"],
          ["Model context", "Higher capacity produces a stronger vacancy prior"],
        ],
      },
    ],
  },
  arts: {
    title: "Dallas Arts District",
    subtitle: "Dallas, Texas · sample snapshot",
    destination: { x: 520, y: 235, label: "Arts District" },
    confidence: "Cautious",
    expectedTime: 11.7,
    candidates: [
      {
        id: "PF-501",
        name: "Flora Street",
        side: "North curb",
        status: "paid",
        probability: 0.72,
        walk: 2.8,
        drive: 4.8,
        capacity: 9,
        free: false,
        path: "M250 170 C345 160 455 172 565 155",
        marker: [398, 165],
        evidenceTier: "Tier A",
        evidence: [
          ["Meter schedule", "Paid period active in the sample window"],
          ["Municipal fixture", "Passenger parking allowed for the sample duration"],
          ["Model context", "Event activity increases demand uncertainty"],
        ],
      },
      {
        id: "PF-512",
        name: "Ross Avenue",
        side: "West curb",
        status: "legal",
        probability: 0.54,
        walk: 7.4,
        drive: 5.5,
        capacity: 3,
        free: true,
        path: "M690 150 C680 245 704 322 687 420",
        marker: [694, 305],
        evidenceTier: "Tier B",
        evidence: [
          ["City curb fixture", "No payment requirement in this sample interval"],
          ["Duration check", "The planned stay remains inside the time limit"],
          ["Model context", "Small curb capacity widens uncertainty"],
        ],
      },
      {
        id: "PF-529",
        name: "Routh Street",
        side: "East curb",
        status: "unknown",
        probability: 0.42,
        walk: 6.5,
        drive: 6.1,
        capacity: 5,
        free: true,
        path: "M325 385 C405 402 486 386 565 402",
        marker: [445, 396],
        evidenceTier: "Tier C",
        evidence: [
          ["Road geometry", "Candidate geometry is present"],
          ["Evidence gap", "Current sign coverage is insufficient"],
          ["Safety rule", "Verify signs before relying on this lead"],
        ],
      },
    ],
  },
  seattle: {
    title: "Seattle Center",
    subtitle: "Seattle, Washington · sample snapshot",
    destination: { x: 590, y: 255, label: "Seattle Center" },
    confidence: "Moderate",
    expectedTime: 10.2,
    candidates: [
      {
        id: "PF-701",
        name: "5th Avenue N",
        side: "West curb",
        status: "legal",
        probability: 0.67,
        walk: 5.2,
        drive: 4.1,
        capacity: 7,
        free: true,
        path: "M420 105 C435 190 418 288 438 380",
        marker: [428, 210],
        evidenceTier: "Tier B",
        evidence: [
          ["Sample curb record", "Legal/free fixture state for this demonstration"],
          ["Duration check", "Sample stay does not cross a restriction boundary"],
          ["Model context", "Seven-space curb · moderate event demand"],
        ],
      },
      {
        id: "PF-718",
        name: "Republican Street",
        side: "South curb",
        status: "unknown",
        probability: 0.58,
        walk: 4.6,
        drive: 4.9,
        capacity: 6,
        free: true,
        path: "M530 405 C615 392 705 410 790 388",
        marker: [650, 399],
        evidenceTier: "Tier C",
        evidence: [
          ["Road geometry", "Live-road-style sample geometry is available"],
          ["Evidence gap", "Payment and permit coverage need verification"],
          ["Model context", "Probability is conditional, not a legal conclusion"],
        ],
      },
      {
        id: "PF-726",
        name: "Mercer Street",
        side: "North curb",
        status: "paid",
        probability: 0.79,
        walk: 6.0,
        drive: 5.8,
        capacity: 10,
        free: false,
        path: "M250 155 C340 145 450 162 555 140",
        marker: [378, 151],
        evidenceTier: "Tier A",
        evidence: [
          ["Meter fixture", "Payment required in the sample interval"],
          ["Rule evaluation", "Legal but paid remains distinct from free"],
          ["Model context", "Large curb capacity supports a higher prior"],
        ],
      },
      {
        id: "PF-734",
        name: "1st Avenue N",
        side: "East curb",
        status: "legal",
        probability: 0.45,
        walk: 8.1,
        drive: 7.0,
        capacity: 4,
        free: true,
        path: "M770 125 C755 225 784 326 765 430",
        marker: [770, 276],
        evidenceTier: "Tier B",
        evidence: [
          ["Sample curb record", "Free state supported in this fixture"],
          ["Walking constraint", "Included only when maximum walk permits"],
          ["Model context", "Distance and lower capacity reduce utility"],
        ],
      },
    ],
  },
};

const state = {
  scenarioId: "fondren",
  mode: "instant",
  maxWalk: 8,
  freeOnly: true,
  arriveNow: true,
  activeCandidateId: "PF-101",
};

const form = document.querySelector("#demo-form");
const destinationSelect = document.querySelector("#destination-select");
const walkRange = document.querySelector("#walk-range");
const freeOnly = document.querySelector("#free-only");
const arriveNow = document.querySelector("#arrive-now");
const resultsTitle = document.querySelector("#results-title");
const resultSubtitle = document.querySelector("#result-subtitle");
const statusBanner = document.querySelector("#status-banner");
const candidateList = document.querySelector("#candidate-list");
const segmentLayer = document.querySelector("#segment-layer");
const markerLayer = document.querySelector("#marker-layer");
const routeLayer = document.querySelector("#route-layer");
const destinationLayer = document.querySelector("#destination-layer");

document.querySelectorAll("[data-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    state.mode = button.dataset.mode;
    document.querySelectorAll("[data-mode]").forEach((item) => {
      item.setAttribute("aria-pressed", String(item === button));
    });
    renderPlan();
  });
});

destinationSelect.addEventListener("change", () => {
  state.scenarioId = destinationSelect.value;
  state.activeCandidateId = null;
  renderPlan();
});

walkRange.addEventListener("input", () => {
  state.maxWalk = Number(walkRange.value);
  document.querySelector("#walk-output").value = `${state.maxWalk} min`;
  renderPlan();
});

freeOnly.addEventListener("change", () => {
  state.freeOnly = freeOnly.checked;
  renderPlan();
});

arriveNow.addEventListener("change", () => {
  state.arriveNow = arriveNow.checked;
  document.querySelector("#arrival-help").textContent = state.arriveNow
    ? "Use the current local hour"
    : "Use the 10:00 AM sample window";
  renderPlan();
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  statusBanner.classList.add("researching");
  statusBanner.querySelector("strong").textContent = state.mode === "research"
    ? "Replaying provider research and deterministic evaluation"
    : "Refreshing official-road sample snapshot";
  statusBanner.querySelector("p").textContent = "This short delay represents the request pipeline.";
  window.setTimeout(() => {
    statusBanner.classList.remove("researching");
    renderPlan();
    document.querySelector("#updated-time").textContent = "Updated just now";
  }, 520);
});

function visibleCandidates() {
  const scenario = scenarios[state.scenarioId];
  return scenario.candidates.filter((candidate) => {
    const walkAllowed = candidate.walk <= state.maxWalk;
    const priceAllowed = !state.freeOnly || candidate.free;
    return walkAllowed && priceAllowed;
  });
}

function renderPlan() {
  const scenario = scenarios[state.scenarioId];
  const candidates = visibleCandidates();
  if (!candidates.some((candidate) => candidate.id === state.activeCandidateId)) {
    state.activeCandidateId = candidates[0]?.id ?? null;
  }

  resultsTitle.textContent = scenario.title;
  const arrivalLabel = state.arriveNow
    ? `now · ${new Intl.DateTimeFormat("en", { hour: "numeric", minute: "2-digit" }).format(new Date())}`
    : "planned · 10:00 AM";
  resultSubtitle.textContent = `${scenario.subtitle} · ${arrivalLabel}`;
  document.querySelector("#mode-indicator").textContent = state.mode === "research"
    ? "Research snapshot"
    : "Instant snapshot";
  document.querySelector("#max-walk-metric").textContent = `${state.maxWalk} min`;
  document.querySelector("#plan-confidence").textContent = scenario.confidence;
  document.querySelector("#candidate-count").textContent = `${candidates.length} candidate${candidates.length === 1 ? "" : "s"}`;

  const bestProbability = candidates.length
    ? Math.max(...candidates.map((candidate) => adjustedProbability(candidate)))
    : 0;
  document.querySelector("#best-chance").textContent = formatPercent(bestProbability);
  document.querySelector("#expected-time").textContent = candidates.length
    ? `${(scenario.expectedTime + Math.max(0, 3 - candidates.length) * 1.4).toFixed(1)} min`
    : "No route";

  const statusStrong = statusBanner.querySelector("strong");
  const statusCopy = statusBanner.querySelector("p");
  statusStrong.textContent = candidates.length
    ? `${candidates.length} curb lead${candidates.length === 1 ? "" : "s"} match this sample plan`
    : "No sample curb matches these preferences";
  statusCopy.textContent = candidates.length
    ? "Legal and payment states are shown separately from predicted vacancy."
    : "Increase walking time or include paid options to restore candidates.";

  renderMap(scenario, candidates);
  renderCandidateList(candidates);
  renderDetail(candidates.find((candidate) => candidate.id === state.activeCandidateId));
}

function adjustedProbability(candidate) {
  const researchAdjustment = state.mode === "research" && candidate.status !== "unknown" ? 0.02 : 0;
  const arrivalAdjustment = state.arriveNow ? 0 : 0.03;
  return Math.min(0.95, candidate.probability + researchAdjustment + arrivalAdjustment);
}

function renderMap(scenario, candidates) {
  segmentLayer.replaceChildren();
  markerLayer.replaceChildren();
  routeLayer.replaceChildren();
  destinationLayer.replaceChildren();

  if (candidates.length) {
    const routePoints = candidates.map((candidate) => candidate.marker).concat([
      [scenario.destination.x, scenario.destination.y],
    ]);
    routeLayer.append(svg("polyline", {
      class: "route-path",
      points: routePoints.map((point) => point.join(",")).join(" "),
    }));
  }

  candidates.forEach((candidate, index) => {
    const path = svg("path", {
      class: `candidate-segment ${candidate.status} ${candidate.id === state.activeCandidateId ? "is-active" : ""}`,
      d: candidate.path,
      tabindex: "0",
      role: "button",
      "aria-label": `${candidate.name}, ${formatPercent(adjustedProbability(candidate))} conditional vacancy chance`,
    });
    path.addEventListener("click", () => selectCandidate(candidate.id));
    path.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectCandidate(candidate.id);
      }
    });
    segmentLayer.append(path);
    markerLayer.append(
      svg("circle", { class: "route-marker", cx: candidate.marker[0], cy: candidate.marker[1], r: 15 }),
      svgText("text", { class: "route-number", x: candidate.marker[0], y: candidate.marker[1] + 1 }, String(index + 1)),
    );
  });

  destinationLayer.append(
    svg("circle", {
      class: "destination-pin",
      cx: scenario.destination.x,
      cy: scenario.destination.y,
      r: 12,
    }),
    svgText("text", {
      class: "destination-label",
      x: scenario.destination.x + 20,
      y: scenario.destination.y - 16,
    }, scenario.destination.label),
  );

  document.querySelector("#map-description").textContent = candidates.length
    ? `${candidates.length} sample curb candidates near ${scenario.title}.`
    : `No candidates satisfy the current sample preferences near ${scenario.title}.`;
}

function renderCandidateList(candidates) {
  candidateList.replaceChildren(...candidates.map((candidate, index) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "candidate-button";
    button.setAttribute("aria-pressed", String(candidate.id === state.activeCandidateId));
    button.addEventListener("click", () => selectCandidate(candidate.id));

    const order = element("span", "candidate-order", String(index + 1));
    const copy = element("span", "candidate-copy");
    copy.append(
      element("strong", "", candidate.name),
      element("small", "", `${candidate.side} · ${candidate.walk.toFixed(1)} min walk · ${statusLabel(candidate)}`),
    );
    button.append(
      order,
      copy,
      element("span", "candidate-chance", formatPercent(adjustedProbability(candidate))),
    );
    item.append(button);
    return item;
  }));
}

function selectCandidate(candidateId) {
  state.activeCandidateId = candidateId;
  renderPlan();
}

function renderDetail(candidate) {
  const panel = document.querySelector(".detail-panel");
  if (!candidate) {
    panel.setAttribute("aria-disabled", "true");
    document.querySelector("#detail-title").textContent = "No matching candidate";
    document.querySelector("#detail-side").textContent = "Adjust the plan preferences";
    document.querySelector("#detail-probability").textContent = "0%";
    document.querySelector("#detail-walk").textContent = "—";
    document.querySelector("#detail-drive").textContent = "—";
    document.querySelector("#detail-capacity").textContent = "—";
    document.querySelector("#evidence-tier").textContent = "No evidence";
    document.querySelector("#evidence-list").replaceChildren();
    updateRing(0);
    return;
  }

  panel.removeAttribute("aria-disabled");
  const probability = adjustedProbability(candidate);
  document.querySelector("#detail-title").textContent = candidate.name;
  document.querySelector("#detail-side").textContent = `${candidate.side} · Segment ${candidate.id}`;
  document.querySelector("#detail-probability").textContent = formatPercent(probability);
  document.querySelector("#detail-walk").textContent = `${candidate.walk.toFixed(1)} min`;
  document.querySelector("#detail-drive").textContent = `${candidate.drive.toFixed(1)} min`;
  document.querySelector("#detail-capacity").textContent = `${candidate.capacity} spaces`;
  document.querySelector("#evidence-tier").textContent = candidate.evidenceTier;
  document.querySelector(".probability-ring").setAttribute(
    "aria-label",
    `${formatPercent(probability)} conditional vacancy chance`,
  );
  document.querySelector("#evidence-list").replaceChildren(...candidate.evidence.map(([title, detail]) => {
    const item = document.createElement("li");
    item.append(element("strong", "", title), document.createTextNode(detail));
    return item;
  }));
  updateRing(probability);
}

function updateRing(probability) {
  document.querySelector("#ring-progress").style.strokeDashoffset = String(
    CIRCUMFERENCE * (1 - probability),
  );
}

function statusLabel(candidate) {
  if (candidate.status === "legal") return "legal + free";
  if (candidate.status === "paid") return "legal + paid";
  return "verify signs";
}

function formatPercent(value) {
  return `${Math.round(value * 100)}%`;
}

function element(tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function svg(tagName, attributes) {
  const node = document.createElementNS(SVG_NS, tagName);
  Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, value));
  return node;
}

function svgText(tagName, attributes, text) {
  const node = svg(tagName, attributes);
  node.textContent = text;
  return node;
}

renderPlan();
