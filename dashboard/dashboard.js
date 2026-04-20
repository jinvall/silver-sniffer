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

// set of blocked and ignored devices (persisted server-side + local filtering)
const blockedSet = new Set();
const ignoredSet = new Set();


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
// make waterfall responsive to its panel width and keep height fixed
waterfallCanvas.style.width = '100%';
waterfallCanvas.height = 300;
function resizeWaterfall() {
    // update canvas pixel width to match CSS width
    waterfallCanvas.width = Math.max(200, waterfallCanvas.clientWidth);
}
resizeWaterfall();
window.addEventListener('resize', resizeWaterfall);

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

    // size canvas to its displayed size for crisp rendering
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(200, Math.floor(rect.width));
    const h = Math.max(120, Math.floor(rect.height || 150));
    if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
    }

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const max = Math.max(...channelCounts) || 1;

    const barW = Math.floor(canvas.width / 14);
    for (let i = 0; i < 14; i++) {
        const ch = i + 1;
        const barHeight = (channelCounts[ch] / max) * (canvas.height - 20);
        const x = i * barW;
        const y = canvas.height - 20 - barHeight;

        ctx.fillStyle = "#66ccff";
        ctx.fillRect(x, y, Math.max(8, barW - 2), barHeight);

        // channel number under bar
        ctx.fillStyle = "#ccc";
        ctx.font = "10px monospace";
        ctx.textAlign = "center";
        ctx.fillText(ch.toString(), x + barW / 2, canvas.height - 5);
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

    // respect blocked and ignored lists (client-side filtering)
    if (blockedSet.has(`wifi:${key}`) || ignoredSet.has(`wifi:${key}`)) return;

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
        row.dataset.bssid = bssid;
        row.innerHTML = `
            <td>${bssid}</td>
            <td>${info.ssid}</td>
            <td>${info.rssi}</td>
            <td>${info.channel}</td>
            <td>${info.lastSeen}</td>
        `;
        if (hoveredWifiBssid && hoveredWifiBssid === bssid) {
            row.classList.add("hovered");
        }
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
bleCanvas.style.width = '100%';
bleCanvas.style.height = '300px';

const bleTooltip = document.getElementById("bleTooltip");
let hoveredBleAddr = null;
let hoveredWifiBssid = null;

function setPanelHover(panelId, active) {
    const panel = document.getElementById(panelId);
    if (!panel) return;
    panel.classList.toggle("hovered", active);
}

function setWifiRowHover(bssid) {
    document.querySelectorAll("#bssidTable tbody tr").forEach(row => {
        row.classList.toggle("hovered", row.dataset.bssid === bssid);
    });
}

function clearBleHover() {
    hoveredBleAddr = null;
    bleTooltip.style.display = "none";
    setPanelHover("ble-panel", false);
    drawBLERadar();
}

function clearWifiHover() {
    hoveredWifiBssid = null;
    wifiTooltip.style.display = "none";
    setPanelHover("wifi-panel", false);
    setWifiRowHover(null);
    drawWiFiRadar();
}

function clampTooltipPosition(tooltip, pageX, pageY) {
    const padding = 12;
    const wasHidden = tooltip.style.display === "none" || window.getComputedStyle(tooltip).display === "none";
    let previousVisibility;
    if (wasHidden) {
        previousVisibility = tooltip.style.visibility;
        tooltip.style.visibility = "hidden";
        tooltip.style.display = "block";
    }

    const rect = tooltip.getBoundingClientRect();
    const maxX = document.documentElement.clientWidth - rect.width - padding;
    const maxY = document.documentElement.clientHeight - rect.height - padding;
    let left = pageX + padding;
    let top = pageY + padding;
    if (left > maxX) left = Math.max(padding, pageX - rect.width - padding);
    if (top > maxY) top = Math.max(padding, pageY - rect.height - padding);

    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;

    if (wasHidden) {
        tooltip.style.visibility = previousVisibility || "visible";
        tooltip.style.display = "none";
    }
}

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

    // respect blocked and ignored lists for BLE
    if (blockedSet.has(`ble:${addr}`) || ignoredSet.has(`ble:${addr}`)) return;

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
    dev.lastRSSI = frame.rssi;
    dev.lastSeen = Date.now();

    // ⭐ BLE history for analytics/report
    if (!dev.history) dev.history = [];
    dev.history.push({
        timestamp: Date.now(),
        rssi: dev.lastRSSI,
        movement: dev.movement,
        distance: dev.distance
    });
    if (dev.history.length > 5000) dev.history.shift();

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
    // resize canvas to displayed size (hi-dpi aware)
    const rect = bleCanvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const cw = Math.max(200, Math.floor(rect.width * dpr));
    const ch = Math.max(200, Math.floor(rect.height * dpr));
    if (bleCanvas.width !== cw || bleCanvas.height !== ch) {
        bleCanvas.width = cw;
        bleCanvas.height = ch;
        bleCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    const cssW = rect.width;
    const cssH = rect.height;

    bleCtx.clearRect(0, 0, cssW, cssH);

    const cx = cssW / 2;
    const cy = cssH / 2;

    window._bleDotPositions = [];

    bleCtx.strokeStyle = "#333";
    const scaleFactor = cssW / 300; // scale rings/radii relative to default
    for (let r = 40; r <= 140; r += 40) {
        bleCtx.beginPath();
        bleCtx.arc(cx, cy, r * scaleFactor, 0, Math.PI * 2);
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

        const rScaled = dev.radius * scaleFactor;
        const x = cx + rScaled * Math.cos(dev.angle);
        const y = cy + rScaled * Math.sin(dev.angle);

        const dist = dev.distance || 5;
        const size = Math.max(3, 12 - dist) * Math.max(1, scaleFactor);

        const [r, g, b] = hexToRGB(dev.color);
        const dotColor = `rgba(${r},${g},${b},${fade})`;

        bleCtx.fillStyle = dotColor;
        bleCtx.beginPath();
        bleCtx.arc(x, y, size, 0, Math.PI * 2);
        bleCtx.fill();

        bleCtx.fillStyle = `rgba(200,200,200,${fade})`;
        bleCtx.font = `${12 * Math.max(1, scaleFactor)}px monospace`;
        bleCtx.fillText(`${dist.toFixed(1)}m`, x + 8, y - 8);

        if (addr === hoveredBleAddr) {
            bleCtx.strokeStyle = "rgba(255,255,255,0.8)";
            bleCtx.lineWidth = 2;
            bleCtx.beginPath();
            bleCtx.arc(x, y, size + 5, 0, Math.PI * 2);
            bleCtx.stroke();
        }

        if (fade > 0.3) {
            window._bleDotPositions.push({ addr, dev, x, y, size });
        }
    }
}

function processBleHoverEvent(e) {
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
        clearBleHover();
        return;
    }

    hoveredBleAddr = hit.addr;
    setPanelHover("ble-panel", true);

    const dev = hit.dev;

    bleTooltip.innerHTML = `
        <strong>${dev.name || "(unnamed)"} </strong><br>
        ${hit.addr}<br>
        Distance: ${dev.distance?.toFixed(1)}m<br>
        RSSI: ${dev.rssi}<br>
        Movement: ${dev.movement}
    `;

    clampTooltipPosition(bleTooltip, e.pageX, e.pageY);
    bleTooltip.style.display = "block";
    drawBLERadar();
}

