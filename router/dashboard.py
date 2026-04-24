"""Dashboard: embedded HTML dashboard for usage stats."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LLM Gateway Dashboard</title>
<script src="https://cdn.bootcdn.net/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0f172a; --card: #1e293b; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #3b82f6;
    --green: #22c55e; --amber: #f59e0b; --red: #ef4444; --purple: #a855f7;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  .header { padding: 20px 32px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
  .header h1 { font-size: 20px; font-weight: 600; }
  .header .refresh { background: var(--accent); color: #fff; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; }
  .header .refresh:hover { opacity: 0.85; }
  .container { max-width: 1280px; margin: 0 auto; padding: 24px 32px; }

  /* Stats cards */
  .stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 20px; }
  .stat-card .label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
  .stat-card .value { font-size: 28px; font-weight: 700; }
  .stat-card .value.blue { color: var(--accent); }
  .stat-card .value.green { color: var(--green); }
  .stat-card .value.amber { color: var(--amber); }
  .stat-card .value.purple { color: var(--purple); }

  /* Chart section */
  .charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  .chart-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 20px; }
  .chart-card h3 { font-size: 14px; color: var(--muted); margin-bottom: 16px; font-weight: 500; }
  .chart-wrap { position: relative; height: 260px; }

  /* Model table */
  .table-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 20px; margin-bottom: 24px; }
  .table-card h3 { font-size: 14px; color: var(--muted); margin-bottom: 16px; font-weight: 500; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; padding: 10px 12px; border-bottom: 1px solid var(--border); }
  td { padding: 12px; font-size: 14px; border-bottom: 1px solid var(--border); }
  tr:last-child td { border-bottom: none; }
  .mono { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .badge.ark { background: #1e3a5f; color: #60a5fa; }
  .badge.kimi { background: #3b1f4a; color: #c084fc; }
  .badge.minimax { background: #1a3d2e; color: #4ade80; }

  /* Recent logs */
  .logs-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 20px; }
  .logs-card h3 { font-size: 14px; color: var(--muted); margin-bottom: 16px; font-weight: 500; }
  .log-row { display: grid; grid-template-columns: 140px 140px 80px 80px 80px 80px 80px; gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; align-items: center; }
  .log-row:last-child { border-bottom: none; }
  .log-header { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }

  @media (max-width: 768px) {
    .charts-row { grid-template-columns: 1fr; }
    .container { padding: 16px; }
    .log-row { grid-template-columns: 1fr 1fr; }
  }

  .loading { text-align: center; padding: 40px; color: var(--muted); }
</style>
</head>
<body>
  <div class="header">
    <h1>🤖 LLM Gateway Dashboard</h1>
    <button class="refresh" onclick="loadAll()">↻ 刷新</button>
  </div>
  <div class="container">
    <!-- Stats cards -->
    <div class="stats-row" id="statsRow">
      <div class="stat-card"><div class="label">总请求数</div><div class="value blue" id="totalReqs">-</div></div>
      <div class="stat-card"><div class="label">输入 Tokens</div><div class="value green" id="totalPrompt">-</div></div>
      <div class="stat-card"><div class="label">输出 Tokens</div><div class="value amber" id="totalComp">-</div></div>
      <div class="stat-card"><div class="label">缓存命中 Tokens</div><div class="value purple" id="totalCached">-</div></div>
    </div>

    <!-- Charts -->
    <div class="charts-row">
      <div class="chart-card">
        <h3>每日请求数 (按模型)</h3>
        <div class="chart-wrap"><canvas id="dailyChart"></canvas></div>
      </div>
      <div class="chart-card">
        <h3>Token 消耗分布</h3>
        <div class="chart-wrap"><canvas id="tokenChart"></canvas></div>
      </div>
    </div>

    <!-- Model summary table -->
    <div class="table-card">
      <h3>模型用量汇总</h3>
      <table>
        <thead><tr>
          <th>模型</th><th>渠道</th><th>请求数</th><th>输入 Tokens</th><th>输出 Tokens</th><th>缓存 Tokens</th><th>平均延迟</th>
        </tr></thead>
        <tbody id="modelTable"></tbody>
      </table>
    </div>

    <!-- Recent logs -->
    <div class="logs-card">
      <h3>最近请求</h3>
      <div class="log-row log-header">
        <span>时间</span><span>模型</span><span>渠道</span><span>输入</span><span>输出</span><span>缓存</span><span>延迟</span>
      </div>
      <div id="logsList"></div>
    </div>
  </div>

<script>
const COLORS = {
  ark: '#3b82f6', kimi: '#a855f7', minimax: '#22c55e',
  'glm-5-1': '#3b82f6', 'doubao-seed-2-0-pro': '#60a5fa',
  'doubao-seed-2-0-lite': '#93c5fd', 'kimi-for-coding': '#a855f7',
  'MiniMax-M2.7-highspeed': '#22c55e'
};
function getColor(key) { return COLORS[key] || '#64748b'; }

function fmt(n) {
  if (n === null || n === undefined) return '-';
  if (n >= 1000000) return (n/1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n/1000).toFixed(1) + 'K';
  return n.toString();
}

function fmtMs(ms) {
  if (ms === null || ms === undefined) return '-';
  return Math.round(ms) + 'ms';
}

function fmtTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  return d.toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit'});
}

async function fetchJSON(url) {
  const r = await fetch(url);
  return r.json();
}

let dailyChartInstance = null;
let tokenChartInstance = null;

async function loadStats() {
  const [totals, summary] = await Promise.all([
    fetchJSON('/api/stats/totals'),
    fetchJSON('/api/stats/summary')
  ]);

  const t = totals.data || {};
  document.getElementById('totalReqs').textContent = fmt(t.total_requests || 0);
  document.getElementById('totalPrompt').textContent = fmt(t.total_prompt_tokens || 0);
  document.getElementById('totalComp').textContent = fmt(t.total_completion_tokens || 0);
  document.getElementById('totalCached').textContent = fmt(t.total_cached_tokens || 0);

  // Model table
  const tbody = document.getElementById('modelTable');
  tbody.innerHTML = '';
  const rows = summary.data || [];
  for (const r of rows) {
    const channelClass = r.grp_channel || r.channel || '';
    const chBadge = channelClass ? `<span class="badge ${channelClass}">${channelClass}</span>` : '-';
    const model = r.grp_model || r.grp || r.model || '-';
    tbody.innerHTML += `<tr>
      <td class="mono">${model}</td>
      <td>${chBadge}</td>
      <td class="mono">${fmt(r.request_count)}</td>
      <td class="mono">${fmt(r.total_prompt_tokens)}</td>
      <td class="mono">${fmt(r.total_completion_tokens)}</td>
      <td class="mono">${fmt(r.total_cached_tokens)}</td>
      <td class="mono">${fmtMs(r.avg_latency_ms)}</td>
    </tr>`;
  }
}

async function loadDaily() {
  const resp = await fetchJSON('/api/stats/daily');
  const rows = resp.data || [];

  // Group by date
  const dateMap = {};
  const modelSet = new Set();
  for (const r of rows) {
    const date = r.date;
    const model = r.model;
    modelSet.add(model);
    if (!dateMap[date]) dateMap[date] = {};
    dateMap[date][model] = r.request_count;
  }

  const dates = Object.keys(dateMap).sort();
  const models = [...modelSet];

  const datasets = models.map(m => ({
    label: m,
    data: dates.map(d => dateMap[d]?.[m] || 0),
    backgroundColor: getColor(m),
    borderRadius: 3,
  }));

  if (dailyChartInstance) dailyChartInstance.destroy();
  dailyChartInstance = new Chart(document.getElementById('dailyChart'), {
    type: 'bar',
    data: { labels: dates, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } },
      scales: {
        x: { stacked: true, ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: '#1e293b' } },
        y: { stacked: true, ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: '#1e293b' } }
      }
    }
  });
}

async function loadTokenChart() {
  const resp = await fetchJSON('/api/stats/summary?group_by=model');
  const rows = resp.data || [];

  const labels = rows.map(r => r.grp_model || r.grp || r.model || '-');
  const promptData = rows.map(r => r.total_prompt_tokens || 0);
  const compData = rows.map(r => r.total_completion_tokens || 0);
  const cachedData = rows.map(r => r.total_cached_tokens || 0);

  if (tokenChartInstance) tokenChartInstance.destroy();
  tokenChartInstance = new Chart(document.getElementById('tokenChart'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: '输入', data: promptData, backgroundColor: '#22c55e', borderRadius: 3 },
        { label: '输出', data: compData, backgroundColor: '#f59e0b', borderRadius: 3 },
        { label: '缓存命中', data: cachedData, backgroundColor: '#a855f7', borderRadius: 3 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } },
      scales: {
        x: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: '#1e293b' } },
        y: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: '#1e293b' } }
      }
    }
  });
}

async function loadLogs() {
  const resp = await fetchJSON('/api/stats/logs?limit=20');
  const rows = resp.data || [];
  const container = document.getElementById('logsList');
  container.innerHTML = '';
  for (const r of rows) {
    const ch = r.channel || '';
    container.innerHTML += `<div class="log-row">
      <span style="color:var(--muted)">${fmtTime(r.timestamp)}</span>
      <span class="mono">${r.model}</span>
      <span><span class="badge ${ch}">${ch}</span></span>
      <span class="mono">${fmt(r.prompt_tokens)}</span>
      <span class="mono">${fmt(r.completion_tokens)}</span>
      <span class="mono">${fmt(r.cached_tokens)}</span>
      <span class="mono">${fmtMs(r.latency_ms)}</span>
    </div>`;
  }
}

async function loadAll() {
  document.getElementById('totalReqs').textContent = '...';
  try {
    await Promise.all([loadStats(), loadDaily(), loadTokenChart(), loadLogs()]);
  } catch(e) {
    console.error(e);
  }
}

loadAll();
</script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML
