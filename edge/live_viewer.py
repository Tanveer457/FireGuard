"""
live_viewer.py  —  Live Viewer (temporary, before server is built)
──────────────────────────────────────────────────────────────────
Shows live camera feed with bounding boxes, threat levels,
confidence scores, FPS counter, and alert history.

HOW TO RUN:
    python live_viewer.py

STOP:
    Press Q in the video window
"""

import asyncio
import json
import threading
import time
import cv2
import numpy as np
import websockets
import logging
from collections import deque

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)s  %(message)s",
    datefmt = "%H:%M:%S",
)
log = logging.getLogger("viewer")

HOST  = "10.1.75.106"
PORT  = 8000
TOKEN = "fire-secret-token"

# ── Shared state ───────────────────────────────────────────────────────────────
latest_frames  = {}    # cam_id → annotated frame
alert_history  = deque(maxlen=10)
stats          = {"frames_received": 0, "alerts_received": 0, "connected": False}
_lock          = threading.Lock()

# ── Threat level config ────────────────────────────────────────────────────────
THREATS = {
    "CRITICAL": (0,   0,   255, ">=80%"),
    "HIGH":     (0,   80,  255, ">=60%"),
    "MEDIUM":   (0,   165, 255, ">=40%"),
    "LOW":      (0,   210, 255, ">=20%"),
    "SMOKE":    (160, 160, 160, "any  "),
}


def get_threat(label: str, conf: float):
    if label.lower() != "fire":
        return "SMOKE", THREATS["SMOKE"][:3]
    if conf >= 0.80: return "CRITICAL", THREATS["CRITICAL"][:3]
    if conf >= 0.60: return "HIGH",     THREATS["HIGH"][:3]
    if conf >= 0.40: return "MEDIUM",   THREATS["MEDIUM"][:3]
    return              "LOW",          THREATS["LOW"][:3]


