"""Dev only: Testudo's CPU use and rate accuracy under a real robot's message load.

The load is the first real robot's (2026-11-23 field bags, ~4,700 msg/s): seven
topics at 400 Hz, GLIM's 347 Hz odometry, two RealSense IMU streams, TF and
~30 slow topics, published by several processes so the publishers keep up.

    python3 tools/load_bench.py              # publish, run `watch --headless`, report
    python3 tools/load_bench.py --profile    # the same, plus a cProfile of the spin thread
    python3 tools/load_bench.py --executor single   # with the SingleThreadedExecutor instead

Run from a sourced ROS 2 shell on an isolated domain (ROS_DOMAIN_ID=178,
ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST). Every process it starts is its own.
"""
from __future__ import annotations

import argparse
import math
import multiprocessing
import os
import random
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil

REPO = Path(__file__).resolve().parent.parent

#: (topic, message type, Hz), grouped into one publisher process each.
LOAD: list[list[tuple[str, str, float]]] = [
    [("/imu/data", "sensor_msgs/msg/Imu", 400), ("/status", "std_msgs/msg/UInt32", 404)],
    [("/imu/angular_velocity", "geometry_msgs/msg/Vector3Stamped", 400), ("/imu/acceleration", "geometry_msgs/msg/Vector3Stamped", 400)],
    [("/filter/euler", "geometry_msgs/msg/Vector3Stamped", 400), ("/filter/quaternion", "geometry_msgs/msg/QuaternionStamped", 400)],
    [("/imu/utctime", "sensor_msgs/msg/TimeReference", 404), ("/imu/mag", "sensor_msgs/msg/MagneticField", 100)],
    [("/glim_ros/odometry/imu_rate", "nav_msgs/msg/Odometry", 347), ("/odometry/filtered", "nav_msgs/msg/Odometry", 60)],
    [
        ("/front_camera/camera/gyro/sample", "sensor_msgs/msg/Imu", 200),
        ("/front_camera/camera/gyro/metadata", "std_msgs/msg/String", 200),
        ("/front_camera/camera/accel/sample", "sensor_msgs/msg/Imu", 100),
        ("/front_camera/camera/accel/metadata", "std_msgs/msg/String", 100),
    ],
    [("/tf", "tf2_msgs/msg/TFMessage", 80), ("/accel/filtered", "geometry_msgs/msg/AccelWithCovarianceStamped", 60)]
    + [(f"/slow/camera_info_{i}", "sensor_msgs/msg/CameraInfo", 30) for i in range(8)]
    + [(f"/slow/status_{i}", "std_msgs/msg/String", 10) for i in range(20)],
]


def _message(msg_type: str, msg_class: type, node, sequence: int):
    """One plausible message of `msg_type`: stamped now, with values the plugins accept."""
    msg = msg_class()
    if hasattr(msg, "header"):
        msg.header.stamp = node.get_clock().now().to_msg()
        msg.header.frame_id = "base_link"
    noise = random.gauss(0.0, 0.01)
    if msg_type == "sensor_msgs/msg/Imu":
        msg.linear_acceleration.z = 9.81 + noise
        msg.angular_velocity.z = noise
        msg.orientation.w = 1.0
        msg.angular_velocity_covariance[0] = msg.angular_velocity_covariance[4] = msg.angular_velocity_covariance[8] = 1e-4
        msg.linear_acceleration_covariance[0] = msg.linear_acceleration_covariance[4] = msg.linear_acceleration_covariance[8] = 1e-3
        msg.orientation_covariance[0] = -1.0
    elif msg_type == "nav_msgs/msg/Odometry":
        msg.header.frame_id, msg.child_frame_id = "odom", "base_link"
        msg.pose.pose.position.x = noise
        msg.pose.pose.orientation.w = 1.0
        for index in (0, 7, 14, 21, 28, 35):
            msg.pose.covariance[index] = msg.twist.covariance[index] = 0.01
    elif msg_type == "tf2_msgs/msg/TFMessage":
        from geometry_msgs.msg import TransformStamped

        transform = TransformStamped()
        transform.header.stamp = node.get_clock().now().to_msg()
        transform.header.frame_id, transform.child_frame_id = "odom", "base_link"
        transform.transform.rotation.w = 1.0
        msg.transforms = [transform]
    elif msg_type in ("std_msgs/msg/String",):
        msg.data = f"meta {sequence}"
    elif msg_type == "std_msgs/msg/UInt32":
        msg.data = sequence % 4096
    elif msg_type == "sensor_msgs/msg/MagneticField":
        msg.magnetic_field.x = 2e-5 + noise * 1e-6
    elif msg_type.endswith("Vector3Stamped"):
        msg.vector.z = 9.81 + noise
    elif msg_type.endswith("QuaternionStamped"):
        msg.quaternion.w = 1.0
    return msg