bleCanvas.addEventListener("pointermove", processBleHoverEvent);
bleCanvas.addEventListener("pointerdown", processBleHoverEvent);
bleCanvas.addEventListener("pointerleave", clearBleHover);

// click-to-block support for BLE radar
bleCanvas.addEventListener('click', async (e) => {
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
    if (!hit) return;

    // prevent the global window click handler from immediately hiding the popup
    e.stopPropagation();

    const addr = hit.addr;
    const devStr = `ble:${addr}`;

    const popup = document.getElementById('radarActionPopup');
    popup.innerHTML = `
        <div style="font-weight:600; margin-bottom:6px;">BLE ${addr}</div>
        <div style="font-size:12px; color:#999;">${hit.dev.name || '(unnamed)'}</div>
        <div style="margin-top:8px; display:flex; gap:8px; justify-content:flex-end;">
          <button id="popup-block">${blockedSet.has(devStr) ? 'Unblock' : 'Block'}</button>
          <button id="popup-ignore">Ignore</button>
          <button id="popup-report">Report</button>
          <button id="popup-cancel">Cancel</button>
        </div>
    `;

    // attach popup to modal content when BLE panel is enlarged so interactions remain inside modal
    const blePanel = document.getElementById('ble-panel');
    const popupContainer = (blePanel && blePanel.parentNode && blePanel.parentNode.id === 'enlarge-content')
        ? document.getElementById('enlarge-content')
        : document.body;
    popupContainer.appendChild(popup);

    if (popupContainer.id === 'enlarge-content') {
        const parentRect = popupContainer.getBoundingClientRect();
        popup.style.left = (e.clientX - parentRect.left + 8) + 'px';
        popup.style.top = (e.clientY - parentRect.top + 8) + 'px';
    } else {
        popup.style.left = (e.pageX + 8) + 'px';
        popup.style.top = (e.pageY + 8) + 'px';
    }
    popup.style.display = 'block';

    document.getElementById('popup-cancel').onclick = () => { popup.style.display = 'none'; };
    document.getElementById('popup-block').onclick = async () => {
        if (blockedSet.has(devStr)) {
            await sendUnblock(devStr);
            blockedSet.delete(devStr);
        } else {
            await sendBlock(devStr);
            blockedSet.add(devStr);
            delete bleMap[addr];
        }
        popup.style.display = 'none';
        drawBLERadar();
    };

    document.getElementById('popup-ignore').onclick = () => {
        ignoredSet.add(devStr);
        delete bleMap[addr];
        popup.style.display = 'none';
        drawBLERadar();
    };

    // BLE REPORT
    document.getElementById('popup-report').onclick = () => {
        popup.style.display = 'none';
        openBleReport(addr);
    };
});


