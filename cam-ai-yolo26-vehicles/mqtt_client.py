import json
import logging
import time
import threading
import paho.mqtt.client as mqtt

logger = logging.getLogger("MQTTClient")

class MQTTClientWrapper:
    """
    Wrapper for Paho MQTT Client.
    Handles thread-safe, non-blocking telemetry publication with auto-reconnection.
    """

    def __init__(self, camera_id: str):
        self.camera_id = camera_id
        
        # Instantiate client with Paho v1 compatibility
        # We will pin paho-mqtt < 2.0.0 in requirements.txt to avoid protocol signature changes.
        import os, socket, time
        core_mask = os.getenv("RKNN_CORE_MASK", "0")
        npu_slot = f"_npu{core_mask}" if core_mask in ["1", "2"] else ""
        hostname = socket.gethostname()
        client_id = f"cam_ai_worker_{self.camera_id}{npu_slot}_{hostname}_veh_{os.getpid()}"
        self.client = mqtt.Client(client_id=client_id)
        
        self.connected = False
        
        # Assign event callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish

    def connect(self, host: str, port: int, keepalive: int = 60):
        """Starts asynchronous connection attempt with robust startup retries in a background thread."""
        logger.info(f"Initializing connection to MQTT Broker at {host}:{port}...")
        
        # loop_start() launches a daemon thread to handle networking and automatic reconnections once connected
        self.client.loop_start()
        
        def connection_worker():
            retry_interval = 1.0
            while True:
                try:
                    logger.info(f"Attempting connection to MQTT broker at {host}:{port}...")
                    self.client.connect(host, port, keepalive)
                    logger.info("Initial connection command successfully issued.")
                    break
                except Exception as e:
                    logger.warning(
                        f"MQTT Broker at {host}:{port} not ready ({e}). Retrying in {retry_interval:.1f}s..."
                    )
                    time.sleep(retry_interval)
                    # Exponential backoff capped at 15.0 seconds
                    retry_interval = min(15.0, retry_interval * 1.5)

        t = threading.Thread(target=connection_worker, name="MQTTConnectThread", daemon=True)
        t.start()

    def stop(self):
        """Cleanly stops network thread and disconnects from broker."""
        logger.info("Stopping MQTT background client...")
        self.connected = False
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("MQTT Client shut down.")

    def publish_detections(self, topic: str, timestamp: float, detections: list[dict], analytics: dict = None):
        """
        Publishes detections to MQTT in a standardized format.
        Fails fast if connection is not ready.
        """
        if not self.connected or not detections:
            return

        payload = {
            "camera": self.camera_id,
            "timestamp": timestamp,
            "detections": detections
        }
        if analytics:
            payload["analytics"] = analytics

        try:
            payload_str = json.dumps(payload)
            info = self.client.publish(topic, payload_str, qos=0, retain=False)
            if info.rc != mqtt.MQTT_ERR_SUCCESS and self.connected:
                logger.error(
                    f"MQTT Publish failed with error code: {info.rc} ({mqtt.error_string(info.rc)})"
                )
        except Exception as e:
            logger.error(f"Failed to encode or publish detection telemetry: {e}")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            logger.info("Successfully connected to MQTT Broker.")
        else:
            self.connected = False
            logger.error(
                f"Failed to connect to MQTT Broker. Reason code: {rc} ({mqtt.connack_string(rc)})"
            )

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc != 0:
            logger.warning(f"Unexpectedly disconnected from MQTT broker. Code: {rc}. Auto-reconnect active.")
        else:
            logger.info("Disconnected from MQTT broker.")

    def _on_publish(self, client, userdata, mid):
        # Callback when a message has been successfully sent to the socket
        # Keeps logs clean, but can be enabled for diagnostic debugging if needed
        logger.debug(f"Telemetry packet (id: {mid}) successfully pushed to broker.")
