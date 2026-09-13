import cv2
from ultralytics import YOLO
from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS
import threading
from datetime import datetime
from pathlib import Path
import sqlite3
import time
import os
import json
import numpy as np
from queue import Empty, Full, Queue
from collections import deque

try:
    import pytesseract
except ImportError:
    pytesseract = None


CONFIDENCE_THRESHOLD = 0.40
DETECTION_EVENT_COOLDOWN_SECONDS = 10
BUS_ID = os.getenv("SMART_ROAD_BUS_ID", "BUS-001")
CAMERA_ID = os.getenv("SMART_ROAD_CAMERA_ID", "CAMERA-001")
INGESTION_TOKEN = os.getenv("SMART_ROAD_INGESTION_TOKEN", "")
ACCIDENT_CONFIRMATION_FRAMES = 3
ACCIDENT_HISTORY_FRAMES = 5
ACCIDENT_COOLDOWN_SECONDS = 60
ACCIDENT_BUFFER_SECONDS = 3
ACCIDENT_AFTER_FRAMES = 45
ACCIDENT_VIDEO_FPS = 15
DEMO_TEST_MODE = os.getenv("SMART_ROAD_DEMO_TEST_MODE", "0").strip().lower() in {
    "1", "true", "yes", "on"
}
VIDEO_SOURCE_VALUE = os.getenv("SMART_ROAD_VIDEO_SOURCE", "0").strip() or "0"
VIDEO_SOURCE = (
    int(VIDEO_SOURCE_VALUE)
    if VIDEO_SOURCE_VALUE.isdigit()
    else VIDEO_SOURCE_VALUE
)
VIDEO_SOURCE_LABEL = (
    f"Webcam {VIDEO_SOURCE}"
    if isinstance(VIDEO_SOURCE, int)
    else VIDEO_SOURCE_VALUE
)
CAMERA_MODE = os.getenv("SMART_ROAD_CAMERA_MODE", "local").strip().lower()
BROWSER_CAMERA_ONLY = CAMERA_MODE == "browser" or VIDEO_SOURCE_VALUE.lower() == "browser"


# ==========================================
# FLASK API SETUP
# ==========================================

dashboard_path = Path(__file__).resolve().parent.parent / "dashboard"
app = Flask(
    __name__,
    static_folder=str(dashboard_path),
    static_url_path=""
)
CORS(
    app,
    resources={
        r"/api/*": {
            "origins": [
                "https://urbanx-0109.web.app",
                "http://localhost:5500",
                "http://127.0.0.1:5500"
            ]
        }
    }
)


@app.route("/")
def serve_dashboard():
    return send_from_directory(dashboard_path, "index.html")

# ==========================================
# LIVE DATA
# ==========================================

ai_models = {
    "vehicle_model": "connected",
    "pothole_model": "unavailable",
    "flood_model": "unavailable",
    "zebra_model": "unavailable",
    "accident_model": "unavailable",
    "plate_model": "unavailable",
}

live_data = {
    "bus_id": BUS_ID,
    "camera_id": CAMERA_ID,
    "persons": 0,
    "vehicles": 0,
    "traffic_status": "LOW",
    "road_defects": 0,
    "potholes": 0,
    "floods": 0,
    "zebra_crossings": 0,
    "active_alerts": 0,
    "accident_detected": False,
    "accident_alert": None,
    "latitude": 11.0168,
    "longitude": 76.9558,
    "buses": {},
    "ai_models": ai_models,
    "demo_test_mode": DEMO_TEST_MODE,
    "video_source": VIDEO_SOURCE_LABEL,
    "accident_diagnostics": {
        "overlap_threshold": 0.15,
        "current_overlap": 0.0,
        "accident_confidence": 0.0,
        "accident_history": [],
        "history_window": ACCIDENT_HISTORY_FRAMES,
        "confirmation_frames": 0,
        "required_confirmation_frames": ACCIDENT_CONFIRMATION_FRAMES,
    },
}

bus_registry = {}

latest_frame = None
latest_frame_lock = threading.Lock()
last_detection_log = {
    "Pothole": set(),
    "Flood": set(),
    "Zebra": set()
}
last_event_times = {}
accident_frame_buffer = deque(maxlen=ACCIDENT_BUFFER_SECONDS * ACCIDENT_VIDEO_FPS)
accident_confirmation_count = 0
accident_detection_history = deque(maxlen=ACCIDENT_HISTORY_FRAMES)
current_accident_overlap = 0.0
current_accident_confidence = 0.0
last_accident_time = 0
pending_accident = None
incident_sequence = 0
last_active_alerts = set()
last_ingested_signatures = {}
database_write_queue = Queue()
browser_frame_queue = Queue(maxsize=1)
browser_camera_active = BROWSER_CAMERA_ONLY


# ==========================================
# REPORT HISTORY
# ==========================================

database_path = Path(__file__).resolve().parent / "report_history.db"
evidence_path = Path(__file__).resolve().parent / "accident_evidence"


