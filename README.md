# Smart Road Safety & Hazard Detection System

## Overview

This project is an AI-powered road monitoring system that uses a live webcam feed to identify road hazards and traffic-safety elements in real time. It helps authorities and road users notice issues early, supporting safer roads and faster maintenance decisions.

The system currently monitors:

- Potholes
- Waterlogging / flood-prone road areas
- Zebra crossings
- Vehicles
- Road accidents / collision events
- Vehicle registration-number plates

Detected information is shown on a dashboard and can be stored for reports and future analysis.

## Problem Statement

Road hazards such as potholes and waterlogging can cause accidents, traffic delays, and vehicle damage. When an accident occurs, identifying the involved vehicle and its registration number quickly is important for emergency response and incident reporting. Manual road inspection is time-consuming and may not provide timely information. Zebra crossings also need to be clearly identified to improve pedestrian safety.

This project provides a camera-based, automated solution to detect these conditions continuously.

## Proposed Solution

The application captures frames from a webcam and sends them through trained object-detection models. Each frame is analysed for potholes, flood/water areas, zebra crossings, vehicles, and possible accidents. When a vehicle number plate is visible, an Automatic Number Plate Recognition (ANPR) module can extract its registration number. The detected objects and incident details are displayed in the live stream and shared with the dashboard.

## Key Features

- Real-time webcam monitoring
- AI detection of potholes, flood/waterlogging, and zebra crossings
- Vehicle detection for traffic context
- Accident / collision detection for immediate incident awareness
- Vehicle registration-number plate detection and text recognition (ANPR)
- Bounding boxes, labels, and confidence values on the video stream
- Flask-based application APIs and dashboard polling
- Database support for recorded detections
- Reports for reviewing detected road issues
- Separate model files for each road-safety feature

## System Workflow

1. The webcam captures a live road image/video frame.
2. The system runs the frame through the detection models.
3. Pothole, flood, zebra crossing, vehicle, and accident detections are extracted.
4. For an accident or selected vehicle, the system detects the number plate and reads its text through ANPR.
5. The live stream is annotated with detection labels, registration number (when readable), and confidence scores.
6. Detection data is made available to the dashboard.
7. Important records can be saved to the database and used in reports.

## Technologies Used

- Python
- Flask
- OpenCV
- Ultralytics YOLO for object detection
- PyTorch-based YOLOv5 compatibility for the zebra-crossing model, when required
- Automatic Number Plate Recognition (ANPR) using number-plate detection and OCR
- HTML, CSS, and JavaScript for the dashboard
- Database integration for detection history and reports

## AI Models

| Detection | Model file | Purpose |
|---|---|---|
| Pothole | `pothole.pt` | Detects road potholes |
| Flood / Waterlogging | `flood.pt` | Detects waterlogged or flood-affected road areas |
| Zebra Crossing | `zebra_crossing.pt` | Detects zebra crossings / crosswalks |
| Vehicle | YOLO vehicle model | Detects vehicles in the monitored scene |
| Accident | Accident-detection model | Identifies possible road accidents or collisions |
| Registration Number | Number-plate detector + OCR | Detects a vehicle plate and reads its text |

> The pothole and flood models operate independently. Zebra-crossing support is handled separately because its checkpoint may use the older YOLOv5 format.

## Project Structure

```text
project-folder/
├── ai/
│   ├── webcam_detection.py     # Live detection and Flask server
│   ├── pothole.pt              # Pothole detection model
│   ├── flood.pt                # Flood/waterlogging detection model
│   └── zebra_crossing.pt       # Zebra-crossing detection model
├── templates/                  # Dashboard pages
├── static/                     # CSS, JavaScript, images
├── database/                   # Detection records (if configured)
└── reports/                    # Generated reports (if configured)
```

## How to Run

1. Install the required Python packages for Flask, OpenCV, PyTorch, and Ultralytics.
2. Keep `pothole.pt`, `flood.pt`, and `zebra_crossing.pt` in the same folder as `webcam_detection.py`.
3. Start the application from the project folder:

```bash
python ai/webcam_detection.py
```

4. Open the dashboard URL displayed in the terminal.

### Multi-bus operation

