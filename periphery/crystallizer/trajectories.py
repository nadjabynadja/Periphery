"""Trajectory detection — find directional movement through embedding spaces.

Tracks cluster centroid positions over time, fits direction vectors, and
detects convergence, divergence, acceleration, and emergence patterns.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import numpy as np
import structlog

from periphery.crystallizer.models import Trajectory, TrajectorySnapshot, ConvergenceAlert

logger = structlog.get_logger(__name__)


class TrajectoryDetector:
    """Detects and tracks trajectories of cluster centroids over time.

    For each cluster, maintains a history of centroid positions. When enough
    snapshots accumulate, fits a direction vector and scores trajectory
    confidence using R-squared.
    """

    def __init__(self, min_snapshots: int = 5) -> None:
        self.min_snapshots = min_snapshots
        # {(space, cluster_id): [TrajectorySnapshot, ...]}
        self._centroid_history: dict[tuple[str, str], list[TrajectorySnapshot]] = {}
        self._trajectories: dict[str, Trajectory] = {}
        self._previous_velocities: dict[str, float] = {}

    @property
    def trajectories(self) -> dict[str, Trajectory]:
        return self._trajectories

    def record_centroids(
        self,
        space: str,
        centroids: dict[int, np.ndarray],
        timestamp: datetime | None = None,
    ) -> None:
        """Record current centroid positions for trajectory fitting."""
        ts = timestamp or datetime.now(timezone.utc)

        for cluster_id, centroid in centroids.items():
            key = (space, f"{space}_{cluster_id}")
            snapshot = TrajectorySnapshot(
                timestamp=ts,
                centroid=centroid.tolist(),
            )
            if key not in self._centroid_history:
                self._centroid_history[key] = []
            self._centroid_history[key].append(snapshot)

    def detect_trajectories(self) -> list[Trajectory]:
        """Fit trajectories for all clusters with enough history.

        Returns list of newly detected or updated trajectories.
        """
        updated: list[Trajectory] = []

        for (space, cluster_id), snapshots in self._centroid_history.items():
            if len(snapshots) < self.min_snapshots:
                continue

            trajectory = self._fit_trajectory(space, cluster_id, snapshots)
            if trajectory is not None:
                traj_key = f"{space}_{cluster_id}"
                self._trajectories[traj_key] = trajectory
                updated.append(trajectory)

        return updated

    def detect_convergences(self, distance_threshold: float = 0.5) -> list[ConvergenceAlert]:
        """Detect pairs of clusters whose centroids are moving toward each other.

        Returns convergence alerts for clusters that are getting closer.
        """
        alerts: list[ConvergenceAlert] = []

        # Group trajectories by space
        by_space: dict[str, list[Trajectory]] = {}
        for traj in self._trajectories.values():
            by_space.setdefault(traj.space, []).append(traj)

        for space, trajs in by_space.items():
            for i, traj_a in enumerate(trajs):
                for traj_b in trajs[i + 1:]:
                    if not traj_a.snapshots or not traj_b.snapshots:
                        continue

                    # Get latest centroids
                    centroid_a = np.array(traj_a.snapshots[-1].centroid)
                    centroid_b = np.array(traj_b.snapshots[-1].centroid)
                    current_dist = float(np.linalg.norm(centroid_a - centroid_b))

                    # Get previous centroids
                    if len(traj_a.snapshots) >= 2 and len(traj_b.snapshots) >= 2:
                        prev_a = np.array(traj_a.snapshots[-2].centroid)
                        prev_b = np.array(traj_b.snapshots[-2].centroid)
                        prev_dist = float(np.linalg.norm(prev_a - prev_b))

                        if prev_dist > current_dist and current_dist < distance_threshold:
                            convergence_rate = (prev_dist - current_dist) / prev_dist
                            # Estimate merge time
                            merge_time = None
                            if convergence_rate > 0:
                                steps_to_merge = current_dist / (prev_dist - current_dist)
                                if len(traj_a.snapshots) >= 2:
                                    dt = (traj_a.snapshots[-1].timestamp - traj_a.snapshots[-2].timestamp)
                                    merge_time = traj_a.snapshots[-1].timestamp + dt * steps_to_merge

                            alerts.append(ConvergenceAlert(
                                cluster_a=traj_a.cluster_id,
                                cluster_b=traj_b.cluster_id,
                                convergence_rate=convergence_rate,
                                estimated_merge_time=merge_time,
                                significance=(
                                    f"Clusters converging in {space} space at "
                                    f"rate {convergence_rate:.2%}, distance {current_dist:.4f}"
                                ),
                            ))

        return alerts

    def cleanup_dissolved(self, active_cluster_ids: set[str]) -> None:
        """Remove trajectory history for clusters that no longer exist."""
        keys_to_remove = [
            key for key in self._centroid_history
            if key[1] not in active_cluster_ids
        ]
        for key in keys_to_remove:
            del self._centroid_history[key]

        traj_keys_to_remove = [
            tid for tid, traj in self._trajectories.items()
            if traj.cluster_id not in active_cluster_ids
        ]
        for tid in traj_keys_to_remove:
            del self._trajectories[tid]

    def _fit_trajectory(
        self,
        space: str,
        cluster_id: str,
        snapshots: list[TrajectorySnapshot],
    ) -> Trajectory | None:
        """Fit a direction vector to centroid snapshots using linear regression.

        Returns a Trajectory object if the fit is meaningful, None otherwise.
        """
        # Use last N snapshots
        recent = snapshots[-max(self.min_snapshots, 20):]
        if len(recent) < self.min_snapshots:
            return None

        # Build matrix: time -> centroid
        centroids = np.array([s.centroid for s in recent])
        t0 = recent[0].timestamp.timestamp()
        times = np.array([(s.timestamp.timestamp() - t0) for s in recent])

        if times[-1] - times[0] == 0:
            return None

        # Normalize time to [0, 1]
        time_range = times[-1] - times[0]
        if time_range == 0:
            return None
        times_norm = (times - times[0]) / time_range

        # Linear regression per dimension
        dim = centroids.shape[1]
        direction = np.zeros(dim)
        ss_res_total = 0.0
        ss_tot_total = 0.0

        for d in range(dim):
            y = centroids[:, d]
            y_mean = np.mean(y)
            ss_tot = np.sum((y - y_mean) ** 2)
            ss_tot_total += ss_tot

            # Fit: y = a * t + b
            t_mean = np.mean(times_norm)
            cov = np.sum((times_norm - t_mean) * (y - y_mean))
            var_t = np.sum((times_norm - t_mean) ** 2)

            if var_t > 0:
                a = cov / var_t
                b = y_mean - a * t_mean
                y_pred = a * times_norm + b
                ss_res = np.sum((y - y_pred) ** 2)
                ss_res_total += ss_res
                direction[d] = a
            else:
                ss_res_total += ss_tot

        # R-squared
        r_squared = 1.0 - (ss_res_total / ss_tot_total) if ss_tot_total > 0 else 0.0

        # Direction vector and velocity
        norm = np.linalg.norm(direction)
        if norm > 0:
            unit_direction = direction / norm
        else:
            unit_direction = direction

        velocity = float(norm)

        # Compute acceleration from previous velocity
        traj_key = f"{space}_{cluster_id}"
        prev_velocity = self._previous_velocities.get(traj_key, velocity)
        acceleration = velocity - prev_velocity
        self._previous_velocities[traj_key] = velocity

        # Determine pattern
        pattern = self._classify_pattern(velocity, acceleration, r_squared)

        # Check for existing trajectory to preserve ID
        existing = self._trajectories.get(traj_key)
        traj_id = existing.trajectory_id if existing else str(uuid.uuid4())[:12]
        first_detected = existing.first_detected if existing else recent[0].timestamp

        return Trajectory(
            trajectory_id=traj_id,
            cluster_id=cluster_id,
            space=space,
            direction_vector=unit_direction.tolist(),
            velocity=velocity,
            acceleration=acceleration,
            confidence=max(0.0, min(1.0, r_squared)),
            pattern=pattern,
            first_detected=first_detected,
            snapshots=recent,
        )

    def _classify_pattern(
        self,
        velocity: float,
        acceleration: float,
        confidence: float,
    ) -> str:
        """Classify trajectory pattern based on velocity and acceleration."""
        if confidence < 0.3:
            return "stable"  # low confidence = no clear direction

        if acceleration > 0.01 and velocity > 0.01:
            return "acceleration"
        if acceleration < -0.01 and velocity > 0.01:
            return "deceleration"
        if velocity > 0.05 and confidence > 0.5:
            return "divergence"
        if velocity < 0.001:
            return "stable"

        return "stable"
