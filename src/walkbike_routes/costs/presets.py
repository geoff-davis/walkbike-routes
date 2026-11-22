"""Route-choice presets: named, cited sets of cost settings for each mode.

A preset is a YAML file in ``walkbike_routes/presets/<mode>/``.  It names its
source, the kind of cost function it feeds and that function's parameters.
The kinds so far:

- ``pedestrian_evidence``: distance scaled by sidewalk evidence, plus signal
  waits (:class:`~walkbike_routes.network.pedestrian.PedestrianProfile`);
- ``lts_factors``: distance scaled by traffic stress level, plus crossing and
  signal waits (:class:`~walkbike_routes.network.bicycle.BicycleProfile`);
- ``broach_2011``: Broach, Gliebe and Dill's Portland cycling route-choice
  model (:class:`~walkbike_routes.network.broach.BroachProfile`), which also
  sets how trips split across routes;
- ``broach_walk_2016``: Broach's Portland walking route-choice model
  (:class:`~walkbike_routes.network.broach_walk.WalkBroachProfile`), which
  also sets how trips split across routes;
- ``motor_delays``: running time plus signal, stop and turn delays
  (:class:`~walkbike_routes.network.motor.MotorPenaltyProfile`).
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml

from walkbike_routes.network.bicycle import BicycleProfile
from walkbike_routes.network.broach import BroachProfile
from walkbike_routes.network.broach_walk import WalkBroachProfile
from walkbike_routes.network.motor import MotorPenaltyProfile
from walkbike_routes.network.pedestrian import PedestrianProfile

MODES = ("walk", "bike", "car")
KINDS = {
    "walk": ("pedestrian_evidence", "broach_walk_2016"),
    "bike": ("lts_factors", "broach_2011"),
    "car": ("motor_delays",),
}
OBJECTIVES = ("generalized_cost_s", "physical_length_m", "travel_time_s")
FIELDS = {"name", "mode", "kind", "title", "source", "objective", "parameters"}


class PresetError(ValueError):
    """A preset that is missing or cannot be used."""


@dataclass(frozen=True)
class Preset:
    name: str
    mode: str
    kind: str
    title: str
    source: str
    objective: str
    parameters: dict[str, Any]

    def profile(
        self,
    ) -> (
        PedestrianProfile | WalkBroachProfile | BicycleProfile | BroachProfile | MotorPenaltyProfile
    ):
        """The network builder's profile for this preset."""
        settings = dict(self.parameters)
        try:
            if self.kind == "pedestrian_evidence":
                return PedestrianProfile(self.name, **settings)
            if self.kind == "lts_factors":
                if "lts_cost_factors" in settings:
                    settings["lts_cost_factors"] = tuple(settings["lts_cost_factors"])
                return BicycleProfile(self.name, **settings)
            if self.kind == "broach_2011":
                return BroachProfile(self.name, **settings)
            if self.kind == "broach_walk_2016":
                return WalkBroachProfile(self.name, **settings)
            return MotorPenaltyProfile(self.name, **settings)
        except (TypeError, ValueError) as err:  # an unknown, missing or bad parameter
            raise PresetError(f"preset {self.mode}/{self.name}: {err}") from err


def names(mode: str) -> tuple[str, ...]:
    """The presets available for a mode."""
    if mode not in MODES:
        raise PresetError(f"unknown mode {mode!r}")
    folder = resources.files("walkbike_routes.presets").joinpath(mode)
    return tuple(
        sorted(p.name.removesuffix(".yaml") for p in folder.iterdir() if p.name.endswith(".yaml"))
    )


def load(mode: str, name: str) -> Preset:
    """Read and check one preset."""
    if name not in names(mode):
        raise PresetError(f"no {mode} preset {name!r}; choose from: {', '.join(names(mode))}")
    text = resources.files("walkbike_routes.presets").joinpath(mode, f"{name}.yaml").read_text()
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict) or set(raw) != FIELDS:
        found = sorted(raw) if isinstance(raw, dict) else type(raw).__name__
        raise PresetError(f"preset {mode}/{name}: expected keys {sorted(FIELDS)}, got {found}")
    if raw["name"] != name or raw["mode"] != mode:
        raise PresetError(f"preset {mode}/{name}: its name or mode does not match its file")
    if raw["kind"] not in KINDS[mode]:
        raise PresetError(f"preset {mode}/{name}: kind {raw['kind']!r} is not for {mode}")
    if raw["objective"] not in OBJECTIVES:
        raise PresetError(f"preset {mode}/{name}: unknown objective {raw['objective']!r}")
    if not isinstance(raw["parameters"], dict):
        raise PresetError(f"preset {mode}/{name}: parameters must be a mapping")
    preset = Preset(**{key: raw[key] for key in FIELDS})
    preset.profile()  # check the parameters now, not at routing time
    return preset