// ======================================================
// WIFI RADAR (MIRRORING BLE, WITH PINNING)
// ======================================================
const wifiCanvas = document.getElementById("wifiRadar");
const wifiCtx = wifiCanvas.getContext("2d");
wifiCanvas.width = 300;
wifiCanvas.height = 300;

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

    // respect blocked and ignored lists
    if (blockedSet.has(`wifi:${bssid}`) || ignoredSet.has(`wifi:${bssid}`)) return;

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
            flashUntil: 0,
            ssidHistory: []
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

    // SSID tracking
    dev.ssid = frame.ssid || dev.ssid || "(hidden)";
    if (!dev.ssidHistory) dev.ssidHistory = [];
    if (dev.ssidHistory[dev.ssidHistory.length - 1] !== dev.ssid) {
        dev.ssidHistory.push(dev.ssid);
        if (dev.ssidHistory.length > 100) dev.ssidHistory.shift();
    }

    // ⭐ WiFi history for analytics/report
    if (!dev.history) dev.history = [];
    dev.history.push({
        timestamp: Date.now(),
        rssi,
        channel: frame.channel,
        movement,
        ssid: dev.ssid,
        distance: dist
    });
    if (dev.history.length > 5000) dev.history.shift();

    // color mapping
    let color = "#888888";
    if (movement === "approach") color = "#00ff00";
    if (movement === "depart")  color = "#ff66cc";
    if (movement === "unknown") color = "#aa66ff";
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
    const rect = wifiCanvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const cw = Math.max(200, Math.floor(rect.width * dpr));
    const ch = Math.max(200, Math.floor(rect.height * dpr));
    if (wifiCanvas.width !== cw || wifiCanvas.height !== ch) {
        wifiCanvas.width = cw;
        wifiCanvas.height = ch;
        wifiCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    const cssW = rect.width;
    const cssH = rect.height;

    wifiCtx.clearRect(0, 0, cssW, cssH);

    const cx = cssW / 2;
    const cy = cssH / 2;

    // IMPORTANT: use the global popup array
    window._wifiDotPositions = [];

    wifiCtx.strokeStyle = "#333";
    const scaleFactor = cssW / 300;
    for (let r = 40; r <= 140; r += 40) {
        wifiCtx.beginPath();
        wifiCtx.arc(cx, cy, r * scaleFactor, 0, Math.PI * 2);
        wifiCtx.stroke();
    }

    const now = Date.now();

    for (const [bssid, dev] of Object.entries(wifiMap)) {
        const age = now - (dev.lastSeen || 0);

        if (!dev.pinned && age > 10000) continue;

        let fade = 1.0;
        if (dev.pinned && age > 10000) fade = 0.3;

        dev.radius += (dev.targetRadius - dev.radius) * 0.1;

        const rScaled = dev.radius * scaleFactor;
        const x = cx + rScaled * Math.cos(dev.angle);
        const y = cy + rScaled * Math.sin(dev.angle);

        const dist = dev.distance || 5;
        const size = Math.max(3, 12 - dist) * Math.max(1, scaleFactor);

        const [r, g, b] = hexToRGB(dev.color);
        let alpha = fade;

        if (dev.flashUntil && now < dev.flashUntil) alpha = 1.0;

        const dotColor = `rgba(${r},${g},${b},${alpha})`;

        wifiCtx.fillStyle = dotColor;
        wifiCtx.beginPath();
        wifiCtx.arc(x, y, size, 0, Math.PI * 2);
        wifiCtx.fill();

        wifiCtx.fillStyle = `rgba(200,200,200,${alpha})`;
        wifiCtx.font = `${10 * Math.max(1, scaleFactor)}px monospace`;
        wifiCtx.fillText(bssid.slice(0, 8), x + 8, y - 8);

        if (bssid === hoveredWifiBssid) {
            wifiCtx.strokeStyle = "rgba(255,255,255,0.8)";
            wifiCtx.lineWidth = 2;
            wifiCtx.beginPath();
            wifiCtx.arc(x, y, size + 5, 0, Math.PI * 2);
            wifiCtx.stroke();
        }

        // CRITICAL: store dot for popup detection
        window._wifiDotPositions.push({ bssid, dev, x, y, size });
    }
}


function processWifiHoverEvent(e) {
    const rect = wifiCanvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    let hit = null;
    for (const dot of window._wifiDotPositions) {
        if (Math.hypot(mx - dot.x, my - dot.y) <= dot.size + 4) {
            hit = dot;
            break;
        }
    }

    if (!hit) {
        clearWifiHover();
        return;
    }

    hoveredWifiBssid = hit.bssid;
    setPanelHover("wifi-panel", true);
    setWifiRowHover(hit.bssid);

    const dev = hit.dev;

    wifiTooltip.innerHTML = `
        <strong>${hit.bssid}</strong><br>
        SSID: ${dev.ssid}<br>
        RSSI: ${dev.lastRSSI}<br>
        Movement: ${dev.movement}<br>
        Distance: ${dev.distance?.toFixed(1)}m
    `;

    clampTooltipPosition(wifiTooltip, e.pageX, e.pageY);
    wifiTooltip.style.display = "block";
    drawWiFiRadar();
}

