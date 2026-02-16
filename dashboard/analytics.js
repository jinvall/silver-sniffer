const ANALYTICS_BASE = "http://localhost:8090";
let wifiChart = null;
let bleChart = null;

async function fetchJSON(path) {
    const res = await fetch(ANALYTICS_BASE + path);
    if (!res.ok) throw new Error("HTTP " + res.status);
    return await res.json();
}

function tsToLabel(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString();
}

function getConvoyBucket() {
    const sel = document.getElementById("convoyBucket");
    return sel ? sel.value : 30;
}

function getTimeWindow() {
    const sel = document.getElementById("timeWindow");
    return sel ? sel.value : 3600;
}

async function buildWifiTimeline() {
    const data = await fetchJSON(`/analytics/wifi_timeline?bucket=5&since=${getTimeWindow()}`);
    const labels = data.buckets.map(b => tsToLabel(b.start_ts));
    const counts = data.buckets.map(b => b.count);

    const ctx = document.getElementById("wifiTimeline").getContext("2d");
   if (wifiChart) wifiChart.destroy();
    wifiChart = new Chart(ctx, {

        type: "line",
        data: {
            labels,
            datasets: [{
                label: "WiFi frames / 5s",
                data: counts,
                borderColor: "#66ccff",
                backgroundColor: "rgba(102,204,255,0.2)",
                tension: 0.2,
            }]
        },
        options: {
            scales: {
                x: { ticks: { color: "#ccc" } },
                y: { ticks: { color: "#ccc" } }
            },
            plugins: {
                legend: { labels: { color: "#ccc" } }
            }
        }
    });
}

async function buildBleTimeline() {
    const data = await fetchJSON(`/analytics/ble_timeline?bucket=5&since=${getTimeWindow()}`);
    const labels = data.buckets.map(b => tsToLabel(b.start_ts));
    const counts = data.buckets.map(b => b.count);

    const ctx = document.getElementById("bleTimeline").getContext("2d");
    if (bleChart) bleChart.destroy();
    bleChart = new Chart(ctx, {
        type: "line",
        data: {
            labels,
            datasets: [{
                label: "BLE frames / 5s",
                data: counts,
                borderColor: "#00ff99",
                backgroundColor: "rgba(0,255,153,0.2)",
                tension: 0.2,
            }]
        },
        options: {
            scales: {
                x: { ticks: { color: "#ccc" } },
                y: { ticks: { color: "#ccc" } }
            },
            plugins: {
                legend: { labels: { color: "#ccc" } }
            }
        }
    });
}

async function buildWifiHeatmap() {
    const data = await fetchJSON(`/analytics/wifi_heatmap?bucket=30&since=${getTimeWindow()}`);
    const cells = data.cells;

    if (!cells.length) return;

    const buckets = [...new Set(cells.map(c => c.start_ts))].sort((a,b) => a - b);
    const channels = [...new Set(cells.map(c => c.channel))].sort((a,b) => a - b);

    const matrix = buckets.map(() => channels.map(() => 0));
    const maxCount = { value: 1 };

    for (const c of cells) {
        const bi = buckets.indexOf(c.start_ts);
        const ci = channels.indexOf(c.channel);
        if (bi >= 0 && ci >= 0) {
            matrix[bi][ci] = c.count;
            if (c.count > maxCount.value) maxCount.value = c.count;
        }
    }

    const ctx = document.getElementById("wifiHeatmap").getContext("2d");
    const width = 440;
    const height = 220;
    ctx.canvas.width = width;
    ctx.canvas.height = height;

    ctx.clearRect(0, 0, width, height);

    const cellW = width / buckets.length;
    const cellH = height / channels.length;

    function colorFor(v) {
        const t = v / maxCount.value;
        const r = Math.floor(255 * t);
        const g = Math.floor(80 * (1 - t));
        const b = 40;
        return `rgb(${r},${g},${b})`;
    }

    for (let bi = 0; bi < buckets.length; bi++) {
        for (let ci = 0; ci < channels.length; ci++) {
            const v = matrix[bi][ci];
            ctx.fillStyle = v ? colorFor(v) : "#111";
            ctx.fillRect(bi * cellW, ci * cellH, cellW, cellH);
        }
    }

    ctx.strokeStyle = "#333";
    for (let bi = 0; bi <= buckets.length; bi++) {
        ctx.beginPath();
        ctx.moveTo(bi * cellW, 0);
        ctx.lineTo(bi * cellW, height);
        ctx.stroke();
    }
    for (let ci = 0; ci <= channels.length; ci++) {
        ctx.beginPath();
        ctx.moveTo(0, ci * cellH);
        ctx.lineTo(width, ci * cellH);
        ctx.stroke();
    }

    ctx.fillStyle = "#ccc";
    ctx.font = "10px monospace";
    ctx.textAlign = "center";

    const step = Math.max(1, Math.floor(buckets.length / 8));
    for (let bi = 0; bi < buckets.length; bi += step) {
        const label = new Date(buckets[bi] * 1000).toLocaleTimeString();
        ctx.fillText(label, bi * cellW + cellW / 2, height - 4);
    }

    ctx.textAlign = "right";
    for (let ci = 0; ci < channels.length; ci++) {
        const ch = channels[ci];
        ctx.fillText(ch.toString(), width - 4, ci * cellH + cellH / 2 + 3);
    }
}
async function buildConvoys() {
    const since = getTimeWindow();
    const bucket = getConvoyBucket();

    const out = document.getElementById("convoyOutput");
    out.textContent = "Loading convoys…";

    try {
        const data = await fetchJSON(`/analytics/convoys?bucket=${bucket}&since=${since}`);

        if (!data.convoys || data.convoys.length === 0) {
            out.textContent = "No convoys detected in this time window.";
            return;
        }

        let text = "";
        for (const c of data.convoys) {
            text += `Members: ${c.members.join(", ")}\n`;
            text += `Correlation: ${(c.correlation * 100).toFixed(1)}%\n`;
            text += `Buckets Compared: ${c.buckets_compared}\n`;
            text += `----------------------------------------\n`;
        }

        out.textContent = text;

    } catch (e) {
        out.textContent = "Convoy error: " + e;
        console.error("Convoy error:", e);
    }
}

(async function main() {
    try {
        await buildWifiTimeline();
    } catch (e) {
        console.error("WiFi timeline error:", e);
    }

    try {
        await buildBleTimeline();
    } catch (e) {
        console.error("BLE timeline error:", e);
    }

    try {
        await buildWifiHeatmap();
    } catch (e) {
        console.error("WiFi heatmap error:", e);
    }

    try {
        await buildConvoys();
    } catch (e) {
        console.error("Convoy error:", e);
    }


})();
document.getElementById("timeWindow").addEventListener("change", main);
document.getElementById("convoyBucket").addEventListener("change", buildConvoys);

