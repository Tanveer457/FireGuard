import time
import logging
import paramiko
import socket
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("jetson_ssh")

class JetsonSSHWorker(QThread):
    """
    Handles remote startup and monitoring of the Jetson Nano edge pipeline via SSH.
    Supports automatic discovery via .local hostnames and handles dynamic IPs.
    """
    # status_changed(status_key, message)
    # keys: "connecting", "connected", "already_running", "failed", "discovery"
    status_changed = Signal(str, str)

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.remote_pid = None
        self._ssh = None
        self._should_stop = False
        self.current_ip = None

    def _resolve_host(self, host: str) -> str:
        """Resolves .local hostnames to IPs or returns the host as-is."""
        if not host:
            return ""
        
        # If it looks like an IP, return it
        try:
            socket.inet_aton(host)
            return host
        except socket.error:
            pass

        # Try to resolve (handles mDNS .local)
        try:
            self.status_changed.emit("discovery", f"Resolving {host}...")
            ip = socket.gethostbyname(host)
            logger.info(f"Resolved {host} to {ip}")
            return ip
        except Exception as e:
            logger.warning(f"Could not resolve {host}: {e}")
            return host

    def run(self):
        host = self.config.get("jetson_host", "").strip()
        if not host:
            # Try default hostname if none configured
            host = "jetson.local"
            logger.info(f"No host configured, trying default: {host}")

        self.current_ip = self._resolve_host(host)
        
        # Determine if we should be noisy about failures (noisy if NOT localhost)
        is_local = self.current_ip in ("127.0.0.1", "localhost")
        
        self.status_changed.emit("connecting", f"Connecting to {host} ({self.current_ip})...")
        
        try:
            self._ssh = paramiko.SSHClient()
            self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Connection kwargs
            connect_kwargs = {
                "hostname": self.current_ip,
                "port":     int(self.config.get("jetson_port", 22)),
                "username": self.config.get("jetson_user", "fireguard"),
                "timeout":  5 if is_local else 10 # Shorter timeout for local
            }
            
            passw = self.config.get("jetson_pass", "")
            pkey_path = self.config.get("jetson_key", "")
            
            if pkey_path:
                connect_kwargs["key_filename"] = pkey_path
            elif passw:
                connect_kwargs["password"] = passw

            try:
                self._ssh.connect(**connect_kwargs)
            except Exception as conn_err:
                if is_local:
                    msg = "SSH server not found on localhost (normal for local tests)"
                    logger.info(msg)
                    self.status_changed.emit("failed", msg)
                    return # EXIT EARLY HERE
                else:
                    raise conn_err

            if self._ssh and self._ssh.get_transport():
                logger.info(f"SSH connected to {host} ({self.current_ip})")
                # 1. Check if already running
                check_cmd = "pgrep -f 'main.py'"
            stdin, stdout, stderr = self._ssh.exec_command(check_cmd)
            pid_out = stdout.read().decode().strip()
            
            if pid_out:
                self.remote_pid = pid_out.split('\n')[0]
                logger.info(f"Edge pipeline already running on PID {self.remote_pid}")
                self.status_changed.emit("already_running", f"Connected to {host} — edge running (PID {self.remote_pid})")
            else:
                # 2. Start pipeline
                edge_path = self.config.get("jetson_path", "/home/fireguard/project/edge")
                py_bin    = self.config.get("jetson_python", "python3")
                
                # Use a specific marker in the process name to make pkill safer
                start_cmd = (
                    f"mkdir -p {edge_path}/logs && "
                    f"cd {edge_path} && "
                    f"nohup {py_bin} main.py --remote-start > logs/edge.log 2>&1 & echo $!"
                )
                
                logger.info(f"Starting remote edge pipeline: {start_cmd}")
                stdin, stdout, stderr = self._ssh.exec_command(start_cmd)
                new_pid = stdout.read().decode().strip()
                
                if new_pid and new_pid.isdigit():
                    self.remote_pid = new_pid
                    logger.info(f"Edge pipeline started on PID {self.remote_pid}")
                    self.status_changed.emit("connected", f"Connected to {host} — pipeline started (PID {self.remote_pid})")
                else:
                    err = stderr.read().decode().strip()
                    self.status_changed.emit("failed", f"Failed to start on {host}: {err}")
                    return

            # 3. Keepalive Loop
            self._keepalive_loop()

        except Exception as e:
            logger.error(f"SSH worker error for {host}: {e}")
            self.status_changed.emit("failed", f"Connection failed to {host}: {str(e)}")
        finally:
            if self._ssh:
                self._ssh.close()
                self._ssh = None

    def _keepalive_loop(self):
        """Monitor connection and remote process."""
        while not self._should_stop:
            try:
                transport = self._ssh.get_transport()
                if transport is None or not transport.is_active():
                    raise Exception("SSH transport inactive")
                
                transport.send_ignore()
                
                if self.remote_pid:
                    check_cmd = f"ps -p {self.remote_pid} > /dev/null"
                    stdin, stdout, stderr = self._ssh.exec_command(check_cmd)
                    if stdout.channel.recv_exit_status() != 0:
                        logger.warning(f"Remote process {self.remote_pid} died")
                        self.status_changed.emit("failed", "Remote process terminated")
                        break
                
                time.sleep(30)
            except Exception as e:
                logger.warning(f"SSH connection lost: {e}")
                if not self._should_stop:
                    self.status_changed.emit("failed", "Connection lost")
                break

    def stop_remote_pipeline(self):
        """Stops the remote main.py process cleanly."""
        self._should_stop = True
        if self._ssh:
            try:
                if self.remote_pid:
                    self._ssh.exec_command(f"kill {self.remote_pid}")
                    logger.info(f"Sent SIGTERM to remote PID {self.remote_pid}")
                
                # Double check with pkill just in case
                self._ssh.exec_command("pkill -f 'main.py --remote-start'")
                logger.info("Executed cleanup pkill on remote host")
            except Exception as e:
                logger.warning(f"Failed to stop remote process: {e}")
