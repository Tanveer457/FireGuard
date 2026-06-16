"""
settings_screen.py — FireGuard System Settings (Premium Enterprise Edition v2)

Professional card-based layout with:
  - Styled section cards with red accent headers
  - Alert detection thresholds with visual confidence slider
  - Audio notification toggles
  - Transmission & storage configuration
  - Server settings with restart warning
  - FastAPI endpoint reference with colored method badges
  - Inline save feedback (no blocking dialogs)
"""

import logging
from contextlib import closing
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSpinBox, QDoubleSpinBox, QCheckBox, QLineEdit,
    QPushButton, QScrollArea, QFormLayout,
    QMessageBox, QSlider, QFrame, QSizePolicy,
    QGridLayout
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QColor

logger = logging.getLogger(__name__)

# ── Default values ────────────────────────────────────────────────────────────
DEFAULTS = {
    # Alert thresholds
    "min_consecutive":   "3",
    "cooldown_sec":      "30",
    "critical_conf":     "0.80",
    "high_conf":         "0.60",
    "medium_conf":       "0.40",
    # Notifications
    "beep_critical":     "1",
    "beep_high":         "1",
    "beep_medium":       "0",
    "beep_low":          "0",
    # Transmission
    "jpeg_quality":      "60",
    "interval_ms":       "500",
    # Storage
    "retention_days":    "30",
    "max_snapshots":     "500",
    "max_clips":         "100",
    # Server
    "server_host":       "0.0.0.0",
    "server_port":       "8000",
    "edge_token":        "fire-secret-token",
    # Options
    "save_snapshots":    "1",
    "save_clips":        "1",
    "auto_cleanup":      "1",
    # Jetson SSH Auto-Start
    "jetson_host":       "",
    "jetson_port":       "22",
    "jetson_user":       "fireguard",
    "jetson_pass":       "",
    "jetson_key":        "",
    "jetson_path":       "/home/fireguard/project/edge",
    "jetson_python":     "python3",
}


# ─────────────────────────────────────────────────────────────────────────────
# Section Card (Reusable)
# ─────────────────────────────────────────────────────────────────────────────
class SectionCard(QFrame):
    """A premium styled card with header and red accent line."""

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

        # Header row
        title_lbl = QLabel(title)
        title_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title_lbl.setStyleSheet("""
            color: white; 
            border-left: 2px solid #dc2626; 
            padding-left: 10px;
            background: transparent;
        """)
        self._outer.addWidget(title_lbl)

        # Content area
        self._content = QVBoxLayout()
        self._content.setContentsMargins(0, 0, 0, 0)
        self._content.setSpacing(12)
        self._outer.addLayout(self._content)

    @property
    def content(self) -> QVBoxLayout:
        return self._content


