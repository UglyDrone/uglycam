import abc
import logging
import numpy as np
import cv2
from config import Config

logger = logging.getLogger("Detector")

class DetectorBackend(abc.ABC):
    """Abstract base class representing an extensible object detection/recognition backend."""

    @abc.abstractmethod
    def load(self, model_path: str):
        """
        Loads and initializes the model.
        Called once at application startup.
        """
        pass

    @abc.abstractmethod
    def detect(self, frame: np.ndarray, conf_threshold: float) -> dict:
        """
        Runs inference on a BGR image frame.
        """
        pass

class RKNNLPRNetDetector(DetectorBackend):
    """
    Rockchip NPU LPRNet License Plate Recognition backend.
    """

    def __init__(self):
        self.rknn = None
        self.chars = Config.CHARS

    def load(self, model_path: str):
        """Loads and initializes the RKNN runtime on the Rockchip hardware NPU."""
        logger.info(f"Loading native LPRNet RKNN model from {model_path}...")
        try:
            from rknnlite.api import RKNNLite
            self.rknn = RKNNLite()
            
            # Load the compiled RKNN model binary
            ret = self.rknn.load_rknn(model_path)
            if ret != 0:
                raise RuntimeError(f"Failed to load RKNN model file. Error code: {ret}")

            # Allocate runtime memory and lock co-processor driver contexts
            core_mask = getattr(Config, "RKNN_CORE_MASK", 0)
            ret = self.rknn.init_runtime(core_mask=core_mask)
            if ret != 0:
                raise RuntimeError(f"Failed to initialize RKNN co-processor context. Error code: {ret}")

            logger.info("RKNN co-processor successfully initialized on Rockchip hardware NPU.")
        except Exception as e:
            logger.error(f"Failed to load RKNN model: {e}")
            raise

    def detect(self, frame: np.ndarray, conf_threshold: float) -> dict:
        """
        Runs NPU inference and decodes license plate characters.
        """
        if self.rknn is None:
            raise RuntimeError("RKNN runtime is not initialized. Call load() first.")

        # LPRNet input dimensions: 94x24 (widthxheight)
        resized = cv2.resize(frame, (94, 24))

        # Model expects NCHW BGR input format
        nchw = np.transpose(resized, (2, 0, 1))
        nchw = np.expand_dims(nchw, 0).astype(np.uint8)

        # Run hardware NPU inference
        outputs = self.rknn.inference(inputs=[nchw])

        if not outputs or len(outputs) == 0:
            return {"detections": [], "analytics": {"total_count": 0, "counts": {}}}

        # Output shape is typically [1, num_classes, sequence_length], e.g., [1, 68, 18]
        output = np.array(outputs[0])
        if len(output.shape) == 3:
            output = output[0]  # shape: (num_classes, sequence_length)

        # Get character index of max probability for each position in the sequence
        preds = np.argmax(output, axis=0)  # shape: (sequence_length,)

        # Determine blank index (typically len(chars) or a custom specified index)
        blank_index = getattr(Config, "CTC_BLANK_INDEX", -1)
        if blank_index == -1:
            blank_index = len(self.chars)

        # CTC Greedy Decode: collapse consecutive duplicates and remove blank index
        decoded_indices = []
        prev = -1
        for val in preds:
            if val != blank_index and val != prev:
                decoded_indices.append(val)
            prev = val

        # Map indices to characters
        plate_text = ""
        for idx in decoded_indices:
            if idx < len(self.chars):
                plate_text += self.chars[idx]

        detections = []
        analytics = {"total_count": 0, "counts": {}}

        if plate_text:
            # Report recognized plate text as the predicted class.
            # Draws a full-frame box overlay.
            detections.append({
                "class": plate_text,
                "confidence": 0.99,
                "bbox": [0.05, 0.05, 0.95, 0.95]
            })
            analytics["total_count"] = 1
            analytics["counts"] = {plate_text: 1}

        return {
            "detections": detections,
            "analytics": analytics
        }
