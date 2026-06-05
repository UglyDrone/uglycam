/* Master Dashboard Logic for Edge AI Camera Hub */

// State Management
let activeCamera = 'cam0';
let camerasConfig = {};
let mqttClient = null;
let lastDetectionTime = 0;
let statsInterval = null;
let bboxTimeout = null;

// Default Global Network settings
const settings = {
    mqttHost: window.location.hostname || '127.0.0.1',
    mqttPort: 9001,
    mediaMtxPort: 8889
};

// Colors mapping for classes to draw high-quality glowing overlays
const CLASS_COLORS = {
    'person': '#00ff87',     // Neon emerald
    'bicycle': '#00c6ff',    // Neon blue
    'car': '#00c6ff',
    'motorcycle': '#00c6ff',
    'bus': '#00c6ff',
    'truck': '#00c6ff',
    'dog': '#ff5f56',        // Coral red
    'cat': '#ff5f56',
    'sports ball': '#ffbd2e', // Yellow
    'default': '#a29bfe'     // Purple
};

// UI Elements
const webrtcIframe = document.getElementById('webrtcIframe');
const webrtcIframeAi = document.getElementById('webrtcIframeAi');
const bboxCanvas = document.getElementById('bboxCanvas');
const ctx = bboxCanvas.getContext('2d');
const streamPlaceholder = document.getElementById('streamPlaceholder');
const streamPlaceholderAi = document.getElementById('streamPlaceholderAi');
const pipelineTitle = document.getElementById('pipelineTitle');

// Initialize Dashboard
document.addEventListener('DOMContentLoaded', () => {
    loadGlobalSettings();
    initTabControllers();
    initFormControls();
    initMqttConnection();
    fetchCamerasConfig();
    fetchSystemStats();
    
    // Poll system statistics every 2 seconds
    statsInterval = setInterval(fetchSystemStats, 2000);

    // Setup Canvas dynamic resizing
    window.addEventListener('resize', resizeCanvas);
    resizeCanvas();
});

// Load config settings from LocalStorage
function loadGlobalSettings() {
    const savedHost = localStorage.getItem('mqttHost');
    const savedMqttPort = localStorage.getItem('mqttPort');
    const savedMediaMtxPort = localStorage.getItem('mediaMtxPort');

    if (savedHost) settings.mqttHost = savedHost;
    if (savedMqttPort) settings.mqttPort = parseInt(savedMqttPort);
    if (savedMediaMtxPort) settings.mediaMtxPort = parseInt(savedMediaMtxPort);

    // Populate Settings UI fields
    document.getElementById('txtMqttHost').value = settings.mqttHost;
    document.getElementById('txtMqttPort').value = settings.mqttPort;
    document.getElementById('txtMediaMtxPort').value = settings.mediaMtxPort;
}

// Navigation Tabs Manager
function initTabControllers() {
    const tabButtons = document.querySelectorAll('.tab-btn');
    const cameraPanel = document.getElementById('cameraPanel');
    const settingsPanel = document.getElementById('settingsPanel');

    tabButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            tabButtons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            const tabName = btn.getAttribute('data-tab');
            
            if (tabName === 'settings') {
                cameraPanel.classList.remove('active-panel');
                settingsPanel.classList.add('active-panel');
            } else {
                settingsPanel.classList.remove('active-panel');
                cameraPanel.classList.add('active-panel');
                
                // Swap active camera
                activeCamera = tabName;
                pipelineTitle.textContent = "AI pipeline for Camera";
                
                // Load updated stream source and fields
                updateStreamSource();
                applyConfigToUI();
                clearCanvas();
            }
        });
    });
}

