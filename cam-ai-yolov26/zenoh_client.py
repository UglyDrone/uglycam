import json
import logging
import zenoh

logger = logging.getLogger("ZenohClient")

class ZenohClientWrapper:
    """Wrapper for Eclipse Zenoh Client publishing detections."""
    def __init__(self, camera_id: str):
        self.camera_id = camera_id
        self.session = None
        
        # Append slot suffix for parallel containers
        import os
        core_mask = os.getenv("RKNN_CORE_MASK", "0")
        npu_slot = "npu1" if core_mask == "1" else ("npu2" if core_mask == "2" else "")
        suffix = f"/{npu_slot}" if npu_slot else ""
        self.key_expr = f"demo/camcam/{self.camera_id}/detections{suffix}"

    def connect(self):
        logger.info("Opening Zenoh session...")
        try:
            self.session = zenoh.open(zenoh.Config())
            logger.info("Zenoh session opened successfully.")
        except Exception as e:
            logger.error(f"Failed to open Zenoh session: {e}")

    def stop(self):
        if self.session:
            logger.info("Closing Zenoh session...")
            try:
                self.session.close()
            except Exception as e:
                logger.error(f"Error closing Zenoh session: {e}")
            self.session = None
            logger.info("Zenoh session closed.")

    def publish_detections(self, timestamp: float, detections: list):
        if not self.session or not detections:
            return

        payload = {
            "camera": self.camera_id,
            "timestamp": timestamp,
            "detections": detections
        }

        try:
            payload_str = json.dumps(payload)
            self.session.put(self.key_expr, payload_str)
            logger.debug(f"Published to Zenoh: {self.key_expr}")
        except Exception as e:
            logger.error(f"Failed to publish to Zenoh: {e}")
