# Node profiling

Samples the CPU, memory and process state of every ROS node whose process can be found, plus Testudo itself. Results appear in the TUI's Nodes category and on `/testudo/nodes`.

It only runs live, because a bag has no processes to sample. GPU memory needs an NVIDIA card and `pynvml`, and is skipped otherwise.

A node is matched to its process through its `__node:=` remap, or else by executable name. A node that can't be matched either way isn't profiled.

## Codes

### NODE-001 · Possible CPU starvation { #node-001 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> The scheduler preempts the process more often than `ctx_switches_per_s`. A starved node's CPU usage can look normal, because it isn't getting the time to use the CPU.

**Likely causes:** more runnable work than the host has cores.

**What to do:** reduce the load, or adjust priorities and CPU affinity.

### NODE-002 · Process in a bad state { #node-002 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> The process is blocked on I/O or stopped (WARN), or is a zombie or dead (ERROR).

**Likely causes:** a stalled disk or network mount, a suspended process, or a crash.

**What to do:** the code's message names the state.

### NODE-003 · Memory growing { #node-003 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> Resident memory grows faster than `memory_slope_mb_per_min`. It is judged only after the process has been up for 5 minutes, because startup allocation isn't a leak.

**Likely causes:** a memory leak, or a buffer that never stops growing.

**What to do:** watch the trend in the Detail Panel over a longer run.

### NODE-004 · CPU usage high { #node-004 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> CPU usage is past `cpu_percent`. A multi-threaded process can exceed 100%, as it does in `top`.

**Likely causes:** a heavy workload or a busy loop.

**What to do:** raise the level if this load is normal for the node.

### NODE-005 · Memory usage high { #node-005 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> Resident memory is past `memory_mb`.

**Likely causes:** large maps or buffers, or a leak.

**What to do:** raise the level if this size is normal for the node.

## Settings

--8<-- "levels/node.md"

```yaml
node_profiling:
  enabled: true
  poll_interval_seconds: 2.0
  thresholds:
    cpu_percent: {warn: 200}
  exclude_nodes:
    - /some_noisy_node
```
