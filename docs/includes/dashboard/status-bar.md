<figure class="tx-map tx-map--strip" style="max-width:1263px">
<div class="tx-map__frame" style="aspect-ratio:1098/18">
<div class="tx-map__view"><img src="../assets/media/tui/dashboard.png" alt="Part of the dashboard, with these marked: Name, ROS distro, Domain, Middleware, Uptime, Verdict, Problem counts, Weighted health" style="width:100.182%;left:-0.091%;top:-5.556%"></div>
<span class="tx-spot" style="left:0.82%;top:0%;width:5.738%;height:100%" title="Name: The tool you are looking at."><b class="tx-spot__badge--top">1</b></span>
<span class="tx-spot" style="left:8.197%;top:0%;width:7.377%;height:100%" title="ROS distro: The ROS 2 distribution this session runs on."><b class="tx-spot__badge--top">2</b></span>
<span class="tx-spot" style="left:18.033%;top:0%;width:8.197%;height:100%" title="Domain: The ROS_DOMAIN_ID. Testudo only sees topics on this domain."><b class="tx-spot__badge--top">3</b></span>
<span class="tx-spot" style="left:28.689%;top:0%;width:14.754%;height:100%" title="Middleware: The RMW implementation in use. Hidden when the terminal is under 120 columns."><b class="tx-spot__badge--top">4</b></span>
<span class="tx-spot" style="left:45.902%;top:0%;width:4.918%;height:100%" title="Uptime: How long this dashboard has been open."><b class="tx-spot__badge--top">5</b></span>
<span class="tx-spot" style="left:66.393%;top:0%;width:7.377%;height:100%" title="Verdict: The state of the whole stack, and the first thing to read."><b class="tx-spot__badge--top">6</b></span>
<span class="tx-spot" style="left:74.59%;top:0%;width:4.098%;height:100%" title="Problem counts: How many topics are stale (■), in error (✗) or in warning (▲). Nodes are not counted."><b class="tx-spot__badge--top">7</b></span>
<span class="tx-spot" style="left:80.328%;top:0%;width:18.852%;height:100%" title="Weighted health: 100% when every topic is OK, falling as topics degrade. Shown when severity_mode is both or weighted."><b class="tx-spot__badge--top">8</b></span>
</div>
</figure>

| # | On screen | What it tells you | Read more |
|---|---|---|---|
| <span class="tx-num">1</span> | **Name** | The tool you are looking at. | [Home](../index.md) |
| <span class="tx-num">2</span> | **ROS distro** | The ROS 2 distribution this session runs on. | [Install](../getting-started/index.md#1-install) |
| <span class="tx-num">3</span> | **Domain** | The `ROS_DOMAIN_ID`. Testudo only sees topics on this domain. | [Watch your stack](../getting-started/index.md#2-watch-your-stack) |
| <span class="tx-num">4</span> | **Middleware** | The RMW implementation in use. Hidden when the terminal is under 120 columns. | [Watch your stack](../getting-started/index.md#2-watch-your-stack) |
| <span class="tx-num">5</span> | **Uptime** | How long this dashboard has been open. | [Watch your stack](../getting-started/index.md#2-watch-your-stack) |
| <span class="tx-num">6</span> | **Verdict** | The state of the whole stack, and the first thing to read. | [The overall verdict](../concepts/severity.md#the-overall-verdict) |
| <span class="tx-num">7</span> | **Problem counts** | How many topics are stale (`■`), in error (`✗`) or in warning (`▲`). Nodes are not counted. | [Severity](../concepts/severity.md#levels-decide-severity) |
| <span class="tx-num">8</span> | **Weighted health** | 100% when every topic is OK, falling as topics degrade. Shown when `severity_mode` is `both` or `weighted`. | [The overall verdict](../concepts/severity.md#the-overall-verdict) |
