# Install

Testudo is a colcon package for ROS 2 Jazzy.

```bash
cd ~/your_ws/src
git clone https://github.com/mlisi1/testudo.git
cd ~/your_ws
rosdep install --from-paths src --ignore-src -r -y
pip install textual   # rosdep's python3-textual is too old for the dashboard
colcon build --packages-select testudo
source install/setup.bash
```

Check that it works:

```bash
testudo plugins
```

This lists every check and the message types it covers.

!!! note "Optional packages"
    `nav2_msgs` is needed only for the [behavior tree log](../plugins/bt-log.md). GPU memory readings need an NVIDIA card and `pynvml`.
