# Getting Started with ParkFree

ParkFree has two ways to explore the project:

- The [hosted interactive demo](https://kaizhongmu.github.io/ParkFree/) uses clearly labeled sample
  data. It is useful for seeing the interface without sharing a location or running a server.
- The local application uses the real FastAPI service, destination search, road providers,
  availability model, evidence states, and PostGIS database.

For a real application that anyone can open without installing anything, follow the
[public deployment guide](PUBLIC_DEPLOYMENT.md). A `localhost` address is available only on the
computer that started it.

## Requirements

Install Git and Docker Desktop, or another Docker Engine with Docker Compose v2. No separate
Node.js installation or frontend build is required.

## Run the real application

Clone the public repository:

```bash
git clone https://github.com/Kaizhongmu/ParkFree.git
cd ParkFree
```

Create your local configuration:

```bash
cp .env.example .env
```

Open `.env` and replace `change-me` with a local database password. The included Nominatim and
Overpass user-agent values identify the public ParkFree repository; operators of shared
deployments should replace them with their own application and contact information.

Build and start the database, migrations, and API:

```bash
docker compose up --build -d
```

Compose waits for PostGIS to become healthy, applies every Alembic migration, and starts ParkFree.
Open [http://localhost:8000](http://localhost:8000) in a browser.

Confirm the API is healthy:

```bash
curl http://localhost:8000/health
```

The expected response is:

```json
{"status":"ok"}
```

## Build your first parking approach

1. Enter a US place such as `Seattle Center` or `Fondren Library, Dallas`.
2. Select **Find this US place**. ParkFree does not send requests for every keystroke.
3. Choose the intended address from the returned matches.
4. Open **Origin, vehicle, and limits** and use your browser location or enter coordinates.
5. Set the maximum walk, stay length, arrival time, and free-only preference.
6. Select **Instant** for a faster Census TIGERweb road snapshot.
7. Select **Research** when you also want ParkFree to try OpenStreetMap road and parking-tag
   context before its official-road fallback.
8. Read numbered candidates in order. Selecting a number on the map or in the list opens the same
   candidate in the detail panel.
9. Switch between **Plan** and **Satellite** above the map. The satellite layer uses USGS aerial
   imagery and keeps the numbered curb candidates overlaid in place.
10. Use **Satellite view** for a close Google Maps view, or **Navigate with Google Maps** to open
    driving directions from the user's current location to the selected candidate coordinates.

The interface intentionally keeps these facts separate:

- **Legality** says whether the available evidence supports legal parking.
- **Price** says whether evidence supports free or paid parking.
- **Conditional vacancy** estimates space availability only if the curb is legal and usable.
- **Search order** tells you which candidates to inspect first; it is not turn-by-turn navigation.

When nationwide road coverage lacks authoritative regulations, candidates remain `UNKNOWN` and
the numbered sequence is labeled provisional. Never treat a provisional road lead as permission
to park.

## Optional local fixture

To load the deterministic SMU geometry fixture used by the strict cached-data path:

```bash
docker compose run --rm api parking-ai-seed-smu
```

The fixture does not invent parking regulations. Authoritative evidence is still required before
a curb can become verified legal or free.

## Stop or restart

Stop the services while preserving database data:

```bash
docker compose down
```

Start them again with:

```bash
docker compose up -d
```

To view service logs:

```bash
docker compose logs -f api migrate db
```

## Common problems

### Port 8000 is already in use

Set a different port in `.env`, for example `API_PORT=8001`, then open
`http://localhost:8001`.

### Destination search is unavailable

Check that the machine has internet access and that `NOMINATIM_USER_AGENT` in `.env` contains an
identifying application/contact value. Public providers have usage policies and may temporarily
rate-limit requests.

### Research falls back to official roads

This is expected when Overpass is not configured, fails, or returns no usable roads. ParkFree
reports the provider path in **Request-time API research** instead of silently hiding the fallback.

### The page opens but no API calls work

Use the FastAPI URL shown above. Do not open `src/parking_ai/web/index.html` directly because the
browser client intentionally calls the API on the same origin.

## Safety and privacy

Origin and destination data may reveal sensitive location information. Avoid sensitive addresses,
especially on shared deployments. ParkFree is a decision aid, not a legal guarantee: always verify
posted signs, curb markings, permits, payment requirements, temporary restrictions, and current
conditions.