// Checkboxes and buttons controller
function initFormControls() {
    // Toggle Live Stream Iframe visibility
    const chkPreview = document.getElementById('chkPreview');
    chkPreview.addEventListener('change', () => {
        if (chkPreview.checked) {
            webrtcIframe.classList.remove('hidden');
            webrtcIframeAi.classList.remove('hidden');
            streamPlaceholder.style.zIndex = 0;
            streamPlaceholderAi.style.zIndex = 0;
            updateStreamSource();
        } else {
            webrtcIframe.classList.add('hidden');
            webrtcIframeAi.classList.add('hidden');
            webrtcIframe.src = '';
            webrtcIframeAi.src = '';
            streamPlaceholder.style.zIndex = 2;
            streamPlaceholderAi.style.zIndex = 2;
            clearCanvas();
        }
    });

    // Save settings button handler
    document.getElementById('btnSaveSettings').addEventListener('click', () => {
        const host = document.getElementById('txtMqttHost').value.trim();
        const mPort = parseInt(document.getElementById('txtMqttPort').value);
        const sPort = parseInt(document.getElementById('txtMediaMtxPort').value);

        if (host) settings.mqttHost = host;
        settings.mqttPort = mPort || 9001;
        settings.mediaMtxPort = sPort || 8889;

        localStorage.setItem('mqttHost', settings.mqttHost);
        localStorage.setItem('mqttPort', settings.mqttPort);
        localStorage.setItem('mediaMtxPort', settings.mediaMtxPort);

        showToast("Global network settings saved!");
        
        // Re-establish MQTT connection to new broker coordinates
        initMqttConnection();
        updateStreamSource();
    });

    // Send pipeline update parameters to host API
    document.getElementById('btnUpdate').addEventListener('click', () => {
        const btn = document.getElementById('btnUpdate');
        btn.disabled = true;
        btn.textContent = "Updating...";

        const payload = {
            camera_id: activeCamera,
            settings: {
                enabled: document.getElementById('chkEnabled').checked,
                fps: parseInt(document.getElementById('selFps').value),
                model: document.getElementById('selModel').value,
                publish_detections: document.getElementById('chkPublish').checked
            }
        };

        fetch('/api/config', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                showToast("Camera pipeline updated! Restarting worker...");
                // Update local config memory
                camerasConfig[activeCamera] = payload.settings;
            } else {
                showToast("Failed to save settings: " + data.message);
            }
        })
        .catch(err => {
            console.error(err);
            showToast("Network error updating parameters.");
        })
        .finally(() => {
            btn.disabled = false;
            btn.textContent = "Update";
        });
    });
}

// Fetch cameras.json parameters from backend
function fetchCamerasConfig() {
    fetch('/api/config')
        .then(res => res.json())
        .then(data => {
            camerasConfig = data;
            applyConfigToUI();
            updateStreamSource();
        })
        .catch(err => {
            console.error("Error fetching cameras config:", err);
            showToast("Failed to load cameras parameters from backend.");
        });
}

// Display the selected camera's configuration values
function applyConfigToUI() {
    const config = camerasConfig[activeCamera];
    if (!config) return;

    document.getElementById('chkEnabled').checked = config.enabled;
    document.getElementById('selFps').value = config.fps.toString();
    document.getElementById('selModel').value = config.model;
    document.getElementById('chkPublish').checked = config.publish_detections;

    // Update captions dynamically
    const captionCam = document.getElementById('captionCamera');
    const captionCamAi = document.getElementById('captionCameraAi');
    if (captionCam) captionCam.textContent = "Camera Feed • 1920x1080 @ 30 fps";
    if (captionCamAi) captionCamAi.textContent = `AI Feed • 640x360 @ ${config.fps} fps`;
}

// Load dynamic WHEP WebRTC preview
function updateStreamSource() {
    if (document.getElementById('chkPreview').checked) {
        // Construct WHEP WebRTC player URL served by MediaMTX
        // Points to http://<host>:<webrtc_port>/<camera_id>/
        const host = settings.mqttHost || window.location.hostname;
        webrtcIframe.src = `http://${host}:${settings.mediaMtxPort}/cam0/`;
        webrtcIframeAi.src = `http://${host}:${settings.mediaMtxPort}/cam0_ai/`;
    }
}

// Fetch hardware gauges metrics from backend
function fetchSystemStats() {
    fetch('/api/system')
        .then(res => res.json())
        .then(data => {
            animateProgressBar('barCpu', 'valCpu', data.cpu);
            animateProgressBar('barMem', 'valMem', data.memory);
            
            // Map the 3 cores of RK3588 NPU
            if (data.npu && data.npu.length >= 3) {
                animateProgressBar('barNpu0', 'valNpu0', data.npu[0]);
                animateProgressBar('barNpu1', 'valNpu1', data.npu[1]);
                animateProgressBar('barNpu2', 'valNpu2', data.npu[2]);
            }
            
            // Update CPU & NPU temperatures
            if (data.temp_cpu !== undefined && data.temp_cpu !== null) {
                animateTempProgressBar('barCpuTemp', 'valCpuTemp', data.temp_cpu);
            }
            
            if (data.temp_npu !== undefined && data.temp_npu !== null) {
                animateTempProgressBar('barNpuTemp', 'valNpuTemp', data.temp_npu);
            }
        })
        .catch(err => {
            console.warn("Unable to fetch system telemetry:", err);
        });
}

// Animate utilization bars smoothly
function animateProgressBar(barId, valId, value) {
    const bar = document.getElementById(barId);
    const text = document.getElementById(valId);
    if (bar && text) {
        bar.style.width = `${value}%`;
        text.textContent = `${value}%`;
    }
}

// Animate temperature bars smoothly (0-100C scale)
function animateTempProgressBar(barId, valId, value) {
    const bar = document.getElementById(barId);
    const text = document.getElementById(valId);
    if (bar && text) {
        const percentage = Math.max(0, Math.min(100, value));
        bar.style.width = `${percentage}%`;
        text.textContent = `${value}°C`;
    }
}

