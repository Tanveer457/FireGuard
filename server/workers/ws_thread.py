"""
ws_thread.py — FireGuard FastAPI Server (Industry Edition)
Runs in a QThread. Provides:
  - WebSocket endpoint:  /ws/edge          (edge devices stream frames)
  - Health:              GET /health
  - Stats:               GET /api/stats
  - Cameras:             GET /api/cameras
  - Alerts:              GET /api/alerts    (paginated + filtered)
  - Alert detail:        GET /api/alerts/{id}
  - Acknowledge:         POST /api/alerts/{id}/acknowledge
  - Acknowledge all:     POST /api/alerts/acknowledge-all
  - Analytics:           GET /api/analytics/hourly
                         GET /api/analytics/daily
                         GET /api/analytics/distribution
  - Settings:            GET  /api/settings
                         POST /api/settings
  - OpenAPI docs:        GET /docs
"""

import json
import logging
import asyncio
import time
import os
import shutil
import struct
from typing import Optional, Dict, Any
from pathlib import Path

import uvicorn
from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    HTTPException, Query, status, Depends,
    File, UploadFile
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


# ── Pydantic Schemas ──────────────────────────────────────────────────────────
class Detection(BaseModel):
    label: str
    conf: float
    x1: float = 0
    y1: float = 0
    x2: float = 0
    y2: float = 0


class FrameMetadata(BaseModel):
    cam_id: int
    name: str
    ts: float
    detections: list[Detection]
    alert: bool
    clip_path: Optional[str] = None


class AcknowledgeRequest(BaseModel):
    notes: Optional[str] = ""
    acknowledged_by: Optional[str] = "operator"


class AcknowledgeAllRequest(BaseModel):
    cam_id: Optional[int] = None


class SettingsPayload(BaseModel):
    key: str
    value: str


# ── Connection Manager ────────────────────────────────────────────────────────
class ConnectionManager:
    """Tracks all active edge WebSocket connections."""

    def __init__(self):
        self.active: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        async with self._lock:
            self.active.add(ws)

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self.active.discard(ws)

    async def broadcast(self, message: str):
        """Send a message to all connected edge clients."""
        async with self._lock:
            for ws in list(self.active):
                try:
                    await ws.send_text(message)
                except Exception as e:
                    logger.warning("Failed to broadcast: %s", e)

    @property
    def connected_count(self) -> int:
        return len(self.active)


