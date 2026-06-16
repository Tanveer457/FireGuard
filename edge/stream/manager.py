# stream/manager.py
import os
# Force RTSP to use TCP and set a 5-second timeout (in microseconds)
# This MUST be set before cv2 is imported to ensure the FFmpeg backend picks it up.
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;5000000"

import cv2
import threading
import time
import logging
from collections import deque

log = logging.getLogger("stream")


class FPSCounter:
    """Calculates rolling average FPS over last 30 frames."""
    def __init__(self):
        self._times = deque(maxlen=30)

    def tick(self):
        self._times.append(time.time())

    @property
    def fps(self) -> float:
        if len(self._times) < 2:
            return 0.0
        elapsed = self._times[-1] - self._times[0]
        return round(len(self._times) / elapsed, 1) if elapsed > 0 else 0.0


class Camera:
    def __init__(self, cam_id, url, name, buffer_size, reconnect_sec, stale_sec):
        self.cam_id       = cam_id
        self.name         = name
        self.reconnect_sec= reconnect_sec
        self.stale_sec    = stale_sec
        self.fps_counter  = FPSCounter()
        self.connected    = False
        self.total_frames = 0

        # Parse URL — keep int for webcam, string for RTSP/file
        try:
            self.url = int(url)
        except (ValueError, TypeError):
            self.url = str(url)

        self._buffer   = deque(maxlen=buffer_size)
        self._lock     = threading.Lock()
        self._running  = False
        self._last_frame_ts = 0

    def start(self):
        self._running = True
        threading.Thread(target=self._read_loop, daemon=True, name=f"cam-{self.cam_id}").start()
        log.info(f"[{self.name}] started")

    def stop(self):
        self._running = False

    def latest_frame(self):
        """Returns most recent valid frame dict, or None."""
        with self._lock:
            if not self._buffer:
                return None
            # Stale check — mark offline if no frame for stale_sec
            if time.time() - self._last_frame_ts > self.stale_sec and self.connected:
                log.warning(f"[{self.name}] stale — no new frame for {self.stale_sec}s")
                self.connected = False
            return self._buffer[-1]

    @property
    def fps(self) -> float:
        return self.fps_counter.fps

    def _is_valid_frame(self, frame) -> bool:
        """Skip completely black or corrupt frames."""
        if frame is None or frame.size == 0:
            return False
        # Check if frame is too dark (mean brightness < 5)
        if frame.mean() < 5:
            return False
        return True

    def _read_loop(self):
        while self._running:
            cap = cv2.VideoCapture(self.url)

            if not cap.isOpened():
                log.warning(f"[{self.name}] Cannot connect — retry in {self.reconnect_sec}s")
                self.connected = False
                time.sleep(self.reconnect_sec)
                continue

            self.connected = True
            log.info(f"[{self.name}] connected ✓  ({self.url})")

            while self._running:
                ok, frame = cap.read()

                if not ok:
                    log.warning(f"[{self.name}] stream lost — reconnecting")
                    self.connected = False
                    break

                if not self._is_valid_frame(frame):
                    continue

                self.fps_counter.tick()
                self.total_frames += 1
                now = time.time()

                with self._lock:
                    self._last_frame_ts = now
                    self._buffer.append({
                        "cam_id": self.cam_id,
                        "name":   self.name,
                        "frame":  frame,
                        "ts":     now,
                    })

            cap.release()


class StreamManager:
    def __init__(self, cameras_cfg, stream_cfg):
        self._stream_cfg = stream_cfg
        buf         = stream_cfg.get("buffer_size", 10)
        reconnect   = stream_cfg.get("reconnect_sec", 5)
        stale       = stream_cfg.get("stale_frame_sec", 5)

        self._cameras = {
            c["id"]: Camera(
                cam_id       = c["id"],
                url          = c["url"],
                name         = c["name"],
                buffer_size  = buf,
                reconnect_sec= reconnect,
                stale_sec    = stale,
            )
            for c in cameras_cfg
        }

    def start(self):
        for cam in self._cameras.values():
            cam.start()
        log.info(f"Started {len(self._cameras)} camera stream(s)")

    def stop(self):
        for cam in self._cameras.values():
            cam.stop()
        log.info("All streams stopped")

    def get_latest_frames(self) -> list:
        return [
            f for cam in self._cameras.values()
            if (f := cam.latest_frame()) is not None
        ]

    def status(self) -> dict:
        return {
            cam.name: {
                "connected":    cam.connected,
                "fps":          cam.fps,
                "total_frames": cam.total_frames,
            }
            for cam in self._cameras.values()
        }

    def get_camera_states(self) -> dict:
        """Returns dict keyed by cam_id for health endpoint."""
        return {
            cam.cam_id: {
                "online":     cam.connected,
                "fps":        cam.fps,
                "name":       cam.name,
                "queue_size": len(cam._buffer),
            }
            for cam in self._cameras.values()
        }

    # ── Hot-Reload Methods ────────────────────────────────────────────────────
    def reload_cameras(self, cameras_cfg: list):
        """Hot-reload cameras from a new config. Stops removed cameras,
        starts new ones, and updates existing ones if the URL changed."""
        buf       = self._stream_cfg.get("buffer_size", 10)
        reconnect = self._stream_cfg.get("reconnect_sec", 5)
        stale     = self._stream_cfg.get("stale_frame_sec", 5)

        new_ids = {c["id"] for c in cameras_cfg}
        old_ids = set(self._cameras.keys())

        # Stop and remove cameras that are no longer in config
        for cam_id in old_ids - new_ids:
            cam = self._cameras.pop(cam_id)
            cam.stop()
            log.info(f"[{cam.name}] removed (hot-reload)")

        # Add new cameras or update existing
        for c in cameras_cfg:
            cam_id = c["id"]
            if cam_id not in self._cameras:
                # New camera — create and start
                cam = Camera(
                    cam_id        = cam_id,
                    url           = c["url"],
                    name          = c["name"],
                    buffer_size   = buf,
                    reconnect_sec = reconnect,
                    stale_sec     = stale,
                )
                self._cameras[cam_id] = cam
                cam.start()
                log.info(f"[{cam.name}] added (hot-reload)")
            else:
                # Existing camera — update name and check if URL changed
                existing = self._cameras[cam_id]
                existing.name = c["name"]
                
                # If URL changed, we must restart the camera
                new_url = c["url"]
                try:
                    new_url = int(new_url)
                except (ValueError, TypeError):
                    new_url = str(new_url)
                    
                if existing.url != new_url:
                    log.info(f"[{existing.name}] URL changed ({existing.url} -> {new_url}) — restarting")
                    existing.stop()
                    existing.url = new_url
                    existing.start()

        log.info("Hot-reload complete: %d camera(s) active", len(self._cameras))