"""
jetson.py — FireGuard Jetson Nano Configuration Screen (Premium Enterprise v2)

Professional terminal-style layout with:
  - SSH connection management with animated status indicator
  - Edge pipeline configuration with organized card sections
  - Camera source management
  - Deployment actions with styled buttons
  - Terminal-style deployment log with header bar
  - All settings persisted to database
"""

import json
import logging
import threading
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFormLayout,
    QTextEdit, QSpinBox, QDoubleSpinBox,
    QScrollArea, QCheckBox, QFrame, QSizePolicy,
    QGridLayout, QProgressBar
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont, QColor, QPainter, QPen, QBrush

logger = logging.getLogger(__name__)

try:
    from server.services.jetson_manager import JetsonManager
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False


# ─────────────────────────────────────────────────────────────────────────────
# Status Pill
# ─────────────────────────────────────────────────────────────────────────────
class StatusPill(QFrame):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(28)
        self.set_status("offline")

    def set_status(self, status: str):
        # status: offline, connected, testing
        if status == "connected":
            self.setStyleSheet("""
                background: #052e16; border: 1px solid #14532d; border-radius: 14px; padding: 0 12px;
            """)
            text, color = "Connected", "#22c55e"
        elif status == "testing":
            self.setStyleSheet("""
                background: #422006; border: 1px solid #713f12; border-radius: 14px; padding: 0 12px;
            """)
            text, color = "Testing...", "#f59e0b"
        else:
            self.setStyleSheet("""
                background: #1e293b; border: 1px solid #334155; border-radius: 14px; padding: 0 12px;
            """)
            text, color = "Not Connected", "#94a3b8"

        layout = QHBoxLayout(self)
        while layout.count(): layout.takeAt(0).widget().deleteLater()
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color}; font-size: 14px; background: transparent;")
        layout.addWidget(dot)

        lbl = QLabel(text)
        lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {color}; background: transparent;")
        layout.addWidget(lbl)

