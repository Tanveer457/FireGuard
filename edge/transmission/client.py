"""
transmission/client.py — FireGuard Edge WebSocket Client (Industry Edition)
Improvements:
  - Heartbeat ping every 15s to keep connection alive
  - Dedicated receiver loop for instant server commands (START/STOP/CONFIG)
  - Single-Packet Transmission: metadata + jpeg in one binary message (fixes server sync issues)
  - Latency tracking (round-trip ping → pong)
  - Exponential backoff with jitter on reconnect
  - Full stats: frames_sent, alerts_sent, bytes_sent, avg_latency_ms, uptime_sec
"""

import asyncio
import json
import time
import random
import cv2
import websockets
import logging
import struct
from typing import Callable, Optional
from pathlib import Path

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

log = logging.getLogger("client")

_HEARTBEAT_INTERVAL = 15.0
_MAX_RETRY_DELAY    = 10

# Jetson GPU load paths
_GPU_LOAD_PATHS = [
    "/sys/devices/gpu.0/load",
    "/sys/class/devfreq/17000000.gp10b/device/gpu.0/load",
    "/sys/class/devfreq/gpgpu/device/gpu.0/load",
]

def _get_gpu_util() -> float:
    for p in _GPU_LOAD_PATHS:
        try:
            path = Path(p)
            if path.exists():
                val = float(path.read_text().strip())
                return min(100.0, val / 10.0)
        except Exception:
            continue
    return 0.0