def _publish(group: list[tuple[str, str, float]], stop: multiprocessing.Event) -> None:
    """One publisher process: a timer per topic at its rate, until `stop`."""
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rosidl_runtime_py.utilities import get_message

    rclpy.init()
    node = rclpy.create_node(f"load_bench_{os.getpid()}")
    for topic, msg_type, hz in group:
        msg_class = get_message(msg_type)
        publisher = node.create_publisher(msg_class, topic, 10)
        counter = [0]

        def _tick(publisher=publisher, msg_type=msg_type, msg_class=msg_class, counter=counter) -> None:
            counter[0] += 1
            publisher.publish(_message(msg_type, msg_class, node, counter[0]))

        node.create_timer(1.0 / hz, _tick)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    while not stop.is_set():
        executor.spin_once(timeout_sec=0.1)
    node.destroy_node()
    rclpy.shutdown()


def _run_testudo(config: Path, seconds: float, profile_path: Path | None, executor: str) -> None:
    """`testudo watch --headless` in this process, stopped by SIGTERM after `seconds`."""
    sys.path.insert(0, str(REPO))
    from testudo import cli

    if executor == "single":
        os.environ["TESTUDO_EXECUTOR"] = "single"

    if profile_path is not None:
        import cProfile

        spin = cli._spin_until_stopped

        def _profiled(*args, **kwargs):
            profiler = cProfile.Profile()
            profiler.enable()
            try:
                return spin(*args, **kwargs)
            finally:
                profiler.disable()
                profiler.dump_stats(str(profile_path))

        cli._spin_until_stopped = _profiled
    threading.Timer(seconds, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    cli.main(["watch", "--headless", "-c", str(config)])


def _read_outputs(seconds: float, result: dict) -> None:
    """Collect the last rate_hz Testudo published for each topic, from its diagnostic topics."""
    import rclpy
    from diagnostic_msgs.msg import DiagnosticArray
    from rclpy.executors import SingleThreadedExecutor

    rclpy.init()
    node = rclpy.create_node("load_bench_reader")
    rates: dict[str, list[float]] = {}

    def _on_array(msg: DiagnosticArray) -> None:
        for status in msg.status:
            values = {kv.key: kv.value for kv in status.values}
            if "rate_hz" in values:
                try:
                    rates.setdefault(status.name.removeprefix("testudo: "), []).append(float(values["rate_hz"]))
                except ValueError:
                    pass

    for name in ("vitals", "imu", "localization", "tf_watch"):
        node.create_subscription(DiagnosticArray, f"/testudo/{name}", _on_array, 10)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        executor.spin_once(timeout_sec=0.1)
    result.update({topic: samples[-20:] for topic, samples in rates.items()})
    node.destroy_node()
    rclpy.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=40.0, help="how long Testudo runs")
    parser.add_argument("--warmup", type=float, default=15.0, help="seconds before CPU is measured")
    parser.add_argument("--profile", action="store_true", help="cProfile the spin thread")
    parser.add_argument("--executor", choices=("auto", "single"), default="auto", help="single: the SingleThreadedExecutor")
    args = parser.parse_args()
    if os.environ.get("ROS_DOMAIN_ID") in (None, "", "0"):
        sys.exit("set an isolated ROS_DOMAIN_ID first")

    stop = multiprocessing.Event()
    publishers = [multiprocessing.Process(target=_publish, args=(group, stop)) for group in LOAD]
    for process in publishers:
        process.start()
    time.sleep(3.0)

    workdir = Path(tempfile.mkdtemp(prefix="load_bench_"))
    config = workdir / "config.yaml"
    config.write_text("node_profiling:\n  enabled: true\n", encoding="utf-8")
    profile_path = workdir / "spin.prof" if args.profile else None
    testudo = multiprocessing.Process(target=_run_testudo, args=(config, args.seconds, profile_path, args.executor))
    testudo.start()

    manager = multiprocessing.Manager()
    rates = manager.dict()
    reader = multiprocessing.Process(target=_read_outputs, args=(args.seconds - 2.0, rates))
    reader.start()

    time.sleep(args.warmup)
    process = psutil.Process(testudo.pid)
    process.cpu_percent(None)
    window = args.seconds - args.warmup - 4.0
    time.sleep(window)
    cpu = process.cpu_percent(None)
    testudo.join(timeout=30)
    reader.join(timeout=30)
    stop.set()
    for publisher in publishers:
        publisher.join(timeout=10)

    expected = {topic: hz for group in LOAD for topic, _type, hz in group}
    print(f"load: {sum(expected.values()):.0f} msg/s on {len(expected)} topics")
    print(f"testudo CPU: {cpu:.0f}% of one core (over {window:.0f} s)")
    worst = []
    for topic, hz in sorted(expected.items(), key=lambda item: -item[1]):
        samples = rates.get(topic)
        if not samples:
            print(f"  {topic:40s} {hz:6.0f} Hz  no rate published")
            continue
        error = max(abs(sample - hz) / hz for sample in samples)
        worst.append(error)
        if hz >= 60:
            print(f"  {topic:40s} {hz:6.0f} Hz  read {min(samples):6.1f}..{max(samples):6.1f}  worst {error:5.1%}")
    if worst:
        print(f"worst rate error over every topic: {max(worst):.1%}")
    if profile_path is not None:
        import pstats

        stats = pstats.Stats(str(profile_path))
        stats.sort_stats("tottime").print_stats(25)


if __name__ == "__main__":
    main()
