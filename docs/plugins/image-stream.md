# Image stream

Checks that a camera is delivering real frames, without decoding them.

## What it covers

| | |
|---|---|
| Message types | `sensor_msgs/msg/Image`, `sensor_msgs/msg/CompressedImage` |
| Tier | **Presence unless declared** |
| TUI category | Cameras |
| Published on | `/testudo/image_stream` |

## What it needs

Declare the topic. Subscribing to camera data can load the pipeline producing it, so an undeclared camera only gets a check that a publisher exists.

```yaml
topics:
  sensor_msgs/msg/CompressedImage:
    - name: /camera/image_raw/compressed
```

## Codes

### IMG-001 · Empty payload { #img-001 }

<span class="sev sev-error">✗ ERROR</span> The frame carries no data.

**Likely causes:** a camera or encoder failing to produce a frame.

**What to do:** check the camera driver's log.

### IMG-002 · Malformed frame { #img-002 }

<span class="sev sev-error">✗ ERROR</span> A raw image has a non-positive size or less data than `height × step`, or a compressed image has an empty `format` or a payload that doesn't start like the JPEG or PNG it declares.

**Likely causes:** a truncated frame or a misconfigured encoder.

**What to do:** the code's message says which of these happened.

### IMG-003 · Frame stuck { #img-003 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> The frame has been byte-identical for more consecutive checked messages than `stuck_count` allows.

**Likely causes:** a frozen driver republishing its last frame.

**What to do:** restart the driver and check the camera's connection.

### IMG-004 · Frame size dropped { #img-004 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> The frame is smaller than `size_drop_ratio` times the average of the recent frames. Lower is worse here.

**Likely causes:** a blank, black or corrupted frame, which compresses to far fewer bytes.

**What to do:** look at the image. Cameras with spiky compressed sizes may need a lower `warn`.

### IMG-005 · Frame id changed { #img-005 }

<span class="sev sev-warn">▲ WARN</span> `header.frame_id` differs from an earlier message on this topic. It stays active until stats are reset with ++r++.

**Likely causes:** two publishers on one topic, or a reconfigured driver.

**What to do:** check how many nodes publish the topic.

## Settings

--8<-- "levels/image-stream.md"

## In the TUI

The Topic Panel adds a `Size` column. The Detail Panel shows the format, frame size and an estimated bandwidth.

## Known limits

- Frames are never decoded, so a sharp but wrong image is not detected.
- The bandwidth estimate only counts checked frames, so it under-reports a decimated topic.
- Other compressed formats are not checked for matching magic bytes.
