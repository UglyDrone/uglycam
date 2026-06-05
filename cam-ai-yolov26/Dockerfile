FROM ubuntu:22.04

# Prevent interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Install system Python 3.10, pip, GStreamer, and GLib dependencies
# Using ubuntu:22.04 matches the target host OS exactly, ensuring ABI compatibility
# and cleanly resolving C-extension loading errors caused by dual-Python environments.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-gi \
    python3-gst-1.0 \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gir1.2-gstreamer-1.0 \
    vim \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory inside the container
WORKDIR /workspace

# Copy the native RKNN runtime C library (librknnrt.so) to system library path
COPY librknnrt.so /usr/lib/librknnrt.so

# Copy the RKNN toolkit wheel file provided in the build context

# (User needs to download this wheel from Rockchip repo or have it in their build environment)
COPY rknn_toolkit_lite2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl /tmp/

# Install python dependencies including the RKNN Lite wheel
# The system pip3 puts packages directly into system Python's path
RUN pip3 install --no-cache-dir \
    numpy \
    opencv-python-headless \
    "paho-mqtt<2.0.0" \
    eclipse-zenoh \
    /tmp/rknn_toolkit_lite2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl \
    && rm -f /tmp/rknn_toolkit_lite2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl

# Copy application source code
COPY main.py config.py gst_capture.py detector.py mqtt_client.py zenoh_client.py /workspace/

# Pre-stage model weights directory and copy model file
RUN mkdir -p /workspace/models
COPY yolo26n-rk3588.rknn /workspace/models/yolo26n-rk3588.rknn

# The application runs natively with system Python 3.10
ENTRYPOINT ["python3", "main.py"]
