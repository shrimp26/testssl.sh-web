import asyncio
import ipaddress
import logging
import os
import re
import shlex
import time
from pathlib import Path
from typing import AsyncGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
logger = logging.getLogger("testssl-web")

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

TESTSSL_PATH = Path(os.environ.get("TESTSSL_PATH", "/opt/testssl/testssl.sh"))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_SCANS", "3"))
SCAN_TIMEOUT = int(os.environ.get("SCAN_TIMEOUT", "600"))
RATE_LIMIT = os.environ.get("RATE_LIMIT", "10/minute")

# Blocks private/reserved ranges to prevent SSRF
_PRIVATE_NETWORKS = [
    ipaddress.ip_network(n)
    for n in [
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
        "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16",
        "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
        "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32",
        "::1/128", "fc00::/7", "fe80::/10",
    ]
]

_TARGET_RE = re.compile(
    r"^(?:"
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}"
    r"|(?:\d{1,3}\.){3}\d{1,3}"
    r"|(?:[0-9a-fA-F:]{2,39})"
    r")(?::\d{1,5})?$"
)

_ALLOWED_OPTIONS = frozenset([
    "--protocols", "--server-defaults", "--server-preference",
    "--cipher-per-proto", "--header", "--pfs",
    "--vulnerable",
    "--heartbleed", "--ccs-injection", "--ticketbleed", "--robot",
    "--lucky13", "--drown", "--logjam", "--beast", "--crime",
    "--poodle", "--freak", "--rc4", "--breach", "--renego",
    "--full", "--fast", "--sneaky",
    "-p", "-S", "-P", "-U",
])

_semaphore: asyncio.Semaphore | None = None

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="testssl.sh Web UI", docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.on_event("startup")
async def startup():
    global _semaphore
    _semaphore = asyncio.Semaphore(MAX_CONCURRENT)


def _is_private(host: str) -> bool:
    hostname = host.split(":")[0].strip("[]")
    # Reject localhost by name
    if hostname.lower() in {"localhost", "local"}:
        return True
    try:
        addr = ipaddress.ip_address(hostname)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        # It's a hostname — we can't resolve here, allow it
        # (the scan itself may fail to reach internal hosts)
        return False


class ScanRequest(BaseModel):
    target: str
    options: list[str] = []

    @field_validator("target")
    @classmethod
    def validate_target(cls, v: str) -> str:
        v = v.strip()
        if not _TARGET_RE.match(v):
            raise ValueError("Invalid target: must be hostname, IPv4, or IPv6 with optional port")
        if _is_private(v):
            raise ValueError("Scanning private/reserved addresses is not allowed")
        return v

    @field_validator("options")
    @classmethod
    def validate_options(cls, v: list[str]) -> list[str]:
        for opt in v:
            if opt not in _ALLOWED_OPTIONS:
                raise ValueError(f"Option not allowed: {opt!r}")
        return v


def _get_client_ip(request: "Request") -> str:
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _stream_testssl(
    target: str, options: list[str], client_ip: str
) -> AsyncGenerator[str, None]:
    cmd = [
        str(TESTSSL_PATH),
        "--color", "0",
        "--warnings", "off",
        "--ip", "one",
        *options,
        target,
    ]

    logger.info("scan_start  ip=%-20s target=%s cmd=%s", client_ip, target, shlex.join(cmd))
    t_start = time.monotonic()

    async with _semaphore:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        try:
            while True:
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=SCAN_TIMEOUT)
                except asyncio.TimeoutError:
                    process.kill()
                    yield "data: [TIMEOUT] Scan exceeded time limit\n\n"
                    return
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                text = re.sub(r"\x1b\[[0-9;]*[mGKHF]", "", text)
                yield f"data: {text}\n\n"
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
            elapsed = time.monotonic() - t_start
            logger.info(
                "scan_end    ip=%-20s target=%s duration=%.1fs exit=%s",
                client_ip, target, elapsed, process.returncode,
            )

    yield "data: [DONE]\n\n"


@app.post("/api/scan")
@limiter.limit(lambda: RATE_LIMIT)
async def scan(request: Request, body: ScanRequest):
    if not TESTSSL_PATH.exists():
        raise HTTPException(status_code=503, detail="testssl.sh not found")
    client_ip = _get_client_ip(request)
    return StreamingResponse(
        _stream_testssl(body.target, body.options, client_ip),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "testssl_available": TESTSSL_PATH.exists(),
        "testssl_path": str(TESTSSL_PATH),
    }


@app.get("/api/version")
async def version():
    version_file = Path("/opt/testssl/VERSION")
    ver = version_file.read_text().strip() if version_file.exists() else "unknown"
    return {"testssl_version": ver}


app.mount("/", StaticFiles(directory="/app/frontend", html=True), name="static")
