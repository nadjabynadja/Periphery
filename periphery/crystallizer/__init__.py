"""Crystallizer — the core analytical engine of Periphery.

Performs continuous topological and statistical analysis over the multi-space
embedding environment, detecting emergent structure and producing a living
ontology snapshot.

Sub-modules:
  - clustering: HDBSCAN multi-space cluster detection
  - trajectories: Centroid tracking and trajectory pattern detection
  - gradients: Relational gradient analysis between clusters
  - anomalies: Outlier detection and scoring
  - labeler: Auto-labeling clusters with template + optional LLM
  - persistence: SQLite storage for snapshots and history
  - graph: Legacy ontology graph (NetworkX)
  - models: Pydantic data models for all structural observations
  - worker: Main CrystallizerWorker that orchestrates everything
  - router: FastAPI endpoints
"""
