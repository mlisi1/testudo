---
title: Testudo
hide:
  - navigation
  - toc
---

# Testudo { .testudo-page-title }

<div class="testudo-hero" markdown>
![Testudo](logo_wordmark.svg)
</div>

<p class="testudo-tagline"><strong>Is my nav stack healthy right now, and if not, what broke first?</strong></p>

Testudo watches a running ROS 2 robot from the outside, with no changes to its nodes. It judges every topic at once and points at the problem, live in the terminal or from a recorded bag.

Severities read the same everywhere: <span class="sev sev-ok">✓ OK</span> <span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> <span class="sev sev-stale">■ STALE</span>
