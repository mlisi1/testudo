# Liveness

Every subscribed topic is checked for being alive, with no configuration. Topics that are [quiet by design](../concepts/liveness.md) are told apart from dead ones.

## Codes

### LIVE-001 · No publishers { #live-001 }

<span class="sev sev-error">✗ ERROR</span> A topic has no publisher, and it is either declared in the config or had a publisher earlier in this session.

**Likely causes:** a crashed node, or a declared topic that was never started.

**What to do:** find the node that should publish it. An undeclared topic that never had a publisher reads as an idle input instead.

### LIVE-002 · Publisher present, no messages { #live-002 }

<span class="sev sev-error">✗ ERROR</span> A declared topic has a publisher, but no message has arrived.

**Likely causes:** a publisher waiting for its input, or a QoS or network problem.

**What to do:** check the publishing node. If the topic only publishes on demand, declare it with `event_driven: true`.

### LIVE-003 · Topic went stale { #live-003 }

<span class="sev sev-stale">■ STALE</span> A topic that was publishing has sent nothing for more than 2 s. The row keeps the last values the plugin reported.

**Likely causes:** a crashed or blocked publisher.

**What to do:** check the publishing node. For a topic that may legitimately stop, declare it with `event_driven: true`.

### LIVE-004 · Publishing too slowly { #live-004 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> The rate is below the topic's `rate_hz` level, or below what Nav2's own parameters ask for.

With Nav2 running, Testudo reads the expected rate of the controller's and velocity smoother's commands, and of a costmap with `always_send_full_costmap`. These are judged only while the topic is publishing: below 80% of the rate is WARN and below 50% is ERROR. A `rate_hz` in the config replaces this.

**Likely causes:** an overloaded host, or a slow input.

**What to do:** check the node's CPU in the Nodes category.

```yaml
topics:
  sensor_msgs/msg/LaserScan:
    - name: /scan
      thresholds:
        rate_hz: {warn: 9.0, error: 5.0}
```

### LIVE-005 · Message type not installed { #live-005 }

<span class="sev sev-error">✗ ERROR</span> A declared topic's message package isn't available here, so it can't be monitored.

**Likely causes:** an interface package missing from this machine or workspace.

**What to do:** install the package and source the workspace. Undeclared topics of an unknown type only log one warning.