wifiCanvas.addEventListener("pointermove", processWifiHoverEvent);
wifiCanvas.addEventListener("pointerdown", processWifiHoverEvent);
wifiCanvas.addEventListener("pointerleave", clearWifiHover);

// click-to-pin + popup actions for WIFI radar
wifiCanvas.addEventListener('click', async (e) => {
    const rect = wifiCanvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    let hit = null;
    for (const dot of window._wifiDotPositions) {
        if (Math.hypot(mx - dot.x, my - dot.y) <= dot.size + 4) {
            hit = dot;
            break;
        }
    }
    if (!hit) return;

    e.stopPropagation();

    const bssid = hit.bssid;
    const dev = wifiMap[bssid];
    const devStr = `wifi:${bssid}`;

    const popup = document.getElementById('radarActionPopup');
    popup.innerHTML = `
        <div style="font-weight:600; margin-bottom:6px;">${bssid}</div>
        <div style="font-size:12px; color:#999;">SSID: ${dev?.ssid || '(hidden)'}</div>
        <div style="margin-top:8px; display:flex; gap:8px; justify-content:flex-end;">
          <button id="popup-pin">${dev?.pinned ? 'Unpin' : 'Pin'}</button>
          <button id="popup-block">${blockedSet.has(devStr) ? 'Unblock' : 'Block'}</button>
          <button id="popup-ignore">Ignore</button>
          <button id="popup-report">Report</button>
          <button id="popup-cancel">Cancel</button>
        </div>
    `;

    const wifiPanel = document.getElementById('wifi-panel');
    const popupContainer = (wifiPanel && wifiPanel.parentNode && wifiPanel.parentNode.id === 'enlarge-content')
        ? document.getElementById('enlarge-content')
        : document.body;
    popupContainer.appendChild(popup);

    if (popupContainer.id === 'enlarge-content') {
        const parentRect = popupContainer.getBoundingClientRect();
        popup.style.left = (e.clientX - parentRect.left + 8) + 'px';
        popup.style.top = (e.clientY - parentRect.top + 8) + 'px';
    } else {
        popup.style.left = (e.pageX + 8) + 'px';
        popup.style.top = (e.pageY + 8) + 'px';
    }
    popup.style.display = 'block';

    document.getElementById('popup-cancel').onclick = () => { popup.style.display = 'none'; };
    document.getElementById('popup-pin').onclick = () => { if (dev) { dev.pinned = !dev.pinned; } popup.style.display = 'none'; drawWiFiRadar(); };
    document.getElementById('popup-block').onclick = async () => {
        if (blockedSet.has(devStr)) {
            await sendUnblock(devStr);
            blockedSet.delete(devStr);
        } else {
            await sendBlock(devStr);
            blockedSet.add(devStr);
            delete wifiMap[bssid];
            delete bssidMap[bssid];
            updateWifiStatsPanel();
        }
        popup.style.display = 'none';
        drawWiFiRadar();
    };
    document.getElementById('popup-ignore').onclick = () => {
        ignoredSet.add(devStr);
        delete wifiMap[bssid];
        delete bssidMap[bssid];
        updateWifiStatsPanel();
        popup.style.display = 'none';
        drawWiFiRadar();
    };
    document.getElementById('popup-report').onclick = () => {
        popup.style.display = 'none';
        openBssidReport(bssid);
    };
});

// ------------------------------------------------------
// WIFI REPORT (FIXED: now uses wifiMap, not bssidMap)
// ------------------------------------------------------
function openBssidReport(bssid) {
    const dev = wifiMap[bssid];
    if (!dev) return;

    const out = document.getElementById('report-output');
    if (!out) return;

    const lastSeen = dev.lastSeen ? new Date(dev.lastSeen).toLocaleTimeString() : "Unknown";
    const frames = dev.history || [];
    const rssiStats = computeRssiStats(frames);
    const freq = computeFrequency(frames);
    const hourBuckets = bucketByHour(frames);
    const movementSummary = summarizeMovement(frames, dev.movement);

    const ssidHistory = dev.ssidHistory || [];
    const uniqueSsids = [...new Set(ssidHistory.length ? ssidHistory : [dev.ssid].filter(Boolean))];

    out.innerHTML = `
        <div style="font-weight:700; margin-bottom:6px;">Wi‑Fi Access Point Report</div>

        <div><b>BSSID:</b> ${bssid}</div>
        <div><b>SSID (current):</b> ${dev.ssid || "(hidden)"}</div>
        <div><b>SSID history:</b> ${uniqueSsids.length ? uniqueSsids.join(", ") : "(no history)"}</div>
        <div><b>Last Seen:</b> ${lastSeen}</div>
        <div><b>Distance:</b> ${dev.distance ? dev.distance.toFixed(2) + " m" : "Unknown"}</div>

        <hr style="margin:8px 0; border-color:#444;">

        <div style="font-weight:600; margin-bottom:4px;">Presence & Frequency</div>
        <div><b>Total sightings:</b> ${freq.count}</div>
        <div><b>Observation span:</b> ${formatSpan(freq.spanMinutes)}</div>
        <div><b>Encounter rate:</b> ${formatPerMinute(freq.perMinute)}</div>

        <div style="margin-top:4px;"><b>Time-of-day pattern:</b></div>
        <div style="font-size:11px; color:#aaa;">${formatHourBuckets(hourBuckets)}</div>

        <hr style="margin:8px 0; border-color:#444;">

        <div style="font-weight:600; margin-bottom:4px;">RSSI & Movement</div>
        <div><b>Last RSSI:</b> ${dev.lastRSSI ?? "N/A"}</div>
        <div><b>RSSI min / max / avg:</b> 
            ${rssiStats.min ?? "N/A"} / 
            ${rssiStats.max ?? "N/A"} / 
            ${rssiStats.avg != null ? rssiStats.avg.toFixed(1) : "N/A"}
        </div>
        <div><b>Dominant movement:</b> ${movementSummary}</div>
    `;
}

