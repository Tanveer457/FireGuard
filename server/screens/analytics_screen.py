"""
analytics_screen.py — FireGuard Analytics & Reporting (Enterprise AMD Edition v5)

Professional, uncrowded 2x2 layout:
  - Alert Trend (timeline chart)
  - Threat Breakdown (distribution chart)
  - Top Cameras (performance rankings)
  - System Snapshot (clean KPIs)
Functional:
  - Export CSV Report
  - Clear Historical Data (requires confirmation)
"""

import csv
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QTimer, QEasingCurve, QPropertyAnimation, Slot, Signal
from PySide6.QtGui import (
    QColor, QFont, QPainter, QPen, QBrush,
    QLinearGradient
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QScrollArea, QGridLayout,
    QSizePolicy, QFrame, QFileDialog, QMessageBox,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView,
    QGraphicsOpacityEffect
)

logger = logging.getLogger(__name__)

try:
    import pyqtgraph as pg
    pg.setConfigOption("background", "transparent")
    pg.setConfigOption("foreground", "#9DA7B3")
    pg.setConfigOption("antialias", True)
    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False

THREAT_COLORS = {
    "CRITICAL": "#e3000f",
    "HIGH":     "#D29922",
    "MEDIUM":   "#facc15",
    "LOW":      "#9DA7B3",
}


# ─────────────────────────────────────────────────────────────────────────────
# Mini Horizontal Bar (for rankings)
# ─────────────────────────────────────────────────────────────────────────────
class RankBar(QWidget):
    def __init__(self, color: str = "#e3000f"):
        super().__init__()
        self.setFixedHeight(8)
        self._pct   = 0.0
        self._color = QColor(color)

    def set_percent(self, pct: float):
        self._pct = max(0.0, min(1.0, pct))
        self.update()

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.NoPen)
            w, h = self.width(), self.height()

            p.setBrush(QBrush(QColor("#1a1a1a")))
            p.drawRoundedRect(0, 0, w, h, 4, 4)

            fw = max(8, int(self._pct * w))
            grad = QLinearGradient(0, 0, fw, 0)
            grad.setColorAt(0, self._color.darker(120))
            grad.setColorAt(1, self._color.lighter(120))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(0, 0, fw, h, 4, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Chart Card (Premium Styling)
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# Gradient Bar Chart (Fix 1)
# ─────────────────────────────────────────────────────────────────────────────
class GradientBarChart(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._data = {"Fire": 0, "Smoke": 0, "Fire + Smoke": 0}
        
    def set_data(self, data: dict):
        # input data mapping
        self._data = {
            "Fire": data.get("FIRE", 0),
            "Smoke": data.get("SMOKE", 0),
            "Fire + Smoke": data.get("COMBINED", 0)
        }
        self.update()

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.Antialiasing)
            
            w, h = self.width(), self.height()
            margin_bottom = 30
            margin_top = 40
            margin_left = 20
            margin_right = 20
            
            chart_h = h - margin_top - margin_bottom
            chart_w = w - margin_left - margin_right
            
            counts = list(self._data.values())
            max_val = max(counts) + 2 if counts else 5
            
            bar_width = chart_w // 4
            spacing = (chart_w - (bar_width * 3)) // 4
            
            # Colors: Fire (#dc2626), Smoke (#f97316), Combined (#eab308)
            color_configs = [
                ("#dc2626", "#7f1d1d"),
                ("#f97316", "#7c2d12"),
                ("#eab308", "#713f12")
            ]

            for i, (cat, count) in enumerate(self._data.items()):
                x = margin_left + spacing + i * (bar_width + spacing)
                
                # Calculate height (min 4px if 0)
                raw_h = (count / max_val) * chart_h
                is_zero = count == 0
                bh = max(4, int(raw_h))
                y = h - margin_bottom - bh
                
                # Gradient fallback
                c1, c2 = color_configs[i] if i < len(color_configs) else ("#94a3b8", "#1e293b")
                grad = QLinearGradient(x, y, x, y + bh)
                grad.setColorAt(0, QColor(c1))
                grad.setColorAt(1, QColor(c2))
                
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(grad))
                if is_zero:
                    p.setOpacity(0.3)
                else:
                    p.setOpacity(1.0)
                    
                p.drawRoundedRect(x, y, bar_width, bh, 4, 4)
                p.setOpacity(1.0)
                
                # Count label
                p.setPen(QColor("#FFFFFF"))
                p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
                p.drawText(x, y - 10, bar_width, 20, Qt.AlignCenter, str(count))
                
                # Axis label
                p.setPen(QColor("#94a3b8"))
                p.setFont(QFont("Segoe UI", 9))
                p.drawText(x, h - margin_bottom + 5, bar_width, 20, Qt.AlignCenter, cat)

