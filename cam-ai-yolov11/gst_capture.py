import logging
import threading
import time
import numpy as np
import cv2

# Import PyGObject GStreamer bindings
try:
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst, GLib
except ImportError as e:
    raise ImportError(
        "GStreamer Python bindings not found. Make sure python3-gst-1.0 and gi are installed."
    ) from e

# Initialize GStreamer
Gst.init(None)

logger = logging.getLogger("GStreamerCapture")

class SafeFrameBuffer:
    """A thread-safe, single-frame buffer with newest-frame semantics."""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None
        self._timestamp = None
        self._new_frame_event = threading.Event()

    def push(self, frame: np.ndarray, timestamp: float):
        """Pushes a new frame, overwriting any previous unread frame."""
        with self._lock:
            self._frame = frame
            self._timestamp = timestamp
        self._new_frame_event.set()

    def pop(self, timeout: float = None) -> tuple[np.ndarray | None, float | None]:
        """
        Blocks until a new frame is pushed, then returns it.
        Uses Event to block efficiently without CPU-intensive polling.
        """
        if not self._new_frame_event.wait(timeout):
            return None, None
        
        with self._lock:
            frame = self._frame
            timestamp = self._timestamp
            # Clear the event so the next call to pop blocks until a new push occurs
            self._new_frame_event.clear()
            return frame, timestamp


