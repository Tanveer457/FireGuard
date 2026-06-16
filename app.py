"""
app.py — FireGuard Desktop Application (AMD Adrenalin Redesign)
Architecture:
  ┌────────────────────────────────────────────────────────┐
  │  QMainWindow                                           │
  │  ┌──────────────────────────────────────────────────┐  │
  │  │  AmdTitleBar (Logo + Title + Min/Max/Close)      │  │
  │  ├──────────────────────────────────────────────────┤  │
  │  │  TopNavBar (Back/Fwd, Tabs, Search, Icons)       │  │
  │  ├──────────────────────────────────────────────────┤  │
  │  │  SubNavBar (Visible only for Settings)           │  │
  │  ├──────────────────────────────────────────────────┤  │
  │  │  QStackedWidget (6 screens)                      │  │
  │  │   0: DashboardScreen (Home)                      │  │
  │  │   1: CamerasScreen                               │  │
  │  │   2: AlertsScreen                                │  │
  │  │   3: AnalyticsScreen (Performance)               │  │
  │  │   4: JetsonScreen (Edge Config)                  │  │
  │  │   5: SettingsScreen (General Settings)           │  │
  │  ├──────────────────────────────────────────────────┤  │
  │  │  Status Bar                                      │  │
  │  └──────────────────────────────────────────────────┘  │
  └────────────────────────────────────────────────────────┘
"""

import sys
import os

# --- FORCE PYSIDE6 FOR PYQTGRAPH & OTHERS ---
os.environ["QT_API"] = "pyside6"
os.environ["QT_LIB"] = "PySide6"

import math
import logging
import threading
import time
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QStackedWidget, QLineEdit,
    QFrame, QSystemTrayIcon, QMenu, QSizePolicy,
    QGraphicsOpacityEffect, QDialog, QFormLayout,
    QFileDialog, QMessageBox
)
from PySide6.QtCore import Qt, Slot, QTimer, QRectF, QSize, QPropertyAnimation
from PySide6.QtGui import (
    QIcon, QFont, QPainter, QColor, QBrush, QPen,
    QPixmap, QMouseEvent, QLinearGradient, QPainterPath
)

# ── ensure project root on path ───────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server.utils.paths import (
    APP_ROOT, STORAGE_DIR, LOGS_DIR, DB_PATH, DARK_THEME_QSS
)
from server.database.sqlite_db    import Database
from server.workers.ws_thread import WSServerThread
from server.workers.jetson_ssh_worker import JetsonSSHWorker
from server.services.alert_service import AlertService

from server.screens.dashboard_screen import DashboardScreen
from server.screens.cameras_screen  import CamerasScreen
from server.screens.alerts_screen   import AlertsScreen
from server.screens.analytics_screen import AnalyticsScreen
from server.screens.jetson           import JetsonScreen
from server.screens.settings_screen  import SettingsScreen

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)-8s]  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fireguard")


