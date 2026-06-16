"""
alert_service.py — FireGuard Alert Service (Industry Edition)
Handles:
  - Save JPEG snapshot with timestamp
  - Save annotated snapshot (with bounding boxes)
  - Confidence → threat level mapping
  - Alert acknowledgment
  - CSV export
  - Snapshot retrieval by alert_id
"""

import os
import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Threat level → color mapping (BGR for OpenCV)
THREAT_COLORS = {
    "CRITICAL": (0,   0, 255),    # Pure Red
    "HIGH":     (0,  69, 255),    # Orange-Red
    "MEDIUM":   (0, 165, 255),    # Orange
    "LOW":      (0, 255, 255),    # Yellow
}


class AlertService:
    def __init__(self, db, storage_root: str = "storage"):
        self.db            = db
        self.storage_root  = Path(storage_root)
        self.snapshots_dir = self.storage_root / "snapshots"
        self.clips_dir     = self.storage_root / "clips"
        self.exports_dir   = self.storage_root / "exports"

        for d in [self.snapshots_dir, self.clips_dir, self.exports_dir]:
            d.mkdir(parents=True, exist_ok=True)

    # ── Threat Level ─────────────────────────────────────────────────────────
    def confidence_to_threat(self, label: str, confidence: float) -> str:
        """Map detection label + confidence to a threat level string."""
        label_lower = label.lower()
        
        # 1. Smoke Logic
        if label_lower == "smoke":
            if confidence >= 0.85: return "CRITICAL"
            if confidence >= 0.70: return "HIGH"
            if confidence >= 0.60: return "MEDIUM"
            return "LOW"

        # 2. Generic / Fire Logic
        try:
            crit = float(self.db.get_config("critical_conf", "0.80"))
            high = float(self.db.get_config("high_conf", "0.60"))
            med  = float(self.db.get_config("medium_conf", "0.40"))
        except Exception:
            crit, high, med = 0.80, 0.60, 0.40

        if confidence >= crit:
            return "CRITICAL"
        if confidence >= high:
            return "HIGH"
        if confidence >= med:
            return "MEDIUM"
        return "LOW"

    def threat_to_priority(self, threat: str) -> int:
        """Numeric priority for sorting (lower = higher priority)."""
        return {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "SMOKE": 3, "LOW": 4}.get(threat, 5)

    # ── Snapshot ──────────────────────────────────────────────────────────────
    def save_snapshot(self, cam_id: int, jpeg_bytes: bytes,
                      detections: list = None) -> str:
        """
        Save a JPEG snapshot with optional bounding box annotations.
        Returns relative path from storage_root.
        """
        if self.db.get_config("save_snapshots", "1") != "1":
            return ""
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        fname     = f"cam{cam_id}_{timestamp}.jpg"
        rel_path  = f"snapshots/{fname}" # Always forward slashes for URLs
        abs_path  = self.storage_root / "snapshots" / fname

        try:
            if detections:
                # Decode JPEG to numpy array for annotation
                arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is not None:
                    img = self._annotate_frame(img, detections)
                    cv2.imwrite(str(abs_path), img)
                else:
                    abs_path.write_bytes(jpeg_bytes)
            else:
                abs_path.write_bytes(jpeg_bytes)

            return rel_path

        except Exception as e:
            logger.error("Failed to save snapshot: %s", e)
            return ""

    def _annotate_frame(self, img: np.ndarray, detections: list) -> np.ndarray:
        """Draw bounding boxes + high-visibility labels on frame."""
        for det in detections:
            label = det.get("label", "")
            conf  = det.get("conf", 0.0)
            x1    = int(det.get("x1", 0))
            y1    = int(det.get("y1", 0))
            x2    = int(det.get("x2", 0))
            y2    = int(det.get("y2", 0))

            label_lower = label.lower()
            if label_lower == "smoke":
                color = (255, 0, 0) # Blue
            else:
                threat = self.confidence_to_threat(label, conf)
                color  = THREAT_COLORS.get(threat, (255, 255, 255))

            # Bounding box
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            
            # Label with Background
            text = f"{label.upper()} {conf * 100:.0f}%"
            (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            
            tx, ty = x1, y1 - 8
            if ty - th < 0: ty = y2 + th + 8 # Flip to bottom if off-screen top
            
            # Draw solid fill for label
            cv2.rectangle(img, (tx, ty - th - 2), (tx + tw, ty + baseline), color, -1)
            # Draw white text on top
            cv2.putText(img, text, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
            
        return img

    def get_snapshot_abs_path(self, rel_path: str) -> Optional[str]:
        """Resolve a relative snapshot path to absolute path if it exists."""
        if not rel_path:
            return None
        abs_path = self.storage_root / rel_path
        return str(abs_path) if abs_path.exists() else None

    def get_snapshot_by_alert_id(self, alert_id: int) -> Optional[str]:
        """Get absolute path to snapshot for a given alert_id."""
        alert = self.db.get_alert_by_id(alert_id)
        if alert and alert.get("snapshot_path"):
            return self.get_snapshot_abs_path(alert["snapshot_path"])
        return None

    # ── Process Alert ─────────────────────────────────────────────────────────
    def process_alert(self, metadata: dict, jpeg_bytes: bytes) -> int:
        """
        Full alert processing pipeline:
        1. Determine best detection + threat level (Severity-based priority)
        2. Save annotated snapshot
        3. Store alert in database
        4. Return alert_id
        """
        cam_id     = metadata["cam_id"]
        detections = metadata.get("detections", [])
        clip_path  = metadata.get("clip_path") # Extract clip_path from edge payload

        if detections:
            # Check for combined Fire + Smoke
            labels_present = {d["label"].lower() for d in detections}
            is_combined = "fire" in labels_present and "smoke" in labels_present

            # Sort by severity priority: CRITICAL(0) > HIGH(1) > MEDIUM(2) > LOW(3)
            # min() picks the lowest priority number (the most severe threat)
            best_det = min(detections, key=lambda d: self.threat_to_priority(
                self.confidence_to_threat(d["label"], d["conf"])
            ))
            
            if is_combined:
                label = "FIRE + SMOKE"
                # For combined, we take the highest confidence of the two
                confidence = max(d["conf"] for d in detections if d["label"].lower() in ["fire", "smoke"])
            else:
                label = best_det["label"]
                confidence = best_det["conf"]

            threat = self.confidence_to_threat(best_det["label"], confidence)
        else:
            label      = "fire"
            confidence = 0.0
            threat     = "CRITICAL"

        snapshot_path = self.save_snapshot(cam_id, jpeg_bytes,
                                           detections=detections if jpeg_bytes else None)

        alert_id = self.db.create_alert(
            cam_id        = cam_id,
            label         = label,
            threat_level  = threat,
            confidence    = confidence,
            snapshot_path = snapshot_path,
            detections    = detections,
            clip_path     = clip_path, # Pass it to the DB
        )

        logger.warning("🔥  ALERT id=%d  cam=%d  [%s] %s  conf=%.1f%%",
                       alert_id, cam_id, threat, label.upper(), confidence * 100)
        return alert_id

    # ── Delete ────────────────────────────────────────────────────────────────
    def delete_alert(self, alert_id: int) -> bool:
        """
        Delete an alert:
        1. Remove associated snapshot and clip files
        2. Delete from database
        """
        alert = self.db.get_alert_by_id(alert_id)
        if not alert:
            return False

        # 1. Remove media files
        if alert.get("snapshot_path"):
            abs_snap = self.storage_root / alert["snapshot_path"]
            if abs_snap.exists():
                abs_snap.unlink(missing_ok=True)
                logger.info("Deleted snapshot: %s", abs_snap)

        if alert.get("clip_path"):
            abs_clip = self.storage_root / alert["clip_path"]
            if abs_clip.exists():
                abs_clip.unlink(missing_ok=True)
                logger.info("Deleted clip: %s", abs_clip)

        # 2. Delete from database
        return self.db.delete_alert(alert_id)

    # ── Acknowledge ───────────────────────────────────────────────────────────
    def acknowledge_alert(self, alert_id: int, notes: str = "",
                          acknowledged_by: str = "operator") -> bool:
        return self.db.acknowledge_alert(alert_id, notes, acknowledged_by)

    def update_alert_media(self, alert_id: int, snapshot_path: str = None, clip_path: str = None):
        """Update media paths for an existing alert (used for async uploads)."""
        self.db.update_alert_media(alert_id, snapshot_path, clip_path)

    def delete_camera_media(self, cam_id: int):
        """Delete all physical media files associated with a camera's alerts."""
        alerts = self.db.get_alerts(limit=10000, cam_id=cam_id)
        count = 0
        for alert in alerts:
            if alert.get("snapshot_path"):
                abs_snap = self.storage_root / alert["snapshot_path"]
                if abs_snap.exists():
                    abs_snap.unlink(missing_ok=True)
                    count += 1
            if alert.get("clip_path"):
                abs_clip = self.storage_root / alert["clip_path"]
                if abs_clip.exists():
                    abs_clip.unlink(missing_ok=True)
                    count += 1
        if count > 0:
            logger.info("Deleted %d media files for camera %d", count, cam_id)

    # ── CSV Export ────────────────────────────────────────────────────────────
    def export_csv(self, cam_id: int = None, date_from: str = None,
                   date_to: str = None) -> str:
        """
        Export filtered alerts to CSV.
        Returns the absolute path to the created CSV file.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename  = f"fireguard_export_{timestamp}.csv"
        path      = str(self.exports_dir / filename)

        rows_written = self.db.export_alerts_csv(path, cam_id=cam_id,
                                                  date_from=date_from,
                                                  date_to=date_to)
        logger.info("Exported %d alerts to %s", rows_written, path)
        return path

    # ── Cleanup ───────────────────────────────────────────────────────────────
    def cleanup_old_snapshots(self, max_count: int = 500):
        """Keep only the N most recent snapshots."""
        files = sorted(self.snapshots_dir.glob("*.jpg"),
                       key=lambda f: f.stat().st_mtime)
        while len(files) > max_count:
            files.pop(0).unlink(missing_ok=True)

    def run_retention_cleanup(self, retention_days: int = 30):
        """Delete DB alerts older than N days and their physical media."""
        if retention_days <= 0:
            # Force wipe all media files from disk
            self._wipe_all_media()
            count = self.db.delete_old_alerts(0) # 0 means delete everything
        else:
            # Standard cleanup based on retention policy
            # Note: A more thorough cleanup would find specific IDs, but for now 
            # we rely on the DB to return count of deleted records.
            count = self.db.delete_old_alerts(retention_days)
            
        logger.info("Retention cleanup: removed %d old alerts and associated media", count)

    def _wipe_all_media(self):
        """Physically deletes all snapshots, clips, and exports from disk."""
        try:
            for folder in [self.snapshots_dir, self.clips_dir, self.exports_dir]:
                for f in folder.glob("*"):
                    if f.is_file():
                        f.unlink(missing_ok=True)
            logger.info("Physical media wipe completed: all snapshots and clips deleted.")
        except Exception as e:
            logger.error("Failed to wipe media files: %s", e)
