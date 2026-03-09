from contextlib import asynccontextmanager
import aiosqlite
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

@asynccontextmanager
async def get_connection(db_path: str | Path):
    db = await aiosqlite.connect(str(db_path))
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=30000")  # 30 seconds
    await db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()

async def get_persistent_connection(db_path: str | Path) -> aiosqlite.Connection:
    """For connections that stay open for the lifetime of a component."""
    db = await aiosqlite.connect(str(db_path))
    logger.info("db_connection_opened", journal_mode="WAL", busy_timeout=5000)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=30000")  # 30 seconds
    await db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = aiosqlite.Row
    return db