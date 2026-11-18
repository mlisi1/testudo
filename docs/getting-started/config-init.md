# Write a config

With your stack running:

```bash
testudo config init
```

This writes `~/.config/testudo/config.yaml`. Use `-c` to write somewhere else, and `--force` to replace an existing file.

The file declares every published topic a plugin covers and every action, with each plugin's default levels as comments ready to uncomment and edit.

Three kinds of topic are left undeclared on purpose, because declaring one changes how it is watched:

- Topics with no publisher, since their silence would become an error.
- Cameras and point clouds, written commented out, since declaring one subscribes to it.
- `/tf_static`, which is read together with `/tf`.

Next, read about [severity levels](../concepts/severity.md) or the full [configuration reference](../reference/config.md).
