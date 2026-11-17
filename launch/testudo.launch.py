"""Run Testudo headless as part of a robot's bringup: `testudo watch --headless`, as a ROS node.

    ros2 launch testudo testudo.launch.py config:=/path/to/testudo.yaml use_sim_time:=true

Publishes on /testudo/* (see the README's Output section) until the launch
is stopped. `config` left empty uses the default config location
(`$XDG_CONFIG_HOME/testudo/config.yaml`, else `~/.config/testudo/config.yaml`,
none at all being the same as an empty config).
"""
from launch import LaunchContext, LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def _testudo_node(context: LaunchContext) -> list[Node]:
    config = LaunchConfiguration("config").perform(context)
    arguments = ["watch", "--headless"] + (["-c", config] if config else [])
    return [
        Node(
            package="testudo",
            executable="testudo",
            name="testudo",
            arguments=arguments,
            parameters=[{"use_sim_time": ParameterValue(LaunchConfiguration("use_sim_time"), value_type=bool)}],
            output="screen",
        )
    ]


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription(
        [
            DeclareLaunchArgument("config", default_value="", description="Testudo config file (empty: the default location)"),
            DeclareLaunchArgument("use_sim_time", default_value="false", description="Use /clock (simulation time)"),
            OpaqueFunction(function=_testudo_node),
        ]
    )
