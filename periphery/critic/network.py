import torch
import torch.nn as nn


class CoherenceNet(nn.Module):
    """
    Coherence scoring network.

    Evaluates whether a pair of embeddings belongs to a coherent structure.
    Input: concatenated pair of embeddings (2 * dim).
    Output: coherence score between 0 and 1.
    """

    def __init__(self, dim: int):
        super().__init__()
        self.dim = dim
        self.net = nn.Sequential(
            nn.Linear(2 * dim, dim),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(dim, dim // 2),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(dim // 2, 1),
            nn.Sigmoid(),
        )

    def forward(self, pairs: torch.Tensor) -> torch.Tensor:
        """Score pairs of embeddings for structural coherence."""
        return self.net(pairs).squeeze(-1)


class StructuralDiscriminator(nn.Module):
    """
    Adversarial discriminator for structural plausibility.

    Learns to distinguish real cluster structure from synthetic perturbations.
    """

    def __init__(self, dim: int):
        super().__init__()
        self.dim = dim
        self.net = nn.Sequential(
            nn.Linear(2 * dim, dim),
            nn.LeakyReLU(0.2),
            nn.Dropout(0.2),
            nn.Linear(dim, dim // 2),
            nn.LeakyReLU(0.2),
            nn.Dropout(0.1),
            nn.Linear(dim // 2, 1),
            nn.Sigmoid(),
        )

    def forward(self, pairs: torch.Tensor) -> torch.Tensor:
        return self.net(pairs).squeeze(-1)
