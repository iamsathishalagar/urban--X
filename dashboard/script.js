// ==========================================
// National Road Safety Portal
// ==========================================

const languageSelector = document.getElementById("languageSelector");

function applyPortalLanguage(language) {
    const selectedLanguage = PORTAL_TRANSLATIONS[language] ? language : "en";
    document.documentElement.lang = selectedLanguage;
    document.querySelectorAll("[data-i18n]").forEach(function (element) {
        element.textContent = getPortalTranslation(selectedLanguage, element.dataset.i18n);
    });
    if (languageSelector) {
        languageSelector.value = selectedLanguage;
    }
    window.localStorage.setItem("portal-language", selectedLanguage);
}

if (languageSelector) {
    PORTAL_LANGUAGE_OPTIONS.forEach(function ([value, label]) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        languageSelector.appendChild(option);
    });
    languageSelector.addEventListener("change", function (event) {
        applyPortalLanguage(event.target.value);
        updateLiveData();
        loadReports();
    });
}

applyPortalLanguage(window.localStorage.getItem("portal-language") || "en");

const splashScreen = document.getElementById("splashScreen");

if (splashScreen) {
    window.setTimeout(function () {
        splashScreen.classList.add("is-hidden");
        window.setTimeout(function () {
            splashScreen.remove();
        }, 500);
    }, 5000);
}

// Create map and set Coimbatore as default location
const map = L.map("map").setView(
    [11.0168, 76.9558],
    13
);

// Add OpenStreetMap
L.tileLayer(
    "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    {
        maxZoom: 19,
        attribution: "© OpenStreetMap contributors"
    }
).addTo(map);
const busFilter = document.getElementById("busFilter");
const busMarkers = new Map();
let selectedBusId = "";
const markerState = new Map();
const API_BASE_URL = window.URBANEYE_API_BASE_URL ?? "http://127.0.0.1:5000";

function apiUrl(path) {
    const url = new URL(path, API_BASE_URL || window.location.origin);
    if (selectedBusId) {
        url.searchParams.set("bus_id", selectedBusId);
    }
    return url.toString();
}

function updateBusMarkers(buses) {
    const visibleBuses = buses || {};
    Object.entries(visibleBuses).forEach(function ([busId, bus]) {
        if (typeof bus.latitude !== "number" || typeof bus.longitude !== "number") {
            return;
        }
        let marker = busMarkers.get(busId);
        if (!marker) {
            marker = L.marker([bus.latitude, bus.longitude]).addTo(map);
            busMarkers.set(busId, marker);
            markerState.set(busId, {});
        }
        const state = markerState.get(busId);
        if (state.latitude !== bus.latitude || state.longitude !== bus.longitude) {
            marker.setLatLng([bus.latitude, bus.longitude]);
        }
        if (
            state.cameraId !== bus.camera_id ||
            state.latitude !== bus.latitude ||
            state.longitude !== bus.longitude
        ) {
            marker.bindPopup(
                `<b>${busId}</b><br><span>${bus.camera_id ?? "-"}</span><br>` +
                `<span>${bus.latitude.toFixed(5)}, ${bus.longitude.toFixed(5)}</span>`
            );
        }
        markerState.set(busId, {
            cameraId: bus.camera_id,
            latitude: bus.latitude,
            longitude: bus.longitude,
        });
    });

    busMarkers.forEach(function (marker, busId) {
        if (selectedBusId && busId !== selectedBusId) {
            marker.removeFrom(map);
        } else if (visibleBuses[busId] && !map.hasLayer(marker)) {
            marker.addTo(map);
        }
    });
}

if (busFilter) {
    busFilter.addEventListener("change", function (event) {
        selectedBusId = event.target.value;
        updateLiveData();
        loadReports();
    });
}

const trafficChart = document.getElementById("trafficChart");
const trafficHistory = [];
let liveUpdateInProgress = false;
let historyUpdateInProgress = false;
let lastReportsSignature = "";
let accidentUpdateInProgress = false;
let lastAccidentIncidentsSignature = "";
let latestProcessedIncidentId = null;

