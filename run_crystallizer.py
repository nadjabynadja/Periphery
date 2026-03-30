python3 -c
import asyncio
from periphery.crystallizer.worker import CrystallizerWorker

async def test():
    # Adjust these paths to match your setup
    worker = CrystallizerWorker(db_path='./data/analytical.db', index_dir='./data/indices')
    await worker.initialize()
    result = await worker.crystallize()
    print(result)

asyncio.run(test())