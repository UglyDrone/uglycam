import abc
import logging
import numpy as np
import cv2

logger = logging.getLogger("Detector")

COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light",
    "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush"
]

class DetectorBackend(abc.ABC):
    """Abstract base class representing an extensible object detection backend."""

    @abc.abstractmethod
    def load(self, model_path: str):
        """
        Loads and initializes the object detection model.
        Called once at application startup.
        """
        pass

    @abc.abstractmethod
    def detect(self, frame: np.ndarray, conf_threshold: float) -> list[dict]:
        """
        Runs object detection inference on a BGR image frame.
        
        Returns:
            A list of dictionary objects, each representing a detection:
            {
                "class": str,          # Name of the predicted class (e.g., 'person')
                "confidence": float,   # Confidence score (0.0 to 1.0)
                "bbox": [x1, y1, x2, y2] # Bounding box coordinates normalized to [0.0, 1.0]
            }
        """
        pass


# -------------------------------------------------------------
# Battle-tested YOLOv8 Post-Processing Utilities from yolo_lite.py
# -------------------------------------------------------------

def softmax(x, axis=1):
    x = x - np.max(x, axis=axis, keepdims=True)
    e = np.exp(x)
    return e / np.sum(e, axis=axis, keepdims=True)

def dfl_numpy(pos):
    """
    pos: (N, C, H, W) where C = 4 * reg_max
    returns: (N, 4, H, W) distances
    """
    n, c, h, w = pos.shape
    if c == 4:
        return pos  # Already raw distances
        
    p_num = 4
    reg_max = c // p_num
    x = pos.reshape(n, p_num, reg_max, h, w)
    x = softmax(x, axis=2)
    acc = np.arange(reg_max, dtype=np.float32).reshape(1, 1, reg_max, 1, 1)
    y = (x * acc).sum(axis=2)
    return y  # (N,4,H,W)

