"""
dashboard_screen.py — FireGuard Live Dashboard (Enterprise v5)

Features:
  - Animated radar scanner widget (smaller, right-aligned)
  - Detection WARNING banner — auto-shows on fire/smoke detection
  - SOUND ALARM button — subdued in idle, vivid during alerts
  - 4 KPI summary cards (cameras, alerts, critical, unacknowledged)
  - System uptime counter
  - Last-incident timestamp with severity color
  - Quick-action row: View Latest Alert, Export Report, Refresh
  - Multi-camera live feed grid
  - System health CPU/RAM bars (psutil)
  - Red alert ticker at bottom
"""

import os
import math
import time
import csv
import logging
import threading
from datetime import datetime
from collections import deque
from typing import Dict

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QGridLayout, QSizePolicy, QScrollArea, QFrame,
    QPushButton, QMessageBox, QFileDialog, QGraphicsDropShadowEffect,
    QProgressBar, QGraphicsOpacityEffect
)
from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import (
    QPixmap, QImage, QFont, QPainter, QColor, QPen,
    QLinearGradient, QRadialGradient, QBrush, QPainterPath
)

logger = logging.getLogger(__name__)

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False


# ─────────────────────────────────────────────────────────────────────────────
# Animated Radar Scanner Widget
# ─────────────────────────────────────────────────────────────────────────────
class RadarWidget(QWidget):
    """Rotating radar sweep — visual system-active indicator."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(100, 100)
        self._angle   = 0.0
        self._blips   = []
        self._alert   = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)

    def set_alert(self, alert: bool):
        self._alert = alert
        self.update()

    def add_blip(self, angle_deg: float, dist_pct: float):
        self._blips.append([angle_deg, dist_pct, 1.0])
        if len(self._blips) > 6:
            self._blips.pop(0)

    def _tick(self):
        self._angle = (self._angle + 2.5) % 360
        for b in self._blips:
            b[2] = max(0.0, b[2] - 0.007)
        self._blips = [b for b in self._blips if b[2] > 0]
        self.update()

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            w, h = self.width(), self.height()
            cx, cy = w // 2, h // 2
            r = min(cx, cy) - 6

            fg = QColor("#e3000f")
            ring_color = QColor(227, 0, 15, 40 if not self._alert else 80)
            sweep_color = QColor(227, 0, 15, 100)

            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor("#11161C")))
            p.drawEllipse(QRectF(cx - r - 4, cy - r - 4, (r + 4) * 2, (r + 4) * 2))

            p.setPen(QPen(ring_color, 0.8))
            p.setBrush(Qt.NoBrush)
            for frac in [0.33, 0.66, 1.0]:
                rr = r * frac
                p.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))

            cross_c = QColor(227, 0, 15, 25)
            p.setPen(QPen(cross_c, 0.8))
            p.drawLine(cx - r, cy, cx + r, cy)
            p.drawLine(cx, cy - r, cx, cy + r)

            span = 60
            sweep_path = QPainterPath()
            sweep_path.moveTo(cx, cy)
            sweep_path.arcTo(QRectF(cx - r, cy - r, r * 2, r * 2), -self._angle, -span)
            sweep_path.closeSubpath()

            sweep_grad = QRadialGradient(cx, cy, r)
            sweep_grad.setColorAt(0.0, QColor(227, 0, 15, 0))
            sweep_grad.setColorAt(0.8, sweep_color)
            sweep_grad.setColorAt(1.0, QColor(227, 0, 15, 0))
            p.setBrush(QBrush(sweep_grad))
            p.setPen(Qt.NoPen)
            p.drawPath(sweep_path)

            arm_rad = math.radians(-self._angle)
            ax = cx + r * math.cos(arm_rad)
            ay = cy + r * math.sin(arm_rad)
            p.setPen(QPen(fg, 1.5))
            p.drawLine(cx, cy, int(ax), int(ay))

            for bangle, bdist, fade in self._blips:
                brad = math.radians(-bangle)
                bx = cx + (r * bdist) * math.cos(brad)
                by = cy + (r * bdist) * math.sin(brad)
                bc = QColor(0, 255, 80, int(220 * fade))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(bc))
                p.drawEllipse(QRectF(bx - 3, by - 3, 6, 6))

            p.setBrush(QBrush(fg))
            p.drawEllipse(QRectF(cx - 3, cy - 3, 6, 6))


# ─────────────────────────────────────────────────────────────────────────────
# Detection Warning Banner
# ─────────────────────────────────────────────────────────────────────────────
class DetectionWarningBanner(QWidget):
    dismissed = Signal()

    def __init__(self):
        super().__init__()
        self._visible   = False
        self._color_alpha = 0.85
        
        self.setFixedHeight(0) # Start height 0 for animation
        self.setVisible(False)
        self.setAttribute(Qt.WA_StyledBackground, True)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(16)

        # Pulsing warning icon
        self._icon_lbl = QLabel("⚠")
        self._icon_lbl.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        self._icon_lbl.setStyleSheet("color: white; background: transparent; border: none;")
        layout.addWidget(self._icon_lbl)

        # Message label
        self._msg_lbl = QLabel("")
        self._msg_lbl.setFont(QFont("Segoe UI", 12, QFont.Weight.ExtraBold))
        self._msg_lbl.setStyleSheet("color: white; background: transparent; letter-spacing: 1.2px;")
        layout.addWidget(self._msg_lbl, stretch=1)

        # Dismiss button
        dismiss = QPushButton("DISMISS")
        dismiss.setCursor(Qt.PointingHandCursor)
        dismiss.setFixedSize(110, 32)
        dismiss.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        dismiss.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: 1px solid white;
                border-radius: 4px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.3);
            }
        """)
        dismiss.clicked.connect(self._dismiss)
        layout.addWidget(dismiss)

        # ── Animations ────────────────────────────────────────────────────────
        # Height animation for slide-down
        self._height_anim = QPropertyAnimation(self, b"minimumHeight", self)
        self._height_anim.setDuration(400)
        self._height_anim.setEasingCurve(QEasingCurve.OutCubic)
        
        # Pulsing color timer
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._update_pulse)
        self._pulse_dir = -1
        
        # Auto-dismiss timer
        self._auto_dismiss_timer = QTimer(self)
        self._auto_dismiss_timer.setSingleShot(True)
        self._auto_dismiss_timer.timeout.connect(self._dismiss)

    def _update_pulse(self):
        step = 0.05
        self._color_alpha += (step * self._pulse_dir)
        if self._color_alpha <= 0.3:
            self._color_alpha = 0.3
            self._pulse_dir = 1
        elif self._color_alpha >= 0.85:
            self._color_alpha = 0.85
            self._pulse_dir = -1
            
        self.setStyleSheet(f"background: rgba(220, 38, 38, {self._color_alpha});")

    def show_event(self, cam_name: str, label: str, threat: str, confidence: float):
        msg = f"[{threat}] DETECTION — {cam_name.upper()} → {label.upper()} ({confidence * 100:.0f}%)"
        self._msg_lbl.setText(msg)
        
        if not self._visible:
            self._visible = True
            self.setVisible(True)
            self._height_anim.setStartValue(0)
            self._height_anim.setEndValue(64)
            self._height_anim.start()
            self._pulse_timer.start(50)
            
        # Reset 15s timer on each new alert
        self._auto_dismiss_timer.start(15000)

    def _dismiss(self):
        if not self._visible: return
        self._visible = False
        self._auto_dismiss_timer.stop()
        self._pulse_timer.stop()
        
        # Slide up animation
        self._height_anim.setStartValue(self.height())
        self._height_anim.setEndValue(0)
        self._height_anim.start()
        # Hide after animation ends
        QTimer.singleShot(400, lambda: self.setVisible(False) if not self._visible else None)
        self.dismissed.emit()


