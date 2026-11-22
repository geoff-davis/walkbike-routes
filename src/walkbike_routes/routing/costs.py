"""Composable route-choice cost components in generalized seconds."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostComponents:
    """Auditable physical time and added generalized disutility."""

    movement_time_s: float = 0.0
    crossing_wait_time_s: float = 0.0
    travel_time_s: float = 0.0
    stress_cost_s: float = 0.0
    facility_cost_s: float = 0.0
    crossing_cost_s: float = 0.0
    turn_cost_s: float = 0.0
    control_cost_s: float = 0.0
    transition_cost_s: float = 0.0

    @property
    def generalized_cost_s(self) -> float:
        """Return physical time plus every added cost component."""
        return sum(
            (
                self.travel_time_s,
                self.stress_cost_s,
                self.facility_cost_s,
                self.crossing_cost_s,
                self.turn_cost_s,
                self.control_cost_s,
                self.transition_cost_s,
            )
        )

    def __add__(self, other: CostComponents) -> CostComponents:
        return CostComponents(
            movement_time_s=self.movement_time_s + other.movement_time_s,
            crossing_wait_time_s=(self.crossing_wait_time_s + other.crossing_wait_time_s),
            travel_time_s=self.travel_time_s + other.travel_time_s,
            stress_cost_s=self.stress_cost_s + other.stress_cost_s,
            facility_cost_s=self.facility_cost_s + other.facility_cost_s,
            crossing_cost_s=self.crossing_cost_s + other.crossing_cost_s,
            turn_cost_s=self.turn_cost_s + other.turn_cost_s,
            control_cost_s=self.control_cost_s + other.control_cost_s,
            transition_cost_s=self.transition_cost_s + other.transition_cost_s,
        )

    def as_link_attributes(self) -> dict[str, float]:
        """Serialize costs onto a canonical routable link."""
        return {
            "movement_time_s": self.movement_time_s,
            "crossing_wait_time_s": self.crossing_wait_time_s,
            "travel_time_s": self.travel_time_s,
            "stress_cost_s": self.stress_cost_s,
            "facility_cost_s": self.facility_cost_s,
            "crossing_cost_s": self.crossing_cost_s,
            "turn_cost_s": self.turn_cost_s,
            "control_cost_s": self.control_cost_s,
            "transition_cost_s": self.transition_cost_s,
            "generalized_cost_s": self.generalized_cost_s,
        }