// Bounding Box High-DPI Canvas overlay scaling
function resizeCanvas() {
    const rect = bboxCanvas.parentElement.getBoundingClientRect();
    
    // Scale for high resolution rendering
    const dpr = window.devicePixelRatio || 1;
    bboxCanvas.width = rect.width * dpr;
    bboxCanvas.height = rect.height * dpr;
    
    bboxCanvas.style.width = `${rect.width}px`;
    bboxCanvas.style.height = `${rect.height}px`;
    
    ctx.scale(dpr, dpr);
}

// Establish WebSockets connection to Mosquitto Broker
function initMqttConnection() {
    if (mqttClient) {
        try {
            mqttClient.disconnect();
        } catch (e) {}
    }

    const host = settings.mqttHost || window.location.hostname;
    const clientId = `cam_manager_web_${Math.random().toString(16).substr(2, 8)}`;
    
    console.log(`Connecting to MQTT Broker WebSockets on: ws://${host}:${settings.mqttPort}/mqtt`);
    mqttClient = new Paho.MQTT.Client(host, settings.mqttPort, clientId);

    mqttClient.onConnectionLost = (responseObject) => {
        if (responseObject.errorCode !== 0) {
            console.warn(`MQTT connection lost: ${responseObject.errorMessage}. Retrying...`);
            setTimeout(initMqttConnection, 3000);
        }
    };

    mqttClient.onMessageArrived = (message) => {
        try {
            const payload = JSON.parse(message.payloadString);
            
            // Render only if message belongs to current camera
            if (payload.camera === activeCamera) {
                renderDetections(payload.detections);
            }
        } catch (e) {
            console.error("Error parsing MQTT detections payload:", e);
        }
    };

    const options = {
        timeout: 3,
        onSuccess: () => {
            console.log("Successfully connected to MQTT WebSockets!");
            // Subscribe to all camera telemetry topics
            mqttClient.subscribe("/+/detections");
        },
        onFailure: (err) => {
            console.warn("MQTT WebSockets connection failed:", err.errorMessage);
            setTimeout(initMqttConnection, 5000);
        }
    };

    mqttClient.connect(options);
}

// Render dynamic colored boxes on top of feed
function renderDetections(detections) {
    clearCanvas();
    lastDetectionTime = Date.now();

    const w = bboxCanvas.width / (window.devicePixelRatio || 1);
    const h = bboxCanvas.height / (window.devicePixelRatio || 1);

    detections.forEach(det => {
        const [x1, y1, x2, y2] = det.bbox; // Normalized float coordinates (0.0 to 1.0)
        
        // Map to display resolution coordinates
        const left = x1 * w;
        const top = y1 * h;
        const width = (x2 - x1) * w;
        const height = (y2 - y1) * h;

        const color = CLASS_COLORS[det.class] || CLASS_COLORS['default'];

        // 1. Draw glowing outer shadow box
        ctx.strokeStyle = color;
        ctx.lineWidth = 3;
        ctx.lineJoin = 'round';
        ctx.shadowBlur = 10;
        ctx.shadowColor = color;
        
        ctx.strokeRect(left, top, width, height);

        // Reset shadows for solid overlays
        ctx.shadowBlur = 0;

        // 2. Draw Semi-transparent inside box overlay
        ctx.fillStyle = hexToRgba(color, 0.12);
        ctx.fillRect(left, top, width, height);

        // 3. Draw text banner label
        const confText = `${Math.round(det.confidence * 100)}%`;
        const label = `${det.class} ${confText}`;
        
        ctx.font = "bold 13px 'Outfit', sans-serif";
        const textWidth = ctx.measureText(label).width;
        
        ctx.fillStyle = color;
        ctx.fillRect(left - 1.5, top - 22, textWidth + 16, 22);

        // Draw label text
        ctx.fillStyle = '#ffffff';
        ctx.fillText(label, left + 6, top - 6);
    });

    // Auto-clear boxes if detections stop coming in (handles object exit/timeout)
    if (bboxTimeout) clearTimeout(bboxTimeout);
    bboxTimeout = setTimeout(() => {
        if (Date.now() - lastDetectionTime >= 800) {
            clearCanvas();
        }
    }, 800);
}

// Canvas utility functions
function clearCanvas() {
    const w = bboxCanvas.width / (window.devicePixelRatio || 1);
    const h = bboxCanvas.height / (window.devicePixelRatio || 1);
    ctx.clearRect(0, 0, w, h);
}

// Convert Hex colors to translucent RGBA
function hexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// Display Toast notifications
function showToast(message) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.classList.add('show');
    setTimeout(() => {
        toast.classList.remove('show');
    }, 3500);
}
