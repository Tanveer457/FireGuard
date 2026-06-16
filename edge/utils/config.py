# utils/config.py
import yaml
import os
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("config")


def load(path: Optional[str] = None) -> dict:
    if path is None:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")

    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    _sanitize(cfg)
    _validate(cfg)
    _create_dirs(cfg)
    return cfg


def _sanitize(cfg: dict):
    """Strip accidental spaces from URLs and tokens."""
    server = cfg.get("server", {})
    if "url" in server:
        original = server["url"]
        server["url"] = str(original).strip()
        if server["url"] != str(original):
            log.warning(f"Stripped whitespace from server.url: '{original}' → '{server['url']}'")
    if "token" in server:
        server["token"] = str(server["token"]).strip()

    # Sanitize camera URLs
    for cam in cfg.get("cameras", []):
        if "url" in cam and isinstance(cam["url"], str):
            cam["url"] = cam["url"].strip()


def _validate(cfg: dict):
    errors = []

    cams = cfg.get("cameras", [])
    if not cams:
        log.warning("No cameras defined — pipeline will start but wait for cameras to be added from UI")

    model_path = cfg.get("model", {}).get("path", "")
    if not Path(model_path).exists():
        errors.append(
            f"Model file not found: '{model_path}'\n"
            f"     Copy best.pt into the edge/ folder"
        )

    if not cfg.get("server", {}).get("url"):
        errors.append("server.url is empty")
    if not cfg.get("server", {}).get("token"):
        errors.append("server.token is empty")

    if errors:
        msg = "\n".join(f"  ✗  {e}" for e in errors)
        raise SystemExit(f"\nConfig errors:\n{msg}\n")

    log.info(f"Config valid — {len(cams)} camera(s)  server={cfg['server']['url']}")


def _create_dirs(cfg: dict):
    for key in ("snapshots_dir", "clips_dir", "logs_dir"):
        d = cfg.get("storage", {}).get(key)
        if d:
            Path(d).mkdir(parents=True, exist_ok=True)