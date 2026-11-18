# Write a plugin

A plugin checks one kind of message. The built-in plugins use this same interface, so yours gets the dashboard, the published diagnostics, debouncing and the fault history for free.

This guide builds a battery check for `sensor_msgs/msg/BatteryState`.

## 1. Write the check

Save this as `~/testudo_plugins/battery.py`:

```python
from typing import Any

from testudo.plugins.base import (
    ActiveCodes,
    CheckPlugin,
    CheckStatus,
    CodeMetric,
    Severity,
    SeverityLevels,
    evaluate_levels,
)
from testudo.plugins.registry import register_plugin


@register_plugin
class BatteryPlugin(CheckPlugin):
    """Warns when the battery runs low."""

    def __init__(self, thresholds=None, related_topics=None, *, levels=None) -> None:
        super().__init__(thresholds, related_topics, levels=levels)
        self._charge: float | None = None

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("sensor_msgs/msg/BatteryState",)

    @classmethod
    def default_levels(cls) -> dict[str, SeverityLevels]:
        return {"charge_ratio": SeverityLevels(warn=0.3, error=0.1)}

    def on_message(self, topic: str, msg: Any) -> None:
        self._charge = msg.percentage

    def get_status(self) -> CheckStatus:
        if self._charge is None:
            return CheckStatus(severity=Severity.OK, label="waiting", message="no message yet")

        severity = evaluate_levels(
            self._charge, self.levels.get("charge_ratio"), higher_is_worse=False
        )
        active = ActiveCodes()
        if severity > Severity.OK:
            active.add("BAT-001", f"battery at {self._charge:.0%}", severity)

        return CheckStatus(
            severity=severity,
            label=f"{self._charge:.0%}",
            message=f"battery at {self._charge:.0%}",
            values={"charge": f"{self._charge:.0%}"},
            metrics={"charge_ratio": self._charge},
            topic_panel_column="Charge",
            topic_panel_value=f"{self._charge:.0%}",
            codes=active.messages,
            code_severities=active.severities,
            code_metrics={"BAT-001": CodeMetric("charge_ratio", self._charge, higher_is_worse=False)},
        )
```

Every non-OK status needs at least one active code, because that's what the dashboard lists and the fault history records. Choose a prefix of your own for your codes.

## 2. Load it

Point the config at the directory:

```yaml
plugins_dir: ~/testudo_plugins
```

Check that Testudo finds it:

```bash
testudo plugins -c ~/.config/testudo/config.yaml
```

From now on, every `BatteryState` topic gets this check. A plugin loaded this way is named after its class, so its results are published on `/testudo/BatteryPlugin`. Users tune it like any built-in:

```yaml
topics:
  sensor_msgs/msg/BatteryState:
    - name: /battery_state
      thresholds:
        charge_ratio: {warn: 0.4}
```

## 3. Test it

A plugin needs no running ROS graph to be tested. Feed it a synthetic message:

```python
from types import SimpleNamespace

from battery import BatteryPlugin
from testudo.plugins.base import Severity


def test_low_battery_warns():
    plugin = BatteryPlugin()
    plugin.on_message("/battery_state", SimpleNamespace(percentage=0.2))

    status = plugin.get_status()

    assert status.severity == Severity.WARN
    assert "BAT-001" in status.codes
```

## 4. Package it

To share a plugin, expose it as an entry point in the `testudo.checks` group of your package's `setup.py`:

```python
entry_points={
    "testudo.checks": [
        "battery = my_package.battery:BatteryPlugin",
    ],
},
```

A packaged plugin doesn't need `@register_plugin` or `plugins_dir`. The entry point's name is the plugin's name, so this one publishes on `/testudo/battery`.

## Good practice

- **Keep `on_message` cheap.** It runs on the thread that receives every topic.
- **Don't guess related topics.** Read them from `self.related_topics`, and skip the check when one isn't configured.
- **Declare every metric you read** in `default_levels()`. Use `SeverityLevels()` when no default makes sense, so Testudo doesn't warn that the metric is unused.
- **Hold brief events active.** A one-off event needs to stay active for a few seconds, or debouncing never confirms it.
- **Publish raw numbers.** `metrics` is what PlotJuggler and Foxglove plot, while `values` is formatted for people.
