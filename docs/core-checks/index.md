# Core checks

These run on every monitored topic, whichever plugin covers it, or with no plugin at all.

<div class="grid cards" markdown>

-   :material-pulse:{ .lg .middle } **[Liveness](live.md)**

    ---

    Does the topic have a publisher, deliver messages and keep its rate?

    `LIVE`

-   :material-clock-check-outline:{ .lg .middle } **[Stamps](stamp.md)**

    ---

    Is each message's header stamp believable?

    `STAMP`

-   :material-alert-octagon-outline:{ .lg .middle } **[Check failures](check.md)**

    ---

    Did a plugin itself crash on a message?

    `CHECK`

-   :material-chip:{ .lg .middle } **[Node profiling](node.md)**

    ---

    CPU, memory and process state of each ROS node.

    `NODE`

</div>
