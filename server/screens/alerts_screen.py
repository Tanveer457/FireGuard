"""
alerts_screen.py — FireGuard Alert Management (Enterprise AMD Edition v3)
Features:
  - Paginated alert table (50 per page) with gradient header
  - Filters: camera, threat level, acknowledged state, date range, search
  - Color-coded rows: CRITICAL=red-tint, HIGH=orange-tint, MEDIUM=amber-tint
  - Emoji-prefix threat labels: 🔥 CRITICAL, ⚠ HIGH, ⚡ MEDIUM, 💨 SMOKE
  - Action buttons: Acknowledge (red gradient), Done (success ghost)
  - Snapshot preview panel on right side (styled card)
  - Acknowledge-all button
"""

import os
import logging
from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QComboBox, QLineEdit, QSizePolicy,
    QSplitter, QScrollArea, QFileDialog, QMessageBox,
    QFrame, QDialog, QTextEdit
)
from PySide6.QtCore import Qt, QTimer, Signal, QSize
from PySide6.QtGui import QPixmap, QImage, QColor, QBrush, QFont

logger = logging.getLogger(__name__)

# Threat level row background tints
THREAT_ROW_BG = {
    "CRITICAL": QColor(227, 0,   15, 30),
    "HIGH":     QColor(242, 139, 0,  22),
    "MEDIUM":   QColor(250, 204, 21, 15),
    "SMOKE":    QColor(0,   186, 78, 12),
    "LOW":      QColor(0,   0,   0,  0),
}

THREAT_FG = {
    "CRITICAL": QColor("#e3000f"),
    "HIGH":     QColor("#D29922"),
    "MEDIUM":   QColor("#facc15"),
    "SMOKE":    QColor("#238636"),
    "LOW":      QColor("#9DA7B3"),
}

THREAT_LABELS = {
    "CRITICAL": "🔥  CRITICAL",
    "HIGH":     "⚠   HIGH",
    "MEDIUM":   "⚡  MEDIUM",
    "SMOKE":    "💨  SMOKE",
    "LOW":      "ℹ   LOW",
}


