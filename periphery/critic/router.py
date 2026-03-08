from fastapi import APIRouter

from periphery.models import CriticScore

router = APIRouter(prefix="/critic", tags=["critic"])

# Set by main.py on startup
_critic_state = None


def set_critic_state(state) -> None:
    global _critic_state
    _critic_state = state


def get_critic_state():
    assert _critic_state is not None, "Critic not initialized"
    return _critic_state


@router.get("/scores", response_model=list[CriticScore])
async def get_scores():
    """Get coherence scores for all clusters."""
    state = get_critic_state()
    clusters = state["worker"].clusters
    return [
        CriticScore(
            cluster_id=c.id,
            coherence_score=c.coherence_score or 0.0,
            document_count=len(c.document_ids),
        )
        for c in clusters
    ]


@router.post("/evaluate")
async def evaluate_document(document_id: str):
    """Evaluate coherence of a specific document within its cluster."""
    from periphery.critic.scoring import score_document

    state = get_critic_state()
    store = state["store"]
    worker = state["worker"]
    model = state["model"]

    # Find which cluster the document belongs to
    doc_ids = store.get_all_ids()
    if document_id not in doc_ids:
        return {"error": "Document not found"}

    if worker.labels is None:
        return {"error": "No clustering results available"}

    idx = doc_ids.index(document_id)
    label = int(worker.labels[idx])

    if label == -1:
        return {"document_id": document_id, "cluster_id": -1, "coherence_score": 0.0, "status": "noise"}

    vectors = store.get_all_vectors()
    doc_vec = vectors[idx]
    cluster_mask = worker.labels == label
    cluster_vecs = vectors[cluster_mask]

    score = score_document(model, doc_vec, cluster_vecs)
    return {"document_id": document_id, "cluster_id": label, "coherence_score": score}


@router.get("/outliers")
async def get_outliers(limit: int = 10):
    """Get documents with lowest coherence scores."""
    from periphery.critic.scoring import score_document

    state = get_critic_state()
    store = state["store"]
    worker = state["worker"]
    model = state["model"]

    if worker.labels is None:
        return {"outliers": []}

    doc_ids = store.get_all_ids()
    vectors = store.get_all_vectors()
    scores = []

    for i, doc_id in enumerate(doc_ids):
        label = int(worker.labels[i])
        if label == -1:
            scores.append((doc_id, label, 0.0))
            continue
        cluster_mask = worker.labels == label
        cluster_vecs = vectors[cluster_mask]
        s = score_document(model, vectors[i], cluster_vecs)
        scores.append((doc_id, label, s))

    scores.sort(key=lambda x: x[2])
    return {
        "outliers": [
            {"document_id": did, "cluster_id": cid, "coherence_score": s}
            for did, cid, s in scores[:limit]
        ]
    }


@router.post("/train")
async def trigger_training(epochs: int = 10):
    """Trigger adversarial training of the critic network."""
    state = get_critic_state()
    store = state["store"]
    worker = state["worker"]
    trainer = state["trainer"]

    if worker.labels is None:
        return {"status": "skipped", "reason": "no_clustering_results"}

    vectors = store.get_all_vectors()
    results = trainer.train_multiple(vectors, worker.labels, epochs=epochs)
    return {"training_results": results}