function updateDateLabel() {
    const currentDate = document.getElementById("currentDate");
    if (currentDate) {
        currentDate.textContent = new Date().toLocaleDateString([], {
            day: "2-digit",
            month: "short",
            year: "numeric"
        });
    }
}

updateDateLabel();

const aiCameraFeed = document.getElementById("aiCameraFeed");
const cameraModal = document.getElementById("cameraModal");
const cameraStream = document.getElementById("cameraStream");
const cameraStreamFallback = document.getElementById("cameraStreamFallback");
const closeCameraModal = document.getElementById("closeCameraModal");
const cameraModalStatus = document.getElementById("cameraModalStatus");
const cameraConnectionState = document.getElementById("cameraConnectionState");

function setCameraState(status, message, showFallback) {
    if (cameraModalStatus) {
        cameraModalStatus.textContent = status;
    }
    if (cameraConnectionState) {
        cameraConnectionState.textContent = message;
    }
    if (cameraStreamFallback) {
        cameraStreamFallback.hidden = !showFallback;
    }
}

function openCameraModal() {
    if (!cameraModal || !cameraStream) {
        return;
    }

    cameraModal.classList.add("is-open");
    cameraModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("camera-modal-open");
    setCameraState("CONNECTING", "Connecting", false);
    cameraStream.src = apiUrl("/video_feed");
    closeCameraModal?.focus();
}

function closeCameraViewer() {
    if (!cameraModal || !cameraStream) {
        return;
    }

    cameraStream.removeAttribute("src");
    cameraModal.classList.remove("is-open");
    cameraModal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("camera-modal-open");
    setCameraState("OFFLINE", "Closed", false);
    console.log("[CAMERA] Camera stopped");
    aiCameraFeed?.focus();
}

aiCameraFeed?.addEventListener("click", openCameraModal);
aiCameraFeed?.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openCameraModal();
    }
});
closeCameraModal?.addEventListener("click", closeCameraViewer);
cameraModal?.addEventListener("click", function (event) {
    if (event.target.matches("[data-camera-close]")) {
        closeCameraViewer();
    }
});
cameraStream?.addEventListener("load", function () {
    setCameraState("CONNECTED", "AI Camera Connected", false);
});
cameraStream?.addEventListener("error", function () {
    setCameraState("OFFLINE", "AI Camera Unavailable", true);
});
document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && cameraModal?.classList.contains("is-open")) {
        closeCameraViewer();
    }
});

