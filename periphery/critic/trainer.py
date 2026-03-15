"""Training pipeline for the CoherenceCritic.

Handles initial training on perturbation datasets, continuous retraining,
model checkpointing, and rollback on regression.

Training approach:
  - Supervised binary cross-entropy on real (label=1) vs perturbed (label=0)
  - Adam optimizer with cosine annealing
  - Periodic retraining with incremental fine-tuning
  - Perturbation dataset versioning (keep last 3)
  - Model checkpointing with validation-based rollback
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from torch.optim.lr_scheduler import CosineAnnealingLR
from torch.utils.data import DataLoader, TensorDataset

from periphery.critic.features import to_input_vector
from periphery.critic.network import CoherenceCritic
from periphery.critic.perturbations import PerturbationSample

logger = logging.getLogger(__name__)


class CriticTrainer:
    """Trains and retrains the CoherenceCritic on perturbation datasets."""

    def __init__(
        self,
        model: CoherenceCritic,
        device: str = "cpu",
        checkpoint_dir: str = "./data/critic_checkpoints",
        training_dir: str = "./data/critic_training",
        max_checkpoints: int = 5,
        max_datasets_kept: int = 3,
    ):
        self.model = model
        self.device = device
        self.checkpoint_dir = Path(checkpoint_dir)
        self.training_dir = Path(training_dir)
        self.max_checkpoints = max_checkpoints
        self.max_datasets_kept = max_datasets_kept

        self.optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        self.criterion = nn.BCELoss()

        self._current_version = 0
        self._best_val_accuracy = 0.0
        self._training_history: list[dict[str, Any]] = []
        self._last_val_data: tuple[torch.Tensor, torch.Tensor] | None = None

    def train_on_samples(
        self,
        samples: list[PerturbationSample],
        epochs: int = 50,
        validation_split: float = 0.2,
        batch_size: int = 32,
        fine_tune: bool = False,
    ) -> dict[str, Any]:
        """Train the Critic on a perturbation dataset.

        Args:
            samples: list of PerturbationSample (real + perturbed)
            epochs: number of training epochs
            validation_split: fraction of data for validation
            batch_size: training batch size
            fine_tune: if True, use lower learning rate for incremental training

        Returns:
            dict with training metrics
        """
        if not samples:
            return {"status": "skipped", "reason": "no_samples"}

        # Convert samples to tensors
        X, y = self._samples_to_tensors(samples)
        if X.shape[0] < 4:
            return {"status": "skipped", "reason": "too_few_samples"}

        # Split train/val
        n_val = max(1, int(X.shape[0] * validation_split))
        indices = torch.randperm(X.shape[0])
        val_indices = indices[:n_val]
        train_indices = indices[n_val:]

        X_train, y_train = X[train_indices], y[train_indices]
        X_val, y_val = X[val_indices], y[val_indices]

        # Set up optimizer
        lr = 5e-4 if fine_tune else 1e-3
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        scheduler = CosineAnnealingLR(self.optimizer, T_max=epochs)

        # Training loop
        train_dataset = TensorDataset(X_train, y_train)
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)

        self.model.train()
        self.model.to(self.device)

        train_losses = []
        val_accuracies = []

        for epoch in range(epochs):
            epoch_loss = 0.0
            n_batches = 0

            for batch_X, batch_y in train_loader:
                batch_X = batch_X.to(self.device)
                batch_y = batch_y.to(self.device)

                self.optimizer.zero_grad()
                predictions = self.model(batch_X)
                loss = self.criterion(predictions, batch_y)
                loss.backward()
                self.optimizer.step()

                epoch_loss += loss.item()
                n_batches += 1

            scheduler.step()
            avg_loss = epoch_loss / max(n_batches, 1)
            train_losses.append(avg_loss)

            # Validation
            val_acc = self._evaluate(X_val, y_val)
            val_accuracies.append(val_acc)

            if epoch % 10 == 0 or epoch == epochs - 1:
                logger.info(
                    "critic_training_epoch %d loss=%.4f val_acc=%.4f",
                    epoch, avg_loss, val_acc,
                )

        final_val_acc = val_accuracies[-1] if val_accuracies else 0.0

        # Store validation data for calibrator fitting
        self._last_val_data = (X_val, y_val)

        result = {
            "status": "trained",
            "epochs": epochs,
            "final_loss": train_losses[-1] if train_losses else 0.0,
            "final_val_accuracy": final_val_acc,
            "train_samples": int(X_train.shape[0]),
            "val_samples": int(X_val.shape[0]),
            "fine_tune": fine_tune,
        }

        self._training_history.append(result)
        return result

    def _evaluate(self, X_val: torch.Tensor, y_val: torch.Tensor) -> float:
        """Evaluate model accuracy on validation set."""
        self.model.eval()
        with torch.no_grad():
            X_val_dev = X_val.to(self.device)
            y_val_dev = y_val.to(self.device)
            predictions = self.model(X_val_dev)
            predicted_labels = (predictions > 0.5).float()
            accuracy = (predicted_labels == y_val_dev).float().mean().item()
        self.model.train()
        return accuracy

    def _samples_to_tensors(
        self, samples: list[PerturbationSample]
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Convert PerturbationSamples to input tensors."""
        X_list = []
        y_list = []

        for sample in samples:
            vec = to_input_vector(sample.structure_type, sample.features)
            X_list.append(vec)
            y_list.append(0.0 if sample.is_perturbed else 1.0)

        X = torch.tensor(np.array(X_list), dtype=torch.float32)
        y = torch.tensor(y_list, dtype=torch.float32)
        return X, y

    def save_checkpoint(
        self,
        val_accuracy: float = 0.0,
        dataset_size: int = 0,
        calibration_params: dict[str, Any] | None = None,
    ) -> str:
        """Save a model checkpoint with metadata."""
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

        self._current_version += 1
        filename = f"critic_v{self._current_version}.pt"
        filepath = self.checkpoint_dir / filename

        checkpoint = {
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "version": self._current_version,
            "training_date": time.time(),
            "val_accuracy": val_accuracy,
            "dataset_size": dataset_size,
            "input_dim": self.model.input_dim,
            "hidden_dim": self.model.hidden_dim,
            "calibration_params": calibration_params,
        }

        torch.save(checkpoint, filepath)
        logger.info("critic_checkpoint_saved path=%s version=%d", str(filepath), self._current_version)

        self._prune_checkpoints()
        return str(filepath)

    def load_checkpoint(self, path: str | None = None) -> dict[str, Any]:
        """Load a model checkpoint. If path is None, loads the latest."""
        if path is None:
            path = self._find_latest_checkpoint()
            if path is None:
                return {"status": "no_checkpoint"}

        checkpoint = torch.load(path, map_location=self.device, weights_only=False)
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        self._current_version = checkpoint.get("version", 0)

        logger.info("critic_checkpoint_loaded path=%s version=%d", path, self._current_version)
        return {
            "status": "loaded",
            "version": checkpoint.get("version"),
            "val_accuracy": checkpoint.get("val_accuracy"),
            "calibration_params": checkpoint.get("calibration_params"),
        }

    def _find_latest_checkpoint(self) -> str | None:
        if not self.checkpoint_dir.exists():
            return None
        checkpoints = sorted(
            self.checkpoint_dir.glob("critic_v*.pt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return str(checkpoints[0]) if checkpoints else None

    def _prune_checkpoints(self) -> None:
        if not self.checkpoint_dir.exists():
            return
        checkpoints = sorted(
            self.checkpoint_dir.glob("critic_v*.pt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in checkpoints[self.max_checkpoints:]:
            old.unlink(missing_ok=True)

    def save_perturbation_dataset(self, samples: list[PerturbationSample]) -> str:
        """Save a perturbation dataset for versioning."""
        self.training_dir.mkdir(parents=True, exist_ok=True)

        version = self._current_version
        filename = f"perturbations_v{version}.json"
        filepath = self.training_dir / filename

        data = [s.to_dict() for s in samples]
        with open(filepath, "w") as f:
            json.dump(data, f)

        # Prune old datasets
        datasets = sorted(
            self.training_dir.glob("perturbations_v*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in datasets[self.max_datasets_kept:]:
            old.unlink(missing_ok=True)

        return str(filepath)

    def should_retrain(
        self,
        crystallizer_runs_since_last: int,
        hours_since_last: float,
        max_runs: int = 10,
        max_hours: float = 24.0,
    ) -> bool:
        """Determine if retraining is needed based on schedule."""
        if crystallizer_runs_since_last >= max_runs:
            return True
        if hours_since_last >= max_hours:
            return True
        return False

    def retrain_with_rollback(
        self,
        samples: list[PerturbationSample],
        fine_tune_epochs: int = 20,
        validation_split: float = 0.2,
        calibrator: Any | None = None,
    ) -> dict[str, Any]:
        """Retrain incrementally with rollback on regression.

        If a calibrator is provided and training succeeds (no rollback),
        the calibrator is fitted on validation data and its params are
        saved into the checkpoint.
        """
        pre_retrain_state = {
            k: v.clone() for k, v in self.model.state_dict().items()
        }
        pre_val_accuracy = self._best_val_accuracy

        result = self.train_on_samples(
            samples,
            epochs=fine_tune_epochs,
            validation_split=validation_split,
            fine_tune=True,
        )

        new_val_accuracy = result.get("final_val_accuracy", 0.0)

        if pre_val_accuracy > 0 and new_val_accuracy < pre_val_accuracy * 0.95:
            self.model.load_state_dict(pre_retrain_state)
            result["rolled_back"] = True
            result["rollback_reason"] = (
                f"New accuracy {new_val_accuracy:.4f} < "
                f"threshold {pre_val_accuracy * 0.95:.4f}"
            )
            logger.warning(
                "critic_retrain_rollback new=%.4f prev=%.4f",
                new_val_accuracy, pre_val_accuracy,
            )
        else:
            self._best_val_accuracy = new_val_accuracy
            result["rolled_back"] = False

            # Fit calibrator on validation predictions
            calibration_params = None
            if calibrator is not None and self._last_val_data is not None:
                X_val, y_val = self._last_val_data
                self.model.eval()
                with torch.no_grad():
                    raw_preds = self.model(X_val.to(self.device)).cpu().numpy()
                self.model.train()
                calibrator.fit(raw_preds, y_val.numpy())
                calibration_params = calibrator.get_params()

            self.save_checkpoint(
                val_accuracy=new_val_accuracy,
                dataset_size=len(samples),
                calibration_params=calibration_params,
            )
            self.save_perturbation_dataset(samples)

        return result

    @property
    def model_version(self) -> int:
        return self._current_version

    @property
    def training_history(self) -> list[dict[str, Any]]:
        return self._training_history