// ======================================================
// FULL ANALYTICS REPORT ENGINE (BLE + WIFI)
// ======================================================

function bucketByHour(frames) {
    const buckets = Array(24).fill(0);
    for (const f of frames) {
        if (!f.timestamp) continue;
        const h = new Date(f.timestamp).getHours();
        buckets[h]++;
    }
    return buckets;
}

function formatHourBuckets(buckets) {
    // show only hours that have activity
    const parts = [];
    for (let h = 0; h < 24; h++) {
        if (buckets[h] > 0) {
            const label = `${h.toString().padStart(2, "0")}:00`;
            parts.push(`${label} (${buckets[h]} hits)`);
        }
    }
    return parts.length ? parts.join(", ") : "No clear time-of-day pattern yet";
}

function computeRssiStats(frames) {
    if (!frames || frames.length === 0) {
        return { min: null, max: null, avg: null };
    }
    const vals = frames
        .map(f => f.rssi)
        .filter(v => typeof v === "number");
    if (!vals.length) return { min: null, max: null, avg: null };
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    return { min, max, avg: Number.isFinite(avg) ? avg : null };
}

function computeFrequency(frames) {
    if (!frames || frames.length < 2) {
        return { count: frames ? frames.length : 0, perMinute: null, spanMinutes: null };
    }
    const sorted = [...frames].sort((a, b) => a.timestamp - b.timestamp);
    const first = sorted[0].timestamp;
    const last = sorted[sorted.length - 1].timestamp;
    if (!first || !last || last <= first) {
        return { count: frames.length, perMinute: null, spanMinutes: null };
    }
    const spanMs = last - first;
    const spanMinutes = spanMs / 60000;
    const perMinute = frames.length / spanMinutes;
    return { count: frames.length, perMinute, spanMinutes };
}

function summarizeMovement(frames, fallbackMovement) {
    if (!frames || frames.length === 0) {
        return fallbackMovement || "unknown";
    }
    const counts = {};
    for (const f of frames) {
        const m = f.movement || fallbackMovement || "unknown";
        counts[m] = (counts[m] || 0) + 1;
    }
    let best = null;
    let bestCount = -1;
    for (const [k, v] of Object.entries(counts)) {
        if (v > bestCount) {
            best = k;
            bestCount = v;
        }
    }
    return best || fallbackMovement || "unknown";
}

function formatSpan(spanMinutes) {
    if (spanMinutes == null || !Number.isFinite(spanMinutes)) return "N/A";
    if (spanMinutes < 1) return `${(spanMinutes * 60).toFixed(0)} sec`;
    if (spanMinutes < 60) return `${spanMinutes.toFixed(1)} min`;
    const hours = spanMinutes / 60;
    return `${hours.toFixed(1)} hr`;
}

function formatPerMinute(perMinute) {
    if (perMinute == null || !Number.isFinite(perMinute)) return "N/A";
    if (perMinute < 0.1) return `${(perMinute * 60).toFixed(2)} /hr`;
    return `${perMinute.toFixed(2)} /min`;
}

