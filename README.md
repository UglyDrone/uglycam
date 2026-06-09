# Production Edge AI Worker for Rockchip RK3588 (Native RKNN)

A modular, production-ready, and highly optimized Python-based Dockerized AI worker designed for the **Rockchip RK3588 SBC** (e.g., Orange Pi 5 Pro, Rock 5B, Firefly) running Ubuntu 22.04.

This service connects to a GStreamer Shared Memory (SHM) stream produced on the host, extracts NV12 frame buffers, performs **native NPU-accelerated RKNN inference**, and publishes telemetry in parallel to both an **MQTT broker** and a **Zenoh router**.

By utilizing the native RK3588 NPU (`rknnlite`):
1. **Inference Latency** drops from **~100ms (CPU)** to **~3-5ms (NPU)**.
2. **Container Image Footprint** drops from **~2.5GB to ~380MB** (by completely eliminating heavy PyTorch and Ultralytics dependencies).
3. **CPU Utilization** is drastically minimized, freeing host CPU cores for streaming and pipeline tasks.

---

## System Architecture

The application is structured to prioritize low latency, newest-frame semantics, and thread safety. Detections are published concurrently to MQTT and Zenoh.

```
                  Host Node                               Container
┌───────────────────────────────────────────┐    ┌───────────────────────────┐
│  Host Camera GStreamer Pipeline           │    │  GStreamer Capture Thread │
│  (Produces NV12 frames to /tmp/cam0_ai)   │    │  (appsink callbacks)      │
└─────────────────────┬─────────────────────┘    └─────────────┬─────────────┘
                      │                                        │
                      ▼ (IPC Shared Memory Volume)             ▼
             [ /tmp/cam0_ai Socket ] ────────────────► [ SafeFrameBuffer ]
                                                     (Single-frame, zero-queue)
                                                               │
                                                               ▼
                                                 ┌───────────────────────────┐
                                                 │  Inference Thread         │
                                                 │  - Native RKNN-Lite NPU   │
                                                 └─────────────┬─────────────┘
                                                               │
                                                               ▼ (Dual Telemetry)
                                                 ┌─────────────┴─────────────┐
                                                 │                           │
                                                 ▼                           ▼
┌───────────────────────────────────────────┐ ┌───────────────────────────┐ ┌───────────────────────────┐
│  Mosquitto Broker (host.docker.internal)  │ │ MQTT Client Wrapper       │ │ Zenoh Client Wrapper      │
└───────────────────────────────────────────┘ └─────────────┬─────────────┘ └─────────────┬─────────────┘
                                                            │                             │
                                                            ▼                             ▼
                                                     MQTT Publish                  Zenoh Publish
                                                   (/cam0/detections)        (demo/camcam/cam0/detections)
```

### Core Architecture Highlights

1. **Callback-Driven GStreamer Capture**: Rather than using blocking polling loops, the service binds to the GStreamer `appsink` element's `new-sample` signal. The GStreamer streaming thread triggers a Python callback the microsecond a new frame is made available in shared memory.
2. **Strict Newest-Frame Semantics**: The pipeline uses a `SafeFrameBuffer` containing a single-frame holder protected by thread locks and controlled by `threading.Event`. If inference is slower than the incoming frame rate (5 FPS), old frames are instantly overwritten. The detector thread is guaranteed to always process the absolute freshest frame, preventing queue backup and latency drift.
3. **Decoupled Telemetry**: Telemetry publishing pipelines run on background client threads, separating the critical inference loop from network roundtrips, temporary broker connection drops, or Zenoh route updates.
4. **Vectorized NumPy-based Post-Processing**: Native RKNN models output raw tensors. This application includes a fully vectorized post-processor written in NumPy that decodes distribution focal loss (DFL) coordinates, performs vectorized sigmoids, and applies Non-Maximum Suppression (NMS) in microseconds.

---

## Directory Structure

```
├── docker-compose.yml          # Container configuration (Mounts NPU, MQTT, and Zenoh router)
├── README.md                   # This instruction manual
├── manager/                    # Management Dashboard and Web API Service
├── bin/                        # Host GStreamer capture and encoding scripts
│   ├── runcams.sh              # Captures raw camera sensor frames to SHM sinks
│   ├── ai_rtsp_stream.sh       # Encodes low-res SHM frame to H264 for RTSP AI view
│   ├── rtsp_stream.sh          # Encodes full-res main SHM frame to H.264 (MPP hardware accelerated)
│   └── compile.sh              # Helper script to compile device tree overlay
├── contrib/                    # Production deployment configuration files
│   ├── orangepi5pro/           # DT overlays and environment file for Orange Pi 5 Pro
│   │   ├── ov13855-4lane.dts   # 4-lane OV13855 MIPI-CSI camera device tree overlay
│   │   └── orangepiEnv.txt     # U-Boot bootloader environment config enabling camera
│   └── etc/systemd/system/     # Production Systemd unit files for background daemons
│       ├── cam0.service        # Manages the GStreamer capture service
│       ├── cam0-ai.service     # Manages the AI RTSP streaming encoder service
│       ├── cam0-full.service   # Manages the full-resolution main RTSP stream encoder
│       ├── cam0-reload.path    # Path watcher tracking config updates to cameras.json
│       └── cam0-reload.service # Triggers pipeline restart upon config updates
├── cam-ai-yolov8/              # Yolov8 AI processing container source
├── cam-ai-yolov11/             # Yolov11 AI processing container source
├── cam-ai-yolov26/             # Yolov26 AI processing container source
├── cam-ai-yolov26-supervision/ # YOLOv26 AI processing container with Roboflow Supervision analytics
└── cam-ai-lprnet/              # LPRNet license plate recognition container source
```