def box_process(pos, input_w, input_h, box_format='dist', box_scale=1.0):
    """
    pos: (N,C,H,W) raw regression output
    returns xyxy in input space (pixel coords)
    """
    grid_h, grid_w = pos.shape[2], pos.shape[3]

    col, row = np.meshgrid(np.arange(grid_w), np.arange(grid_h))
    col = col.reshape(1, 1, grid_h, grid_w).astype(np.float32)
    row = row.reshape(1, 1, grid_h, grid_w).astype(np.float32)
    grid = np.concatenate((col, row), axis=1)  # (1,2,H,W)

    stride = np.array([input_w // grid_w, input_h // grid_h], dtype=np.float32).reshape(1, 2, 1, 1)

    dist = dfl_numpy(pos)  # (1,4,H,W)
    dist *= box_scale      # User/Heuristic boost

    if box_format == 'xywh':
        cxcy = grid + 0.5 + dist[:, 0:2, :, :]
        wh = dist[:, 2:4, :, :]
        xy1 = cxcy - wh / 2
        xy2 = cxcy + wh / 2
    else:
        # Standard l,t,r,b distances
        xy1 = (grid + 0.5) - dist[:, 0:2, :, :]
        xy2 = (grid + 0.5) + dist[:, 2:4, :, :]

    xy1 = np.clip(xy1, -2.0, grid_w + 2.0)
    xy2 = np.clip(xy2, -2.0, grid_w + 2.0)

    xyxy = np.concatenate((xy1 * stride, xy2 * stride), axis=1)  # (1,4,H,W) in pixels
    return xyxy

def sp_flatten(x):
    # (N,C,H,W) -> (H*W*N, C)
    x = x.transpose(0, 2, 3, 1)
    return x.reshape(-1, x.shape[-1])

def filter_boxes(boxes_xyxy, obj_scores, class_probs, obj_thresh):
    """
    boxes_xyxy: (M,4)
    obj_scores: (M,1) or (M,)
    class_probs: (M,80)
    """
    # Conditional sigmoid: only apply if values appear to be raw logits
    if np.max(obj_scores) > 1.0 or np.min(obj_scores) < 0:
        obj_scores = 1.0 / (1.0 + np.exp(-obj_scores.reshape(-1)))
    else:
        obj_scores = obj_scores.reshape(-1)
    
    # Sigmoid for class probabilities (if logits)
    if np.max(class_probs) > 1.0 or np.min(class_probs) < 0:
        class_probs = 1.0 / (1.0 + np.exp(-class_probs))
        
    class_max = np.max(class_probs, axis=-1)
    classes = np.argmax(class_probs, axis=-1)
    scores = class_max * obj_scores
    keep = np.where(scores >= obj_thresh)[0]
    return boxes_xyxy[keep], classes[keep], scores[keep]

def nms_boxes(boxes, scores, nms_thresh):
    """
    boxes: (N,4) xyxy
    """
    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]

    w = np.maximum(0.0, x2 - x1)
    h = np.maximum(0.0, y2 - y1)
    areas = w * h
    order = scores.argsort()[::-1]
    
    # Weighted NMS: Average boxes that match (fixes low-quantization fragmentation)
    keep = []
    processed = np.zeros(order.size, dtype=bool)
    
    for i in range(order.size):
        if processed[i]:
            continue
        
        idx = order[i]
        processed[i] = True
        
        # Find others that overlap significantly
        xx1 = np.maximum(x1[idx], x1[order[i+1:]])
        yy1 = np.maximum(y1[idx], y1[order[i+1:]])
        xx2 = np.minimum(x2[idx], x2[order[i+1:]])
        yy2 = np.minimum(y2[idx], y2[order[i+1:]])
        ww = np.maximum(0.0, xx2 - xx1)
        hh = np.maximum(0.0, yy2 - yy1)
        inter = ww * hh
        ovr = inter / (areas[idx] + areas[order[i+1:]] - inter + 1e-9)
        
        match_idx = np.where(ovr > nms_thresh)[0]
        if match_idx.size > 0:
            # Weighted average boxes (basic WBF)
            weights = scores[order[i+1:]][match_idx]
            weights = weights / (np.sum(weights) + scores[idx])
            main_weight = scores[idx] / (np.sum(scores[order[i+1:]][match_idx]) + scores[idx])
            
            final_box = boxes[idx] * main_weight
            for m_i, weight in zip(match_idx, weights):
                final_box += boxes[order[i+1:][m_i]] * weight
                processed[i + 1 + m_i] = True
                
            boxes[idx] = final_box
            
        keep.append(idx)
        
    return np.array(keep, dtype=np.int32)

def post_process_yolov8(outputs, input_size=640, obj_thresh=0.25, nms_thresh=0.45, box_format='dist', box_scale=1.0):
    """
    This expects typical YOLOv8 RKNN export layout:
      3 branches, each branch has [reg, cls] or [reg, cls, obj]
    """
    if outputs is None or len(outputs) < 3:
        return None

    # Try to normalize outputs to numpy arrays
    outs = [np.array(o) for o in outputs]
    
    regs = []
    clss = []
    objs = []
    for o in outs:
        if o.ndim != 4:
            continue
        
        n, c, h, w = o.shape
        # Identify NHWC and transpose to NCHW
        if c not in [80, 64, 4, 1] and w in [80, 64, 4, 1]:
            o = o.transpose(0, 3, 1, 2)
            c = o.shape[1]
        
        if c == 80:
            clss.append(o)
        elif c in [64, 4]:
            regs.append(o)
        elif c == 1:
            objs.append(o)

    # Sort to match branches (largest resolution H first: 80x80 -> 40x40 -> 20x20)
    clss.sort(key=lambda x: x.shape[2], reverse=True)
    regs.sort(key=lambda x: x.shape[2], reverse=True)
    objs.sort(key=lambda x: x.shape[2], reverse=True)

    if len(regs) != 3 or len(clss) != 3:
        # fallback based on user's specific [reg, cls, obj] order (e.g. 9 outputs)
        regs, clss, objs = [], [], []
        if len(outs) == 9:
            # Scale 0 (80x80)
            regs.append(outs[0]); clss.append(outs[1]); objs.append(outs[2])
            # Scale 1 (40x40)
            regs.append(outs[3]); clss.append(outs[4]); objs.append(outs[5])
            # Scale 2 (20x20)
            regs.append(outs[6]); clss.append(outs[7]); objs.append(outs[8])
        else:
            # generic fallback
            num_branches = len(outs)
            for i in range(0, num_branches, 3 if num_branches % 3 == 0 else 2):
                regs.append(outs[i])
                clss.append(outs[i+1])
                if num_branches % 3 == 0:
                    objs.append(outs[i+2])
    
    # Ensure objs has same length as clss
    if not objs:
        objs = [None] * len(clss)
    elif len(objs) < len(clss):
        objs.extend([None] * (len(clss) - len(objs)))

    boxes_all = []
    cls_all = []
    obj_all = []

    # YOLOv8 uses implicit objectness (treated as 1.0 if not provided explicitly by NPU outputs)
    for reg, cls, obj in zip(regs, clss, objs):
        xyxy = box_process(reg, input_size, input_size, box_format=box_format, box_scale=box_scale)  # (1,4,H,W)
        boxes = sp_flatten(xyxy)                          # (M,4)
        class_probs = sp_flatten(cls)                     # (M,80)
        
        if obj is not None:
            obj_scores = sp_flatten(obj)                  # (M,1)
        else:
            obj_scores = np.ones((class_probs.shape[0], 1), dtype=np.float32)

        boxes, classes, scores = filter_boxes(boxes, obj_scores, class_probs, obj_thresh)
        if boxes.shape[0] == 0:
            continue
        boxes_all.append(boxes)
        cls_all.append(classes)
        obj_all.append(scores)

    if not boxes_all:
        return None

    boxes = np.concatenate(boxes_all, axis=0)
    classes = np.concatenate(cls_all, axis=0)
    scores = np.concatenate(obj_all, axis=0)

    # NMS per-class to avoid suppressions across label types
    final_boxes = []
    final_classes = []
    final_scores = []
    for c in np.unique(classes):
        inds = np.where(classes == c)[0]
        keep = nms_boxes(boxes[inds], scores[inds], nms_thresh)
        final_boxes.append(boxes[inds][keep])
        final_classes.append(classes[inds][keep])
        final_scores.append(scores[inds][keep])

    boxes = np.concatenate(final_boxes, axis=0)
    classes = np.concatenate(final_classes, axis=0)
    scores = np.concatenate(final_scores, axis=0)

    return boxes, classes, scores


# -------------------------------------------------------------
# Letterboxing Helper for Arbitrary Resolution Input
# -------------------------------------------------------------

def letterbox(img, new_shape=(640, 640), color=(0, 0, 0)):
    """
    Resizes and pads image to new_shape while preserving aspect ratio.
    Returns:
        padded_img: the padded and resized image
        ratio: scale ratio used
        (dw, dh): padding added on left/right and top/bottom
    """
    shape = img.shape[:2]  # current shape [height, width]
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)

    # Scale ratio (new / old)
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])

    # Compute padding
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]  # wh padding

    # Divide padding into top/bottom, left/right
    dw /= 2.0
    dh /= 2.0

    if shape[::-1] != new_unpad:  # resize
        img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
    
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
    return img, r, (dw, dh)


