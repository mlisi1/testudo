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
    install_requires=["setuptools", "PyYAML", "textual"],
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
            "nav2_goals = testudo.plugins.builtin.nav2_goals:Nav2GoalPlugin",
            "tf_watch = testudo.plugins.builtin.tf_watch:TFWatchPlugin",
        ],
    },
)