class GStreamerCapture:
    """Manages the GStreamer pipeline and captures frames from the SHM producer with auto-reconnection."""

    def __init__(self, shm_path: str, width: int, height: int, fps: int):
        self.shm_path = shm_path
        self.width = width
        self.height = height
        self.fps = fps
        
        self.frame_buffer = SafeFrameBuffer()
        self.pipeline = None
        self.running = False
        self.worker_thread = None
        self.reconnect_event = threading.Event()
        self._state_lock = threading.Lock()

        # Define pipeline string
        # Configured for low latency and zero unnecessary caching
        self.pipeline_str = (
            f"shmsrc socket-path={self.shm_path} is-live=true do-timestamp=true ! "
            f"queue max-size-buffers=1 max-size-time=0 max-size-bytes=0 leaky=downstream ! "
            f"video/x-raw,format=NV12,width={self.width},height={self.height},framerate={self.fps}/1 ! "
            f"appsink name=sink emit-signals=true sync=false drop=true max-buffers=1"
        )
        logger.info(f"Initialized with pipeline: {self.pipeline_str}")

    def start(self):
        """Launches the background orchestrator thread for the GStreamer pipeline."""
        if self.running:
            logger.warning("Pipeline is already running.")
            return

        logger.info("Starting GStreamer capture orchestrator...")
        self.running = True
        self.reconnect_event.clear()
        
        # Start worker thread
        self.worker_thread = threading.Thread(target=self._pipeline_orchestrator_loop, name="GstOrchestrator", daemon=True)
        self.worker_thread.start()
        logger.info("GStreamer capture orchestrator started.")

    def stop(self):
        """Stops the pipeline and orchestrator thread, cleaning up resources."""
        if not self.running:
            return

        logger.info("Stopping GStreamer pipeline and orchestrator...")
        with self._state_lock:
            self.running = False
            self.reconnect_event.set()
            if self.pipeline:
                try:
                    self.pipeline.set_state(Gst.State.NULL)
                except Exception as e:
                    logger.error(f"Error setting pipeline state to NULL: {e}")
                self.pipeline = None
        
        if self.worker_thread:
            self.worker_thread.join(timeout=3.0)
            self.worker_thread = None

        logger.info("GStreamer pipeline and orchestrator stopped.")

    def _pipeline_orchestrator_loop(self):
        """Background loop that ensures the GStreamer pipeline runs and recovers from errors."""
        while True:
            with self._state_lock:
                if not self.running:
                    break
                logger.info("Attempting to initialize GStreamer pipeline...")
            
            pipeline = None
            try:
                # Create and parse pipeline
                pipeline = Gst.parse_launch(self.pipeline_str)
                appsink = pipeline.get_by_name("sink")
                if not appsink:
                    raise RuntimeError("Could not find appsink 'sink' in the GStreamer pipeline.")
                
                # Attach callback using the 'new-sample' signal
                appsink.connect("new-sample", self._on_new_sample)
                
                # Set pipeline state to PLAYING
                ret = pipeline.set_state(Gst.State.PLAYING)
                if ret == Gst.StateChangeReturn.FAILURE:
                    raise RuntimeError("Failed to set GStreamer pipeline to PLAYING state.")
                
                # Check if stopped during transition
                with self._state_lock:
                    if not self.running:
                        pipeline.set_state(Gst.State.NULL)
                        break
                    self.pipeline = pipeline
                    logger.info("GStreamer pipeline successfully set to PLAYING.")
                    bus = pipeline.get_bus()
                
                # Monitor bus for errors or EOS
                while True:
                    with self._state_lock:
                        if not self.running:
                            break
                    
                    # Check bus for messages (timeout to regularly check self.running)
                    msg = bus.timed_pop_filtered(
                        200 * Gst.MSECOND,
                        Gst.MessageType.ERROR | Gst.MessageType.EOS
                    )
                    if not msg:
                        continue
                    
                    t = msg.type
                    if t == Gst.MessageType.EOS:
                        logger.warning("Received End-Of-Stream (EOS) signal. Producer likely stopped.")
                        break
                    elif t == Gst.MessageType.ERROR:
                        err, debug = msg.parse_error()
                        logger.error(f"GStreamer Pipeline Error: {err.message} | Debug Info: {debug}")
                        break
                        
            except Exception as e:
                logger.error(f"Error in GStreamer pipeline execution: {e}")
            
            # Cleanup current pipeline
            with self._state_lock:
                if self.pipeline:
                    logger.info("Cleaning up GStreamer pipeline...")
                    try:
                        self.pipeline.set_state(Gst.State.NULL)
                    except Exception as ex:
                        logger.error(f"Error setting pipeline to NULL: {ex}")
                    self.pipeline = None
                elif pipeline:
                    try:
                        pipeline.set_state(Gst.State.NULL)
                    except Exception:
                        pass
            
            # Reconnection delay
            with self._state_lock:
                if not self.running:
                    break
            
            logger.info("Waiting 2 seconds before attempting reconnection...")
            self.reconnect_event.wait(timeout=2.0)
            with self._state_lock:
                if self.running:
                    self.reconnect_event.clear()
        
        logger.info("Orchestrator loop exited.")

    def _on_new_sample(self, sink) -> Gst.FlowReturn:
        """
        Callback triggered by GStreamer appsink when a new frame is ready.
        Executes on the GStreamer streaming thread.
        Converts the incoming NV12 buffer to BGR natively via OpenCV and pushes it.
        """
        if not self.running:
            return Gst.FlowReturn.OK

        sample = sink.emit("pull-sample")
        if not sample:
            return Gst.FlowReturn.OK

        buffer = sample.get_buffer()
        if not buffer:
            return Gst.FlowReturn.OK

        # Map buffer memory for reading
        success, map_info = buffer.map(Gst.MapFlags.READ)
        if not success:
            logger.error("Failed to map GStreamer buffer memory.")
            return Gst.FlowReturn.ERROR

        try:
            # NV12 image format layout:
            # Y plane: width * height bytes
            # UV plane: interleaved, width * (height / 2) bytes
            # Total size: width * height * 1.5
            expected_size = int(self.width * self.height * 1.5)
            if map_info.size < expected_size:
                logger.warning(
                    f"Buffer size {map_info.size} is less than expected NV12 size {expected_size}."
                )
                return Gst.FlowReturn.OK

            # Direct buffer-to-numpy conversion (no-copy array creation)
            # Reshaping to (height + height // 2, width) representing NV12 planar structure
            raw_nv12 = np.frombuffer(map_info.data, dtype=np.uint8).reshape(
                (self.height + self.height // 2, self.width)
            )

            # High-performance native conversion to BGR
            # This is completed in OpenCV C++ backend and avoids any custom loop overhead
            bgr_frame = cv2.cvtColor(raw_nv12, cv2.COLOR_YUV2BGR_NV12)
            
            # Use real-world wall clock timestamp for low-latency drift checks
            timestamp = time.time()

            # Push to safe buffer for consumer consumption
            self.frame_buffer.push(bgr_frame, timestamp)

        except Exception as e:
            logger.exception(f"Error handling new sample: {e}")
            return Gst.FlowReturn.ERROR
        finally:
            # Always unmap buffer to prevent memory leaks and pipeline stalls
            buffer.unmap(map_info)

        return Gst.FlowReturn.OK
