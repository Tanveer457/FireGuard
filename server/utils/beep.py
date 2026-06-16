"""
utils/beep.py — System beep notification for fire/smoke alerts

Called automatically when a CRITICAL or HIGH alert is confirmed.
Works on Windows, Linux, and Mac without any extra packages.

Beep patterns:
  CRITICAL → 5 fast beeps  (urgent)
  HIGH     → 3 beeps       (serious)
  MEDIUM   → 1 beep        (attention)
  LOW      → no beep       (logged only)
"""
import asyncio
import logging
import sys

logger = logging.getLogger(__name__)


def _beep_sync(frequency: int, duration_ms: int, count: int, gap_ms: int):
    """
    Synchronous beep — runs in a thread so it doesn't block the async loop.

    Windows : uses winsound.Beep() — actual speaker beep at exact frequency
    Linux   : writes \\a (bell character) to terminal
    Mac     : uses afplay to play system alert sound
    """
    try:
        if sys.platform == "win32":
            import winsound
            for i in range(count):
                winsound.Beep(frequency, duration_ms)
                if i < count - 1:
                    import time
                    time.sleep(gap_ms / 1000)

        elif sys.platform == "darwin":
            import subprocess, time
            for i in range(count):
                subprocess.run(["afplay", "/System/Library/Sounds/Sosumi.aiff"],
                               capture_output=True)
                if i < count - 1:
                    time.sleep(gap_ms / 1000)

        else:
            # Linux — print bell character to terminal
            import time
            for i in range(count):
                print("\a", end="", flush=True)
                if i < count - 1:
                    time.sleep(gap_ms / 1000)

    except Exception as e:
        logger.debug(f"Beep failed (non-critical): {e}")


async def beep_alert(threat_level: str, cam_id: int):
    """
    Async wrapper — runs beep in a thread pool so the event loop never blocks.
    Called from alert_service.create_alert() after saving to DB.

    Args:
        threat_level: "CRITICAL", "HIGH", "MEDIUM", "LOW", "SMOKE"
        cam_id: camera that triggered the alert (for logging)
    """
    patterns = {
        # (frequency_hz, duration_ms, count, gap_ms)
        "CRITICAL": (1200, 300, 5, 150),   # 5 fast high-pitched beeps
        "HIGH":     (1000, 400, 3, 200),   # 3 medium beeps
        "MEDIUM":   (800,  500, 1, 0),     # 1 low beep
    }

    if threat_level not in patterns:
        return  # LOW and SMOKE don't beep

    freq, dur, count, gap = patterns[threat_level]
    logger.info(f"🔔 BEEP — {threat_level} alert on camera {cam_id} "
                f"({count} beep{'s' if count > 1 else ''})")

    # Run in thread pool — winsound.Beep() is blocking
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _beep_sync, freq, dur, count, gap)