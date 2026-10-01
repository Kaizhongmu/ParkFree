# Publish ParkFree for Everyone

Running ParkFree at `localhost` makes it available only on that computer. A public deployment runs
the API and PostGIS database on cloud servers, giving everyone a normal web address that they can
open without Git, Docker, or access to the owner's computer.

## One-click Render deployment

ParkFree includes a Render Blueprint that creates the web service and PostGIS database, runs all
database migrations, and serves the application over HTTPS.

1. Open the [ParkFree Render deployment page](https://render.com/deploy?repo=https://github.com/Kaizhongmu/ParkFree).
2. Sign in to Render with the GitHub account that owns or can access the repository.
3. Review the two resources: `parkfree` and `parkfree-db`.
4. Select **Apply** or **Deploy Blueprint**.
5. Wait for both resources to finish provisioning and for the `parkfree` health check to pass.
6. Open the public `https://parkfree-....onrender.com` address shown by Render.

That `onrender.com` address is the real application URL to share with users. Users only need a
browser; they do not clone the repository, install Docker, or connect to the owner's computer.

## Cost and availability

The included Blueprint starts with Render's free plans so the owner can verify the deployment
before paying. At the time this guide was written, a free Render Postgres database expires after
30 days. Upgrade the database in the Render dashboard before its expiration if ParkFree should
remain available. Review Render's current pricing and limits before publishing because hosting
plans can change.

Free web services may also take time to wake after inactivity. A paid web service avoids that
sleep behavior and is more appropriate for a public application with regular users.

## What happens automatically

- Render builds the checked-in Docker image.
- Render provisions PostgreSQL 16 and exposes the supported PostGIS extension.
- ParkFree runs `alembic upgrade head`, whose first migration enables PostGIS.
- The app starts in production mode and disables public API documentation routes.
- Render checks `/health` before directing public traffic to the new version.
- Later commits deploy only after the GitHub checks pass.

## Before sharing widely

Replace the provider user-agent contact values in the Render environment settings with an
identifying project URL or monitored contact address. Public Nominatim and Overpass services have
usage policies and rate limits; a high-traffic deployment should use suitable hosted providers or
operator-managed infrastructure instead of relying on community endpoints.

ParkFree processes user-provided origin and destination locations. Publish a privacy policy,
monitor service logs, and avoid collecting location data that is not required. The application is
a decision aid, not legal permission to park; users must still verify signs, curb markings,
payment rules, temporary restrictions, and current conditions.

## Custom domain

The Render URL works immediately. To use a domain such as `parkfree.example.com`, add it under the
web service's **Settings > Custom Domains**, then create the DNS records Render displays. HTTPS is
configured after the domain is verified.

## Updating the public app

Push a tested commit to the repository's default branch. The Blueprint is configured to deploy
after GitHub checks pass. Render runs the migrations again before starting the updated app.

Use the Render dashboard to inspect deployment logs, health, database expiration, and usage. Do
not put database passwords or provider secrets in the repository.

