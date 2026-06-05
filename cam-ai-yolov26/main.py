import logging
import signal
import sys
import threading
import time

from config import Config, setup_logging
from gst_capture import GStreamerCapture
from detector import RKNNYOLOv8Detector
from mqtt_client import MQTTClientWrapper
from zenoh_client import ZenohClientWrapper

# Initialize global logger
setup_logging()
logger = logging.getLogger("MainOrchestrator")

class EdgeAIWorker:
    """Orchestrates GStreamer capture, YOLO inference, and MQTT telemetry."""

    def __init__(self):
        self.stop_event = threading.Event()
        self.inference_thread = None
        self.capture = None
        self.detector = None
        self.mqtt_client = None
        self.zenoh_client = None

    def start(self):
        """Initializes components and starts all concurrent threads."""
        logger.info("==================================================")
        logger.info("Starting RK3588 Edge AI Worker...")
        logger.info(f"CAMERA_ID:     {Config.CAMERA_ID}")
        logger.info(f"SHM_PATH:      {Config.SHM_PATH}")
        logger.info(f"RESOLUTION:    {Config.FRAME_WIDTH}x{Config.FRAME_HEIGHT}")
        logger.info(f"FPS:           {Config.FRAME_FPS}")
        logger.info(f"MODEL_PATH:    {Config.MODEL_PATH}")
        logger.info(f"MQTT_BROKER:   {Config.MQTT_HOST}:{Config.MQTT_PORT}")
        logger.info(f"MQTT_TOPIC:    {Config.MQTT_TOPIC}")
        logger.info("==================================================")

        # Check if pipeline is enabled; exit gracefully if not
        if not Config.ENABLED:
            logger.warning(f"Camera pipeline for {Config.CAMERA_ID} is DISABLED in config. Exiting worker container gracefully.")
            sys.exit(0)

        # 1. Initialize detector backend (YOLOv8)
        self.detector = RKNNYOLOv8Detector()
        self.detector.load(Config.MODEL_PATH)

        # 2. Initialize MQTT Client
        self.mqtt_client = MQTTClientWrapper(camera_id=Config.CAMERA_ID)
        self.mqtt_client.connect(host=Config.MQTT_HOST, port=Config.MQTT_PORT, keepalive=Config.MQTT_KEEPALIVE)

        # 2b. Initialize Zenoh Client
        self.zenoh_client = ZenohClientWrapper(camera_id=Config.CAMERA_ID)
        self.zenoh_client.connect()

        # 3. Initialize GStreamer Capture
        self.capture = GStreamerCapture(
            shm_path=Config.SHM_PATH,
            width=Config.FRAME_WIDTH,
            height=Config.FRAME_HEIGHT,
            fps=Config.FRAME_FPS
        )
        self.capture.start()

        # 4. Launch Inference Processing Thread
        self.inference_thread = threading.Thread(
            target=self._inference_worker_loop,
            name="InferenceThread",
            daemon=True
        )
        self.inference_thread.start()

        logger.info("All worker subsystems fully operational.")

    def stop(self):
        """Triggers graceful cleanup and termination of all running threads."""
        logger.info("Initiating graceful shutdown...")
        
        # 1. Set the stop flag for the inference thread
        self.stop_event.set()

        # 2. Stop GStreamer capture thread (stops pipeline callbacks)
        if self.capture:
            self.capture.stop()

        # 3. Wait for inference processing thread to terminate
        if self.inference_thread:
            logger.info("Waiting for inference thread to exit...")
            self.inference_thread.join(timeout=2.0)
            if self.inference_thread.is_alive():
                logger.warning("Inference thread failed to exit within timeout.")
            else:
                logger.info("Inference thread exited successfully.")

        # 4. Stop MQTT client loop and disconnect
        if self.mqtt_client:
            self.mqtt_client.stop()

        # Stop Zenoh client
        if self.zenoh_client:
            self.zenoh_client.stop()

        logger.info("Graceful shutdown completed successfully. Exiting.")

    def _inference_worker_loop(self):
        """
        Background loop executing model inference on the latest frame.
        Maintains 'newest-frame' semantics by fetching frames from the SafeFrameBuffer.
        """
        logger.info("Inference worker loop active.")
        
        # Track statistics for performance validation
        frame_counter = 0
        last_stat_time = time.time()

        while not self.stop_event.is_set():
            try:
                # Retrieve latest BGR frame from GStreamer appsink
                # Timeout allows periodic checks of the stop_event
                frame, timestamp = self.capture.frame_buffer.pop(timeout=0.5)
                
                if frame is None:
                    continue  # Timeout occurred with no new frame
                
                t_inference_start = time.perf_counter()

                # Run object detection
                detections = self.detector.detect(frame, Config.CONF_THRESHOLD)

                # Performance benchmarks
                processing_time = time.perf_counter() - t_inference_start
                frame_counter += 1

                # Periodically log throughput and average latency every 30 seconds
                now = time.time()
                if now - last_stat_time >= 30.0:
                    fps = frame_counter / (now - last_stat_time)
                    logger.info(
                        f"Performance Metrics | Inference Speed: {processing_time * 1000:.1f}ms | "
                        f"Effective Processing Throughput: {fps:.2f} FPS"
                    )
                    frame_counter = 0
                    last_stat_time = now

                # Only publish telemetry if target objects are detected and publishing is enabled
                if detections and Config.PUBLISH_DETECTIONS:
                    # Capture latency is current wall-clock minus capture timestamp
                    transport_latency = time.time() - timestamp
                    logger.debug(
                        f"Detected {len(detections)} targets in {processing_time * 1000:.1f}ms. "
                        f"Total pipe latency (capture->infer): {transport_latency * 1000:.1f}ms"
                    )
                    
                    self.mqtt_client.publish_detections(
                        topic=Config.MQTT_TOPIC,
                        timestamp=timestamp,
                        detections=detections
                    )

                    if self.zenoh_client:
                        self.zenoh_client.publish_detections(
                            timestamp=timestamp,
                            detections=detections
                        )

            except Exception as e:
                logger.error(f"Unhandled exception in inference loop: {e}", exc_info=True)
                # Short backoff to prevent CPU spinning in case of tight failure loops
                time.sleep(0.1)

        logger.info("Inference worker loop stopped.")


def main():
    """Application entrypoint."""
    worker = EdgeAIWorker()

    # Define signal handlers for container orchestration (e.g. docker stop)
    def signal_handler(signum, frame):
        signame = signal.Signals(signum).name
        logger.warning(f"Received system signal {signame} ({signum}).")
        worker.stop()
        sys.exit(0)

    # Register handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        worker.start()
        # Keep main thread alive while background threads perform work
        while True:
            time.sleep(1.0)
    except SystemExit:
        pass
    except Exception as e:
        logger.critical(f"Unhandled critical failure in main thread: {e}", exc_info=True)
        worker.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
