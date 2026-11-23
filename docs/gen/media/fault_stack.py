"""A small ROS 2 node whose topics each trigger one plugin's faults, for the docs' screenshots."""
import math
import random
import uuid

import rclpy
from action_msgs.msg import GoalInfo, GoalStatus, GoalStatusArray
from rclpy.executors import ExternalShutdownException
from geometry_msgs.msg import TransformStamped, Twist
from nav2_msgs.msg import BehaviorTreeLog, BehaviorTreeStatusChange
from nav_msgs.msg import OccupancyGrid, Odometry
from sensor_msgs.msg import CompressedImage, Imu, LaserScan, NavSatFix, NavSatStatus
from tf2_msgs.msg import TFMessage
from unique_identifier_msgs.msg import UUID

_GRID_WIDTH = 160
_GRID_HEIGHT = 80
_LETHAL = 100
_FREE = 0
_UNKNOWN = -1
_INFLATION_CELLS = 7
_SCAN_BEAMS = 360
_PLACEHOLDER_VARIANCE = 1.0e6
_KEPT_GOALS = 6


def _grid_cells() -> list[int]:
    """A healthy local costmap: a room with furniture, inflated, and unknown beyond its walls."""
    def is_wall(column: int, row: int) -> bool:
        outer = column in (12, 147) or row in (8, 71)
        door = 62 <= column <= 74 and row == 71
        partition = column == 92 and not 30 <= row <= 42
        table = 30 <= column <= 48 and 24 <= row <= 34
        shelf = 112 <= column <= 136 and 54 <= row <= 58
        pillar = (column - 66) ** 2 + (row - 26) ** 2 <= 16
        return (outer and not door) or partition or table or shelf or pillar

    cells = [_UNKNOWN] * (_GRID_WIDTH * _GRID_HEIGHT)
    nearest: dict[tuple[int, int], float] = {}
    lethal = []
    for column in range(12, 148):
        for row in range(8, 72):
            cells[row * _GRID_WIDTH + column] = _FREE
            if is_wall(column, row):
                lethal.append((column, row))
    reach = range(-_INFLATION_CELLS, _INFLATION_CELLS + 1)
    for column, row in lethal:
        for x in reach:
            for y in reach:
                cell = (column + x, row + y)
                distance = math.hypot(x, y)
                if 12 <= cell[0] < 148 and 8 <= cell[1] < 72 and distance < nearest.get(cell, _INFLATION_CELLS + 1):
                    nearest[cell] = distance
    for (column, row), distance in nearest.items():
        cost = int(90 * (1.0 - distance / (_INFLATION_CELLS + 1)) ** 2)
        cells[row * _GRID_WIDTH + column] = cost
    for column, row in lethal:
        cells[row * _GRID_WIDTH + column] = _LETHAL
    return cells


