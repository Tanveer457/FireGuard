"""
sqlite_db.py — FireGuard Complete Database Layer (Industry Edition)
Features:
  - WAL mode + NORMAL sync for high-performance concurrent writes
  - Full alert management: create, filter, acknowledge, export CSV
  - Analytics queries: hourly/daily/weekly trend data
  - Camera stats: per-camera alert counts, status history
  - Retention cleanup: delete alerts older than N days
  - Thread-safe connection per call (SQLite best practice)
"""

import sqlite3
import csv
import os
import logging
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str = "fire.db"):
        self.db_path = db_path
        self._init_db()

    # ── Connection ──────────────────────────────────────────────────────────
    def _get_connection(self) -> sqlite3.Connection:
        # Increased timeout to 30s to handle contention in multi-threaded/bundled env
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA cache_size=-32000;")   # 32MB cache
        return conn

    # ── Schema Init ─────────────────────────────────────────────────────────
    def _init_db(self):
        with closing(self._get_connection()) as conn:
            cursor = conn.cursor()

            # Cameras table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cameras (
                    id           INTEGER PRIMARY KEY,
                    name         TEXT NOT NULL,
                    source       TEXT NOT NULL DEFAULT '0',
                    is_online    INTEGER DEFAULT 0,
                    last_seen    TEXT,
                    total_alerts INTEGER DEFAULT 0,
                    added_at     TEXT DEFAULT (datetime('now'))
                )
            """)

            # Alerts table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    cam_id           INTEGER REFERENCES cameras(id),
                    timestamp        TEXT NOT NULL,
                    label            TEXT NOT NULL,
                    threat_level     TEXT NOT NULL,
                    confidence       REAL NOT NULL,
                    snapshot_path    TEXT,
                    clip_path        TEXT,
                    acknowledged     INTEGER DEFAULT 0,
                    acknowledged_at  TEXT,
                    acknowledged_by  TEXT DEFAULT 'operator',
                    notes            TEXT
                )
            """)

            # Detections table (bounding boxes per alert)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS detections (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_id   INTEGER REFERENCES alerts(id) ON DELETE CASCADE,
                    label      TEXT,
                    confidence REAL,
                    x1 REAL, y1 REAL, x2 REAL, y2 REAL
                )
            """)

            # System config key-value table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_config (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            # Camera status log (for uptime tracking)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS camera_status_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    cam_id    INTEGER REFERENCES cameras(id),
                    status    TEXT NOT NULL,    -- 'online' | 'offline'
                    timestamp TEXT NOT NULL
                )
            """)

            # Indexes for common queries
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_ts    ON alerts(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_cam   ON alerts(cam_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_threat ON alerts(threat_level)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_ack   ON alerts(acknowledged)")

            # --- IDEMPOTENT MIGRATIONS ---
            # Migration: Ensure clip_path exists
            try:
                cursor.execute("ALTER TABLE alerts ADD COLUMN clip_path TEXT")
                logger.info("Migrated: added clip_path column to alerts table")
            except sqlite3.OperationalError:
                pass 

            # Migration: Ensure acknowledged_by exists
            try:
                cursor.execute("ALTER TABLE alerts ADD COLUMN acknowledged_by TEXT DEFAULT 'operator'")
                logger.info("Migrated: added acknowledged_by column to alerts table")
            except sqlite3.OperationalError:
                pass

            # Migration: Ensure acknowledged_at exists
            try:
                cursor.execute("ALTER TABLE alerts ADD COLUMN acknowledged_at TEXT")
                logger.info("Migrated: added acknowledged_at column to alerts table")
            except sqlite3.OperationalError:
                pass

            # --- INITIALIZE DEFAULTS ---
            from server.screens.settings_screen import DEFAULTS
            for key, val in DEFAULTS.items():
                cursor.execute(
                    "INSERT OR IGNORE INTO system_config (key, value) VALUES (?, ?)",
                    (key, val)
                )

            conn.commit()
            # logger.info("Database initialized at %s", self.db_path) # logged in MainWindow instead

    # ── Camera Operations ────────────────────────────────────────────────────
    def camera_exists(self, cam_id: int) -> bool:
        """Check if a camera exists in the database."""
        with closing(self._get_connection()) as conn:
            row = conn.execute(
                "SELECT 1 FROM cameras WHERE id = ?", (cam_id,)
            ).fetchone()
            return row is not None

    def upsert_camera(self, cam_id: int, name: str, source: str = "0"):
        """Mark a camera as online. Creates it if it doesn't exist with the given ID.
        Preserves user-set names — does not overwrite with edge-reported names if already exists."""
        now = datetime.utcnow().isoformat()
        with closing(self._get_connection()) as conn:
            existing = conn.execute(
                "SELECT id FROM cameras WHERE id = ?", (cam_id,)
            ).fetchone()
            
            if not existing:
                # Create camera with specific ID
                conn.execute("""
                    INSERT INTO cameras (id, name, source, is_online, last_seen)
                    VALUES (?, ?, ?, 1, ?)
                """, (cam_id, name, source, now))
                logger.info("Auto-registered camera: id=%d name=%s", cam_id, name)
            else:
                # Update online status and last_seen
                conn.execute("""
                    UPDATE cameras
                    SET is_online = 1, last_seen = ?
                    WHERE id = ?
                """, (now, cam_id))
            
            conn.execute("""
                INSERT INTO camera_status_log (cam_id, status, timestamp)
                VALUES (?, 'online', ?)
            """, (cam_id, now))
            conn.commit()

    def mark_camera_offline(self, cam_id: int):
        now = datetime.utcnow().isoformat()
        with closing(self._get_connection()) as conn:
            conn.execute("UPDATE cameras SET is_online = 0 WHERE id = ?", (cam_id,))
            conn.execute("""
                INSERT INTO camera_status_log (cam_id, status, timestamp)
                VALUES (?, 'offline', ?)
            """, (cam_id, now))
            conn.commit()

    def reset_camera_statuses(self):
        """Reset all cameras to offline status (e.g., on app startup)."""
        with closing(self._get_connection()) as conn:
            conn.execute("UPDATE cameras SET is_online = 0")
            conn.commit()
            logger.info("All camera statuses reset to offline in database")

    def get_cameras(self) -> list:
        with closing(self._get_connection()) as conn:
            rows = conn.execute("""
                SELECT * FROM cameras ORDER BY id
            """).fetchall()
            return [dict(r) for r in rows]

    def get_camera(self, cam_id: int) -> Optional[dict]:
        with closing(self._get_connection()) as conn:
            row = conn.execute("SELECT * FROM cameras WHERE id = ?", (cam_id,)).fetchone()
            return dict(row) if row else None

    def add_camera(self, name: str, source: str = "0") -> int:
        """Add a new camera and return its ID."""
        now = datetime.utcnow().isoformat()
        with closing(self._get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO cameras (name, source, is_online, last_seen)
                VALUES (?, ?, 0, ?)
            """, (name, source, now))
            conn.commit()
            cam_id = cursor.lastrowid
            logger.info("Camera added: id=%d name=%s source=%s", cam_id, name, source)
            return cam_id

    def update_camera(self, cam_id: int, name: str = None, source: str = None):
        """Update camera name and/or source."""
        with closing(self._get_connection()) as conn:
            if name is not None and source is not None:
                conn.execute(
                    "UPDATE cameras SET name = ?, source = ? WHERE id = ?",
                    (name, source, cam_id)
                )
            elif name is not None:
                conn.execute(
                    "UPDATE cameras SET name = ? WHERE id = ?",
                    (name, cam_id)
                )
            elif source is not None:
                conn.execute(
                    "UPDATE cameras SET source = ? WHERE id = ?",
                    (source, cam_id)
                )
            conn.commit()
            logger.info("Camera updated: id=%d name=%s source=%s", cam_id, name, source)

    def delete_camera(self, cam_id: int):
        """Delete a camera record. Alerts and logs are preserved (orphaned)."""
        with closing(self._get_connection()) as conn:
            # Disable foreign keys temporarily so we can delete the camera 
            # without triggering cascading deletes or foreign key violations.
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DELETE FROM cameras WHERE id = ?", (cam_id,))
            conn.execute("PRAGMA foreign_keys=ON")
            conn.commit()
            logger.info("Camera deleted (history preserved): id=%d", cam_id)
    # ── Alert Operations ─────────────────────────────────────────────────────
    def create_alert(self, cam_id: int, label: str, threat_level: str,
                     confidence: float, snapshot_path: str,
                     detections: list, clip_path: str = None) -> int:
        ts = datetime.utcnow().isoformat()
        with closing(self._get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO alerts
                    (cam_id, timestamp, label, threat_level, confidence, snapshot_path, clip_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (cam_id, ts, label, threat_level, confidence, snapshot_path, clip_path))
            alert_id = cursor.lastrowid

            for det in detections:
                cursor.execute("""
                    INSERT INTO detections (alert_id, label, confidence, x1, y1, x2, y2)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (alert_id, det.get("label"), det.get("conf"),
                      det.get("x1", 0), det.get("y1", 0),
                      det.get("x2", 0), det.get("y2", 0)))

            conn.execute("""
                UPDATE cameras
                SET total_alerts = total_alerts + 1, last_seen = ?
                WHERE id = ?
            """, (ts, cam_id))
            conn.commit()
            return alert_id

    def get_alerts(self, limit: int = 50, offset: int = 0,
                   cam_id: int = None, threat_level: str = None,
                   acknowledged: bool = None, date_from: str = None,
                   date_to: str = None, search: str = None) -> list:
        """Paginated alerts with optional filters."""
        conditions = []
        params = []

        if cam_id is not None:
            conditions.append("a.cam_id = ?")
            params.append(cam_id)
        if threat_level:
            conditions.append("a.threat_level = ?")
            params.append(threat_level)
        if acknowledged is not None:
            conditions.append("a.acknowledged = ?")
            params.append(1 if acknowledged else 0)
        if date_from:
            conditions.append("a.timestamp >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("a.timestamp <= ?")
            params.append(date_to + "T23:59:59")
        if search:
            # Search by ID, label, or camera name
            conditions.append("(CAST(a.id AS TEXT) LIKE ? OR a.label LIKE ? OR c.name LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])

        with closing(self._get_connection()) as conn:
            rows = conn.execute(f"""
                SELECT a.*, c.name AS cam_name
                FROM alerts a
                LEFT JOIN cameras c ON a.cam_id = c.id
                {where}
                ORDER BY a.timestamp DESC
                LIMIT ? OFFSET ?
            """, params).fetchall()
            return [dict(r) for r in rows]

    def count_alerts(self, cam_id: int = None, threat_level: str = None,
                     acknowledged: bool = None, date_from: str = None,
                     date_to: str = None, search: str = None) -> int:
        """Count matching alerts (for pagination)."""
        conditions = []
        params = []

        if cam_id is not None:
            conditions.append("a.cam_id = ?")
            params.append(cam_id)
        if threat_level:
            conditions.append("a.threat_level = ?")
            params.append(threat_level)
        if acknowledged is not None:
            conditions.append("a.acknowledged = ?")
            params.append(1 if acknowledged else 0)
        if date_from:
            conditions.append("a.timestamp >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("a.timestamp <= ?")
            params.append(date_to + "T23:59:59")
        if search:
            # Search by ID, label, or camera name
            conditions.append("(CAST(a.id AS TEXT) LIKE ? OR a.label LIKE ? OR c.name LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        with closing(self._get_connection()) as conn:
            query = f"""
                SELECT COUNT(*) FROM alerts a
                LEFT JOIN cameras c ON a.cam_id = c.id
                {where}
            """
            row = conn.execute(query, params).fetchone()
            return row[0]

    def get_alert_by_id(self, alert_id: int) -> Optional[dict]:
        with closing(self._get_connection()) as conn:
            row = conn.execute("""
                SELECT a.*, c.name AS cam_name
                FROM alerts a LEFT JOIN cameras c ON a.cam_id = c.id
                WHERE a.id = ?
            """, (alert_id,)).fetchone()
            return dict(row) if row else None

    def get_alert_detections(self, alert_id: int) -> list:
        with closing(self._get_connection()) as conn:
            rows = conn.execute(
                "SELECT * FROM detections WHERE alert_id = ?", (alert_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_alert(self, alert_id: int) -> bool:
        """Delete an alert and its detections from the database."""
        with closing(self._get_connection()) as conn:
            # Detections should be deleted via ON DELETE CASCADE if defined,
            # but we'll do it explicitly if needed.
            conn.execute("DELETE FROM detections WHERE alert_id = ?", (alert_id,))
            result = conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
            conn.commit()
            return result.rowcount > 0

    def acknowledge_alert(self, alert_id: int, notes: str = "",
                          acknowledged_by: str = "operator") -> bool:
        now = datetime.utcnow().isoformat()
        with closing(self._get_connection()) as conn:
            result = conn.execute("""
                UPDATE alerts
                SET acknowledged = 1, acknowledged_at = ?, acknowledged_by = ?, notes = ?
                WHERE id = ?
            """, (now, acknowledged_by, notes, alert_id))
            conn.commit()
            return result.rowcount > 0

    def acknowledge_all(self, cam_id: int = None):
        with closing(self._get_connection()) as conn:
            now = datetime.utcnow().isoformat()
            if cam_id:
                conn.execute("""
                    UPDATE alerts SET acknowledged = 1, acknowledged_at = ?
                    WHERE cam_id = ? AND acknowledged = 0
                """, (now, cam_id))
            else:
                conn.execute("""
                    UPDATE alerts SET acknowledged = 1, acknowledged_at = ?
                    WHERE acknowledged = 0
                """, (now,))
            conn.commit()

    def delete_old_alerts(self, retention_days: int = 30) -> int:
        cutoff = (datetime.utcnow() - timedelta(days=retention_days)).isoformat()
        with closing(self._get_connection()) as conn:
            # 1. Delete associated detections first to avoid FK constraint issues
            conn.execute("""
                DELETE FROM detections 
                WHERE alert_id IN (SELECT id FROM alerts WHERE timestamp < ?)
            """, (cutoff,))
            
            # 2. Delete the alerts
            result = conn.execute(
                "DELETE FROM alerts WHERE timestamp < ?", (cutoff,)
            )
            conn.commit()
            return result.rowcount

    def update_alert_media(self, alert_id: int, snapshot_path: str = None, clip_path: str = None):
        """Update media paths for an existing alert (used for async uploads)."""
        with closing(self._get_connection()) as conn:
            if snapshot_path:
                conn.execute(
                    "UPDATE alerts SET snapshot_path = ? WHERE id = ?",
                    (snapshot_path, alert_id)
                )
            if clip_path:
                conn.execute(
                    "UPDATE alerts SET clip_path = ? WHERE id = ?",
                    (clip_path, alert_id)
                )
            conn.commit()

    # ── Analytics ────────────────────────────────────────────────────────────
    def get_stats(self) -> dict:
        """Live dashboard KPI stats."""
        today = datetime.utcnow().date().isoformat()
        with closing(self._get_connection()) as conn:
            stats = {}
            stats["total_alerts_today"] = conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE timestamp LIKE ?", (f"{today}%",)
            ).fetchone()[0]

            stats["total_alerts_all_time"] = conn.execute(
                "SELECT COUNT(*) FROM alerts"
            ).fetchone()[0]

            stats["critical_alerts_today"] = conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE timestamp LIKE ? AND threat_level = 'CRITICAL'",
                (f"{today}%",)
            ).fetchone()[0]

            stats["unacknowledged"] = conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE acknowledged = 0"
            ).fetchone()[0]

            stats["cameras_online"] = conn.execute(
                "SELECT COUNT(*) FROM cameras WHERE is_online = 1"
            ).fetchone()[0]

            stats["cameras_total"] = conn.execute(
                "SELECT COUNT(*) FROM cameras"
            ).fetchone()[0]

            row = conn.execute(
                "SELECT label, confidence FROM alerts ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            stats["last_detection"] = dict(row) if row else None

            return stats

    def get_hourly_chart(self, days: int = 1) -> list:
        """Alerts per hour for the last N days (for bar chart).
        Ensures all hours are represented (even if 0)."""
        now = datetime.utcnow()
        since = (now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
        
        # Initialize buckets
        buckets = {}
        for i in range(days * 24 + 1):
            ts = (since + timedelta(hours=i)).strftime('%Y-%m-%d %H:00')
            buckets[ts] = 0
            
        with closing(self._get_connection()) as conn:
            rows = conn.execute("""
                SELECT strftime('%Y-%m-%d %H:00', timestamp) AS hour,
                       COUNT(*) AS count
                FROM alerts
                WHERE timestamp >= ?
                GROUP BY hour
            """, (since.isoformat(),)).fetchall()
            for r in rows:
                if r["hour"] in buckets:
                    buckets[r["hour"]] = r["count"]
                    
        return [{"hour": k, "count": v} for k, v in sorted(buckets.items())]

    def get_daily_chart(self, days: int = 30) -> list:
        """Alerts per day for the last N days."""
        now = datetime.utcnow()
        since = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        buckets = {}
        for i in range(days + 1):
            ts = (since + timedelta(days=i)).strftime('%Y-%m-%d')
            buckets[ts] = 0

        with closing(self._get_connection()) as conn:
            rows = conn.execute("""
                SELECT strftime('%Y-%m-%d', timestamp) AS day,
                       COUNT(*) AS count
                FROM alerts
                WHERE timestamp >= ?
                GROUP BY day
            """, (since.isoformat(),)).fetchall()
            for r in rows:
                if r["day"] in buckets:
                    buckets[r["day"]] = r["count"]
            return [{"day": k, "count": v} for k, v in sorted(buckets.items())]

    def get_threat_distribution(self, days: int = None) -> dict:
        """Count per threat level and labels (FIRE, SMOKE, COMBINED)."""
        dist = {"FIRE": 0, "SMOKE": 0, "COMBINED": 0, "CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        
        where = ""
        params = []
        if days:
            since = (datetime.utcnow() - timedelta(days=days)).isoformat()
            where = "WHERE timestamp >= ?"
            params = [since]

        with closing(self._get_connection()) as conn:
            # Basic threat levels
            rows = conn.execute(f"SELECT threat_level, COUNT(*) AS count FROM alerts {where} GROUP BY threat_level", params).fetchall()
            for r in rows:
                dist[r["threat_level"]] = r["count"]
            
            # Label counts
            label_where = "WHERE lower(label) = 'fire'" + (f" AND timestamp >= ?" if days else "")
            dist["FIRE"] = conn.execute(f"SELECT COUNT(*) FROM alerts {label_where}", params).fetchone()[0]
            
            label_where = "WHERE lower(label) = 'smoke'" + (f" AND timestamp >= ?" if days else "")
            dist["SMOKE"] = conn.execute(f"SELECT COUNT(*) FROM alerts {label_where}", params).fetchone()[0]
            
            # Combined
            combined_where = (f"WHERE timestamp >= ?" if days else "")
            # We need to pass params here as well
            # Now includes both the minute-based grouping AND explicit 'FIRE + SMOKE' labels
            dist["COMBINED"] = conn.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT id FROM alerts WHERE upper(label) = 'FIRE + SMOKE' {"AND timestamp >= ?" if days else ""}
                    UNION
                    SELECT id FROM (
                        SELECT id, cam_id, strftime('%Y-%m-%d %H:%M', timestamp) as minute 
                        FROM alerts 
                        {combined_where}
                        GROUP BY cam_id, minute 
                        HAVING COUNT(DISTINCT label) > 1
                    )
                )
            """, params + params).fetchone()[0]
            return dist

    def get_camera_alert_stats(self, days: int = None) -> list:
        """Per-camera alert counts (total, today, critical, avg_confidence)."""
        today = datetime.utcnow().date().isoformat()
        
        since_clause = ""
        params = []
        if days:
            since = (datetime.utcnow() - timedelta(days=days)).isoformat()
            since_clause = "AND timestamp >= ?"
            params = [since]

        with closing(self._get_connection()) as conn:
            # We have 3 placeholders if days is set, or 0 if not
            params_list = params * 3 if days else []
            rows = conn.execute(f"""
                SELECT c.id, c.name, c.is_online, c.last_seen, c.total_alerts,
                    (SELECT COUNT(*) FROM alerts WHERE cam_id = c.id AND timestamp LIKE '{today}%') AS today_count,
                    (SELECT COUNT(*) FROM alerts WHERE cam_id = c.id AND threat_level = 'CRITICAL' {since_clause}) AS critical_count,
                    (SELECT AVG(confidence) FROM alerts WHERE cam_id = c.id {since_clause}) AS avg_confidence,
                    (SELECT COUNT(*) FROM alerts WHERE cam_id = c.id {since_clause}) AS period_alerts
                FROM cameras c
                ORDER BY c.total_alerts DESC
            """, params_list).fetchall()
            return [dict(r) for r in rows]

    # ── CSV Export ────────────────────────────────────────────────────────────
    def export_alerts_csv(self, path: str, **kwargs) -> int:
        """Export filtered alerts to CSV. Returns number of rows written."""
        alerts = self.get_alerts(limit=100000, offset=0, **kwargs)
        if not alerts:
            return 0
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=alerts[0].keys())
            writer.writeheader()
            writer.writerows(alerts)
        return len(alerts)

    # ── Config / Settings ─────────────────────────────────────────────────────
    def set_config(self, key: str, value: str):
        with closing(self._get_connection()) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)
            """, (key, value))
            conn.commit()

    def get_config(self, key: str, default=None) -> Optional[str]:
        with closing(self._get_connection()) as conn:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else default

    def get_all_config(self) -> dict:
        with closing(self._get_connection()) as conn:
            rows = conn.execute("SELECT key, value FROM system_config").fetchall()
            return {r["key"]: r["value"] for r in rows}
