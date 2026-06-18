<p align="center">
  <img src="https://img.shields.io/badge/FireGuard-AI%20Fire%20Detection-FF3D00?style=flat-square&logoColor=white" alt="FireGuard"/>
</p>

<h1 align="center">
  🔥 FireGuard
</h1>

<p align="center">
  <strong>End-to-end AI fire & smoke detection system — edge inference on NVIDIA Jetson, WebSocket streaming, and a professional Windows desktop dashboard.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/YOLO-v26s%20Custom-FF4500?style=flat-square&logo=pytorch&logoColor=white" alt="YOLO"/>
  <img src="https://img.shields.io/badge/PySide6-Qt%206-41CD52?style=flat-square&logo=qt&logoColor=white" alt="PySide6"/>
  <img src="https://img.shields.io/badge/FastAPI-WebSocket-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/NVIDIA-Jetson%20Edge-76B900?style=flat-square&logo=nvidia&logoColor=white" alt="Jetson"/>
  <img src="https://img.shields.io/badge/mAP%4050-80.48%25-brightgreen?style=flat-square" alt="mAP"/>
</p>

<p align="center">
  <a href="#overview">Overview</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#demo">Demo</a> ·
  <a href="#model-performance">Model</a> ·
  <a href="#tech-stack">Tech Stack</a> ·
  <a href="#getting-started">Getting Started</a> ·
  <a href="#deployment">Deployment</a> ·
  <a href="#configuration">Configuration</a>
</p>

---

## Overview

FireGuard is a production-grade, full-stack fire safety system built as a Final Year Project. It combines a **custom-trained YOLOv26s model**, a **real-time edge inference pipeline**, and a **professional desktop control center** into a single cohesive product.

The system is designed for real environments: warehouses, server rooms, industrial facilities, and campus buildings — anywhere that standard smoke detectors are too slow or too coarse.

**Three integrated layers:**

| Layer | Technology | Role |
|---|---|---|
| **Edge Node** | YOLOv26s + PyTorch on Jetson/Linux | Real-time GPU inference on camera streams |
| **Central Server** | FastAPI + SQLite over WebSocket | Receives detections, persists data, serves UI |
| **Desktop Dashboard** | PySide6 (Qt 6) on Windows | Command center — live feeds, alerts, analytics |

---

## Architecture

```mermaid
graph TD
    classDef default fill:#1E1E1E,stroke:#333,stroke-width:2px,color:#fff;
    classDef edge fill:#1565C0,stroke:#0D47A1,stroke-width:2px,color:#fff;
    classDef server fill:#6A1B9A,stroke:#4A148C,stroke-width:2px,color:#fff;
    classDef ui fill:#2E7D32,stroke:#1B5E20,stroke-width:2px,color:#fff;
    classDef camera fill:#E65100,stroke:#BF360C,stroke-width:2px,color:#fff;
    classDef db fill:#00695C,stroke:#004D40,stroke-width:2px,color:#fff;

    subgraph Sources["📷 Video Sources"]
        C1["IP Cameras (RTSP)"]:::camera
        C2["USB Webcams"]:::camera
    end

    subgraph EdgeNode["⚙️ Edge Node (NVIDIA Jetson / Linux PC)"]
        direction TB
        Capture["OpenCV VideoCapture"]:::edge
        YOLO["YOLOv26s Inference (GPU/CPU)"]:::edge
        Logic["Frame Buffering & Alert Logic"]:::edge
        Storage1["Snapshot & Clip Storage"]:::db
        WS_Client["WebSocket Client"]:::edge

        Capture -->|Raw Frames| YOLO
        YOLO -->|Detections| Logic
        Logic -->|Save| Storage1
        Logic -->|Package Payload| WS_Client
    end

    subgraph CentralServer["🌐 Central Hub (FastAPI Server)"]
        direction TB
        WS_Server["WebSocket Server"]:::server
        AlertProc["Alert Processor & Health Aggregation"]:::server
        SQLite[("SQLite Database\n(WAL Mode)")]:::db
        WS_Broadcaster["WebSocket Broadcaster"]:::server

        WS_Server -->|Receive JSON+JPEG| AlertProc
        AlertProc -->|Persist Data| SQLite
        AlertProc -->|Forward Alerts| WS_Broadcaster
    end

    subgraph DesktopApp["💻 Desktop Dashboard (PySide6 / Windows)"]
        direction TB
        UI["PySide6 Dashboard UI"]:::ui
        PyQtGraph["Real-time Analytics Charts"]:::ui
        Audio["Audio Alarms"]:::ui
        SSH["Remote Jetson SSH Manager"]:::ui
        
        UI --- PyQtGraph
        UI --- Audio
        UI --- SSH
    end

    C1 -->|RTSP Stream| Capture
    C2 -->|USB Stream| Capture
    WS_Client -->|"WebSocket (500ms intervals)"| WS_Server
    WS_Broadcaster -->|WebSocket Broadcast| UI
    SSH -.->|"SSH Commands<br>Config Deploy & Restart"| EdgeNode
```