# ─────────────────────────────────────────────────────────────────────────────
# Alarm Button — Subdued in idle, vivid during alarm
# ─────────────────────────────────────────────────────────────────────────────
class AlarmButton(QPushButton):
    def __init__(self):
        super().__init__()
        self._alarming     = False
        self._alarm_thread = None
        self._stop_flag    = threading.Event()
        self._flash_on     = False

        self.setFixedHeight(44)
        self.setMinimumWidth(160)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self._set_idle_style()
        self.clicked.connect(self._toggle_alarm)

        self._flash_timer = QTimer(self)
        self._flash_timer.timeout.connect(self._flash_ui)

    def _set_idle_style(self):
        """Subdued ghost style when no alarm is active."""
        self.setText("Sound Alarm")
        self.setStyleSheet(
            "QPushButton { background: rgba(227,0,15,0.08);"
            " color: #9DA7B3; border: 1px solid rgba(227,0,15,0.2);"
            " border-radius: 8px; padding: 0 20px; }"
            "QPushButton:hover { background: rgba(227,0,15,0.15);"
            " color: #e3000f; border-color: rgba(227,0,15,0.5); }"
        )

    def _set_active_style(self, flash: bool):
        bg = "#ff0000" if flash else "#cc0000"
        self.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: white;"
            f" border: 2px solid #ff8888; border-radius: 8px;"
            f" padding: 0 20px; }}"
        )
        self.setText("■  STOP ALARM")

    def _toggle_alarm(self):
        if self._alarming:
            self._stop_alarm()
        else:
            self._start_alarm()

    def _start_alarm(self):
        self._alarming = True
        self._stop_flag.clear()
        self._flash_timer.start(350)

        if HAS_WINSOUND:
            def _play_loop():
                while not self._stop_flag.is_set():
                    try:
                        winsound.PlaySound(
                            "SystemExclamation",
                            winsound.SND_ALIAS | winsound.SND_NOSTOP
                        )
                        time.sleep(0.9)
                    except Exception:
                        break
            self._alarm_thread = threading.Thread(target=_play_loop, daemon=True)
            self._alarm_thread.start()

    def _stop_alarm(self):
        self._alarming = False
        self._stop_flag.set()
        self._flash_timer.stop()
        self._set_idle_style()

    def _flash_ui(self):
        self._flash_on = not self._flash_on
        self._set_active_style(self._flash_on)


