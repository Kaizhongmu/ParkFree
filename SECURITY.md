# Security Policy

## Reporting a vulnerability

Please open a private GitHub security advisory for vulnerabilities. Do not publish credentials,
personal location data, exploit details, or sensitive provider responses in a public issue.

## Deployment notes

- Replace every placeholder credential in `.env.example`.
- Run public deployments with `ENVIRONMENT=production`.
- Put authentication and ingress rate limiting in front of internet-facing instances.
- Treat origins, destinations, and parking outcomes as potentially sensitive location data.
- Review external provider terms before storing or redistributing fetched content.
- ParkFree is a decision aid and must not be presented as an authoritative legal guarantee.