function drawTrafficChart() {
    if (!trafficChart) {
        return;
    }

    const context = trafficChart.getContext("2d");
    const width = trafficChart.clientWidth;
    const height = trafficChart.clientHeight;
    const devicePixelRatio = window.devicePixelRatio || 1;

    trafficChart.width = width * devicePixelRatio;
    trafficChart.height = height * devicePixelRatio;
    context.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    context.clearRect(0, 0, width, height);

    context.strokeStyle = "#e5e7eb";
    context.lineWidth = 1;
    for (let index = 0; index <= 4; index += 1) {
        const y = 16 + ((height - 36) * index) / 4;
        context.beginPath();
        context.moveTo(42, y);
        context.lineTo(width - 16, y);
        context.stroke();
    }

    if (trafficHistory.length < 2) {
        context.fillStyle = "#667085";
        context.font = "14px Inter, sans-serif";
        context.fillText("Waiting for live detection data...", 52, height / 2);
        return;
    }

    const maximum = Math.max(
        1,
        ...trafficHistory.flatMap((entry) => [entry.vehicles, entry.persons])
    );
    const plotWidth = width - 58;
    const plotHeight = height - 52;

    function drawLine(valueKey, color) {
        context.strokeStyle = color;
        context.lineWidth = 3;
        context.beginPath();

        trafficHistory.forEach((entry, index) => {
            const x = 42 + (plotWidth * index) / (trafficHistory.length - 1);
            const y = 16 + plotHeight - (plotHeight * entry[valueKey]) / maximum;
            if (index === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        });

        context.stroke();
    }

    drawLine("vehicles", "#2563eb");
    drawLine("persons", "#16a34a");
}

// ==========================================
// POTHOLE EVENT
// ==========================================

L.marker([11.0220, 76.9600])
    .addTo(map)
    .bindPopup(`
        <b>🚌 Bus 02</b><br>
        <b>🕳️ Pothole Detected</b><br>
        Severity: HIGH
    `);


// ==========================================
// HEAVY TRAFFIC EVENT
// ==========================================

L.marker([11.0100, 76.9480])
    .addTo(map)
    .bindPopup(`
        <b>🚌 Bus 03</b><br>
        <b>🚗 Heavy Traffic</b><br>
        Traffic Density: HIGH
    `);

L.marker([11.0122, 76.9856]) // Hicas
    .addTo(map)
    .bindPopup(`
        <b>🚌 Bus 05</b><br>
        <b>🚗 Heavy Traffic</b><br>
        Traffic Density: HIGH
    `);


// ==========================================
// WATERLOGGING EVENT
// ==========================================

L.marker([11.0300, 76.9700])
    .addTo(map)
    .bindPopup(`
        <b>🚌 Bus 04</b><br>
        <b>🌊 Waterlogging</b><br>
        Severity: HIGH
    `);

    // ==========================================
// LIVE AI DATA FROM PYTHON
// ==========================================

async function updateLiveData() {
    if (liveUpdateInProgress) {
        return;
    }

    liveUpdateInProgress = true;
    try {
        const response = await fetch(apiUrl("/api/live-data"), { cache: "no-store" });

        if (!response.ok) {
            throw new Error("Live data API unavailable");
        }

        const data = await response.json();
        updateBusMarkers(data.buses || (data.bus_id ? { [data.bus_id]: data } : {}));

        const vehicleElement = document.getElementById("vehicle");
        const trafficStatusElement = document.getElementById("trafficStatus");
        const alertsElement = document.getElementById("alerts");
        const roadDefectsElement = document.getElementById("roadDefects");
        const accidentDetectionElement = document.getElementById("accidentDetection");
        const accidentDetectionStatus = document.getElementById("accidentDetectionStatus");

        if (vehicleElement) {
            vehicleElement.textContent = data.vehicles ?? 0;
        }
        if (trafficStatusElement) {
            trafficStatusElement.textContent = t(
                `status.${String(data.traffic_status ?? "low").toLowerCase()}`
            );
        }
        if (alertsElement) {
            alertsElement.textContent = data.active_alerts ?? 0;
        }
        updateNotificationBadge(data.active_alerts);
        if (accidentDetectionElement) {
            accidentDetectionElement.textContent = t(
                data.accident_detected ? "status.detected" : "status.clear"
            );
        }
        if (accidentDetectionStatus) {
            accidentDetectionStatus.innerHTML = data.accident_detected
            ? `<i class="fa-solid fa-circle"></i> ${t("label.confirmedAccidentAlert")}`
            : `<i class="fa-solid fa-circle"></i> ${t("noConfirmedAccident")}`;
        }
        updateAccidentAlertIfNew(data.accident_alert);
        if (roadDefectsElement) {
            roadDefectsElement.textContent = data.road_defects ?? 0;
        }

        const floodsElement = document.getElementById("floods");
        const zebraCrossingsElement = document.getElementById("zebraCrossings");
        if (floodsElement) {
            floodsElement.textContent = data.floods ?? 0;
        }
        if (zebraCrossingsElement) {
            zebraCrossingsElement.textContent = data.zebra_crossings ?? 0;
        }

        const floodStatus = document.getElementById("floodStatus");
        const zebraStatus = document.getElementById("zebraStatus");
        const modelStatus = data.ai_models ?? {};
        if (floodStatus) {
            floodStatus.innerHTML = `<i class="fa-solid fa-circle"></i> ${modelStatus.flood_model === "connected" ? t("liveDetection") : t("label.modelUnavailable")}`;
        }
        if (zebraStatus) {
            zebraStatus.innerHTML = `<i class="fa-solid fa-circle"></i> ${modelStatus.zebra_model === "connected" ? t("liveDetection") : t("label.modelUnavailable")}`;
        }
        updateAccidentDiagnostics(data);

        const cameraVehicleCount = document.getElementById("cameraVehicleCount");
        const cameraPersonCount = document.getElementById("cameraPersonCount");
        if (cameraVehicleCount) {
            cameraVehicleCount.textContent = data.vehicles ?? 0;
        }
        if (cameraPersonCount) {
            cameraPersonCount.textContent = data.persons ?? 0;
        }

        const syncTime = new Date().toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit"
        });
        const currentDate = document.getElementById("currentDate");
        const lastSync = document.getElementById("lastSync");
        const lastSyncMain = document.getElementById("lastSyncMain");
        if (currentDate) {
            currentDate.textContent = new Date().toLocaleDateString([], {
                day: "2-digit",
                month: "short",
                year: "numeric"
            });
        }
        if (lastSync) {
            lastSync.textContent = syncTime;
        }
        if (lastSyncMain) {
            lastSyncMain.textContent = syncTime;
        }

        trafficHistory.push({
            vehicles: Number(data.vehicles) || 0,
            persons: Number(data.persons) || 0
        });
        if (trafficHistory.length > 20) {
            trafficHistory.shift();
        }
        drawTrafficChart();
        console.log("Live AI Data:", data);

    } catch (error) {
        console.error("Live data update failed:", error);
    } finally {
        liveUpdateInProgress = false;
    }
}