class WSClient:
    def __init__(self, url: str, token: str,
                 interval_ms: int, jpeg_quality: int,
                 streams=None,
                 alert_mgr=None,
                 on_reload_config: Optional[Callable] = None,
                 on_command: Optional[Callable[[str], None]] = None):
        self.url          = url.strip()
        self.token        = token.strip()
        self.interval_sec = interval_ms / 1000.0
        self.jpeg_quality = jpeg_quality
        self.streams      = streams
        self.alert_mgr    = alert_mgr
        self._on_reload_config = on_reload_config
        self._on_command       = on_command

        self._ws            = None
        self._last_sent     = {}
        self._retry_delay   = 3.0
        self._connected     = False
        self._start_time    = time.time()

        self.frames_sent    = 0
        self.alerts_sent    = 0
        self.bytes_sent     = 0
        self._latency_samples: list[float] = []

    def update_config(self, trans_cfg: dict):
        """Update transmission settings on the fly."""
        new_ms = trans_cfg.get("interval_ms")
        if new_ms is not None:
            self.interval_sec = new_ms / 1000.0
        self.jpeg_quality = trans_cfg.get("jpeg_quality", self.jpeg_quality)
        log.info(f"WSClient updated: interval={self.interval_sec}s, quality={self.jpeg_quality}")

    async def _connect(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        conn_args = {
            "ping_interval": None,
            "close_timeout": 5,
            "open_timeout":  10,
        }
        try:
            self._ws = await websockets.connect(self.url, additional_headers=headers, **conn_args)
        except TypeError:
            self._ws = await websockets.connect(self.url, extra_headers=headers, **conn_args)

        self._connected   = True
        self._retry_delay = 3.0
        log.info("✓  Connected to server  (%s)", self.url)

    async def _heartbeat_loop(self):
        while self._connected and self._ws:
            try:
                payload = {"type": "heartbeat", "ts": time.time()}
                if HAS_PSUTIL:
                    payload["cpu"] = psutil.cpu_percent(interval=None)
                    payload["ram"] = psutil.virtual_memory().percent
                payload["gpu"] = _get_gpu_util()
                
                if self.streams:
                    payload["cameras"] = self.streams.get_camera_states()

                await self._ws.send(json.dumps(payload))
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
            except Exception as e:
                log.warning("Heartbeat send failed: %s", e)
                self._connected = False
                break

    async def _receiver_loop(self):
        while self._connected and self._ws:
            try:
                raw = await self._ws.recv()
                # If we receive binary here, it's likely a misrouted frame or pong
                if isinstance(raw, bytes): continue
                
                msg = json.loads(raw)
                mtype = msg.get("type")

                if mtype == "pong":
                    rtt = (time.time() - msg.get("ts", time.time())) * 1000
                    self._latency_samples.append(rtt)
                    if len(self._latency_samples) > 20: self._latency_samples.pop(0)
                
                elif mtype == "reload_config":
                    if self._on_reload_config: self._on_reload_config(None)
                
                elif mtype == "config_update":
                    if self._on_reload_config: self._on_reload_config(msg.get("config"))
                
                elif mtype == "command":
                    if self._on_command: self._on_command(msg.get("cmd"))
                
                elif mtype == "alert_confirmed":
                    if self.alert_mgr:
                        self.alert_mgr.register_server_confirmation(msg["alert_id"], msg["upload_url"])

            except Exception as e:
                if self._connected: log.warning("Receiver loop error: %s", e)
                self._connected = False
                break

    async def _send_item(self, item: dict):
        """Combined Packet: JSON Metadata Length (4 bytes) + JSON Metadata + JPEG Bytes."""
        cam_id   = item["cam_id"]
        now      = time.time()
        is_alert = bool(item.get("alert"))

        if not is_alert and now - self._last_sent.get(cam_id, 0) < self.interval_sec:
            return

        try:
            frame_to_send = item["frame"].copy()
            detections    = item.get("detections", [])
            
            # Professional drawing
            for det in detections:
                if det.label.lower() == "smoke" and det.threat_level == "LOW": continue
                
                label_lower = det.label.lower()
                threat      = det.threat_level
                
                # BGR colors
                if label_lower == "smoke":
                    color = (255, 120, 0)     # Industrial Blue
                elif threat == "CRITICAL":
                    color = (0, 0, 255)       # Pure Red
                elif threat == "HIGH":
                    color = (0, 69, 255)      # Orange-Red
                elif threat == "MEDIUM":
                    color = (0, 165, 255)     # Orange
                else: 
                    color = (0, 255, 255)     # Yellow

                # 1. Draw Rect
                cv2.rectangle(frame_to_send, (det.x1, det.y1), (det.x2, det.y2), color, 2)

                # 2. Draw Pill Label
                txt = f"{det.label.upper()} {det.confidence*100:.0f}%"
                font = cv2.FONT_HERSHEY_SIMPLEX
                (tw, th), base = cv2.getTextSize(txt, font, 0.45, 1)
                
                # Ensure label stays inside frame
                lx = max(0, det.x1)
                ly = max(0, det.y1 - th - 12)
                
                # Check if ly went off-screen top, if so, move pill inside box
                if ly < 5:
                    ly = det.y1 + 5

                # Background pill
                cv2.rectangle(frame_to_send, (lx, ly), (min(frame_to_send.shape[1], lx + tw + 10), ly + th + 8), color, -1)
                # White text
                cv2.putText(frame_to_send, txt, (lx + 5, ly + th + 4), font, 0.45, (255,255,255), 1, cv2.LINE_AA)

            _, jpeg = cv2.imencode(".jpg", frame_to_send, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
            jpeg_bytes = jpeg.tobytes()

            metadata = json.dumps({
                "cam_id":     cam_id,
                "name":       item["name"],
                "ts":         now,
                "detections": [d.to_dict() for d in detections],
                "alert":      is_alert,
                "clip_path":  item.get("clip_path"),
            }).encode("utf-8")

            # PACKET: [4 bytes header for json len] + [json] + [jpeg]
            header = struct.pack("!I", len(metadata))
            packet = header + metadata + jpeg_bytes

            await self._ws.send(packet)

            self._last_sent[cam_id] = now
            self.frames_sent       += 1
            self.bytes_sent        += len(packet)
            if is_alert: self.alerts_sent += 1
        except Exception as e:
            log.warning("Send packet failed: %s", e)
            self._connected = False
            raise

    async def run(self, queue: asyncio.Queue):
        while True:
            try:
                await self._connect()
                hb_task   = asyncio.create_task(self._heartbeat_loop())
                recv_task = asyncio.create_task(self._receiver_loop())
                
                while self._connected:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=0.5)
                        await self._send_item(item)
                        queue.task_done()
                    except asyncio.TimeoutError:
                        continue
                
                hb_task.cancel()
                recv_task.cancel()
            except Exception as e:
                log.warning("Connection lost (%s) — retrying in %.1fs", e, self._retry_delay)
            
            self._connected = False
            self._ws = None
            await asyncio.sleep(self._retry_delay)
            self._retry_delay = min(self._retry_delay * 1.5, _MAX_RETRY_DELAY)

    def stats(self) -> dict:
        avg_lat = sum(self._latency_samples)/len(self._latency_samples) if self._latency_samples else 0
        return {
            "connected": self._connected,
            "frames_sent": self.frames_sent,
            "alerts_sent": self.alerts_sent,
            "mb_sent": round(self.bytes_sent / 1e6, 2),
            "avg_latency_ms": round(avg_lat, 1),
            "uptime_sec": round(time.time() - self._start_time),
        }
