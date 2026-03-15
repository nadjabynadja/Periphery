"""CoherenceCritic neural network.

A feedforward network that takes structured feature vectors and outputs
coherence scores between 0.0 and 1.0. Evaluates structural plausibility,
not factual correctness.

The network is deliberately simple — it evaluates pre-computed feature
vectors, not raw text or embeddings. Training is fast and stable because
negative examples come from a deterministic perturbation engine, not a
learned generator.
"""

from __future__ import annotations

import torch
import torch.nn as nn

from periphery.critic.features import TOTAL_INPUT_DIM


class CoherenceCritic(nn.Module):
    """Neural coherence scoring network.

    Input: fixed-size feature vector (type prefix + padded features).
    Output: coherence score between 0.0 and 1.0.
    """

    def __init__(self, input_dim: int = TOTAL_INPUT_DIM, hidden_dim: int = 128):
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim

        self.network = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(hidden_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Score input feature vectors for structural coherence.

        Args:
            x: tensor of shape (batch_size, input_dim)

        Returns:
            tensor of shape (batch_size,) with scores in [0, 1]
        """
        return self.network(x).squeeze(-1)
