# Core checks

These checks run whatever plugin covers a topic, or with no plugin at all.

| Page | Checks | Codes |
|---|---|---|
| [Liveness](live.md) | Whether a topic has a publisher, delivers messages and keeps its rate | `LIVE` |
| [Stamps](stamp.md) | Whether a message's header stamp is believable | `STAMP` |
| [Check failures](check.md) | Whether a plugin itself crashed | `CHECK` |
| [Node profiling](node.md) | CPU, memory and process state of each ROS node | `NODE` |