def initialize_report_database():
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bus_id TEXT NOT NULL DEFAULT 'BUS-001',
                camera_id TEXT NOT NULL DEFAULT 'CAMERA-001',
                timestamp TEXT NOT NULL,
                vehicles INTEGER NOT NULL,
                persons INTEGER NOT NULL,
                traffic_status TEXT NOT NULL,
                road_defects INTEGER NOT NULL,
                potholes INTEGER NOT NULL DEFAULT 0,
                floods INTEGER NOT NULL DEFAULT 0,
                zebra_crossings INTEGER NOT NULL DEFAULT 0,
                active_alerts INTEGER NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL
            )
            """
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(reports)")
        }
        if "potholes" not in columns:
            connection.execute(
                "ALTER TABLE reports ADD COLUMN potholes INTEGER NOT NULL DEFAULT 0"
            )
        if "floods" not in columns:
            connection.execute(
                "ALTER TABLE reports ADD COLUMN floods INTEGER NOT NULL DEFAULT 0"
            )
        if "zebra_crossings" not in columns:
            connection.execute(
                "ALTER TABLE reports ADD COLUMN zebra_crossings INTEGER NOT NULL DEFAULT 0"
            )
        if "bus_id" not in columns:
            connection.execute(
                "ALTER TABLE reports ADD COLUMN bus_id TEXT NOT NULL DEFAULT 'BUS-001'"
            )
        if "camera_id" not in columns:
            connection.execute(
                "ALTER TABLE reports ADD COLUMN camera_id TEXT NOT NULL DEFAULT 'CAMERA-001'"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS detection_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                bus_id TEXT NOT NULL,
                camera_id TEXT NOT NULL DEFAULT 'CAMERA-001',
                detection_type TEXT NOT NULL,
                confidence REAL,
                latitude REAL,
                longitude REAL,
                severity TEXT,
                status TEXT
            )
            """
        )
        event_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(detection_events)")
        }
        if "camera_id" not in event_columns:
            connection.execute(
                "ALTER TABLE detection_events ADD COLUMN camera_id TEXT NOT NULL DEFAULT 'CAMERA-001'"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bus_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                latitude REAL,
                longitude REAL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS accident_incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL UNIQUE,
                bus_id TEXT NOT NULL DEFAULT 'BUS-001',
                camera_id TEXT NOT NULL DEFAULT 'CAMERA-001',
                timestamp TEXT NOT NULL,
                camera_location TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                incident_type TEXT NOT NULL,
                vehicle_type TEXT NOT NULL,
                registration_number TEXT NOT NULL,
                plate_confidence REAL,
                accident_image_path TEXT,
                vehicle_crop_path TEXT,
                plate_crop_path TEXT,
                accident_video_path TEXT,
                status TEXT NOT NULL
            )
            """
        )
        incident_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(accident_incidents)")
        }
        for column, definition in {
            "bus_id": "TEXT NOT NULL DEFAULT 'BUS-001'",
            "camera_id": "TEXT NOT NULL DEFAULT 'CAMERA-001'",
            "latitude": "REAL",
            "longitude": "REAL",
        }.items():
            if column not in incident_columns:
                connection.execute(
                    f"ALTER TABLE accident_incidents ADD COLUMN {column} {definition}"
                )


def load_report_history():
    initialize_report_database()
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT bus_id, camera_id, timestamp, vehicles, persons, traffic_status,
                   road_defects, potholes, floods, zebra_crossings,
                   active_alerts, latitude, longitude
            FROM reports
            ORDER BY id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _write_report(report):
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO reports (
                bus_id, camera_id, timestamp, vehicles, persons, traffic_status, road_defects,
                potholes, floods, zebra_crossings, active_alerts,
                latitude, longitude
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report["bus_id"],
                report["camera_id"],
                report["timestamp"],
                report["vehicles"],
                report["persons"],
                report["traffic_status"],
                report["road_defects"],
                report["potholes"],
                report["floods"],
                report["zebra_crossings"],
                report["active_alerts"],
                report["latitude"],
                report["longitude"],
            ),
        )


def _write_detection_event(
    detection_type,
    confidence,
    latitude,
    longitude,
    bus_id=BUS_ID,
    camera_id=CAMERA_ID,
    timestamp=None,
    severity="high",
    status="open",
):
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO detection_events (
                timestamp, bus_id, camera_id, detection_type, confidence,
                latitude, longitude, severity, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp or datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
                bus_id,
                camera_id,
                detection_type,
                confidence,
                latitude,
                longitude,
                severity,
                status,
            )
        )


def _write_alert(alert_type, severity, status, latitude, longitude,
               bus_id=BUS_ID, camera_id=CAMERA_ID, timestamp=None):
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO alerts (
                bus_id, camera_id, timestamp, alert_type, severity, status,
                latitude, longitude
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bus_id,
                camera_id,
                timestamp or datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
                alert_type,
                severity,
                status,
                latitude,
                longitude,
            ),
        )


def _write_accident_incident(incident):
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO accident_incidents (
                incident_id, bus_id, camera_id, timestamp, camera_location,
                latitude, longitude, incident_type,
                vehicle_type, registration_number, plate_confidence,
                accident_image_path, vehicle_crop_path, plate_crop_path,
                accident_video_path, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident["incident_id"], incident["bus_id"], incident["camera_id"],
                incident["timestamp"], incident["camera_location"],
                incident["latitude"], incident["longitude"],
                incident["incident_type"],
                incident["vehicle_type"], incident["registration_number"],
                incident["plate_confidence"], incident["accident_image_path"],
                incident["vehicle_crop_path"], incident["plate_crop_path"],
                incident["accident_video_path"], incident["status"],
            ),
        )
    print(f"Accident report/database save: {incident['incident_id']}")


def enqueue_database_write(writer, *args, **kwargs):
    database_write_queue.put((writer, args, kwargs))


def database_writer_loop():
    while True:
        writer, args, kwargs = database_write_queue.get()
        try:
            writer(*args, **kwargs)
        except Exception as error:
            print(f"Database write error: {error}")
        finally:
            database_write_queue.task_done()


def save_report(report):
    enqueue_database_write(_write_report, report)


def save_detection_event(*args, **kwargs):
    enqueue_database_write(_write_detection_event, *args, **kwargs)


def save_alert(*args, **kwargs):
    enqueue_database_write(_write_alert, *args, **kwargs)


def save_accident_incident(incident):
    enqueue_database_write(_write_accident_incident, incident)


def update_bus_registry(bus_id, camera_id, timestamp, latitude, longitude,
                        payload=None):
    bus_registry[bus_id] = {
        "bus_id": bus_id,
        "camera_id": camera_id,
        "timestamp": timestamp,
        "latitude": latitude,
        "longitude": longitude,
        "data": payload or {},
    }


report_history = load_report_history()
database_writer_thread = threading.Thread(
    target=database_writer_loop,
    daemon=True,
)
database_writer_thread.start()


# ==========================================
# LIVE DATA API
# ==========================================

@app.route("/api/live-data")
def get_live_data():
    selected_bus_id = request.args.get("bus_id")
    if selected_bus_id and selected_bus_id != BUS_ID:
        return jsonify(bus_registry.get(selected_bus_id, {
            "bus_id": selected_bus_id,
            "buses": bus_registry,
        }))
    live_data["buses"] = bus_registry
    return jsonify(live_data)


def generate_video_stream():
    while True:
        with latest_frame_lock:
            frame = None if latest_frame is None else latest_frame.copy()

        if frame is None:
            time.sleep(0.1)
            continue

        success, encoded_frame = cv2.imencode(".jpg", frame)
        if not success:
            time.sleep(0.1)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + encoded_frame.tobytes()
            + b"\r\n"
        )


@app.route("/video_feed")
def video_feed():
    return Response(
        generate_video_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/api/detect-frame", methods=["POST", "DELETE"])
def detect_browser_frame():
    global browser_camera_active

    if request.method == "DELETE":
        browser_camera_active = BROWSER_CAMERA_ONLY
        live_data["video_source"] = "Browser webcam" if BROWSER_CAMERA_ONLY else VIDEO_SOURCE_LABEL
        print("[CAMERA] Browser camera session released")
        return jsonify({"accepted": True, "browser_camera_active": browser_camera_active})

    uploaded_frame = request.files.get("frame")
    encoded_frame = uploaded_frame.read() if uploaded_frame else request.get_data(cache=False)
    if not encoded_frame:
        return jsonify({"error": "JPEG frame is required"}), 400

    frame = cv2.imdecode(
        np.frombuffer(encoded_frame, dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    if frame is None:
        return jsonify({"error": "Invalid JPEG frame"}), 400

    browser_camera_active = True
    live_data["video_source"] = "Browser webcam"
    try:
        browser_frame_queue.get_nowait()
    except Empty:
        pass
    try:
        browser_frame_queue.put_nowait(frame)
    except Full:
        return jsonify({"accepted": False, "error": "Frame queue is busy"}), 503

    print("[AI FRAME] Received frame")
    return jsonify({"accepted": True, "queued": True})


# ==========================================
# REPORTS API
# ==========================================

@app.route("/api/reports")
def get_reports():
    report_history[:] = load_report_history()
    selected_bus_id = request.args.get("bus_id")
    reports = (
        report_history
        if not selected_bus_id
        else [item for item in report_history if item["bus_id"] == selected_bus_id]
    )
    return jsonify(reports)


@app.route("/api/detection-events")
def get_detection_events():
    initialize_report_database()
    selected_bus_id = request.args.get("bus_id")
    where_clause = "WHERE bus_id = ?" if selected_bus_id else ""
    parameters = (selected_bus_id,) if selected_bus_id else ()
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
                 SELECT timestamp, bus_id, camera_id, detection_type, confidence,
                   latitude, longitude, severity, status
            FROM detection_events
                 {where_clause}
            ORDER BY id DESC
            LIMIT 25
                 """.format(where_clause=where_clause),
                 parameters,
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/accident-incidents")
def get_accident_incidents():
    initialize_report_database()
    selected_bus_id = request.args.get("bus_id")
    where_clause = "WHERE bus_id = ?" if selected_bus_id else ""
    parameters = (selected_bus_id,) if selected_bus_id else ()
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
                 SELECT incident_id, bus_id, camera_id, timestamp, camera_location,
                     latitude, longitude, incident_type,
                   vehicle_type, registration_number, plate_confidence,
                   accident_image_path, vehicle_crop_path, plate_crop_path,
                   accident_video_path, status
            FROM accident_incidents
                 {where_clause}
                 ORDER BY rowid DESC
            LIMIT 50
                 """.format(where_clause=where_clause),
                 parameters,
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/alerts")
def get_alerts():
    initialize_report_database()
    selected_bus_id = request.args.get("bus_id")
    where_clause = "WHERE bus_id = ?" if selected_bus_id else ""
    parameters = (selected_bus_id,) if selected_bus_id else ()
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT bus_id, camera_id, timestamp, alert_type, severity, status,
                   latitude, longitude
            FROM alerts
            {where_clause}
            ORDER BY id DESC
            LIMIT 50
            """.format(where_clause=where_clause),
            parameters,
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/accident-evidence/<path:filename>")
def accident_evidence(filename):
    return send_from_directory(evidence_path, filename, as_attachment=False)