# ─────────────────────────────────────────────────────────────────────────────
# Custom AMD-Style Logo Widget (QPainter — no external file)
# ─────────────────────────────────────────────────────────────────────────────
class AmdLogoWidget(QWidget):
    """Sleek branded F logo rendered with QPainter."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(42, 42)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            cx, cy = self.width() // 2, self.height() // 2
            sz = 28

            grad = QLinearGradient(cx - sz//2, cy - sz//2, cx + sz//2, cy + sz//2)
            grad.setColorAt(0, QColor("#e3000f"))
            grad.setColorAt(1, QColor("#8a0008"))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(cx - sz//2, cy - sz//2, sz, sz), 6, 6)

            highlight = QLinearGradient(cx, cy - sz//2, cx, cy)
            highlight.setColorAt(0, QColor(255, 255, 255, 30))
            highlight.setColorAt(1, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(highlight))
            p.drawRoundedRect(QRectF(cx - sz//2, cy - sz//2, sz, sz//2), 6, 0)

            p.setPen(QPen(QColor(255, 255, 255, 240)))
            p.setFont(QFont("Segoe UI", 14, QFont.Weight.ExtraBold))
            p.drawText(QRectF(cx - sz//2, cy - sz//2, sz, sz), Qt.AlignCenter, "F")

# ─────────────────────────────────────────────────────────────────────────────
# Window Control Button (Minimize / Maximize / Close)
# ─────────────────────────────────────────────────────────────────────────────
class WindowControlButton(QPushButton):
    """Consistently sized 36x36 window control button with centered icon.
    Uses QPainter for pixel-perfect icon rendering."""

    def __init__(self, role: str):
        """role: 'minimize', 'maximize', or 'close'"""
        super().__init__()
        self._role = role
        self._maximized = False
        self.setFixedSize(36, 36)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("WinCtrlBtn")
        if role == "close":
            self.setObjectName("WinCloseBtn")

    def set_maximized(self, val: bool):
        self._maximized = val
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)

            is_hovered = self.underMouse()
            if self._role == "close" and is_hovered:
                color = QColor("#FFFFFF")
            elif is_hovered:
                color = QColor("#E6EDF3")
            else:
                color = QColor("#9DA7B3")

            pen = QPen(color, 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            cx, cy = self.width() // 2, self.height() // 2

            if self._role == "minimize":
                p.drawLine(cx - 5, cy, cx + 5, cy)

            elif self._role == "maximize":
                if self._maximized:
                    # Restore icon (two overlapping rectangles)
                    p.drawRect(cx - 4, cy - 2, 7, 7)
                    p.drawLine(cx - 2, cy - 2, cx - 2, cy - 4)
                    p.drawLine(cx - 2, cy - 4, cx + 5, cy - 4)
                    p.drawLine(cx + 5, cy - 4, cx + 5, cy + 3)
                    p.drawLine(cx + 5, cy + 3, cx + 3, cy + 3)
                else:
                    p.drawRect(cx - 5, cy - 5, 10, 10)

            elif self._role == "close":
                p.drawLine(cx - 4, cy - 4, cx + 4, cy + 4)
                p.drawLine(cx - 4, cy + 4, cx + 4, cy - 4)


# ─────────────────────────────────────────────────────────────────────────────
# Custom Enterprise Title Bar
# ─────────────────────────────────────────────────────────────────────────────
class AmdTitleBar(QWidget):
    def __init__(self, parent_window):
        super().__init__(parent_window)
        self.setObjectName("AmdTitleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.parent_window = parent_window
        self.setFixedHeight(38)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(0)

        # Logo badge
        logo = QLabel(" F ")
        logo.setObjectName("TitleBarLogo")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(20, 20)
        layout.addWidget(logo, alignment=Qt.AlignVCenter)
        layout.addSpacing(8)

        # Title
        self.title_label = QLabel("FireGuard — AI Fire Detection System")
        self.title_label.setObjectName("TitleLabel")
        layout.addWidget(self.title_label, alignment=Qt.AlignVCenter)

        layout.addStretch()

        # Window controls — consistent 36x36, 2px spacing
        self.min_btn = WindowControlButton("minimize")
        self.min_btn.setToolTip("Minimize")
        self.min_btn.clicked.connect(self.parent_window.showMinimized)

        self.max_btn = WindowControlButton("maximize")
        self.max_btn.setToolTip("Maximize")
        self.max_btn.clicked.connect(self._toggle_maximize)

        self.close_btn = WindowControlButton("close")
        self.close_btn.setToolTip("Close")
        self.close_btn.clicked.connect(self.parent_window.close)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(2)
        btn_layout.setAlignment(Qt.AlignVCenter)
        btn_layout.addWidget(self.min_btn)
        btn_layout.addWidget(self.max_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self._drag_pos = None

    def _toggle_maximize(self):
        if self.parent_window.isMaximized():
            self.parent_window.showNormal()
            self.max_btn.set_maximized(False)
        else:
            self.parent_window.showMaximized()
            self.max_btn.set_maximized(True)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_pos:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.parent_window.move(self.parent_window.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._toggle_maximize()


# ─────────────────────────────────────────────────────────────────────────────
# Navigation Arrow Button (Back / Forward)
# ─────────────────────────────────────────────────────────────────────────────
class NavArrowButton(QPushButton):
    """Compact back/forward navigation with QPainter chevron."""
    def __init__(self, direction: str):
        super().__init__()
        self.direction = direction
        self.setFixedSize(32, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("NavArrowBtn")

    def paintEvent(self, event):
        super().paintEvent(event)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            enabled = self.isEnabled()
            color = QColor("#E6EDF3") if (self.underMouse() and enabled) else (
                QColor("#9DA7B3") if enabled else QColor("#484F58")
            )
            pen = QPen(color, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            cx, cy = self.width() // 2, self.height() // 2
            s = 5
            if self.direction == 'left':
                p.drawLine(cx + s//2, cy - s, cx - s//2, cy)
                p.drawLine(cx - s//2, cy, cx + s//2, cy + s)
            else:
                p.drawLine(cx - s//2, cy - s, cx + s//2, cy)
                p.drawLine(cx + s//2, cy, cx - s//2, cy + s)


# ─────────────────────────────────────────────────────────────────────────────
# Iconic Buttons (Colorful & Basic)
# ─────────────────────────────────────────────────────────────────────────────
class AmdIconButton(QPushButton):
    def __init__(self, icon_name: str):
        super().__init__()
        self.icon_name = icon_name
        self.setFixedSize(36, 36)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("TopIconButton")

    def paintEvent(self, event):
        super().paintEvent(event)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)

            active = self.property("active") == "true"
            color = QColor("#E6EDF3" if (self.underMouse() or active) else "#9DA7B3")
            p.setPen(QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            cx, cy = self.width() // 2, self.height() // 2

            if self.icon_name == "bell":
                path = QPainterPath()
                path.moveTo(cx - 5, cy + 3)
                path.lineTo(cx - 6, cy + 5)
                path.lineTo(cx + 6, cy + 5)
                path.lineTo(cx + 5, cy + 3)
                path.lineTo(cx + 5, cy - 2)
                path.quadTo(cx, cy - 8, cx - 5, cy - 2)
                path.closeSubpath()
                p.drawPath(path)
                p.drawArc(cx - 2, cy + 5, 4, 3, 0, -180 * 16)
            elif self.icon_name == "gear":
                p.drawEllipse(cx - 3, cy - 3, 6, 6)
                for i in range(8):
                    angle = math.radians(i * 45)
                    p.drawLine(
                        int(cx + math.cos(angle) * 5), int(cy + math.sin(angle) * 5),
                        int(cx + math.cos(angle) * 8), int(cy + math.sin(angle) * 8),
                    )


# ─────────────────────────────────────────────────────────────────────────────
# Jetson Config Dialog
# ─────────────────────────────────────────────────────────────────────────────
class JetsonConfigDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Configure Jetson Nano (SSH)")
        self.setFixedWidth(450)
        self.setStyleSheet("""
            QDialog { background: #11161C; color: white; }
            QLabel { color: #9DA7B3; font-weight: bold; }
            QLineEdit { background: #1B212B; border: 1px solid #30363D; border-radius: 4px; color: white; padding: 8px; }
            QPushButton { background: #3b82f6; color: white; border-radius: 4px; padding: 10px; font-weight: bold; }
            QPushButton:hover { background: #2563eb; }
            QPushButton#Ghost { background: transparent; border: 1px solid #30363D; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        header = QLabel("Remote Edge Configuration")
        header.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        header.setStyleSheet("color: white;")
        layout.addWidget(header)

        form = QFormLayout()
        form.setSpacing(12)

        self._host = QLineEdit(db.get_config("jetson_host", ""))
        self._port = QLineEdit(db.get_config("jetson_port", "22"))
        self._user = QLineEdit(db.get_config("jetson_user", "fireguard"))
        self._pass = QLineEdit(db.get_config("jetson_pass", ""))
        self._pass.setEchoMode(QLineEdit.Password)
        
        self._key_path = QLineEdit(db.get_config("jetson_key", ""))
        key_btn = QPushButton("...")
        key_btn.setFixedWidth(40)
        key_btn.clicked.connect(self._pick_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self._key_path)
        key_row.addWidget(key_btn)

        form.addRow("Jetson Host IP", self._host)
        form.addRow("SSH Port", self._port)
        form.addRow("Username", self._user)
        form.addRow("Password", self._pass)
        form.addRow("Private Key (.pem)", key_row)
        
        layout.addLayout(form)

        btns = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        
        save = QPushButton("Save & Connect")
        save.clicked.connect(self._save)
        
        btns.addWidget(cancel)
        btns.addWidget(save)
        layout.addLayout(btns)

    def _pick_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Private Key", "", "Key files (*.pem *.key);;All Files (*)")
        if path: self._key_path.setText(path)

    def _save(self):
        self.db.set_config("jetson_host", self._host.text())
        self.db.set_config("jetson_port", self._port.text())
        self.db.set_config("jetson_user", self._user.text())
        self.db.set_config("jetson_pass", self._pass.text())
        self.db.set_config("jetson_key", self._key_path.text())
        self.accept()

# ─────────────────────────────────────────────────────────────────────────────
# Main Window
# ─────────────────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FireGuard — AI Fire Detection System")
        self.resize(1400, 860)
        self.setMinimumSize(1100, 720)

        # Frameless window
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowSystemMenuHint | Qt.WindowMinMaxButtonsHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)

        # Generated window icon
        self._app_icon = self._generate_icon()
        self.setWindowIcon(self._app_icon)

        # --- STORAGE & DATABASE (Pinned Absolutely) ---
        self.storage_dir = STORAGE_DIR
        
        # Ensure subdirectories exist
        (self.storage_dir / "snapshots").mkdir(parents=True, exist_ok=True)
        (self.storage_dir / "clips").mkdir(parents=True, exist_ok=True)
        (self.storage_dir / "exports").mkdir(parents=True, exist_ok=True)
        
        db_path = DB_PATH
        db_existed = db_path.exists()
        
        self.db = Database(str(db_path))
        self.db.reset_camera_statuses()
        self.alert_service = AlertService(self.db, storage_root=str(self.storage_dir))

        # Startup Logging
        if db_existed:
            alert_count = self.db.get_stats().get("total_alerts_all_time", 0)
            logger.info(f"Database path: {db_path} (existing, {alert_count} alerts found)")
        else:
            logger.info(f"Database path: {db_path} (new — created fresh)")

        # Navigation history
        self._nav_history: list[int] = [0]
        self._nav_pos: int = 0
        self._recently_deleted_cams = set()

        self.jetson_worker = None

        self._init_default_settings()
        
        # Initialize WS thread object early so screens can reference it
        self.ws_thread = WSServerThread(
            host  = self.db.get_config("server_host", "0.0.0.0"),
            port  = int(self.db.get_config("server_port", "8000")),
            token = self.db.get_config("edge_token", "fire-secret-token"),
            db    = self.db,
            storage_dir = str(self.storage_dir)
        )

        self._build_ui()
        self._load_stylesheet()
        self._setup_ws()
        self._setup_tray()
        self._setup_background_tasks()

        logger.info("FireGuard started (AMD Aesthetic). FastAPI docs: http://localhost:8000/docs")
        
    def _setup_background_tasks(self):
        # Run cleanup routines once per hour automatically
        self._cleanup_timer = QTimer(self)
        self._cleanup_timer.timeout.connect(self._run_cleanup_routines)
        self._cleanup_timer.start(3600 * 1000) # 1 hour
        
    def _run_cleanup_routines(self):
        try:
            if self.db.get_config("auto_cleanup", "1") == "1":
                retention = int(self.db.get_config("retention_days", "30"))
                max_snap = int(self.db.get_config("max_snapshots", "500"))
                self.alert_service.run_retention_cleanup(retention)
                self.alert_service.cleanup_old_snapshots(max_snap)
        except Exception as e:
            logger.error("Background cleanup failed: %s", e)

    # ── Default Settings ──────────────────────────────────────────────────────
    def _init_default_settings(self):
        from server.screens.settings_screen import DEFAULTS
        for key, val in DEFAULTS.items():
            if not self.db.get_config(key):
                self.db.set_config(key, val)

    # ── UI Construction ───────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("CentralWidget")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 0. Title Bar ──────────────────────────────────────────────────────
        self.title_bar = AmdTitleBar(self)
        outer.addWidget(self.title_bar)

        # ── 1. Top Navigation Bar ─────────────────────────────────────────────
        top_nav = QWidget()
        top_nav.setObjectName("TopNavBar")
        top_nav.setFixedHeight(48)

        top_layout = QHBoxLayout(top_nav)
        top_layout.setContentsMargins(8, 0, 12, 0)
        top_layout.setSpacing(0)

        # Logo
        self.f_sign = AmdLogoWidget()
        top_layout.addWidget(self.f_sign, alignment=Qt.AlignVCenter)
        top_layout.addSpacing(8)

        # Back / Forward
        self._back_btn = NavArrowButton('left')
        self._back_btn.setToolTip("Go back")
        self._back_btn.setEnabled(False)
        self._back_btn.clicked.connect(self._nav_back)
        top_layout.addWidget(self._back_btn, alignment=Qt.AlignVCenter)
        top_layout.addSpacing(4)

        self._fwd_btn = NavArrowButton('right')
        self._fwd_btn.setToolTip("Go forward")
        self._fwd_btn.setEnabled(False)
        self._fwd_btn.clicked.connect(self._nav_forward)
        top_layout.addWidget(self._fwd_btn, alignment=Qt.AlignVCenter)
        top_layout.addSpacing(12)

        # Main Tabs
        self._top_tabs = []
        tab_names = [
            ("Home", 0),
            ("Cameras", 1),
            ("Performance", 3),
            ("Alerts", 2),
        ]

        for label, idx in tab_names:
            btn = QPushButton(label)
            btn.setObjectName("TopTabButton")
            btn.setFont(QFont("Segoe UI", 11))
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, x=idx, b=btn: self._nav_main(x, b))
            self._top_tabs.append(btn)
            top_layout.addWidget(btn, alignment=Qt.AlignVCenter)

        top_layout.addStretch()

        # Search bar
        search = QLineEdit()
        search.setObjectName("TopSearchBar")
        search.setPlaceholderText("Search...")
        search.setFixedWidth(200)
        search.setFixedHeight(30)
        top_layout.addWidget(search, alignment=Qt.AlignVCenter)
        top_layout.addSpacing(6)

        # Bell icon → Alerts
        self._bell_btn = AmdIconButton("bell")
        self._bell_btn.clicked.connect(lambda: self._nav_main(2, self._top_tabs[3]))
        top_layout.addWidget(self._bell_btn, alignment=Qt.AlignVCenter)
        top_layout.addSpacing(4)

        # Gear icon → Settings
        self._gear_btn = AmdIconButton("gear")
        self._gear_btn.clicked.connect(self._nav_settings)
        top_layout.addWidget(self._gear_btn, alignment=Qt.AlignVCenter)

        outer.addWidget(top_nav)

        # ── 2. Sub Navigation Bar (Settings only) ────────────────────────────
        self.sub_nav = QWidget()
        self.sub_nav.setObjectName("SubNavBar")
        self.sub_nav.setFixedHeight(40)

        sub_layout = QHBoxLayout(self.sub_nav)
        sub_layout.setContentsMargins(60, 0, 16, 0)
        sub_layout.setSpacing(4)

        self._sub_tabs = []
        sub_items = [
            ("General Preferences", 5),
            ("Edge Configuration (Jetson)", 4),
        ]

        for label, idx in sub_items:
            btn = QPushButton(label)
            btn.setObjectName("SubTabButton")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, x=idx, b=btn: self._nav_sub(x, b))
            self._sub_tabs.append(btn)
            sub_layout.addWidget(btn, alignment=Qt.AlignVCenter)

        sub_layout.addStretch()
        self.sub_nav.setVisible(False)
        outer.addWidget(self.sub_nav)

        # ── 3. Screen Stack ───────────────────────────────────────────────────
        self.stack = QStackedWidget()
        self.stack.setObjectName("ContentArea")
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.dashboard_screen = DashboardScreen(self.db, self.alert_service)
        # Redirect Dashboard 'Configure' button to the Jetson screen in the stack (Index 4)
        self.dashboard_screen.configure_clicked.connect(lambda: self._jump_to_index(4))
        # Retry clicked will now just try to broadcast a START command to connected edges
        self.dashboard_screen.retry_clicked.connect(lambda: self.ws_thread.send_command("START") if hasattr(self, "ws_thread") else None)
        self.cameras_screen   = CamerasScreen(self.db, self.alert_service)

        # Drop feeds instantly on UI delete
        self.cameras_screen.camera_deleted.connect(self._on_camera_deleted)
        self.cameras_screen.camera_deleted.connect(self.dashboard_screen.remove_camera_feed)
        self.cameras_screen.camera_renamed.connect(
            lambda cid, name: self.dashboard_screen._feeds[cid].set_camera_name(name) if cid in self.dashboard_screen._feeds else None
        )
        self.alerts_screen    = AlertsScreen(self.db, self.alert_service)
        self.analytics_screen = AnalyticsScreen(self.db)
        self.analytics_screen.nav_requested.connect(self._jump_to_index)
        self.jetson_screen    = JetsonScreen(db=self.db, ws_server=self.ws_thread)
        self.settings_screen  = SettingsScreen(self.db)
        self.settings_screen.alert_service = self.alert_service # Add this

        for scr in [self.dashboard_screen, self.cameras_screen,
                    self.alerts_screen, self.analytics_screen,
                    self.jetson_screen, self.settings_screen]:
            self.stack.addWidget(scr)

        outer.addWidget(self.stack, stretch=1)

        # ── 4. Status Bar ─────────────────────────────────────────────────────
        status_bar = QWidget()
        status_bar.setObjectName("StatusBar")
        status_bar.setFixedHeight(30)

        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(16, 0, 16, 0)
        status_layout.setSpacing(8)
        
        # Pulse dot
        self._health_dot = QLabel()
        self._health_dot.setFixedSize(8, 8)
        self._health_dot.setStyleSheet("background: #10B981; border-radius: 4px;")
        opacity_effect = QGraphicsOpacityEffect(self._health_dot)
        self._health_dot.setGraphicsEffect(opacity_effect)
        self._health_anim = QPropertyAnimation(opacity_effect, b"opacity", self._health_dot)
        self._health_anim.setDuration(1500)
        self._health_anim.setStartValue(1.0)
        self._health_anim.setEndValue(0.2)
        self._health_anim.setLoopCount(-1)
        self._health_anim.start()
        status_layout.addWidget(self._health_dot, alignment=Qt.AlignVCenter)

        self._sys_health_lbl = QLabel("<span style='color:#64748B'>SYSTEM HEALTH:</span> <span style='color:#10B981'>ONLINE</span>")
        self._sys_health_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        status_layout.addWidget(self._sys_health_lbl, alignment=Qt.AlignVCenter)
        
        status_layout.addSpacing(16)

        self._status_bar_lbl = QLabel("<span style='color:#64748B'>STATUS:</span> <span style='color:#F1F5F9'>FastAPI Initializing...</span>")
        status_layout.addWidget(self._status_bar_lbl, alignment=Qt.AlignVCenter)

        status_layout.addStretch()

        self._cam_count_lbl = QLabel("<span style='color:#64748B'>CAMERAS ONLINE:</span> <span style='color:#F1F5F9'>0</span>")
        status_layout.addWidget(self._cam_count_lbl, alignment=Qt.AlignVCenter)

        outer.addWidget(status_bar)

        # Start on Dashboard
        self._nav_main(0, self._top_tabs[0])

    # ── Navigation Logic ──────────────────────────────────────────────────────
    def _clear_active_states(self):
        for btn in self._top_tabs:
            btn.setProperty("active", "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        self._gear_btn.setProperty("active", "false")
        self._gear_btn.style().unpolish(self._gear_btn)
        self._gear_btn.style().polish(self._gear_btn)

        for btn in self._sub_tabs:
            btn.setProperty("active", "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _push_history(self, idx: int):
        self._nav_history = self._nav_history[: self._nav_pos + 1]
        if not self._nav_history or self._nav_history[-1] != idx:
            self._nav_history.append(idx)
            self._nav_pos = len(self._nav_history) - 1
        self._update_nav_arrows()

    def _update_nav_arrows(self):
        self._back_btn.setEnabled(self._nav_pos > 0)
        self._fwd_btn.setEnabled(self._nav_pos < len(self._nav_history) - 1)

    def _nav_back(self):
        if self._nav_pos > 0:
            self._nav_pos -= 1
            idx = self._nav_history[self._nav_pos]
            self._jump_to_index(idx)
            self._update_nav_arrows()

    def _nav_forward(self):
        if self._nav_pos < len(self._nav_history) - 1:
            self._nav_pos += 1
            idx = self._nav_history[self._nav_pos]
            self._jump_to_index(idx)
            self._update_nav_arrows()

    def _nav_main(self, idx: int, active_btn: QPushButton, alert_id: int = -1):
        self.sub_nav.setVisible(False)
        self._clear_active_states()
        active_btn.setProperty("active", "true")
        active_btn.style().unpolish(active_btn)
        active_btn.style().polish(active_btn)
        self.stack.setCurrentIndex(idx)
        self._push_history(idx)
        
        if idx == 2 and alert_id != -1:
            self.alerts_screen.focus_alert(alert_id)

    def _jump_to_index(self, idx: int, alert_id: int = -1):
        self.sub_nav.setVisible(idx in (4, 5))
        self._clear_active_states()
        self.stack.setCurrentIndex(idx)
        tab_map = {0: 0, 1: 1, 3: 2, 2: 3}
        tab_pos = tab_map.get(idx)
        if tab_pos is not None and tab_pos < len(self._top_tabs):
            btn = self._top_tabs[tab_pos]
            btn.setProperty("active", "true")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        elif idx in (4, 5):
            self._gear_btn.setProperty("active", "true")
            self._gear_btn.style().unpolish(self._gear_btn)
            self._gear_btn.style().polish(self._gear_btn)
        
        self._push_history(idx)
        
        if idx == 2 and alert_id != -1:
            self.alerts_screen.focus_alert(alert_id)

    def _nav_settings(self):
        self.sub_nav.setVisible(True)
        self._clear_active_states()
        self._gear_btn.setProperty("active", "true")
        self._gear_btn.style().unpolish(self._gear_btn)
        self._gear_btn.style().polish(self._gear_btn)
        self._nav_sub(5, self._sub_tabs[0])

    def _nav_sub(self, idx: int, active_btn: QPushButton):
        for btn in self._sub_tabs:
            btn.setProperty("active", "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        active_btn.setProperty("active", "true")
        active_btn.style().unpolish(active_btn)
        active_btn.style().polish(active_btn)
        self.stack.setCurrentIndex(idx)
        self._push_history(idx)

    # ── Stylesheet ────────────────────────────────────────────────────────────
    def _load_stylesheet(self):
        if DARK_THEME_QSS.exists():
            with open(DARK_THEME_QSS, "r", encoding="utf-8") as f:
                self.setStyleSheet(f.read())
        else:
            logger.warning("dark_theme.qss not found at %s", DARK_THEME_QSS)

    # ── WebSocket Server ──────────────────────────────────────────────────────
    def _setup_ws(self):
        # ws_thread is now initialized in __init__ before _build_ui
        self.ws_thread.frame_received.connect(self.on_frame_received)
        self.ws_thread.alert_confirmed.connect(self.on_alert_confirmed)
        self.ws_thread.camera_connected.connect(self.on_camera_connected)
        self.ws_thread.camera_offline.connect(self.on_camera_offline)
        self.ws_thread.edge_connected.connect(self.dashboard_screen.set_edge_connected)
        self.ws_thread.edge_disconnected.connect(self.dashboard_screen.set_edge_disconnected)
        self.ws_thread.edge_health.connect(self.dashboard_screen.set_edge_health)

        # Simple Way: Automatically send START when edge connects
        self.ws_thread.edge_connected.connect(lambda cnt: self.ws_thread.send_command("START") if cnt > 0 else None)

        self.ws_thread.start()
        # Give screens access to ws_thread for edge reload notifications
        self.cameras_screen.ws_thread = self.ws_thread
        self.settings_screen.ws_thread = self.ws_thread

        QTimer.singleShot(1000, lambda: self._status_bar_lbl.setText(
            "<span style='color:#64748B'>STATUS:</span> <span style='color:#F1F5F9'>FastAPI Port: 8000</span>"
        ))

    def closeEvent(self, event):
        """Clean shutdown: Stop remote pipeline via WebSocket and local servers."""
        logger.info("Shutting down FireGuard...")
        
        # Stop remote pipeline via WebSocket (The fast way)
        if hasattr(self, 'ws_thread') and self.ws_thread:
            try:
                self.ws_thread.send_command("STOP")
                # Briefly wait for command to reach edge
                time.sleep(0.5) 
                self.ws_thread.stop()
                self.ws_thread.wait(1000)
            except Exception: pass
            
        event.accept()

    # ── Tray Icon ─────────────────────────────────────────────────────────────
    def _setup_tray(self):
        try:
            self._tray = QSystemTrayIcon(self)
            self._tray.setIcon(self._app_icon)
            self._tray.setToolTip("FireGuard — AI Fire Detection")
            tray_menu = QMenu()
            tray_menu.addAction("Show", self.show)
            tray_menu.addAction("Quit", QApplication.quit)
            self._tray.setContextMenu(tray_menu)
            self._tray.show()
        except Exception:
            pass

    @staticmethod
    def _generate_icon() -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor(0, 0, 0, 0))
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#e3000f"))
        p.drawRoundedRect(4, 4, 56, 56, 12, 12)
        p.setPen(QPen(QColor(255, 255, 255, 240)))
        p.setFont(QFont("Segoe UI", 32, QFont.Weight.ExtraBold))
        p.drawText(QRectF(4, 4, 56, 56), Qt.AlignCenter, "F")
        p.end()
        return QIcon(pixmap)

    # ── Slots: WebSocket Events ───────────────────────────────────────────────
    @Slot(int, str)
    def on_camera_connected(self, cam_id: int, name: str):
        # Auto-register camera if it connects
        if cam_id in self._recently_deleted_cams:
            self._recently_deleted_cams.remove(cam_id)

        self.db.upsert_camera(cam_id, name)
        self.cameras_screen.update_camera_status(cam_id, True)
        self.dashboard_screen.refresh_stats()
        stats = self.db.get_stats()
        self._cam_count_lbl.setText(f"<span style='color:#64748B'>CAMERAS ONLINE:</span> <span style='color:#F1F5F9'>{stats.get('cameras_online', 0)}</span>")

    @Slot(int)
    def on_camera_offline(self, cam_id: int):
        self.db.mark_camera_offline(cam_id)
        self.dashboard_screen.mark_camera_offline(cam_id)
        self.dashboard_screen.refresh_stats()
        self.cameras_screen.update_camera_status(cam_id, False)
        stats = self.db.get_stats()
        self._cam_count_lbl.setText(f"<span style='color:#64748B'>CAMERAS ONLINE:</span> <span style='color:#F1F5F9'>{stats.get('cameras_online', 0)}</span>")

    @Slot(dict, bytes)
    def on_frame_received(self, meta: dict, jpeg_bytes: bytes):
        cam_id   = meta["cam_id"]
        # Ignore frames for cameras that were just deleted (Edge latency)
        if cam_id in self._recently_deleted_cams:
            return

        cam_name = meta.get("name", f"Camera {cam_id}")
        is_alert = bool(meta.get("alert", False))
        self.dashboard_screen.update_camera_feed(cam_id, cam_name, jpeg_bytes, is_alert, metadata=meta)

    def _on_camera_deleted(self, cam_id: int):
        """Add to ignore list for 5 seconds while Edge reloads."""
        self._recently_deleted_cams.add(cam_id)
        # Use discard to avoid KeyError if already removed
        QTimer.singleShot(5000, lambda: self._recently_deleted_cams.discard(cam_id))

    @Slot(dict, bytes)
    def on_alert_confirmed(self, meta: dict, jpeg_bytes: bytes):
        try:
            alert_id = self.alert_service.process_alert(meta, jpeg_bytes)
            
            # Send alert_id back to edge for media upload
            self.ws_thread.send_alert_confirmation(alert_id)

            detections = meta.get("detections", [])
            best_det   = max(detections, key=lambda d: d["conf"]) if detections else None
            label      = best_det["label"]  if best_det else "fire"
            confidence = best_det["conf"]   if best_det else 0.0
            threat     = self.alert_service.confidence_to_threat(label, confidence)

            cam_name = meta.get("name", f"Cam {meta['cam_id']}")
            self.dashboard_screen.push_alert_ticker(cam_name, label, threat, confidence)
            self.alerts_screen.on_new_alert()
            self.analytics_screen.refresh()
            self._beep(threat)

            self._status_bar_lbl.setText(
                f"<span style='color:#64748B'>LATEST INCIDENT:</span> <span style='color:#F1F5F9'>[{threat}] {label.upper()} ON {cam_name.upper()}  "
                f"({confidence*100:.0f}%)</span>"
            )
        except Exception as e:
            logger.error("Alert processing failed: %s", e)

    def _beep(self, threat: str):
        # Fetch actual user preferences for audio notifications
        def get_beep(t): return self.db.get_config(f"beep_{t.lower()}", "0") == "1"
        beep_map = {
            "CRITICAL": get_beep("CRITICAL"),
            "HIGH": get_beep("HIGH"),
            "MEDIUM": get_beep("MEDIUM"),
            "LOW": get_beep("LOW")
        }
        
        if not beep_map.get(threat, False):
            return
            
        try:
            from server.utils.beep import _beep_sync
            # Define sound patterns (freq, duration_ms, repeats, gap_ms)
            patterns = {
                "CRITICAL": (1200, 300, 5, 150), 
                "HIGH": (1000, 400, 3, 200),
                "MEDIUM": (800, 300, 2, 200),
                "LOW": (600, 200, 1, 0)
            }
            if threat in patterns:
                freq, dur, count, gap = patterns[threat]
                threading.Thread(
                    target=_beep_sync, args=(freq, dur, count, gap), daemon=True
                ).start()
        except Exception:
            pass


from server.utils.logger import setup_logging

if __name__ == "__main__":
    # Ensure logs and storage exist before starting
    setup_logging()
    
    app = QApplication(sys.argv)
    app.setApplicationName("FireGuard")
    app.setOrganizationName("FireGuard Systems")

    font = QFont("Segoe UI", 10)
    app.setFont(font)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())
