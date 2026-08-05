#!/usr/bin/env python3
"""
Mock RTSP Stream Ingestion for uglycam
Decodes RTSP stream (H.264/H.265) and creates shared memory sockets (/tmp/cam0_main, /tmp/cam0_ai, /tmp/cam0_ai_rtsp)
"""

import sys
import os
import json
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 runmock.py <rtsp_url> [camera_id]")
        print("Example: python3 runmock.py rtsp://admin:Q1w2e3r4@hiknvr.allmine.mobi:554/Streaming/Channels/101 cam0")
        sys.exit(1)

    rtsp_url = sys.argv[1]
    cam_id = sys.argv[2] if len(sys.argv) > 2 else "cam0"

    # Read target FPS from cameras.json config
    fps = 5
    config_path = "/opt/uglycam/config/cameras.json"
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                cfg = json.load(f)
                fps = cfg.get(cam_id, {}).get("fps", 5)
        except Exception:
            pass

    print("==================================================")
    print("Starting Mock RTSP Stream Ingestion (Python GStreamer)")
    print(f"RTSP URL:       {rtsp_url}")
    print(f"Camera ID:      {cam_id}")
    print(f"AI Target FPS:  {fps}")
    print("==================================================")

    Gst.init(None)

    # Pipeline using urisourcebin with automatic decoding & SHM sinks
    pipeline_str = f"""
        urisourcebin uri="{rtsp_url}" buffer-duration=200000000 name=src !
        videoconvert ! video/x-raw,format=NV12 !
        videoscale ! video/x-raw,width=1920,height=1080 !
        tee name=t0

        t0. ! queue max-size-buffers=4 leaky=downstream !
        shmsink socket-path=/tmp/{cam_id}_main wait-for-connection=false sync=false shm-size=134217728

        t0. ! queue max-size-buffers=2 leaky=downstream !
        videorate ! video/x-raw,framerate={fps}/1 !
        videoscale ! video/x-raw,format=NV12,width=640,height=360 !
        shmsink socket-path=/tmp/{cam_id}_ai wait-for-connection=false sync=false shm-size=67108864

        t0. ! queue max-size-buffers=4 leaky=downstream !
        videoconvert ! video/x-raw,format=I420 !
        videoscale ! video/x-raw,width=640,height=360 !
        videoconvert ! video/x-raw,format=NV12 !
        shmsink socket-path=/tmp/{cam_id}_ai_rtsp wait-for-connection=false sync=false shm-size=67108864
    """

    try:
        pipeline = Gst.parse_launch(pipeline_str)
    except Exception as e:
        print(f"[ERROR] Failed to parse GStreamer pipeline: {e}")
        sys.exit(1)

    loop = GLib.MainLoop()

    # Bus monitoring for errors and messages
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_bus_message(bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            print("[GStreamer] End-of-stream reached.")
            loop.quit()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"[GStreamer Error] {err.message}")
            if debug:
                print(f"[GStreamer Debug] {debug}")
            loop.quit()
        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == pipeline:
                old_state, new_state, pending_state = message.parse_state_changed()
                print(f"[GStreamer] Pipeline state changed: {old_state.value_nick} -> {new_state.value_nick}")
                if new_state == Gst.State.PLAYING:
                    print(f"SUCCESS: Pipeline is PLAYING. Sockets created in /tmp:")
                    print(f"  - /tmp/{cam_id}_main")
                    print(f"  - /tmp/{cam_id}_ai")
                    print(f"  - /tmp/{cam_id}_ai_rtsp")

    bus.connect("message", on_bus_message)

    print("[GStreamer] Setting pipeline to PLAYING...")
    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        print("[ERROR] Failed to set pipeline to PLAYING state.")
        sys.exit(1)

    try:
        loop.run()
    except KeyboardInterrupt:
        print("\n[GStreamer] Stopping pipeline...")
    finally:
        pipeline.set_state(Gst.State.NULL)
        print("[GStreamer] Pipeline stopped cleanly.")

if __name__ == "__main__":
    main()