@app.route("/api/ingest", methods=["POST"])
def ingest_bus_data():
    if not INGESTION_TOKEN or request.headers.get("X-API-Key") != INGESTION_TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    bus_id = str(payload.get("bus_id", "")).strip()
    camera_id = str(payload.get("camera_id", "")).strip()
    if not bus_id or not camera_id:
        return jsonify({"error": "bus_id and camera_id are required"}), 400

    timestamp = payload.get("timestamp") or datetime.now().strftime(
        "%d-%m-%Y %H:%M:%S"
    )
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    update_bus_registry(
        bus_id, camera_id, timestamp, latitude, longitude, payload
    )

    meaningful_payload = dict(payload)
    meaningful_payload.pop("timestamp", None)
    signature = json.dumps(meaningful_payload, sort_keys=True, default=str)
    state_changed = last_ingested_signatures.get(bus_id) != signature
    last_ingested_signatures[bus_id] = signature
    if not state_changed:
        return jsonify({
            "accepted": True,
            "changed": False,
            "bus_id": bus_id,
            "camera_id": camera_id,
        })

    for detection in payload.get("detection_events", []):
        save_detection_event(
            detection.get("detection_type", "unknown"),
            detection.get("confidence"),
            latitude,
            longitude,
            bus_id=bus_id,
            camera_id=camera_id,
            timestamp=timestamp,
            severity=detection.get("severity", "medium"),
            status=detection.get("status", "open"),
        )

    report = payload.get("report")
    if isinstance(report, dict):
        save_report({
            "bus_id": bus_id,
            "camera_id": camera_id,
            "timestamp": report.get("timestamp", timestamp),
            "vehicles": report.get("vehicles", 0),
            "persons": report.get("persons", 0),
            "traffic_status": report.get("traffic_status", "low"),
            "road_defects": report.get("road_defects", 0),
            "potholes": report.get("potholes", 0),
            "floods": report.get("floods", 0),
            "zebra_crossings": report.get("zebra_crossings", 0),
            "active_alerts": report.get("active_alerts", 0),
            "latitude": latitude,
            "longitude": longitude,
        })

    for alert in payload.get("alerts", []):
        save_alert(
            alert.get("alert_type", "unknown"),
            alert.get("severity", "medium"),
            alert.get("status", "open"),
            latitude,
            longitude,
            bus_id=bus_id,
            camera_id=camera_id,
            timestamp=timestamp,
        )

    accident = payload.get("accident_incident")
    if isinstance(accident, dict) and accident.get("incident_id"):
        save_accident_incident({
            "incident_id": accident["incident_id"],
            "bus_id": bus_id,
            "camera_id": camera_id,
            "timestamp": accident.get("timestamp", timestamp),
            "camera_location": accident.get(
                "camera_location", f"{latitude}, {longitude}"
            ),
            "latitude": latitude,
            "longitude": longitude,
            "incident_type": accident.get("incident_type", "accident"),
            "vehicle_type": accident.get("vehicle_type", "unknown"),
            "registration_number": accident.get(
                "registration_number", "Unreadable"
            ),
            "plate_confidence": accident.get("plate_confidence"),
            "accident_image_path": accident.get("accident_image_path"),
            "vehicle_crop_path": accident.get("vehicle_crop_path"),
            "plate_crop_path": accident.get("plate_crop_path"),
            "accident_video_path": accident.get("accident_video_path"),
            "status": accident.get("status", "new"),
        })

    return jsonify({"accepted": True, "bus_id": bus_id, "camera_id": camera_id})


