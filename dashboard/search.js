const ANALYTICS_BASE = 'http://localhost:8090';
const queryInput = document.getElementById('searchQuery');
const messageEl = document.getElementById('searchMessage');
const resultsEl = document.getElementById('searchResults');
const searchButton = document.getElementById('searchSubmit');
const clearButton = document.getElementById('searchClear');

function setMessage(message, isError = false) {
    messageEl.textContent = message;
    messageEl.style.color = isError ? '#f66' : '#ccc';
}

function formatTimestamp(value) {
    if (value == null) return 'N/A';
    const num = Number(value);
    if (!Number.isFinite(num)) return String(value);
    return new Date(num * 1000).toLocaleString();
}

function renderTable(rows, columns) {
    if (!rows || rows.length === 0) {
        return '<div style="color:#ccc; margin-top:6px;">No matches in this category.</div>';
    }

    const headers = columns.filter((col) => rows.some((row) => row[col] != null));
    const headRow = headers.map((col) => `<th>${col}</th>`).join('');
    const bodyRows = rows.slice(0, 50).map((row) => {
        const cells = headers.map((col) => {
            const value = row[col];
            if (col === 'timestamp') {
                return `<td>${formatTimestamp(value)}</td>`;
            }
            return `<td>${value != null ? value : ''}</td>`;
        }).join('');
        return `<tr>${cells}</tr>`;
    }).join('');

    return `
        <div class="search-section">
          <div style="margin-bottom:8px; color:#ccc;">Showing ${Math.min(rows.length, 50)} of ${rows.length} matches.</div>
          <table>
            <thead><tr>${headRow}</tr></thead>
            <tbody>${bodyRows}</tbody>
          </table>
        </div>
    `;
}

function renderSummary(summary) {
    if (!summary) {
        return '';
    }

    const lines = [];
    lines.push(`<div><strong>Total matches:</strong> ${summary.total_matches}</div>`);
    if (summary.frequency_per_hour != null) {
        lines.push(`<div><strong>Frequency:</strong> ${summary.frequency_per_hour} events/hour</div>`);
    }
    if (summary.first_seen != null && summary.last_seen != null) {
        lines.push(`<div><strong>First seen:</strong> ${formatTimestamp(summary.first_seen)} &nbsp; <strong>Last seen:</strong> ${formatTimestamp(summary.last_seen)}</div>`);
    }
    if (summary.behavior) {
        lines.push(`<div><strong>Behavior predictability:</strong> ${summary.behavior.field}=${summary.behavior.dominant} (${Math.round(summary.behavior.score * 100)}%)</div>`);
    }
    if (summary.seen_with && summary.seen_with.length) {
        const seenWith = summary.seen_with.map((item) => `${item.device} (${item.count})`).join(', ');
        lines.push(`<div><strong>Seen with:</strong> ${seenWith}</div>`);
    }
    if (summary.convoys && summary.convoys.length) {
        const convoyLines = summary.convoys.map((convoy) => `${convoy.members.join(', ')} [corr=${convoy.correlation.toFixed(2)}]`).join('<br>');
        lines.push(`<div><strong>Convoy matches:</strong><br>${convoyLines}</div>`);
    }

    return `
      <div class="search-section">
        <h2>Search summary</h2>
        <div style="color:#ccc; font-size:14px; line-height:1.5;">${lines.join('<br>')}</div>
      </div>
    `;
}

function renderResults(data, query) {
    if (!data) {
        resultsEl.innerHTML = '';
        return;
    }

    const wifiColumns = ['timestamp', 'bssid', 'ssid', 'channel', 'rssi', 'movement', 'distance', 'frame_type'];
    const bleColumns = ['timestamp', 'addr', 'name', 'movement', 'rssi', 'distance_m', 'frame_type'];

    const summaryHtml = renderSummary(data.summary);

    const wifiHtml = `
      <div class="search-section">
        <h2>WiFi results (${data.wifi.count})</h2>
        ${renderTable(data.wifi.results, wifiColumns)}
      </div>
    `;

    const bleHtml = `
      <div class="search-section">
        <h2>BLE results (${data.ble.count})</h2>
        ${renderTable(data.ble.results, bleColumns)}
      </div>
    `;

    resultsEl.innerHTML = `
      <div class="search-summary">Search query: <strong>${query}</strong></div>
      ${summaryHtml}
      ${wifiHtml}
      ${bleHtml}
    `;
}

async function fetchJSON(path) {
    const response = await fetch(path, { method: 'GET' });
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
    }
    return await response.json();
}

async function executeSearch(query) {
    if (!query || !query.trim()) {
        setMessage('Enter a search term to look up MAC, BSSID, SSID, BLE name, or metadata.');
        resultsEl.innerHTML = '';
        return;
    }
    setMessage('Searching…');
    resultsEl.innerHTML = '';

    try {
        const url = `${ANALYTICS_BASE}/analytics/search?q=${encodeURIComponent(query.trim())}&since=259200&limit=200`;
        const data = await fetchJSON(url);
        renderResults(data, query.trim());
        setMessage(`Search completed: ${data.wifi.count + data.ble.count} total matches.`);
    } catch (error) {
        setMessage(`Search failed: ${error.message}`, true);
        resultsEl.innerHTML = '';
        console.error('Search error:', error);
    }
}

function getInitialQuery() {
    const params = new URLSearchParams(window.location.search);
    return params.get('q') || '';
}

searchButton.addEventListener('click', () => executeSearch(queryInput.value));
clearButton.addEventListener('click', () => {
    queryInput.value = '';
    setMessage('Enter a search term to begin.');
    resultsEl.innerHTML = '';
    window.history.replaceState({}, '', 'search.html');
});
queryInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
        event.preventDefault();
        executeSearch(queryInput.value);
    }
});

window.addEventListener('DOMContentLoaded', () => {
    const q = getInitialQuery();
    if (q) {
        queryInput.value = q;
        executeSearch(q);
    } else {
        setMessage('Enter a search term to begin.');
    }
});