class FaultStack:
    """Publishes one misbehaving topic per plugin."""

    def __init__(self, node) -> None:
        self._node = node
        self._tick = 0
        self._goals: list[GoalStatus] = []
        self._cells = _grid_cells()
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
        node.create_timer(0.1, self._fast)
        node.create_timer(1.0, self._slow)

    def _now(self):
        return self._node.get_clock().now().to_msg()

    def _fast(self) -> None:
        """The 10 Hz topics."""
        self._tick += 1
        stamp = self._now()
        self._publish_odom(stamp)
        self._publish_imu(stamp)
        self._publish_gps(stamp)
        self._publish_camera(stamp)
        self._publish_scan(stamp)
        self._publish_tf(stamp)
        command = Twist()
        command.linear.x = 0.5
        command.angular.z = 0.2 + 0.05 * math.sin(self._tick / 10.0)
        self._cmd.publish(command)

    def _slow(self) -> None:
        """The 1 Hz topics."""
        stamp = self._now()
        grid = OccupancyGrid()
        grid.header.stamp = stamp
        grid.header.frame_id = "map"
        grid.info.resolution = 0.05
        grid.info.width = _GRID_WIDTH
        grid.info.height = _GRID_HEIGHT
        grid.info.origin.orientation.w = 1.0
        grid.data = self._cells
        self._costmap.publish(grid)
        self._publish_goals(stamp)
        self._publish_bt_log(stamp)

    def _publish_odom(self, stamp) -> None:
        """LOC-001: a placeholder covariance."""
        msg = Odometry()
        msg.header.stamp = stamp
        msg.header.frame_id = "odom"
        msg.child_frame_id = "base_link"
        msg.pose.pose.position.x = 0.01 * self._tick
        msg.pose.pose.orientation.w = 1.0
        msg.pose.covariance[0] = _PLACEHOLDER_VARIANCE
        msg.pose.covariance[7] = _PLACEHOLDER_VARIANCE
        msg.pose.covariance[35] = 0.01
        self._odom.publish(msg)

    def _publish_imu(self, stamp) -> None:
        """IMU-005 and IMU-008: a wrong gravity at rest and an all-zero covariance."""
        msg = Imu()
        msg.header.stamp = stamp
        msg.header.frame_id = "imu_link"
        msg.orientation.w = 1.0
        msg.linear_acceleration.x = random.gauss(0.0, 0.01)
        msg.linear_acceleration.y = random.gauss(0.0, 0.01)
        msg.linear_acceleration.z = 5.0 + random.gauss(0.0, 0.01)
        msg.angular_velocity.z = random.gauss(0.0, 0.002)
        self._imu.publish(msg)

    def _publish_gps(self, stamp) -> None:
        """GNSS-001: no fix (its placeholder variance is not judged without one)."""
        msg = NavSatFix()
        msg.header.stamp = stamp
        msg.header.frame_id = "gps_link"
        msg.status.status = NavSatStatus.STATUS_NO_FIX
        msg.latitude = 45.0 + random.gauss(0.0, 1e-6)
        msg.longitude = 9.0 + random.gauss(0.0, 1e-6)
        msg.altitude = 120.0 + random.gauss(0.0, 0.05)
        msg.position_covariance = [25.0, 0.0, 0.0, 0.0, 25.0, 0.0, 0.0, 0.0, 64.0]
        msg.position_covariance_type = NavSatFix.COVARIANCE_TYPE_DIAGONAL_KNOWN
        self._gps.publish(msg)

    def _publish_camera(self, stamp) -> None:
        """IMG-002: a payload that doesn't start like the JPEG it declares."""
        msg = CompressedImage()
        msg.header.stamp = stamp
        msg.header.frame_id = "camera_link"
        msg.format = "jpeg"
        msg.data = bytes([self._tick % 256]) * 48000
        self._camera.publish(msg)

    def _publish_scan(self, stamp) -> None:
        """SENS-001: four readings in ten are NaN."""
        msg = LaserScan()
        msg.header.stamp = stamp
        msg.header.frame_id = "laser"
        msg.angle_min = -math.pi
        msg.angle_max = math.pi
        msg.angle_increment = 2.0 * math.pi / _SCAN_BEAMS
        msg.range_min = 0.1
        msg.range_max = 30.0
        msg.ranges = [
            math.nan if beam % 10 < 4 else 4.0 + 2.0 * math.sin(beam / 20.0) + random.gauss(0.0, 0.01)
            for beam in range(_SCAN_BEAMS)
        ]
        self._scan.publish(msg)

    def _publish_tf(self, stamp) -> None:
        """TF-001: `laser` has two parents."""
        edges = (("map", "odom"), ("odom", "base_link"), ("base_link", "laser"), ("odom", "laser"))
        transforms = []
        for parent, child in edges:
            transform = TransformStamped()
            transform.header.stamp = stamp
            transform.header.frame_id = parent
            transform.child_frame_id = child
            transform.transform.rotation.w = 1.0
            transforms.append(transform)
        self._tf.publish(TFMessage(transforms=transforms))

    def _publish_goals(self, stamp) -> None:
        """ACT-001: three goals in four abort."""
        second = self._tick // 10
        if second % 2 == 0:
            goal_id = UUID(uuid=list(uuid.uuid4().bytes))
            info = GoalInfo(goal_id=goal_id, stamp=stamp)
            self._goals.append(GoalStatus(goal_info=info, status=GoalStatus.STATUS_EXECUTING))
        elif self._goals and self._goals[-1].status == GoalStatus.STATUS_EXECUTING:
            succeeded = second % 8 == 1
            self._goals[-1].status = GoalStatus.STATUS_SUCCEEDED if succeeded else GoalStatus.STATUS_ABORTED
        self._goals = self._goals[-_KEPT_GOALS:]
        self._status.publish(GoalStatusArray(status_list=self._goals))

    def _publish_bt_log(self, stamp) -> None:
        """BT-001: a recovery every few seconds."""
        if self._tick // 10 % 4 != 0:
            return
        change = BehaviorTreeStatusChange(
            timestamp=stamp, node_name="Spin", previous_status="IDLE", current_status="RUNNING"
        )
        self._bt_log.publish(BehaviorTreeLog(timestamp=stamp, event_log=[change]))


def main() -> None:
    """Spin the stack until stopped."""
    rclpy.init()
    node = rclpy.create_node("fault_stack")
    FaultStack(node)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass


if __name__ == "__main__":
    main()
