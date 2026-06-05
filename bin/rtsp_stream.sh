#!/bin/sh

#gst-launch-1.0 -e videotestsrc pattern=ball is-live=true ! video/x-raw,width=1920,height=1080,framerate=30/1 ! queue ! openh264enc bitrate=4000000 ! h264parse ! rtspclientsink location=rtsp://127.0.0.1:8554/cam0

#GST_DEBUG=1 gst-launch-1.0 -e \
#shmsrc socket-path=/tmp/cam0_main is-live=true do-timestamp=true ! \
#video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! \
#queue ! \
#videoconvert ! \
#video/x-raw,format=I420 ! \
#openh264enc bitrate=4000000 ! \
#h264parse ! \
#rtspclientsink location=rtsp://127.0.0.1:8554/cam0

# workaround Orange pi kernel build

ln -s /dev/dma_heap/system /dev/dma_heap/system-uncached-dma32
#hw encoder
GST_DEBUG=1 gst-launch-1.0 -e shmsrc socket-path=/tmp/cam0_main is-live=true do-timestamp=true ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! queue max-size-buffers=1 leaky=downstream ! mpph264enc bps=4000000 rc-mode=cbr ! h264parse config-interval=1 ! rtspclientsink protocols=tcp location=rtsp://127.0.0.1:8554/cam0