# ── Qt Worker Thread ──────────────────────────────────────────────────────────
class WSServerThread(QThread):
    frame_received    = Signal(dict, bytes)
    alert_confirmed   = Signal(dict, bytes)
    camera_connected  = Signal(int, str)
    camera_offline    = Signal(int)
    edge_connected    = Signal(int)   # emits connected_count
    edge_disconnected = Signal(int)   # emits connected_count
    edge_health       = Signal(int, float, float, float)  # count, cpu, ram, gpu

    def __init__(self, host: str = "0.0.0.0", port: int = 8000,
                 token: str = "fire-secret-token", db=None, storage_dir: str = None):
        super().__init__()
        self.host         = host
        self.port         = port
        self.token        = token
        self.db           = db
        self.storage_dir  = storage_dir
        self.should_exit  = False
        self.server       = None
        self._start_time  = time.time()
        self._manager     = ConnectionManager()
        self._stats_cache = {}
        self._loop        = None   # asyncio loop reference for cross-thread calls
        self.app          = self._build_app()

    # ── Build FastAPI App ─────────────────────────────────────────────────────
    def _build_app(self) -> FastAPI:
        app = FastAPI(
            title="FireGuard API",
            description="Real-time AI Fire & Smoke Detection System API",
            version="2.0.0",
            docs_url="/docs",
            redoc_url="/redoc",
        )

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # ── Static Storage ───────────────────────────────────────────────────
        if self.storage_dir:
            app.mount("/storage", StaticFiles(directory=self.storage_dir), name="storage")

        # ── Health ────────────────────────────────────────────────────────────
        @app.get("/health", tags=["System"])
        async def health():
            return {
                "status": "ok",
                "uptime_sec": round(time.time() - self._start_time),
                "edge_connections": self._manager.connected_count,
                "timestamp": time.time(),
            }

        # ── Stats ─────────────────────────────────────────────────────────────
        @app.get("/api/stats", tags=["Dashboard"])
        async def get_stats():
            if self.db:
                return self.db.get_stats()
            return {"error": "db not available"}

        # ── Cameras ───────────────────────────────────────────────────────────
        @app.get("/api/cameras", tags=["Cameras"])
        async def get_cameras():
            if not self.db:
                raise HTTPException(503, "Database unavailable")
            return self.db.get_cameras()

        @app.get("/api/cameras/{cam_id}", tags=["Cameras"])
        async def get_camera(cam_id: int):
            if not self.db:
                raise HTTPException(503, "Database unavailable")
            cam = self.db.get_camera(cam_id)
            if not cam:
                raise HTTPException(404, f"Camera {cam_id} not found")
            return cam

        # ── Alerts ────────────────────────────────────────────────────────────
        @app.get("/api/alerts", tags=["Alerts"])
        async def get_alerts(
            page:          int            = Query(1, ge=1),
            page_size:     int            = Query(50, ge=1, le=500),
            cam_id:        Optional[int]  = None,
            threat_level:  Optional[str]  = None,
            acknowledged:  Optional[bool] = None,
            date_from:     Optional[str]  = None,
            date_to:       Optional[str]  = None,
            search:        Optional[str]  = None,
        ):
            if not self.db:
                raise HTTPException(503, "Database unavailable")
            offset = (page - 1) * page_size
            alerts = self.db.get_alerts(
                limit=page_size, offset=offset,
                cam_id=cam_id, threat_level=threat_level,
                acknowledged=acknowledged, date_from=date_from,
                date_to=date_to, search=search,
            )
            total = self.db.count_alerts(cam_id=cam_id, threat_level=threat_level,
                                          acknowledged=acknowledged, date_from=date_from,
                                          date_to=date_to)
            return {
                "page":      page,
                "page_size": page_size,
                "total":     total,
                "pages":     max(1, (total + page_size - 1) // page_size),
                "alerts":    alerts,
            }

        @app.get("/api/alerts/{alert_id}", tags=["Alerts"])
        async def get_alert(alert_id: int):
            if not self.db:
                raise HTTPException(503, "Database unavailable")
            alert = self.db.get_alert_by_id(alert_id)
            if not alert:
                raise HTTPException(404, f"Alert {alert_id} not found")
            alert["detections"] = self.db.get_alert_detections(alert_id)
            return alert

        @app.post("/api/alerts/{alert_id}/acknowledge", tags=["Alerts"])
        async def acknowledge_alert(alert_id: int, req: AcknowledgeRequest):
            if not self.db:
                raise HTTPException(503, "Database unavailable")
            ok = self.db.acknowledge_alert(alert_id, req.notes, req.acknowledged_by)
            if not ok:
                raise HTTPException(404, f"Alert {alert_id} not found")
            return {"success": True, "alert_id": alert_id}

        @app.post("/api/alerts/acknowledge-all", tags=["Alerts"])
        async def acknowledge_all(req: AcknowledgeAllRequest):
            if not self.db:
                raise HTTPException(503, "Database unavailable")
            self.db.acknowledge_all(cam_id=req.cam_id)
            return {"success": True}

        @app.post("/api/alerts/{alert_id}/snapshot", tags=["Alerts"])
        async def upload_snapshot(alert_id: int, file: UploadFile = File(...)):
            if not self.db: raise HTTPException(503, "Database unavailable")
            if not self.storage_dir: raise HTTPException(500, "Storage not configured")
            
            # Delete old snapshot if it exists to prevent orphaned files
            alert = self.db.get_alert_by_id(alert_id)
            if alert and alert.get("snapshot_path"):
                old_path = Path(self.storage_dir) / alert["snapshot_path"]
                if old_path.exists():
                    try:
                        old_path.unlink(missing_ok=True)
                    except Exception as e:
                        logger.warning(f"Failed to delete old snapshot: {e}")

            # Save file locally on server
            filename = f"alert_{alert_id}_snap_{int(time.time())}.jpg"
            dest_path = Path(self.storage_dir) / "snapshots" / filename
            with open(dest_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            # Update DB with NEW server-side relative path
            relative_path = f"snapshots/{filename}"
            self.db.update_alert_media(alert_id, snapshot_path=relative_path)
            return {"success": True, "path": relative_path}

        @app.post("/api/alerts/{alert_id}/clip", tags=["Alerts"])
        async def upload_clip(alert_id: int, file: UploadFile = File(...)):
            if not self.db: raise HTTPException(503, "Database unavailable")
            if not self.storage_dir: raise HTTPException(500, "Storage not configured")
            
            # Save file locally on server
            filename = f"alert_{alert_id}_clip_{int(time.time())}.mp4"
            dest_path = Path(self.storage_dir) / "clips" / filename
            with open(dest_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            # Update DB
            relative_path = f"clips/{filename}"
            self.db.update_alert_media(alert_id, clip_path=relative_path)
            return {"success": True, "path": relative_path}

        # ── Analytics ─────────────────────────────────────────────────────────
        @app.get("/api/analytics/hourly", tags=["Analytics"])
        async def analytics_hourly(days: int = Query(1, ge=1, le=30)):
            if not self.db:
                raise HTTPException(503, "Database unavailable")
            return self.db.get_hourly_chart(days=days)

        @app.get("/api/analytics/daily", tags=["Analytics"])
        async def analytics_daily(days: int = Query(30, ge=1, le=365)):
            if not self.db:
                raise HTTPException(503, "Database unavailable")
            return self.db.get_daily_chart(days=days)

        @app.get("/api/analytics/distribution", tags=["Analytics"])
        async def analytics_distribution():
            if not self.db:
                raise HTTPException(503, "Database unavailable")
            return self.db.get_threat_distribution()

        @app.get("/api/analytics/cameras", tags=["Analytics"])
        async def analytics_cameras():
            if not self.db:
                raise HTTPException(503, "Database unavailable")
            return self.db.get_camera_alert_stats()

        # ── Settings ──────────────────────────────────────────────────────────
        @app.get("/api/settings", tags=["Settings"])
        async def get_settings():
            if not self.db:
                raise HTTPException(503, "Database unavailable")
            return self.db.get_all_config()

        @app.post("/api/settings", tags=["Settings"])
        async def update_setting(payload: SettingsPayload):
            if not self.db:
                raise HTTPException(503, "Database unavailable")
            self.db.set_config(payload.key, payload.value)
            return {"success": True, "key": payload.key, "value": payload.value}

        # ── WebSocket: Edge Devices ───────────────────────────────────────────
        @app.websocket("/ws/edge")
        async def edge_ws_handler(websocket: WebSocket):
            auth_header = websocket.headers.get("authorization", "")
            if auth_header != f"Bearer {self.token}":
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                logger.warning("WS connection rejected: bad token")
                return

            await websocket.accept()
            
            # Smart IP Detection:
            # Use the 'Host' header from the connection to find the PC's real IP.
            # This ensures the Jetson knows exactly where to upload photos.
            host_header = websocket.headers.get("host", "127.0.0.1")
            server_ip = host_header.split(":")[0]
            
            if server_ip == "0.0.0.0": 
                server_ip = "127.0.0.1"
            
            self._current_server_ip = server_ip
            logger.info(f"Edge connected. Jetson is using server IP: {server_ip}")

            await self._manager.connect(websocket)
            self.edge_connected.emit(self._manager.connected_count)
            known_cams = set()
            active_cams = set()

            try:
                while not self.should_exit:
                    try:
                        # 1. Receive generic message (could be Text or Binary)
                        raw = await asyncio.wait_for(
                            websocket.receive(), timeout=2.0
                        )

                        if raw.get("type") == "websocket.disconnect":
                            break
                        
                        # --- CASE 1: HEARTBEAT (TEXT) ---
                        if raw.get("text"):
                            msg = json.loads(raw["text"])
                            if msg.get("type") == "heartbeat":
                                await websocket.send_text(
                                    json.dumps({"type": "pong", "ts": time.time()})
                                )
                                if "cpu" in msg and "ram" in msg:
                                    gpu = float(msg.get("gpu", 0))
                                    self.edge_health.emit(
                                        self._manager.connected_count,
                                        float(msg["cpu"]), float(msg["ram"]), gpu
                                    )
                                
                                # Process camera states from heartbeat
                                remote_cams = msg.get("cameras", {})
                                for c_id_str, info in remote_cams.items():
                                    try:
                                        c_id = int(c_id_str)
                                        is_online = info.get("online", False)
                                        if is_online:
                                            if c_id not in active_cams:
                                                active_cams.add(c_id)
                                                known_cams.add(c_id)
                                                self.camera_connected.emit(c_id, info.get("name", f"Camera {c_id}"))
                                        else:
                                            if c_id in active_cams:
                                                active_cams.remove(c_id)
                                                if c_id in known_cams: known_cams.remove(c_id)
                                                self.camera_offline.emit(c_id)
                                    except Exception:
                                        continue
                            continue

                        # --- CASE 2: CAMERA FRAME (BINARY PACKET) ---
                        if raw.get("bytes"):
                            data = raw["bytes"]
                            if len(data) < 4: continue
                            
                            # Header: 4 bytes for JSON length
                            json_len = struct.unpack("!I", data[:4])[0]
                            
                            # Payload: JSON Metadata
                            meta_json = data[4:4+json_len].decode("utf-8")
                            msg = json.loads(meta_json)
                            
                            # Body: Remaining bytes are JPEG
                            jpeg_bytes = data[4+json_len:]

                            meta   = FrameMetadata(**msg)
                            cam_id = meta.cam_id

                            # Emit connected event once per camera per session
                            if cam_id not in known_cams:
                                known_cams.add(cam_id)
                                active_cams.add(cam_id)
                                self.camera_connected.emit(cam_id, meta.name)

                            # Compatibility helper for Pydantic v1/v2
                            if hasattr(meta, "model_dump"):
                                meta_dict = meta.model_dump()
                            else:
                                meta_dict = meta.dict()

                            self.frame_received.emit(meta_dict, jpeg_bytes)

                            if meta.alert:
                                self.alert_confirmed.emit(meta_dict, jpeg_bytes)

                    except asyncio.TimeoutError:
                        continue   # no data — just loop

            except WebSocketDisconnect:
                logger.info("Edge connection closed")
            except Exception as e:
                logger.error("Edge WS error: %s", e)
            finally:
                await self._manager.disconnect(websocket)
                self.edge_disconnected.emit(self._manager.connected_count)
                for c_id in active_cams:
                    self.camera_offline.emit(c_id)

        return app

    # ── QThread Run ───────────────────────────────────────────────────────────
    def run(self):
        import socket
        logger.info(f"Starting FastAPI server on {self.host}:{self.port}")
        
        # Pre-flight check: Is the port already in use?
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((self.host, self.port))
            except socket.error as e:
                logger.error(f"CRITICAL: Cannot bind to {self.host}:{self.port}. Port may be in use: {e}")
                return

        try:
            config = uvicorn.Config(
                self.app,
                host=self.host,
                port=self.port,
                log_level="info",
                access_log=False,
                # Use None to prevent uvicorn from trying to configure logging,
                # which fails in PyInstaller/Nuitka bundles.
                log_config=None, 
            )
            self.server = uvicorn.Server(config)
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            logger.info("FastAPI Event Loop starting...")
            self._loop.run_until_complete(self.server.serve())
        except Exception as e:
            logger.error(f"CRITICAL: FastAPI server failed to start: {e}")

    def stop(self):
        self.should_exit = True
        if self.server:
            self.server.should_exit = True
        self.wait(3000)

    def notify_edge_reload(self):
        """Send the full current configuration to all connected edge clients.
        Called from the main Qt thread after camera add/edit/delete.
        This ensures the Jetson (Edge) stays in sync with the Server UI.
        """
        from server.utils.config_sync import load_edge_config
        try:
            config_dict = load_edge_config()
            self.sync_edge_config(config_dict)
        except Exception as e:
            logger.error(f"Failed to push config to edge: {e}")

    def sync_edge_config(self, config_dict: dict):
        """Send the full configuration dictionary to all connected edge clients."""
        import json as _json
        msg = _json.dumps({
            "type": "config_update",
            "config": config_dict
        })
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._manager.broadcast(msg), self._loop
            )
            logger.info("Sent config_update to %d edge client(s)",
                        self._manager.connected_count)

    def send_alert_confirmation(self, alert_id: int):
        """Notify edge that alert was accepted and provide its full upload URL."""
        import json as _json
        # Use the IP detected when the Edge first connected
        host = getattr(self, "_current_server_ip", "127.0.0.1")
        # Send ONLY the base URL; the edge will append the specific endpoint paths
        base_url = f"http://{host}:{self.port}"
        
        msg = _json.dumps({
            "type": "alert_confirmed",
            "alert_id": alert_id,
            "upload_url": base_url
        })
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._manager.broadcast(msg), self._loop
            )

    def send_command(self, cmd_name: str):
        """Send a control command (START/STOP) to all connected edges."""
        import json as _json
        msg = _json.dumps({
            "type": "command",
            "cmd": cmd_name
        })
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._manager.broadcast(msg), self._loop
            )
            logger.info("Sent command '%s' to edges", cmd_name)