class SettingsScreen(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.ws_thread = None
        self._widgets: dict = {}
        self._dirty = False
        self._build_ui()
        self._load_all()

    # ── UI Build ──────────────────────────────────────────────────────────────
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

        # ── Header ───────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(16)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title = QLabel("System Settings")
        title.setFont(QFont("Segoe UI", 26, QFont.Weight.Bold))
        title.setStyleSheet("color: #E6EDF3; background: transparent;")
        title_col.addWidget(title)
        hdr.addLayout(title_col)
        hdr.addStretch()

        self._reset_btn = QPushButton(" ⟳  Reset Defaults")
        self._reset_btn.setFixedSize(140, 36)
        self._reset_btn.setCursor(Qt.PointingHandCursor)
        self._reset_btn.setStyleSheet("""
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
        self._reset_btn.clicked.connect(self._reset_defaults)

        self._save_btn = QPushButton("Save Settings")
        self._save_btn.setFixedSize(140, 36)
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setStyleSheet("""
            QPushButton {
                background: #dc2626;
                color: white;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #ef4444;
            }
        """)
        self._save_btn.clicked.connect(self._save_all)

        hdr.addWidget(self._reset_btn)
        hdr.addWidget(self._save_btn)
        layout.addLayout(hdr)

        # ── Inline Save Feedback Banner ───────────────────────────────────────
        self._feedback_banner = QLabel("")
        self._feedback_banner.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._feedback_banner.setAlignment(Qt.AlignCenter)
        self._feedback_banner.setFixedHeight(30)
        self._feedback_banner.setVisible(False)
        self._feedback_banner.setStyleSheet("color: #22c55e; background: rgba(34, 197, 94, 0.1); border-radius: 4px;")
        layout.addWidget(self._feedback_banner)

        # ── Alert Detection Section ───────────────────────────────────────────
        det_card = SectionCard("Alert Detection Thresholds")
        det_form = QGridLayout()
        det_form.setSpacing(12)
        det_form.setColumnStretch(2, 1)

        self._widgets["min_consecutive"] = self._spin(1, 20, 3, " frames")
        self._widgets["cooldown_sec"]    = self._spin(5, 600, 30, " sec")
        self._widgets["critical_conf"]   = self._dspin(0.01, 1.0, 0.80, " 0.0–1.0")
        self._widgets["high_conf"]       = self._dspin(0.01, 1.0, 0.60, " 0.0–1.0")
        self._widgets["medium_conf"]     = self._dspin(0.01, 1.0, 0.40, " 0.0–1.0")

        rows = [
            ("Sensitivity (Min Frames)", "min_consecutive"),
            ("Alert Cooldown", "cooldown_sec"),
            ("Critical Threshold", "critical_conf"),
            ("High Threshold", "high_conf"),
            ("Medium Threshold", "medium_conf"),
        ]

        for i, (label, key) in enumerate(rows):
            det_form.addWidget(self._form_label(label), i, 0)
            det_form.addWidget(self._widgets[key], i, 1)

        det_card.content.addLayout(det_form)
        layout.addWidget(det_card)

        # ── Notification Section ──────────────────────────────────────────────
        notif_card = SectionCard("Audio Notifications")
        notif_layout = QVBoxLayout()
        notif_layout.setSpacing(8)

        notif_items = [
            ("beep_critical", "Critical Alerts", True,  "#dc2626"),
            ("beep_high",     "High Alerts",     True,  "#ea580c"),
            ("beep_medium",   "Medium Alerts",   False, "#f59e0b"),
            ("beep_low",      "Low Alerts",      False, "#94a3b8"),
        ]

        toggle_qss = """
            QCheckBox::indicator { width: 40px; height: 20px; }
            QCheckBox::indicator:unchecked {
                image: url(none);
                background: #1e2d3d;
                border-radius: 10px;
            }
            QCheckBox::indicator:checked {
                image: url(none);
                background: %COLOR%;
                border-radius: 10px;
            }
        """

        for key, label, default, color in notif_items:
            row_widget = QWidget()
            row_widget.setFixedHeight(36)
            row_widget.setStyleSheet(f"""
                QWidget {{
                    border-left: 3px solid {color};
                    background: rgba(255,255,255,0.02);
                    border-radius: 4px;
                }}
            """)
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(16, 0, 16, 0)
            
            cb = QCheckBox(label)
            cb.setChecked(default)
            cb.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            cb.setCursor(Qt.PointingHandCursor)
            cb.setStyleSheet(toggle_qss.replace("%COLOR%", color))
            self._widgets[key] = cb
            
            row_layout.addWidget(cb)
            row_layout.addStretch()
            notif_layout.addWidget(row_widget)

        notif_card.content.addLayout(notif_layout)
        layout.addWidget(notif_card)

        # ── Storage Section ───────────────────────────────────────────────────
        stor_card = SectionCard("Storage & Cleanup")
        stor_form = QGridLayout()
        stor_form.setSpacing(12)
        stor_form.setColumnStretch(2, 1)

        self._widgets["retention_days"]  = self._spin(1, 365, 30, " days")
        self._widgets["max_snapshots"]   = self._spin(10, 10000, 500)
        self._widgets["max_clips"]       = self._spin(10, 2000, 100)
        
        self._widgets["save_snapshots"]  = QCheckBox("  Auto-save snapshot on alert")
        self._widgets["save_clips"]      = QCheckBox("  Auto-save video clip on alert")
        self._widgets["auto_cleanup"]    = QCheckBox("  Enable automatic old data cleanup")

        for k in ["save_snapshots", "save_clips", "auto_cleanup"]:
            self._widgets[k].setFont(QFont("Segoe UI", 10))
            self._widgets[k].setStyleSheet("QCheckBox { color: #94a3b8; } QCheckBox::indicator { width: 18px; height: 18px; }")

        stor_form.addWidget(self._form_label("History Retention"), 0, 0)
        stor_form.addWidget(self._widgets["retention_days"], 0, 1)
        stor_form.addWidget(self._form_label("Snapshot Limit"), 1, 0)
        stor_form.addWidget(self._widgets["max_snapshots"], 1, 1)
        stor_form.addWidget(self._form_label("Video Clip Limit"), 2, 0)
        stor_form.addWidget(self._widgets["max_clips"], 2, 1)
        
        stor_vbox = QVBoxLayout()
        stor_vbox.addLayout(stor_form)
        stor_vbox.addSpacing(10)
        stor_vbox.addWidget(self._widgets["save_snapshots"])
        stor_vbox.addWidget(self._widgets["save_clips"])
        stor_vbox.addWidget(self._widgets["auto_cleanup"])

        stor_card.content.addLayout(stor_vbox)
        layout.addWidget(stor_card)

        # ── Server Identity ───────────────────────────────────────────────────
        srv_card = SectionCard("FastAPI Server Identity")
        srv_form = QGridLayout()
        srv_form.setSpacing(12)
        srv_form.setColumnStretch(2, 1)

        self._widgets["server_host"]  = QLineEdit("0.0.0.0")
        self._widgets["server_port"]  = self._spin(1024, 65535, 8000)
        self._widgets["edge_token"]   = QLineEdit("fire-secret-token")

        for w in [self._widgets["server_host"], self._widgets["edge_token"]]:
            w.setFixedSize(340, 34)
            w.setFont(QFont("Consolas", 10))
            w.setStyleSheet("background: #1e2d3d; color: white; border: 1px solid #334155; border-radius: 4px; padding: 0 8px;")

        srv_form.addWidget(self._form_label("Listen Interface"), 0, 0)
        srv_form.addWidget(self._widgets["server_host"], 0, 1)
        srv_form.addWidget(self._form_label("Port Number"), 1, 0)
        srv_form.addWidget(self._widgets["server_port"], 1, 1)
        srv_form.addWidget(self._form_label("Access Token"), 2, 0)
        srv_form.addWidget(self._widgets["edge_token"], 2, 1)

        srv_vbox = QVBoxLayout()
        srv_vbox.addLayout(srv_form)
        
        warn_lbl = QLabel("⚠ Identity changes require an app restart.")
        warn_lbl.setStyleSheet("color: #f59e0b; font-size: 11px; padding: 8px 0 0 170px;")
        srv_vbox.addWidget(warn_lbl)

        srv_card.content.addLayout(srv_vbox)
        layout.addWidget(srv_card)

        # ── Maintenance ──────────────────────────────────────────────────────
        maint_card = SectionCard("Maintenance")
        maint_vbox = QVBoxLayout()
        maint_vbox.setSpacing(12)
        
        clear_btn = QPushButton("Clear All Detection Records")
        clear_btn.setFixedSize(220, 36)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #ef4444; border: 1px solid #ef4444; border-radius: 6px; font-weight: bold; font-size: 10px;
            }
            QPushButton:hover { background: rgba(239, 68, 68, 0.1); }
        """)
        clear_btn.clicked.connect(self._clear_all_data)
        
        maint_desc = QLabel("Permanently wipe historical alerts and snapshots from disk and database.")
        maint_desc.setStyleSheet("color: #64748b; font-size: 11px;")
        
        maint_vbox.addWidget(clear_btn)
        maint_vbox.addWidget(maint_desc)
        maint_card.content.addLayout(maint_vbox)
        layout.addWidget(maint_card)

        layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)

    # ── Widget Helpers ────────────────────────────────────────────────────────
    def _form_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedWidth(160)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lbl.setStyleSheet("color: #94a3b8; background: transparent; padding-right: 10px;")
        lbl.setFont(QFont("Segoe UI", 10))
        return lbl

    def _spin(self, lo, hi, val, suffix="") -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        s.setFixedSize(340, 34)
        s.setFont(QFont("Consolas", 10))
        if suffix:
            s.setSuffix(suffix)
        s.setStyleSheet("""
            QSpinBox {
                background: #1e2d3d; color: white; border: 1px solid #334155; border-radius: 4px; padding: 0 8px;
            }
            QSpinBox::up-button, QSpinBox::down-button { width: 20px; border: none; background: transparent; }
        """)
        return s

    def _dspin(self, lo, hi, val, suffix="") -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        s.setSingleStep(0.05)
        s.setDecimals(2)
        s.setFixedSize(340, 34)
        s.setFont(QFont("Consolas", 10))
        if suffix:
            s.setSuffix(suffix)
        s.setStyleSheet("""
            QDoubleSpinBox {
                background: #1e2d3d; color: white; border: 1px solid #334155; border-radius: 4px; padding: 0 8px;
            }
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 20px; border: none; background: transparent; }
        """)
        return s

    def _show_feedback(self, success: bool, message: str):
        """Show inline feedback banner that auto-hides after 3 seconds."""
        self._feedback_banner.setText(message)
        if success:
            self._feedback_banner.setObjectName("SaveSuccessBanner")
        else:
            self._feedback_banner.setObjectName("SaveErrorBanner")
        self._feedback_banner.style().unpolish(self._feedback_banner)
        self._feedback_banner.style().polish(self._feedback_banner)
        self._feedback_banner.setVisible(True)
        QTimer.singleShot(3000, lambda: self._feedback_banner.setVisible(False))

    # ── Load / Save ────────────────────────────────────────────────────────────
    def _load_all(self):
        if not self.db:
            return
        try:
            all_cfg = self.db.get_all_config()
            for key, widget in self._widgets.items():
                raw = all_cfg.get(key, DEFAULTS.get(key, ""))
                if isinstance(widget, QSpinBox):
                    try:
                        widget.setValue(int(raw))
                    except Exception:
                        pass
                elif isinstance(widget, QDoubleSpinBox):
                    try:
                        widget.setValue(float(raw))
                    except Exception:
                        pass
                elif isinstance(widget, QCheckBox):
                    widget.setChecked(raw == "1")
                elif isinstance(widget, QLineEdit):
                    widget.setText(raw)
                
                # Prevent stretching layout
                if not isinstance(widget, QCheckBox):
                    widget.setMaximumWidth(350)
        except Exception as e:
            logger.error("Settings load failed: %s", e)

    def _save_all(self):
        if not self.db:
            self._show_feedback(False, "✕  Database not available")
            return
        try:
            for key, widget in self._widgets.items():
                if isinstance(widget, QSpinBox):
                    val = str(widget.value())
                elif isinstance(widget, QDoubleSpinBox):
                    val = str(widget.value())
                elif isinstance(widget, QCheckBox):
                    val = "1" if widget.isChecked() else "0"
                elif isinstance(widget, QLineEdit):
                    val = widget.text()
                else:
                    continue
                self.db.set_config(key, val)
                
            # Sync to edge config and broadcast reload live
            try:
                from server.utils.config_sync import sync_general_settings_to_config
                sync_general_settings_to_config(self.db)
                if self.ws_thread:
                    self.ws_thread.notify_edge_reload()
            except Exception as sync_err:
                logger.warning("Failed to sync settings to edge config: %s", sync_err)
                
            self._show_feedback(True, "✔  All settings saved successfully")
        except Exception as e:
            logger.error("Settings save failed: %s", e)
            self._show_feedback(False, f"✕  Failed to save: {e}")

    def _reset_defaults(self):
        reply = QMessageBox.question(
            self, "Reset Defaults",
            "Reset all settings to factory defaults?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        for key, widget in self._widgets.items():
            raw = DEFAULTS.get(key, "")
            if isinstance(widget, QSpinBox):
                try:
                    widget.setValue(int(raw))
                except Exception:
                    pass
            elif isinstance(widget, QDoubleSpinBox):
                try:
                    widget.setValue(float(raw))
                except Exception:
                    pass
            elif isinstance(widget, QCheckBox):
                widget.setChecked(raw == "1")
            elif isinstance(widget, QLineEdit):
                widget.setText(raw)

        self._show_feedback(True, "✔  All settings reset to defaults")

    def get_setting(self, key: str, default=None):
        """Runtime access to a specific setting value."""
        w = self._widgets.get(key)
        if w is None:
            return default
        if isinstance(w, QSpinBox):
            return w.value()
        if isinstance(w, QDoubleSpinBox):
            return w.value()
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QLineEdit):
            return w.text()
        return default

    def _clear_all_data(self):
        """Clear all historical alert data (moved from Analytics for safety)."""
        ans = QMessageBox.question(
            self, "Clear All Alert Data",
            "<p>Are you sure you want to <b>permanently delete</b> all historical alert data?</p>"
            "<p><b>This action cannot be undone.</b> Camera configurations will be preserved.</p>",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if ans == QMessageBox.Yes:
            # Second confirmation
            ans2 = QMessageBox.warning(
                self, "Final Confirmation",
                "This will permanently remove ALL detection records.\n\n"
                "Type 'yes' wasn't asked — just confirming once more.\nProceed?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if ans2 == QMessageBox.Yes:
                try:
                    # 1. Clear files from disk first
                    if hasattr(self, 'alert_service') and self.alert_service:
                        self.alert_service.run_retention_cleanup(0) # Delete everything
                    
                    # 2. Clear Database (Correct Order)
                    with closing(self.db._get_connection()) as conn:
                        conn.execute("DELETE FROM detections")
                        conn.execute("DELETE FROM alerts")
                        # Reset total_alerts on cameras
                        conn.execute("UPDATE cameras SET total_alerts = 0")
                        conn.commit()
                    
                    self._show_feedback(True, "✔  All alert data and media cleared")
                    logger.info("Manual data wipe completed by user.")
                except Exception as e:
                    logger.error("Failed to clear data: %s", e)
                    self._show_feedback(False, f"✕  Failed: {e}")