# ==========================================
# RUN FLASK SERVER
# ==========================================

def run_api():
    port = int(os.getenv("PORT", "5000"))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )


# Start API in background
api_thread = threading.Thread(
    target=run_api,
    daemon=True
)

api_thread.start()


# ==========================================
# LOAD YOLO MODELS
# ==========================================

project_path = Path(__file__).resolve().parent.parent
model = YOLO(str(project_path / "yolo11n.pt"))


def load_custom_model(label, filename, status_key):
    model_path = Path(__file__).resolve().parent / filename
    if not model_path.exists():
        ai_models[status_key] = "unavailable"
        print(f"{label} model: NOT FOUND ({model_path})")
        return None

    try:
        custom_model = YOLO(str(model_path))
    except Exception as error:
        ai_models[status_key] = "unavailable"
        print(f"{label} model: NOT FOUND (load failed: {error})")
        return None
    ai_models[status_key] = "connected"
    print(f"{label} model: LOADED")
    print(f"{label} model classes: {custom_model.names}")
    return custom_model


pothole_model = load_custom_model("Pothole", "pothole.pt", "pothole_model")
flood_model = load_custom_model("Flood", "flood.pt", "flood_model")
zebra_model = load_custom_model(
    "Zebra Crossing", "zebra_crossing.pt", "zebra_model"
)
accident_model = load_custom_model("Accident", "accident.pt", "accident_model")
plate_model = None
for plate_filename in ("number_plate.pt", "plate.pt"):
    if (Path(__file__).resolve().parent / plate_filename).exists():
        plate_model = load_custom_model(
            "Number plate", plate_filename, "plate_model"
        )
        break
if plate_model is None:
    print("Number plate model: NOT FOUND (expected number_plate.pt or plate.pt)")

print(f"Vehicle model: LOADED")
print(f"Vehicle classes: {model.names}")


def normalize_class_name(class_name):
    return " ".join(
        str(class_name).strip().lower().replace("_", " ").replace("-", " ").split()
    )


def model_class_names(custom_model):
    names = custom_model.names
    return names.values() if isinstance(names, dict) else names


def supported_class_names(custom_model, supported_names, label):
    if custom_model is None:
        return set()

    supported = {
        normalize_class_name(name)
        for name in model_class_names(custom_model)
        if normalize_class_name(name) in supported_names
    }
    if not supported:
        print(f"{label} model loaded but no supported class found.")
    return supported