# ─────────────────────────────────────────────────────────────────────────────
# Progress Bar
# ─────────────────────────────────────────────────────────────────────────────
class AmdProgressBar(QWidget):
    def __init__(self, color_hex: str):
        super().__init__()
        self.setFixedHeight(10)
        self._val   = 0
        self._color = QColor(color_hex)
        self._base  = color_hex

    def setValue(self, val: int):
        self._val = min(100, max(0, val))
        if self._base == "#238636":
            if self._val > 85:
                self._color = QColor("#e3000f")
            elif self._val > 60:
                self._color = QColor("#D29922")
            else:
                self._color = QColor("#238636")
        self.update()

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.NoPen)
            w, h, r = self.width(), self.height(), 5

            p.setBrush(QBrush(QColor("#11161C")))
            p.drawRoundedRect(0, 0, w, h, r, r)

            fw = max(r * 2, int((self._val / 100.0) * w))
            grad = QLinearGradient(0, 0, fw, 0)
            grad.setColorAt(0, self._color.darker(130))
            grad.setColorAt(0.5, self._color)
            grad.setColorAt(1, self._color.lighter(120))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(0, 0, fw, h, r, r)

            if fw > r * 2:
                glow = QLinearGradient(0, 0, 0, h)
                glow.setColorAt(0, QColor(255, 255, 255, 40))
                glow.setColorAt(0.5, QColor(255, 255, 255, 0))
                p.setBrush(QBrush(glow))
                p.drawRoundedRect(0, 0, fw, h // 2, r, r)


# ─────────────────────────────────────────────────────────────────────────────
# KPI Summary Card
# ─────────────────────────────────────────────────────────────────────────────
class KpiSummaryCard(QFrame):
    def __init__(self, title: str, icon_color: str = "#e3000f"):
        super().__init__()
        self.setObjectName("KpiCard")
        self.setMinimumWidth(150)
        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._icon_color = icon_color

        self.setStyleSheet(
            "background: rgba(255,255,255,8);"
            "border: 1px solid rgba(255,255,255,15);"
            "border-radius: 12px;"
        )

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(0, 0, 0, 100))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 14)
        layout.setSpacing(4)

        # Top accent bar
        accent = QFrame()
        accent.setFixedHeight(3)
        accent.setStyleSheet(f"background-color: {icon_color}; border: none; border-bottom-left-radius: 0; border-bottom-right-radius: 0; border-top-left-radius: 12px; border-top-right-radius: 12px;")
        layout.addWidget(accent)
        layout.addSpacing(10)

        title_lbl = QLabel(title.upper())
        title_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        title_lbl.setStyleSheet("color: #9DA7B3; letter-spacing: 1px; background: transparent; border: none;")
        layout.addWidget(title_lbl)

        self._val_lbl = QLabel("—")
        self._val_lbl.setStyleSheet(f"font-family: 'JetBrains Mono', 'Courier New', monospace; font-size: 26px; font-weight: 700; color: {icon_color}; background: transparent; border: none;")
        layout.addWidget(self._val_lbl)

        self._note_lbl = QLabel("")
        self._note_lbl.setFont(QFont("Segoe UI", 10))
        self._note_lbl.setStyleSheet("color: #484F58; background: transparent;")
        layout.addWidget(self._note_lbl)
        layout.addStretch()

    def set_value(self, val: str, note: str = ""):
        self._val_lbl.setText(val)
        self._note_lbl.setText(note)