function updateAccidentDiagnostics(data) {
    const diagnostics = data.accident_diagnostics || {};
    const modelStatus = data.ai_models || {};
    const demoModeStatus = document.getElementById("demoModeStatus");
    const videoSource = document.getElementById("diagnosticVideoSource");
    const accidentModel = document.getElementById("diagnosticAccidentModel");
    const plateModel = document.getElementById("diagnosticPlateModel");
    const overlap = document.getElementById("diagnosticOverlap");
    const confirmation = document.getElementById("diagnosticConfirmation");

    if (demoModeStatus) {
        demoModeStatus.textContent = data.demo_test_mode ? "DEMO TEST MODE" : "PRODUCTION MODE";
    }
    if (videoSource) {
        videoSource.textContent = data.video_source || "Unknown";
    }
    if (accidentModel) {
        accidentModel.textContent = modelStatus.accident_model === "connected" ? "Loaded" : "Unavailable / overlap fallback";
    }
    if (plateModel) {
        plateModel.textContent = modelStatus.plate_model === "connected" ? "Loaded" : "Unavailable / Unreadable";
    }
    if (overlap) {
        overlap.textContent = `${Number(diagnostics.current_overlap || 0).toFixed(3)} / ${Number(diagnostics.overlap_threshold || 0.15).toFixed(3)} required`;
    }
    if (confirmation) {
        confirmation.textContent = `${diagnostics.confirmation_frames || 0} / ${diagnostics.required_confirmation_frames || 3}`;
    }
}

// Start one background polling loop without replacing or reloading the page.
updateLiveData();
window.setInterval(updateLiveData, 2000);
window.addEventListener("resize", drawTrafficChart);

// Load saved history independently; this updates table rows only.
loadReports();
setInterval(loadReports, 10000);
loadAccidentIncidents();
setInterval(loadAccidentIncidents, 2000);
// ==========================================
// REPORTS PAGE NAVIGATION
// ==========================================

const reportsBtn = document.getElementById("reportsBtn");
const reportsPage = document.getElementById("reports");
const backDashboard = document.getElementById("backDashboard");

const dashboardContent = document.querySelectorAll(".dashboard-content");

document.querySelectorAll(".sidebar nav a").forEach(function (link) {
    link.addEventListener("click", function (event) {
        event.preventDefault();

        document.querySelectorAll(".sidebar nav a").forEach(function (navLink) {
            navLink.classList.remove("active");
        });
        link.classList.add("active");

        const targetId = link.getAttribute("href").slice(1);
        const target = document.getElementById(targetId);

        if (targetId === "reports") {
            dashboardContent.forEach(function (element) {
                element.style.display = "none";
            });
            reportsPage.style.display = "block";
            loadReports();
            return;
        }

        reportsPage.style.display = "none";
        dashboardContent.forEach(function (element) {
            element.style.display = "";
        });

        if (target && targetId !== "dashboard") {
            target.scrollIntoView({ behavior: "smooth", block: "start" });
        } else {
            window.scrollTo({ top: 0, behavior: "smooth" });
        }
    });
});