pothole_classes = supported_class_names(
    pothole_model,
    {"pothole", "road defect", "road damage", "0"},
    "Pothole"
)
flood_classes = supported_class_names(
    flood_model,
    {"flood", "water", "waterlogging", "water logging", "flooded area"},
    "Flood"
)
zebra_classes = supported_class_names(
    zebra_model,
    {"zebra", "zebra crossing", "crosswalk"},
    "Zebra crossing"
)


def box_area(box):
    left, top, right, bottom = box
    return max(0, right - left) * max(0, bottom - top)


def box_iou(first_box, second_box):
    left = max(first_box[0], second_box[0])
    top = max(first_box[1], second_box[1])
    right = min(first_box[2], second_box[2])
    bottom = min(first_box[3], second_box[3])
    intersection = box_area((left, top, right, bottom))
    union = box_area(first_box) + box_area(second_box) - intersection
    return intersection / union if union else 0


def accident_vehicle_pair(vehicle_detections):
    global current_accident_overlap
    best_pair = None
    best_overlap = 0
    for index, first in enumerate(vehicle_detections):
        for second in vehicle_detections[index + 1:]:
            overlap = box_iou(first["coordinates"], second["coordinates"])
            if overlap > best_overlap:
                best_overlap = overlap
                if overlap >= 0.15:
                    best_pair = (first, second)
    current_accident_overlap = best_overlap
    return best_pair


def clamp_crop(frame, coordinates):
    height, width = frame.shape[:2]
    left, top, right, bottom = coordinates
    left = max(0, min(left, width - 1))
    top = max(0, min(top, height - 1))
    right = max(left + 1, min(right, width))
    bottom = max(top + 1, min(bottom, height))
    return frame[top:bottom, left:right]


def read_accident_plate(vehicle_crop):
    if plate_model is None or pytesseract is None or vehicle_crop.size == 0:
        print("ANPR result: Unreadable (plate model or OCR unavailable)")
        return "Unreadable", None, None

    try:
        plate_results = plate_model(vehicle_crop, conf=CONFIDENCE_THRESHOLD, verbose=False)
        best_box = None
        best_confidence = 0
        for result in plate_results:
            for box in result.boxes:
                confidence = float(box.conf[0])
                if confidence > best_confidence:
                    best_box = [int(value) for value in box.xyxy[0].tolist()]
                    best_confidence = confidence
        if best_box is None:
            print("ANPR result: Unreadable (no plate detected on accident vehicle)")
            return "Unreadable", None, None

        plate_crop = clamp_crop(vehicle_crop, best_box)
        gray_plate = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2GRAY)
        _, thresholded_plate = cv2.threshold(
            gray_plate, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        text = pytesseract.image_to_string(
            thresholded_plate,
            config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        )
        registration_number = " ".join(text.upper().split())
        if len(registration_number) < 4:
            registration_number = "Unreadable"
        print(
            f"ANPR result: {registration_number}, "
            f"plate confidence={best_confidence:.2f}"
        )
        return registration_number, plate_crop, best_confidence
    except Exception as error:
        print(f"Detailed ANPR error: {error}")
        return "Unreadable", None, None


def save_accident_video(frames, incident_id):
    evidence_path.mkdir(parents=True, exist_ok=True)
    video_name = f"{incident_id}.mp4"
    video_file = evidence_path / video_name
    if not frames:
        return None
    height, width = frames[0].shape[:2]
    writer = cv2.VideoWriter(
        str(video_file),
        cv2.VideoWriter_fourcc(*"mp4v"),
        ACCIDENT_VIDEO_FPS,
        (width, height),
    )
    if not writer.isOpened():
        print(f"Detailed accident video error: unable to open {video_file}")
        return None
    try:
        for buffered_frame in frames:
            writer.write(buffered_frame)
    finally:
        writer.release()
    print(f"Saved accident video path: {video_file}")
    return f"/accident-evidence/{video_name}"


def finalize_accident(pending):
    video_path = save_accident_video(pending["frames"], pending["incident_id"])
    incident = pending["incident"]
    incident["accident_video_path"] = video_path
    try:
        save_accident_incident(incident)
        live_data["accident_alert"] = incident
        print(f"Accident alert creation: {incident['incident_id']}")
    except Exception as error:
        print(f"Detailed accident report error: {error}")


def confirm_accident(frame, vehicle_detections, latitude, longitude):
    global accident_confirmation_count, current_accident_confidence
    global last_accident_time, incident_sequence
    pair = None
    accident_detected = False

    if accident_model is not None:
        accident_results = accident_model(frame, conf=CONFIDENCE_THRESHOLD, verbose=False)
        accident_confidences = []
        for result in accident_results:
            for box in result.boxes:
                class_id = int(box.cls[0])
                class_name = accident_model.names[class_id]
                if normalize_class_name(class_name) == "accident":
                    accident_confidences.append(float(box.conf[0]))

        current_accident_confidence = max(accident_confidences, default=0.0)
        print(
            f"Accident model detection: confidence={current_accident_confidence:.2f}"
        )
        accident_detected = current_accident_confidence >= CONFIDENCE_THRESHOLD
    else:
        pair = accident_vehicle_pair(vehicle_detections)
        accident_detected = pair is not None
        current_accident_confidence = 0.0

    accident_detection_history.append(1 if accident_detected else 0)
    accident_confirmation_count = sum(accident_detection_history)
    print(
        f"Accident confidence: {current_accident_confidence:.2f}\n"
        f"Accident history: {list(accident_detection_history)}\n"
        f"Accident confirmation: {accident_confirmation_count}/"
        f"{ACCIDENT_HISTORY_FRAMES}"
    )
    now = time.monotonic()
    if (
        accident_confirmation_count < ACCIDENT_CONFIRMATION_FRAMES
        or now - last_accident_time < ACCIDENT_COOLDOWN_SECONDS
    ):
        return None

    print("ACCIDENT CONFIRMED")
    accident_detection_history.clear()
    accident_confirmation_count = 0

    if pair is not None:
        selected_vehicle = max(pair, key=lambda item: box_area(item["coordinates"]))
    elif vehicle_detections:
        selected_vehicle = max(
            vehicle_detections,
            key=lambda item: box_area(item["coordinates"]),
        )
    else:
        selected_vehicle = {
            "class_name": "Accident",
            "coordinates": [0, 0, frame.shape[1], frame.shape[0]],
        }
    print(f"Selected accident evidence region: {selected_vehicle['class_name']}")
    incident_date = datetime.now().strftime("%Y%m%d")
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM accident_incidents WHERE incident_id LIKE ?",
            (f"ACC-{incident_date}-%",),
        ).fetchone()
    incident_sequence = max(incident_sequence, row[0]) + 1
    incident_id = f"ACC-{incident_date}-{incident_sequence:03d}"
    vehicle_crop = clamp_crop(frame, selected_vehicle["coordinates"])
    registration_number, plate_crop, plate_confidence = read_accident_plate(vehicle_crop)
    evidence_path.mkdir(parents=True, exist_ok=True)
    image_name = f"{incident_id}.jpg"
    vehicle_name = f"{incident_id}_vehicle.jpg"
    plate_name = f"{incident_id}_plate.jpg"
    cv2.imwrite(str(evidence_path / image_name), frame)
    cv2.imwrite(str(evidence_path / vehicle_name), vehicle_crop)
    if plate_crop is not None:
        cv2.imwrite(str(evidence_path / plate_name), plate_crop)
    incident = {
        "incident_id": incident_id,
        "bus_id": BUS_ID,
        "camera_id": CAMERA_ID,
        "timestamp": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
        "camera_location": f"{latitude}, {longitude}",
        "latitude": latitude,
        "longitude": longitude,
        "incident_type": "Accident",
        "vehicle_type": selected_vehicle["class_name"],
        "registration_number": registration_number,
        "plate_confidence": plate_confidence,
        "accident_image_path": f"/accident-evidence/{image_name}",
        "vehicle_crop_path": f"/accident-evidence/{vehicle_name}",
        "plate_crop_path": f"/accident-evidence/{plate_name}" if plate_crop is not None else None,
        "accident_video_path": None,
        "status": "New",
    }
    last_accident_time = now
    print(f"Incident ID created: {incident_id}")
    return {"incident_id": incident_id, "incident": incident, "frames": list(accident_frame_buffer)}