# ─────────────────────────────────────────────────────────────────────────────
# Incident Response Panel (Fix 2)
# ─────────────────────────────────────────────────────────────────────────────
class IncidentRow(QFrame):
    view_requested = Signal(int)

    def __init__(self, alert: dict, bg: str):
        super().__init__()
        self.alert_id = alert["id"]
        self.setFixedHeight(44)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"background: {bg}; border: none;")
        self.setObjectName("IncidentRow")
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(12)
        
        # Severity pill
        sev = alert.get("threat_level", "LOW").upper()
        # Colors: CRITICAL = red #dc2626, HIGH = orange #f97316, MEDIUM = yellow #eab308, LOW = gray #64748b
        sev_colors = {
            "CRITICAL": "#dc2626",
            "HIGH":     "#f97316",
            "MEDIUM":   "#eab308",
            "LOW":      "#64748b"
        }
        c = sev_colors.get(sev, "#64748b")
        
        sev_pill = QLabel(sev)
        sev_pill.setFixedSize(70, 22)
        sev_pill.setAlignment(Qt.AlignCenter)
        sev_pill.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        sev_pill.setStyleSheet(f"background: {c}; color: white; border-radius: 4px;")
        layout.addWidget(sev_pill)
        
        # Camera → Label
        cam_name = alert.get("cam_name", f"Cam {alert['cam_id']}")
        label = alert.get("label", "unknown").upper()
        info = QLabel(f"<span style='color: white; font-weight: bold;'>{cam_name}</span>  <span style='color: #94a3b8;'>→</span>  <span style='color: white;'>{label}</span>")
        info.setFont(QFont("Segoe UI", 10))
        layout.addWidget(info)
        
        # Confidence
        conf = alert.get("confidence", 0) * 100
        conf_lbl = QLabel(f"{conf:.0f}%")
        conf_lbl.setStyleSheet("color: #64748b;")
        conf_lbl.setFont(QFont("Segoe UI", 9))
        layout.addWidget(conf_lbl)
        
        layout.addStretch()
        
        # Relative Time
        ts_str = alert.get("timestamp", "")
        rel_time = self._get_relative_time(ts_str)
        time_lbl = QLabel(rel_time)
        time_lbl.setStyleSheet("color: #64748b;")
        time_lbl.setFont(QFont("Segoe UI", 9))
        layout.addWidget(time_lbl)
        
        # Arrow indicator instead of ACK
        arrow = QLabel("→")
        arrow.setStyleSheet("color: #00b4d8; font-weight: bold; font-size: 14px;")
        layout.addWidget(arrow)
        
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.view_requested.emit(self.alert_id)
        super().mousePressEvent(event)

    def _get_relative_time(self, ts_iso: str) -> str:
        try:
            dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
            now = datetime.now(dt.tzinfo)
            diff = now - dt
            mins = int(diff.total_seconds() // 60)
            if mins < 1: return "Just now"
            if mins < 60: return f"{mins}m ago"
            hours = mins // 60
            if hours < 24: return f"{hours}h ago"
            return f"{hours//24}d ago"
        except: return "---"

    def fade_out(self):
        self.anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.anim.setDuration(400)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.finished.connect(self.deleteLater)
        self.anim.start()

class IncidentResponsePanel(QWidget):
    view_all_requested = Signal()
    view_requested     = Signal(int)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        
        # Header
        hdr = QHBoxLayout()
        title = QLabel("RECENT INCIDENTS")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet("color: white;")
        hdr.addWidget(title)
        
        hdr.addStretch()
        
        view_all = QPushButton("View All →")
        view_all.setCursor(Qt.PointingHandCursor)
        view_all.setStyleSheet("background: transparent; color: #00b4d8; font-size: 11px; border: none;")
        view_all.clicked.connect(self.view_all_requested.emit)
        hdr.addWidget(view_all)
        layout.addLayout(hdr)
        
        # Stat Chips
        chip_layout = QHBoxLayout()
        chip_layout.setSpacing(8)
        self.unack_chip = self._make_chip("🔴", "0 Unacked")
        self.today_chip = self._make_chip("⚡", "0 Today")
        self.conf_chip = self._make_chip("📈", "0% Avg Conf")
        chip_layout.addWidget(self.unack_chip)
        chip_layout.addWidget(self.today_chip)
        chip_layout.addWidget(self.conf_chip)
        chip_layout.addStretch()
        layout.addLayout(chip_layout)
        
        # List Area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(1)
        self.scroll.setWidget(self.list_container)
        layout.addWidget(self.scroll)

    def _make_chip(self, icon: str, text: str) -> QLabel:
        lbl = QLabel(f"{icon} {text}")
        lbl.setStyleSheet("""
            background: rgba(255,255,255,0.05); color: #e0e0e0; 
            padding: 4px 10px; border-radius: 12px; font-size: 10px;
        """)
        return lbl

    def update_list(self, alerts: list, stats: dict):
        # Update chips
        self.unack_chip.setText(f"🔴 {stats.get('unacknowledged', 0)} Unacked")
        self.today_chip.setText(f"⚡ {stats.get('total_alerts_today', 0)} Today")
        
        # Calculate avg confidence for these alerts
        if alerts:
            avg = sum(a.get("confidence", 0) for a in alerts) / len(alerts)
            self.conf_chip.setText(f"📈 {avg*100:.0f}% Avg Conf")
        
        # Update list
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            
        for i, alert in enumerate(alerts[:6]):
            bg = "#0a0f1a" if i % 2 == 0 else "#0d1520"
            row = IncidentRow(alert, bg)
            row.view_requested.connect(self.view_requested.emit)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch()

    def remove_alert(self, alert_id: int):
        for i in range(self.list_layout.count()):
            w = self.list_layout.itemAt(i).widget()
            if isinstance(w, IncidentRow) and w.alert_id == alert_id:
                w.fade_out()
                break

class ChartCard(QFrame):
    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        self.setObjectName("AmdMetricCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(340)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(12)

        hdr = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        
        self._title_lbl = QLabel(title.upper())
        self._title_lbl.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet(
            "color: #E6EDF3; letter-spacing: 0.5px; background: transparent;"
        )
        title_col.addWidget(self._title_lbl)
        
        if subtitle:
            sub_lbl = QLabel(subtitle)
            sub_lbl.setFont(QFont("Segoe UI", 9))
            sub_lbl.setStyleSheet("color: #757575; background: transparent;")
            title_col.addWidget(sub_lbl)
            
        hdr.addLayout(title_col)
        hdr.addStretch()
        layout.addLayout(hdr)

        # Red accent rule
        accent = QFrame()
        accent.setFixedHeight(1)
        accent.setStyleSheet("background: rgba(227,0,15,0.4); border: none;")
        layout.addWidget(accent)

        self._area = QVBoxLayout()
        self._area.setContentsMargins(0, 8, 0, 0)
        self._area.setSpacing(8)
        layout.addLayout(self._area)

    def set_title(self, t: str):
        self._title_lbl.setText(t.upper())

    @property
    def area(self):
        return self._area


# ─────────────────────────────────────────────────────────────────────────────
# Camera Performance Row
# ─────────────────────────────────────────────────────────────────────────────
class CamPerfRow(QWidget):
    def __init__(self, rank: int, name: str, alerts: int,
                 avg_conf: float, pct: float, color: str):
        super().__init__()
        self.setFixedHeight(56)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)

        rank_lbl = QLabel(f"#{rank}")
        rank_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        rank_lbl.setFixedWidth(30)
        rank_lbl.setStyleSheet(f"color: {color}; background: transparent;")
        top.addWidget(rank_lbl)

        name_lbl = QLabel(name)
        name_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        name_lbl.setStyleSheet("color: #e0e0e0; background: transparent;")
        top.addWidget(name_lbl)
        top.addStretch()

        alerts_lbl = QLabel(f"{alerts} events")
        alerts_lbl.setFont(QFont("Segoe UI", 10))
        alerts_lbl.setStyleSheet(
            f"color: {'#e3000f' if alerts > 0 else '#484F58'}; background: transparent;"
        )
        top.addWidget(alerts_lbl)

        sep = QLabel("•")
        sep.setStyleSheet("color: #484F58; background: transparent;")
        top.addWidget(sep)

        conf_lbl = QLabel(f"{avg_conf:.0f}% avg conf")
        conf_lbl.setFont(QFont("Segoe UI", 10))
        conf_lbl.setStyleSheet("color: #757575; background: transparent;")
        top.addWidget(conf_lbl)

        layout.addLayout(top)

        bar = RankBar(color)
        bar.set_percent(pct)
        layout.addWidget(bar)


# ─────────────────────────────────────────────────────────────────────────────
# Analytics Screen
# ─────────────────────────────────────────────────────────────────────────────
class AnalyticsScreen(QWidget):
    """
    Professional 2x2 Grid of performance metrics. 
    Clean aesthetic, minimal clutter. Contains useful export & data clear features.
    """
    nav_requested = Signal(int, int) # (screen_index, optional_alert_id)

    def __init__(self, db):
        super().__init__()
        self.db = db
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_all)
        self._timer.start(30_000)
        self._refresh_all()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        content = QWidget()
        content.setObjectName("ContentArea")
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(32, 28, 32, 28)
        self._layout.setSpacing(24)

        # ── Page Header ───────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(4)

        title = QLabel("Analytics & Reporting")
        title.setObjectName("PageTitle")
        title.setFont(QFont("Segoe UI", 26, QFont.Weight.Bold))
        title.setStyleSheet("color: #E6EDF3; background: transparent;")

        sub = QLabel("Historical trends, performance insights, and data exports")
        sub.setObjectName("PageSubtitle")

        title_col.addWidget(title)
        title_col.addWidget(sub)
        hdr.addLayout(title_col)
        hdr.addStretch()

        self._period_combo = QComboBox()
        self._period_combo.setFixedHeight(40)
        self._period_combo.setMinimumWidth(160)
        self._period_combo.setFont(QFont("Segoe UI", 11))
        self._period_combo.addItems(["Last 24 Hours", "Last 3 Days", "Last 7 Days", "Last 30 Days"])
        self._period_combo.setCurrentIndex(1) # Default to 'Last 3 Days'
        self._period_combo.currentIndexChanged.connect(self._refresh_all)

        self._refresh_btn = QPushButton("⟳  Refresh")
        self._refresh_btn.setObjectName("GhostButton")
        self._refresh_btn.setFixedHeight(40)
        self._refresh_btn.setMinimumWidth(100)
        self._refresh_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)

        export_btn = QPushButton("↓  Export CSV")
        export_btn.setFixedHeight(40)
        export_btn.setMinimumWidth(120)
        export_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        export_btn.clicked.connect(self._export_report)
        
        hdr.addWidget(self._period_combo)
        hdr.addWidget(self._refresh_btn)
        hdr.addWidget(export_btn)
        self._layout.addLayout(hdr)

        # Red accent line
        accent = QFrame()
        accent.setFixedHeight(1)
        accent.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FF3B30, stop:0.3 rgba(255,59,48,60), stop:1 transparent); border: none;")
        self._layout.addWidget(accent)

        # ── 2x2 Grid ──────────────────────────────────────────────────────────
        grid = QGridLayout()
        grid.setSpacing(24)

        # 1. Alert Trend (Top Left)
        self._trend_card = ChartCard("Alert Timeline", "Total detection events over time")
        if HAS_PYQTGRAPH:
            self._trend_plot = pg.PlotWidget()
            self._style_plot(self._trend_plot)
            self._trend_card.area.addWidget(self._trend_plot)
        else:
            self._trend_fallback = QFrame()
            self._trend_fallback.setStyleSheet(
                "background: rgba(255,165,2,0.06); border: 1px solid rgba(255,165,2,0.2);"
                " border-radius: 8px;"
            )
            fb_layout = QVBoxLayout(self._trend_fallback)
            fb_layout.setContentsMargins(20, 16, 20, 16)
            fb_layout.setSpacing(6)
            fb_icon = QLabel("⚠")
            fb_icon.setFont(QFont("Segoe UI", 18))
            fb_icon.setStyleSheet("color: #ffa502; background: transparent;")
            fb_layout.addWidget(fb_icon)
            fb_text = QLabel("PyQtGraph not installed")
            fb_text.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
            fb_text.setStyleSheet("color: #ffa502; background: transparent;")
            fb_layout.addWidget(fb_text)
            fb_hint = QLabel("Run:  pip install pyqtgraph  to enable charts")
            fb_hint.setFont(QFont("Consolas", 10))
            fb_hint.setStyleSheet("color: #9DA7B3; background: transparent;")
            fb_layout.addWidget(fb_hint)
            self._trend_card.area.addWidget(self._trend_fallback)
            
        grid.addWidget(self._trend_card, 0, 0)

        # 2. Threat Breakdown (Top Right)
        self._threat_card = ChartCard("Threat Distribution", "Detections categorized by label")
        self._threat_chart = GradientBarChart()
        self._threat_card.area.addWidget(self._threat_chart)
        grid.addWidget(self._threat_card, 0, 1)

        # 3. Top Cameras (Bottom Left)
        self._rank_card = ChartCard("Activity by Camera", "Most active nodes in the selected period")
        self._rank_container = QVBoxLayout()
        self._rank_container.setSpacing(8)
        self._rank_card.area.addLayout(self._rank_container)
        self._rank_card.area.addStretch()
        grid.addWidget(self._rank_card, 1, 0)
        
        # 4. Incident Response (Bottom Right)
        self._incident_card = ChartCard("Recent Incidents", "Actionable detection events")
        self._incident_panel = IncidentResponsePanel()
        self._incident_panel.view_requested.connect(self._view_incident)
        self._incident_panel.view_all_requested.connect(lambda: self.nav_requested.emit(2, -1)) 
        self._incident_card.area.addWidget(self._incident_panel)
        grid.addWidget(self._incident_card, 1, 1)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self._layout.addLayout(grid)
        self._layout.addStretch()

        scroll.setWidget(content)
        root.addWidget(scroll)

    def _style_plot(self, plot: "pg.PlotWidget"):
        plot.setBackground(None)
        plot.showGrid(x=True, y=True, alpha=0.06)
        for ax in ("left", "bottom"):
            a = plot.getAxis(ax)
            a.setPen(pg.mkPen(color="#1E293B", width=1))
            a.setTextPen(pg.mkPen(color="#475569"))
        plot.getViewBox().setContentsMargins(10, 10, 10, 10)

    # ── Refresh ───────────────────────────────────────────────────────────────
    def _on_refresh_clicked(self):
        self._refresh_all()
        self._refresh_btn.setText("✔  Refreshed")
        self._refresh_btn.setStyleSheet("color: #238636;")
        QTimer.singleShot(1500, lambda: self._refresh_btn.setText("⟳  Refresh"))
        QTimer.singleShot(1500, lambda: self._refresh_btn.setStyleSheet(""))

    def refresh(self):
        """Public method called when new alerts arrive."""
        self._refresh_all()

    def _refresh_all(self):
        try:
            # Indices: 0:24h, 1:3d, 2:7d, 3:30d
            days = [1, 3, 7, 30][self._period_combo.currentIndex()]
            dist     = self.db.get_threat_distribution(days=days)
            cam_data = self.db.get_camera_alert_stats(days=days)
            stats    = self.db.get_stats()
            recent   = self.db.get_alerts(limit=10, offset=0, acknowledged=False)

            if HAS_PYQTGRAPH:
                self._draw_trend_chart(days)
            
            self._threat_chart.set_data(dist)
            self._incident_panel.update_list(recent, stats)
            self._draw_camera_rankings(cam_data)

        except Exception as e:
            logger.error("Analytics refresh failed: %s", e)

    @Slot(int)
    def _view_incident(self, alert_id: int):
        """Redirect to Alert Log screen and focus on this alert."""
        self.nav_requested.emit(2, alert_id)

    # ── Trend charts ──────────────────────────────────────────────────────────
    def _draw_trend_chart(self, days: int):
        self._trend_plot.clear()
        try:
            if days <= 1:
                data = self.db.get_hourly_chart(days=1)
                y = [d["count"] for d in data]
                x = list(range(len(y)))
                
                labels = []
                for d in data:
                    try:
                        h_str = d["hour"].split(" ")[1]
                        labels.append(h_str)
                    except:
                        labels.append("")
                
                ticks = []
                for i, l in enumerate(labels):
                    if i % 3 == 0:
                        ticks.append((i, l))
                
                ax = self._trend_plot.getAxis('bottom')
                ax.setTicks([ticks])
            else:
                data = self.db.get_daily_chart(days=days)
                y = [d["count"] for d in data]
                x = list(range(len(y)))
                
                labels = [d["day"].split("-")[2] for d in data] # Just Day
                ticks = [(i, l) for i, l in enumerate(labels) if i % (max(1, days//7)) == 0]
                ax = self._trend_plot.getAxis('bottom')
                ax.setTicks([ticks])

            if not y: y = [0]
            if not x: x = [0]
            
            self._trend_plot.setYRange(0, max(max(y) + 1, 5))
            pen = pg.mkPen(color='#FF3B30', width=2)
            curve = self._trend_plot.plot(x, y, pen=pen)
            
            fill = pg.FillBetweenItem(
                curve,
                self._trend_plot.plot(x, [0]*len(x), pen=pg.mkPen(None)),
                brush=pg.mkBrush(255, 59, 48, 35)
            )
            self._trend_plot.addItem(fill)

        except Exception as e:
            logger.error("Trend chart: %s", e)

    # ── Camera Rankings ───────────────────────────────────────────────────────
    def _draw_camera_rankings(self, cam_data: list):
        while self._rank_container.count():
            item = self._rank_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            if not cam_data:
                lbl = QLabel("No cameras recorded.")
                lbl.setStyleSheet("color: #484F58; background: transparent; padding: 20px;")
                self._rank_container.addWidget(lbl)
                return

            sorted_cams = sorted(
                cam_data, key=lambda x: x.get("period_alerts", 0) or 0, reverse=True
            )
            max_alerts = max(c.get("period_alerts", 0) or 0 for c in sorted_cams) or 1

            colors = ["#e3000f", "#D29922", "#facc15", "#9DA7B3"]

            # Only show top 4 to prevent clutter
            for rank, cam in enumerate(sorted_cams[:4], 1):
                alerts   = cam.get("period_alerts", 0) or 0
                avg_conf = cam.get("avg_confidence")
                
                # Robust None/Zero handling
                if avg_conf is None:
                    avg_conf_val = 0.0
                else:
                    avg_conf_val = float(avg_conf)
                    if avg_conf_val < 1.0:
                        avg_conf_val *= 100.0

                pct   = alerts / max_alerts
                color = colors[min(rank - 1, 3)]

                row_widget = CamPerfRow(
                    rank, cam.get("name", f"Camera {cam['id']}"),
                    alerts, avg_conf_val, pct, color
                )
                self._rank_container.addWidget(row_widget)

        except Exception as e:
            logger.error("Camera rankings error: %s", e)


    # ── Functional Actions ────────────────────────────────────────────────────
    def _export_report(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Analytics CSV",
            f"fireguard_report_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            "CSV Files (*.csv)"
        )
        if not path:
            return

        try:
            alerts = self.db.get_alerts(limit=5000, offset=0)

            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["FireGuard Analytics Export"])
                writer.writerow(["Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
                writer.writerow([])
                
                writer.writerow(["=== ALERT LOG ==="])
                writer.writerow(["ID", "Camera", "Label", "Threat", "Confidence", "Time", "Acknowledged"])
                for a in alerts:
                    conf_raw = a.get('confidence')
                    conf_val = (float(conf_raw) * 100) if conf_raw is not None else 0.0
                    writer.writerow([
                        a.get("id"), a.get("cam_name", "?"), a.get("label", ""),
                        a.get("threat_level", ""), f"{conf_val:.1f}%",
                        a.get("timestamp", ""), "Yes" if a.get("acknowledged") else "No"
                    ])

            QMessageBox.information(
                self, "Export Complete",
                f"Successfully exported {len(alerts)} records to CSV."
            )

        except Exception as e:
            logger.error("Export failed: %s", e)
            QMessageBox.critical(self, "Export Failed", str(e))

    def _clear_data(self):
        ans = QMessageBox.question(
            self, "Clear Analytics Data",
            "<p>Are you sure you want to delete all historical alert data?</p>"
            "<p><b>This action cannot be undone.</b></p>",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if ans == QMessageBox.Yes:
            try:
                # Use closing and correct connection method
                from contextlib import closing
                with closing(self.db._get_connection()) as conn:
                    conn.execute("DELETE FROM detections") # Also clear detections
                    conn.execute("DELETE FROM alerts")
                    conn.commit()
                QMessageBox.information(self, "Data Cleared", "All historical alert records have been permanently removed.")
                self._refresh_all()
            except Exception as e:
                logger.error(f"Failed to clear analytics data: {e}")
                QMessageBox.critical(self, "Error", f"Failed to clear data: {str(e)}")
