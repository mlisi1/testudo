<figure class="tx-map" style="max-width:662px">
<div class="tx-map__frame" style="aspect-ratio:576/18">
<div class="tx-map__view"><img src="../assets/media/tui/dashboard.png" alt="Part of the dashboard, with these marked: Name, ROS distro, Domain, Middleware, Uptime" style="width:190.972%;left:-0.174%;top:-5.556%"></div>
<a class="tx-spot" style="left:1.562%;top:0%;width:10.938%;height:100%" title="Name: The tool you are looking at." href="../"><b class="tx-spot__badge--top">1</b></a>
<a class="tx-spot" style="left:15.625%;top:0%;width:14.062%;height:100%" title="ROS distro: The ROS 2 distribution this session runs on." href="../getting-started/#1-install"><b class="tx-spot__badge--top">2</b></a>
<a class="tx-spot" style="left:34.375%;top:0%;width:15.625%;height:100%" title="Domain: The ROS_DOMAIN_ID. Testudo only sees topics on this domain." href="../getting-started/#2-watch-your-stack"><b class="tx-spot__badge--top">3</b></a>
<span class="tx-spot" style="left:54.688%;top:0%;width:28.125%;height:100%" title="Middleware: The RMW implementation in use. Hidden when the terminal is under 120 columns."><b class="tx-spot__badge--top">4</b></span>
<span class="tx-spot" style="left:87.5%;top:0%;width:9.375%;height:100%" title="Uptime: How long this dashboard has been open."><b class="tx-spot__badge--top">5</b></span>
</div>
</figure>
<figure class="tx-map" style="max-width:455px">
<div class="tx-map__frame" style="aspect-ratio:396/18">
<div class="tx-map__view"><img src="../assets/media/tui/dashboard.png" alt="Part of the dashboard, with these marked: Verdict, Problem counts, Weighted health" style="width:277.778%;left:-177.525%;top:-5.556%"></div>
<a class="tx-spot" style="left:6.818%;top:0%;width:20.455%;height:100%" title="Verdict: The state of the whole stack, and the first thing to read." href="../concepts/severity/#the-overall-verdict"><b class="tx-spot__badge--top">6</b></a>
<a class="tx-spot" style="left:29.545%;top:0%;width:11.364%;height:100%" title="Problem counts: How many topics are stale (■), in error (✗) or in warning (▲). Nodes are not counted." href="../concepts/severity/#levels-decide-severity"><b class="tx-spot__badge--top">7</b></a>
<a class="tx-spot" style="left:45.455%;top:0%;width:52.273%;height:100%" title="Weighted health: 100% when every topic is OK, falling as topics degrade. Shown when severity_mode is both or weighted." href="../concepts/severity/#the-overall-verdict"><b class="tx-spot__badge--top">8</b></a>
</div>
</figure>

| # | On screen | What it tells you | Read more |
|---|---|---|---|
| <span class="tx-num">1</span> | **Name** | The tool you are looking at. | [Home](../index.md) |
| <span class="tx-num">2</span> | **ROS distro** | The ROS 2 distribution this session runs on. | [Install](../getting-started/index.md#1-install) |
| <span class="tx-num">3</span> | **Domain** | The `ROS_DOMAIN_ID`. Testudo only sees topics on this domain. | [Watch your stack](../getting-started/index.md#2-watch-your-stack) |
| <span class="tx-num">4</span> | **Middleware** | The RMW implementation in use. Hidden when the terminal is under 120 columns. | – |
| <span class="tx-num">5</span> | **Uptime** | How long this dashboard has been open. | – |
| <span class="tx-num">6</span> | **Verdict** | The state of the whole stack, and the first thing to read. | [The overall verdict](../concepts/severity.md#the-overall-verdict) |
| <span class="tx-num">7</span> | **Problem counts** | How many topics are stale (`■`), in error (`✗`) or in warning (`▲`). Nodes are not counted. | [Severity](../concepts/severity.md#levels-decide-severity) |
| <span class="tx-num">8</span> | **Weighted health** | 100% when every topic is OK, falling as topics degrade. Shown when `severity_mode` is `both` or `weighted`. | [The overall verdict](../concepts/severity.md#the-overall-verdict) |
