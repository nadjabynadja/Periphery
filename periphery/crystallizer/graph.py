from collections import defaultdict

import networkx as nx
import numpy as np

from periphery.models import Document, GraphNode, GraphEdge, OntologySnapshot


class OntologyGraph:
    """Living ontology graph built from emergent cluster structure."""

    def __init__(self):
        self.graph = nx.Graph()
        self._cluster_labels: dict[int, str] = {}

    def build_from_clusters(
        self,
        documents: list[Document],
        doc_ids: list[str],
        labels: np.ndarray,
        vectors: np.ndarray,
        coherence_scores: dict[int, float] | None = None,
    ) -> None:
        """Rebuild the graph from clustering results."""
        self.graph.clear()
        coherence_scores = coherence_scores or {}

        # Group documents by cluster
        clusters: dict[int, list[int]] = defaultdict(list)
        for i, label in enumerate(labels):
            clusters[int(label)].append(i)

        # Add document nodes
        for i, doc_id in enumerate(doc_ids):
            cluster_id = int(labels[i])
            doc = documents[i] if i < len(documents) else None
            self.graph.add_node(
                doc_id,
                label=doc.content[:80] if doc else doc_id,
                cluster_id=cluster_id,
                node_type="document",
                coherence_score=None,
            )

        # Add cluster super-nodes and connect members
        for cluster_id, member_indices in clusters.items():
            if cluster_id == -1:
                continue  # Skip noise

            cluster_node_id = f"cluster_{cluster_id}"
            score = coherence_scores.get(cluster_id)
            self.graph.add_node(
                cluster_node_id,
                label=self._cluster_labels.get(cluster_id, f"Cluster {cluster_id}"),
                cluster_id=cluster_id,
                node_type="cluster",
                coherence_score=score,
            )

            # Connect cluster node to its members
            for idx in member_indices:
                doc_id = doc_ids[idx]
                self.graph.add_edge(cluster_node_id, doc_id, weight=1.0)

            # Add intra-cluster edges weighted by similarity
            if len(member_indices) > 1:
                cluster_vecs = vectors[member_indices]
                # Compute pairwise cosine similarity
                sims = cluster_vecs @ cluster_vecs.T
                for a in range(len(member_indices)):
                    for b in range(a + 1, len(member_indices)):
                        sim = float(sims[a, b])
                        if sim > 0.3:  # Only add meaningful edges
                            self.graph.add_edge(
                                doc_ids[member_indices[a]],
                                doc_ids[member_indices[b]],
                                weight=sim,
                            )

        # Add inter-cluster edges via centroid similarity
        cluster_ids = [cid for cid in clusters if cid != -1]
        if len(cluster_ids) > 1:
            centroids = {}
            for cid in cluster_ids:
                idx = clusters[cid]
                centroids[cid] = vectors[idx].mean(axis=0)

            for i, cid_a in enumerate(cluster_ids):
                for cid_b in cluster_ids[i + 1:]:
                    sim = float(centroids[cid_a] @ centroids[cid_b])
                    if sim > 0.2:
                        self.graph.add_edge(
                            f"cluster_{cid_a}",
                            f"cluster_{cid_b}",
                            weight=sim,
                        )

    def get_subgraph(self, node_id: str, depth: int = 2) -> OntologySnapshot:
        """Get a subgraph around a node via BFS."""
        if node_id not in self.graph:
            return OntologySnapshot(nodes=[], edges=[])

        visited = set()
        frontier = {node_id}
        for _ in range(depth):
            next_frontier = set()
            for n in frontier:
                visited.add(n)
                for neighbor in self.graph.neighbors(n):
                    if neighbor not in visited:
                        next_frontier.add(neighbor)
            frontier = next_frontier
        visited.update(frontier)

        return self._snapshot_from_nodes(visited)

    def to_snapshot(self) -> OntologySnapshot:
        """Convert entire graph to a serializable snapshot."""
        return self._snapshot_from_nodes(set(self.graph.nodes))

    def _snapshot_from_nodes(self, node_ids: set[str]) -> OntologySnapshot:
        nodes = []
        for nid in node_ids:
            data = self.graph.nodes[nid]
            nodes.append(GraphNode(
                id=nid,
                label=data.get("label", nid),
                cluster_id=data.get("cluster_id"),
                coherence_score=data.get("coherence_score"),
                node_type=data.get("node_type", "document"),
            ))

        edges = []
        for u, v, data in self.graph.edges(data=True):
            if u in node_ids and v in node_ids:
                edges.append(GraphEdge(source=u, target=v, weight=data.get("weight", 1.0)))

        cluster_nodes = [n for n in nodes if n.node_type == "cluster"]
        doc_nodes = [n for n in nodes if n.node_type == "document"]

        return OntologySnapshot(
            nodes=nodes,
            edges=edges,
            cluster_count=len(cluster_nodes),
            document_count=len(doc_nodes),
        )

    def find_bridges(self) -> list[str]:
        """Find documents that bridge separate clusters (high betweenness)."""
        if self.graph.number_of_nodes() == 0:
            return []
        bc = nx.betweenness_centrality(self.graph)
        doc_bc = {
            n: score for n, score in bc.items()
            if self.graph.nodes[n].get("node_type") == "document" and score > 0
        }
        return sorted(doc_bc, key=doc_bc.get, reverse=True)[:10]
