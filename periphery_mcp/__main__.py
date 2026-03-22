"""Allow `python -m periphery_mcp` to start the server."""
import asyncio
from periphery_mcp.server import main

asyncio.run(main())
