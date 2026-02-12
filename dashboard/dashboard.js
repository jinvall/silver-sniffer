// ======================================================
// WEBSOCKET + GLOBAL STATE
// ======================================================

const ws = new WebSocket("ws://localhost:8765");

let frameCount = 0;
let lastFPS = 0;
let lastTick = performance.now();
const bleAngles = {};

const channelCounts = Array(15).fill(0);
const bssidMap = {};
const bleMap = {};
const wifiMap = {};

// ===============================
// SESSION TRACKING (STEP 1)
// ===============================
const sessionStart = Date.now();

function formatElapsed(ms) {
    const sec = Math.floor(ms / 1000);
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    return `${h.toString().padStart(2,'0')}:${m.toString().padStart(2,'0')}:${s.toString().padStart(2,'0')}`;
}

const waterfallCanvas = document.getElementById("waterfall");
const waterfallCtx = waterfallCanvas.getContext("2d");
waterfallCanvas.width = 600;
waterfallCanvas.height = 300;

// simple wifi stats (frontend-only)
const wifiStats = {
    totalFrames: 0,
    uniqueBSSIDs: 0,
    strongestRSSI: -999,
    strongestBSSID: ""
};


// ======================================================
// FPS
// ======================================================
function updateFPS() {
    const now = performance.now();
    if (now - lastTick >= 1000) {
        lastFPS = frameCount;
        frameCount = 0;
        lastTick = now;

        document.getElementById("fps-value").innerText = lastFPS;

        // ===============================
        // SESSION ELAPSED UPDATE (STEP 4)
        // ===============================
        document.getElementById("session-elapsed").innerText =
            "Elapsed: " + formatElapsed(Date.now() - sessionStart);
    }
}


// ======================================================
// WIFI CHANNEL OCCUPANCY
// ======================================================
function updateChannel(frame) {
    const ch = frame.channel;
    if (ch >= 1 && ch <= 14) {
        channelCounts[ch]++;
    }
}

function drawChannelChart() {
    const canvas = document.getElementById("channelChart");
    const ctx = canvas.getContext("2d");
    canvas.width = 300;
    canvas.height = 150;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const max = Math.max(...channelCounts) || 1;

    for (let ch = 1; ch <= 14; ch++) {
        const barHeight = (channelCounts[ch] / max) * (canvas.height - 20);
        const x = (ch - 1) * 20;
        const y = canvas.height - 20 - barHeight;

        ctx.fillStyle = "#66ccff";
        ctx.fillRect(x, y, 18, barHeight);

        // channel number under bar
        ctx.fillStyle = "#ccc";
        ctx.font = "10px monospace";
        ctx.textAlign = "center";
        ctx.fillText(ch.toString(), x + 9, canvas.height - 5);
    }
}


// ======================================================
// WIFI RSSI WATERFALL
// ======================================================
function updateWaterfall(frame) {
    waterfallCtx.drawImage(waterfallCanvas, 0, 1);

    const rssi = frame.rssi;
    const intensity = Math.max(0, Math.min(255, 255 + rssi * 3));
    waterfallCtx.fillStyle = `rgb(${intensity}, 0, 0)`;
    waterfallCtx.fillRect(0, 0, waterfallCanvas.width, 1);
}


// ======================================================
// WIFI BSSID TABLE + STATS
// ======================================================
function updateBSSID(frame) {
    const key = frame.bssid || "00:00:00:00:00:00";

    if (!bssidMap[key]) {
        bssidMap[key] = {
            ssid: frame.ssid || "(hidden)",
            rssi: frame.rssi,
            channel: frame.channel,
            lastSeen: new Date().toLocaleTimeString()
        };
    } else {
        bssidMap[key].ssid = frame.ssid || bssidMap[key].ssid || '(hidden)';
        bssidMap[key].rssi = frame.rssi;
        bssidMap[key].channel = frame.channel;
        bssidMap[key].lastSeen = new Date().toLocaleTimeString();
    }

    // stats
    wifiStats.totalFrames++;
    wifiStats.uniqueBSSIDs = Object.keys(bssidMap).length;
    if (frame.rssi > wifiStats.strongestRSSI) {
        wifiStats.strongestRSSI = frame.rssi;
        wifiStats.strongestBSSID = key;
    }

    const tbody = document.querySelector("#bssidTable tbody");
    tbody.innerHTML = "";

    for (const [bssid, info] of Object.entries(bssidMap)) {
        const row = document.createElement("tr");
        row.innerHTML = `
            <td>${bssid}</td>
            <td>${info.ssid}</td>
            <td>${info.rssi}</td>
            <td>${info.channel}</td>
            <td>${info.lastSeen}</td>
        `;
        tbody.appendChild(row);
    }

    updateWifiStatsPanel();
}

