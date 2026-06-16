# monitoring/health.py
# Monitors pipeline health and prints stats every N seconds.
# Tracks: FPS per camera, inference time, alerts, bytes sent, CPU/memory.

import time
import threading
import logging
import platform

log = logging.getLogger("health")

# Try importing psutil for CPU/memory — optional dependency
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# Try importing for GPU temp on Jetson
try:
    from pathlib import Path
    _JETSON = Path("/sys/devices/virtual/thermal/thermal_zone1/temp").exists()
except Exception:
    _JETSON = False


def _read_jetson_temp() -> float:
    try:
        val = int(Path("/sys/devices/virtual/thermal/thermal_zone1/temp").read_text())
        return val / 1000.0
    except Exception:
        return -1.0


class HealthMonitor:
    def __init__(self, interval_sec: int, streams, detector, alert_mgr, client):
        self.interval   = interval_sec
        self._streams   = streams
        self._detector  = detector
        self._alert_mgr = alert_mgr
        self._client    = client
        self._running   = False
        self._start_ts  = time.time()

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="health").start()
        log.info("Health monitor started")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            time.sleep(self.interval)
            self._print_stats()

    def _print_stats(self):
        uptime   = int(time.time() - self._start_ts)
        h, m, s  = uptime // 3600, (uptime % 3600) // 60, uptime % 60

        lines = [
            "",
            "─" * 60,
            f"  PIPELINE HEALTH   uptime: {h:02d}:{m:02d}:{s:02d}",
            "─" * 60,
        ]

        # Camera streams
        lines.append("  Cameras:")
        for name, status in self._streams.status().items():
            state = "✓ LIVE" if status["connected"] else "✗ OFFLINE"
            lines.append(
                f"    {name:<20s}  {state}  "
                f"FPS={status['fps']:>5.1f}  "
                f"frames={status['total_frames']}"
            )

        # Inference
        det = self._detector.stats()
        lines.append(
            f"  Inference:   {det['total_inferences']} calls  "
            f"avg={det['avg_inference_ms']}ms  "
            f"detections={det['total_detections']}"
        )

        # Alerts
        al = self._alert_mgr.stats()
        lines.append(f"  Alerts:      {al['total_alerts']} total  per_camera={al['per_camera']}")

        # Transmission
        tx = self._client.stats()
        lines.append(
            f"  Transmission: {'connected' if tx['connected'] else 'DISCONNECTED'}  "
            f"sent={tx['frames_sent']} frames  "
            f"{tx['mb_sent']} MB  "
            f"alerts={tx['alerts_sent']}"
        )

        # System resources
        if HAS_PSUTIL:
            cpu  = psutil.cpu_percent(interval=None)
            mem  = psutil.virtual_memory()
            lines.append(
                f"  System:      CPU={cpu:.0f}%  "
                f"RAM={mem.used//1024//1024}MB/{mem.total//1024//1024}MB  "
                f"({mem.percent:.0f}%)"
            )

        # Jetson GPU temperature
        if _JETSON:
            temp = _read_jetson_temp()
            if temp > 0:
                lines.append(f"  GPU Temp:    {temp:.1f}°C")

        # Recent alerts
        recent = al.get("recent_alerts", [])
        if recent:
            lines.append("  Recent alerts:")
            for a in recent[-3:]:
                t = time.strftime("%H:%M:%S", time.localtime(a["ts"]))
                lines.append(
                    f"    [{t}] {a['cam_name']}  "
                    f"{a['threat']}  {a['label'].upper()}  {a['conf']*100:.0f}%"
                )

        lines.append("─" * 60)
        log.info("\n".join(lines))