// ------------------------------------------------------
// BLE REPORT
// ------------------------------------------------------
function openBleReport(addr) {
    const dev = bleMap[addr];
    if (!dev) return;

    const out = document.getElementById('report-output');
    if (!out) return;

    const lastSeen = dev.lastSeen ? new Date(dev.lastSeen).toLocaleTimeString() : "Unknown";
    const frames = dev.history || [];
    const rssiStats = computeRssiStats(frames);
    const freq = computeFrequency(frames);
    const hourBuckets = bucketByHour(frames);
    const movementSummary = summarizeMovement(frames, dev.movement);

    out.innerHTML = `
        <div style="font-weight:700; margin-bottom:6px;">BLE Device Report</div>

        <div><b>Address:</b> ${addr}</div>
        <div><b>Name:</b> ${dev.name || "(unnamed)"}</div>
        <div><b>Last Seen:</b> ${lastSeen}</div>
        <div><b>Distance:</b> ${dev.distance ? dev.distance.toFixed(2) + " m" : "Unknown"}</div>

        <hr style="margin:8px 0; border-color:#444;">

        <div style="font-weight:600; margin-bottom:4px;">Presence & Frequency</div>
        <div><b>Total sightings:</b> ${freq.count}</div>
        <div><b>Observation span:</b> ${formatSpan(freq.spanMinutes)}</div>
        <div><b>Encounter rate:</b> ${formatPerMinute(freq.perMinute)}</div>

        <div style="margin-top:4px;"><b>Time-of-day pattern:</b></div>
        <div style="font-size:11px; color:#aaa;">${formatHourBuckets(hourBuckets)}</div>

        <hr style="margin:8px 0; border-color:#444;">

        <div style="font-weight:600; margin-bottom:4px;">RSSI & Movement</div>
        <div><b>Last RSSI:</b> ${dev.lastRSSI ?? "N/A"}</div>
        <div><b>RSSI min / max / avg:</b> 
            ${rssiStats.min ?? "N/A"} / 
            ${rssiStats.max ?? "N/A"} / 
            ${rssiStats.avg != null ? rssiStats.avg.toFixed(1) : "N/A"}
        </div>
        <div><b>Dominant movement:</b> ${movementSummary}</div>
    `;
}

// ------------------------------------------------------
// WIFI REPORT
// ------------------------------------------------------
function openBssidReport(bssid) {
    const dev = bssidMap[bssid];
    if (!dev) return;

    const out = document.getElementById('report-output');
    if (!out) return;

    const lastSeen = dev.lastSeen ? new Date(dev.lastSeen).toLocaleTimeString() : "Unknown";
    const frames = dev.history || [];
    const rssiStats = computeRssiStats(frames);
    const freq = computeFrequency(frames);
    const hourBuckets = bucketByHour(frames);
    const movementSummary = summarizeMovement(frames, dev.movement);

    // optional: SSID history if you track it
    const ssidHistory = dev.ssidHistory || [];
    const uniqueSsids = [...new Set(ssidHistory.length ? ssidHistory : [dev.ssid].filter(Boolean))];

    out.innerHTML = `
        <div style="font-weight:700; margin-bottom:6px;">Wi‑Fi Access Point Report</div>

        <div><b>BSSID:</b> ${bssid}</div>
        <div><b>SSID (current):</b> ${dev.ssid || "(hidden)"}</div>
        <div><b>SSID history:</b> ${uniqueSsids.length ? uniqueSsids.join(", ") : "(no history)"}</div>
        <div><b>Last Seen:</b> ${lastSeen}</div>
        <div><b>Distance:</b> ${dev.distance ? dev.distance.toFixed(2) + " m" : "Unknown"}</div>

        <hr style="margin:8px 0; border-color:#444;">

        <div style="font-weight:600; margin-bottom:4px;">Presence & Frequency</div>
        <div><b>Total sightings:</b> ${freq.count}</div>
        <div><b>Observation span:</b> ${formatSpan(freq.spanMinutes)}</div>
        <div><b>Encounter rate:</b> ${formatPerMinute(freq.perMinute)}</div>

        <div style="margin-top:4px;"><b>Time-of-day pattern:</b></div>
        <div style="font-size:11px; color:#aaa;">${formatHourBuckets(hourBuckets)}</div>

        <hr style="margin:8px 0; border-color:#444;">

        <div style="font-weight:600; margin-bottom:4px;">RSSI & Movement</div>
        <div><b>Last RSSI:</b> ${dev.lastRSSI ?? "N/A"}</div>
        <div><b>RSSI min / max / avg:</b> 
            ${rssiStats.min ?? "N/A"} / 
            ${rssiStats.max ?? "N/A"} / 
            ${rssiStats.avg != null ? rssiStats.avg.toFixed(1) : "N/A"}
        </div>
        <div><b>Dominant movement:</b> ${movementSummary}</div>
    `;
}

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
// -------------------------
// Camera players + RTSP/HLS/jsmpeg helpers
// -------------------------
const cameraPlayers = [null, null];
const cameraHls = [null, null];

function setCameraStatus(n, msg, ok=true) {
    const el = document.getElementById(`camera${n}-status`);
    if (!el) return;
    el.innerText = msg;
    el.style.color = ok ? '#0f0' : '#f66';
}

function makeFFmpegHints(rtspUrl) {
    return {
        hls: `ffmpeg -rtsp_transport tcp -i "${rtspUrl}" -c:v copy -c:a aac -f hls -hls_time 2 -hls_list_size 3 -hls_flags delete_segments /var/www/html/stream.m3u8`,
        ws: `# Example (mpeg1 -> ws for jsmpeg):\nffmpeg -rtsp_transport tcp -i "${rtspUrl}" -r 25 -f mpegts -codec:v mpeg1video -s 640x360 -b:v 800k - | your-ws-proxy`
    };
}

