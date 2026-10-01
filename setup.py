from setuptools import find_packages, setup

package_name = "testudo"

setup(
    name=package_name,
    version="0.0.1",
    packages=find_packages(exclude=["tests", "tests.*"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/config", ["config/example_config.yaml"]),
    ],
    # numpy: package.xml declares it as an apt exec_depend (python3-numpy),
    # which colcon/rosdep-based installs satisfy for free -- but a plain
    # `pip install` (e.g. on a machine set up without colcon, or into a venv
    # without --system-site-packages) won't pull it in unless it's also
    # listed here. It's a direct import (odometry.py) as well as a
    # transitive need of several rclpy message bindings.
    # psutil: node profiling's per-process CPU/memory/thread sampling
    # (core/node_profiling/sampler.py) -- a hard dependency, unlike the
    # optional pynvml (GPU memory only, lazily imported, deliberately
    # never listed here or in package.xml -- see core/node_profiling/gpu.py).
    install_requires=["setuptools", "PyYAML", "textual", "numpy", "psutil"],
    zip_safe=True,
    maintainer="mlisi1",
    maintainer_email="elechim2196@gmail.com",
    description="Black-box diagnostics tool for navigation stacks (Nav2-first).",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "testudo = testudo.cli:main",
        ],
        "testudo.checks": [
            "dummy = testudo.plugins.builtin.dummy:DummyPlugin",
            "odometry = testudo.plugins.builtin.odometry:OdometryPlugin",
            "sensors_generic = testudo.plugins.builtin.sensors_generic:GenericSensorPlugin",
            "gnss = testudo.plugins.builtin.gnss:GnssPlugin",
            # Re-enabled now that undeclared Image/CompressedImage topics
            # default to the presence-only tier (no subscription at all) --
            # that's what previously caused problems on real hardware.
            "image_stream = testudo.plugins.builtin.image_stream:ImageStreamPlugin",
            "point_stream = testudo.plugins.builtin.point_stream:PointStreamPlugin",
            "nav2_goals = testudo.plugins.builtin.nav2_goals:Nav2GoalPlugin",
            "bt_log = testudo.plugins.builtin.bt_log:BehaviorTreeLogPlugin",
            "tf_watch = testudo.plugins.builtin.tf_watch:TFWatchPlugin",
        ],
    },
)
