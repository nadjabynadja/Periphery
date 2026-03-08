import logging

import numpy as np
import torch
import torch.nn as nn

from periphery.critic.network import CoherenceNet, StructuralDiscriminator

logger = logging.getLogger(__name__)


class AdversarialTrainer:
    """
    Adversarial training for the coherence critic.

    Generates synthetic structural perturbations (shuffled cluster assignments)
    and trains the critic to distinguish coherent from incoherent structure.
    This is NOT a GAN — no generation, just structural plausibility scoring.
    """

    def __init__(self, coherence_net: CoherenceNet, device: str = "cpu"):
        self.coherence_net = coherence_net
        self.discriminator = StructuralDiscriminator(coherence_net.dim).to(device)
        self.device = device

        self.coherence_opt = torch.optim.Adam(coherence_net.parameters(), lr=1e-3)
        self.disc_opt = torch.optim.Adam(self.discriminator.parameters(), lr=1e-3)
        self.criterion = nn.BCELoss()

    def _sample_pairs(
        self, vectors: np.ndarray, labels: np.ndarray, n_pairs: int, same_cluster: bool
    ) -> torch.Tensor:
        """Sample pairs from same or different clusters."""
        n = vectors.shape[0]
        pairs = []
        attempts = 0
        max_attempts = n_pairs * 10

        while len(pairs) < n_pairs and attempts < max_attempts:
            attempts += 1
            i, j = np.random.randint(0, n, size=2)
            if i == j:
                continue
            if same_cluster and labels[i] != labels[j]:
                continue
            if not same_cluster and labels[i] == labels[j]:
                continue
            if labels[i] == -1 or labels[j] == -1:
                continue
            pair = np.concatenate([vectors[i], vectors[j]])
            pairs.append(pair)

        if not pairs:
            return torch.empty(0, vectors.shape[1] * 2, device=self.device)

        return torch.tensor(np.array(pairs), dtype=torch.float32, device=self.device)

    def _generate_perturbations(
        self, vectors: np.ndarray, labels: np.ndarray, n_pairs: int
    ) -> torch.Tensor:
        """Generate plausible-but-wrong structural pairs by shuffling cluster assignments."""
        # Shuffle labels to create synthetic wrong structure
        shuffled = labels.copy()
        np.random.shuffle(shuffled)
        return self._sample_pairs(vectors, shuffled, n_pairs, same_cluster=True)

    def train_epoch(
        self, vectors: np.ndarray, labels: np.ndarray, n_pairs: int = 256
    ) -> dict:
        """Run one training epoch."""
        unique_labels = set(labels) - {-1}
        if len(unique_labels) < 2:
            return {"status": "skipped", "reason": "insufficient_clusters"}

        # Generate real pairs (from same cluster) and negative pairs (cross-cluster)
        real_pairs = self._sample_pairs(vectors, labels, n_pairs, same_cluster=True)
        neg_pairs = self._sample_pairs(vectors, labels, n_pairs, same_cluster=False)
        perturbed = self._generate_perturbations(vectors, labels, n_pairs)

        if real_pairs.shape[0] == 0 or neg_pairs.shape[0] == 0:
            return {"status": "skipped", "reason": "insufficient_pairs"}

        # --- Train discriminator ---
        self.discriminator.train()
        self.disc_opt.zero_grad()

        real_scores = self.discriminator(real_pairs)
        real_labels = torch.ones(real_scores.shape[0], device=self.device)
        loss_real = self.criterion(real_scores, real_labels)

        if perturbed.shape[0] > 0:
            fake_scores = self.discriminator(perturbed)
            fake_labels = torch.zeros(fake_scores.shape[0], device=self.device)
            loss_fake = self.criterion(fake_scores, fake_labels)
        else:
            loss_fake = torch.tensor(0.0)

        disc_loss = loss_real + loss_fake
        disc_loss.backward()
        self.disc_opt.step()

        # --- Train coherence net ---
        self.coherence_net.train()
        self.coherence_opt.zero_grad()

        coh_real = self.coherence_net(real_pairs)
        coh_neg = self.coherence_net(neg_pairs)

        # Coherence net should score same-cluster pairs high, cross-cluster low
        coh_loss = (
            self.criterion(coh_real, torch.ones_like(coh_real))
            + self.criterion(coh_neg, torch.zeros_like(coh_neg))
        )
        coh_loss.backward()
        self.coherence_opt.step()

        return {
            "status": "trained",
            "disc_loss": float(disc_loss.item()),
            "coherence_loss": float(coh_loss.item()),
            "real_pairs": int(real_pairs.shape[0]),
            "neg_pairs": int(neg_pairs.shape[0]),
        }

    def train_multiple(
        self, vectors: np.ndarray, labels: np.ndarray, epochs: int = 10
    ) -> list[dict]:
        """Run multiple training epochs."""
        results = []
        for epoch in range(epochs):
            result = self.train_epoch(vectors, labels)
            result["epoch"] = epoch
            results.append(result)
            if result["status"] == "skipped":
                break
        return results