def custom_detections(results, custom_model, supported_classes):
    detections = []
    if custom_model is None or not supported_classes:
        return detections

    for result in results:
        for box in result.boxes:
            confidence = float(box.conf[0])
            class_id = int(box.cls[0])
            class_name = str(custom_model.names[class_id])
            normalized_name = normalize_class_name(class_name)
            if confidence < CONFIDENCE_THRESHOLD or normalized_name not in supported_classes:
                continue

            coordinates = [int(value) for value in box.xyxy[0].tolist()]
            detections.append({
                "class_name": class_name,
                "confidence": confidence,
                "coordinates": coordinates
            })
    return detections


def detection_signature(detection):
    left, top, right, bottom = detection["coordinates"]
    return (
        detection["class_name"],
        left // 40,
        top // 40,
        right // 40,
        bottom // 40
    )


def log_and_save_detection_events(model_label, detections, latitude, longitude):
    current_signatures = {detection_signature(item) for item in detections}
    if current_signatures != last_detection_log[model_label]:
        for item in detections:
            print(
                f"Detection: Model = {model_label}, "
                f"Class = {item['class_name']}, "
                f"Confidence = {item['confidence']:.2f}"
            )
        last_detection_log[model_label] = current_signatures

    now = time.monotonic()
    for item in detections:
        event_key = (model_label, detection_signature(item))
        if now - last_event_times.get(event_key, 0) < DETECTION_EVENT_COOLDOWN_SECONDS:
            continue
        save_detection_event(
            "Road Defect" if model_label == "Pothole" else model_label,
            item["confidence"],
            latitude,
            longitude
        )
        last_event_times[event_key] = now


# ==========================================
# OPEN WEBCAM
# ==========================================

cap = None if BROWSER_CAMERA_ONLY else cv2.VideoCapture(VIDEO_SOURCE)


print("==========================================")
print("UrbanEye AI started")
print("==========================================")
print(f"Vehicle model: LOADED")
print(f"Vehicle classes: {model.names}")
print(f"Pothole model: {'LOADED' if pothole_model else 'NOT FOUND'}")
print(f"Flood model: {'LOADED' if flood_model else 'NOT FOUND'}")
print(f"Zebra Crossing model: {'LOADED' if zebra_model else 'NOT FOUND'}")
print(f"Camera mode: {CAMERA_MODE}")
print(f"Video source: {'Browser webcam' if BROWSER_CAMERA_ONLY else VIDEO_SOURCE_LABEL}")
print(f"Demo test mode: {'ENABLED' if DEMO_TEST_MODE else 'DISABLED'}")
print(f"Accident model: {'LOADED' if accident_model else 'UNAVAILABLE (vehicle overlap fallback)'}")
print(f"Plate model: {'LOADED' if plate_model else 'UNAVAILABLE (ANPR returns Unreadable)'}")
print(
    "Camera/source: READY FOR BROWSER"
    if BROWSER_CAMERA_ONLY
    else f"Camera/source: {'CONNECTED' if cap.isOpened() else 'NOT CONNECTED'}"
)
print("Live API:")
print("http://127.0.0.1:5000/api/live-data")
print("Reports API:")
print("http://127.0.0.1:5000/api/reports")
print("Video:")
print("http://127.0.0.1:5000/video_feed")
print("Press Q to stop")
print("==========================================")