function connectCamera(n) {
    const url = document.getElementById(`camera${n}-url`).value.trim();
    const video = document.getElementById(`camera${n}-video`);
    const canvas = document.getElementById(`camera${n}-canvas`);
    const ff = document.getElementById(`camera${n}-ffmpeg`);

    disconnectCamera(n);

    if (!url) {
        setCameraStatus(n, 'No URL provided', false);
        return;
    }

    // RTSP cannot be played directly by the browser — show proxy hints
    if (url.startsWith('rtsp://')) {
        setCameraStatus(n, 'RTSP detected — proxy to HLS or ws-jsmpeg (see hint)', false);
        const hints = makeFFmpegHints(url);
        ff.style.display = 'block';
        ff.textContent = 'HLS proxy (serve .m3u8):\n' + hints.hls + '\n\nWebSocket (jsmpeg) proxy example:\n' + hints.ws;
        return;
    }

    ff.style.display = 'none';

    // WebSocket -> jsmpeg
    if (url.startsWith('ws://') || url.startsWith('wss://')) {
        canvas.style.display = 'block';
        video.style.display = 'none';
        try {
            const player = new JSMpeg.Player(url, {canvas: canvas, autoplay: true, audio: false});
            cameraPlayers[n] = player;
            setCameraStatus(n, 'Playing (ws-jsmpeg)');
        } catch (e) {
            setCameraStatus(n, 'jsmpeg error', false);
            console.error(e);
        }
        return;
    }

    // HLS (.m3u8) or direct http(s)
    canvas.style.display = 'none';
    video.style.display = 'block';

    if (url.includes('.m3u8')) {
        if (Hls && Hls.isSupported()) {
            const hls = new Hls();
            hls.loadSource(url);
            hls.attachMedia(video);
            hls.on(Hls.Events.MANIFEST_PARSED, function() { video.play().catch(()=>{}); });
            cameraHls[n] = hls;
            setCameraStatus(n, 'Playing (HLS)');
            return;
        }
    }

    // fallback: set src directly
    video.src = url;
    video.play().then(() => setCameraStatus(n, 'Playing (native)')).catch((e) => {
        setCameraStatus(n, 'Playback failed', false);
        console.error(e);
    });
}

function disconnectCamera(n) {
    // stop HLS
    if (cameraHls[n]) {
        try { cameraHls[n].destroy(); } catch(e){}
        cameraHls[n] = null;
    }
    // stop jsmpeg
    if (cameraPlayers[n]) {
        try { cameraPlayers[n].destroy(); } catch(e){}
        cameraPlayers[n] = null;
    }
    const video = document.getElementById(`camera${n}-video`);
    if (video) { video.pause(); video.removeAttribute('src'); video.load(); }
    const canvas = document.getElementById(`camera${n}-canvas`);
    if (canvas) { canvas.style.display = 'none'; }
    const ff = document.getElementById(`camera${n}-ffmpeg`);
    if (ff) { ff.style.display = 'none'; }
    setCameraStatus(n, 'Stopped', false);
}

// Status probes for analytics/ws
function updateServiceStatus() {
    // analytics
    fetch('http://localhost:8090/analytics/wifi_timeline?bucket=5&since=60').then(r => {
        document.getElementById('analytics-status').innerText = 'analytics: OK';
    }).catch(() => {
        document.getElementById('analytics-status').innerText = 'analytics: down';
    });
    // websocket
    const wsEl = document.getElementById('ws-status');
    if (ws && ws.readyState === WebSocket.OPEN) wsEl.innerText = 'ws: connected';
    else if (ws && ws.readyState === WebSocket.CONNECTING) wsEl.innerText = 'ws: connecting';
    else wsEl.innerText = 'ws: disconnected';
}
setInterval(updateServiceStatus, 5000);


// ------------------------------
// Blocked devices (load from analytics server)
// ------------------------------
async function fetchBlockedDevices() {
    try {
        const res = await fetch('http://localhost:8090/analytics/blocked');
        if (!res.ok) return;
        const j = await res.json();
        (j.blocked || []).forEach(d => blockedSet.add(d));
        // remove any blocked devices that may already be present locally
        for (const d of Array.from(blockedSet)) {
            if (d.startsWith('wifi:')) {
                const b = d.split(':').slice(1).join(':');
                delete wifiMap[b];
                delete bssidMap[b];
            }
            if (d.startsWith('ble:')) {
                const a = d.split(':').slice(1).join(':');
                delete bleMap[a];
            }
        }
        console.debug('blocked set loaded', blockedSet);
    } catch (e) {
        console.warn('Could not load blocked list:', e);
    }
}

async function sendBlock(device) {
    await fetch('http://localhost:8090/analytics/blocked', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device })
    }).catch(()=>{});
}
async function sendUnblock(device) {
    await fetch('http://localhost:8090/analytics/blocked?device='+encodeURIComponent(device), { method: 'DELETE' }).catch(()=>{});
}

// load blocked list (best-effort)
fetchBlockedDevices();

// ===============================
// SESSION START DISPLAY (STEP 3)
// ===============================
document.getElementById("session-start").innerText =
    "Started: " + new Date(sessionStart).toLocaleTimeString();

const _originalPosition = {}; // stores {parent, nextSibling} for panels moved to modal