// Back to Dashboard
backDashboard.addEventListener("click", function () {

    document.querySelectorAll(".sidebar nav a").forEach(function (link) {
        link.classList.toggle("active", link.getAttribute("href") === "#dashboard");
    });

    reportsPage.style.display = "none";

    dashboardContent.forEach(function (element) {
        element.style.display = "";
    });

});
// ==========================================
// LOAD SAVED REPORT HISTORY
// ==========================================

async function loadReports() {
    if (historyUpdateInProgress) {
        return;
    }

    historyUpdateInProgress = true;

    try {
        const response = await fetch(apiUrl("/api/reports"), { cache: "no-store" });

        if (!response.ok) {
            throw new Error("Reports API unavailable");
        }

        const reports = await response.json();

        const reportTableBody = document.getElementById("reportsTableBody");
        const dashboardTableBody = document.getElementById("dashboardHistoryTableBody");
        const reportCount = document.getElementById("reportCount");
        const dashboardHistoryCount = document.getElementById("dashboardHistoryCount");

        if (!Array.isArray(reports)) {
            return;
        }

        const reportsSignature = JSON.stringify(reports);
        if (reportsSignature === lastReportsSignature) {
            return;
        }
        lastReportsSignature = reportsSignature;

        const latestReports = [...reports].reverse();

        function renderTableRows(targetBody, rows) {
            if (!targetBody) {
                return;
            }

            targetBody.innerHTML = "";

            if (rows.length === 0) {
                targetBody.innerHTML = `
                    <tr>
                        <td colspan="13">No reports saved yet.</td>
                    </tr>
                `;
                return;
            }

            rows.forEach((report) => {
                const row = document.createElement("tr");

                const traffic = report.traffic_status || "LOW";
                const trafficClass = traffic.toLowerCase().replace(/\s+/g, "-");

                row.innerHTML = `
                    <td>${report.bus_id ?? "BUS-001"}</td>
                    <td>${report.camera_id ?? "CAMERA-001"}</td>
                    <td>🕒 ${report.timestamp ?? "-"}</td>
                    <td>${report.vehicles ?? 0}</td>
                    <td>${report.persons ?? 0}</td>
                    <td>
                        <span class="traffic-badge ${trafficClass}">
                            ${traffic}
                        </span>
                    </td>
                    <td>${report.road_defects ?? 0}</td>
                    <td>${report.potholes ?? report.road_defects ?? 0}</td>
                    <td>${report.floods ?? 0}</td>
                    <td>${report.zebra_crossings ?? 0}</td>
                    <td>${report.active_alerts ?? 0}</td>
                    <td>${report.latitude ?? "-"}</td>
                    <td>${report.longitude ?? "-"}</td>
                `;

                targetBody.appendChild(row);
            });
        }

        if (reportTableBody) {
            renderTableRows(reportTableBody, latestReports);
        }

        if (dashboardTableBody) {
            renderTableRows(dashboardTableBody, latestReports.slice(0, 5));
        }

        if (reportCount) {
            reportCount.textContent = `${reports.length} Reports`;
        }

        if (dashboardHistoryCount) {
            dashboardHistoryCount.textContent = `${reports.length} Records`;
        }

        const latestReport = latestReports[0];
        if (latestReport) {
            const reportVehicles = document.getElementById("reportVehicles");
            const reportDefects = document.getElementById("reportDefects");
            const reportAlerts = document.getElementById("reportAlerts");
            const reportTraffic = document.getElementById("reportTraffic");
            const reportPersons = document.getElementById("reportPersons");
            const reportGPS = document.getElementById("reportGPS");

            if (reportVehicles) {
                reportVehicles.textContent = latestReport.vehicles ?? 0;
            }
            if (reportDefects) {
                reportDefects.textContent = latestReport.road_defects ?? 0;
            }
            if (reportAlerts) {
                reportAlerts.textContent = latestReport.active_alerts ?? 0;
            }
            if (reportTraffic) {
                reportTraffic.textContent = t(
                    `status.${String(latestReport.traffic_status ?? "low").toLowerCase()}`
                );
            }
            if (reportPersons) {
                reportPersons.textContent = latestReport.persons ?? 0;
            }
            if (reportGPS) {
                reportGPS.textContent = `${latestReport.latitude ?? "-"}, ${latestReport.longitude ?? "-"}`;
            }
        }

        await loadDetectionEvents();

    } catch (error) {

        console.error("Reports loading error:", error);

    } finally {
        historyUpdateInProgress = false;
    }

}

