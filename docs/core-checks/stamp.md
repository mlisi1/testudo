# Stamps

Every message with a `header.stamp` on the full tier is compared with the clock, whichever plugin checks it. A bad stamp breaks TF lookups and message filters downstream.

A latched topic, or one allowed to go quiet, is only checked for stamps from the future. A stamp more than a day from the clock is flagged live, with a hint about `use_sim_time`. In a replay it is ignored, because a simulator's bag normally pairs sim stamps with wall-clock receive times.

## Codes

### STAMP-001 · Stamp is zero { #stamp-001 }

<span class="sev sev-error">✗ ERROR</span> The header stamp was never set.

**Likely causes:** a publisher that doesn't fill in the header.

**What to do:** fix the publisher to stamp its messages.

### STAMP-002 · Stamp ahead of the clock { #stamp-002 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> The stamp is later than the clock by more than `stamp_future_s`.

**Likely causes:** unsynchronized clocks between machines, or Testudo and the stack disagreeing on `use_sim_time`.

**What to do:** synchronize the clocks, for example with chrony.

### STAMP-003 · Stamp behind the clock { #stamp-003 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> The stamp is older than the clock by more than `stamp_lag_s`.

**Likely causes:** a slow pipeline, a congested network or unsynchronized clocks.

**What to do:** look for the slow stage. A driver that stamps late by design can have its `warn` raised.

## Settings

--8<-- "levels/stamp.md"

Set these under a declared topic's `thresholds:`.
