"""
jetson_manager.py — FireGuard Jetson SSH Manager (Industry Edition)
Handles:
  - SSH connection test
  - Push YAML config via SFTP
  - Restart pipeline via systemctl / direct command
  - Fetch remote CPU/GPU/temp status
  - Close connection cleanly
"""

import logging
import yaml
import socket
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False
    logger.warning("paramiko not installed. Install: pip install paramiko")


class JetsonManager:
    """
    SSH-based manager for a remote Jetson Nano edge device.
    Supports dynamic IP resolution via hostnames.
    """

    REMOTE_CONFIG_PATH  = "/home/jetson/fire_detection/config.yaml"
    REMOTE_SERVICE_NAME = "fire-detection"

    def __init__(self, host: str, username: str, password: str, port: int = 22,
                 remote_base_path: str = "/home/jetson/fire_detection"):
        self.host     = host
        self.username = username
        self.password = password
        self.port     = port
        self.remote_base_path = remote_base_path.rstrip("/")
        self.REMOTE_CONFIG_PATH = f"{self.remote_base_path}/config.yaml"
        self._ssh: Optional["paramiko.SSHClient"] = None

    def _resolve_host(self) -> str:
        """Resolves hostname to IP if needed."""
        try:
            # If it's an IP, return it
            socket.inet_aton(self.host)
            return self.host
        except socket.error:
            try:
                ip = socket.gethostbyname(self.host)
                logger.info(f"Resolved {self.host} to {ip}")
                return ip
            except Exception as e:
                logger.warning(f"Could not resolve {self.host}: {e}")
                return self.host

    # ── Connection ────────────────────────────────────────────────────────────
    def _connect(self, timeout: int = 8) -> "paramiko.SSHClient":
        if not HAS_PARAMIKO:
            raise RuntimeError("paramiko not installed")
        
        ip = self._resolve_host()
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            ip,
            port=self.port,
            username=self.username,
            password=self.password,
            timeout=timeout,
            banner_timeout=10,
        )
        return ssh

    def close(self):
        if self._ssh:
            try:
                self._ssh.close()
            except Exception:
                pass
            self._ssh = None

    # ── Test ─────────────────────────────────────────────────────────────────
    def test_connection(self) -> Tuple[bool, str]:
        try:
            ssh = self._connect(timeout=5)
            _, stdout, _ = ssh.exec_command("uname -a")
            uname = stdout.read().decode().strip()
            ssh.close()
            return True, f"Connected — {uname}"
        except Exception as e:
            logger.error("SSH test failed: %s", e)
            return False, str(e)

    # ── Deploy Config ─────────────────────────────────────────────────────────
    def update_config(self, config_dict: dict,
                      remote_path: str = None) -> bool:
        """
        Push config_dict as YAML to the Jetson and restart the pipeline.
        Returns True on success.
        """
        remote_path = remote_path or self.REMOTE_CONFIG_PATH
        remote_dir = "/".join(remote_path.split("/")[:-1])
        
        yaml_content = yaml.dump(config_dict, default_flow_style=False,
                                 allow_unicode=True)
        try:
            ssh = self._connect()

            # Ensure directory exists
            ssh.exec_command(f"mkdir -p {remote_dir}")
            logger.info("Ensured remote directory exists: %s", remote_dir)

            # Write via SFTP
            sftp = ssh.open_sftp()
            try:
                with sftp.open(remote_path, "w") as f:
                    f.write(yaml_content)
                logger.info("Config pushed to %s:%s", self.host, remote_path)
            except Exception as sftp_err:
                logger.error("SFTP write failed to %s: %s", remote_path, sftp_err)
                sftp.close()
                ssh.close()
                return False
            sftp.close()

            # Restart pipeline — try systemctl first, fall back to pkill + nohup
            # We use a more robust restart logic
            restart_cmd = (
                f"sudo systemctl restart {self.REMOTE_SERVICE_NAME} 2>/dev/null || "
                f"(pkill -f main.py 2>/dev/null; sleep 1; "
                f"nohup python3 {remote_dir}/main.py "
                f"--config {remote_path} > /tmp/fire.log 2>&1 &)"
            )
            logger.info("Executing restart command: %s", restart_cmd)
            _, stdout, stderr = ssh.exec_command(restart_cmd)
            
            # Note: channel.recv_exit_status() can block if the command doesn't return
            # For nohup backgrounds, we might just check if it was sent
            exit_status = stdout.channel.recv_exit_status()
            
            if exit_status != 0:
                err = stderr.read().decode().strip()
                logger.warning("Restart command returned %d: %s", exit_status, err)
                # We don't necessarily return False here because the nohup might have worked 
                # even if the systemctl failed.

            ssh.close()
            logger.info("Pipeline restart triggered on %s", self.host)
            return True

        except Exception as e:
            logger.error("Config deploy failed: %s", e)
            return False

    # ── Remote Status ─────────────────────────────────────────────────────────
    def get_status(self) -> Optional[dict]:
        """
        Retrieve CPU/memory/temperature from the Jetson.
        Returns a dict or None on failure.
        """
        try:
            ssh = self._connect()
            results = {}

            # CPU usage
            _, stdout, _ = ssh.exec_command("top -bn1 | grep 'Cpu(s)' | awk '{print $2}'")
            cpu = stdout.read().decode().strip()
            results["cpu_percent"] = cpu or "N/A"

            # Memory
            _, stdout, _ = ssh.exec_command("free -m | grep Mem | awk '{printf \"%s/%s MB\", $3, $2}'")
            mem = stdout.read().decode().strip()
            results["memory"] = mem or "N/A"

            # Temperature (Jetson-specific)
            _, stdout, _ = ssh.exec_command(
                "cat /sys/devices/virtual/thermal/thermal_zone0/temp 2>/dev/null"
            )
            temp_raw = stdout.read().decode().strip()
            if temp_raw.isdigit():
                results["temperature_c"] = f"{int(temp_raw) / 1000:.1f} °C"
            else:
                results["temperature_c"] = "N/A"

            # Disk usage
            _, stdout, _ = ssh.exec_command("df -h / | tail -1 | awk '{print $3\"/\"$2\" (\"$5\")\"}'")
            disk = stdout.read().decode().strip()
            results["disk"] = disk or "N/A"

            # Check if pipeline is running
            _, stdout, _ = ssh.exec_command("pgrep -f main.py > /dev/null && echo running || echo stopped")
            results["pipeline"] = stdout.read().decode().strip()

            # Uptime
            _, stdout, _ = ssh.exec_command("uptime -p 2>/dev/null || uptime")
            results["uptime"] = stdout.read().decode().strip()

            ssh.close()
            return results

        except Exception as e:
            logger.error("get_status failed: %s", e)
            return None