# ─────────────────────────────────────────────────────────────────────────────
# Camera Feed Widget
# ─────────────────────────────────────────────────────────────────────────────
class CameraFeedWidget(QWidget):
    def __init__(self, cam_id: int, cam_name: str):
        super().__init__()
        self.cam_id   = cam_id
        self.cam_name = cam_name
        self.setObjectName("CameraFeedCard")
        self.setMinimumSize(640, 360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # Outer container styling
        self.setStyleSheet("""
            #CameraFeedCard { 
                background: #0a0f1a; 
                border: 2px solid #000000; 
                border-radius: 8px; 
            }
        """)

        # Main layout
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # The video frame container
        self.frame_container = QWidget()
        self.frame_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.frame_layout = QGridLayout(self.frame_container)
        self.frame_layout.setContentsMargins(0, 0, 0, 0)

        self._frame_lbl = QLabel()
        self._frame_lbl.setAlignment(Qt.AlignCenter)
        self._frame_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._frame_lbl.setStyleSheet("background: transparent;")
        self.frame_layout.addWidget(self._frame_lbl, 0, 0)

        self.main_layout.addWidget(self.frame_container)

        # 3. Camera label header (Overlay Top Center)
        self._name_overlay = QLabel(cam_name.upper(), self)
        self._name_overlay.setAlignment(Qt.AlignCenter)
        self._name_overlay.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._name_overlay.setStyleSheet("""
            color: rgba(255, 255, 255, 0.9);
            background: rgba(0, 0, 0, 0.5);
            padding: 4px 16px;
            border-bottom-left-radius: 8px;
            border-bottom-right-radius: 8px;
            letter-spacing: 1px;
        """)

        # 4. Live feed watermark (Overlay Center)
        self._watermark = QLabel("[ LIVE FEED ]", self)
        self._watermark.setAlignment(Qt.AlignCenter)
        self._watermark.setFont(QFont("Segoe UI", 28, QFont.Weight.Black))
        self._watermark.setStyleSheet("color: rgba(200, 200, 200, 0.12); background: transparent;")
        self._watermark.setAttribute(Qt.WA_TransparentForMouseEvents)

        # 5. Bottom status bar overlay
        self.bottom_bar = QFrame(self)
        self.bottom_bar.setFixedHeight(30)
        self.bottom_bar.setStyleSheet("background: rgba(0, 0, 0, 0.7); border: none; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px;")
        bottom_layout = QHBoxLayout(self.bottom_bar)
        bottom_layout.setContentsMargins(12, 0, 12, 0)

        # Left: ● REC
        self._rec_lbl = QLabel("● REC")
        self._rec_lbl.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        self._rec_lbl.setStyleSheet("color: #dc2626; background: transparent;")
        bottom_layout.addWidget(self._rec_lbl)

        bottom_layout.addStretch()

        # Center: FPS
        self._fps_lbl = QLabel("FPS: 0")
        self._fps_lbl.setFont(QFont("Consolas", 9))
        self._fps_lbl.setStyleSheet("color: white; background: transparent;")
        bottom_layout.addWidget(self._fps_lbl)

        bottom_layout.addStretch()

        # Right: RTT
        self._rtt_lbl = QLabel("RTT: --ms")
        self._rtt_lbl.setFont(QFont("Consolas", 9))
        self._rtt_lbl.setStyleSheet("color: white; background: transparent;")
        bottom_layout.addWidget(self._rtt_lbl)

        # 6. Severity badge (Bottom-left corner, above bottom bar)
        self._severity_badge = QLabel("CRITICAL", self)
        self._severity_badge.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._severity_badge.setStyleSheet("""
            background: #dc2626;
            color: white;
            padding: 3px 10px;
            border-radius: 10px;
        """)
        self._severity_badge.hide()

        # Animation for blinking REC
        self._rec_timer = QTimer(self)
        self._rec_timer.timeout.connect(self._toggle_rec)
        self._rec_timer.start(1000)
        self._rec_visible = True

        self._frame_count = 0
        self._last_fps_ts = time.time()
        self._is_offline = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._name_overlay.adjustSize()
        self._name_overlay.move((self.width() - self._name_overlay.width()) // 2, 0)
        self._watermark.setGeometry(0, 0, self.width(), self.height())
        self.bottom_bar.setGeometry(0, self.height() - 30, self.width(), 30)
        self._severity_badge.adjustSize()
        self._severity_badge.move(12, self.height() - 30 - self._severity_badge.height() - 8)

    def set_camera_name(self, name: str):
        if name != self.cam_name:
            self.cam_name = name
            self._name_overlay.setText(name.upper())
            self._name_overlay.adjustSize()
            self._name_overlay.move((self.width() - self._name_overlay.width()) // 2, 0)

    def closeEvent(self, event):
        self._rec_timer.stop()
        super().closeEvent(event)

    def _toggle_rec(self):
        self._rec_visible = not self._rec_visible
        opacity = "0.9" if self._rec_visible else "0.0"
        self._rec_lbl.setStyleSheet(f"color: rgba(220, 38, 38, {opacity}); background: transparent;")

    def update_frame(self, jpeg_bytes: bytes, is_alert: bool = False, metadata: dict = None):
        if not jpeg_bytes:
            return
            
        self._is_offline = False
        img = QImage()
        img.loadFromData(jpeg_bytes)
        if not img.isNull():
            pix = QPixmap.fromImage(img)
            # Ensure we have a valid label size
            lbl_size = self._frame_lbl.size()
            if lbl_size.width() > 0 and lbl_size.height() > 0:
                self._frame_lbl.setPixmap(
                    pix.scaled(lbl_size,
                               Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
                )

        # Update RTT from metadata if possible
        if metadata and "ts" in metadata:
            try:
                latency = (time.time() - float(metadata["ts"])) * 1000
                self._rtt_lbl.setText(f"RTT: {latency:.0f}ms")
            except (ValueError, TypeError):
                self._rtt_lbl.setText("RTT: ERR")

        self._frame_count += 1
        now = time.time()
        if now - self._last_fps_ts >= 1.0:
            fps = self._frame_count / (now - self._last_fps_ts)
            self._fps_lbl.setText(f"FPS: {fps:.0f}")
            self._frame_count = 0
            self._last_fps_ts = now

        if is_alert:
            self._severity_badge.show()
            self.setStyleSheet("#CameraFeedCard { background: #0a0f1a; border: 2px solid #dc2626; border-radius: 8px; }")
        else:
            self._severity_badge.hide()
            self.setStyleSheet("#CameraFeedCard { background: #0a0f1a; border: 2px solid #000000; border-radius: 8px; }")

    def set_offline(self):
        self._is_offline = True
        self._frame_lbl.setPixmap(QPixmap()) # Clear the last frame
        self._frame_lbl.setText("FEED DISCONNECTED")
        self._frame_lbl.setStyleSheet("color: #484F58; font-family: 'Segoe UI'; font-weight: bold; font-size: 18px;")
        self._fps_lbl.setText("FPS: 0")
        self._rtt_lbl.setText("RTT: --ms")
        self._severity_badge.hide()
        self.setStyleSheet("#CameraFeedCard { background: #0a0f1a; border: 2px solid #000000; border-radius: 8px; }")

# ─────────────────────────────────────────────────────────────────────────────
# Dashboard Screen
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Top Health Pill Widget
# ─────────────────────────────────────────────────────────────────────────────
class HealthPill(QWidget):
    def __init__(self, title: str, color_hex: str):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.lbl = QLabel(title)
        self.lbl.setAlignment(Qt.AlignCenter)
        self.lbl.setFont(QFont("Segoe UI", 9))
        self.lbl.setStyleSheet("color: #9DA7B3; background: transparent;")

        self.bar = QProgressBar()
        self.bar.setFixedHeight(24)
        self.bar.setTextVisible(True)
        self.bar.setAlignment(Qt.AlignCenter)
        self.bar.setStyleSheet(f"""
            QProgressBar {{
                background: #1B212B;
                border: 1px solid #2B3139;
                border-radius: 8px;
                color: #FFFFFF;
                font-weight: bold;
                font-size: 11px;
            }}
            QProgressBar::chunk {{
                background-color: {color_hex};
                border-radius: 8px;
            }}
        """)
        self.bar.setValue(0)

        layout.addWidget(self.lbl)
        layout.addWidget(self.bar)

    def set_value(self, val: int):
        self.bar.setValue(val)


# ─────────────────────────────────────────────────────────────────────────────
# Jetson Status Card
# ─────────────────────────────────────────────────────────────────────────────
class JetsonStatusCard(QFrame):
    retry_clicked = Signal()
    config_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("JetsonStatusCard")
        self.setFixedHeight(80)
        self.setStyleSheet("""
            #JetsonStatusCard {
                background: #0f1923;
                border: 1px solid #1e2d3d;
                border-radius: 10px;
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(24)

        # Left: Icon + Label + Subtext
        left_layout = QVBoxLayout()
        left_layout.setSpacing(2)
        left_layout.setAlignment(Qt.AlignVCenter)
        
        top_left = QHBoxLayout()
        top_left.setSpacing(10)
        self.icon_lbl = QLabel("⬡")
        self.icon_lbl.setStyleSheet("color: #00b4d8; font-size: 22px; font-weight: bold; background: transparent;")
        top_left.addWidget(self.icon_lbl)
        
        self.title_lbl = QLabel("JETSON NANO")
        self.title_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.title_lbl.setStyleSheet("color: white; background: transparent; letter-spacing: 0.5px;")
        top_left.addWidget(self.title_lbl)
        top_left.addStretch()
        left_layout.addLayout(top_left)
        
        self.sub_lbl = QLabel("Edge Inference Device")
        self.sub_lbl.setFont(QFont("Segoe UI", 9))
        self.sub_lbl.setStyleSheet("color: #64748b; background: transparent;")
        left_layout.addWidget(self.sub_lbl)
        layout.addLayout(left_layout)

        # Center: Pulsing dot + Status text
        center_layout = QHBoxLayout()
        center_layout.setSpacing(12)
        center_layout.setAlignment(Qt.AlignVCenter)
        
        self.status_dot = QLabel("●")
        self.status_dot.setFont(QFont("Segoe UI", 16))
        center_layout.addWidget(self.status_dot)
        
        self.status_text = QLabel("Disconnected")
        self.status_text.setFont(QFont("Segoe UI", 10))
        self.status_text.setStyleSheet("color: #64748b; background: transparent;")
        center_layout.addWidget(self.status_text)
        center_layout.addStretch()
        layout.addLayout(center_layout, stretch=1)

        # Right: Buttons / Spinner
        self.btn_layout = QHBoxLayout()
        self.btn_layout.setSpacing(12)
        self.btn_layout.setAlignment(Qt.AlignVCenter)
        
        self.spinner = QProgressBar()
        self.spinner.setRange(0, 0)
        self.spinner.setFixedSize(120, 6)
        self.spinner.setStyleSheet("""
            QProgressBar { background: rgba(0,0,0,0.2); border: none; border-radius: 3px; }
            QProgressBar::chunk { background: #00b4d8; border-radius: 3px; }
        """)
        self.spinner.hide()
        self.btn_layout.addWidget(self.spinner)

        self.retry_btn = QPushButton("↺  RETRY")
        self.retry_btn.setFixedSize(110, 34)
        self.retry_btn.setCursor(Qt.PointingHandCursor)
        self.retry_btn.clicked.connect(self.retry_clicked.emit)
        self.retry_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #00b4d8;
                border: 1px solid #00b4d8;
                border-radius: 6px;
                font-weight: bold;
                font-size: 10px;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                background: rgba(0, 180, 216, 0.15);
            }
        """)
        self.btn_layout.addWidget(self.retry_btn)

        self.config_btn = QPushButton("⚙  CONFIGURE")
        self.config_btn.setFixedSize(125, 34)
        self.config_btn.setCursor(Qt.PointingHandCursor)
        self.config_btn.clicked.connect(self.config_clicked.emit)
        self.config_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #64748b;
                border: 1px solid #30363D;
                border-radius: 6px;
                font-weight: bold;
                font-size: 10px;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                border-color: #9DA7B3;
                color: white;
            }
        """)
        self.btn_layout.addWidget(self.config_btn)
        
        layout.addLayout(self.btn_layout)

        # Animation for pulsing dot
        self.opacity_effect = QGraphicsOpacityEffect(self.status_dot)
        self.status_dot.setGraphicsEffect(self.opacity_effect)
        self.pulse_anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.pulse_anim.setDuration(1200)
        self.pulse_anim.setStartValue(1.0)
        self.pulse_anim.setEndValue(0.2)
        self.pulse_anim.setLoopCount(-1)
        self.pulse_anim.setEasingCurve(QEasingCurve.InOutQuad)

    def set_status(self, status: str, message: str):
        self.status_text.setText(message)
        self.pulse_anim.stop()
        self.opacity_effect.setOpacity(1.0)
        self.spinner.hide()
        self.retry_btn.show()
        self.config_btn.show()
        
        if status == "connected" or status == "already_running":
            self.status_dot.setStyleSheet("color: #22c55e; background: transparent;")
            self.status_text.setStyleSheet("color: #22c55e; background: transparent;")
            self.pulse_anim.start()
            self.retry_btn.show()
        elif status == "connecting" or status == "discovery":
            self.status_dot.setStyleSheet("color: #f59e0b; background: transparent;")
            self.status_text.setStyleSheet("color: #f59e0b; background: transparent;")
            self.pulse_anim.start()
            self.retry_btn.hide()
            self.spinner.show()
            if status == "discovery":
                self.icon_lbl.setStyleSheet("color: #f59e0b; font-size: 22px; font-weight: bold; background: transparent;")
            else:
                self.icon_lbl.setStyleSheet("color: #00b4d8; font-size: 22px; font-weight: bold; background: transparent;")
        elif status == "failed":
            self.status_dot.setStyleSheet("color: #ef4444; background: transparent;")
            self.status_text.setStyleSheet("color: #ef4444; background: transparent;")
            self.retry_btn.show()
            self.icon_lbl.setStyleSheet("color: #ef4444; font-size: 22px; font-weight: bold; background: transparent;")
        else: # offline / disconnected
            self.status_dot.setStyleSheet("color: #30363D; background: transparent;")
            self.status_text.setStyleSheet("color: #64748b; background: transparent;")
            self.retry_btn.hide()
            self.icon_lbl.setStyleSheet("color: #00b4d8; font-size: 22px; font-weight: bold; background: transparent;")

class DashboardScreen(QWidget):
    configure_clicked = Signal()
    retry_clicked     = Signal()

    def __init__(self, db, alert_service):
        super().__init__()
        self.db            = db
        self.alert_service = alert_service
        self._feeds: Dict[int, CameraFeedWidget] = {}
        self._ticker_queue = deque(maxlen=10)
        self._start_time   = datetime.utcnow()
        self._setup_ui()
        self._start_timers()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        content = QWidget()
        content.setObjectName("ContentArea")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        # ── Page Header ───────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title = QLabel("Dashboard")
        title.setObjectName("PageTitle")
        title.setFont(QFont("Segoe UI", 26, QFont.Weight.Bold))
        title.setStyleSheet("color: #E6EDF3; background: transparent;")

        sub_row = QHBoxLayout()
        sub_row.setSpacing(12)
        sub = QLabel("Real-time fire & smoke detection monitoring")
        sub.setObjectName("PageSubtitle")
        sub_row.addWidget(sub)

        # Uptime badge
        self._uptime_lbl = QLabel("Uptime: 0m")
        self._uptime_lbl.setFont(QFont("Segoe UI", 10))
        self._uptime_lbl.setStyleSheet(
            "color: #238636; background: rgba(0,186,78,0.06);"
            " border: 1px solid rgba(0,186,78,0.15); border-radius: 4px;"
            " padding: 2px 10px;"
        )
        sub_row.addWidget(self._uptime_lbl)
        sub_row.addStretch()

        title_col.addWidget(title)
        title_col.addLayout(sub_row)
        hdr.addLayout(title_col)
        hdr.addStretch()

        # Radar
        self._radar = RadarWidget()
        hdr.addWidget(self._radar)
        hdr.addSpacing(12)

        # Alarm button — subdued in idle
        self._alarm_btn = AlarmButton()
        hdr.addWidget(self._alarm_btn)

        layout.addLayout(hdr)

        # ── Warning Banner ────────────────────────────────────────────────────
        self._warning_banner = DetectionWarningBanner()
        self._warning_banner.dismissed.connect(self._on_banner_dismissed)
        layout.addWidget(self._warning_banner)

        # ── Health Pills Row ──────────────────────────────────────────────────
        layout.addSpacing(16)
        health_pills_row = QHBoxLayout()
        health_pills_row.setSpacing(16)
        
        self._cpu_pill = HealthPill("Server CPU", "#10B981")
        self._ram_pill = HealthPill("Server RAM", "#3182CE")
        self._edge_cpu_pill = HealthPill("Edge CPU", "#D29922")
        self._edge_ram_pill = HealthPill("Edge RAM", "#E3000F")
        self._gpu_pill = HealthPill("Edge GPU", "#8B5CF6")
        
        health_pills_row.addWidget(self._cpu_pill)
        health_pills_row.addWidget(self._ram_pill)
        health_pills_row.addWidget(self._edge_cpu_pill)
        health_pills_row.addWidget(self._edge_ram_pill)
        health_pills_row.addWidget(self._gpu_pill)
        
        layout.addLayout(health_pills_row)
        layout.addSpacing(24)

        # ── Live Feeds (top priority) ─────────────────────────────────────────
        feeds_hdr = QHBoxLayout()
        feeds_hdr.setSpacing(10)

        feeds_title = QLabel("CAMERA FEEDS")
        feeds_title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        feeds_title.setStyleSheet("color: #E6EDF3; letter-spacing: 1.5px; background: transparent;")
        feeds_hdr.addWidget(feeds_title)
        feeds_hdr.addStretch()

        self._feeds_count = QLabel("0 active")
        self._feeds_count.setFont(QFont("Segoe UI", 10))
        self._feeds_count.setStyleSheet("color: #484F58; background: transparent;")
        feeds_hdr.addWidget(self._feeds_count)

        layout.addLayout(feeds_hdr)

        self._feed_grid = QGridLayout()
        self._feed_grid.setSpacing(16)
        self._feed_container = QWidget()
        self._feed_container.setLayout(self._feed_grid)
        layout.addWidget(self._feed_container)

        self._no_cam_frame = QFrame()
        self._no_cam_frame.setVisible(False)

        # ── KPI Row ───────────────────────────────────────────────────────────
        layout.addSpacing(20)
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(14)
        self._kpi_cams     = KpiSummaryCard("Cameras Online",  "#10B981")
        self._kpi_today    = KpiSummaryCard("Alerts Today",    "#FF3B30")
        self._kpi_critical = KpiSummaryCard("Critical",        "#FF3B30")
        self._kpi_unack    = KpiSummaryCard("Unacknowledged",  "#F59E0B")
        for card in [self._kpi_cams, self._kpi_today, self._kpi_critical, self._kpi_unack]:
            kpi_row.addWidget(card)
        layout.addLayout(kpi_row)

        # ── Edge Status Row ───────────────────────────────────────────────────
        layout.addSpacing(16)
        self._jetson_card = JetsonStatusCard()
        self._jetson_card.retry_clicked.connect(self.retry_clicked.emit)
        self._jetson_card.config_clicked.connect(self.configure_clicked.emit)
        layout.addWidget(self._jetson_card)
        
        self._edge_health_widget = QWidget() # Stub to prevent crashes

        layout.addStretch()

        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)

        # ── Alert Ticker ──────────────────────────────────────────────────────
        ticker_bar = QWidget()
        ticker_bar.setObjectName("AlertTicker")
        ticker_bar.setFixedHeight(36)
        t_layout = QHBoxLayout(ticker_bar)
        t_layout.setContentsMargins(20, 0, 20, 0)
        t_layout.setSpacing(10)

        live_dot = QLabel("●")
        live_dot.setStyleSheet("color: #e3000f; font-size: 10px; background: transparent;")
        t_layout.addWidget(live_dot)

        self._ticker_lbl = QLabel(
            "System monitoring active — No critical incidents detected."
        )
        self._ticker_lbl.setStyleSheet(
            "color: #9DA7B3; font-family: \"Consolas\";"
            " font-size: 11px; background: transparent;"
        )
        t_layout.addWidget(self._ticker_lbl)
        t_layout.addStretch()

        self._ticker_ts = QLabel("")
        self._ticker_ts.setStyleSheet("color: #484F58; font-size: 10px; background: transparent;")
        t_layout.addWidget(self._ticker_ts)

        root.addWidget(ticker_bar)

    # ── Quick Action Handlers ─────────────────────────────────────────────────
    def _go_to_alerts(self):
        try:
            stack = self.parent()
            while stack and not hasattr(stack, 'setCurrentIndex'):
                stack = stack.parent()
            if stack:
                stack.setCurrentIndex(2)
        except Exception:
            pass

    def _quick_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Alert Report",
            f"fireguard_report_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv",
            "CSV Files (*.csv)"
        )
        if not path:
            return
        try:
            alerts = self.db.get_alerts(limit=5000, offset=0)
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["ID", "Camera", "Label", "Threat", "Confidence", "Time", "Acknowledged"])
                for a in alerts:
                    writer.writerow([
                        a.get("id"), a.get("cam_name", "?"), a.get("label", ""),
                        a.get("threat_level", ""), f"{a.get('confidence', 0) * 100:.1f}%",
                        a.get("timestamp", ""), "Yes" if a.get("acknowledged") else "No"
                    ])
            self._refresh_btn.setText("Exported")
            self._refresh_btn.setStyleSheet("color: #238636;")
            QTimer.singleShot(2000, lambda: self._refresh_btn.setText("Refresh"))
            QTimer.singleShot(2000, lambda: self._refresh_btn.setStyleSheet(""))
        except Exception as e:
            logger.error("Quick export failed: %s", e)

    # ── Timers ────────────────────────────────────────────────────────────────
    def _start_timers(self):
        self._kpi_timer = QTimer(self)
        self._kpi_timer.timeout.connect(self._refresh_kpis)
        self._kpi_timer.start(3000)

        self._radar_blip_timer = QTimer(self)
        self._radar_blip_timer.timeout.connect(self._maybe_add_blip)
        self._radar_blip_timer.start(4000)

        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._refresh_uptime)
        self._uptime_timer.start(60000)

        if HAS_PSUTIL:
            self._health_timer = QTimer(self)
            self._health_timer.timeout.connect(self._refresh_health)
            self._health_timer.start(2000)

        self._refresh_kpis()
        self._refresh_uptime()

    def _refresh_uptime(self):
        delta = datetime.utcnow() - self._start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes = remainder // 60
        if hours > 0:
            self._uptime_lbl.setText(f"Uptime: {hours}h {minutes}m")
        else:
            self._uptime_lbl.setText(f"Uptime: {minutes}m")

    def _maybe_add_blip(self):
        try:
            stats = self.db.get_stats()
            if stats.get("total_alerts_today", 0) > 0:
                import random
                self._radar.add_blip(random.uniform(0, 360), random.uniform(0.3, 0.9))
        except Exception:
            pass

    def _on_refresh_clicked(self):
        self._refresh_kpis()
        self._refresh_btn.setText("Done")
        self._refresh_btn.setStyleSheet("color: #238636;")
        QTimer.singleShot(1500, lambda: self._refresh_btn.setText("Refresh"))
        QTimer.singleShot(1500, lambda: self._refresh_btn.setStyleSheet(""))

    def _refresh_kpis(self):
        try:
            stats = self.db.get_stats()
            cams  = stats.get("cameras_online", 0)
            total = stats.get("cameras_total", 0)
            self._kpi_cams.set_value(f"{cams}/{total}", f"of {total} registered")

            alerts_val = stats.get("total_alerts_today", 0)
            self._kpi_today.set_value(str(alerts_val), "events today")

            crit_val = stats.get("critical_alerts_today", 0)
            self._kpi_critical.set_value(str(crit_val), "critical events")

            unack_val = stats.get("unacknowledged", 0)
            self._kpi_unack.set_value(str(unack_val), "need attention")

            self._radar.set_alert(crit_val > 0)

            # Update last incident
            try:
                recent = self.db.get_alerts(limit=1, offset=0)
                if recent:
                    ts_str = recent[0].get("timestamp", "")
                    if ts_str:
                        dt = datetime.fromisoformat(ts_str)
                        delta = datetime.utcnow() - dt
                        mins = int(delta.total_seconds() // 60)
                        if mins < 1:
                            ago = "just now"
                        elif mins < 60:
                            ago = f"{mins} min ago"
                        elif mins < 1440:
                            ago = f"{mins // 60}h {mins % 60}m ago"
                        else:
                            ago = f"{mins // 1440}d ago"
                        threat = recent[0].get("threat_level", "")
                        color = {"CRITICAL": "#e3000f", "HIGH": "#D29922"}.get(threat, "#9DA7B3")
                        self._last_incident_lbl.setText(f"Last incident:  {ago}  •  {threat}")
                        self._last_incident_lbl.setStyleSheet(f"color: {color}; background: transparent;")
                    else:
                        self._last_incident_lbl.setText("Last incident:  —  No alerts recorded")
                        self._last_incident_lbl.setStyleSheet("color: #238636; background: transparent;")
                else:
                    self._last_incident_lbl.setText("Last incident:  —  No alerts recorded")
                    self._last_incident_lbl.setStyleSheet("color: #238636; background: transparent;")
            except Exception:
                pass

        except Exception as e:
            logger.error("KPI refresh failed: %s", e)

    def _refresh_health(self):
        try:
            cpu = int(psutil.cpu_percent())
            ram = int(psutil.virtual_memory().percent)
            self._cpu_pill.set_value(cpu)
            self._ram_pill.set_value(ram)
        except Exception:
            pass


    def _on_banner_dismissed(self):
        self._radar.set_alert(False)

    # ── Public API ────────────────────────────────────────────────────────────
    def refresh_stats(self):
        """Update KPI cards from database."""
        self._refresh_kpis()

    def update_camera_feed(self, cam_id: int, cam_name: str,
                           jpeg_bytes: bytes, is_alert: bool = False, metadata: dict = None):
        if cam_id not in self._feeds:
            self._add_camera_widget(cam_id, cam_name)
        
        feed = self._feeds[cam_id]
        feed.set_camera_name(cam_name) # Ensure name is always current
        feed.update_frame(jpeg_bytes, is_alert=is_alert, metadata=metadata)
        self._no_cam_frame.setVisible(False)
        self._feeds_count.setText(f"{len(self._feeds)} active")

    def _add_camera_widget(self, cam_id: int, cam_name: str):
        widget = CameraFeedWidget(cam_id, cam_name)
        self._feeds[cam_id] = widget
        self._reorganize_camera_grid()

    def remove_camera_feed(self, cam_id: int):
        if cam_id in self._feeds:
            widget = self._feeds.pop(cam_id)
            self._feed_grid.removeWidget(widget)
            widget.deleteLater()
            self._reorganize_camera_grid()
            self._feeds_count.setText(f"{len(self._feeds)} active")
            
            if not self._feeds:
                self._no_cam_frame.setVisible(True)

    def _reorganize_camera_grid(self):
        # Clear existing grid items
        while self._feed_grid.count():
            item = self._feed_grid.takeAt(0)
            # We don't delete the widget, just remove it from the grid layout
        
        count = len(self._feeds)
        if count == 0: return

        cols = 1 if count == 1 else (2 if count <= 4 else 3)
        sorted_ids = sorted(self._feeds.keys())
        
        for idx, cam_id in enumerate(sorted_ids):
            row = idx // cols
            col = idx % cols
            widget = self._feeds[cam_id]
            self._feed_grid.addWidget(widget, row, col)
            if count == 1:
                self._feed_grid.setAlignment(widget, Qt.AlignCenter)
            else:
                self._feed_grid.setAlignment(widget, Qt.Alignment())

    def mark_camera_offline(self, cam_id: int):
        if cam_id in self._feeds:
            self._feeds[cam_id].set_offline()

    def push_alert_ticker(self, cam_name: str, label: str,
                          threat: str, confidence: float):
        ts  = datetime.utcnow().strftime("%H:%M:%S")
        msg = (f"[{threat}]  {cam_name.upper()}  →  "
               f"{label.upper()}  ({confidence * 100:.0f}% confidence)")
        self._ticker_queue.appendleft(msg)

        color_map = {"CRITICAL": "#e3000f", "HIGH": "#D29922", "MEDIUM": "#facc15"}
        c = color_map.get(threat, "#9DA7B3")
        self._ticker_lbl.setStyleSheet(
            f"color: {c}; font-family: \"Consolas\";"
            f" font-size: 11px; font-weight: bold; background: transparent;"
        )
        self._ticker_lbl.setText(msg)
        self._ticker_ts.setText(ts)

        self._warning_banner.show_event(cam_name, label, threat, confidence)
        self._radar.set_alert(True)
        self._radar.add_blip(
            __import__("random").uniform(0, 360),
            __import__("random").uniform(0.4, 0.9)
        )

    # ── Edge Connection Status ────────────────────────────────────────────────
    def set_jetson_status(self, status_key: str, message: str):
        """Update Jetson status UI based on SSH worker state."""
        self._jetson_card.set_status(status_key, message)

    def set_edge_connected(self, count: int):
        """Called when a Jetson Nano edge pipeline connects via WebSocket."""
        self._jetson_card.set_status("connected", f"Connected — {count} device(s) online")

    def set_edge_disconnected(self, count: int):
        """Called when a Jetson Nano edge pipeline disconnects."""
        if count > 0:
            self._jetson_card.set_status("connecting", f"Partial — {count} device(s) still online")
        else:
            self._jetson_card.set_status("offline", "Disconnected (Waiting for edge pipeline…)")

    def set_edge_health(self, count: int, cpu: float, ram: float, gpu: float):
        if count > 0:
            self._edge_cpu_pill.set_value(int(cpu))
            self._edge_ram_pill.set_value(int(ram))
            self._gpu_pill.set_value(int(gpu))
        else:
            # Optionally clear or set to 0 if no edge connected
            # But maybe we want to keep the last known if it just disconnected?
            # Usually better to show 0 if offline.
            self._edge_cpu_pill.set_value(0)
            self._edge_ram_pill.set_value(0)
            self._gpu_pill.set_value(0)