**Data pipeline at a glance:**

```
Camera Frame → OpenCV Capture → Queue Buffer → YOLOv26s Inference
    → Detection? → Save Snapshot/Clip → WebSocket Transmission (500ms)
    → FastAPI receives → SQLite persist → PySide6 Dashboard update
    → Audio alarm + Alert Log entry
```

| Stage | Tech | Detail |
|---|---|---|
| Capture | `cv2.VideoCapture` | RTSP / USB, stride=2 |
| Inference | YOLOv26s 640×640 | conf ≥ 0.80, IoU ≥ 0.61, batch=2 |
| Noise filter | Consecutive frame gate | 10 frames minimum before alert fires |
| Transmission | WebSocket JSON | 500 ms interval, 60% JPEG quality |
| Persistence | SQLite WAL | 30 s timeout, zero-config setup |
| UI | PySide6 + PyQtGraph | Dark mode, real-time charts |

---

## Demo

> **Live detection** — the system detects and classifies fire and smoke from live camera feeds with sub-second latency.

### Dashboard — Fire Detected (89% confidence)

![Dashboard Fire](model/app_screen_shots/Screenshot%202026-06-16%20151902.png)

*A critical fire event triggers an immediate audio alarm, highlights the banner in red, and logs the incident with a timestamped snapshot.*

### Dashboard — Smoke Detected (86% confidence)

![Dashboard Smoke](model/app_screen_shots/Screenshot%202026-06-16%20152013.png)

### Camera Management

![Camera Config](model/app_screen_shots/Screenshot%202026-06-16%20152335.png)

*Add and test RTSP IP cameras or USB webcams. Each camera shows its connection type, last-seen time, and online/offline status.*

### Alert Log

![Alert Log](model/app_screen_shots/Screenshot%202026-06-16%20152412.png)

*Complete audit trail — filter by camera, threat type, or time range. Inline snapshot preview. Acknowledge workflow. Export to CSV.*

### Analytics & Reporting

![Analytics](model/app_screen_shots/Screenshot%202026-06-16%20152346.png)

*Alert timeline chart, threat distribution by class, activity ranking by camera, and recent incidents panel.*

### System Settings

![Settings](model/app_screen_shots/Screenshot%202026-06-16%20152422.png)

*Per-severity detection thresholds and audio notification toggles. All settings persist across restarts.*

### Edge Configuration (Remote Jetson Management)

![Edge Config](model/app_screen_shots/Screenshot%202026-06-16%20152432.png)

*SSH into the Jetson edge node directly from the desktop — update model weights, confidence thresholds, and server URL, then redeploy with one click.*

---

## Features

- **Real-time dual-class detection** — fire and smoke, independently and simultaneously
- **Sub-second alert latency** from event to dashboard notification and audio alarm
- **Multi-camera support** — unlimited RTSP IP cameras + USB webcams, auto-reconnect on drop
- **Noise-resistant detection** — configurable consecutive-frame gate prevents false positives
- **Bandwidth-efficient** — only detection payloads (JSON + JPEG) transmitted, never raw video
- **Complete alert lifecycle** — log, snapshot preview, acknowledge, filter, CSV export
- **Live system telemetry** — Server CPU/RAM and Edge CPU/RAM/GPU displayed in real-time
- **Remote Jetson management** — SSH-based config deploy & pipeline restart from dashboard
- **SQLite persistence** — zero-config database with WAL mode, configurable retention policy
- **Windows installer** — one-click setup via Inno Setup packaged installer
- **Jetson one-liner** — automated install + systemd service via `install.sh`

---

## Model Performance

The detection backbone is a **custom-trained YOLOv26s** fine-tuned on 21,000 annotated fire and smoke images over 200 epochs.

### Training Report

![Training Report](model/26s_runs%20(2)/fireguard_v2_showcase_different.png)

### Training Curves (200 Epochs)

![Training Curves](model/26s_runs%20(2)/runs/detect/fireguard_outputs/fire_smoke_26s/results.png)

