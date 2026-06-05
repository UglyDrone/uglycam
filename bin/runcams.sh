#!/bin/sh

FPS=$(jq -r '.cam0.fps' /opt/uglycam/config/cameras.json)
echo "AI camera FPS $FPS"

v4l2-ctl -d /dev/video11 \
  --set-fmt-video=width=1920,height=1080,pixelformat=NV12

gst-launch-1.0 -e \
v4l2src device=/dev/video11 io-mode=mmap do-timestamp=true ! \
video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! \
tee name=t0 \
\
t0. ! queue max-size-buffers=4 leaky=downstream ! \
shmsink socket-path=/tmp/cam0_main \
wait-for-connection=false sync=false \
shm-size=134217728 \
\
t0. ! queue max-size-buffers=2 leaky=downstream ! \
videorate ! video/x-raw,framerate=$FPS/1 ! \
videoscale ! \
video/x-raw,format=NV12,width=640,height=360 ! \
shmsink socket-path=/tmp/cam0_ai \
wait-for-connection=false sync=false \
shm-size=67108864 \
\
t0. ! queue max-size-buffers=4 leaky=downstream ! \
videoconvert ! video/x-raw,format=I420 ! \
videoscale ! video/x-raw,width=640,height=360 ! \
videoconvert ! video/x-raw,format=NV12 ! \
shmsink socket-path=/tmp/cam0_ai_rtsp \
  wait-for-connection=false sync=false \
  shm-size=67108864