---

## Configuration Variables

The AI worker is completely configured via environment variables, defined in `config.py` in each worker folder:

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `CAMERA_ID` | `cam0` | String ID used for logging and MQTT/Zenoh topic namespaces. |
| `SHM_PATH` | `/tmp/cam0_ai` | Absolute path to the host GStreamer SHM socket. |
| `FRAME_WIDTH` | `640` | Video frame width in pixels (must match producer). |
| `FRAME_HEIGHT` | `360` | Video frame height in pixels (must match producer). |
| `FRAME_FPS` | `5` | Framerate matching the GStreamer producer stream. |
| `MODEL_PATH` | `models/yolov8.rknn` | Path to compiled RKNN model. |
| `CONF_THRESHOLD` | `0.25` | Confidence filter threshold for detections. |
| `MQTT_HOST` | `127.0.0.1` | DNS or IP of the host MQTT broker. |
| `MQTT_PORT` | `1883` | Network port of the host MQTT broker. |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |

---

## Production Deployment to Host `/opt/uglycam`

The production system is designed to reside in `/opt/uglycam`. Follow these setup steps on your target Orange Pi 5 Pro running Ubuntu 22.04:

### Step 1: Create Installation Directory
```bash
sudo mkdir -p /opt/uglycam/bin /opt/uglycam/config
```

### Step 2: Copy Scripts and Configs
Copy the binary helper scripts and your cameras configuration file to `/opt/uglycam`:
```bash
sudo cp bin/* /opt/uglycam/bin/
sudo chmod +x /opt/uglycam/bin/*.sh
sudo cp -r config/* /opt/uglycam/config/
```

### Step 3: Install GStreamer and System Dependencies on Host
To support host-level capturing and H.264 hardware/software encoding, install the GStreamer plugins:
```bash
sudo apt-get update
sudo apt-get install -y jq gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
  gstreamer1.0-plugins-rtp v4l-utils
```

### Step 4: Configure Device Tree Overlay (Orange Pi 5 Pro camera)
Copy and compile the device tree overlay for the 4-lane OV13855 sensor:
```bash
sudo cp contrib/orangepi5pro/ov13855-4lane.dts /boot/overlay-user/
sudo cp contrib/orangepi5pro/orangepiEnv.txt /boot/orangepiEnv.txt
sudo /opt/uglycam/bin/compile.sh
sudo reboot
```

### Step 5: Install and Enable Systemd Services
Copy the production service files to the systemd directory, reload the daemon, and start the services. The path watcher `cam0-reload.path` will automatically reload the services whenever you modify the JSON cameras configuration.
```bash
sudo cp contrib/etc/systemd/system/cam0* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cam0.path cam0.service cam0-ai.service cam0-full.service
```

---

## Docker Quick Start & Verification

### Step 1: Place the RKNN-Lite Wheel in the Worker Context
Place the Rockchip `rknnlite` wheel in each worker folder before building:
- e.g., `cam-ai-yolov8/rknn_toolkit_lite2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl`

### Step 2: Build and Run
Build the container stack:
```bash
docker compose build
docker compose up -d
```

### Step 3: Verify MQTT Telemetry
Subscribe to the MQTT topics from the host to verify detection publishing for each NPU slot:
```bash
# Verify NPU Core 1 (Slot 1) detections
docker exec -it rk3588-mosquitto mosquitto_sub -t "/cam0/detections/npu1"

# Verify NPU Core 2 (Slot 2) detections
docker exec -it rk3588-mosquitto mosquitto_sub -t "/cam0/detections/npu2"
```

### Step 4: Verify Zenoh Telemetry
Clients publish detection events to Zenoh at the key expression `demo/camcam/{camera_id}/detections/{npu_slot}`.
You can use `zenoh` CLI tools to verify data ingestion:
```bash
# Listen to Zenoh telemetry using the zenoh-cli tool
zenoh subscribe "demo/camcam/+/detections/#"
```

#### Expected Telemetry Payload (Standard):
```json
{
  "camera": "cam0",
  "timestamp": 1710000000.123,
  "detections": [
    {
      "class": "person",
      "confidence": 0.9254,
      "bbox": [0.125, 0.334, 0.458, 0.789]
    }
  ]
}
```

#### Expected Telemetry Payload (YOLOv26 Supervision):
```json
{
  "camera": "cam0",
  "timestamp": 1710000000.123,
  "detections": [
    {
      "class": "person",
      "confidence": 0.9254,
      "bbox": [0.125, 0.334, 0.458, 0.789]
    }
  ],
  "analytics": {
    "total_count": 1,
    "counts": {
      "person": 1
    }
  }
}
```

#### Expected Telemetry Payload (LPRNet):
```json
{
  "camera": "cam0",
  "timestamp": 1710000000.123,
  "detections": [
    {
      "class": "ABC-1234",
      "confidence": 0.99,
      "bbox": [0.05, 0.05, 0.95, 0.95]
    }
  ],
  "analytics": {
    "total_count": 1,
    "counts": {
      "ABC-1234": 1
    }
  }
}
```
*(Bounding boxes `bbox` represent normalized coordinates `[x1, y1, x2, y2]` in the range `[0.0, 1.0]`)*

---

## Hardware Device Passthrough Configuration

To enable the container to communicate with the physical RK3588 NPU, the `docker-compose.yml` mounts the actual hardware driver nodes from the host:
- `/dev/rknn`: The hardware NPU character driver.
- `/dev/mpp`: Media Process Platform driver (for hardware decoding).
- `/dev/rga`: Raster Graphic Acceleration driver (for fast 2D colorspace conversions and scaling).
- `/dev/dma_heap`: Linux DMA-BUF Heap memory manager to implement zero-copy.
