---
title: Testudo
hide:
  - navigation
  - toc
---

# Testudo { .testudo-page-title }

<div class="tx-hero" markdown>

![Testudo](logo_wordmark.svg){ .tx-hero__logo }

<p class="tx-hero__title">Is my nav stack healthy right now?<br>And if not, what broke first?</p>

<p class="tx-hero__text">Testudo is a diagnostics dashboard for ROS 2 navigation stacks. It watches every topic from the outside, judges what it sees, and points you at the problem, live in your terminal or from a recorded bag.</p>

[Get started](getting-started/index.md){ .md-button .md-button--primary }
[See what it checks](plugins/index.md){ .md-button }

<ul class="tx-pills">
<li>ROS 2 Jazzy</li>
<li>No changes to your nodes</li>
<li>Works with no config</li>
<li>Built for Nav2, not tied to it</li>
</ul>

</div>

<video class="tx-loop tx-video" src="assets/media/home/hero.mp4" poster="assets/media/home/hero.jpg" autoplay loop muted playsinline disablepictureinpicture disableremoteplayback preload="auto" aria-label="Short reels of the Testudo dashboard, each with a panel explaining it"></video>

## A navigation stack rarely fails loudly

The robot stops short, drifts, or spins in place, and nothing has crashed. Every node is up and every topic is publishing. The fault is in the data: a covariance left at a placeholder, an IMU in the wrong units, a transform with two parents, a planner that keeps falling back on recoveries.

Your existing tools can show you all of this, once you know where to look. Testudo tells you where to look.

<div class="tx-compare" markdown>

| Tool | What it gives you | What's left to you |
|---|---|---|
| `ros2 topic hz`, `echo` | One topic at a time | Choosing the topic and judging the numbers |
| `rqt_robot_monitor` | What nodes report about themselves | Everything they don't report |
| Foxglove, PlotJuggler | Every signal, plotted | Knowing which signal matters |
| **Testudo** | **Every topic at once, already judged** | **Fixing the cause** |

</div>

## What it does

<div class="grid cards" markdown>

-   :material-eye-outline:{ .lg .middle } **Watches from the outside**

    ---

    Point it at a running graph. It finds the topics, matches their QoS and starts checking, with nothing added to your nodes.

-   :material-scale-balance:{ .lg .middle } **Judges the content**

    ---

    Not only whether a topic is alive, but whether its data makes sense: covariances, jumps, frozen sensors, broken TF, failing goals.

-   :material-target:{ .lg .middle } **Points at what broke**

    ---

    Every problem has a code and an explanation. The worst come first, and faults that cleared stay on record.

</div>

## Where you'll use it

<div class="tx-split" markdown>

<div markdown>

### On the robot, while it misbehaves

**For bring-up and field debugging.** Open it over SSH next to your stack, work down the **Needs attention** list, and press ++n++ to jump to each problem.

```bash
testudo watch
```

[Bring up a robot :material-arrow-right:](getting-started/bring-up.md)

</div>

<video class="tx-loop" src="assets/media/clips/jump.mp4" autoplay loop muted playsinline disablepictureinpicture disableremoteplayback preload="auto" aria-label="Jumping between problems in the dashboard"></video>

</div>

<div class="tx-split tx-split--flip" markdown>

<div markdown>

### After the run, from a bag

**For post-mortems.** Get a report of every fault with its timing, or scrub through the run in the same dashboard to see what went wrong first.

```bash
testudo replay --watch my_bag/
```

[Investigate a bag :material-arrow-right:](getting-started/replay.md)

</div>

<video class="tx-loop" src="assets/media/clips/scrub.mp4" autoplay loop muted playsinline disablepictureinpicture disableremoteplayback preload="auto" aria-label="Scrubbing through a recorded run"></video>

</div>

<div class="tx-split" markdown>

<div markdown>

### In your pipeline

**For CI on simulated runs.** The verdict at the end of a bag is the exit code, so a regression in the stack can fail the build.

[Use it in CI :material-arrow-right:](getting-started/ci.md)

</div>

```console
$ testudo replay -c config.yaml run_bag/
$ echo $?
2   # 0 OK · 1 WARN · 2 ERROR or STALE
```

</div>

## What it checks

Ten plugins read the content of your topics, and every topic gets liveness and timestamp checks.

<div class="grid cards tx-mini" markdown>

-   [:material-crosshairs-gps: **Localization**](plugins/localization.md)

    Covariance, jumps, relocalizations

-   [:material-axis-arrow: **IMU**](plugins/imu.md)

    Units, bias, clipping, stamps

-   [:material-satellite-variant: **GNSS**](plugins/gnss.md)

    Fix, accuracy, dropouts

-   [:material-camera: **Cameras**](plugins/image-stream.md)

    Empty, frozen or corrupt frames

-   [:material-radar: **Lidar**](plugins/point-stream.md)

    Invalid, stuck or malformed data

-   [:material-speedometer: **Velocity commands**](plugins/velocity-command.md)

    Stuck robot, oscillation

-   [:material-map: **Costmaps**](plugins/costmap.md)

    Lethal and unknown share, drawn in the terminal

-   [:material-flag-checkered: **Actions**](plugins/actions.md)

    Goal success rate and duration

-   [:material-file-tree: **Behavior tree**](plugins/bt-log.md)

    Recoveries and what triggered them

-   [:material-family-tree: **TF**](plugins/tf-watch.md)

    Multiple parents, missing chains

</div>

<div class="tx-cta" markdown>

**Have a stack running? You're one command away from a diagnosis.**

[Install Testudo](getting-started/index.md){ .md-button .md-button--primary }

</div>