### Final Metrics — Best Checkpoint (Epoch 167)

| Metric | Value |
|---|---|
| Overall mAP@50 | **80.48%** |
| mAP@50-95 | **51.87%** |
| Fire mAP@50 | **89.0%** |
| Smoke mAP@50 | **87.0%** |
| Average F1 Score | **85.8%** |
| Fire Precision | 91.7% |
| Fire Recall | 85.4% |
| Smoke Precision | 93.1% |
| Smoke Recall | 82.3% |

### Training Configuration

| Parameter | Value |
|---|---|
| Architecture | YOLOv26s |
| Dataset | 21,000 images — Fire / Smoke / Other |
| Epochs | 200 &nbsp;(best @ epoch 167) |
| Image size | 640 × 640 px |
| Batch size | 16 |
| Optimizer | SGD &nbsp;(lr₀ = 0.01, cosine annealing, patience = 50) |
| Augmentation | Mosaic, MixUp (0.1), CopyPaste (0.2), RandAugment |
| Hardware | CUDA GPU (device 0) |

### Validation Predictions

| Ground Truth | Model Output |
|:---:|:---:|
| ![Labels](model/26s_runs%20(2)/runs/detect/val/val_batch0_labels.jpg) | ![Predictions](model/26s_runs%20(2)/runs/detect/val/val_batch0_pred.jpg) |
| ![Labels](model/26s_runs%20(2)/runs/detect/val/val_batch1_labels.jpg) | ![Predictions](model/26s_runs%20(2)/runs/detect/val/val_batch1_pred.jpg) |

---

## Tech Stack

**Desktop Application (Windows)**

| Component | Technology |
|---|---|
| Desktop UI | PySide6 — Qt 6 |
| Charts | PyQtGraph |
| API server | FastAPI + Uvicorn |
| WebSocket | `websockets` |
| Image processing | OpenCV, Pillow |
| Database | SQLite (WAL mode) |
| Remote SSH | Paramiko |
| Packaging | PyInstaller + Inno Setup |

**Edge Node (Jetson Nano / Linux)**

| Component | Technology |
|---|---|
| Inference engine | Ultralytics YOLOv26s |
| Deep learning | PyTorch (NVIDIA-optimized) |
| Camera capture | OpenCV VideoCapture |
| Server transport | WebSocket client |
| Service management | systemd |

---

## Project Structure

```
FireGuard/
├── app.py                        # Entry point — starts FastAPI + PySide6
├── FireGuard_fixed.spec          # PyInstaller build spec
├── FireGuard_Setup.iss           # Inno Setup installer configuration
├── JETSON_SETUP_GUIDE.txt        # Step-by-step Jetson deployment guide
│
├── server/                       # Central server + desktop UI
│   ├── screens/                  # PySide6 screens (Home, Cameras, Alerts, Performance)
│   ├── services/                 # WebSocket hub, alert processor, background tasks
│   ├── database/                 # SQLite models and query helpers
│   ├── workers/                  # Async task workers
│   ├── utils/                    # Logger, path resolver, shared utilities
│   ├── config/                   # Configuration loader
│   ├── assets/                   # UI sounds and QSS stylesheets
│   ├── config.py                 # Pydantic settings model
│   ├── requirements.txt          # Server dependencies
│   └── .env.example              # Environment variable template  ← copy to .env
│
├── edge/                         # Edge inference node
│   ├── main.py                   # Pipeline entry point
│   ├── live_viewer.py            # Standalone detection viewer
│   ├── config.yaml               # Camera, model, and server configuration
│   ├── best.pt                   # Model weights  [download from Releases]
│   ├── install.sh                # Automated Jetson installer
│   ├── requirements.txt          # Edge dependencies
│   ├── inference/                # YOLOv26s inference wrapper
│   ├── stream/                   # Camera capture and frame buffering
│   ├── transmission/             # WebSocket client and alert packaging
│   ├── monitoring/               # CPU / RAM / GPU stats collector
│   └── alerts/                   # Saved snapshots and video clips
│
├── model/                        # Model artifacts and training results
│   ├── app_screen_shots/         # Application UI screenshots
│   └── 26s_runs (2)/             # Full YOLOv26s training run
│       ├── fireguard_v2_showcase_different.png
│       └── runs/detect/          # Per-epoch metrics, confusion matrices, val batches
│
└── storage/                      # Runtime data — auto-created, git-ignored
    ├── clips/
    ├── snapshots/
    ├── exports/
    └── fire.db
```