def draw_frame(frame, detections, alert, cam_name, fps_rx):
    out  = frame.copy()
    h, w = out.shape[:2]

    # ── Bounding boxes ─────────────────────────────────────────────────────
    for det in detections:
        label           = det["label"]
        conf            = det["conf"]
        x1, y1, x2, y2 = det["x1"], det["y1"], det["x2"], det["y2"]
        threat, color   = get_threat(label, conf)

        # Transparent fill
        overlay = out.copy()
        cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
        cv2.addWeighted(overlay, 0.20, out, 0.80, 0, out)

        # Border — thicker for critical
        border = 3 if threat == "CRITICAL" else 2
        cv2.rectangle(out, (x1, y1), (x2, y2), color, border)

        # Label pill
        text  = f"{threat}  {label.upper()}  {conf*100:.0f}%"
        scale = 0.52
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        cv2.rectangle(out, (x1, y1 - th - 10), (x1 + tw + 8, y1), color, -1)
        cv2.putText(out, text, (x1 + 4, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2)

    # ── Top bar ────────────────────────────────────────────────────────────
    cv2.rectangle(out, (0, 0), (w, 34), (20, 20, 20), -1)
    cv2.putText(out, cam_name,
                (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.putText(out, f"RX: {fps_rx:.1f} fps",
                (w - 120, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1)

    # ── Alert banner ───────────────────────────────────────────────────────
    if alert:
        # Flashing red banner
        flash = int(time.time() * 3) % 2 == 0
        color = (0, 0, 220) if flash else (0, 0, 180)
        cv2.rectangle(out, (0, h - 44), (w, h), color, -1)
        cv2.putText(out, "  FIRE ALERT  —  ALERTING SERVER",
                    (10, h - 13), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)

    # ── Threat legend (top right) ──────────────────────────────────────────
    lx = w - 185
    cv2.rectangle(out, (lx - 4, 38), (w - 2, 38 + len(THREATS) * 22 + 6), (20, 20, 20), -1)
    for i, (name, vals) in enumerate(THREATS.items()):
        color_b = vals[:3]
        label   = vals[3]
        y = 56 + i * 22
        cv2.rectangle(out, (lx, y - 13), (w - 6, y + 5), color_b, -1)
        cv2.putText(out, f"{name:<9s} {label}",
                    (lx + 4, y), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1)

    return out


def draw_sidebar(height: int):
    """Draw alert history panel on the right."""
    sidebar = np.zeros((height, 240, 3), dtype=np.uint8)
    cv2.rectangle(sidebar, (0, 0), (240, height), (18, 18, 18), -1)

    # Title
    cv2.putText(sidebar, "ALERT HISTORY",
                (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 120, 0), 2)
    cv2.line(sidebar, (0, 32), (240, 32), (50, 50, 50), 1)

    with _lock:
        history = list(alert_history)[::-1]   # newest first
        s       = dict(stats)

    # Stats
    cv2.putText(sidebar, f"Frames : {s['frames_received']}",
                (10, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (140, 140, 140), 1)
    cv2.putText(sidebar, f"Alerts : {s['alerts_received']}",
                (10, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (140, 140, 140), 1)
    cv2.putText(sidebar,
                "Server : CONNECTED" if s["connected"] else "Server : waiting...",
                (10, 92),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                (0, 200, 80) if s["connected"] else (80, 80, 80), 1)

    cv2.line(sidebar, (0, 102), (240, 102), (50, 50, 50), 1)

    # Alert entries
    for i, entry in enumerate(history[:12]):
        y      = 122 + i * 52
        if y + 50 > height:
            break
        _, color = get_threat(entry["label"], entry["conf"])
        cv2.rectangle(sidebar, (6, y - 14), (234, y + 36), (30, 30, 30), -1)
        cv2.rectangle(sidebar, (6, y - 14), (8, y + 36), color, -1)   # color stripe
        cv2.putText(sidebar,
                    f"{entry['threat']}  {entry['conf']*100:.0f}%",
                    (14, y + 2), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
        cv2.putText(sidebar,
                    entry["cam_name"],
                    (14, y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180, 180, 180), 1)
        cv2.putText(sidebar,
                    time.strftime("%H:%M:%S", time.localtime(entry["ts"])),
                    (14, y + 32), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (100, 100, 100), 1)

    return sidebar


# ── WebSocket server ───────────────────────────────────────────────────────────
class _FPS:
    def __init__(self): self._t = deque(maxlen=30)
    def tick(self): self._t.append(time.time())
    @property
    def fps(self):
        if len(self._t) < 2: return 0.0
        d = self._t[-1] - self._t[0]
        return len(self._t) / d if d > 0 else 0.0


async def handle_edge(websocket):
    # Token auth — supports both websockets >= 14 and older
    try:
        auth = websocket.request.headers.get("Authorization", "")
    except AttributeError:
        auth = websocket.request_headers.get("Authorization", "")

    if auth.replace("Bearer ", "").strip() != TOKEN:
        log.warning("Rejected — wrong token")
        await websocket.close()
        return

    log.info("Edge device connected ✓")
    with _lock:
        stats["connected"] = True

    fps_counters = {}

    try:
        while True:
            meta_raw   = await websocket.recv()
            jpeg_bytes = await websocket.recv()

            meta       = json.loads(meta_raw)
            cam_id     = meta["cam_id"]
            cam_name   = meta["name"]
            detections = meta["detections"]
            alert      = meta["alert"]

            # Decode JPEG → numpy
            arr   = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                continue

            # Track receive FPS per camera
            if cam_id not in fps_counters:
                fps_counters[cam_id] = _FPS()
            fps_counters[cam_id].tick()

            # Annotate frame
            annotated = draw_frame(
                frame, detections, alert,
                cam_name, fps_counters[cam_id].fps
            )

            with _lock:
                latest_frames[cam_id] = annotated
                stats["frames_received"] += 1
                if alert:
                    stats["alerts_received"] += 1

            if alert:
                log.warning(f"FIRE ALERT — {cam_name}")
                best = max(detections, key=lambda d: d["conf"]) if detections else None
                if best:
                    with _lock:
                        alert_history.append({
                            "cam_name": cam_name,
                            "label":    best["label"],
                            "conf":     best["conf"],
                            "threat":   get_threat(best["label"], best["conf"])[0],
                            "ts":       time.time(),
                        })

            elif detections:
                for d in detections:
                    t, _ = get_threat(d["label"], d["conf"])
                    log.info(f"[{cam_name}]  {t:<9s}  {d['label'].upper():<6s}  {d['conf']*100:.0f}%")

    except Exception as e:
        log.info(f"Edge disconnected: {e}")
        with _lock:
            latest_frames.clear()
            stats["connected"] = False


async def start_ws():
    log.info(f"Listening on ws://0.0.0.0:{PORT}/ws/edge")
    async with websockets.serve(handle_edge, HOST, PORT):
        await asyncio.Future()


def run_ws():
    asyncio.run(start_ws())


# ── Display loop ───────────────────────────────────────────────────────────────
def display_loop():
    log.info("Window ready — press Q to quit")

    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(blank, "Waiting for edge device...",
                (80, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (60, 60, 60), 2)
    cv2.putText(blank, f"ws://0.0.0.0:{PORT}/ws/edge",
                (120, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (40, 40, 40), 1)

    while True:
        with _lock:
            frames = dict(latest_frames)

        # Build camera grid
        if not frames:
            grid = blank
        else:
            cam_frames = list(frames.values())
            if len(cam_frames) == 1:
                grid = cam_frames[0]
            elif len(cam_frames) == 2:
                h = min(f.shape[0] for f in cam_frames)
                grid = np.hstack([cv2.resize(f, (640, h)) for f in cam_frames])
            else:
                target  = (640, 480)
                resized = [cv2.resize(f, target) for f in cam_frames[:4]]
                while len(resized) < 4:
                    resized.append(np.zeros((480, 640, 3), dtype=np.uint8))
                grid = np.vstack([np.hstack(resized[:2]),
                                  np.hstack(resized[2:])])

        # Attach sidebar
        sidebar = draw_sidebar(grid.shape[0])
        display = np.hstack([grid, sidebar])

        cv2.imshow("Fire Detection - Live View", display)
        if cv2.waitKey(30) & 0xFF == ord('q'):
            break

    cv2.destroyAllWindows()


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  Fire Detection - Live Viewer")
    print("=" * 55)
    print(f"  Token  : {TOKEN}")
    print(f"  Port   : {PORT}")
    print(f"\n  In edge/config.yaml set:")
    print(f"    server.url   : ws://YOUR_PC_IP:{PORT}/ws/edge")
    print(f"    server.token : {TOKEN}")
    print("=" * 55 + "\n")

    threading.Thread(target=run_ws, daemon=True).start()
    display_loop()