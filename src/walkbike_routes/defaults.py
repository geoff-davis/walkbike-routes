"""Default speeds and waits shared by the walking and cycling profiles.

Study-specific values (snap limits, crossing and turn penalties) belong to a
route-choice preset or the study config, not here.
"""

from __future__ import annotations

WALK_SPEED_MPH = 3.0
BICYCLE_SPEED_MPH = 10.0
LTS_COST_FACTORS = (1.0, 1.25, 3.0, 8.0)
PEDESTRIAN_SIGNAL_WAIT_S = 30.0
BICYCLE_SIGNAL_WAIT_S = 20.0