# ─────────────────────────────────────────────────────────────────────────────
# Section Card (Standardized)
# ─────────────────────────────────────────────────────────────────────────────
class SectionCard(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("SectionCard")
        self.setStyleSheet("""
            #SectionCard {
                background: #0f1923;
                border: 1px solid #1e2d3d;
                border-radius: 10px;
            }
        """)

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(24, 20, 24, 20)
        self._outer.setSpacing(16)

        title_lbl = QLabel(title)
        title_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title_lbl.setStyleSheet("""
            color: white; 
            border-left: 2px solid #dc2626; 
            padding-left: 10px;
            background: transparent;
        """)
        self._outer.addWidget(title_lbl)

        self._content = QVBoxLayout()
        self._content.setContentsMargins(0, 0, 0, 0)
        self._content.setSpacing(12)
        self._outer.addLayout(self._content)

    @property
    def content(self) -> QVBoxLayout:
        return self._content


# ─────────────────────────────────────────────────────────────────────────────
# Jetson Screen
# ─────────────────────────────────────────────────────────────────────────────
class JetsonScreen(QWidget):
    _log_signal = Signal(str)
    _status_signal = Signal(str)

    def __init__(self, db=None, ws_server=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.ws_server = ws_server
        self._build_ui()
        self._log_signal.connect(self._append_log)
        self._status_signal.connect(self._update_status)
        self._load_saved_settings()

    def _form_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedWidth(160)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lbl.setStyleSheet("color: #94a3b8; background: transparent; padding-right: 10px;")
        lbl.setFont(QFont("Segoe UI", 10))
        return lbl

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        content = QWidget()
        content.setObjectName("ContentArea")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(24)

        # ── Page Header ──────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("Jetson Nano Configuration")
        title.setFont(QFont("Segoe UI", 26, QFont.Weight.Bold))
        title.setStyleSheet("color: #E6EDF3; background: transparent;")
        title_col.addWidget(title)
        hdr.addLayout(title_col)
        hdr.addStretch()

        self._save_settings_btn = QPushButton("Save Settings")
        self._save_settings_btn.setFixedSize(140, 36)
        self._save_settings_btn.setCursor(Qt.PointingHandCursor)
        self._save_settings_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #94a3b8;
                border: 1px solid #1e2d3d;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(148, 163, 184, 0.1);
                color: white;
            }
        """)
        self._save_settings_btn.clicked.connect(self._save_settings)
        hdr.addWidget(self._save_settings_btn)

        layout.addLayout(hdr)

        # ── Feedback Banner ──────────────────────────────────────────────────
        self._feedback_banner = QLabel("")
        self._feedback_banner.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._feedback_banner.setAlignment(Qt.AlignCenter)
        self._feedback_banner.setFixedHeight(30)
        self._feedback_banner.setVisible(False)
        self._feedback_banner.setStyleSheet("color: #22c55e; background: rgba(34, 197, 94, 0.1); border-radius: 4px;")
        layout.addWidget(self._feedback_banner)

        # ── SSH Connection Section ───────────────────────────────────────────
        ssh_card = SectionCard("SSH Connection")
        ssh_grid = QGridLayout()
        ssh_grid.setSpacing(12)
        ssh_grid.setColumnStretch(2, 1)

        self._ip_input   = QLineEdit("192.168.1.105")
        self._user_input = QLineEdit("jetson")
        self._pass_input = QLineEdit("jetson")
        self._pass_input.setEchoMode(QLineEdit.Password)
        self._port_input = QSpinBox()
        self._port_input.setRange(1, 65535)
        self._port_input.setValue(22)

        for w in [self._ip_input, self._user_input, self._pass_input, self._port_input]:
            w.setFixedSize(340, 34)
            w.setFont(QFont("Consolas", 10))
            w.setStyleSheet("background: #1e2d3d; color: white; border: 1px solid #334155; border-radius: 4px; padding: 0 8px;")

        ssh_grid.addWidget(self._form_label("Jetson IP"), 0, 0)
        ssh_grid.addWidget(self._ip_input, 0, 1)
        ssh_grid.addWidget(self._form_label("SSH Port"), 1, 0)
        ssh_grid.addWidget(self._port_input, 1, 1)
        ssh_grid.addWidget(self._form_label("Username"), 2, 0)
        ssh_grid.addWidget(self._user_input, 2, 1)
        ssh_grid.addWidget(self._form_label("Password"), 3, 0)
        ssh_grid.addWidget(self._pass_input, 3, 1)
        ssh_card.content.addLayout(ssh_grid)

        # Test button & status row
        test_layout = QHBoxLayout()
        test_layout.addStretch()
        
        self._status_pill = StatusPill()
        test_layout.addWidget(self._status_pill)

        self._test_btn = QPushButton("↺  Test Connection")
        self._test_btn.setFixedSize(160, 34)
        self._test_btn.setCursor(Qt.PointingHandCursor)
        self._test_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #00b4d8;
                border: 1px solid #00b4d8;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(0, 180, 216, 0.15);
            }
        """)
        self._test_btn.clicked.connect(self._test_connection)
        test_layout.addWidget(self._test_btn)
        
        ssh_card.content.addLayout(test_layout)
        layout.addWidget(ssh_card)

        # ── Pipeline Config Section ───────────────────────────────────────────
        cfg_card = SectionCard("Edge Pipeline Engine")
        cfg_grid = QGridLayout()
        cfg_grid.setSpacing(12)
        cfg_grid.setColumnStretch(2, 1)

        self._model_path = QLineEdit("best.pt")
        self._conf_spin  = QDoubleSpinBox()
        self._conf_spin.setRange(0.01, 1.0)
        self._conf_spin.setSingleStep(0.05)
        self._conf_spin.setValue(0.60)
        self._iou_spin   = QDoubleSpinBox()
        self._iou_spin.setRange(0.01, 1.0)
        self._iou_spin.setSingleStep(0.05)
        self._iou_spin.setValue(0.50)
        self._device_input = QLineEdit("cpu")
        self._server_url = QLineEdit("ws://127.0.0.1:8000/ws/edge")
        self._server_token = QLineEdit("fire-secret-token")

        # Removed redundant thresholds (Min Consecutive, Cooldown) — now in General Settings

        fields = [
            ("Model Weights", self._model_path),
            ("Confidence", self._conf_spin),
            ("IOU Threshold", self._iou_spin),
            ("Device (0/cpu)", self._device_input),
            ("Server URL", self._server_url),
            ("Access Token", self._server_token),
        ]

        for i, (label, widget) in enumerate(fields):
            widget.setFixedSize(340, 34)
            widget.setFont(QFont("Consolas", 10))
            widget.setStyleSheet("background: #1e2d3d; color: white; border: 1px solid #334155; border-radius: 4px; padding: 0 8px;")
            cfg_grid.addWidget(self._form_label(label), i, 0)
            cfg_grid.addWidget(widget, i, 1)

        cfg_card.content.addLayout(cfg_grid)
        layout.addWidget(cfg_card)

        # ── Deploy Buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._status_btn = QPushButton("📊  Get Remote Status")
        self._status_btn.setFixedSize(180, 40)
        self._status_btn.setCursor(Qt.PointingHandCursor)
        self._status_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #94a3b8; border: 1px solid #334155; border-radius: 6px; font-weight: bold;
            }
            QPushButton:hover { color: white; border-color: #94a3b8; }
        """)
        self._status_btn.clicked.connect(self._get_remote_status)
        btn_row.addWidget(self._status_btn)

        self._deploy_btn = QPushButton("🚀  Deploy Config & Restart Pipeline")
        self._deploy_btn.setFixedSize(260, 40)
        self._deploy_btn.setCursor(Qt.PointingHandCursor)
        self._deploy_btn.setStyleSheet("""
            QPushButton {
                background: #dc2626; color: white; border-radius: 6px; font-weight: bold;
            }
            QPushButton:hover { background: #ef4444; }
        """)
        self._deploy_btn.clicked.connect(self._deploy_config)
        btn_row.addWidget(self._deploy_btn)
        
        layout.addLayout(btn_row)

        # ── Deployment Log ──────────────────────────────────
        log_card = SectionCard("Deployment Log")
        
        # Terminal-style header
        term_header = QWidget()
        term_header.setFixedHeight(28)
        term_header.setStyleSheet("background: #1e1e1e; border-top-left-radius: 4px; border-top-right-radius: 4px;")
        term_h_layout = QHBoxLayout(term_header)
        term_h_layout.setContentsMargins(12, 0, 12, 0)
        
        # Fake window controls
        for color in ["#ff5f56", "#ffbd2e", "#27c93f"]:
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color}; font-size: 10px;")
            term_h_layout.addWidget(dot)
        
        term_title = QLabel("SSH REMOTE TERMINAL")
        term_title.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        term_title.setStyleSheet("color: #64748b; margin-left: 8px;")
        term_h_layout.addWidget(term_title)
        term_h_layout.addStretch()
        
        clear_log_btn = QPushButton("CLEAR")
        clear_log_btn.setFixedSize(50, 18)
        clear_log_btn.setCursor(Qt.PointingHandCursor)
        clear_log_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #4ade80; border: 1px solid #4ade80; border-radius: 3px; font-size: 8px; font-weight: bold; }
            QPushButton:hover { background: rgba(74, 222, 128, 0.1); }
        """)
        clear_log_btn.clicked.connect(lambda: self._log_area.clear())
        term_h_layout.addWidget(clear_log_btn)
        
        log_card.content.addWidget(term_header)

        self._log_area = QTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setMinimumHeight(200)
        self._log_area.setStyleSheet("""
            QTextEdit {
                font-family: 'Consolas'; 
                font-size: 11px; 
                background: #070a12; 
                color: #4ade80; 
                border: 1px solid #1e1e1e;
                border-top: none;
                border-bottom-left-radius: 8px; 
                border-bottom-right-radius: 8px; 
                padding: 12px;
            }
        """)
        log_card.content.addWidget(self._log_area)
        layout.addWidget(log_card)

        layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)

    def _update_status(self, status: str):
        self._status_pill.set_status(status)

    def _test_connection(self):
        self._log("Testing SSH connection...")
        self._status_signal.emit("testing")
        self._test_btn.setEnabled(False)

        def _run():
            mgr, err = self._get_manager()
            if not mgr:
                self._log(f"✗  {err}")
                self._status_signal.emit("offline")
            else:
                ok, msg = mgr.test_connection()
                self._log(f"{'✓' if ok else '✗'}  {msg}")
                self._status_signal.emit("connected" if ok else "offline")
                mgr.close()
            self._test_btn.setEnabled(True)

        threading.Thread(target=_run, daemon=True).start()

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_signal.emit(f"[{ts}]  {msg}")

    def _append_log(self, msg: str):
        self._log_area.append(msg)
        self._log_area.verticalScrollBar().setValue(
            self._log_area.verticalScrollBar().maximum()
        )

    def _get_manager(self):
        if not HAS_PARAMIKO:
            return None, "paramiko not installed"
        try:
            mgr = JetsonManager(
                self._ip_input.text().strip(),
                self._user_input.text().strip(),
                self._pass_input.text().strip(),
                port=self._port_input.value(),
                remote_base_path=self._remote_path.text().strip()
            )
            return mgr, None
        except Exception as e:
            return None, str(e)

    def _build_config(self) -> dict:
        """Builds final YAML config for edge, merging general thresholds and cameras from DB."""
        def g(k, d): return self.db.get_config(k, d)
        
        # Get actual cameras from DB
        db_cameras = self.db.get_cameras()
        yaml_cameras = []
        for cam in db_cameras:
            s = str(cam.get("source", "0")).strip()
            source = int(s) if s.isdigit() else s
            yaml_cameras.append({
                "id": cam["id"],
                "url": source,
                "name": cam.get("name", f"Camera {cam['id']}")
            })

        return {
            "cameras": yaml_cameras, 
            "model": {
                "path":   self._model_path.text().strip(),
                "device": self._device_input.text().strip(),
                "conf":   self._conf_spin.value(),
                "iou":    self._iou_spin.value(),
                "imgsz":  640,
                "batch":  2,
            },
            "server": {
                "url":   self._server_url.text().strip(),
                "token": self._server_token.text().strip(),
            },
            "alert": {
                "min_consecutive":  int(g("min_consecutive", "3")),
                "cooldown_sec":     int(g("cooldown_sec", "30")),
                "save_snapshots":   g("save_snapshots", "1") == "1",
                "save_clips":       g("save_clips", "1") == "1",
                "clip_duration_sec": 10,
            },
            "stream":       {"buffer_size": 10, "vid_stride": 2, "reconnect_sec": 5, "stale_frame_sec": 5},
            "transmission": {"interval_ms": 500, "jpeg_quality": 60},
            "storage":      {"snapshots_dir": "alerts/snapshots", "clips_dir": "alerts/clips",
                             "logs_dir": "logs", "max_snapshots": 500, "max_clips": 100},
            "monitoring":   {"stats_interval_sec": 30, "log_to_file": True, "log_file": "logs/edge.log"},
        }

    def _deploy_config(self):
        self._log("Building config...")
        config = self._build_config()
        self._deploy_btn.setEnabled(False)
        self._deploy_btn.setText("⏳  Deploying...")

        def _run():
            # 1. Primary Sync: WebSocket (Fast, Path-Independent)
            if self.ws_server and self.ws_server._manager.connected_count > 0:
                self._log("📡  Syncing config via WebSocket...")
                self.ws_server.sync_edge_config(config)
                self._log("✓  Config synced to all active edge devices.")
                # We also try SSH as a fallback for the restart command
            
            # 2. Secondary Sync: SSH (For file persistence and hard restart)
            mgr, err = self._get_manager()
            if not mgr:
                if self.ws_server and self.ws_server._manager.connected_count > 0:
                    # If WebSocket worked but SSH didn't, we're still mostly OK
                    self._log("ℹ  Note: SSH not available for hard restart, but config was pushed via WS.")
                    self._status_signal.emit("connected")
                else:
                    self._log(f"✗  SSH Error: {err}")
                    self._status_signal.emit("offline")
                self._deploy_btn.setEnabled(True)
                self._deploy_btn.setText("🚀  Deploy Config & Restart Pipeline")
                return

            self._log("Pushing config to Jetson via SSH...")
            # Use path from DB if available, else default
            db_path = self.db.get_config("jetson_path", "/home/jetson/fire_detection")
            ok = mgr.update_config(config, remote_path=f"{db_path.rstrip('/')}/config.yaml")
            
            if ok:
                self._log("✓  Config deployed via SSH. Pipeline restarted.")
                self._status_signal.emit("connected")
            else:
                if self.ws_server and self.ws_server._manager.connected_count > 0:
                    self._log("✓  Config active (WS), but SSH deployment failed.")
                else:
                    self._log("✗  Deployment failed.")
                    self._status_signal.emit("offline")
            
            mgr.close()
            self._deploy_btn.setEnabled(True)
            self._deploy_btn.setText("🚀  Deploy Config & Restart Pipeline")

        threading.Thread(target=_run, daemon=True).start()

    def _get_remote_status(self):
        self._log("Fetching remote status...")
        def _run():
            mgr, err = self._get_manager()
            if not mgr:
                self._log(f"✗  {err}")
                return
            status = mgr.get_status()
            if status:
                for k, v in status.items(): self._log(f"  {k}: {v}")
            else:
                self._log("✗  Could not retrieve status")
            mgr.close()
        threading.Thread(target=_run, daemon=True).start()

    def _save_settings(self, feedback=True):
        if not self.db: return
        try:
            cfg = {
                "jetson_host":   self._ip_input.text().strip(),
                "jetson_user":   self._user_input.text().strip(),
                "jetson_pass":   self._pass_input.text().strip(),
                "jetson_port":   str(self._port_input.value()),
                # jetson_path is kept in DB but not in UI
                "jetson_model":  self._model_path.text().strip(),
                "jetson_conf":   str(self._conf_spin.value()),
                "jetson_iou":    str(self._iou_spin.value()),
                "jetson_device": self._device_input.text().strip(),
                "jetson_server": self._server_url.text().strip(),
                "jetson_token":  self._server_token.text().strip(),
            }
            for k, v in cfg.items(): 
                self.db.set_config(k, v)
            
            if feedback:
                self._show_feedback(True, "✔  Settings saved successfully")
            self._log("✓  Settings saved to database.")
        except Exception as e:
            logger.error("Save settings failed: %s", e)

    def _show_feedback(self, success: bool, message: str):
        self._feedback_banner.setText(message)
        self._feedback_banner.setVisible(True)
        QTimer.singleShot(3000, lambda: self._feedback_banner.setVisible(False))

    def _load_saved_settings(self):
        if not self.db: return
        try:
            def g(k, d=""): return self.db.get_config(k, d)
            if g("jetson_host"): self._ip_input.setText(g("jetson_host"))
            if g("jetson_user"): self._user_input.setText(g("jetson_user"))
            if g("jetson_pass"): self._pass_input.setText(g("jetson_pass"))
            if g("jetson_port"): self._port_input.setValue(int(g("jetson_port", "22")))
            
            if g("jetson_model"): self._model_path.setText(g("jetson_model"))
            if g("jetson_conf"): self._conf_spin.setValue(float(g("jetson_conf", "0.25")))
            if g("jetson_iou"): self._iou_spin.setValue(float(g("jetson_iou", "0.45")))
            if g("jetson_device"): self._device_input.setText(g("jetson_device"))
            if g("jetson_server"): self._server_url.setText(g("jetson_server"))
            if g("jetson_token"): self._server_token.setText(g("jetson_token"))

            # Connect for auto-save (silent)
            for w in [self._ip_input, self._user_input, self._pass_input, 
                      self._model_path, self._device_input, self._server_url, self._server_token]:
                w.editingFinished.connect(lambda: self._save_settings(feedback=False))
            
            self._port_input.valueChanged.connect(lambda: self._save_settings(feedback=False))
            self._conf_spin.valueChanged.connect(lambda: self._save_settings(feedback=False))
            self._iou_spin.valueChanged.connect(lambda: self._save_settings(feedback=False))

        except Exception as e: logger.error("Load settings failed: %s", e)
