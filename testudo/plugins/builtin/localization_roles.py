"""What a localization output's frame says about how to judge it: its role and that role's default levels."""
from __future__ import annotations

from testudo.plugins.base import SeverityLevels

#: What a localization output's frame says about it (`frame_role`, or a
#: topic's `frame_role:` in config). Continuous: drifts smoothly, so growing
#: covariance is normal and any jump is a bug. Global: corrected against the
#: world, so relocalizing is normal and growing covariance means losing it.
CONTINUOUS_ROLE = "continuous"
GLOBAL_ROLE = "global"
_CONTINUOUS_FRAMES = frozenset({"odom"})
_GLOBAL_FRAMES = frozenset({"map", "earth"})

#: Each role's levels where the config leaves a metric at its default.
#: Continuous: only a placeholder-sized covariance is a fault (wheel odometry
#: grows without bound). Global: a particle filter normally sits at decimeters.
ROLE_LEVELS: dict[str, dict[str, SeverityLevels]] = {
    CONTINUOUS_ROLE: {
        "position_covariance_trace": SeverityLevels(error=1000.0),
        "yaw_variance": SeverityLevels(error=10.0),
        "covariance_growth_rate": SeverityLevels(),
    },
    GLOBAL_ROLE: {
        "position_covariance_trace": SeverityLevels(warn=1.0, error=1000.0),
        "yaw_variance": SeverityLevels(warn=0.25, error=10.0),
        "covariance_growth_rate": SeverityLevels(warn=0.05),
    },
}

def frame_role(frame_id: str) -> str | None:
    """The role `frame_id` implies (its last segment, so `robot1/map` counts), or None for any other frame."""
    name = frame_id.rsplit("/", 1)[-1]
    if name in _CONTINUOUS_FRAMES:
        return CONTINUOUS_ROLE
    return GLOBAL_ROLE if name in _GLOBAL_FRAMES else None