function updateAccidentAlertIfNew(incident) {
    if (!incident || !incident.incident_id || incident.incident_id === latestProcessedIncidentId) {
        return;
    }

    latestProcessedIncidentId = incident.incident_id;
    renderAccidentAlert(incident);
    renderAccidentToast(incident);
}

function updateNotificationBadge(activeAlerts) {
    const notificationBadge = document.getElementById("notificationBadge");
    if (!notificationBadge) {
        return;
    }

    const count = Number(activeAlerts) || 0;
    notificationBadge.textContent = count > 99 ? "99+" : String(count);
    notificationBadge.hidden = count === 0;
}

function renderAccidentToast(incident) {
    const toast = document.getElementById("accidentToast");
    if (!toast || !incident) {
        return;
    }

    toast.replaceChildren();

    const header = document.createElement("div");
    header.className = "accident-toast-header";
    header.innerHTML = `<strong class="accident-toast-title"><i class="fa-solid fa-triangle-exclamation"></i> Confirmed Accident</strong>`;

    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "accident-toast-close";
    closeButton.setAttribute("aria-label", "Dismiss confirmed accident notification");
    closeButton.innerHTML = "&times;";
    closeButton.addEventListener("click", function () {
        toast.hidden = true;
    });
    header.appendChild(closeButton);

    const details = document.createElement("div");
    details.className = "accident-toast-details";
    [
        ["Incident ID", incident.incident_id],
        ["Bus ID", incident.bus_id ?? "BUS-001"],
        ["Camera ID", incident.camera_id ?? "CAMERA-001"],
        ["Timestamp", incident.timestamp ?? "-"],
    ].forEach(function ([label, value]) {
        const detail = document.createElement("span");
        detail.textContent = `${label}: ${value ?? "-"}`;
        details.appendChild(detail);
    });

    const actions = document.createElement("div");
    actions.className = "accident-toast-actions";
    const reviewButton = document.createElement("button");
    reviewButton.type = "button";
    reviewButton.className = "accident-toast-review";
    reviewButton.textContent = "Review Incident";
    reviewButton.addEventListener("click", function () {
        document.getElementById("reportsBtn")?.click();
    });
    actions.appendChild(reviewButton);

    toast.append(header, details, actions);
    toast.hidden = false;
}

function renderAccidentAlert(incident) {
    const alertPanel = document.getElementById("accidentAlertPanel");
    if (!alertPanel || !incident) {
        return;
    }

    alertPanel.hidden = false;
    alertPanel.innerHTML = `
        <div><strong><i class="fa-solid fa-triangle-exclamation"></i> ACCIDENT ALERT</strong><span>Incident ID: ${incident.incident_id ?? "-"}</span></div>
        <div><span>Bus: ${incident.bus_id ?? "BUS-001"}</span><span>Camera: ${incident.camera_id ?? "CAMERA-001"}</span><span>Vehicle: ${incident.vehicle_type ?? "-"}</span><span>Registration: ${incident.registration_number ?? "Unreadable"}</span><span>Status: ${incident.status ?? "new"}</span></div>
        <button type="button" class="review-incident-button" data-review-incident><i class="fa-solid fa-arrow-up-right-from-square"></i> ${getPortalTranslation(document.documentElement.lang, "reviewIncident")}</button>
    `;
    alertPanel.querySelector("[data-review-incident]")?.addEventListener("click", function () {
        document.getElementById("reportsBtn")?.click();
    });
}

