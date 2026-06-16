"""
cameras_screen.py — FireGuard Camera Configuration & Management (v4)

Industrial-grade redesign:
  - Unified elevated camera cards with left-edge status strip
  - Glowing LED status indicators (Online / Offline / Unknown)
  - Pill-badge for source type, no raw text borders
  - Compact stats bar with large color-coded numbers
  - Ghost card at bottom as Add Camera CTA
  - Icon-only action buttons (▶ / ✎ / ✕) with tooltips
  - Hover-reveal Delete button pattern
"""

import os
import logging
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QPushButton, QSizePolicy,
    QLineEdit, QDialog, QFormLayout, QMessageBox,
    QGraphicsDropShadowEffect, QGraphicsOpacityEffect
)
from PySide6.QtCore import Qt, QTimer, Signal, QPropertyAnimation, QEasingCurve, QRect
from PySide6.QtGui import (
    QFont, QColor, QPainter, QPen, QBrush,
    QLinearGradient, QPainterPath, QCursor
)

from server.utils.config_sync import (
    add_camera_to_config,
    update_camera_in_config,
    delete_camera_from_config,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _detect_source_type(source: str) -> str:
    s = source.strip()
    if s.isdigit():          return "USB"
    if s.startswith("rtsp://"): return "RTSP"
    if s.startswith(("http://", "https://")): return "HTTP"
    if os.path.exists(s) or s.endswith((".mp4", ".avi", ".mkv")): return "FILE"
    return "—"


_TYPE_COLORS = {
    "USB":  ("#60A5FA", "rgba(59,130,246,0.10)", "rgba(59,130,246,0.28)"),
    "RTSP": ("#34D399", "rgba(16,185,129,0.10)", "rgba(16,185,129,0.28)"),
    "HTTP": ("#A78BFA", "rgba(139,92,246,0.10)", "rgba(139,92,246,0.28)"),
    "FILE": ("#FBBF24", "rgba(251,191,36,0.10)", "rgba(251,191,36,0.28)"),
    "\u2014":    ("#9DA7B3", "rgba(157,167,179,0.08)", "rgba(157,167,179,0.2)"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Glowing Status Strip (left edge of camera card)
# ─────────────────────────────────────────────────────────────────────────────
class StatusStrip(QWidget):
    """5-px vertical colour strip indicating camera connection state."""

    STATUS_COLORS = {
        "online":  "#10B981",
        "offline": "#E3000F",
        "unknown": "#4B5563",
    }

    def __init__(self, status: str = "unknown"):
        super().__init__()
        self.setFixedWidth(5)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._status = status

    def set_status(self, status: str):
        self._status = status
        self.update()

    def paintEvent(self, _event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            color = QColor(self.STATUS_COLORS.get(self._status, "#4B5563"))
            grad = QLinearGradient(0, 0, 0, self.height())
            grad.setColorAt(0.0, color.lighter(130))
            grad.setColorAt(0.5, color)
            grad.setColorAt(1.0, color.lighter(130))
            p.setBrush(QBrush(grad))
            p.setPen(Qt.NoPen)
            path = QPainterPath()
            path.addRoundedRect(0, 8, 5, self.height() - 16, 2, 2)
            p.drawPath(path)


# ─────────────────────────────────────────────────────────────────────────────
# LED Dot (pulsing when online)
# ─────────────────────────────────────────────────────────────────────────────
class StatusDot(QLabel):
    def __init__(self, status: str = "unknown"):
        super().__init__()
        self._anim = None
        self.setFixedSize(10, 10)
        self.set_status(status)

    def set_status(self, status: str):
        if self._anim:
            self._anim.stop()
            self.setGraphicsEffect(None)
            self._anim = None

        styles = {
            "online":  ("border-radius:5px; background:#10B981;", True),
            "offline": ("border-radius:5px; background:#E3000F;", False),
            "unknown": ("border-radius:5px; background:#4B5563;", False),
        }
        style, pulse = styles.get(status, styles["unknown"])
        self.setStyleSheet(style)

        if pulse:
            fx = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(fx)
            self._anim = QPropertyAnimation(fx, b"opacity", self)
            self._anim.setDuration(1400)
            self._anim.setStartValue(1.0)
            self._anim.setEndValue(0.25)
            self._anim.setEasingCurve(QEasingCurve.Type.SineCurve)
            self._anim.setLoopCount(-1)
            self._anim.start()


# ─────────────────────────────────────────────────────────────────────────────
# Action Button — slim text label style (no emoji, always visible)
# ─────────────────────────────────────────────────────────────────────────────
def _action_btn(label: str, tooltip: str,
                fg: str = "#9DA7B3",
                hover_fg: str = "#E6EDF3",
                hover_bg: str = "rgba(255,255,255,0.08)",
                border_hover: str = "rgba(255,255,255,0.15)") -> QPushButton:
    btn = QPushButton(label)
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(30)
    btn.setMinimumWidth(52)
    btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
    btn.setStyleSheet(f"""
        QPushButton {{
            color: {fg};
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 6px;
            padding: 0 10px;
        }}
        QPushButton:hover {{
            color: {hover_fg};
            background: {hover_bg};
            border: 1px solid {border_hover};
        }}
        QPushButton:pressed {{
            background: rgba(255,255,255,0.12);
        }}
    """)
    return btn


# ─────────────────────────────────────────────────────────────────────────────
# Test Result Badge
# ─────────────────────────────────────────────────────────────────────────────
class TestResultBadge(QLabel):
    def __init__(self):
        super().__init__()
        self._reset_style()

    def _reset_style(self):
        self.setText("")
        self.setVisible(False)

    def set_ok(self, latency_ms: int):
        self.setText(f"✓ {latency_ms}ms")
        self.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.setStyleSheet(
            "color:#10B981; background:rgba(16,185,129,0.1);"
            " border:1px solid rgba(16,185,129,0.3); border-radius:5px;"
            " padding: 2px 8px;"
        )
        self.setVisible(True)
        QTimer.singleShot(6000, self._reset_style)

    def set_fail(self, reason: str = "Unreachable"):
        short = reason[:18] if len(reason) > 18 else reason
        self.setText(f"✗ {short}")
        self.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.setStyleSheet(
            "color:#E3000F; background:rgba(227,0,15,0.08);"
            " border:1px solid rgba(227,0,15,0.3); border-radius:5px;"
            " padding: 2px 8px;"
        )
        self.setVisible(True)
        QTimer.singleShot(6000, self._reset_style)

    def set_testing(self):
        self.setText("Testing…")
        self.setFont(QFont("Segoe UI", 9))
        self.setStyleSheet(
            "color:#9DA7B3; background:rgba(255,255,255,0.04);"
            " border:1px solid rgba(255,255,255,0.1); border-radius:5px;"
            " padding: 2px 8px;"
        )
        self.setVisible(True)


# ─────────────────────────────────────────────────────────────────────────────
# Add / Edit Camera Dialog
# ─────────────────────────────────────────────────────────────────────────────
class CameraDialog(QDialog):
    def __init__(self, parent=None, existing: dict = None):
        super().__init__(parent)
        is_edit = existing is not None
        self.setWindowTitle("Edit Camera" if is_edit else "Add Camera")
        self.setMinimumWidth(520)
        self.setStyleSheet("background-color: #11161C;")

        layout = QVBoxLayout(self)
        layout.setSpacing(18)
        layout.setContentsMargins(28, 24, 28, 24)

        dlg_title = QLabel("Edit Camera" if is_edit else "Add New Camera")
        dlg_title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        dlg_title.setStyleSheet("color: #E6EDF3; background: transparent;")
        layout.addWidget(dlg_title)

        accent = QFrame()
        accent.setFixedHeight(1)
        accent.setStyleSheet("background: rgba(227,0,15,0.4); border: none;")
        layout.addWidget(accent)

        form = QFormLayout()
        form.setSpacing(14)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. Entrance Camera")
        self._name_edit.setFixedHeight(40)
        self._name_edit.setFont(QFont("Segoe UI", 12))
        if existing:
            self._name_edit.setText(existing.get("name", ""))

        self._source_edit = QLineEdit()
        self._source_edit.setPlaceholderText("rtsp://... or 0 for webcam")
        self._source_edit.setFixedHeight(40)
        self._source_edit.setFont(QFont("Consolas", 11))
        if existing:
            self._source_edit.setText(existing.get("source", ""))

        name_lbl = QLabel("Camera Name")
        name_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        name_lbl.setStyleSheet("color: #9DA7B3; background: transparent;")

        src_lbl = QLabel("Stream Source")
        src_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        src_lbl.setStyleSheet("color: #9DA7B3; background: transparent;")

        form.addRow(name_lbl, self._name_edit)
        form.addRow(src_lbl, self._source_edit)
        layout.addLayout(form)

        # Stream format reference
        ref_frame = QFrame()
        ref_frame.setStyleSheet(
            "background: rgba(255,255,255,0.02); border: 1px solid #1e2530;"
            " border-radius: 8px;"
        )
        ref_layout = QVBoxLayout(ref_frame)
        ref_layout.setSpacing(6)
        ref_layout.setContentsMargins(14, 10, 14, 10)

        ref_title = QLabel("SUPPORTED FORMATS")
        ref_title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        ref_title.setStyleSheet("color: #484F58; letter-spacing: 1px; background: transparent;")
        ref_layout.addWidget(ref_title)

        formats = [
            ("RTSP", "rtsp://user:pass@192.168.1.x:554/stream"),
            ("HTTP", "http://192.168.1.x:8080/video"),
            ("File", "/path/to/video.mp4  or  C:/video.mp4"),
            ("USB",  "0  (first webcam),  1  (second), …"),
        ]
        for fmt, example in formats:
            row = QHBoxLayout()
            row.setSpacing(10)
            fg, bg, _ = _TYPE_COLORS.get(fmt.upper(), _TYPE_COLORS["—"])
            badge = QLabel(fmt)
            badge.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
            badge.setFixedWidth(44)
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(f"color:{fg}; background:{bg}; border-radius:4px; padding:2px 0;")
            ex = QLabel(example)
            ex.setFont(QFont("Consolas", 9))
            ex.setStyleSheet("color: #9DA7B3; background: transparent;")
            row.addWidget(badge)
            row.addWidget(ex)
            row.addStretch()
            ref_layout.addLayout(row)

        layout.addWidget(ref_frame)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("GhostButton")
        cancel.setFixedHeight(40)
        cancel.setMinimumWidth(100)
        cancel.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        cancel.clicked.connect(self.reject)

        self._ok = QPushButton("Save Camera" if is_edit else "Add Camera")
        self._ok.setFixedHeight(40)
        self._ok.setMinimumWidth(140)
        self._ok.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._ok.clicked.connect(self._validate)

        btn_row.addStretch()
        btn_row.addWidget(cancel)
        btn_row.addWidget(self._ok)
        layout.addLayout(btn_row)

    def _validate(self):
        if not self._name_edit.text().strip():
            self._name_edit.setFocus()
            self._name_edit.setStyleSheet("border: 2px solid #e3000f;")
            return
        self.accept()

    @property
    def name(self) -> str:
        return self._name_edit.text().strip()

    @property
    def source(self) -> str:
        return self._source_edit.text().strip()


# ─────────────────────────────────────────────────────────────────────────────
# Camera Card Row
# ─────────────────────────────────────────────────────────────────────────────
class CameraRow(QFrame):
    renamed   = Signal(int, str, str)
    deleted   = Signal(int)
    test_conn = Signal(int, str)

    def __init__(self, d: dict):
        super().__init__()
        self.cam_id     = d["id"]
        self._source    = d.get("source", "")
        self._is_online = bool(d.get("is_online", 0))

        self.setMinimumHeight(88)
        self.setMaximumHeight(88)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("CameraCard")

        self.setStyleSheet("""
            QFrame#CameraCard {
                background: #161B22;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
            }
            QFrame#CameraCard:hover {
                background: #1A2030;
                border: 1px solid rgba(255,255,255,0.18);
            }
        """)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(22)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        self._build(d)

    def _build(self, d: dict):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 20, 0)
        outer.setSpacing(0)

        # ── LEFT: colour status strip ──────────────────────────────────────
        status_str = "online" if self._is_online else "offline"
        self._strip = StatusStrip(status_str)
        outer.addWidget(self._strip)
        outer.addSpacing(18)

        # ── LED dot ───────────────────────────────────────────────────────
        self._dot = StatusDot(status_str)
        outer.addWidget(self._dot, alignment=Qt.AlignVCenter)
        outer.addSpacing(14)

        # ── Name + source block ───────────────────────────────────────────
        name_block = QVBoxLayout()
        name_block.setSpacing(4)
        name_block.setContentsMargins(0, 0, 0, 0)
        name_block.setAlignment(Qt.AlignVCenter)

        # Name row: camera name + type pill inline
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.setContentsMargins(0, 0, 0, 0)

        cam_name = d.get("name", f"Camera {d['id']}")
        # Capitalise first letter
        cam_name = cam_name[0].upper() + cam_name[1:] if cam_name else cam_name
        self._name_lbl = QLabel(cam_name)
        self._name_lbl.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        self._name_lbl.setStyleSheet("color: #E6EDF3; background: transparent;")
        name_row.addWidget(self._name_lbl)

        # Slim inline type pill
        src_type = _detect_source_type(self._source)
        fg, bg, border = _TYPE_COLORS.get(src_type, _TYPE_COLORS["\u2014"])
        self._type_pill = QLabel(src_type)
        self._type_pill.setFixedHeight(18)
        self._type_pill.setMinimumWidth(36)
        self._type_pill.setAlignment(Qt.AlignCenter)
        self._type_pill.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        self._type_pill.setStyleSheet(
            f"color:{fg}; background:{bg}; border:1px solid {border};"
            f" border-radius:9px; padding:0 6px;"
        )
        name_row.addWidget(self._type_pill)
        name_row.addStretch()
        name_block.addLayout(name_row)

        src = d.get("source", "Not configured")
        src_display = src if len(src) < 56 else src[:53] + "\u2026"
        self._src_lbl = QLabel(src_display)
        self._src_lbl.setFont(QFont("Consolas", 9))
        self._src_lbl.setStyleSheet("color: #4B5563; background: transparent;")
        self._src_lbl.setToolTip(src)
        name_block.addWidget(self._src_lbl)

        outer.addLayout(name_block, stretch=4)
        outer.addSpacing(20)

        # ── Heartbeat block ────────────────────────────────────────────────
        hb_block = QVBoxLayout()
        hb_block.setSpacing(3)
        hb_block.setContentsMargins(0, 0, 0, 0)
        hb_block.setAlignment(Qt.AlignVCenter)

        hb_lbl = QLabel("LAST SEEN")
        hb_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        hb_lbl.setStyleSheet("color: #374151; letter-spacing: 0.8px; background: transparent;")
        hb_block.addWidget(hb_lbl)

        last = d.get("last_seen", "Never")
        if last and last != "Never":
            try:
                last = datetime.fromisoformat(last).strftime("%b %d  %H:%M")
            except Exception:
                pass
        self._last_lbl = QLabel(last if last else "Never")
        self._last_lbl.setFont(QFont("Segoe UI", 10))
        self._last_lbl.setStyleSheet("color: #9DA7B3; background: transparent;")
        hb_block.addWidget(self._last_lbl)

        outer.addLayout(hb_block, stretch=2)
        outer.addSpacing(20)

        # ── Test result badge ──────────────────────────────────────────────
        self._test_badge = TestResultBadge()
        outer.addWidget(self._test_badge, alignment=Qt.AlignVCenter)
        outer.addSpacing(12)

        # ── Action buttons (text labels, always visible) ───────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.setContentsMargins(0, 0, 0, 0)

        test_btn = _action_btn("Test", "Test connection from this PC")
        edit_btn = _action_btn("Edit", "Edit camera settings")
        del_btn  = _action_btn(
            "Delete", "Remove camera",
            fg="#9B2335",
            hover_fg="#FFFFFF",
            hover_bg="rgba(227,0,15,0.25)",
            border_hover="rgba(227,0,15,0.6)"
        )

        test_btn.clicked.connect(self._on_test)
        edit_btn.clicked.connect(self._on_edit)
        del_btn.clicked.connect(self._on_delete)

        btn_row.addWidget(test_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(del_btn)
        outer.addLayout(btn_row)

    # ── Slots ──────────────────────────────────────────────────────────────
    def _on_test(self):
        self._test_badge.set_testing()
        self.test_conn.emit(self.cam_id, self._source)

    def _on_edit(self):
        dlg = CameraDialog(parent=self,
                           existing={"name": self._name_lbl.text(), "source": self._source})
        if dlg.exec() == QDialog.Accepted:
            self.renamed.emit(self.cam_id, dlg.name, dlg.source)

    def _on_delete(self):
        ans = QMessageBox.question(
            self, "Delete Camera",
            f"Remove <b>{self._name_lbl.text()}</b> from the system?<br>"
            "Historical alerts will not be deleted.",
            QMessageBox.Yes | QMessageBox.No
        )
        if ans == QMessageBox.Yes:
            self.deleted.emit(self.cam_id)

    # ── Public refresh & result setters ───────────────────────────────────
    def refresh(self, d: dict):
        online = bool(d.get("is_online", 0))
        status = "online" if online else "offline"
        self._strip.set_status(status)
        self._dot.set_status(status)

        name = d.get("name", f"Camera {d['id']}")
        self._name_lbl.setText(name)

        src = d.get("source", "Not configured")
        self._source = src
        self._src_lbl.setText(src if len(src) < 52 else src[:49] + "…")
        self._src_lbl.setToolTip(src)

        last = d.get("last_seen", "Never")
        if last and last != "Never":
            try:
                last = datetime.fromisoformat(last).strftime("%b %d  %H:%M")
            except Exception:
                pass
        self._last_lbl.setText(last)

    def set_test_result(self, ok: bool, detail: str = ""):
        if ok:
            try:
                self._test_badge.set_ok(int(detail))
            except Exception:
                self._test_badge.set_ok(0)
        else:
            self._test_badge.set_fail(detail or "Unreachable")


# ─────────────────────────────────────────────────────────────────────────────
# "Ghost" Add Camera Card (CTA at bottom of list)
# ─────────────────────────────────────────────────────────────────────────────
class GhostCameraCard(QFrame):
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedHeight(64)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            QFrame {
                background: transparent;
                border: 1px dashed rgba(255,255,255,0.10);
                border-radius: 10px;
            }
            QFrame:hover {
                border-color: rgba(227,0,15,0.50);
                background: rgba(227,0,15,0.04);
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(28, 0, 28, 0)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignVCenter)

        plus_frame = QLabel("+")
        plus_frame.setFixedSize(28, 28)
        plus_frame.setAlignment(Qt.AlignCenter)
        plus_frame.setFont(QFont("Segoe UI", 16))
        plus_frame.setStyleSheet(
            "color: rgba(255,255,255,0.25); background: rgba(255,255,255,0.04);"
            " border: 1px solid rgba(255,255,255,0.08); border-radius: 14px;"
        )
        layout.addWidget(plus_frame)

        cta_lbl = QLabel("Add another camera")
        cta_lbl.setFont(QFont("Segoe UI", 11))
        cta_lbl.setStyleSheet("color: rgba(255,255,255,0.22); background: transparent;")
        layout.addWidget(cta_lbl)
        layout.addStretch()

    def mousePressEvent(self, _event):
        self.clicked.emit()


# ─────────────────────────────────────────────────────────────────────────────
# Cameras Management Screen
# ─────────────────────────────────────────────────────────────────────────────
class CamerasScreen(QWidget):
    camera_deleted = Signal(int)
    camera_renamed = Signal(int, str)

    def __init__(self, db, alert_service=None):
        super().__init__()
        self.db = db
        self.alert_service = alert_service
        self.ws_thread = None
        self._rows: dict[int, CameraRow] = {}
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(6000)
        self._refresh()

    # ── Edge reload ────────────────────────────────────────────────────────
    def _notify_edge_reload(self):
        try:
            if self.ws_thread:
                self.ws_thread.notify_edge_reload()
        except Exception as e:
            logger.warning("Edge reload notification failed: %s", e)

    # ── Setup UI ───────────────────────────────────────────────────────────
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        content = QWidget()
        content.setObjectName("ContentArea")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(20)

        # ── Page Header ────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(14)

        title_col = QVBoxLayout()
        title_col.setSpacing(5)

        title = QLabel("Camera Configuration")
        title.setObjectName("PageTitle")
        title.setFont(QFont("Segoe UI", 26, QFont.Weight.Bold))
        title.setStyleSheet("color: #E6EDF3; background: transparent;")

        sub = QLabel("Add, remove, and configure cameras  •  Test connections")
        sub.setObjectName("PageSubtitle")
        sub.setFont(QFont("Segoe UI", 11))
        sub.setStyleSheet("color: #6B7280; background:transparent;")

        title_col.addWidget(title)
        title_col.addWidget(sub)
        hdr.addLayout(title_col)
        hdr.addStretch()

        # Right controls — vertically centered
        controls = QHBoxLayout()
        controls.setSpacing(10)
        controls.setAlignment(Qt.AlignVCenter)

        add_btn = QPushButton("＋  Add Camera")
        add_btn.setFixedHeight(38)
        add_btn.setMinimumWidth(136)
        add_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add_camera)

        self._refresh_btn = QPushButton("↺  Refresh")
        self._refresh_btn.setObjectName("GhostButton")
        self._refresh_btn.setFixedHeight(38)
        self._refresh_btn.setMinimumWidth(100)
        self._refresh_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)

        controls.addWidget(add_btn)
        controls.addWidget(self._refresh_btn)
        hdr.addLayout(controls)

        layout.addLayout(hdr)

        # Red accent line
        accent = QFrame()
        accent.setFixedHeight(1)
        accent.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            " stop:0 #FF3B30, stop:0.35 rgba(255,59,48,50), stop:1 transparent);"
            " border: none;"
        )
        layout.addWidget(accent)

        # ── Stats Cards ────────────────────────────────────────────────────
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)

        def _stat_cell(label: str, value: str, accent_color: str, bg_tint: str):
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                                                stop:0 {bg_tint}, stop:1 rgba(22, 27, 34, 0.8));
                    border: 1px solid rgba(255,255,255,0.08);
                    border-radius: 12px;
                    border-top: 3px solid {accent_color};
                }}
                QFrame:hover {{
                    border: 1px solid rgba(255,255,255,0.15);
                    border-top: 3px solid {accent_color};
                }}
            """)
            shadow = QGraphicsDropShadowEffect()
            shadow.setBlurRadius(20)
            shadow.setColor(QColor(0, 0, 0, 80))
            shadow.setOffset(0, 4)
            card.setGraphicsEffect(shadow)

            cell_layout = QVBoxLayout(card)
            cell_layout.setContentsMargins(24, 16, 24, 16)
            cell_layout.setSpacing(4)

            # Top label
            sub_lbl = QLabel(label)
            sub_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            sub_lbl.setStyleSheet(f"color: {accent_color}; background: transparent; letter-spacing: 1px; border: none;")
            cell_layout.addWidget(sub_lbl)

            # Bottom value
            val_lbl = QLabel(value)
            val_lbl.setFont(QFont("Segoe UI", 32, QFont.Weight.Bold))
            val_lbl.setStyleSheet("color: white; background: transparent; border: none;")
            cell_layout.addWidget(val_lbl)

            return card, val_lbl

        c1, self._lbl_total   = _stat_cell("TOTAL CAMERAS", "0", "#60A5FA", "rgba(96,165,250,0.05)")
        c2, self._lbl_online  = _stat_cell("ONLINE", "0", "#10B981", "rgba(16,185,129,0.05)")
        c3, self._lbl_offline = _stat_cell("OFFLINE", "0", "#E3000F", "rgba(227,0,15,0.05)")

        stats_layout.addWidget(c1)
        stats_layout.addWidget(c2)
        stats_layout.addWidget(c3)

        layout.addLayout(stats_layout)

        # ── Camera rows container ──────────────────────────────────────────
        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(8)
        layout.addWidget(self._rows_container)

        # Ghost "Add Another Camera" CTA card
        self._ghost_card = GhostCameraCard()
        self._ghost_card.clicked.connect(self._add_camera)
        self._ghost_card.setVisible(False)
        layout.addWidget(self._ghost_card)

        # ── Full Empty State ───────────────────────────────────────────────
        self._empty_widget = QFrame()
        self._empty_widget.setObjectName("SectionCard")
        self._empty_widget.setMinimumHeight(200)
        empty_inner = QVBoxLayout(self._empty_widget)
        empty_inner.setAlignment(Qt.AlignCenter)
        empty_inner.setSpacing(16)
        empty_inner.setContentsMargins(40, 48, 40, 48)

        e_icon = QLabel("⊕")
        e_icon.setFont(QFont("Segoe UI", 36))
        e_icon.setAlignment(Qt.AlignCenter)
        e_icon.setStyleSheet("color: rgba(255,255,255,0.08); background: transparent;")

        e_title = QLabel("No cameras registered")
        e_title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        e_title.setStyleSheet("color: #374151; background: transparent;")
        e_title.setAlignment(Qt.AlignCenter)

        e_sub = QLabel(
            "Click  '+ Add Camera'  to register a new camera source.\n"
            "Supports RTSP streams, USB webcams, and local video files."
        )
        e_sub.setFont(QFont("Segoe UI", 11))
        e_sub.setStyleSheet("color: #374151; background: transparent;")
        e_sub.setAlignment(Qt.AlignCenter)

        e_btn = QPushButton("＋  Add Your First Camera")
        e_btn.setFixedSize(230, 42)
        e_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        e_btn.setCursor(Qt.PointingHandCursor)
        e_btn.clicked.connect(self._add_camera)

        empty_inner.addWidget(e_icon)
        empty_inner.addWidget(e_title)
        empty_inner.addWidget(e_sub)
        empty_inner.addSpacing(4)
        empty_inner.addWidget(e_btn, alignment=Qt.AlignCenter)

        layout.addWidget(self._empty_widget)
        layout.addStretch()

        scroll.setWidget(content)
        root.addWidget(scroll)

    # ── Refresh ────────────────────────────────────────────────────────────
    def _on_refresh_clicked(self):
        try:
            # Force a full sync of DB to config.yaml on refresh
            from server.utils.config_sync import sync_cameras_to_config
            sync_cameras_to_config(self.db)
            self._notify_edge_reload()
            
            self._refresh()
            self._refresh_btn.setText("✓  Synced")
            self._refresh_btn.setStyleSheet("color: #10B981;")
            QTimer.singleShot(1500, lambda: self._refresh_btn.setText("↺  Refresh"))
            QTimer.singleShot(1500, lambda: self._refresh_btn.setStyleSheet(""))
        except Exception as e:
            logger.error("Manual refresh sync failed: %s", e)
            self._refresh()

    def _refresh(self):
        try:
            cams = self.db.get_cameras()
            online = 0
            # Track which IDs we saw in this refresh
            current_ids = set()
            
            for d in cams:
                cid = d["id"]
                current_ids.add(cid)
                if d.get("is_online"):
                    online += 1
                
                if cid in self._rows:
                    try:
                        self._rows[cid].refresh(d)
                    except Exception as e:
                        logger.error(f"Error refreshing camera row {cid}: {e}")
                else:
                    try:
                        row = CameraRow(d)
                        row.renamed.connect(self._rename_camera)
                        row.deleted.connect(self._delete_camera)
                        row.test_conn.connect(self._test_connection)
                        self._rows[cid] = row
                        self._rows_layout.addWidget(row)
                    except Exception as e:
                        logger.error(f"Error creating camera row {cid}: {e}")

            # Cleanup rows that are no longer in DB
            for cid in list(self._rows.keys()):
                if cid not in current_ids:
                    try:
                        widget = self._rows.pop(cid)
                        self._rows_layout.removeWidget(widget)
                        widget.deleteLater()
                    except Exception as e:
                        logger.error(f"Error removing stale camera row {cid}: {e}")

            total = len(cams)
            self._lbl_total.setText(str(total))
            self._lbl_online.setText(str(online))
            self._lbl_offline.setText(str(total - online))
            self._empty_widget.setVisible(total == 0)
            self._rows_container.setVisible(total > 0)
            # Show ghost "Add Another" card only when there's at least one camera
            self._ghost_card.setVisible(total > 0)

        except Exception as e:
            logger.error("Camera list refresh failed: %s", e)

    # ── Actions ────────────────────────────────────────────────────────────
    def _add_camera(self):
        dlg = CameraDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            try:
                cam_id = self.db.add_camera(name=dlg.name, source=dlg.source)
                try:
                    add_camera_to_config(cam_id, dlg.name, dlg.source)
                    logger.info("Camera %d synced to config.yaml", cam_id)
                except Exception as cfg_err:
                    logger.warning("Config sync failed: %s", cfg_err)
                self._notify_edge_reload()
                self._refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to add camera:\n{e}")

    def _rename_camera(self, cam_id: int, new_name: str, new_source: str):
        try:
            self.db.update_camera(cam_id, name=new_name, source=new_source)
            try:
                update_camera_in_config(cam_id, name=new_name, source=new_source)
                logger.info("Camera %d update synced to config.yaml", cam_id)
            except Exception as cfg_err:
                logger.warning("Config sync failed: %s", cfg_err)
            self._notify_edge_reload()
            self.camera_renamed.emit(cam_id, new_name)
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to update camera:\n{e}")

    def _delete_camera(self, cam_id: int):
        try:
            self.db.delete_camera(cam_id)
            try:
                delete_camera_from_config(cam_id)
                logger.info("Camera %d removed from config.yaml", cam_id)
            except Exception as cfg_err:
                logger.warning("Config sync failed: %s", cfg_err)
            if cam_id in self._rows:
                widget = self._rows.pop(cam_id)
                self._rows_layout.removeWidget(widget)
                widget.deleteLater()
            self._notify_edge_reload()
            self.camera_deleted.emit(cam_id)
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to delete camera:\n{e}")

    def _test_connection(self, cam_id: int, source: str):
        import threading, time

        def _do_test():
            ok = False
            detail = "Unreachable"
            start = time.time()
            try:
                if source.strip().isdigit():
                    import cv2
                    cap = cv2.VideoCapture(int(source.strip()))
                    if cap.isOpened():
                        ok = True
                        detail = str(int((time.time() - start) * 1000))
                        cap.release()
                    else:
                        detail = "Webcam not found"
                elif source.startswith(("rtsp://", "http://", "https://")):
                    import socket, urllib.parse
                    parsed = urllib.parse.urlparse(source)
                    host = parsed.hostname
                    port = parsed.port or (554 if "rtsp" in source else 80)
                    sock = socket.create_connection((host, port), timeout=3)
                    sock.close()
                    ok = True
                    detail = str(int((time.time() - start) * 1000))
                elif os.path.exists(source):
                    ok = True
                    detail = "0"
                else:
                    detail = "File not found"
            except Exception as ex:
                detail = str(ex)[:40]

            if cam_id in self._rows:
                row = self._rows[cam_id]
                if ok:
                    row.set_test_result(True, detail)
                else:
                    row.set_test_result(False, detail)

        threading.Thread(target=_do_test, daemon=True).start()

    def update_camera_status(self, cam_id: int, online: bool):
        if cam_id in self._rows:
            status = "online" if online else "offline"
            self._rows[cam_id]._strip.set_status(status)
            self._rows[cam_id]._dot.set_status(status)
            self._refresh()
