"""
config_sync.py — Sync cameras between SQLite DB and edge/config.yaml

When cameras are added, edited, or deleted from the UI, this module
rewrites the `cameras:` section of config.yaml so the Jetson Nano
edge pipeline picks up the changes on next restart.

Non-camera sections (model, server, alert, stream, etc.) are preserved.
"""

import os
import logging
import yaml
from pathlib import Path
from typing import Optional

from server.utils.paths import EDGE_CONFIG_PATH

logger = logging.getLogger(__name__)

# Default path to edge config.yaml
DEFAULT_CONFIG_PATH = EDGE_CONFIG_PATH


def _get_config_path() -> Path:
    """Return the absolute path to edge/config.yaml."""
    return DEFAULT_CONFIG_PATH


def load_edge_config(config_path: Optional[str] = None) -> dict:
    """Load the full edge config.yaml as a dict."""
    path = Path(config_path) if config_path else _get_config_path()
    if not path.exists():
        logger.warning("Edge config not found at %s", path)
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def save_edge_config(data: dict, config_path: Optional[str] = None):
    """Write the full config dict back to config.yaml, preserving structure."""
    path = Path(config_path) if config_path else _get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    logger.info("Edge config saved to %s", path)


def sync_cameras_to_config(db, config_path: Optional[str] = None):
    """
    Read all cameras from the database and write them into the
    cameras section of edge/config.yaml.

    This is the main function called after any camera add/edit/delete.
    """
    try:
        cameras = db.get_cameras()
        config = load_edge_config(config_path)

        # Build the cameras list for YAML
        yaml_cameras = []
        for cam in cameras:
            entry = {
                "id": cam["id"],
                "url": _format_url(cam.get("source", "0")),
                "name": cam.get("name", f"Camera {cam['id']}"),
            }
            yaml_cameras.append(entry)

        config["cameras"] = yaml_cameras
        save_edge_config(config, config_path)
        logger.info("Synced %d cameras to edge config.yaml", len(yaml_cameras))

    except Exception as e:
        logger.error("Failed to sync cameras to config.yaml: %s", e)
        raise


def add_camera_to_config(cam_id: int, name: str, source: str,
                         config_path: Optional[str] = None):
    """Add a single camera entry to config.yaml."""
    try:
        config = load_edge_config(config_path)
        cameras = config.get("cameras", [])

        # Check if camera already exists
        for cam in cameras:
            if cam.get("id") == cam_id:
                cam["name"] = name
                cam["url"] = _format_url(source)
                save_edge_config(config, config_path)
                return

        cameras.append({
            "id": cam_id,
            "url": _format_url(source),
            "name": name,
        })
        config["cameras"] = cameras
        save_edge_config(config, config_path)

    except Exception as e:
        logger.error("Failed to add camera %d to config: %s", cam_id, e)
        raise


def update_camera_in_config(cam_id: int, name: str = None, source: str = None,
                            config_path: Optional[str] = None):
    """Update a camera entry in config.yaml."""
    try:
        config = load_edge_config(config_path)
        cameras = config.get("cameras", [])

        for cam in cameras:
            if cam.get("id") == cam_id:
                if name is not None:
                    cam["name"] = name
                if source is not None:
                    cam["url"] = _format_url(source)
                break

        config["cameras"] = cameras
        save_edge_config(config, config_path)

    except Exception as e:
        logger.error("Failed to update camera %d in config: %s", cam_id, e)
        raise


def delete_camera_from_config(cam_id: int, config_path: Optional[str] = None):
    """Remove a camera entry from config.yaml."""
    try:
        config = load_edge_config(config_path)
        cameras = config.get("cameras", [])
        config["cameras"] = [c for c in cameras if c.get("id") != cam_id]
        save_edge_config(config, config_path)

    except Exception as e:
        logger.error("Failed to delete camera %d from config: %s", cam_id, e)
        raise


def _format_url(source: str):
    """Convert source string to appropriate YAML value.
    Integer sources (webcam IDs) are stored as ints, others as strings.
    """
    s = source.strip()
    if s.isdigit():
        return int(s)
    return s

def sync_general_settings_to_config(db, config_path: Optional[str] = None):
    """Sync dynamic non-camera settings from db to config.yaml."""
    try:
        config = load_edge_config(config_path)
        
        if "alert" not in config: config["alert"] = {}
        config["alert"]["min_consecutive"] = int(db.get_config("min_consecutive", "3"))
        config["alert"]["cooldown_sec"] = int(db.get_config("cooldown_sec", "30"))
        
        if "transmission" not in config: config["transmission"] = {}
        config["transmission"]["interval_ms"] = int(db.get_config("interval_ms", "100"))
        config["transmission"]["jpeg_quality"] = int(db.get_config("jpeg_quality", "60"))
        
        save_edge_config(config, config_path)
        logger.info("Synced general settings to edge config.yaml")
    except Exception as e:
        logger.error("Failed to sync general settings to config.yaml: %s", e)
        raise
