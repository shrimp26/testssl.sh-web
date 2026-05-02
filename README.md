# testssl.sh-web

A modern, responsive web UI for [testssl.sh](https://github.com/drwetter/testssl.sh) — the well-known TLS/SSL scanner. Run scans from your browser with real-time streaming output, no CLI required.

> **Vibecoded with [Claude Code](https://claude.ai/code)**

![License](https://img.shields.io/badge/license-MIT-blue)
![testssl.sh](https://img.shields.io/badge/testssl.sh-3.2-green)
![Docker](https://img.shields.io/badge/docker-alpine--based-blue)

---

## Features

- **Real-time streaming** — output appears line by line as testssl.sh runs
- **Colour-coded results** — vulnerabilities, warnings and OK findings are visually distinguished
- **Scan modes** — Quick, Full, Vulnerabilities only, Protocols, or Custom option picker
- **Download / copy** — save results as plain text with one click
- **Responsive UI** — works on desktop and mobile
- **Hardened container** — multiple layers of Docker security (see below)
- **Auto-updated** — CI/CD watches for new testssl.sh releases and rebuilds automatically

---

## Quick start

```bash
docker compose up
```

Then open [http://localhost:8000](http://localhost:8000).

To use a specific testssl.sh version:

```bash
TESTSSL_VERSION=3.2 docker compose up --build
```

---

## Configuration

All parameters are set via environment variables, either in a `.env` file or directly on the command line.

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8000` | Host port to expose |
| `TESTSSL_VERSION` | `3.2` | testssl.sh version to bundle |
| `RATE_LIMIT` | `10/minute` | Max scan requests per IP (`N/second`, `N/minute`, `N/hour`, `N/day`) |
| `MAX_CONCURRENT_SCANS` | `3` | Max simultaneous scans |
| `SCAN_TIMEOUT` | `600` | Timeout per scan in seconds |

Example `.env`:

```env
PORT=8080
RATE_LIMIT=5/minute
MAX_CONCURRENT_SCANS=2
SCAN_TIMEOUT=300
```

---

## Security

### Container hardening

| Measure | Detail |
|---|---|
| Non-root user | Runs as `uid/gid 1000` |
| `no-new-privileges` | Prevents privilege escalation |
| `cap_drop: ALL` | No Linux capabilities |
| Read-only root filesystem | `read_only: true` |
| Ephemeral scratch space | `tmpfs /tmp` — `noexec`, `nosuid`, `nodev` |
| Syscall filtering | Custom `seccomp` allowlist (`docker/seccomp.json`) |
| CPU/RAM limits | 2 vCPU, 512 MB |
| Alpine base image | Minimal attack surface (~5 MB base) |

### Application-level protections

- **SSRF prevention** — all private and reserved IP ranges (RFC 1918, loopback, link-local, etc.) are blocked server-side; hostname targets are accepted but will simply fail to reach internal infrastructure
- **Option allowlist** — only a fixed set of testssl.sh flags can be passed; no arbitrary shell arguments
- **Rate limiting** — configurable per-IP rate limit via `RATE_LIMIT`
- **Concurrency cap** — configurable semaphore prevents resource exhaustion
- **Scan timeout** — configurable hard timeout per scan process

---

## CI/CD

Two GitHub Actions workflows handle automation:

### `update-testssl.yml` — daily version check
Runs every day at 06:00 UTC. Queries the GitHub API for the latest testssl.sh release and, if a newer version exists, bumps `TESTSSL_VERSION`, commits, and pushes.

### `docker.yml` — build and publish
Triggered on every push to `main`. Builds a multi-arch image (`linux/amd64` + `linux/arm64`) and pushes it to the GitHub Container Registry:

```
ghcr.io/<owner>/testssl.sh-web:latest
ghcr.io/<owner>/testssl.sh-web:<testssl-version>
```

### Required secret

To allow the update workflow's push to trigger the Docker build, create a repository secret named `GH_PAT` with a Personal Access Token (PAT) that has `repo` scope. Without it, the workflow falls back to `GITHUB_TOKEN`, which does not trigger downstream workflows.

---

## Architecture

```
┌─────────────────────────────────┐
│  Browser                        │
│  frontend/index.html            │  ← Vanilla JS, dark terminal UI
└────────────┬────────────────────┘
             │ HTTP POST /api/scan
             │ SSE streaming response
┌────────────▼────────────────────┐
│  FastAPI  backend/main.py       │  ← Input validation, rate limiting,
│                                 │    async subprocess management
└────────────┬────────────────────┘
             │ subprocess
┌────────────▼────────────────────┐
│  testssl.sh  /opt/testssl/      │  ← Read-only, bundled at build time
└─────────────────────────────────┘
```

**Stack:** Python 3.12 · FastAPI · uvicorn · slowapi · Alpine Linux

---

## Development

Run without Docker:

```bash
pip install -r requirements.txt

# Point to a local testssl.sh install
export TESTSSL_PATH=/path/to/testssl.sh
export MAX_CONCURRENT_SCANS=3
export SCAN_TIMEOUT=600
export RATE_LIMIT=100/minute

uvicorn backend.main:app --reload --port 8000
```

The frontend is served as static files from `frontend/` — no build step required.

---

## License

MIT
