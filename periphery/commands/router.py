"""Pipeline command endpoints.

Allows operators to trigger ingestion and collection processes
from the UI instead of SSH-ing into the box.

All endpoints require the X-Admin-Key header to match the configured
admin_api_key setting. If admin_api_key is unset, all endpoints return 403.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

import structlog
from fastapi import APIRouter, Header, HTTPException

from periphery.config import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/commands", tags=["commands"])

# Project root — two parents up from this file (periphery/commands/router.py → project root)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Track running subprocesses keyed by command name
_running_processes: dict[str, asyncio.subprocess.Process] = {}

# Map of command names to their arguments.
# Uses sys.executable so the correct Python interpreter is always used,
# regardless of venv location (Docker, system install, etc.).
_COMMANDS: dict[str, list[str]] = {
    "pipeline": [
        sys.executable,
        "-m", "periphery.pipeline",
    ],
    "rss": [
        sys.executable,
        "-m", "periphery.rss_ingest",
        "--no-server", "--duration", "30",
    ],
    "rss-continuous": [
        sys.executable,
        "-m", "periphery.rss_ingest",
        "--no-server",
    ],
}


def _check_admin_key(x_admin_key: str | None) -> None:
    """Raise HTTP 403 if admin key is missing or incorrect."""
    settings = get_settings()
    if not settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Admin endpoints are disabled (admin_api_key not configured)")
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Key header")


def _is_running(proc: asyncio.subprocess.Process) -> bool:
    """Check if a subprocess is still running."""
    return proc.returncode is None


@router.post("/force-ingest")
async def force_ingest(x_admin_key: str | None = Header(None)):
    """Run the full ingestion pipeline. Requires X-Admin-Key header."""
    _check_admin_key(x_admin_key)
    return await _start_command("pipeline")


@router.post("/run-collect")
async def run_collect(x_admin_key: str | None = Header(None)):
    """Run RSS collection for 30 seconds. Requires X-Admin-Key header."""
    _check_admin_key(x_admin_key)
    return await _start_command("rss")


@router.post("/continuous-collect")
async def continuous_collect(x_admin_key: str | None = Header(None)):
    """Run continuous RSS collection. Requires X-Admin-Key header."""
    _check_admin_key(x_admin_key)
    return await _start_command("rss-continuous")


@router.get("/status")
async def command_status(x_admin_key: str | None = Header(None)):
    """Return the running/stopped state and PID of each command. Requires X-Admin-Key header."""
    _check_admin_key(x_admin_key)
    statuses = {}
    for name in _COMMANDS:
        proc = _running_processes.get(name)
        if proc is not None and _is_running(proc):
            statuses[name] = {"state": "running", "pid": proc.pid}
        else:
            statuses[name] = {"state": "stopped", "pid": None}
    return statuses


@router.post("/stop/{command_name}")
async def stop_command(command_name: str, x_admin_key: str | None = Header(None)):
    """Send SIGTERM to a running command. Requires X-Admin-Key header."""
    _check_admin_key(x_admin_key)
    proc = _running_processes.get(command_name)
    if proc is None or not _is_running(proc):
        return {"status": "not_running", "command": command_name}

    logger.info("stopping_command", command=command_name, pid=proc.pid)
    try:
        proc.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        pass
    return {"status": "stopping", "pid": proc.pid}


async def _start_command(name: str) -> dict:
    """Spawn a subprocess for the given command name, or return already_running."""
    existing = _running_processes.get(name)
    if existing is not None and _is_running(existing):
        logger.info("command_already_running", command=name, pid=existing.pid)
        return {"status": "already_running", "pid": existing.pid}

    cmd = _COMMANDS[name]
    logger.info("starting_command", command=name, cmd=cmd)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(_PROJECT_ROOT),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    _running_processes[name] = proc
    logger.info("command_started", command=name, pid=proc.pid)
    return {"status": "started", "pid": proc.pid}