The local webcam remains `BUS-001` with `CAMERA-001` by default. A device can
use stable identities without changing the detection models:

```bat
set SMART_ROAD_BUS_ID=BUS-001
set SMART_ROAD_CAMERA_ID=CAMERA-001
set SMART_ROAD_INGESTION_TOKEN=change-this-token
python ai/webcam_detection.py
```

Edge devices keep inference local. Central ingestion accepts detection
metadata, reports, alerts, and confirmed accident evidence metadata through
`POST /api/ingest` using the `X-API-Key` header. Normal video is not uploaded;
the existing `/video_feed` remains local to the webcam process.

For a demonstration without additional cameras, start the backend with an
ingestion token and run:

```bat
python ai/simulated_buses.py --token change-this-token --interval 15
```

This sends lightweight fleet telemetry for `BUS-002` and `BUS-003` only.
The default interval is 15 seconds and can be changed with `--interval`.
Reports, detection events, incidents, alerts, and dashboard data can be
filtered with the `bus_id` query parameter; omitting it shows all buses.
Existing database rows are preserved and receive `BUS-001` / `CAMERA-001`
defaults through additive startup migrations.

### Demo modes

For the smooth `BUS-001` live camera and AI demonstration, run only:

```bat
set SMART_ROAD_INGESTION_TOKEN=change-this-token
python ai/webcam_detection.py
```

### Local MP4 accident test

Use a clear local MP4 when testing the vehicle-overlap fallback. This keeps
the existing production decision logic unchanged; it does not create a
synthetic accident or lower the IoU threshold.

```bat
set "SMART_ROAD_DEMO_TEST_MODE=1"
set "SMART_ROAD_VIDEO_SOURCE=D:\videos\accident-test.mp4"
python ai\webcam_detection.py
```

The dashboard diagnostics panel and console show the active source, whether
`accident.pt` and a plate model are loaded, the current vehicle IoU, and the
confirmation progress. The fallback requires two real vehicle detections with
IoU of at least `0.15` for `3` consecutive frames. The MP4 process stops when
the file reaches its end. To use the default webcam again, clear the source:

```bat
set "SMART_ROAD_DEMO_TEST_MODE=0"
set "SMART_ROAD_VIDEO_SOURCE=0"
python ai\webcam_detection.py
```

For full accident-model detection, place `accident.pt` in `ai\`. For
accident-only ANPR, place either `number_plate.pt` or `plate.pt` in `ai\` and
install/configure `pytesseract` with the Tesseract OCR executable. Without
those files, the dashboard explicitly reports the overlap fallback and
`Unreadable` ANPR while preserving the privacy rule.

For the GIS scalability demonstration with the live camera and two simulated
fleet markers, use two terminals:

```bat
set SMART_ROAD_INGESTION_TOKEN=change-this-token
python ai/webcam_detection.py
```

```bat
python ai/simulated_buses.py --token change-this-token --interval 15
```

## Expected Output

- A live camera stream with detected objects highlighted.
- Labels such as **Pothole**, **Flood**, **Zebra Crossing**, **Vehicle**, and **Accident**.
- Vehicle registration numbers when the number plate is clear enough to read.
- Dashboard updates showing detection status, counts, and accident alerts.
- Stored detection, accident, and vehicle-registration records when database/report features are enabled.

## Benefits

- Reduces dependence on manual road inspection
- Supports early identification of dangerous road conditions
- Improves pedestrian safety through zebra-crossing detection
- Enables faster accident notification and response
- Helps identify vehicles involved in an incident through registration-number recognition
- Helps authorities prioritise maintenance work
- Provides useful visual evidence and detection history

## Future Enhancements

- GPS location tagging for every detected hazard
- Automatic alert messages to municipal authorities
- Emergency alerts with accident image, time, location, and detected registration number
- Severity scoring based on pothole size and water coverage
- Cloud deployment with multiple roadside cameras
- Mobile application for citizens and field officers
- Analytics dashboard with maps and trend charts

## Conclusion

The Smart Road Safety & Hazard Detection System demonstrates how computer vision can improve road monitoring. By combining live video, AI detection, dashboard updates, and reporting, it offers a practical foundation for smarter and safer road infrastructure.