function enableEnlarge(id) {
    const panel = document.getElementById(id);
    if (!panel) return;
    panel.style.cursor = "pointer";

    panel.addEventListener("click", (ev) => {
        // ignore clicks when the panel is already inside the modal (prevents overwriting saved original position)
        if (panel.parentNode && panel.parentNode.id === 'enlarge-content') return;
        // prevent accidental enlarge when clicking actionable elements inside panel
        if (ev.target && (ev.target.tagName === 'BUTTON' || ev.target.tagName === 'A' || ev.target.closest('.popup'))) return;

        const modal = document.getElementById("enlarge-modal");
        const content = document.getElementById("enlarge-content");

        // save original position so we can restore later
        _originalPosition[id] = { parent: panel.parentNode, nextSibling: panel.nextSibling };

        // move the real node (preserves canvas pixels & event handlers)
        content.innerHTML = "";
        panel.classList.add('enlarged');
        content.appendChild(panel);

        modal.style.display = "flex";

        // small delay to allow CSS to settle, then trigger redraws where helpful
        requestAnimationFrame(() => {
            // if panel contains known canvases, trigger their redraws
            if (panel.querySelector('#wifiRadar')) drawWiFiRadar();
            if (panel.querySelector('#bleRadar')) drawBLERadar();
            if (panel.querySelector('#waterfall')) {
                // waterfall is continuously updated; resize canvas to match modal and refresh
                resizeWaterfall();
                const ev = { rssi: -80, channel: 1 };
                updateWaterfall(ev);
            }
            if (panel.querySelector('#channelChart')) drawChannelChart();
        });
    });
}

// restore moved panel only when clicking the backdrop (not when clicking inside the content)
document.getElementById("enlarge-modal").addEventListener("click", (e) => {
    const modal = document.getElementById("enlarge-modal");
    if (e.target !== modal) return; // require click on backdrop to close

    const content = document.getElementById("enlarge-content");
    const moved = content.querySelector('.panel.enlarged');
    if (moved) {
        const id = moved.id;
        const orig = _originalPosition[id];
        moved.classList.remove('enlarged');

        // restore to original parent if still available; otherwise fallback to a sensible container
        if (orig && orig.parent && document.contains(orig.parent)) {
            if (orig.nextSibling) orig.parent.insertBefore(moved, orig.nextSibling);
            else orig.parent.appendChild(moved);
        } else {
            const fallbackParent = (id === 'waterfall-panel' || id === 'bssid-panel') ? document.getElementById('bottom-row') : document.getElementById('top-row') || document.body;
            fallbackParent.appendChild(moved);
            console.warn('enlarge-restore: original parent missing, appended to fallback for', id);
        }

        delete _originalPosition[id];

        // force a layout frame then redraw canvases to ensure visibility
        requestAnimationFrame(() => {
            if (moved.querySelector('#wifiRadar')) drawWiFiRadar();
            if (moved.querySelector('#bleRadar')) drawBLERadar();
            if (moved.querySelector('#waterfall')) { resizeWaterfall(); updateWaterfall({ rssi: -80, channel: 1 }); }
            if (moved.querySelector('#channelChart')) drawChannelChart();
        });
    }

    modal.style.display = "none";
});

// ESC key closes modal and restores panel (safe fallback)
window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const modal = document.getElementById('enlarge-modal');
    if (modal.style.display !== 'flex') return;

    const content = document.getElementById('enlarge-content');
    const moved = content.querySelector('.panel.enlarged');
    if (moved) {
        const id = moved.id;
        const orig = _originalPosition[id];
        moved.classList.remove('enlarged');
        if (orig && orig.parent && document.contains(orig.parent)) {
            if (orig.nextSibling) orig.parent.insertBefore(moved, orig.nextSibling);
            else orig.parent.appendChild(moved);
        } else {
            const fallbackParent = (id === 'waterfall-panel' || id === 'bssid-panel') ? document.getElementById('bottom-row') : document.getElementById('top-row') || document.body;
            fallbackParent.appendChild(moved);
        }
        delete _originalPosition[id];
        requestAnimationFrame(() => {
            if (moved.querySelector('#wifiRadar')) drawWiFiRadar();
            if (moved.querySelector('#bleRadar')) drawBLERadar();
            if (moved.querySelector('#waterfall')) { resizeWaterfall(); updateWaterfall({ rssi: -80, channel: 1 }); }
            if (moved.querySelector('#channelChart')) drawChannelChart();
        });
    }

    modal.style.display = 'none';
});

enableEnlarge("ble-panel");
enableEnlarge("wifi-panel");
enableEnlarge("waterfall-panel");
enableEnlarge("channel-panel");

// Camera connect/disconnect bindings
document.getElementById('camera1-connect')?.addEventListener('click', () => connectCamera(1));
document.getElementById('camera1-disconnect')?.addEventListener('click', () => disconnectCamera(1));
document.getElementById('camera2-connect')?.addEventListener('click', () => connectCamera(2));
document.getElementById('camera2-disconnect')?.addEventListener('click', () => disconnectCamera(2));

// Clean up players on page unload
window.addEventListener('beforeunload', () => { disconnectCamera(1); disconnectCamera(2); });

