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

// set of blocked devices (persisted server-side)
const blockedSet = new Set();


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

    // respect blocked list (client-side filtering)
    if (blockedSet.has(`wifi:${key}`)) return;

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

// ======================================================
// ANALYTICS ENGINE
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
        <div><b>Last RSSI:</b> ${dev.rssi ?? "N/A"}</div>
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

