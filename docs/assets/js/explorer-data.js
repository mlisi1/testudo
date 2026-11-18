/* Sample data for the Explore page: a small stack with a few things wrong, written by hand. */
window.TESTUDO_EXPLORER = {
  codePages: {
    LOC: "plugins/localization", IMU: "plugins/imu", GNSS: "plugins/gnss", IMG: "plugins/image-stream",
    SENS: "plugins/point-stream", VEL: "plugins/velocity-command", MAP: "plugins/costmap", ACT: "plugins/actions",
    BT: "plugins/bt-log", TF: "plugins/tf-watch", LIVE: "core-checks/live", STAMP: "core-checks/stamp",
    CHECK: "core-checks/check", NODE: "core-checks/node",
  },

  cleared: [
    { code: "LOC-002", sev: 1, ago: "2m", topic: "/odom" },
  ],

  categories: [
    {
      name: "Localization", page: "plugins/localization", column: "σxy",
      topics: [
        {
          name: "/odom", type: "nav_msgs/msg/Odometry", tier: "full", rate: "10.0", extra: "±1414.21",
          values: [
            ["x", "±1000m   roll  ±0.00°   v  0.00 m/s"], ["y", "±1000m   pitch ±0.00°   ω  0.00 rad/s"],
            ["z", "±0.00m   yaw   ±1.81°   PSD ✓ pose ✓ twist"], ["pose", "x 8.750  y 0.000  z 0.000  yaw 0.0°"],
            ["stamp lag", "0.007 s"], ["publisher", "/ekf_node (reliable, volatile)"],
          ],
          codes: [{ code: "LOC-001", sev: 2, msg: "position covariance high (trace 2e+06 m²)" }],
          past: ["▲ [LOC-002] cleared 2m ago, lasted 5s ×3 · yaw variance high"],
        },
        {
          name: "/amcl_pose", type: "geometry_msgs/msg/PoseWithCovarianceStamped", tier: "full", rate: "2.00", extra: "±0.07",
          values: [["x", "±0.05m   yaw ±1.20°"], ["y", "±0.05m"], ["pose", "x 3.210  y -1.044  yaw 12.5°"], ["frame", "map · global"]],
        },
      ],
    },
    {
      name: "IMU", page: "plugins/imu", column: "|a|",
      topics: [{
        name: "/imu/data", type: "sensor_msgs/msg/Imu", tier: "full", rate: "100", extra: "4.99",
        values: [
          ["accel", "x -0.01  y -0.00  z 4.99   |a| 4.99 m/s²"], ["gyro", "x 0.000  y 0.000  z 0.001 rad/s"],
          ["stuck", "0"], ["invalid", "0%"], ["covariance", "orient ✓ · gyro ✓ · accel ✓"], ["frame", "imu_link"],
          ["gravity", "bad scale  |a| 4.99 at rest"], ["stamp lag", "0.005 s"],
        ],
        codes: [{ code: "IMU-005", sev: 1, msg: "|a| at rest is 4.99 m/s², expected 9.81 or 0" }],
      }],
    },
    {
      name: "GNSS", page: "plugins/gnss", column: "Fix",
      topics: [{
        name: "/gps/fix", type: "sensor_msgs/msg/NavSatFix", tier: "full", rate: "10.0", extra: "NO_FIX",
        values: [["fix", "● NO_FIX   σ horiz ±7.07m   σ vert ±8.00m"], ["fixes", "20/20 dropped"], ["jump", "0.09 m/s"], ["stuck", "0"], ["invalid", "0%"], ["position", "lat 45.0000001  lon 8.9999998  alt 120.0 m"], ["frame", "gps_link"]],
        codes: [
          { code: "GNSS-001", sev: 2, msg: "no fix" },
          { code: "GNSS-002", sev: 1, msg: "position σ high (σx ±5.00m, σy ±5.00m, σz ±8.00m)" },
        ],
      }],
    },
    {
      name: "Cameras", page: "plugins/image-stream", column: "Size",
      topics: [
        {
          name: "/camera/image_raw/compressed", type: "sensor_msgs/msg/CompressedImage", tier: "full", rate: "10.0", extra: "46.9KB",
          values: [["stream", "jpeg  46.9KB/frame · 293.6KB/s checked"], ["sizes", "100% of avg (last 20)"], ["stuck", "24"], ["payload", "✓ jpeg"], ["frame", "camera_link"], ["stamp lag", "0.001 s"]],
          codes: [{ code: "IMG-003", sev: 2, msg: "frame identical for 24 messages" }],
        },
        {
          name: "/camera/depth/image_raw", type: "sensor_msgs/msg/Image", tier: "presence", quiet: "◌ present", rate: "-",
          values: [["publisher", "/camera_driver (best effort, volatile)"]],
        },
      ],
    },
    {
      name: "Lidar", page: "plugins/point-stream", column: "Points",
      topics: [{
        name: "/scan", type: "sensor_msgs/msg/LaserScan", tier: "full", rate: "10.0", extra: "360",
        values: [["returns", "40% valid · 0% no return · 60% invalid"], ["beams", "360 @ 1°"], ["stuck", "0"], ["frame", "laser"], ["stamp lag", "0.005 s"]],
        codes: [{ code: "SENS-001", sev: 2, msg: "invalid readings 60.0%" }],
      }],
    },
    {
      name: "Velocity Commands", page: "plugins/velocity-command", column: "Cmd",
      topics: [{
        name: "/cmd_vel_nav", type: "geometry_msgs/msg/Twist", tier: "full", rate: "10.0", extra: "0.50 +0.14",
        values: [["linear", "0.50 m/s"], ["angular", "+0.14 rad/s"], ["reversals", "0.2 /s"], ["pinned", "0.1 s"], ["not moving", "8.2 s"]],
        codes: [{ code: "VEL-002", sev: 2, msg: "commanded to move for 8.2 s, odometry reports no motion" }],
      }],
    },
    {
      name: "Maps", page: "plugins/costmap", column: "Lethal",
      topics: [
        {
          name: "/local_costmap/costmap", type: "nav_msgs/msg/OccupancyGrid", tier: "full", rate: "2.00", extra: "12%",
          values: [["size", "160 x 80 cells"], ["extent", "8.0 x 4.0 m"], ["resolution", "0.05 m"], ["lethal", "12.0%"], ["unknown", "32.0%"], ["frame", "map"]],
        },
        {
          name: "/map", type: "nav_msgs/msg/OccupancyGrid", tier: "full", quiet: "● latched", rate: "", extra: "4%",
          values: [["size", "2048 x 2048 cells"], ["extent", "102.4 x 102.4 m"], ["resolution", "0.05 m"], ["lethal", "4.0%"], ["unknown", "71.0%"], ["frame", "map"]],
        },
      ],
    },
    {
      name: "TF", page: "plugins/tf-watch", column: "Frames",
      topics: [{
        name: "/tf", type: "tf2_msgs/msg/TFMessage", tier: "full", rate: "40.0", extra: "3 ✗1",
        values: [["frames", "3   2 dynamic · 0 static edges"], ["roots", "odom (3)"], ["tree", "odom ─ base_link ─ laser"], ["stale", "laser 12s"], ["chains", "✓ 1/1 watched chains connected"]],
        codes: [{ code: "TF-003", sev: 1, msg: "1 dynamic edge(s) not updated in >10s" }],
      }],
    },
    {
      name: "Actions", page: "plugins/actions", column: "Last 5", rate: "State",
      topics: [{
        name: "/navigate_to_pose", type: "action_msgs/msg/GoalStatusArray", tier: "full", rate: "◌ idle", extra: "✗✗✓✗✗",
        values: [["state", "◌ idle 0.1s · last ✗ aborted in 12.4s"], ["history", "✗✗✓✗✗✗✓✗✗✗  4✓ 16✗ 0⊘"], ["success", "20%"], ["mean duration", "12.4 s"], ["goal rate", "2 /min"]],
        codes: [{ code: "ACT-001", sev: 2, msg: "success rate 20%" }],
      }],
    },
    {
      name: "BT Navigator", page: "plugins/bt-log", column: "Rec/m",
      topics: [{
        name: "/behavior_tree_log", type: "nav2_msgs/msg/BehaviorTreeLog", tier: "full", quiet: "◌ idle", rate: "1.00", extra: "4.0",
        values: [["running", "◌ idle"], ["recoveries", "4.0/min   last: Spin 12s ago"], ["recovery counts", "BackUp 1 · Spin 2"]],
      }],
    },
    {
      name: "Nodes", page: "core-checks/node", column: "CPU%", rate: "Mem", nodes: true,
      topics: [
        { name: "/controller_server", type: "node", tier: "node", rate: "86.2M", extra: "14.1", values: [["cpu", "14.1 %"], ["memory", "86.2 MB (0.27 %)"], ["threads", "12"]] },
        { name: "/planner_server", type: "node", tier: "node", rate: "64.8M", extra: "3.4", values: [["cpu", "3.4 %"], ["memory", "64.8 MB (0.20 %)"], ["threads", "9"]] },
      ],
    },
    {
      name: "Other Topics", last: "other",
      topics: [
        { name: "/diagnostics_agg", type: "diagnostic_msgs/msg/DiagnosticArray", tier: "vitals", rate: "1.00", values: [["message count", "128"], ["age", "0.42 s"]] },
        { name: "/goal_pose", type: "geometry_msgs/msg/PoseStamped", tier: "vitals", quiet: "◌ input", rate: "-", values: [["subscribers", "/bt_navigator"]] },
      ],
    },
    {
      name: "Excluded Topics", last: "excluded",
      topics: [{ name: "/velodyne_packets", type: "velodyne_msgs/msg/VelodyneScan", tier: "excluded", excluded: true, rate: "", values: [["rule", "literal /velodyne_packets"]] }],
    },
  ],

  tips: {
    start: { title: "Try it.", text: "Click the dashboard, then press j and k, Tab, n or ?. Point at anything on it to see what it means." },
    brand: { title: "Testudo", text: "The tool you are looking at.", page: "", link: "Home" },
    distro: { title: "ROS distro", text: "The ROS 2 distribution this session runs on.", page: "getting-started/#1-install", link: "Install" },
    domain: { title: "Domain", text: "The ROS_DOMAIN_ID. Testudo only sees topics on this domain.", page: "getting-started/#2-watch-your-stack", link: "Watch your stack" },
    rmw: { title: "Middleware", text: "The RMW implementation in use. It is hidden when the terminal is under 120 columns." },
    uptime: { title: "Uptime", text: "How long this dashboard has been open." },
    paused: { title: "Paused", text: "The display is frozen. Checks and publishing continue.", page: "tui/keys/", link: "Keys" },
    verdict: { title: "Verdict", text: "The state of the whole stack, and the first thing to read.", page: "concepts/severity/#the-overall-verdict", link: "The overall verdict" },
    counts: { title: "Problem counts", text: "How many topics are stale, in error or in warning. Nodes are not counted.", page: "concepts/severity/#levels-decide-severity", link: "Severity" },
    gauge: { title: "Weighted health", text: "100% when every topic is OK, falling as topics degrade. Shown when severity_mode is both or weighted.", page: "concepts/severity/#the-overall-verdict", link: "The overall verdict" },
    breakdown: { title: "Severity breakdown", text: "How many of the category's topics are at each severity, worst first.", page: "concepts/severity/", link: "Severity" },
    sort: { title: "Sort marker", text: "▼ sorts worst first, ▲ by name. Press s to switch.", page: "tui/keys/", link: "Keys" },
    attention: { title: "Needs attention", text: "Up to five active codes across the whole stack, worst first. Click one to jump to its topic.", page: "concepts/faults/", link: "Faults" },
    where: { title: "Where", text: "The topic with this code, or how many topics share it.", page: "tui/keys/", link: "Keys" },
    more: { title: "More", text: "Codes that did not fit. Press n to visit every topic with a problem.", page: "tui/keys/", link: "Keys" },
    cleared: { title: "Cleared", text: "Faults that went away, so a problem that came and went isn't lost.", page: "concepts/faults/", link: "Faults" },
    topic: { title: "Topic", text: "Every topic in the highlighted category. The tier decides how closely each one is watched.", page: "concepts/tiers/", link: "Monitoring tiers" },
    status: { title: "Status", text: "The topic's severity right now.", page: "concepts/severity/", link: "Severity" },
    quiet: { title: "Quiet by design", text: "Nothing is wrong: this topic is allowed to be silent, or Testudo doesn't subscribe to it.", page: "concepts/liveness/", link: "Liveness" },
    excluded: { title: "Excluded", text: "You asked Testudo to ignore this topic, so nothing is checked.", page: "concepts/tiers/#excluded-tier", link: "Excluded tier" },
    hz: { title: "Hz", text: "The publish rate over the last 20 messages.", page: "core-checks/live/#live-004", link: "LIVE-004" },
    state: { title: "State", text: "What the action server is doing now.", page: "plugins/actions/", link: "Actions" },
    mem: { title: "Mem", text: "The node's memory use.", page: "core-checks/node/", link: "Node profiling" },
    column: { title: "Plugin column", text: "One number chosen by the plugin that checks this category.", page: "plugins/", link: "Plugins" },
    dsev: { title: "Severity", text: "The highlighted topic's current severity.", page: "concepts/severity/#levels-decide-severity", link: "Severity" },
    dtopic: { title: "Topic name", text: "The full name. In the real dashboard, y copies it.", page: "tui/keys/", link: "Keys" },
    dtype: { title: "Message type", text: "The type decides which plugin checks the topic.", page: "plugins/", link: "Plugins" },
    dtier: { title: "Tier", text: "How closely Testudo monitors this topic.", page: "concepts/tiers/", link: "Monitoring tiers" },
    keys: { title: "Keys", text: "The keys you need most.", page: "tui/keys/", link: "Every key" },
    actions: { title: "Quick actions", text: "Press a on a topic with a code to downgrade or mute it.", page: "tui/quick-actions/", link: "Quick actions" },
    options: { title: "Options", text: "Exclude topics and change check and display settings. The real dashboard opens a menu here.", page: "tui/options/", link: "Options" },
  },
};
