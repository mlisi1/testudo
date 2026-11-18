# Plugin API

Everything here is imported from `testudo.plugins.base`, except `register_plugin`, which is in `testudo.plugins.registry`.

## `CheckPlugin`

### Required

| Method | Purpose |
|---|---|
| `msg_types()` (classmethod) | The full message types handled, such as `("nav_msgs/msg/Odometry",)` |
| `on_message(topic, msg)` | Handle one message from the primary topic or a related topic |
| `get_status()` | Return the current `CheckStatus` |

### Optional

| Method | Default | Purpose |
|---|---|---|
| `default_levels()` | `{}` | Severity levels per metric |
| `default_thresholds()` | `{}` | Meter zones per metric |
| `presence_only_msg_types()` | empty | Types too heavy to subscribe to unless declared |
| `needs_every_message()` | `False` | `True` when skipping a message would lose information |
| `default_related_topics(topic_name)` | `{}` | Companion topics fixed by convention |
| `silence_expected(msg_type)` | `False` | `True` while the topic may legitimately stop publishing |
| `on_tick(now_seconds)` | nothing | Advance time-based state without new messages |
| `reset()` | re-runs `__init__` | Clear accumulated state when the user presses ++r++ |

### Attributes

| Attribute | Content |
|---|---|
| `self.levels` | Metric name to `SeverityLevels`, with the user's config merged in |
| `self.thresholds` | Metric name to `ThresholdZone`, with the user's config merged in |
| `self.related_topics` | Logical name to real topic name |

Testudo assigns `self.levels` right after construction, so read it in `get_status`, not in `__init__`.

## `CheckStatus`

| Field | Purpose |
|---|---|
| `severity` | A `Severity` value. Required |
| `label` | A short status text. Required |
| `message` | One sentence about the state. Required |
| `values` | Key and value strings for the Detail Panel. Rich markup is allowed |
| `metrics` | Raw numbers and short identifiers to publish. Replaces `values` in published output |
| `codes` | Active code to message |
| `code_severities` | Active code to its own severity. A code left out counts at `severity` |
| `code_metrics` | Active code to the `CodeMetric` behind it, which enables the "warn from current value" quick action |
| `topic_panel_column`, `topic_panel_value` | One extra Topic Panel column |
| `picture` | A `StatusPicture` to draw in the Detail Panel |

### Value key conventions

| Suffix | Effect |
|---|---|
| `_compact` | Replaces the value on a narrow terminal |
| `_more` | Shown only when the Detail Panel has room |
| `_s`, `_hz`, `_mb`, ... | The unit moves next to the value on screen |

## Helpers

| Name | Purpose |
|---|---|
| `Severity` | `OK`, `WARN`, `ERROR`, `STALE` |
| `SeverityLevels(warn, error)` | A metric's levels. A missing level never fires |
| `ThresholdZone(green, orange, red)` | A meter's color zones. Never affects severity |
| `evaluate_levels(value, levels, higher_is_worse=True)` | Turn a value into a `Severity` |
| `ActiveCodes()` | Build `codes` and `code_severities` together with `add(code, message, severity)` |
| `CodeMetric(name, value, higher_is_worse)` | Name the metric behind a code |
| `register_plugin` | Class decorator for a plugin loaded from `plugins_dir` |
