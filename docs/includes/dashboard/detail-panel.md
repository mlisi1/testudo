<figure class="tx-map" style="max-width:911px">
<div class="tx-map__frame" style="aspect-ratio:792/162">
<div class="tx-map__view"><img src="../assets/media/tui/dashboard.png" alt="Part of the dashboard, with these marked: Severity, Topic name, Message type, Tier, stream, sizes, stuck, payload, frame, stamp lag, Active code" style="width:138.889%;left:-0.126%;top:-267.284%"></div>
<span class="tx-spot" style="left:1.136%;top:0%;width:7.955%;height:11.111%" title="Severity: The highlighted topic&#x27;s current severity."><b class="tx-spot__badge--top">21</b></span>
<span class="tx-spot" style="left:11.364%;top:0%;width:32.955%;height:11.111%" title="Topic name: The full name. Press y to copy it."><b class="tx-spot__badge--top">22</b></span>
<span class="tx-spot" style="left:45.455%;top:0%;width:35.227%;height:11.111%" title="Message type: The type decides which plugin checks the topic."><b class="tx-spot__badge--top">23</b></span>
<span class="tx-spot" style="left:84.091%;top:0%;width:10.227%;height:11.111%" title="Tier: How closely Testudo monitors this topic."><b class="tx-spot__badge--top">24</b></span>
<span class="tx-spot" style="left:1.136%;top:11.111%;width:64.773%;height:11.111%" title="stream: Image format, size of the last frame and throughput of the frames checked."><b class="tx-spot__badge--right">25</b></span>
<span class="tx-spot" style="left:1.136%;top:22.222%;width:65.909%;height:11.111%" title="sizes: The last 20 frame sizes, and the newest frame as a share of their average."><b class="tx-spot__badge--right">26</b></span>
<span class="tx-spot" style="left:1.136%;top:33.333%;width:9.091%;height:11.111%" title="stuck: How many checked frames in a row were byte-identical."><b class="tx-spot__badge--right">27</b></span>
<span class="tx-spot" style="left:1.136%;top:44.444%;width:76.136%;height:11.111%" title="payload: Whether the data starts the way its declared format should."><b class="tx-spot__badge--right">28</b></span>
<span class="tx-spot" style="left:1.136%;top:55.556%;width:25%;height:11.111%" title="frame: The message&#x27;s header.frame_id."><b class="tx-spot__badge--right">29</b></span>
<span class="tx-spot" style="left:1.136%;top:66.667%;width:20.455%;height:11.111%" title="stamp lag: How far the message&#x27;s stamp is behind the clock."><b class="tx-spot__badge--right">30</b></span>
<span class="tx-spot" style="left:1.136%;top:88.889%;width:75%;height:11.111%" title="Active code: What is wrong on this topic now, one line per code."><b class="tx-spot__badge--right">31</b></span>
</div>
</figure>

| # | On screen | What it tells you | Read more |
|---|---|---|---|
| <span class="tx-num">21</span> | **Severity** | The highlighted topic's current severity. | [Severity](../concepts/severity.md#levels-decide-severity) |
| <span class="tx-num">22</span> | **Topic name** | The full name. Press ++y++ to copy it. | [Keys](../tui/keys.md) |
| <span class="tx-num">23</span> | **Message type** | The type decides which plugin checks the topic. | [Plugins](../plugins/index.md) |
| <span class="tx-num">24</span> | **Tier** | How closely Testudo monitors this topic. | [Monitoring tiers](../concepts/tiers.md#full-tier) |
| <span class="tx-num">25</span> | **`stream`** | Image format, size of the last frame and throughput of the frames checked. | [Image stream](../plugins/image-stream.md) |
| <span class="tx-num">26</span> | **`sizes`** | The last 20 frame sizes, and the newest frame as a share of their average. | [IMG-004](../plugins/image-stream.md#img-004) |
| <span class="tx-num">27</span> | **`stuck`** | How many checked frames in a row were byte-identical. | [IMG-003](../plugins/image-stream.md#img-003) |
| <span class="tx-num">28</span> | **`payload`** | Whether the data starts the way its declared format should. | [IMG-002](../plugins/image-stream.md#img-002) |
| <span class="tx-num">29</span> | **`frame`** | The message's `header.frame_id`. | [IMG-005](../plugins/image-stream.md#img-005) |
| <span class="tx-num">30</span> | **`stamp lag`** | How far the message's stamp is behind the clock. | [STAMP-003](../core-checks/stamp.md#stamp-003) |
| <span class="tx-num">31</span> | **Active code** | What is wrong on this topic now, one line per code. | [IMG-002](../plugins/image-stream.md#img-002) |