---

## Getting Started

### Prerequisites

- Python 3.10 or higher
- Git

### Desktop App — Windows

**Option 1: Installer (recommended)**

Download `FireGuard_Installer_v1.0.exe` from the [Releases](../../releases) page and run it. The installer handles everything — dependencies, directory structure, and a Start Menu shortcut.

**Option 2: Run from source**

```powershell
git clone https://github.com/Tanveer457/FireGuard.git
cd FireGuard

python -m venv server\venv
server\venv\Scripts\activate

pip install -r server\requirements.txt

copy server\.env.example server\.env
# Open server\.env and fill in your tokens and storage paths

python app.py
```

The FastAPI server starts on `http://localhost:8000` and the desktop dashboard opens automatically.

---

### Edge Node — Jetson Nano / Linux

**One-line install**

```bash
curl -sSL https://raw.githubusercontent.com/Tanveer457/FireGuard/main/edge/install.sh | bash
```

This script installs NVIDIA-optimized PyTorch, all Python dependencies, and registers a `systemd` service that starts the detection pipeline automatically on boot.

**Manual setup**

```bash
git clone https://github.com/Tanveer457/FireGuard.git
cd FireGuard/edge

python3 -m venv edge_venv
source edge_venv/bin/activate

pip install -r requirements.txt

# Place model weights
# Download best.pt from the Releases page and put it in edge/

# Edit cameras, model, and server settings
nano config.yaml

python main.py
```

---

## Configuration

### Edge node — `edge/config.yaml`

```yaml
cameras:
  - id: 1
    name: "Front Entrance"
    url: "rtsp://admin:password@192.168.1.100:554/stream"  # RTSP IP camera
  - id: 2
    name: "Workstation Area"
    url: 0                                                  # USB webcam (index 0)

model:
  path: best.pt
  conf: 0.80        # Minimum confidence to trigger detection
  iou: 0.61         # IoU threshold for non-maximum suppression
  imgsz: 640        # Input resolution
  device: '0'       # '0' = first GPU,  'cpu' = CPU-only mode

server:
  url: "ws://YOUR_SERVER_IP:8000/ws/edge"
  token: "your-edge-secret-token"     # Must match EDGE_TOKEN in server/.env

alert:
  min_consecutive: 10    # Consecutive positive frames before firing an alert
  cooldown_sec: 10       # Minimum gap between two alerts from the same camera
  clip_duration_sec: 10  # Duration of saved video clip per alert
  save_clips: true
  save_snapshots: true
```

### Server — `server/.env`

Copy `server/.env.example` to `server/.env` and fill in your values.

| Variable | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | HTTP and WebSocket port |
| `EDGE_TOKEN` | — | Shared secret — must match edge `config.yaml` |
| `DASHBOARD_TOKEN` | — | Desktop dashboard authentication token |
| `STORAGE_ROOT` | `./storage` | Root directory for clips and snapshots |
| `ALERT_RETENTION_DAYS` | `30` | Automatically purge alerts older than N days |
| `MAX_SNAPSHOT_SIZE_MB` | `5` | Reject oversized JPEG payloads from edge |
| `LOG_LEVEL` | `info` | Logging verbosity: `debug` / `info` / `warning` |

> **Important:** Never commit `server/.env`. It contains authentication secrets. Use the provided `.env.example` as a template for collaborators.

---

## Deployment

### Windows — Build the Installer (`.exe`)

The project ships with a ready-made [PyInstaller spec](FireGuard_fixed.spec) and an [Inno Setup script](FireGuard_Setup.iss).

#### Prerequisites
- [PyInstaller](https://pyinstaller.org/) — `pip install pyinstaller` inside `server\venv`
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) — free download, install on Windows

#### Step 1 — Activate the virtual environment

```powershell
server\venv\Scripts\activate
```

#### Step 2 — Compile to a standalone folder with PyInstaller

```powershell
pyinstaller FireGuard_fixed.spec --noconfirm
```

This produces `dist\FireGuard\FireGuard.exe` — a fully self-contained folder with all Python dependencies bundled. The build spec automatically:
- Collects all `server/` sub-packages and hidden imports (`fastapi`, `uvicorn`, `starlette`, `websockets`, `PySide6`, `paramiko`, `pyqtgraph`, `cv2`)
- Bundles `server/assets/` (QSS themes, sounds)
- Applies the red "F" icon (`fireguard.ico`)
- Sets `console=False` for a clean windowless app

