# Contributing

## Run the tests

From the repository root:

```bash
source /opt/ros/jazzy/setup.bash
python3 -m pytest -q tests
```

Plugin logic is tested with synthetic messages, so no live graph or bag is needed.

## Build the docs

```bash
pip install -r docs/requirements.txt
python3 docs/gen/gen_reference.py
mkdocs serve
```

The level tables, the code index and the command-line reference are generated from the source, and a test fails when they are out of date. Every code needs an entry on its plugin's page, under a heading of this form:

```markdown
### IMU-001 · NaN or Inf in the readings { #imu-001 }
```

## Recreate the screenshots

Every image and video comes from the real dashboard:

```bash
python3 docs/gen/media/capture.py live
```

`fault_stack.py` publishes a misbehaving topic for each plugin, on a virtual display and a separate ROS domain. It needs Xvfb, Terminator and ffmpeg. The `replay` group also records a field bag, which isn't part of the repository.

## Code conventions

- Keep files small, with one responsibility per module.
- Parse the config into typed dataclasses once at startup, and fail loudly on bad input.
- Add type hints and docstrings to every public function and class.
- Use logging, never `print()`, because stray output corrupts the dashboard.
- Keep `CheckPlugin` small and stable, since external plugins are built against it.