# ==========================================
# TEMPORARY GPS LOCATION
# ==========================================

latitude = 11.0168
longitude = 76.9558
update_bus_registry(
    BUS_ID,
    CAMERA_ID,
    datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
    latitude,
    longitude,
    {"vehicles": 0, "persons": 0},
)


# ==========================================
# WEBCAM DETECTION LOOP
# ==========================================

while True:

    # Read a browser frame when the online camera is active; otherwise use the
    # existing local webcam or configured MP4 source.
    if browser_camera_active:
        try:
            frame = browser_frame_queue.get(timeout=0.25)
            success = True
        except Empty:
            continue
    elif cap is not None:
        success, frame = cap.read()
    else:
        try:
            frame = browser_frame_queue.get(timeout=0.25)
            success = True
        except Empty:
            continue

    if not success:
        print("Unable to access local camera; waiting for browser camera frames")
        cap.release()
        cap = None
        continue

    accident_frame_buffer.append(frame.copy())


    # ==========================================
    # YOLO DETECTION
    # ==========================================

    results = model(frame, verbose=False)
    print("[YOLO TEST] Results received:", len(results))
    print("[YOLO TEST] Frame shape:", frame.shape)
    pothole_results = (
        pothole_model(frame, conf=CONFIDENCE_THRESHOLD, verbose=False)
        if pothole_model is not None
        else []
    )

    vehicle_count = 0
    person_count = 0
    pothole_count = 0
    flood_count = 0
    zebra_crossing_count = 0
    vehicle_detections = []


    # ==========================================
    # DETECT OBJECTS
    # ==========================================

    for result in results:

        for box in result.boxes:

            class_id = int(box.cls[0])

            class_name = model.names[class_id]


            # --------------------------------------
            # PERSON DETECTION
            # --------------------------------------

            if class_name == "person":

                person_count += 1


            # --------------------------------------
            # VEHICLE DETECTION
            # --------------------------------------

            if class_name in [

                "car",
                "motorcycle",
                "bus",
                "truck"

            ]:

                vehicle_count += 1
                vehicle_detections.append({
                    "class_name": class_name,
                    "confidence": float(box.conf[0]),
                    "coordinates": [int(value) for value in box.xyxy[0].tolist()],
                })

    pothole_detections = custom_detections(
        pothole_results, pothole_model, pothole_classes
    )
    pothole_count = len(pothole_detections)

    flood_results = (
        flood_model(frame, conf=CONFIDENCE_THRESHOLD, verbose=False)
        if flood_model is not None
        else []
    )
    flood_detections = custom_detections(
        flood_results, flood_model, flood_classes
    )
    flood_count = len(flood_detections)

    zebra_results = (
        zebra_model(frame, conf=CONFIDENCE_THRESHOLD, verbose=False)
        if zebra_model is not None
        else []
    )
    zebra_detections = custom_detections(
        zebra_results, zebra_model, zebra_classes
    )
    zebra_crossing_count = len(zebra_detections)

    log_and_save_detection_events(
        "Pothole", pothole_detections, latitude, longitude
    )
    log_and_save_detection_events(
        "Flood", flood_detections, latitude, longitude
    )
    log_and_save_detection_events(
        "Zebra", zebra_detections, latitude, longitude
    )

    accident_candidate = confirm_accident(
        frame, vehicle_detections, latitude, longitude
    )
    if accident_candidate is not None and pending_accident is None:
        pending_accident = accident_candidate
        pending_accident["remaining_frames"] = ACCIDENT_AFTER_FRAMES
        print(
            f"Accident confirmed: collecting {ACCIDENT_AFTER_FRAMES} "
            "post-incident frames"
        )
    elif pending_accident is not None:
        pending_accident["frames"].append(frame.copy())
        pending_accident["remaining_frames"] -= 1
        if pending_accident["remaining_frames"] <= 0:
            finalize_accident(pending_accident)
            pending_accident = None


    # ==========================================
    # TRAFFIC STATUS
    # ==========================================

    if vehicle_count <= 2:

        traffic_status = "LOW"

    elif vehicle_count <= 5:

        traffic_status = "MEDIUM"

    else:

        traffic_status = "HIGH"


    # ==========================================
    # ACTIVE ALERTS
    # ==========================================

    active_alerts = 0
    current_alerts = set()


    # Heavy traffic alert
    if vehicle_count >= 6:

        active_alerts += 1
        current_alerts.add("heavy_traffic")


    # Pedestrian + vehicle risk alert
    if person_count >= 1 and vehicle_count >= 4:

        active_alerts += 1
        current_alerts.add("pedestrian_vehicle_risk")

    if pothole_count > 0:
        active_alerts += 1
        current_alerts.add("pothole")
    if flood_count > 0:
        active_alerts += 1
        current_alerts.add("flood")
    if pending_accident is not None or live_data["accident_alert"] is not None:
        active_alerts += 1
        current_alerts.add("accident")

    for alert_type in current_alerts - last_active_alerts:
        save_alert(
            alert_type,
            "high" if alert_type in {"accident", "pothole", "flood"} else "medium",
            "open",
            latitude,
            longitude,
        )
    last_active_alerts = current_alerts


    # ==========================================
    # ROAD DEFECTS
    # ==========================================

    road_defects = pothole_count


    # ==========================================
    # UPDATE LIVE DASHBOARD DATA
    # ==========================================

    live_data["persons"] = person_count

    live_data["vehicles"] = vehicle_count

    live_data["traffic_status"] = traffic_status

    live_data["road_defects"] = road_defects
    live_data["potholes"] = pothole_count
    live_data["floods"] = flood_count
    live_data["zebra_crossings"] = zebra_crossing_count

    live_data["active_alerts"] = active_alerts
    live_data["accident_detected"] = (
        pending_accident is not None or live_data["accident_alert"] is not None
    )

    live_data["latitude"] = latitude

    live_data["longitude"] = longitude
    live_data["accident_diagnostics"] = {
        "overlap_threshold": 0.15,
        "current_overlap": round(current_accident_overlap, 3),
        "accident_confidence": round(current_accident_confidence, 3),
        "accident_history": list(accident_detection_history),
        "history_window": ACCIDENT_HISTORY_FRAMES,
        "confirmation_frames": accident_confirmation_count,
        "required_confirmation_frames": ACCIDENT_CONFIRMATION_FRAMES,
    }
    live_data["bus_id"] = BUS_ID
    live_data["camera_id"] = CAMERA_ID
    update_bus_registry(
        BUS_ID,
        CAMERA_ID,
        datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
        latitude,
        longitude,
        {
            "vehicles": vehicle_count,
            "persons": person_count,
            "traffic_status": traffic_status,
            "active_alerts": active_alerts,
        },
    )


    # ==========================================
    # SAVE REPORT WHEN STATUS CHANGES
    # ==========================================

    if (
        len(report_history) == 0
        or report_history[-1]["traffic_status"] != traffic_status
        or report_history[-1]["road_defects"] != road_defects
        or report_history[-1].get("vehicles", 0) != vehicle_count
        or report_history[-1].get("persons", 0) != person_count
        or report_history[-1].get("potholes", 0) != pothole_count
        or report_history[-1].get("floods", 0) != flood_count
        or report_history[-1].get("zebra_crossings", 0) != zebra_crossing_count
        or report_history[-1].get("active_alerts", 0) != active_alerts
        or report_history[-1].get("floods", 0) != flood_count
        or report_history[-1].get("zebra_crossings", 0) != zebra_crossing_count
    ):

        report = {

            "bus_id": BUS_ID,

            "camera_id": CAMERA_ID,

            "timestamp": datetime.now().strftime(
                "%d-%m-%Y %H:%M:%S"
            ),

            "vehicles": vehicle_count,

            "persons": person_count,

            "traffic_status": traffic_status,

            "road_defects": road_defects,
            "potholes": pothole_count,
            "floods": flood_count,
            "zebra_crossings": zebra_crossing_count,

            "active_alerts": active_alerts,

            "latitude": latitude,

            "longitude": longitude

        }


        # Add report
        report_history.append(report)
        save_report(report)


        # Keep only latest 50 reports
        if len(report_history) > 50:

            report_history.pop(0)


        print("Report saved:", report)


    # ==========================================
    # DRAW YOLO DETECTION BOXES
    # ==========================================

    annotated_frame = results[0].plot()
    if pothole_results:
        annotated_frame = pothole_results[0].plot(img=annotated_frame)
    if flood_results:
        annotated_frame = flood_results[0].plot(img=annotated_frame)
    if zebra_results:
        annotated_frame = zebra_results[0].plot(img=annotated_frame)

    # ==========================================
    # DISPLAY INFORMATION
    # ==========================================

    cv2.putText(
        annotated_frame,
        f"Persons: {person_count}",
        (20, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (0, 255, 0),
        2
    )

    cv2.putText(
        annotated_frame,
        f"Vehicles: {vehicle_count}",
        (20, 80),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (0, 255, 0),
        2
    )


    cv2.putText(
        annotated_frame,
        f"Traffic: {traffic_status}",
        (20, 120),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (0, 255, 0),
        2
    )


    cv2.putText(
        annotated_frame,
        f"Alerts: {active_alerts}",
        (20, 160),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (0, 255, 0),
        2
    )

    cv2.putText(
        annotated_frame,
        f"Potholes: {pothole_count}",
        (20, 200),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (0, 165, 255),
        2
    )
    cv2.putText(
        annotated_frame,
        f"Floods: {flood_count}  Zebra crossings: {zebra_crossing_count}",
        (20, 240),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 165, 0),
        2
    )

    with latest_frame_lock:
        latest_frame = annotated_frame.copy()

    if browser_camera_active:
        print(
            f"[AI FRAME] Processing frame | Vehicles: {vehicle_count} | "
            f"Potholes: {pothole_count} | Water: {flood_count}"
        )
    # ==========================================
    # SHOW WEBCAM - LOCAL ONLY
    # ==========================================

    if not BROWSER_CAMERA_ONLY:
        cv2.imshow(
            "UrbanEye AI - Live Detection",
            annotated_frame
        )

        # ==========================================
        # PRESS Q TO STOP
        # ==========================================

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
# ==========================================
# CLEANUP
# ==========================================

if cap is not None:
    cap.release()

cv2.destroyAllWindows()

print("UrbanEye AI stopped.")