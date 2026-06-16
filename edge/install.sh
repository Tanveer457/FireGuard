#!/bin/bash
# ==============================================================================
# FIREGUARD EDGE — AUTOMATED INSTALLER FOR JETSON NANO
# ==============================================================================

set -e # Exit on error

echo "----------------------------------------------------------------"
echo "🔥 Starting FireGuard Edge Installation..."
echo "----------------------------------------------------------------"

# 1. Update System
echo "Checking for system updates..."
sudo apt-get update -y

# 2. Install System Dependencies
echo "Installing core dependencies (OpenCV, Python3, Git)..."
sudo apt-get install -y python3-pip git libopencv-dev python3-opencv

# 3. Check for CUDA
echo "Verifying CUDA installation..."
if [ -d "/usr/local/cuda" ]; then
    echo "✅ CUDA found at /usr/local/cuda"
else
    echo "⚠️ CUDA not found in /usr/local/cuda. Please ensure JetPack is installed correctly."
    exit 1
fi

# 4. Install PyTorch for Jetson (NVIDIA Optimized)
# Standard 'pip install torch' will NOT work correctly on Jetson Nano.
if python3 -c "import torch; print(torch.cuda.is_available())" 2>/dev/null | grep -q "True"; then
    echo "✅ PyTorch with CUDA is already installed."
else
    echo "📦 Installing NVIDIA-optimized PyTorch for Jetson (JetPack 4.6.1)..."
    # Note: These URLs are for JetPack 4.6.1. Users on other versions may need different wheels.
    sudo apt-get install -y libopenblas-base libopenmpi-dev
    export TORCH_INSTALL=https://developer.download.nvidia.com/compute/redist/jp/v461/pytorch/torch-1.10.1-cp36-cp36m-linux_aarch64.whl
    pip3 install --user --no-cache-dir $TORCH_INSTALL
fi

# 5. Clone Repository
# Note: User must replace YOUR_USERNAME and YOUR_REPO with actual values
REPO_URL="https://github.com/YOUR_USERNAME/YOUR_REPO.git"
INSTALL_DIR="$HOME/fireguard"

if [ -d "$INSTALL_DIR" ]; then
    echo "Folder $INSTALL_DIR already exists. Updating code..."
    cd "$INSTALL_DIR"
    git pull
else
    echo "Cloning FireGuard Edge into $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# 4. Install Python Requirements
echo "Installing Python libraries..."
pip3 install -r requirements.txt

# 5. Configuration
echo "----------------------------------------------------------------"
echo "CONFIGURATION"
echo "----------------------------------------------------------------"
read -p "Enter your Windows PC IP Address (e.g. 192.168.1.50): " SERVER_IP

# Update config.yaml with the user's IP
if [ -f "config.yaml" ]; then
    sed -i "s|url: ws://.*:8000/ws/edge|url: ws://$SERVER_IP:8000/ws/edge|g" config.yaml
    echo "Configured server URL: ws://$SERVER_IP:8000/ws/edge"
fi

# 6. Create Systemd Service (Background Service)
echo "Creating background service (fireguard-edge.service)..."
sudo mkdir -p $INSTALL_DIR/logs
sudo chown $USER:$USER $INSTALL_DIR/logs

cat <<EOF | sudo tee /etc/systemd/system/fireguard-edge.service
[Unit]
Description=FireGuard Edge Detection Pipeline
After=network.target

[Service]
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/main.py
Restart=always
RestartSec=10
User=$USER
Environment=PYTHONPATH=$INSTALL_DIR
# Ensure all logs (stdout/stderr) are sent to the system journal
StandardOutput=append:$INSTALL_DIR/logs/service.log
StandardError=append:$INSTALL_DIR/logs/service.log

[Install]
WantedBy=multi-user.target
EOF

# 7. Start the Service
echo "Enabling and starting service..."
sudo systemctl daemon-reload
sudo systemctl enable fireguard-edge
sudo systemctl start fireguard-edge

echo "----------------------------------------------------------------"
echo "✅ INSTALLATION COMPLETE!"
echo "----------------------------------------------------------------"
echo "FireGuard is now running as a silent background service."
echo ""
echo "HOW TO MONITOR YOUR SERVICE:"
echo "1. View Live Logs:   tail -f $INSTALL_DIR/logs/service.log"
echo "2. Check Status:      sudo systemctl status fireguard-edge"
echo "3. Restart Service:   sudo systemctl restart fireguard-edge"
echo "----------------------------------------------------------------"