# -------------------------------------------------------------
# Native RKNN Detector Backend Wrapper
# -------------------------------------------------------------

class RKNNYOLOv8Detector(DetectorBackend):
    """
    Native Rockchip NPU Object Detection backend.
    Uses rknnlite to execute model inference and imports the battle-tested,
    highly accurate post-processing formulas from yolo_lite.py.
    """

    def __init__(self, iou_threshold: float = 0.45, box_format: str = "dist", box_scale: float = 4.0):
        self.rknn = None
        self.iou_threshold = iou_threshold
        self.box_format = box_format
        self.box_scale = box_scale

    def load(self, model_path: str):
        """Loads and initializes the RKNN runtime on the Rockchip hardware NPU."""
        logger.info(f"Loading native RKNN model from {model_path}...")
        try:
            from rknnlite.api import RKNNLite
            self.rknn = RKNNLite()
            
            # Load the compiled RKNN model binary
            ret = self.rknn.load_rknn(model_path)
            if ret != 0:
                raise RuntimeError(f"Failed to load RKNN model file. Error code: {ret}")

            # Allocate runtime memory and lock co-processor driver contexts
            from config import Config
            core_mask = getattr(Config, "RKNN_CORE_MASK", 0)
            ret = self.rknn.init_runtime(core_mask=core_mask)
            if ret != 0:
                raise RuntimeError(f"Failed to initialize RKNN co-processor context. Error code: {ret}")

            logger.info("RKNN co-processor successfully initialized on Rockchip hardware NPU.")

            # One-off test on startup if test_image.jpg exists
            import os
            test_img_path = "/config/test_image.jpg"
            if os.path.exists(test_img_path):
                logger.info(f"DIAGNOSTIC - Running one-off test on {test_img_path}...")
                test_img = cv2.imread(test_img_path)
                if test_img is not None:
                    # Run detect on the test image
                    res = self.detect(test_img, conf_threshold=0.25)
                    # Draw detections
                    if res:
                        logger.info(f"DIAGNOSTIC - YOLOv8 Test image detections: {res}")
                        for det in res:
                            x1_n, y1_n, x2_n, y2_n = det["bbox"]
                            h_img, w_img, _ = test_img.shape
                            x1 = int(x1_n * w_img)
                            y1 = int(y1_n * h_img)
                            x2 = int(x2_n * w_img)
                            y2 = int(y2_n * h_img)
                            cv2.rectangle(test_img, (x1, y1), (x2, y2), (0, 0, 255), 2)
                            label = f"{det['class']} {det['confidence']:.2f}"
                            cv2.putText(test_img, label, (x1, max(20, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
                    cv2.imwrite("/config/annotated_test_yolov8.jpg", test_img)
                    logger.info("DIAGNOSTIC - Saved annotated test image to /config/annotated_test_yolov8.jpg")
                else:
                    logger.warning(f"DIAGNOSTIC - Failed to load {test_img_path}")
        except Exception as e:
            logger.error(f"Failed to load RKNN model: {e}")
            raise

    def detect(self, frame: np.ndarray, conf_threshold: float) -> list[dict]:
        """
        Runs co-processor NPU inference and parses outputs using integrated math helpers.
        """
        if self.rknn is None:
            raise RuntimeError("RKNN runtime is not initialized. Call load() first.")

        height, width, _ = frame.shape

        # 1. Letterbox the frame to the model's expected input size (640x640)
        letterboxed_frame, ratio, (dw, dh) = letterbox(frame, new_shape=(640, 640))

        # 2. Colorspace conversion
        # GStreamer reads in BGR; native model was compiled for RGB
        rgb_frame = cv2.cvtColor(letterboxed_frame, cv2.COLOR_BGR2RGB)

        # 3. Add batch dimension and convert to NCHW as expected by standard RKNN INT8 models
        nchw = np.transpose(rgb_frame, (2, 0, 1))
        nchw = np.expand_dims(nchw, 0).astype(np.uint8)

        # 4. Run hardware inference
        outputs = self.rknn.inference(inputs=[nchw])

        # 5. Apply battle-tested YOLOv8 NPU postprocessing
        pp = post_process_yolov8(
            outputs,
            input_size=640,
            obj_thresh=conf_threshold,
            nms_thresh=self.iou_threshold,
            box_format=self.box_format,
            box_scale=self.box_scale
        )

        detections = []
        if pp is not None:
            boxes, classes, scores = pp
            for box, cls_id, sc in zip(boxes, classes, scores):
                x1, y1, x2, y2 = [float(v) for v in box]

                # Map coordinates back to original frame size (before letterboxing)
                x1 = (x1 - dw) / ratio
                y1 = (y1 - dh) / ratio
                x2 = (x2 - dw) / ratio
                y2 = (y2 - dh) / ratio

                # Exclude detections that fall heavily in the letterbox padding areas
                # (a small margin of 15 pixels is allowed for border-crossing detections)
                if x1 < -15.0 or y1 < -15.0 or x2 > width + 15.0 or y2 > height + 15.0:
                    continue

                cls_id = int(cls_id)
                class_name = COCO_CLASSES[cls_id] if cls_id < len(COCO_CLASSES) else f"unknown-{cls_id}"

                # Bound coordinates inside frame parameters and normalize to [0.0, 1.0]
                x1_norm = round(max(0.0, x1) / width, 4)
                y1_norm = round(max(0.0, y1) / height, 4)
                x2_norm = round(min(float(width), x2) / width, 4)
                y2_norm = round(min(float(height), y2) / height, 4)

                detections.append({
                    "class": class_name,
                    "confidence": round(float(sc), 4),
                    "bbox": [x1_norm, y1_norm, x2_norm, y2_norm]
                })

        return detections
