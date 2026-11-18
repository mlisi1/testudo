"""A small ROS 2 node whose topics go from healthy to faulty and back, for the plugin pages' clips.

Every topic follows the same wall-clock cycle (`phase_s`), so `capture.py` can start
recording at a known moment without talking to this node.
"""
import math
import random
import time
import uuid

import rclpy
from action_msgs.msg import GoalInfo, GoalStatus, GoalStatusArray
from fault_stack import _LETHAL, _SCAN_BEAMS, _GRID_HEIGHT, _GRID_WIDTH, _grid_cells
from geometry_msgs.msg import TransformStamped, Twist
from nav2_msgs.msg import BehaviorTreeLog, BehaviorTreeStatusChange
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.executors import ExternalShutdownException
from sensor_msgs.msg import CompressedImage, Imu, LaserScan, NavSatFix, NavSatStatus
from tf2_msgs.msg import TFMessage
from unique_identifier_msgs.msg import UUID

HEALTHY_S = 6.0
FAULT_S = 14.0
CYCLE_S = HEALTHY_S + FAULT_S

_SPEED_MPS = 0.5
_PERIOD_S = 0.1
_JPEG_START = b"\xff\xd8\xff\xe0"
_KEPT_GOALS = 6
#: Past the error level, with part of the room still visible.
_SWEEP_END_COLUMN = 138


def phase_s() -> float:
    """Seconds into the current cycle: healthy first, then faulty."""
    return time.time() % CYCLE_S


def _faulty() -> bool:
    return phase_s() >= HEALTHY_S