function updateWifiStatsPanel() {
    const el = document.getElementById("wifi-stats");
    if (!el) return;
    el.innerHTML = `
        <div>Total WiFi Frames: ${wifiStats.totalFrames}</div>
        <div>Unique BSSIDs: ${wifiStats.uniqueBSSIDs}</div>
        <div>Strongest RSSI: ${wifiStats.strongestRSSI} dBm</div>
        <div>Strongest BSSID: ${wifiStats.strongestBSSID}</div>
    `;
}


// ======================================================
// BLE RADAR SYSTEM (FINAL, SMOOTH, COLORFUL)
// ======================================================
const bleCanvas = document.getElementById("bleRadar");
const bleCtx = bleCanvas.getContext("2d");
bleCanvas.width = 300;
bleCanvas.height = 300;

const bleTooltip = document.getElementById("bleTooltip");

function computeLane(distance) {
    if (distance < 2) return 1;
    if (distance < 5) return 2;
    if (distance < 10) return 3;
    return 4;
}

function laneToRadius(lane) {
    return { 1: 40, 2: 80, 3: 120, 4: 140 }[lane] || 140;
}

function hexToRGB(hex) {
    hex = hex.replace("#", "");
    const n = parseInt(hex, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function updateBLE(frame) {
    const addr = frame.addr;
    const dist = frame.distance_m || 5;

    if (!bleMap[addr]) {
        const lane = computeLane(dist);
        const baseRadius = laneToRadius(lane);

        bleMap[addr] = {
            angle: Math.random() * Math.PI * 2,
            lane: lane,
            radius: baseRadius,
            targetRadius: baseRadius
        };
    }

    const dev = bleMap[addr];

    const newLane = computeLane(dist);
    if (newLane !== dev.lane && Math.abs(newLane - dev.lane) >= 1) {
        dev.lane = newLane;
        dev.targetRadius = laneToRadius(newLane);
    }

    let color = "#888888";

    if (frame.movement === "approach") color = "#00ff00";
    if (frame.movement === "depart")  color = "#ff66cc";
    if (frame.movement === "unknown") color = "#aa66ff";
    if (frame.movement === "steady")  color = "#888888";
   
    dev.color = color;
    dev.movement = frame.movement;
    dev.distance = dist;
    dev.name = frame.name || "";
    dev.rssi = frame.rssi;
    dev.lastSeen = Date.now();

    drawBLERadar();
}
function computeAngleForMac(mac) {
    // Hash the MAC into a number
    let hash = 0;
    for (let i = 0; i < mac.length; i++) {
        hash = (hash * 31 + mac.charCodeAt(i)) >>> 0;
    }

    // Convert hash → angle in radians
    return (hash % 360) * (Math.PI / 180);
}

function drawBLERadar() {
    bleCtx.clearRect(0, 0, bleCanvas.width, bleCanvas.height);

    const cx = bleCanvas.width / 2;
    const cy = bleCanvas.height / 2;

    window._bleDotPositions = [];

    bleCtx.strokeStyle = "#333";
    for (let r = 40; r <= 140; r += 40) {
        bleCtx.beginPath();
        bleCtx.arc(cx, cy, r, 0, Math.PI * 2);
        bleCtx.stroke();
    }

    const now = Date.now();

    for (const [addr, dev] of Object.entries(bleMap)) {
        const age = now - dev.lastSeen;

        let fade = 1.0;
        if (dev.movement !== "steady") {
            if (age > 5000) fade = Math.max(0, 1 - (age - 5000) / 3000);
            if (fade <= 0) continue;
        }

        dev.radius += (dev.targetRadius - dev.radius) * 0.1;

        const x = cx + dev.radius * Math.cos(dev.angle);
        const y = cy + dev.radius * Math.sin(dev.angle);

        const dist = dev.distance || 5;
        const size = Math.max(3, 12 - dist);

        const [r, g, b] = hexToRGB(dev.color);
        const dotColor = `rgba(${r},${g},${b},${fade})`;

        bleCtx.fillStyle = dotColor;
        bleCtx.beginPath();
        bleCtx.arc(x, y, size, 0, Math.PI * 2);
        bleCtx.fill();

        bleCtx.fillStyle = `rgba(200,200,200,${fade})`;
        bleCtx.font = "12px monospace";
        bleCtx.fillText(`${dist.toFixed(1)}m`, x + 8, y - 8);

        if (fade > 0.3) {
            window._bleDotPositions.push({ addr, dev, x, y, size });
        }
    }
}

bleCanvas.addEventListener("mousemove", (e) => {
    const rect = bleCanvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    let hit = null;

    for (const dot of window._bleDotPositions) {
        if (Math.hypot(mx - dot.x, my - dot.y) <= dot.size + 4) {
            hit = dot;
            break;
        }
    }

    if (!hit) {
        bleTooltip.style.display = "none";
        return;
    }

    const dev = hit.dev;

    bleTooltip.innerHTML = `
        <strong>${dev.name || "(unnamed)"} </strong><br>
        ${hit.addr}<br>
        Distance: ${dev.distance?.toFixed(1)}m<br>
        RSSI: ${dev.rssi}<br>
        Movement: ${dev.movement}
    `;

    bleTooltip.style.left = (e.pageX + 12) + "px";
    bleTooltip.style.top = (e.pageY + 12) + "px";
    bleTooltip.style.display = "block";
});


// ======================================================
// WIFI RADAR (MIRRORING BLE, WITH PINNING)
// ======================================================
const wifiCanvas = document.getElementById("wifiRadar");
const wifiCtx = wifiCanvas.getContext("2d");
wifiCanvas.width = 300;
wifiCanvas.height = 300;

let wifiDotPositions = [];

const wifiTooltip = document.getElementById("wifiTooltip");

function rssiToDistance(rssi) {
    // crude mapping: closer = stronger
    const clamped = Math.max(-90, Math.min(-30, rssi || -70));
    const t = (clamped + 90) / 60; // 0..1
    return 0.5 + (1 - t) * 8.0;    // ~0.5m..8.5m
}

function updateWiFiRadar(frame) {
    const bssid = frame.bssid || "00:00:00:00:00:00";
    const rssi = frame.rssi || -80;
    const dist = rssiToDistance(rssi);

    if (!wifiMap[bssid]) {
        const lane = computeLane(dist);
        const baseRadius = laneToRadius(lane);

        wifiMap[bssid] = {
            angle: Math.random() * Math.PI * 2,
            lane: lane,
            radius: baseRadius,
            targetRadius: baseRadius,
            pinned: false,
            lastRSSI: rssi,
            movement: "unknown",
            flashUntil: 0
        };
    }

    const dev = wifiMap[bssid];

    // movement classification
    let movement = "steady";
    if (dev.lastRSSI !== undefined) {
        if (rssi > dev.lastRSSI + 3) movement = "approach";
        else if (rssi < dev.lastRSSI - 3) movement = "depart";
    } else {
        movement = "unknown";
    }
    dev.lastRSSI = rssi;
    dev.movement = movement;

    dev.ssid = frame.ssid || dev.ssid || "(hidden)";

    // color mapping (reuse BLE palette)
    let color = "#888888";
    if (movement === "approach") color = "#00ff00";
    if (movement === "depart")  color = "#ff66cc";
    if (movement === "unknown") color = "#aa66ff";
    if (movement === "steady")  color = "#888888";
    dev.color = color;

    // lane + radius
    const newLane = computeLane(dist);
    if (newLane !== dev.lane && Math.abs(newLane - dev.lane) >= 1) {
        dev.lane = newLane;
        dev.targetRadius = laneToRadius(newLane);
    }

    dev.distance = dist;
    const now = Date.now();

    // flash if returning after being gone
    if (dev.lastSeen && now - dev.lastSeen > 10000) {
        dev.flashUntil = now + 1000;
    }

    dev.lastSeen = now;

    drawWiFiRadar();
}

function drawWiFiRadar() {
    wifiCtx.clearRect(0, 0, wifiCanvas.width, wifiCanvas.height);

    const cx = wifiCanvas.width / 2;
    const cy = wifiCanvas.height / 2;

    wifiDotPositions = [];

    wifiCtx.strokeStyle = "#333";
    for (let r = 40; r <= 140; r += 40) {
        wifiCtx.beginPath();
        wifiCtx.arc(cx, cy, r, 0, Math.PI * 2);
        wifiCtx.stroke();
    }

    const now = Date.now();

    for (const [bssid, dev] of Object.entries(wifiMap)) {
        const age = now - (dev.lastSeen || 0);

        // non-pinned dots disappear after 10s
        if (!dev.pinned && age > 10000) continue;

        let fade = 1.0;
        if (dev.pinned && age > 10000) {
            fade = 0.3; // pinned but stale
        }

        dev.radius += (dev.targetRadius - dev.radius) * 0.1;

        const x = cx + dev.radius * Math.cos(dev.angle);
        const y = cy + dev.radius * Math.sin(dev.angle);

        const dist = dev.distance || 5;
        const size = Math.max(3, 12 - dist);

        const [r, g, b] = hexToRGB(dev.color);
        let alpha = fade;

        if (dev.flashUntil && now < dev.flashUntil) {
            alpha = 1.0;
        }

        const dotColor = `rgba(${r},${g},${b},${alpha})`;

        wifiCtx.fillStyle = dotColor;
        wifiCtx.beginPath();
        wifiCtx.arc(x, y, size, 0, Math.PI * 2);
        wifiCtx.fill();

        wifiCtx.fillStyle = `rgba(200,200,200,${alpha})`;
        wifiCtx.font = "10px monospace";
        wifiCtx.fillText(bssid.slice(0, 8), x + 8, y - 8);

        wifiDotPositions.push({ bssid, dev, x, y, size });
    }
}

// click-to-pin
wifiCanvas.addEventListener("click", (e) => {
    const rect = wifiCanvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    for (const dot of wifiDotPositions) {
        if (Math.hypot(mx - dot.x, my - dot.y) <= dot.size + 4) {
            const dev = wifiMap[dot.bssid];
            dev.pinned = !dev.pinned;
            break;
        }
    }

    drawWiFiRadar();
});
// ===============================
// WIFI TOOLTIP HANDLER
// ===============================
wifiCanvas.addEventListener("mousemove", (e) => {
    const rect = wifiCanvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    let hit = null;

    for (const dot of wifiDotPositions) {
        if (Math.hypot(mx - dot.x, my - dot.y) <= dot.size + 4) {
            hit = dot;
            break;
        }
    }

    if (!hit) {
        wifiTooltip.style.display = "none";
        return;
    }

    const dev = hit.dev;

    wifiTooltip.innerHTML = `
        <strong>${hit.bssid}</strong><br>
        SSID: ${dev.ssid}<br>
        RSSI: ${dev.lastRSSI}<br>
        Movement: ${dev.movement}<br>
        Distance: ${dev.distance?.toFixed(1)}m
    `;


    wifiTooltip.style.left = (e.pageX + 12) + "px";
    wifiTooltip.style.top = (e.pageY + 12) + "px";
    wifiTooltip.style.display = "block";
});


// ======================================================
// WEBSOCKET HANDLER
// ======================================================
ws.onmessage = (event) => {
    const frame = JSON.parse(event.data);
    frameCount++;

    if (frame.type === "wifi") {
        updateChannel(frame);
        updateWaterfall(frame);
        updateBSSID(frame);
        updateWiFiRadar(frame);
    }

    if (frame.type === "ble") {
        updateBLE(frame);
    }

    updateFPS();
    drawChannelChart();
};
// ===============================
// SESSION START DISPLAY (STEP 3)
// ===============================
document.getElementById("session-start").innerText =
    "Started: " + new Date(sessionStart).toLocaleTimeString();

function enableEnlarge(id) {
    const panel = document.getElementById(id);
    panel.style.cursor = "pointer";

    panel.addEventListener("click", () => {
        const modal = document.getElementById("enlarge-modal");
        const content = document.getElementById("enlarge-content");

        content.innerHTML = "";
        content.appendChild(panel.cloneNode(true));

        modal.style.display = "flex";
    });
}

document.getElementById("enlarge-modal").addEventListener("click", () => {
    document.getElementById("enlarge-modal").style.display = "none";
});

enableEnlarge("ble-radar-panel");
enableEnlarge("wifi-radar-panel");
enableEnlarge("waterfall-panel");
enableEnlarge("channel-panel");

