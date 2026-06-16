# inference/detector.py
# Production YOLO inference.
# Features:
#   - Batch inference (multiple cameras in one GPU call)
#   - Inference time tracking
#   - Works with best.pt (CPU) and best.engine (TensorRT GPU)

import time
import logging
import torch
from dataclasses import dataclass
from ultralytics import YOLO

log = logging.getLogger("detector")


@dataclass
class Detection:
    label:      str
    confidence: float
    x1: int
    y1: int
    x2: int
    y2: int

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "conf":  self.confidence,
            "x1":    self.x1,
            "y1":    self.y1,
            "x2":    self.x2,
            "y2":    self.y2,
        }

    @property
    def threat_level(self) -> str:
        label_lower = self.label.lower()
        conf = self.confidence

        # 1. Smoke Logic
        if label_lower == "smoke":
            if conf >= 0.85: return "CRITICAL"
            if conf >= 0.70: return "HIGH"
            if conf >= 0.60: return "MEDIUM"
            return "LOW"

        # 2. Fire Logic
        if label_lower == "fire":
            if conf >= 0.80: return "CRITICAL"
            if conf >= 0.60: return "HIGH"
            if conf >= 0.40: return "MEDIUM"
            return "LOW"
        
        # 3. Default for 'other' or unknown labels
        return "LOW"


class Detector:
    def __init__(self, model_cfg: dict):
        self._path   = model_cfg["path"]
        self._device = str(model_cfg["device"])
        self._conf   = model_cfg["conf"]
        self._iou    = model_cfg["iou"]
        self._imgsz  = model_cfg["imgsz"]

        # Automatic GPU Fallback: If device is 0 or other GPU index, check if CUDA is available
        if "cpu" not in self._device.lower():
            if not torch.cuda.is_available():
                log.warning(f"⚠️ CUDA requested (device={self._device}) but not available on this hardware.")
                log.warning("🔄 Falling back to CPU for inference.")
                self._device = "cpu"

        # Stats
        self.total_inferences = 0
        self.total_detections = 0
        self._inference_times = []

        log.info(f"Loading model: {self._path}  device={self._device}")
        self._model = YOLO(self._path)
        log.info("Model ready ✓")

    def update_config(self, model_cfg: dict):
        """Update detection thresholds on the fly."""
        self._conf  = model_cfg.get("conf", self._conf)
        self._iou   = model_cfg.get("iou", self._iou)
        self._imgsz = model_cfg.get("imgsz", self._imgsz)
        
        # Re-check device if it was changed (though imgsz/device usually require reload)
        new_device = str(model_cfg.get("device", self._device))
        if new_device != self._device:
            self._device = new_device
            if "cpu" not in self._device.lower() and not torch.cuda.is_available():
                self._device = "cpu"
            log.info("Detector device updated to: %s", self._device)

        log.info(f"Detector thresholds updated: conf={self._conf}, iou={self._iou}")

    def detect_batch(self, frames: list) -> list[list[Detection]]:
        """
        Run YOLO on a batch of frames in one call.
        frames: list of numpy BGR arrays
        Returns: list of Detection lists, one per frame
        """
        if not frames:
            return []

        t0      = time.perf_counter()
        results = self._model.predict(
            source  = frames,
            device  = self._device,
            conf    = self._conf,
            iou     = self._iou,
            imgsz   = self._imgsz,
            verbose = False,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self._inference_times.append(elapsed_ms)
        if len(self._inference_times) > 100:
            self._inference_times.pop(0)

        self.total_inferences += 1

        all_detections = []
        for r in results:
            frame_dets = []
            if r.boxes is not None:
                for box in r.boxes:
                    label = r.names[int(box.cls)].lower()
                    
                    # Track all detections in stats, including 'other'
                    self.total_detections += 1

                    # Filter: Only return fire and smoke for processing/transmission
                    if label not in ["fire", "smoke"]:
                        if label == "other":
                            log.debug("Model detected 'other' class (ignoring for app)")
                        continue

                    x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                    det = Detection(
                        label      = label,
                        confidence = round(float(box.conf), 3),
                        x1=x1, y1=y1, x2=x2, y2=y2,
                    )
                    frame_dets.append(det)

            all_detections.append(frame_dets)

        return all_detections

    @property
    def avg_inference_ms(self) -> float:
        if not self._inference_times:
            return 0.0
        return round(sum(self._inference_times) / len(self._inference_times), 1)

    def stats(self) -> dict:
        return {
            "total_inferences": self.total_inferences,
            "total_detections": self.total_detections,
            "avg_inference_ms": self.avg_inference_ms,
        }
