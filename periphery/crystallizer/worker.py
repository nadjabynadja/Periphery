import asyncio
import logging
from datetime import datetime, timezone

import numpy as np

from periphery.crystallizer.clustering import run_clustering
from periphery.crystallizer.graph import OntologyGraph
from periphery.ingest.store import FAISSStore
from periphery.models import Cluster, Document

logger = logging.getLogger(__name__)


class CrystallizerWorker:
    """Background worker that continuously analyzes the embedding space."""

    def __init__(
        self,
        store: FAISSStore,
        documents: dict[str, Document],
        interval: int = 300,
    ):
        self.store = store
        self.documents = documents
        self.interval = interval
        self.graph = OntologyGraph()
        self.clusters: list[Cluster] = []
        self.labels: np.ndarray | None = None
        self.stats: dict = {}
        self.last_run: datetime | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        # Callback for critic scoring — set by main.py
        self.on_crystallize: callable | None = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Crystallizer worker started (interval=%ds)", self.interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Crystallizer worker stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.crystallize()
            except Exception:
                logger.exception("Crystallizer run failed")
            await asyncio.sleep(self.interval)

    async def crystallize(self) -> dict:
        """Run one crystallization pass."""
        vectors = self.store.get_all_vectors()
        if vectors.shape[0] < 2:
            logger.info("Not enough vectors for clustering (%d)", vectors.shape[0])
            return {"status": "skipped", "reason": "insufficient_data"}

        doc_ids = self.store.get_all_ids()

        # Adaptive min_cluster_size based on data volume
        min_size = max(2, min(5, vectors.shape[0] // 10))
        labels, stats = run_clustering(vectors, min_cluster_size=min_size, min_samples=max(1, min_size - 1))
        self.labels = labels
        self.stats = stats

        # Build cluster objects
        cluster_map: dict[int, list[str]] = {}
        for i, label in enumerate(labels):
            label_int = int(label)
            if label_int == -1:
                continue
            cluster_map.setdefault(label_int, []).append(doc_ids[i])

        # Run critic scoring if available
        coherence_scores = {}
        if self.on_crystallize:
            coherence_scores = await self.on_crystallize(vectors, labels)

        self.clusters = [
            Cluster(
                id=cid,
                document_ids=members,
                coherence_score=coherence_scores.get(cid),
            )
            for cid, members in cluster_map.items()
        ]

        # Build ontology graph
        docs = [self.documents.get(did, Document(id=did, content="")) for did in doc_ids]
        self.graph.build_from_clusters(docs, doc_ids, labels, vectors, coherence_scores)

        self.last_run = datetime.now(timezone.utc)
        logger.info(
            "Crystallization complete: %d clusters, %d noise points, %d documents",
            stats["n_clusters"], stats["noise_count"], len(doc_ids),
        )
        return stats
