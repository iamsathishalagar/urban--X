"""Optional metadata-only BUS-002/BUS-003 demonstration client."""

import argparse
import json
import os
import time
from datetime import datetime
from urllib.request import Request, urlopen


SIMULATED_BUSES = (
    ("BUS-002", "CAMERA-002", 11.0220, 76.9600),
    ("BUS-003", "CAMERA-003", 11.0300, 76.9700),
)


def send_metadata(endpoint, token, bus_id, camera_id, latitude, longitude):
    payload = {
        "bus_id": bus_id,
        "camera_id": camera_id,
        "timestamp": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
        "latitude": latitude,
        "longitude": longitude,
        "telemetry": {
            "vehicles": 4,
            "persons": 1,
            "traffic_status": "medium",
            "active_alerts": 0,
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-API-Key": token,
        },
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return response.read().decode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:5000/api/ingest",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("SMART_ROAD_INGESTION_TOKEN", ""),
    )
    parser.add_argument("--interval", type=float, default=15)
    args = parser.parse_args()
    if not args.token:
        parser.error("provide --token or SMART_ROAD_INGESTION_TOKEN")

    while True:
        for bus_id, camera_id, latitude, longitude in SIMULATED_BUSES:
            print(send_metadata(
                args.endpoint, args.token, bus_id, camera_id,
                latitude, longitude,
            ))
        if args.interval <= 0:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