> **Tip:** If you see `ModuleNotFoundError` at runtime, open `FireGuard_fixed.spec`, add the missing module to `hidden_imports`, and rebuild.

#### Step 3 — Package into a professional installer with Inno Setup

1. Open **Inno Setup Compiler** (installed in Step 0)
2. Go to **File → Open** and select `FireGuard_Setup.iss`
3. Press **Ctrl + F9** (or click **Build → Compile**)
4. The finished installer is output to: `Output\FireGuard_Installer_v1.0.exe`

The installer:
- Installs FireGuard to `C:\Program Files\FireGuard\` by default
- Creates Start Menu shortcuts and an optional Desktop shortcut
- Bundles `JETSON_SETUP_GUIDE.txt` and shows it on install completion
- Sets folder permissions so the app can write its SQLite database and logs
- Excludes local `storage\` and `logs\` for a clean first-run experience

#### Step 4 — Distribute

Share `Output\FireGuard_Installer_v1.0.exe` with users or upload it to the [Releases](../../releases) page on GitHub. Users just double-click and run — no Python required.

---

### Jetson Nano — Run Edge as a Background Service

The edge detection pipeline is designed to run as a **silent systemd service** that starts automatically on boot and restarts if it crashes.

#### Option A: Automated install (recommended)

Run the one-liner on the Jetson — it does everything automatically:

```bash
curl -sSL https://raw.githubusercontent.com/Tanveer457/FireGuard/main/edge/install.sh | bash
```

The script will:
1. Update `apt` and install system dependencies (`git`, `python3-pip`, `libopencv-dev`)
2. Verify CUDA / JetPack installation
3. Install NVIDIA-optimised PyTorch (correct wheel for JetPack 4.6.1)
4. Clone the repo into `~/fireguard/`
5. Install Python requirements from `requirements.txt`
6. Ask for your Windows PC IP address and update `config.yaml` automatically
7. Write and register the `fireguard-edge.service` systemd unit
8. Enable it on boot and start it immediately

#### Option B: Manual systemd setup

If you cloned the repo manually, create the service file yourself:

```bash
sudo nano /etc/systemd/system/fireguard-edge.service
```

Paste this (replace `/home/YOUR_USER/fireguard` with your actual path):

```ini
[Unit]
Description=FireGuard Edge Detection Pipeline
After=network.target

[Service]
WorkingDirectory=/home/YOUR_USER/fireguard/edge
ExecStart=/usr/bin/python3 /home/YOUR_USER/fireguard/edge/main.py
Restart=always
RestartSec=10
User=YOUR_USER
Environment=PYTHONPATH=/home/YOUR_USER/fireguard/edge
StandardOutput=append:/home/YOUR_USER/fireguard/edge/logs/service.log
StandardError=append:/home/YOUR_USER/fireguard/edge/logs/service.log

[Install]
WantedBy=multi-user.target
```

Then enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable fireguard-edge   # Start on every boot
sudo systemctl start  fireguard-edge   # Start right now
```

#### Managing the service day-to-day

```bash
sudo systemctl status  fireguard-edge        # Is it running?
sudo systemctl restart fireguard-edge        # Restart after config changes
sudo systemctl stop    fireguard-edge        # Stop the service
sudo systemctl disable fireguard-edge        # Remove from boot

# Live log streaming
tail -f ~/fireguard/edge/logs/service.log
# or via journald
sudo journalctl -u fireguard-edge -f
```

#### Updating model weights on the Jetson

```bash
# Stop the service first
sudo systemctl stop fireguard-edge

# Replace the weights file
cp /path/to/new_best.pt ~/fireguard/edge/best.pt

# Restart
sudo systemctl start fireguard-edge
```

> **Alternatively**, use the **Edge Configuration screen** in the desktop dashboard — it SSH-es into the Jetson, uploads a new `best.pt`, updates `config.yaml`, and restarts the service, all with one click.

---

## Academic Context

| | |
|---|---|
| **Type** | Final Year Project (FYP) |
| **Domain** | Computer Vision · Artificial Intelligence · IoT |
| **Model** | YOLOv26s — custom fine-tuned |
| **Dataset** | 21,000+ annotated images (fire, smoke, negative) |
| **Platforms** | Windows 10/11 (server + UI) · NVIDIA Jetson (edge) |

---

<p align="center">
  <sub>FireGuard — built for real-world fire safety. ⭐ Star the repo if you found it useful.</sub>
  <sub>Built by Tanveer Younas & Team.</sub>
</p>