class PluginStack:
    """Publishes one topic per plugin, each with a fault that comes and goes."""

    def __init__(self, node) -> None:
        self._node = node
        self._x = 0.0
        self._tick = 0
        self._goals: list[GoalStatus] = []
        self._healthy_cells = _grid_cells()
        self._stuck_frame = _JPEG_START + bytes(random.getrandbits(8) for _ in range(40000))
        self._odom = node.create_publisher(Odometry, "/odom", 10)
        self._imu = node.create_publisher(Imu, "/imu/data", 10)
        self._gps = node.create_publisher(NavSatFix, "/gps/fix", 10)
        self._camera = node.create_publisher(CompressedImage, "/camera/image_raw/compressed", 10)
        self._scan = node.create_publisher(LaserScan, "/scan", 10)
        self._cmd = node.create_publisher(Twist, "/cmd_vel_nav", 10)
        self._costmap = node.create_publisher(OccupancyGrid, "/local_costmap/costmap", 10)
        self._status = node.create_publisher(GoalStatusArray, "/navigate_to_pose/_action/status", 10)
        self._bt_log = node.create_publisher(BehaviorTreeLog, "/behavior_tree_log", 10)
        self._tf = node.create_publisher(TFMessage, "/tf", 100)
        node.create_timer(_PERIOD_S, self._fast)
        node.create_timer(0.25, self._publish_goals)
        node.create_timer(0.5, self._publish_costmap)
        node.create_timer(1.0, self._publish_bt_log)

    def _fast(self) -> None:
        self._tick += 1
        stamp = self._node.get_clock().now().to_msg()
        faulty = _faulty()
        self._publish_odom(stamp, faulty)
        self._publish_imu(stamp, faulty)
        self._publish_gps(stamp, faulty)
        self._publish_camera(stamp, faulty)
        self._publish_scan(stamp, faulty)
        self._publish_tf(stamp, faulty)
        command = Twist()
        command.linear.x = _SPEED_MPS
        command.angular.z = 0.2 * math.sin(self._tick / 15.0)
        self._cmd.publish(command)

    def _publish_odom(self, stamp, faulty: bool) -> None:
        """LOC-001: the position variance becomes a placeholder. VEL-002: the robot stops responding."""
        speed = 0.0 if faulty else _SPEED_MPS
        self._x += speed * _PERIOD_S
        variance = 1.0e6 if faulty else 0.002
        msg = Odometry()
        msg.header.stamp = stamp
        msg.header.frame_id = "odom"
        msg.child_frame_id = "base_link"
        msg.pose.pose.position.x = self._x
        msg.pose.pose.orientation.w = 1.0
        msg.pose.covariance[0] = variance
        msg.pose.covariance[7] = variance
        msg.pose.covariance[35] = 0.001
        msg.twist.twist.linear.x = speed
        msg.twist.covariance[0] = 0.001
        msg.twist.covariance[35] = 0.001
        self._odom.publish(msg)

    def _publish_imu(self, stamp, faulty: bool) -> None:
        """IMU-005: the acceleration at rest stops looking like gravity."""
        msg = Imu()
        msg.header.stamp = stamp
        msg.header.frame_id = "imu_link"
        msg.orientation.w = 1.0
        msg.orientation_covariance[0] = msg.orientation_covariance[4] = msg.orientation_covariance[8] = 0.01
        msg.angular_velocity_covariance[0] = msg.angular_velocity_covariance[4] = 0.001
        msg.angular_velocity_covariance[8] = 0.001
        msg.linear_acceleration_covariance[0] = msg.linear_acceleration_covariance[4] = 0.01
        msg.linear_acceleration_covariance[8] = 0.01
        msg.linear_acceleration.x = random.gauss(0.0, 0.01)
        msg.linear_acceleration.y = random.gauss(0.0, 0.01)
        msg.linear_acceleration.z = (5.0 if faulty else 9.81) + random.gauss(0.0, 0.01)
        msg.angular_velocity.z = random.gauss(0.0, 0.002)
        self._imu.publish(msg)

    def _publish_gps(self, stamp, faulty: bool) -> None:
        """GNSS-001 and GNSS-002: the fix is lost and the claimed accuracy collapses."""
        variance = (25.0, 25.0, 64.0) if faulty else (0.00002, 0.00002, 0.00005)
        msg = NavSatFix()
        msg.header.stamp = stamp
        msg.header.frame_id = "gps_link"
        msg.status.status = NavSatStatus.STATUS_NO_FIX if faulty else NavSatStatus.STATUS_FIX
        msg.latitude = 45.0 + random.gauss(0.0, 1e-7)
        msg.longitude = 9.0 + random.gauss(0.0, 1e-7)
        msg.altitude = 120.0 + random.gauss(0.0, 0.01)
        msg.position_covariance = [variance[0], 0.0, 0.0, 0.0, variance[1], 0.0, 0.0, 0.0, variance[2]]
        msg.position_covariance_type = NavSatFix.COVARIANCE_TYPE_DIAGONAL_KNOWN
        self._gps.publish(msg)

    def _publish_camera(self, stamp, faulty: bool) -> None:
        """IMG-003: the driver keeps sending the same frame."""
        msg = CompressedImage()
        msg.header.stamp = stamp
        msg.header.frame_id = "camera_link"
        msg.format = "jpeg"
        size = 40000 + int(3000 * math.sin(self._tick / 7.0))
        msg.data = self._stuck_frame if faulty else _JPEG_START + self._tick.to_bytes(4, "big") + bytes(size)
        self._camera.publish(msg)

    def _publish_scan(self, stamp, faulty: bool) -> None:
        """SENS-001: most readings turn to NaN."""
        msg = LaserScan()
        msg.header.stamp = stamp
        msg.header.frame_id = "laser"
        msg.angle_min = -math.pi
        msg.angle_max = math.pi
        msg.angle_increment = 2.0 * math.pi / _SCAN_BEAMS
        msg.range_min = 0.1
        msg.range_max = 30.0
        msg.ranges = [
            math.nan if faulty and beam % 10 < 6 else 4.0 + 2.0 * math.sin(beam / 20.0) + random.gauss(0.0, 0.01)
            for beam in range(_SCAN_BEAMS)
        ]
        self._scan.publish(msg)

    def _publish_tf(self, stamp, faulty: bool) -> None:
        """TF-003: `base_link -> laser` stops being updated."""
        edges = [("odom", "base_link")] + ([] if faulty else [("base_link", "laser")])
        transforms = []
        for parent, child in edges:
            transform = TransformStamped()
            transform.header.stamp = stamp
            transform.header.frame_id = parent
            transform.child_frame_id = child
            transform.transform.rotation.w = 1.0
            transforms.append(transform)
        self._tf.publish(TFMessage(transforms=transforms))

    def _publish_costmap(self) -> None:
        """MAP-002: obstacles sweep across the grid until nearly all of it is lethal."""
        cells = self._healthy_cells
        into_fault = phase_s() - HEALTHY_S
        if into_fault >= 0.0:
            reach = int(_SWEEP_END_COLUMN * min(1.0, into_fault / (FAULT_S * 0.7)))
            cells = [
                _LETHAL if cell >= 0 and index % _GRID_WIDTH < reach else cell
                for index, cell in enumerate(cells)
            ]
        grid = OccupancyGrid()
        grid.header.stamp = self._node.get_clock().now().to_msg()
        grid.header.frame_id = "map"
        grid.info.resolution = 0.05
        grid.info.width = _GRID_WIDTH
        grid.info.height = _GRID_HEIGHT
        grid.info.origin.orientation.w = 1.0
        grid.data = cells
        self._costmap.publish(grid)

    def _publish_goals(self) -> None:
        """ACT-001: goals start aborting."""
        stamp = self._node.get_clock().now().to_msg()
        if self._goals and self._goals[-1].status == GoalStatus.STATUS_EXECUTING:
            self._goals[-1].status = GoalStatus.STATUS_ABORTED if _faulty() else GoalStatus.STATUS_SUCCEEDED
        else:
            info = GoalInfo(goal_id=UUID(uuid=list(uuid.uuid4().bytes)), stamp=stamp)
            self._goals.append(GoalStatus(goal_info=info, status=GoalStatus.STATUS_EXECUTING))
        self._goals = self._goals[-_KEPT_GOALS:]
        self._status.publish(GoalStatusArray(status_list=self._goals))

    def _publish_bt_log(self) -> None:
        """BT-001: the tree keeps falling back on recoveries."""
        stamp = self._node.get_clock().now().to_msg()
        names = ("Spin", "BackUp", "ClearEntireCostmap-Local") if _faulty() else ("ComputePathToPose", "FollowPath")
        running = names[self._tick % len(names)]
        changes = [
            BehaviorTreeStatusChange(timestamp=stamp, node_name=name, previous_status=before, current_status=after)
            for name, before, after in ((running, "IDLE", "RUNNING"), (running, "RUNNING", "SUCCESS"), (running, "SUCCESS", "IDLE"))
        ]
        self._bt_log.publish(BehaviorTreeLog(timestamp=stamp, event_log=changes))


def main() -> None:
    """Spin the stack until stopped."""
    rclpy.init()
    node = rclpy.create_node("plugin_stack")
    PluginStack(node)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass


if __name__ == "__main__":
    main()
