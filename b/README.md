# TourGuard AI

A complete local, persistent visitor-and-vehicle management app for tourist destinations, parks, museums, temples, and other high-footfall places.

## Quick start

1. Copy `.env.example` to `.env` and set a strong `TOURGUARD_SECRET` before any real deployment.
2. Run `npm start` from this folder (or `py -3 backend/server.py` on Windows / `python3 backend/server.py` on macOS and Linux).
3. Open `http://127.0.0.1:8080`.

The first start creates `database/tourguard.db`, a real persistent SQLite relational database, and clearly marked demo records.

Demo credentials:

| Role | Email | Password |
|---|---|---|
| Administrator | `admin@tourguard.local` | `Admin@123` |
| Gate operator | `gate@tourguard.local` | `Gate@123` |
| Viewer | `viewer@tourguard.local` | `Viewer@123` |

## Included workflows

- Secure PBKDF2 password hashing, signed expiring sessions, role permissions and login rate limiting.
- Automatic visitor ID, IN/OUT time, duration, vehicle history and duplicate-active-vehicle prevention.
- A printable visitor pass is created immediately after entry. It contains the visitor, vehicle, gate, operator, time, status, and a unique QR code stored with the visit in SQLite. At exit, choose the gate once and use **Start camera** to scan the pass; a valid active pass records the exit without retyping visitor data. Browsers without native QR detection load the scanner fallback from jsDelivr on first use, which requires internet access.
- Live dashboard refreshes across open sessions via Server-Sent Events.
- Searchable visitor history, pagination, CSV export, print reports, parking capacity, crowd warnings, notifications, analytics and data-grounded AI insights.
- Admin-only user, parking, capacity and gate management with audit logs.

## Deployment

### Share the source on GitHub

Install Git for your operating system, then open this project folder in a terminal. Do not upload `.env`, `database/tourguard.db`, or real visitor records. These are excluded by `.gitignore`; keep the repository private if it contains anything sensitive.

With GitHub CLI installed and signed in (`gh auth login`), create and push a private repository with:

```sh
git init
git add .
git commit -m "Initial TourGuard release"
gh repo create tourguard-ai --private --source=. --remote=origin --push
```

Replace `tourguard-ai` with the repository name you want. GitHub stores the source code; it does not run this Python backend, and GitHub Pages cannot host this app by itself.

### Run on a server with Docker

Copy `.env.example` to `.env`, replace `TOURGUARD_SECRET` with a long random value, then run from the project folder:

```sh
docker compose --env-file .env -f docker/docker-compose.yml up --build -d
```

The server must have Docker Compose installed and allow inbound traffic to port `8080`; open `http://SERVER_ADDRESS:8080` to test it. For a public production site, use a domain with HTTPS through a reverse proxy, restrict network access, back up the persistent database, and create real user accounts. The included server is intended for a small deployment; use a production-grade database and hosting setup before relying on it for important visitor records.

To run without Docker, install Python 3.10 or newer and run `py -3 backend/server.py` on Windows or `python3 backend/server.py` on macOS/Linux, then visit `http://127.0.0.1:8080` on that same machine.

## API overview

`POST /api/auth/login`, `GET /api/dashboard`, `POST /api/visits/entry`, `POST /api/visits/{id}/exit`, `GET /api/visits`, `GET /api/visits/inside`, `GET /api/vehicles/{number}`, `GET /api/analytics`, `GET /api/ai/insights`, and `GET /api/reports` are all implemented. See [docs/API.md](docs/API.md) for request examples.
