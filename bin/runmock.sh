#!/bin/sh

# Usage: ./bin/runmock.sh <rtsp_url> [camera_id]
# Example: ./bin/runmock.sh rtsp://admin:Q1w2e3r4@hiknvr.allmine.mobi:554/Streaming/Channels/101 cam0

if [ -z "$1" ]; then
  echo "Error: RTSP URL parameter is required."
  echo "Usage: $0 <rtsp_url> [camera_id]"
  echo "Example: $0 rtsp://admin:password@192.168.20.100:554/stream cam0"
  exit 1
fi

RTSP_URL="$1"
CAM_ID="${2:-cam0}"

CONFIG_FILE="/opt/uglycam/config/cameras.json"
if [ -f "$CONFIG_FILE" ]; then
  FPS=$(jq -r ".${CAM_ID}.fps // 5" "$CONFIG_FILE")
else
  FPS=5
fi

echo "=================================================="
echo "Starting Mock RTSP Stream Ingestion (RK3588 MPP Hardware Accelerated)"
echo "RTSP URL:       $RTSP_URL"
echo "Camera ID:      $CAM_ID"
echo "AI Target FPS:  $FPS"
echo "=================================================="

# Uses RK3588 MPP Hardware H.264/H.265 VPU decoder (mppvideodec) for zero-latency streaming
gst-launch-1.0 -e \
rtspsrc location="$RTSP_URL" protocols=tcp latency=200 drop-on-latency=true name=src \
src. ! rtph264depay ! h264parse ! mppvideodec ! \
videoconvert ! \
video/x-raw,format=NV12 ! \
videoscale ! \
video/x-raw,width=1920,height=1080 ! \
tee name=t0 \
\
t0. ! queue max-size-buffers=4 leaky=downstream ! \
shmsink socket-path=/tmp/${CAM_ID}_main \
wait-for-connection=false sync=false \
shm-size=134217728 \
\
t0. ! queue max-size-buffers=2 leaky=downstream ! \
videorate ! video/x-raw,framerate=${FPS}/1 ! \
videoscale ! \
video/x-raw,format=NV12,width=640,height=360 ! \
shmsink socket-path=/tmp/${CAM_ID}_ai \
wait-for-connection=false sync=false \
shm-size=67108864 \
\
t0. ! queue max-size-buffers=4 leaky=downstream ! \
videoconvert ! video/x-raw,format=I420 ! \
videoscale ! video/x-raw,width=640,height=360 ! \
videoconvert ! video/x-raw,format=NV12 ! \
shmsink socket-path=/tmp/${CAM_ID}_ai_rtsp \
  wait-for-connection=false sync=false \
  shm-size=67108864
