# Check failures

A plugin that raises an exception never stops the monitoring of other topics.

## Codes

### CHECK-001 · A plugin's check failed { #check-001 }

<span class="sev sev-error">✗ ERROR</span> A plugin raised an exception while handling this topic. The failure is logged once with its traceback, and what the row shows can no longer be trusted.

**Likely causes:** a bug in the plugin, or a message it didn't expect.

**What to do:** read the logged traceback. Press ++r++ to reset stats and clear the code once the cause is gone.