# ── Snapshot placeholder (wire-frame camera icon) ─────────────────────────────
class AmdSnapshotPlaceholder(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(190)
        self.setStyleSheet(
            "background: #0B0F14; border-radius: 6px; border: 1px dashed #2a2a2a;"
        )

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QPen
        from PySide6.QtCore import Qt
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#2a2a2a"), 1.5))

            cx, cy = self.width() // 2, self.height() // 2
            painter.drawRect(cx - 28, cy - 20, 56, 38)
            painter.drawEllipse(cx - 8, cy - 8, 16, 16)
            painter.drawLine(cx - 28, cy - 20, cx + 28, cy + 18)
            painter.drawLine(cx - 28, cy + 18, cx + 28, cy - 20)

            painter.setPen(QPen(QColor("#353535")))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(
                0, cy + 36, self.width(), 20,
                Qt.AlignmentFlag.AlignCenter,
                "Select an alert to preview"
            )


# ── Acknowledge Dialog ────────────────────────────────────────────────────────
class AcknowledgeDialog(QDialog):
    def __init__(self, alert_id: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Acknowledge Alert #{alert_id}")
        self.setMinimumWidth(420)
        self.setStyleSheet("background-color: #111111;")
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)

        info = QLabel(
            f"Acknowledging <b>Alert #{alert_id}</b><br>"
            "Add optional notes about the incident:"
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #E6EDF3;")
        layout.addWidget(info)

        self._notes = QTextEdit()
        self._notes.setPlaceholderText(
            "Optional notes (investigation result, actions taken...)"
        )
        self._notes.setFixedHeight(100)
        layout.addWidget(self._notes)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("GhostButton")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(self.reject)

        self._ok_btn = QPushButton("Acknowledge")
        self._ok_btn.setFixedHeight(36)
        self._ok_btn.clicked.connect(self.accept)

        btns.addWidget(cancel_btn)
        btns.addWidget(self._ok_btn)
        layout.addLayout(btns)

    @property
    def notes(self) -> str:
        return self._notes.toPlainText()


# ── Alerts Screen ─────────────────────────────────────────────────────────────
class AlertsScreen(QWidget):
    def __init__(self, db, alert_service):
        super().__init__()
        self.db            = db
        self.alert_service = alert_service
        self._current_page = 1
        self._page_size    = 50
        self._total_pages  = 1
        self._selected_id  = None
        self._setup_ui()
        self._load_alerts()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 20)
        root.setSpacing(16)

        # ── Page Header ───────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(16)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)

        title = QLabel("Alert Log")
        title.setObjectName("PageTitle")
        title.setFont(QFont("Segoe UI", 26, QFont.Weight.Bold))
        title.setStyleSheet("color: #E6EDF3; background: transparent;")

        sub = QLabel("All fire & smoke detection events")
        sub.setObjectName("PageSubtitle")

        title_col.addWidget(title)
        title_col.addWidget(sub)
        hdr.addLayout(title_col)
        hdr.addStretch()

        self._ack_all_btn = QPushButton("Acknowledge All")
        self._ack_all_btn.setObjectName("btnPrimary")
        self._ack_all_btn.setStyleSheet("""
            QPushButton {
                background: #FF3B30;
                border: none;
                border-radius: 6px;
                color: white;
                font-size: 12px;
                font-weight: 600;
                padding: 0 16px;
                min-height: 32px;
            }
            QPushButton:hover { background: #E02D22; }
        """)
        self._ack_all_btn.clicked.connect(self._acknowledge_all)

        self._export_btn = QPushButton("Export CSV")
        self._export_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,6);
                border: 1px solid rgba(255,255,255,20);
                border-radius: 6px;
                color: #94A3B8;
                font-size: 12px;
                padding: 0 14px;
                min-height: 32px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,12);
                color: #F1F5F9;
                border-color: rgba(255,255,255,45);
            }
        """)
        self._export_btn.clicked.connect(self._export_csv)

        hdr.addWidget(self._ack_all_btn)
        hdr.addWidget(self._export_btn)
        root.addLayout(hdr)

        # Red accent rule
        accent = QFrame()
        accent.setFixedHeight(1)
        accent.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FF3B30, stop:0.3 rgba(255,59,48,60), stop:1 transparent); border: none;")
        root.addWidget(accent)

        # ── Filter Bar ────────────────────────────────────────────────────────
        filter_frame = QFrame()
        filter_frame.setObjectName("FilterBar")
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setSpacing(12)
        filter_layout.setContentsMargins(16, 12, 16, 12)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search camera or label...")
        self._search_input.setFixedHeight(38)
        self._search_input.setFont(QFont("Segoe UI", 11))
        self._search_input.textChanged.connect(self._on_filter_changed)

        self._cam_combo = QComboBox()
        self._cam_combo.setFixedHeight(38)
        self._cam_combo.setMinimumWidth(140)
        self._cam_combo.addItem("All Cameras", None)
        self._cam_combo.currentIndexChanged.connect(self._on_filter_changed)

        self._threat_combo = QComboBox()
        self._threat_combo.setFixedHeight(38)
        self._threat_combo.setMinimumWidth(120)
        for t in ["All Threats", "CRITICAL", "HIGH", "MEDIUM", "LOW"]:
            self._threat_combo.addItem(t)
        self._threat_combo.currentIndexChanged.connect(self._on_filter_changed)

        self._ack_combo = QComboBox()
        self._ack_combo.setFixedHeight(38)
        self._ack_combo.setMinimumWidth(130)
        self._ack_combo.addItems(["All Status", "Unacknowledged", "Acknowledged"])
        self._ack_combo.currentIndexChanged.connect(self._on_filter_changed)

        # Time Period Filter
        self._period_combo = QComboBox()
        self._period_combo.setFixedHeight(38)
        self._period_combo.setMinimumWidth(120)
        self._period_combo.addItems(["Today", "3 Days", "1 Week", "2 Weeks", "1 Month", "All Time"])
        self._period_combo.setCurrentIndex(1) # Default to '3 Days'
        self._period_combo.currentIndexChanged.connect(self._on_filter_changed)

        # Auto-refresh toggle
        self._auto_refresh_combo = QComboBox()
        self._auto_refresh_combo.setFixedHeight(38)
        self._auto_refresh_combo.setMinimumWidth(110)
        self._auto_refresh_combo.addItems(["Auto: Off", "Auto: 5s", "Auto: 15s", "Auto: 30s"])
        self._auto_refresh_combo.currentIndexChanged.connect(self._on_auto_refresh_changed)

        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._load_alerts)

        filter_layout.addWidget(self._search_input, stretch=2)
        filter_layout.addWidget(self._cam_combo)
        filter_layout.addWidget(self._threat_combo)
        filter_layout.addWidget(self._ack_combo)
        filter_layout.addWidget(self._period_combo)
        filter_layout.addWidget(self._auto_refresh_combo)
        root.addWidget(filter_frame)

        # ── Splitter: Table + Snapshot ────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: table ───────────────────────────────────────────────────────
        table_widget = QWidget()
        table_layout = QVBoxLayout(table_widget)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(8)

        self._table = QTableWidget(0, 8)
        self._table.setStyleSheet("""
            QTableWidget {
                background: transparent;
                border: none;
                gridline-color: rgba(255,255,255,6);
                font-size: 13px;
            }
            QTableWidget::item {
                padding: 0 14px;
                border-bottom: 1px solid rgba(255,255,255,5);
                color: #CBD5E1;
                min-height: 52px;
            }
            QTableWidget::item:selected {
                background: rgba(255,59,48,22);
                color: #F1F5F9;
            }
            QTableWidget::item:hover:!selected {
                background: rgba(255,255,255,4);
            }
            QHeaderView::section {
                background: rgba(255,255,255,5);
                border: none;
                border-bottom: 1px solid rgba(255,255,255,12);
                border-right: 1px solid rgba(255,255,255,5);
                color: #475569;
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 1px;
                padding: 0 14px;
                min-height: 38px;
            }
        """)
        self._table.setHorizontalHeaderLabels([
            "ID", "CAMERA", "LABEL", "THREAT", "CONF", "TIME", "ACK", "ACTION"
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 52)
        self._table.setColumnWidth(1, 120)
        self._table.setColumnWidth(2, 100)
        self._table.setColumnWidth(3, 120)
        self._table.setColumnWidth(4, 70)
        self._table.setColumnWidth(5, 140)
        self._table.setColumnWidth(6, 50)
        self._table.setColumnWidth(7, 130)
        self._table.verticalHeader().setDefaultSectionSize(52)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(False)
        self._table.setSortingEnabled(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.cellClicked.connect(self._on_row_selected)
        table_layout.addWidget(self._table)

        self._empty_widget = QWidget(self._table)
        empty_layout = QVBoxLayout(self._empty_widget)
        empty_layout.setAlignment(Qt.AlignCenter)
        icon_label = QLabel("🔥")
        icon_label.setStyleSheet("font-size: 32px; color: #1E293B;")
        icon_label.setAlignment(Qt.AlignCenter)
        text_label = QLabel("No alerts recorded")
        text_label.setStyleSheet("font-size: 13px; color: #334155; font-weight: 500;")
        text_label.setAlignment(Qt.AlignCenter)
        sub_label = QLabel("Detection events will appear here")
        sub_label.setStyleSheet("font-size: 11px; color: #1E293B;")
        sub_label.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(icon_label)
        empty_layout.addSpacing(8)
        empty_layout.addWidget(text_label)
        empty_layout.addWidget(sub_label)
        self._empty_widget.hide()

        # Pagination
        pagination = QHBoxLayout()
        pagination.setSpacing(12)

        self._count_lbl = QLabel("0 alerts")
        self._count_lbl.setFont(QFont("Segoe UI", 11))
        self._count_lbl.setStyleSheet("color: #757575; background: transparent;")

        self._prev_btn = QPushButton("← Prev")
        self._prev_btn.setObjectName("GhostButton")
        self._prev_btn.setFixedHeight(34)
        self._prev_btn.setMinimumWidth(80)
        self._prev_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._prev_btn.clicked.connect(self._prev_page)

        self._page_lbl = QLabel("Page 1 / 1")
        self._page_lbl.setAlignment(Qt.AlignCenter)
        self._page_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._page_lbl.setStyleSheet(
            "color: #E6EDF3; background: rgba(255,255,255,0.03);"
            " border: 1px solid #222222; border-radius: 6px;"
            " padding: 4px 16px;"
        )

        self._next_btn = QPushButton("Next →")
        self._next_btn.setObjectName("GhostButton")
        self._next_btn.setFixedHeight(34)
        self._next_btn.setMinimumWidth(80)
        self._next_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._next_btn.clicked.connect(self._next_page)

        pagination.addWidget(self._count_lbl)
        pagination.addStretch()
        pagination.addWidget(self._prev_btn)
        pagination.addWidget(self._page_lbl)
        pagination.addWidget(self._next_btn)
        table_layout.addLayout(pagination)

        splitter.addWidget(table_widget)

        # ── Right: snapshot preview panel ─────────────────────────────────────
        preview_widget = QFrame()
        preview_widget.setStyleSheet("""
            background: rgba(255,255,255,5);
            border-left: 1px solid rgba(255,255,255,10);
            border-radius: 0px;
        """)
        preview_widget.setMinimumWidth(260)
        preview_widget.setMaximumWidth(360)
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(18, 18, 18, 18)
        preview_layout.setSpacing(14)

        preview_hdr = QHBoxLayout()
        preview_hdr.setSpacing(8)

        pr_icon = QLabel("📷")
        pr_icon.setFont(QFont("Segoe UI", 14))
        pr_icon.setStyleSheet("color: #FF3B30; background: transparent;")
        pr_icon.setFixedWidth(22)
        preview_hdr.addWidget(pr_icon)

        preview_header = QLabel("SNAPSHOT PREVIEW")
        preview_header.setStyleSheet("""
            font-size: 10px;
            font-weight: 600;
            color: #64748B;
            letter-spacing: 1.5px;
            text-transform: uppercase;
        """)
        preview_hdr.addWidget(preview_header)
        preview_hdr.addStretch()
        preview_layout.addLayout(preview_hdr)

        pr_accent = QWidget()
        pr_accent.setFixedHeight(1)
        pr_accent.setStyleSheet("""
            background: qlineargradient(
                x1:0, y1:0, x2:1, y2:0,
                stop:0 #FF3B30, 
                stop:0.4 rgba(255,59,48,60),
                stop:1 transparent);
        """)
        preview_layout.addWidget(pr_accent)

        self._snapshot_lbl = AmdSnapshotPlaceholder()
        preview_layout.addWidget(self._snapshot_lbl)

        # Alert details
        self._detail_frame = QFrame()
        self._detail_frame.setStyleSheet(
            "background: rgba(255,255,255,0.02); border: 1px solid #1e1e1e;"
            " border-radius: 6px;"
        )
        detail_inner = QVBoxLayout(self._detail_frame)
        detail_inner.setContentsMargins(14, 12, 14, 12)
        detail_inner.setSpacing(4)

        self._detail_lbl = QLabel("Select an alert to see details")
        self._detail_lbl.setWordWrap(True)
        self._detail_lbl.setFont(QFont("Segoe UI", 10))
        self._detail_lbl.setStyleSheet("color: #757575; background: transparent;")
        detail_inner.addWidget(self._detail_lbl)
        preview_layout.addWidget(self._detail_frame)
        preview_layout.addStretch()

        splitter.addWidget(preview_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter, stretch=1)

        # Populate camera combo
        self._refresh_cam_combo()

    # ── Data Loading ──────────────────────────────────────────────────────────
    def _get_filters(self) -> dict:
        filters = {}
        cam_id = self._cam_combo.currentData()
        if cam_id:
            filters["cam_id"] = cam_id
        threat = self._threat_combo.currentText()
        if threat != "All Threats":
            filters["threat_level"] = threat
        ack_idx = self._ack_combo.currentIndex()
        if ack_idx == 1:
            filters["acknowledged"] = False
        elif ack_idx == 2:
            filters["acknowledged"] = True
        
        # Period Filter
        period = self._period_combo.currentText()
        if period != "All Time":
            now = datetime.utcnow()
            if period == "Today":
                start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            elif period == "3 Days":
                start_date = now - timedelta(days=3)
            elif period == "1 Week":
                start_date = now - timedelta(days=7)
            elif period == "2 Weeks":
                start_date = now - timedelta(days=14)
            elif period == "1 Month":
                start_date = now - timedelta(days=30)
            else:
                start_date = None
            
            if start_date:
                filters["date_from"] = start_date.isoformat()

        search = self._search_input.text().strip()
        if search:
            filters["search"] = search
        return filters

    def _on_auto_refresh_changed(self, idx: int):
        self._auto_refresh_timer.stop()
        intervals = [0, 5000, 15000, 30000]
        if idx > 0:
            self._auto_refresh_timer.start(intervals[idx])

    def _load_alerts(self):
        try:
            filters       = self._get_filters()
            total         = self.db.count_alerts(**filters)
            self._total_pages  = max(1, (total + self._page_size - 1) // self._page_size)
            self._current_page = min(self._current_page, self._total_pages)

            alerts = self.db.get_alerts(
                limit=self._page_size,
                offset=(self._current_page - 1) * self._page_size,
                **filters
            )

            self._table.setRowCount(0)
            for row_idx, alert in enumerate(alerts):
                self._table.insertRow(row_idx)
                self._populate_row(row_idx, alert)

            # Update empty state visibility
            self._empty_widget.setGeometry(0, 0, self._table.viewport().width(), self._table.viewport().height())
            if self._table.rowCount() == 0:
                self._empty_widget.show()
            else:
                self._empty_widget.hide()

            self._page_lbl.setText(f"Page {self._current_page} / {self._total_pages}")
            self._count_lbl.setText(f"{total} alerts total")
            self._prev_btn.setEnabled(self._current_page > 1)
            self._next_btn.setEnabled(self._current_page < self._total_pages)
            
            if alerts:
                self._table.setRowHeight(0, 44)

        except Exception as e:
            logger.error("Alert load failed: %s", e)

    def _populate_row(self, row: int, alert: dict):
        threat   = alert.get("threat_level", "LOW")
        bg_color = THREAT_ROW_BG.get(threat, THREAT_ROW_BG["LOW"])
        fg_color = THREAT_FG.get(threat, THREAT_FG["LOW"])

        ts_raw = alert.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_raw).strftime("%b %d  %H:%M:%S")
        except Exception:
            ts = ts_raw

        ack      = "✔" if alert.get("acknowledged") else "—"
        conf_pct = f"{alert.get('confidence', 0) * 100:.0f}%"

        # Build row data (col 3 = threat display label)
        row_data = [
            str(alert.get("id", "")),
            alert.get("cam_name", f"Cam {alert.get('cam_id', '?')}"),
            alert.get("label", "").upper(),
            THREAT_LABELS.get(threat, threat),
            conf_pct,
            ts,
            ack,
        ]

        for col, text in enumerate(row_data):
            item = QTableWidgetItem(text)
            item.setBackground(QBrush(bg_color))
            item.setFont(QFont("Segoe UI", 10))

            if col == 3:   # Threat — bold + color
                item.setForeground(QBrush(fg_color))
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            elif col == 6:  # Ack tick
                if alert.get("acknowledged"):
                    item.setForeground(QBrush(QColor("#238636")))
                    f = item.font(); f.setBold(True); item.setFont(f)
                else:
                    item.setForeground(QBrush(QColor("#484F58")))

            item.setData(Qt.UserRole, alert.get("id"))
            self._table.setItem(row, col, item)

        # Action buttons
        action_container = QWidget()
        action_layout = QHBoxLayout(action_container)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(6)
        action_layout.setAlignment(Qt.AlignCenter)

        if alert.get("acknowledged"):
            ack_btn = QPushButton("✔ Done")
            ack_btn.setObjectName("GhostButton")
            ack_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            ack_btn.setStyleSheet(
                "QPushButton { color: #238636; border: 1px solid #1a4d30; "
                "background: rgba(0,186,78,0.08); border-radius: 4px; padding: 4px 8px; }"
                "QPushButton:hover { background: rgba(0,186,78,0.15); }"
            )
            ack_btn.setEnabled(False)
            action_layout.addWidget(ack_btn)
        else:
            alert_id = alert["id"]
            
            # ACK Button
            ack_btn = QPushButton("ACK")
            ack_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            ack_btn.setFixedWidth(42)
            ack_btn.setStyleSheet(
                "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                " stop:0 #e3000f, stop:1 #c10009); color: white; border: none;"
                " border-radius: 4px; padding: 4px 8px; font-weight: 700; }"
                "QPushButton:hover { background: #ff1a2b; }"
            )
            ack_btn.clicked.connect(lambda _, aid=alert_id: self._acknowledge(aid))
            
            # DELETE Button
            del_btn = QPushButton("DEL")
            del_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            del_btn.setFixedWidth(42)
            del_btn.setStyleSheet(
                "QPushButton { background: #1a1a1a; color: #757575; border: 1px solid #333;"
                " border-radius: 4px; padding: 4px 8px; }"
                "QPushButton:hover { background: #331111; color: #ff5555; border-color: #ff3b30; }"
            )
            del_btn.clicked.connect(lambda _, aid=alert_id: self._delete_alert(aid))
            
            action_layout.addWidget(ack_btn)
            action_layout.addWidget(del_btn)

        self._table.setCellWidget(row, 7, action_container)


    def _refresh_cam_combo(self):
        self._cam_combo.blockSignals(True)
        self._cam_combo.clear()
        self._cam_combo.addItem("All Cameras", None)
        try:
            cams = self.db.get_cameras()
            for c in cams:
                self._cam_combo.addItem(c["name"], c["id"])
        except Exception:
            pass
        self._cam_combo.blockSignals(False)

    # ── Row Actions ───────────────────────────────────────────────────────────
    def _on_row_selected(self, row: int, col: int):
        id_item = self._table.item(row, 0)
        if not id_item:
            return
        alert_id = int(id_item.text())
        self._selected_id = alert_id
        self._load_snapshot_preview(alert_id)

    def _load_snapshot_preview(self, alert_id: int):
        try:
            alert = self.db.get_alert_by_id(alert_id)
            if not alert:
                return

            ts = alert.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts).strftime("%Y-%m-%d  %H:%M:%S")
            except Exception:
                pass

            threat = alert.get("threat_level", "")
            fg = THREAT_FG.get(threat, QColor("#9DA7B3"))

            detail = (
                f"<b style='color:#E6EDF3;'>Alert #{alert_id}</b><br>"
                f"<span style='color:#9DA7B3;'>Camera: {alert.get('cam_name', '?')}</span><br>"
                f"<span style='color:#9DA7B3;'>Label: {alert.get('label','').upper()}</span><br>"
                f"<span style='color:{fg.name()};font-weight:bold;'>"
                f"Threat: {threat}</span><br>"
                f"<span style='color:#9DA7B3;'>"
                f"Confidence: {alert.get('confidence', 0)*100:.1f}%</span><br>"
                f"<span style='color:#484F58;'>Time: {ts}</span>"
            )

            if alert.get("acknowledged"):
                ack_at = alert.get("acknowledged_at", "")
                try:
                    ack_at = datetime.fromisoformat(ack_at).strftime("%b %d %H:%M")
                except Exception:
                    pass
                detail += (
                    f"<br><span style='color:#238636;font-weight:bold;'>"
                    f"✔ Acknowledged {ack_at}</span>"
                )
            if alert.get("notes"):
                detail += f"<br><i style='color:#484F58;'>Notes: {alert['notes']}</i>"

            self._detail_lbl.setText(detail)

            # Load snapshot image
            img_path = self.alert_service.get_snapshot_by_alert_id(alert_id)
            if img_path and os.path.exists(img_path):
                import gc
                if isinstance(self._snapshot_lbl, AmdSnapshotPlaceholder):
                    preview_layout = self._detail_frame.parent().layout()
                    self._snapshot_lbl.deleteLater()
                    self._snapshot_lbl = QLabel()
                    self._snapshot_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    self._snapshot_lbl.setMinimumHeight(190)
                    self._snapshot_lbl.setStyleSheet(
                        "background:#0B0F14; border-radius:6px;"
                        " border: 1px solid #2a2a2a;"
                    )
                    preview_layout.insertWidget(2, self._snapshot_lbl)

                pix    = QPixmap(img_path)
                # Determine scaling to fit while keeping aspect ratio
                scaled = pix.scaled(
                    self._snapshot_lbl.width(), self._snapshot_lbl.height(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self._snapshot_lbl.setPixmap(scaled)
                self._snapshot_lbl.setAlignment(Qt.AlignCenter)
                gc.collect()
            else:
                if not isinstance(self._snapshot_lbl, AmdSnapshotPlaceholder):
                    preview_layout = self._detail_frame.parent().layout()
                    self._snapshot_lbl.deleteLater()
                    self._snapshot_lbl = AmdSnapshotPlaceholder()
                    preview_layout.insertWidget(2, self._snapshot_lbl)

        except Exception as e:
            logger.error("Preview failed: %s", e)

    def _acknowledge(self, alert_id: int):
        dlg = AcknowledgeDialog(alert_id, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self.alert_service.acknowledge_alert(alert_id, notes=dlg.notes)
            self._load_alerts()

    def _delete_alert(self, alert_id: int):
        reply = QMessageBox.question(
            self, "Delete Alert",
            f"Are you sure you want to permanently delete alert #{alert_id}?\n"
            "This will also remove the associated snapshot and video clip.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            if self.alert_service.delete_alert(alert_id):
                self._load_alerts()
                if self._selected_id == alert_id:
                    self._selected_id = None
                    self._detail_lbl.setText("Select an alert to see details")
                    if not isinstance(self._snapshot_lbl, AmdSnapshotPlaceholder):
                        preview_layout = self._detail_frame.parent().layout()
                        self._snapshot_lbl.deleteLater()
                        self._snapshot_lbl = AmdSnapshotPlaceholder()
                        preview_layout.insertWidget(2, self._snapshot_lbl)
            else:
                QMessageBox.warning(self, "Error", f"Failed to delete alert #{alert_id}")

    def _acknowledge_all(self):
        reply = QMessageBox.question(
            self, "Acknowledge All",
            "Mark ALL unacknowledged alerts as acknowledged?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.db.acknowledge_all()
            self._load_alerts()

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Alerts CSV", "fireguard_alerts.csv",
            "CSV Files (*.csv)"
        )
        if path:
            filters = self._get_filters()
            written = self.db.export_alerts_csv(path, **filters)
            QMessageBox.information(
                self, "Export Complete",
                f"Exported {written} alerts to:\n{path}"
            )

    # ── Filter / Page Nav ─────────────────────────────────────────────────────
    def _on_filter_changed(self):
        self._current_page = 1
        self._load_alerts()

    def _prev_page(self):
        if self._current_page > 1:
            self._current_page -= 1
            self._load_alerts()

    def _next_page(self):
        if self._current_page < self._total_pages:
            self._current_page += 1
            self._load_alerts()

    def on_new_alert(self):
        if self._current_page == 1:
            self._load_alerts()

    def focus_alert(self, alert_id: int):
        """Called from other screens to jump to a specific alert."""
        # Reset filters to ensure the alert is visible
        self._search_input.setText(str(alert_id))
        self._cam_combo.setCurrentIndex(0)
        self._threat_combo.setCurrentIndex(0)
        self._ack_combo.setCurrentIndex(0)
        self._period_combo.setCurrentIndex(5) # All Time
        
        self._load_alerts()
        
        # Auto-select the row if it's the first result
        if self._table.rowCount() > 0:
            self._table.selectRow(0)
            self._on_row_selected(0, 0)