async function loadAccidentIncidents() {
    if (accidentUpdateInProgress) {
        return;
    }

    const tableBody = document.getElementById("accidentReportsTableBody");
    const reportCount = document.getElementById("accidentReportCount");
    if (!tableBody) {
        return;
    }

    accidentUpdateInProgress = true;
    try {
        const response = await fetch(
            apiUrl("/api/accident-incidents"),
            { cache: "no-store" }
        );
        if (!response.ok) {
            throw new Error("Accident incidents API unavailable");
        }

        const incidents = await response.json();
        const normalizedIncidents = Array.isArray(incidents) ? incidents : [];
        const incidentsSignature = JSON.stringify(normalizedIncidents);
        const anprEvidenceCount = document.getElementById("anprEvidenceCount");
        if (anprEvidenceCount) {
            anprEvidenceCount.textContent = normalizedIncidents.length;
        }
        if (incidentsSignature === lastAccidentIncidentsSignature) {
            updateAccidentAlertIfNew(normalizedIncidents[0]);
            return;
        }
        lastAccidentIncidentsSignature = incidentsSignature;
        tableBody.innerHTML = "";
        if (normalizedIncidents.length === 0) {
            tableBody.innerHTML = "<tr><td colspan=\"10\">No accident reports saved yet.</td></tr>";
            if (reportCount) {
                reportCount.textContent = "0 Accidents";
            }
            return;
        }

        normalizedIncidents.forEach((incident) => {
            const row = document.createElement("tr");
            const imageLink = incident.accident_image_path
                ? `<a href="http://127.0.0.1:5000${incident.accident_image_path}" target="_blank" rel="noopener">View image</a>`
                : "-";
            const videoLink = incident.accident_video_path
                ? `<a href="http://127.0.0.1:5000${incident.accident_video_path}" download>View/Download video</a>`
                : "Preparing video...";
            row.innerHTML = `
                <td>${incident.incident_id ?? "-"}</td>
                <td>${incident.bus_id ?? "BUS-001"}</td>
                <td>${incident.camera_id ?? "CAMERA-001"}</td>
                <td>${incident.timestamp ?? "-"}</td>
                <td>${incident.vehicle_type ?? "-"}</td>
                <td>${incident.registration_number ?? "Unreadable"}</td>
                <td>${incident.plate_confidence == null ? "-" : `${Math.round(incident.plate_confidence * 100)}%`}</td>
                <td>${incident.status ?? "New"}</td>
                <td>${incident.latitude ?? "-"}, ${incident.longitude ?? "-"}</td>
                <td>${imageLink} <span>+</span> ${videoLink}</td>
            `;
            tableBody.appendChild(row);
        });
        if (reportCount) {
            reportCount.textContent = `${normalizedIncidents.length} Accidents`;
        }
        updateAccidentAlertIfNew(normalizedIncidents[0]);
    } catch (error) {
        console.error("Accident incidents loading error:", error);
    } finally {
        accidentUpdateInProgress = false;
    }
}

async function loadDetectionEvents() {
    const eventsContainer = document.getElementById("detectionEvents");
    if (!eventsContainer) {
        return;
    }

    try {
        const response = await fetch(
            apiUrl("/api/detection-events"),
            { cache: "no-store" }
        );
        if (!response.ok) {
            throw new Error("Detection events API unavailable");
        }

        const events = await response.json();
        eventsContainer.replaceChildren();

        if (!Array.isArray(events) || events.length === 0) {
            const emptyMessage = document.createElement("p");
            emptyMessage.className = "empty-detection-events";
            emptyMessage.textContent = t("label.noConfirmedEvents");
            eventsContainer.appendChild(emptyMessage);
            return;
        }

        events.forEach((event) => {
            const item = document.createElement("div");
            item.className = "detection-event-item";
            item.innerHTML = `
                <span class="event-type">${event.detection_type ?? "Detection"}</span>
                <span>${event.bus_id ?? "BUS-001"}</span>
                <span>${event.camera_id ?? "CAMERA-001"}</span>
                <span>${event.confidence == null ? "-" : `${Math.round(event.confidence * 100)}%`}</span>
                <span>${event.latitude ?? "-"}, ${event.longitude ?? "-"}</span>
                <span>${event.status ?? "OPEN"}</span>
                <time>${event.timestamp ?? "-"}</time>
            `;
            eventsContainer.appendChild(item);
        });
    } catch (error) {
        console.error("Detection events loading error:", error);
    }
}