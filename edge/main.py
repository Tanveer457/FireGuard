#!/usr/bin/env python3
"""
main.py — FireGuard Edge Pipeline (Industry Edition)

Improvements vs original:
  - HTTP health endpoint (GET /health on --health-port, default 8765)
    Returns JSON: status, uptime, fps, alerts, server_connected, camera_states
  - Graceful Windows-compatible shutdown (SIGINT + SIGTERM + KeyboardInterrupt)
  - Startup banner with all config summary
  - Pipeline runs independently of server connection
  - Final stats printed on shutdown

HOW TO RUN:
  python main.py
  python main.py --config config.yaml
  python main.py --config config.yaml --health-port 8765
STOP:
  Ctrl+C
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import asyncio
import argparse
import signal
import logging
import time
import json
from aiohttp import web

from utils.config        import load as load_config
from utils.logger        import setup as setup_logging
from stream.manager      import StreamManager
from inference.detector  import Detector
from alert.manager       import AlertManager
from transmission.client import WSClient
from monitoring.health   import HealthMonitor

log = logging.getLogger("main")
_shutdown = False


# ── Signal Handling (cross-platform) ─────────────────────────────────────────
def _handle_signal(sig, frame):
    global _shutdown
    log.info("Shutdown signal received (sig=%s) — stopping gracefully...", sig)
    _shutdown = True


# SIGTERM always available; SIGBREAK on Windows (Ctrl+Break)
signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)
if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, _handle_signal)


# ── Health HTTP Server ────────────────────────────────────────────────────────
class HealthServer:
    """
    Lightweight aiohttp server exposing GET /health.
    Runs as an asyncio task alongside the main pipeline.
    """

    def __init__(self, port: int, streams, detector, alert_mgr, client):
        self._port      = port
        self._streams   = streams
        self._detector  = detector
        self._alert_mgr = alert_mgr
        self._client    = client
        self._start_ts  = time.time()
        self._runner    = None

    async def start(self):
        app = web.Application()
        app.router.add_get("/health", self._health_handler)
        app.router.add_get("/stats",  self._stats_handler)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        log.info("Health endpoint: http://0.0.0.0:%d/health", self._port)

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()

    async def _health_handler(self, request):
        payload = {
            "status":           "ok",
            "uptime_sec":       round(time.time() - self._start_ts),
            "server_connected": self._client.stats()["connected"],
            "cameras": {
                cam_id: {
                    "online":     info.get("online", False),
                    "fps":        round(info.get("fps", 0), 1),
                    "queue_size": info.get("queue_size", 0),
                }
                for cam_id, info in (self._streams.get_camera_states() or {}).items()
            },
        }
        return web.Response(
            text=json.dumps(payload, indent=2),
            content_type="application/json"
        )

    async def _stats_handler(self, request):
        payload = {
            "detector":     self._detector.stats(),
            "client":       self._client.stats(),
            "alerts":       self._alert_mgr.stats(),
        }
        return web.Response(
            text=json.dumps(payload, indent=2, default=str),
            content_type="application/json"
        )


# ── Inference Loop ────────────────────────────────────────────────────────────
async def inference_loop(streams, detector, alert_mgr,
                         stride: int, result_queue: asyncio.Queue,
                         state: dict):
    """
    Core loop — processes every Nth frame (stride), runs batch inference,
    checks alert conditions, pushes results to transmission queue.
    Only runs if state['enabled'] is True.
    """
    counter = 0
    log.info("Inference loop initialized (stride=%d)", stride)

    while not _shutdown:
        if not state.get("enabled", False):
            await asyncio.sleep(0.5)
            continue

        counter += 1
        if counter % stride != 0:
            await asyncio.sleep(0.001)
            continue

        raw_frames = streams.get_latest_frames()
        if not raw_frames:
            await asyncio.sleep(0.05)
            continue

        # Feed every frame to the clip pre-buffer
        for f in raw_frames:
            alert_mgr.feed_frame(f["cam_id"], f["frame"], f["ts"])

        frames    = [f["frame"] for f in raw_frames]
        # Run synchronous inference off the event loop (avoids blocking asyncio)
        batch_det = await asyncio.to_thread(detector.detect_batch, frames)

        for frame_data, detections in zip(raw_frames, batch_det):
            is_alert, clip_path = alert_mgr.check(
                frame_data["cam_id"],
                frame_data["name"],
                detections,
                frame_data["frame"],
            )
            # Non-blocking put (drop frames if queue full — inference never blocks)
            if not result_queue.full():
                await result_queue.put({
                    "cam_id":     frame_data["cam_id"],
                    "name":       frame_data["name"],
                    "frame":      frame_data["frame"],
                    "detections": detections,
                    "alert":      is_alert,
                    "clip_path":  clip_path,
                })

        await asyncio.sleep(0.001)


# ── Main ──────────────────────────────────────────────────────────────────────
async def main(config_path: str, health_port: int, server_url: str = None):
    global _shutdown

    cfg = load_config(config_path)
    
    # Override server URL if provided via command line
    if server_url:
        cfg["server"]["url"] = server_url
        log.info("Server URL overridden via command line: %s", server_url)

    setup_logging(
        log_file=cfg["monitoring"]["log_file"]
        if cfg["monitoring"]["log_to_file"] else None
    )

    # ── Pipeline State ────────────────────────────────────────────────────────
    # Shared state between loops and callbacks
    pipeline_state = {"enabled": False}

    # ── Startup Banner ────────────────────────────────────────────────────────
    log.info("=" * 64)
    log.info("  🔥  FIREGUARD EDGE PIPELINE  (Industry Edition)")
    log.info("=" * 64)
    log.info("  STATUS    : IDLE (Waiting for App command)")
    log.info("  Cameras   : %d", len(cfg["cameras"]))
    for cam in cfg["cameras"]:
        log.info("    ↳ [%d] %s  →  %s", cam["id"], cam["name"], cam["url"])
    log.info("  Model     : %s  (device=%s)", cfg["model"]["path"], cfg["model"]["device"])
    log.info("  Server    : %s", cfg["server"]["url"])
    log.info("  Health    : http://0.0.0.0:%d/health", health_port)
    log.info("=" * 64)

    # ── Component Init ────────────────────────────────────────────────────────
    streams   = StreamManager(cfg["cameras"], cfg["stream"])
    detector  = Detector(cfg["model"])
    alert_mgr = AlertManager(cfg["alert"], cfg["storage"])
    
    alert_mgr.run_startup_cleanup()

    def _on_reload_config(new_config: dict = None):
        try:
            import yaml
            if new_config:
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(new_config, f, default_flow_style=False, allow_unicode=True)
                log.info("📡  Configuration synced from server and saved to: %s", config_path)
            
            with open(config_path, encoding="utf-8") as f:
                refreshed_cfg = yaml.safe_load(f)
            
            # 1. Reload cameras
            new_cameras = refreshed_cfg.get("cameras", [])
            log.info("🔄 Reloading cameras: %d camera(s) active", len(new_cameras))
            streams.reload_cameras(new_cameras)

            # 2. Reload Detector settings (conf, iou, device)
            if "model" in refreshed_cfg:
                detector.update_config(refreshed_cfg["model"])
            
            # 3. Reload Alert settings (min_consecutive, cooldown)
            if "alert" in refreshed_cfg:
                alert_mgr.update_config(refreshed_cfg["alert"])

            # 4. Reload Transmission settings (jpeg_quality, interval)
            if "transmission" in refreshed_cfg:
                client.update_config(refreshed_cfg["transmission"])

        except Exception as e:
            log.error("Config reload/sync failed: %s", e)

    def _on_command(cmd: str):
        if cmd == "START":
            if pipeline_state.get("enabled"):
                log.info("🎮  Received START but pipeline is already active.")
                return
            log.warning("🎮  Pipeline ACTIVATED via remote command")
            pipeline_state["enabled"] = True
            streams.start()
        elif cmd == "STOP":
            if not pipeline_state.get("enabled"):
                return
            log.warning("🎮  Pipeline DEACTIVATED via remote command (IDLE)")
            pipeline_state["enabled"] = False
            streams.stop()

    client    = WSClient(
        url             = cfg["server"]["url"],
        token           = cfg["server"]["token"],
        interval_ms     = cfg["transmission"]["interval_ms"],
        jpeg_quality    = cfg["transmission"]["jpeg_quality"],
        streams         = streams,
        alert_mgr       = alert_mgr,
        on_reload_config = _on_reload_config,
        on_command       = _on_command,
    )
    
    # [Rest of components remain similar, health server uses client.stats()]
    health_monitor = HealthMonitor(
        interval_sec = cfg["monitoring"]["stats_interval_sec"],
        streams      = streams,
        detector     = detector,
        alert_mgr    = alert_mgr,
        client       = client,
    )
    health_server = HealthServer(
        port      = health_port,
        streams   = streams,
        detector  = detector,
        alert_mgr = alert_mgr,
        client    = client,
    )

    result_queue = asyncio.Queue(maxsize=20)

    # ── Start background components ───────────────────────────────────────────
    # Note: streams.start() is now deferred until 'START' command
    health_monitor.start()
    try:
        await health_server.start()
    except Exception as e:
        log.warning("Health server could not start: %s", e)

    # ── Run core tasks ────────────────────────────────────────────────────────
    try:
        await asyncio.gather(
            inference_loop(streams, detector, alert_mgr,
                           cfg["stream"]["vid_stride"], result_queue, pipeline_state),
            client.run(result_queue),
        )
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt received")
    finally:
        log.info("Shutting down...")
        streams.stop()
        health_monitor.stop()
        await health_server.stop()

        log.info("")
        log.info("=" * 64)
        log.info("  FINAL STATS")
        log.info("=" * 64)

        for section, stats_dict in [
            ("Detector",     detector.stats()),
            ("Transmission", client.stats()),
            ("Alerts",       alert_mgr.stats()),
        ]:
            log.info("  ── %s", section)
            for k, v in stats_dict.items():
                if k != "recent_alerts":
                    log.info("    %-26s: %s", k, v)

        log.info("=" * 64)
        log.info("Pipeline stopped cleanly.")


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FireGuard Edge Detection Pipeline"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--health-port",
        type=int,
        default=8765,
        dest="health_port",
        help="Port for the HTTP health endpoint (default: 8765)",
    )
    parser.add_argument(
        "--server-url",
        default=None,
        dest="server_url",
        help="WebSocket URL for the server (overrides config.yaml)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(args.config, args.health_port, args.server_url))
    except KeyboardInterrupt:
        pass