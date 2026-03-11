"""Pipeline command endpoints.

Allows operators to trigger ingestion and collection processes
from the UI instead of SSH-ing into the box.
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import structlog
from fastapi import APIRouter

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/commands", tags=["commands"])

# Project root — two parents up from this file (periphery/commands/router.py → project root)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Track running subprocesses keyed by command name
_running_processes: dict[str, asyncio.subprocess.Process] = {}

# Map of command names to their arguments
_COMMANDS: dict[str, list[str]] = {
    "pipeline": [
        str(_PROJECT_ROOT / ".venv" / "bin" / "python"),
        "-m", "periphery.pipeline",
    ],
    "rss": [
        str(_PROJECT_ROOT / ".venv" / "bin" / "python"),
        "-m", "periphery.rss_ingest",
        "--no-server", "--duration", "30",
    ],
    "rss-continuous": [
        str(_PROJECT_ROOT / ".venv" / "bin" / "python"),
        "-m", "periphery.rss_ingest",
        "--no-server",
    ],
}


def _is_running(proc: asyncio.subprocess.Process) -> bool:
    """Check if a subprocess is still running."""
    return proc.returncode is None


@router.post("/force-ingest")
async def force_ingest():
    """Run the full ingestion pipeline (.venv/bin/python -m periphery.pipeline)."""
    return await _start_command("pipeline")


@router.post("/run-collect")
async def run_collect():
    """Run RSS collection for 30 seconds (.venv/bin/python -m periphery.rss_ingest --no-server --duration 30)."""
    return await _start_command("rss")


@router.post("/continuous-collect")
async def continuous_collect():
    """Run continuous RSS collection (.venv/bin/python -m periphery.rss_ingest --no-server)."""
    return await _start_command("rss-continuous")


@router.get("/status")
async def command_status():
    """Return the running/stopped state and PID of each command."""
    statuses = {}
    for name in _COMMANDS:
        proc = _running_processes.get(name)
        if proc is not None and _is_running(proc):
            statuses[name] = {"state": "running", "pid": proc.pid}
        else:
            statuses[name] = {"state": "stopped", "pid": None}
    return statuses


@router.post("/stop/{command_name}")
async def stop_command(command_name: str):
    """Send SIGTERM to a running command."""
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
