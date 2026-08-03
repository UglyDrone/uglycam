import os
import logging
import json

class Config:
    """Application Configuration loaded from environment variables and JSON config store."""
    
    # Camera Stream Settings
    CAMERA_ID = os.getenv("CAMERA_ID", "cam0")
    SHM_PATH = os.getenv("SHM_PATH", f"/tmp/{CAMERA_ID}_ai")
    
    # Frame Dimensions (Must match the producer stream caps exactly)
    FRAME_WIDTH = int(os.getenv("FRAME_WIDTH", "640"))
    FRAME_HEIGHT = int(os.getenv("FRAME_HEIGHT", "640"))
    FRAME_FPS = int(os.getenv("FRAME_FPS", "5"))
    
    # AI Inference Settings
    MODEL_PATH = os.getenv("MODEL_PATH", "models/yolo26n-rk3588.rknn")
    CONF_THRESHOLD = float(os.getenv("CONF_THRESHOLD", "0.25"))
    RKNN_CORE_MASK = int(os.getenv("RKNN_CORE_MASK", "0"))
    
    # MQTT Telemetry Settings
    MQTT_HOST = os.getenv("MQTT_HOST", "host.docker.internal")
    MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
    MQTT_KEEPALIVE = int(os.getenv("MQTT_KEEPALIVE", "60"))
    MQTT_TOPIC = os.getenv("MQTT_TOPIC", f"/{CAMERA_ID}/detections")
    
    # Dynamic settings from cameras.json config store
    ENABLED = True
    PUBLISH_DETECTIONS = True
    
    # System Settings
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

    # Load from shared cameras.json configuration store if present
    JSON_CONFIG_PATH = "/config/cameras.json"
    if os.path.exists(JSON_CONFIG_PATH):
        try:
            with open(JSON_CONFIG_PATH, "r") as f:
                json_data = json.load(f)
            if CAMERA_ID in json_data:
                cam_settings = json_data[CAMERA_ID]
                ENABLED = cam_settings.get("enabled", ENABLED)
                FRAME_FPS = int(cam_settings.get("fps", FRAME_FPS))
                PUBLISH_DETECTIONS = cam_settings.get("publish_detections", PUBLISH_DETECTIONS)
                
                # Get model selection based on RKNN_CORE_MASK
                core_mask_env = os.getenv("RKNN_CORE_MASK", "0")
                if core_mask_env == "2":
                    model_name = cam_settings.get("model2", "none")
                else:
                    model_name = cam_settings.get("model1", cam_settings.get("model", "yolov26"))

                if model_name.lower() == "none":
                    ENABLED = False
                else:
                    # Map model string to target rknn weight path if compatible
                    if model_name in ["yolov26", "yolov26-supervision"]:
                        MODEL_PATH = "models/yolo26n-rk3588.rknn"
                    else:
                        # Fall back to default model for this container
                        pass
                    
                print(f"[Config] Loaded parameters for {CAMERA_ID} from cameras.json: "
                      f"enabled={ENABLED}, fps={FRAME_FPS}, model={MODEL_PATH}, publish={PUBLISH_DETECTIONS}")
        except Exception as e:
            print(f"[Config] Warning: Failed to parse cameras.json: {e}")

def setup_logging():
    """Sets up standard structured logging across the application."""
    log_format = "%(asctime)s [%(levelname)s] [%(name)s] (%(threadName)s) %(message)s"
    logging.basicConfig(
        level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
        format=log_format,
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    # Reduce noise from external libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)

