"""Dashboard: mobile-first, no external CDN, pure CSS charts."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>LLM Gateway Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🤖</text></svg>">
<style>
:root {
  --bg: #f0f4f8; --card: #ffffff; --card-hover: #f8fafc; --border: #e2e8f0;
  --text: #1e293b; --muted: #64748b; --accent: #3b82f6;
  --green: #10b981; --amber: #f59e0b; --red: #ef4444; --purple: #8b5cf6;
  --cyan: #06b6d4; --pink: #ec4899;
  --shadow-sm: 0 1px 3px rgba(0,0,0,0.06);
  --shadow-md: 0 4px 14px rgba(0,0,0,0.08);
  --radius: 12px;
  --transition: 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; -webkit-text-size-adjust: 100%; }

/* Header */
.header { padding: 16px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; background: var(--bg); z-index: 10; }
.header h1 { font-size: 18px; font-weight: 600; }
.header .refresh { background: var(--accent); color: #fff; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; white-space: nowrap; display: inline-flex; align-items: center; gap: 6px; transition: background var(--transition), transform var(--transition); }
.header .refresh:hover { background: #2563eb; }
.header .refresh:active { transform: scale(0.97); }
.header .refresh .icon { display: inline-block; transition: transform 0.6s ease; }
.header .refresh.spinning .icon { transform: rotate(360deg); }

/* Container */
.container { max-width: 1280px; margin: 0 auto; padding: 16px; }

/* Tab nav */
.tab-nav { display: flex; gap: 0; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
.tab-btn { background: none; border: none; color: var(--muted); padding: 10px 16px; cursor: pointer; font-size: 14px; border-bottom: 2px solid transparent; margin-bottom: -1px; white-space: nowrap; transition: color 0.15s; -webkit-tap-highlight-color: transparent; }
.tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }
.tab-pane { display: none; }
.tab-pane.active { display: block; }

/* Stat cards */
.stats-row { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-bottom: 0; align-items: stretch; }
@media (min-width: 768px) {
  .stats-row { grid-template-columns: repeat(4, 1fr); }
}
.stat-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; position: relative; overflow: hidden; height: 100px; display: flex; flex-direction: column; justify-content: center; transition: transform var(--transition), border-color var(--transition), box-shadow var(--transition); }
.stat-card:hover { transform: translateY(-2px); border-color: var(--accent); box-shadow: var(--shadow-md); }
.stat-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: var(--accent); opacity: 0.7; }
.stat-card:nth-child(2)::before { background: var(--green); }
.stat-card:nth-child(3)::before { background: var(--amber); }
.stat-card:nth-child(4)::before { background: var(--purple); }
.stat-card .label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
.stat-card .value { font-size: 24px; font-weight: 700; font-variant-numeric: tabular-nums; }
.stat-card .trend { font-size: 11px; color: var(--muted); margin-top: 4px; }
.stat-card .trend.up { color: var(--green); }
.stat-card .trend.down { color: var(--red); }
.stat-card .value.blue { color: var(--accent); }
.stat-card .value.green { color: var(--green); }
.stat-card .value.amber { color: var(--amber); }
.stat-card .value.purple { color: var(--purple); }

/* Bar chart (CSS only) */
.chart-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; display: flex; flex-direction: column; height: 360px; transition: border-color var(--transition); }
@media (max-width: 767px) {
  .chart-card { height: 320px; }
}
.chart-card:hover { border-color: var(--accent); }
.chart-card h3 { font-size: 13px; color: var(--text); margin-bottom: 12px; font-weight: 600; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; flex-shrink: 0; }
.bar-group { margin-bottom: 10px; }
.bar-label { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; font-size: 12px; }
.bar-label .name { color: var(--text); max-width: 60%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.bar-label .val { color: var(--muted); font-family: 'SF Mono', 'Fira Code', monospace; font-size: 11px; font-variant-numeric: tabular-nums; }
.bar-track { height: 8px; background: var(--border); border-radius: 4px; overflow: hidden; }
.bar-fill { height: 100%; border-radius: 4px; transition: width 0.5s ease, filter var(--transition); min-width: 2px; }
.bar-group:hover .bar-fill { filter: brightness(1.15); }

/* Chart content area */
.chart-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding-right: 4px;
  /* 标准 CSS 滚动条属性（Firefox + Chrome 121+） */
  scrollbar-width: thin;
  scrollbar-color: var(--accent) var(--border);
}
.chart-scroll::-webkit-scrollbar { width: 10px; }
.chart-scroll::-webkit-scrollbar-track { background: var(--border); border-radius: 5px; }
.chart-scroll::-webkit-scrollbar-thumb { background: var(--accent); border-radius: 5px; border: 2px solid var(--card); min-height: 30px; }
.chart-scroll::-webkit-scrollbar-thumb:hover { background: #1d4ed8; }

/* Scrollable table area */
.table-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: auto;
  padding-right: 4px;
}
.table-scroll::-webkit-scrollbar { height: 4px; width: 4px; }
.table-scroll::-webkit-scrollbar-track { background: transparent; }
.table-scroll::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
.table-scroll::-webkit-scrollbar-thumb:hover { background: var(--muted); }

/* Overview tab sections with consistent gap */
.overview-sections {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.donut-wrap { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
.donut { width: 120px; height: 120px; border-radius: 50%; flex-shrink: 0; }
.donut-legend { flex: 1; min-width: 140px; }
.donut-legend-item { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 12px; }
.donut-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }

/* Day buttons */
.day-btns { display: flex; gap: 4px; }
.day-btn { background: var(--border); border: none; color: var(--muted); padding: 3px 8px; border-radius: 4px; cursor: pointer; font-size: 10px; -webkit-tap-highlight-color: transparent; transition: background var(--transition), color var(--transition); }
.day-btn:hover { color: var(--text); }
.day-btn.active { background: var(--accent); color: #fff; }

/* Model table */
.table-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; overflow: hidden; display: flex; flex-direction: column; height: 420px; transition: border-color var(--transition); }
@media (max-width: 767px) {
  .table-card { height: 360px; }
}
.table-card:hover { border-color: var(--accent); }
.table-card h3 { font-size: 13px; color: var(--text); margin-bottom: 12px; font-weight: 600; flex-shrink: 0; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; padding: 10px; border-bottom: 1px solid var(--border); font-weight: 600; position: sticky; top: 0; background: var(--card); z-index: 1; }
td { padding: 10px; font-size: 13px; border-bottom: 1px solid var(--border); font-variant-numeric: tabular-nums; }
td.right { text-align: right; }
tbody tr { transition: background var(--transition); }
tbody tr:hover { background: rgba(59,130,246,0.05); }
tr:last-child td { border-bottom: none; }
.mono { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 10px; font-weight: 600; }
.badge.ark { background: #eff6ff; color: #2563eb; }
.badge.kimi { background: #f5f3ff; color: #7c3aed; }
.badge.minimax { background: #ecfdf5; color: #059669; }
.badge.deepseek { background: #eff6ff; color: #1d4ed8; }
.badge.xiaomi { background: #fef2f2; color: #dc2626; }
.badge.longcat { background: #fffbeb; color: #d97706; }
.badge.aliyuncs { background: #f5f3ff; color: #7c3aed; }
.badge.dashscope { background: #f5f3ff; color: #7c3aed; }

/* Provider config cards */
.config-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; transition: transform var(--transition), border-color var(--transition), box-shadow var(--transition); }
.config-card:hover { transform: translateY(-2px); border-color: var(--accent); box-shadow: var(--shadow-md); }
.config-card-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
.config-card-provider { font-weight:600; font-size:14px; color:var(--accent); text-transform:capitalize; }
.config-card-badge { font-size:11px; padding:2px 8px; border-radius:10px; font-weight:600; }
.config-card-badge.ok { background:#1a3d2e; color:#4ade80; }
.config-card-badge.warn { background:#3d2a1a; color:#fb923c; }
.config-card-models { display:flex; flex-direction:column; gap:6px; }
.config-model-row { display:flex; justify-content:space-between; align-items:center; padding:6px 10px; background:rgba(255,255,255,0.03); border-radius:6px; }
.config-model-info { display:flex; align-items:center; gap:8px; min-width:0; flex:1; }
.config-model-name { font-family:'SF Mono','Fira Code',monospace; font-size:12px; color:var(--text); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:130px; }
.config-status-dot { width:7px; height:7px; border-radius:50%; flex-shrink:0; }
.config-status-dot.on { background:var(--green); }
.config-status-dot.off { background:var(--red); }

/* Toggle switch */
.toggle-switch { position: relative; display: inline-block; width: 36px; height: 20px; flex-shrink: 0; }
.toggle-switch input { opacity: 0; width: 0; height: 0; }
.toggle-slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background: var(--border); border-radius: 20px; transition: 0.2s; }
.toggle-slider::before { content: ''; position: absolute; height: 16px; width: 16px; left: 2px; bottom: 2px; background: #fff; border-radius: 50%; transition: 0.2s; box-shadow: 0 1px 2px rgba(0,0,0,0.15); }
.toggle-switch input:checked + .toggle-slider { background: var(--green); }
.toggle-switch input:checked + .toggle-slider::before { transform: translateX(16px); }

/* Loading */
.loading { text-align: center; padding: 40px; color: var(--muted); font-size: 14px; }

/* Status indicator */
.status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.status-dot.on { background: var(--green); }
.status-dot.off { background: var(--red); }
.status-dot.draining { background: var(--amber); }

/* Two-column layout for charts on desktop */
@media (min-width: 768px) {
  .charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: stretch; }
  .charts-row .chart-card { margin-bottom: 0; }
}

/* Tab pane fade-in */
.tab-pane { animation: fadeIn 0.25s ease; }
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* Stat-card staggered entrance (one-shot on page load) */
.stat-card { animation: slideUp 0.4s ease both; }
.stat-card:nth-child(1) { animation-delay: 0.05s; }
.stat-card:nth-child(2) { animation-delay: 0.10s; }
.stat-card:nth-child(3) { animation-delay: 0.15s; }
.stat-card:nth-child(4) { animation-delay: 0.20s; }
@keyframes slideUp {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* Logs row hover (for the recent-requests card) */
.log-row { transition: background var(--transition); }
.log-row:hover { background: rgba(59,130,246,0.06); }
.log-row .status-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 4px; vertical-align: middle; }
.log-row .status-dot.ok { background: var(--green); }
.log-row .status-dot.err { background: var(--red); }

/* Honor user motion preference */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
</style>
</head>
<body>
<div class="header">
  <div style="display:flex;align-items:center;gap:12px;">
    <h1>🤖 LLM Gateway</h1>
    <span id="lastUpdate" style="font-size:11px;color:var(--muted);"></span>
  </div>
  <div style="display:flex;align-items:center;gap:8px;">
    <span id="refreshStatus" style="font-size:11px;color:var(--muted);"></span>
    <button class="refresh" id="refreshBtn" onclick="refreshAll()"><span class="icon">↻</span><span>刷新</span></button>
  </div>
</div>
<div class="container">
  <div class="tab-nav">
    <button class="tab-btn active" onclick="switchTab('overview')">概览</button>
    <button class="tab-btn" onclick="switchTab('config')">配置</button>
  </div>

  <div id="tab-overview" class="tab-pane active">
    <div class="overview-sections">
    <div class="stats-row" id="statsRow">
      <div class="stat-card"><div class="label">近30天请求数</div><div class="value blue" id="totalReqs">-</div></div>
      <div class="stat-card"><div class="label">近30天输入 Tokens</div><div class="value green" id="totalPrompt">-</div></div>
      <div class="stat-card"><div class="label">近30天输出 Tokens</div><div class="value amber" id="totalComp">-</div></div>
      <div class="stat-card"><div class="label">近30天缓存命中</div><div class="value purple" id="totalCached">-</div></div>
    </div>

    <div class="charts-row">
      <!-- Daily requests bar chart -->
      <div class="chart-card">
        <h3>
          <span>每日请求数 (按模型)</span>
          <span class="day-btns" id="dailyBtns">
            <button class="day-btn active" data-days="7">近7天</button>
            <button class="day-btn" data-days="3">近3天</button>
            <button class="day-btn" data-days="1">今天</button>
          </span>
        </h3>
        <div class="chart-scroll" id="dailyBars"><div class="loading">加载中...</div></div>
      </div>

      <!-- Token distribution bar chart -->
      <div class="chart-card">
        <h3>
          <span>Token 消耗分布</span>
          <span class="day-btns" id="tokenBtns">
            <button class="day-btn active" data-days="7">近7天</button>
            <button class="day-btn" data-days="3">近3天</button>
            <button class="day-btn" data-days="1">今天</button>
          </span>
        </h3>
        <div class="chart-scroll" id="tokenBars"><div class="loading">加载中...</div></div>
      </div>
    </div>

    <!-- Model summary table -->
    <div class="table-card">
      <h3>模型用量汇总</h3>
      <div class="table-scroll">
        <table>
          <thead><tr>
            <th>模型</th><th>渠道</th><th>请求数</th><th>输入</th><th>输出</th><th>缓存</th><th>延迟</th>
          </tr></thead>
          <tbody id="modelTable"></tbody>
        </table>
      </div>
    </div>

    <!-- Recent logs -->
    <div class="table-card">
      <h3>最近请求</h3>
      <div class="table-scroll" id="logsList"><div class="loading">加载中...</div></div>
    </div>
    </div>
  </div>

  <div id="tab-config" class="tab-pane">
    <div id="configCards" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;"></div>
  </div>
</div>

<script>
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
function getColor(key) {
  const m = {
    'glm-latest': '#3b82f6', 'ark-code-latest': '#93c5fd',
    'doubao-seed-2-0-lite': '#67e8f9',
    'MiniMax-M2.7-highspeed': '#34d399', 'mimo-v2.6-pro': '#fbbf24',
    'mimo-v2.6-flash': '#f87171', 'LongCat-Flash-Lite': '#a78bfa',
    'LongCat-2.0': '#c4b5fd', 'LongCat-Flash-Chat': '#fcd34d',
    'glm-5-1': '#60a5fa', 'kimi-for-coding': '#f472b6',
    'MiniMax-M3': '#6ee7b7',
    'Qwen3.8-27B': '#8b5cf6',  // 紫色，区分其他模型
  };
  return m[key] || '#cbd5e1';
}

async function fetchJSON(url) {
  const headers = { 'Content-Type': 'application/json' };
  // 任何调用都默认带 API key（公开路径 server 端会跳过认证，不会报错）
  headers['Authorization'] = 'Bearer ' + getApiKey();
  const r = await fetch(url, { headers });
  if (!r.ok) {
    // 认证失败时返回空 data，让 loadXxx 走"暂无数据"分支
    if (r.status === 401) return { data: [] };
    throw new Error(`HTTP ${r.status}`);
  }
  return r.json();
}

// ─── Stats ───
function pctTrend(curr, prev) {
  if (!prev || prev === 0) return '';
  const pct = ((curr - prev) / prev * 100).toFixed(0);
  const cls = pct >= 0 ? 'up' : 'down';
  const sign = pct >= 0 ? '+' : '';
  return `<span class="trend ${cls}">今日vs昨日 ${sign}${pct}%</span>`;
}

async function loadStats() {
  // 累计 = 近 30 天滚动窗口；趋势 = 今天 vs 昨天（精确单日）
  const today = new Date().toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const [totals, summary, todayStats, yesterdayStats] = await Promise.all([
    fetchJSON('/api/stats/totals?days=30'),
    fetchJSON('/api/stats/summary'),
    fetchJSON('/api/stats/totals?date=' + today).catch(() => null),
    fetchJSON('/api/stats/totals?date=' + yesterday).catch(() => null),
  ]);
  const t = totals.data || {};
  document.getElementById('totalReqs').parentElement.querySelector('.trend')?.remove();
  document.getElementById('totalPrompt').parentElement.querySelector('.trend')?.remove();
  document.getElementById('totalComp').parentElement.querySelector('.trend')?.remove();
  document.getElementById('totalCached').parentElement.querySelector('.trend')?.remove();
  document.getElementById('totalReqs').textContent = fmt(t.total_requests || 0);
  document.getElementById('totalPrompt').textContent = fmt(t.total_prompt_tokens || 0);
  document.getElementById('totalComp').textContent = fmt(t.total_completion_tokens || 0);
  document.getElementById('totalCached').textContent = fmt(t.total_cached_tokens || 0);
  // 趋势：今天 vs 昨天
  const td = todayStats ? (todayStats.data || {}) : {};
  const yd = yesterdayStats ? (yesterdayStats.data || {}) : {};
  for (const [id, val, prev] of [['totalReqs', td.total_requests, yd.total_requests], ['totalPrompt', td.total_prompt_tokens, yd.total_prompt_tokens], ['totalComp', td.total_completion_tokens, yd.total_completion_tokens], ['totalCached', td.total_cached_tokens, yd.total_cached_tokens]]) {
    const el = document.getElementById(id);
    if (el) el.insertAdjacentHTML('afterend', pctTrend(val || 0, prev || 0));
  }

  const tbody = document.getElementById('modelTable');
  tbody.innerHTML = '';
  const rows = summary.data || [];
  for (const r of rows) {
    const ch = r.grp_channel || r.channel || '';
    const chBadge = ch ? `<span class="badge ${ch}">${ch}</span>` : '-';
    const model = r.grp_model || r.grp || r.model || '-';
    const lat = r.avg_latency_ms || 0;
    const latCls = lat < 8000 ? 'green' : lat < 15000 ? 'amber' : 'red';
    tbody.innerHTML += `<tr>
      <td class="mono">${model}</td>
      <td>${chBadge}</td>
      <td class="mono right">${fmt(r.request_count)}</td>
      <td class="mono right">${fmt(r.total_prompt_tokens)}</td>
      <td class="mono right">${fmt(r.total_completion_tokens)}</td>
      <td class="mono right">${fmt(r.total_cached_tokens)}</td>
      <td class="mono right" style="color:var(--${latCls})">${fmtMs(lat)}</td>
    </tr>`;
  }
}

// ─── Daily bar chart (CSS) ───
let dailyDays = 7;
async function loadDaily(days) {
  dailyDays = days;
  const container = document.getElementById('dailyBars');
  container.innerHTML = '<div class="loading">加载中...</div>';

  let labels, dataMap, modelSet = new Set();
  if (days === 1) {
    const resp = await fetchJSON('/api/stats/hourly?role_filter=request');
    const rows = resp.data || [];
    labels = Array.from({length: 24}, (_, i) => String(i).padStart(2, '0') + '时');
    dataMap = {};
    for (const r of rows) {
      modelSet.add(r.model);
      if (!dataMap[r.hour]) dataMap[r.hour] = {};
      dataMap[r.hour][r.model] = r.request_count;
    }
    var xKeys = Array.from({length: 24}, (_, i) => i);
  } else {
    const since = new Date(Date.now() - (days - 1) * 86400000).toISOString().slice(0, 10);
    const resp = await fetchJSON('/api/stats/daily?since=' + since + '&role_filter=request');
    const rows = resp.data || [];
    const allDates = [];
    for (let i = days - 1; i >= 0; i--) {
      allDates.push(new Date(Date.now() - i * 86400000).toISOString().slice(0, 10));
    }
    dataMap = {};
    for (const r of rows) {
      modelSet.add(r.model);
      if (!dataMap[r.date]) dataMap[r.date] = {};
      dataMap[r.date][r.model] = r.request_count;
    }
    labels = allDates.map(d => d.slice(5));
    var xKeys = allDates;
  }

  const models = [...modelSet];
  // 对每个 xKey (日期/小时)，堆叠所有模型的请求数
  const stacked = xKeys.map(k => {
    let total = 0;
    const parts = [];
    for (const m of models) {
      const v = dataMap[k]?.[m] || 0;
      if (v > 0) { parts.push({model: m, value: v}); total += v; }
    }
    return {key: k, total, parts};
  });

  const maxTotal = Math.max(...stacked.map(s => s.total), 1);

  // 计算每个模型的总请求数
  const modelTotals = {};
  for (const m of models) {
    modelTotals[m] = stacked.reduce((sum, s) => {
      const p = s.parts.find(x => x.model === m);
      return sum + (p ? p.value : 0);
    }, 0);
  }

  let html = '';
  for (const s of stacked) {
    if (s.total === 0) continue;
    const pct = (s.total / maxTotal * 100).toFixed(1);
    html += `<div class="bar-group">
      <div class="bar-label">
        <span class="name">${labels[xKeys.indexOf(s.key)] || s.key}</span>
        <span class="val">${s.total}</span>
      </div>
      <div class="bar-track" style="position:relative;overflow:visible;">`;
    let left = 0;
    for (const p of s.parts) {
      const w = (p.value / maxTotal * 100).toFixed(1);
      html += `<div class="bar-fill" style="position:absolute;left:${left}%;width:${w}%;background:${getColor(p.model)};" title="${p.model}: ${p.value}"></div>`;
      left += parseFloat(w);
    }
    html += `</div></div>`;
  }

  // 图例：模型名 + 颜色 + 总请求数
  const legendHTML = models
    .filter(m => modelTotals[m] > 0)
    .sort((a, b) => modelTotals[b] - modelTotals[a])
    .map(m => `<span style="display:inline-flex;align-items:center;gap:4px;margin-right:12px;font-size:11px;color:var(--muted);">
      <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${getColor(m)};"></span>
      ${m} <span style="color:var(--text);font-weight:600;">${modelTotals[m]}</span>
    </span>`).join('');

  if (!html) html = '<div class="loading">暂无数据</div>';
  container.innerHTML = html + (legendHTML ? `<div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--border);">${legendHTML}</div>` : '');
}

// ─── Token bar chart (CSS) ───
async function loadTokenChart(days) {
  const since = new Date(Date.now() - (days - 1) * 86400000).toISOString().slice(0, 10);
  const [respRequest, respClassifier] = await Promise.all([
    fetchJSON('/api/stats/summary?group_by=model&since=' + since + '&role_filter=request'),
    fetchJSON('/api/stats/summary?group_by=model&since=' + since + '&role_filter=classifier'),
  ]);

  // 合并下游模型 + 路由模型消耗，按总量排序
  const merged = {};
  for (const r of (respRequest.data || [])) {
    const model = r.grp_model || r.grp || r.model || '-';
    merged[model] = {
      model,
      total_prompt_tokens: r.total_prompt_tokens || 0,
      total_completion_tokens: r.total_completion_tokens || 0,
      total_cached_tokens: r.total_cached_tokens || 0,
      supports_cached_tokens: r.supports_cached_tokens ?? 1,
    };
  }
  for (const r of (respClassifier.data || [])) {
    const model = r.grp_model || r.grp || r.model || '-';
    if (merged[model]) {
      merged[model].total_prompt_tokens += r.total_prompt_tokens || 0;
      merged[model].total_completion_tokens += r.total_completion_tokens || 0;
      merged[model].total_cached_tokens += r.total_cached_tokens || 0;
      merged[model].supports_cached_tokens = (merged[model].supports_cached_tokens && (r.supports_cached_tokens ?? 1)) ? 1 : 0;
    } else {
      merged[model] = {
        model,
        total_prompt_tokens: r.total_prompt_tokens || 0,
        total_completion_tokens: r.total_completion_tokens || 0,
        total_cached_tokens: r.total_cached_tokens || 0,
        supports_cached_tokens: r.supports_cached_tokens ?? 1,
      };
    }
  }
  const rows = Object.values(merged).sort((a, b) =>
    ((b.total_prompt_tokens + b.total_completion_tokens) || 0) -
    ((a.total_prompt_tokens + a.total_completion_tokens) || 0)
  );

  const container = document.getElementById('tokenBars');
  container.innerHTML = '';

  const maxTokens = Math.max(...rows.map(r => (r.total_prompt_tokens || 0) + (r.total_completion_tokens || 0)), 1);

  for (const r of rows) {
    const model = r.model || '-';
    const cached = r.total_cached_tokens || 0;
    const nonCached = Math.max(0, (r.total_prompt_tokens || 0) - cached);
    const comp = r.total_completion_tokens || 0;
    const total = cached + nonCached + comp;
    if (total === 0) continue;
    const showCached = r.supports_cached_tokens ? fmt(cached) : 'N/A';

    container.innerHTML += `<div class="bar-group">
      <div class="bar-label">
        <span class="name" title="${model}">${model}</span>
        <span class="val">${fmt(total)}</span>
      </div>
      <div class="bar-track" style="height:12px;">
        <div class="bar-fill" style="width:${(total / maxTokens * 100).toFixed(1)}%;background:var(--border);position:relative;">
          ${r.supports_cached_tokens ? `<div class="bar-fill" style="width:${(cached/total*100).toFixed(1)}%;background:#a855f7;position:absolute;left:0;top:0;bottom:0;"></div>` : ''}
          <div class="bar-fill" style="width:${(nonCached/total*100).toFixed(1)}%;background:#22c55e;position:absolute;left:${r.supports_cached_tokens ? (cached/total*100).toFixed(1) + '%' : '0'};top:0;bottom:0;"></div>
          <div class="bar-fill" style="width:${(comp/total*100).toFixed(1)}%;background:#f59e0b;position:absolute;left:${(((r.supports_cached_tokens ? cached : 0)+nonCached)/total*100).toFixed(1)}%;top:0;bottom:0;"></div>
        </div>
      </div>
      <div style="display:flex;gap:12px;margin-top:2px;font-size:10px;color:var(--muted);">
        <span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:#7c3aed;vertical-align:middle;margin-right:3px;"></span>缓存 ${showCached}</span>
        <span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:#059669;vertical-align:middle;margin-right:3px;"></span>输入 ${fmt(nonCached)}</span>
        <span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:#d97706;vertical-align:middle;margin-right:3px;"></span>输出 ${fmt(comp)}</span>
      </div>
    </div>`;
  }
  if (!container.innerHTML) container.innerHTML = '<div class="loading">暂无数据</div>';
}

// ─── Logs ───
async function loadLogs() {
  const resp = await fetchJSON('/api/stats/logs?limit=15');
  const rows = resp.data || [];
  const container = document.getElementById('logsList');
  if (!rows.length) { container.innerHTML = '<div class="loading">暂无数据</div>'; return; }

  const headerHTML = `<div style="display:grid;grid-template-columns:18% 24% 10% 14% 14% 20%;gap:4px;padding:6px 10px 8px;border-bottom:2px solid var(--border);font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;position:sticky;top:0;background:var(--card);z-index:1;">
    <span>时间</span>
    <span>模型</span>
    <span>渠道</span>
    <span class="right">输入</span>
    <span class="right">输出</span>
    <span class="right">延迟</span>
  </div>`;

  const rowsHTML = rows.map(r => {
    const ch = r.channel || '';
    const sc = r.status_code;
    const statusCls = (sc === 200 || sc === 201) ? 'ok' : 'err';
    const statusTip = sc ? `HTTP ${sc}` : '';
    const lat = r.latency_ms || 0;
    const latCls = lat < 8000 ? 'green' : lat < 15000 ? 'amber' : 'red';
    return `<div class="log-row" style="display:grid;grid-template-columns:18% 24% 10% 14% 14% 20%;gap:4px;padding:8px 10px;border-bottom:1px solid var(--border);font-size:12px;align-items:center;">
      <span style="color:var(--muted)"><span class="status-dot ${statusCls}" title="${statusTip}"></span>${fmtTime(r.timestamp)}</span>
      <span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${r.model || ''}">${r.model || '-'}</span>
      <span class="badge ${ch}" style="display:inline-block;width:fit-content;">${ch}</span>
      <span class="mono right">${fmt(r.prompt_tokens)}</span>
      <span class="mono right">${fmt(r.completion_tokens)}</span>
      <span class="mono right" style="color:var(--${latCls})">${fmtMs(lat)}</span>
    </div>`;
  }).join('');

  container.innerHTML = headerHTML + rowsHTML;
}

// ─── Model config (card grid) ───
function getApiKey() {
  return localStorage.getItem('llm_gateway_api_key') || 'V.A.L.O.R.';
}

// Provider brand colors
const PROVIDER_COLORS = {
  ark:      { bg: '#eff6ff', fg: '#2563eb' },
  minimax:  { bg: '#ecfdf5', fg: '#059669' },
  longcat:  { bg: '#fffbeb', fg: '#d97706' },
  xiaomi:   { bg: '#fef2f2', fg: '#dc2626' },
  deepseek: { bg: '#eff6ff', fg: '#1d4ed8' },
  kimi:     { bg: '#f5f3ff', fg: '#7c3aed' },
  aliyuncs: { bg: '#f5f3ff', fg: '#7c3aed' },
  soloagilab: { bg: '#ede9fe', fg: '#6d28d9' },  // 紫色系，soloagilab channel
};
const DEFAULT_COLOR = { bg: '#e5e7eb', fg: '#6b7280' };

function providerColor(name) {
  return PROVIDER_COLORS[name] || DEFAULT_COLOR;
}

async function loadModels() {
  const container = document.getElementById('configCards');
  container.innerHTML = '<div class="loading" style="padding:40px;text-align:center">加载中...</div>';
  const resp = await fetch('/api/config/models');
  if (!resp.ok) { container.innerHTML = '<div class="loading">加载失败</div>'; return; }
  const data = await resp.json();
  const modelsByProvider = data.data || {};
  const entries = Object.entries(modelsByProvider);
  // 同时拉 Codex 额度（公开路径无需 auth，401 时静默降级）
  let codexHTML = '';
  try {
    const cr = await fetch('/quota/codex');
    if (cr.ok) {
      const cd = await cr.json();
      if (cd && cd.parsed) {
        const p = cd.parsed;
        const hLeft = p.hourly_limit ? p.hourly_limit.pct_left : 0;
        const wLeft = p.weekly_limit ? p.weekly_limit.pct_left : 0;
        const hPct = 100 - hLeft;
        const wPct = 100 - wLeft;
        codexHTML = `<div class="config-card codex-card">
      <div class="config-card-header">
        <span class="config-card-provider" style="color:#06b6d4">Codex 额度</span>
        <span class="config-card-badge ${hPct > 90 ? 'warn' : 'ok'}">${hPct}% 已用</span>
      </div>
      <div style="margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-bottom:4px">
          <span>5h 额度</span><span>${hLeft}% 剩余</span>
        </div>
        <div style="background:var(--border);border-radius:4px;height:6px;overflow:hidden">
          <div style="width:${hPct}%;background:linear-gradient(90deg,#06b6d4,#3b82f6);height:100%;border-radius:4px;transition:width 0.5s"></div>
        </div>
      </div>
      <div>
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-bottom:4px">
          <span>周额度</span><span>${wLeft}% 剩余</span>
        </div>
        <div style="background:var(--border);border-radius:4px;height:6px;overflow:hidden">
          <div style="width:${wPct}%;background:linear-gradient(90deg,#a855f7,#ec4899);height:100%;border-radius:4px;transition:width 0.5s"></div>
        </div>
      </div>
    </div>`;
      }
    }
  } catch(e) {}

  if (entries.length === 0 && !codexHTML) {
    container.innerHTML = '<div class="loading">暂无数据</div>'; return;
  }

  let html = '';
  for (const [provider, models] of entries) {
    const enabled = models.filter(m => m.enabled).length;
    const total = models.length;
    const badgeClass = enabled === total ? 'ok' : 'warn';
    const badgeLabel = `${enabled}/${total} 启用`;
    const color = providerColor(provider);
    let modelsHTML = '';
    for (const m of models) {
      const dotClass = m.enabled ? 'on' : 'off';
      modelsHTML += `<div class="config-model-row">
        <div class="config-model-info">
          <span class="config-status-dot ${dotClass}"></span>
          <span class="config-model-name" title="${m.name}">${m.name}</span>
        </div>
        <label class="toggle-switch">
          <input type="checkbox" ${m.enabled ? 'checked' : ''} onchange="toggleModel('${m.name.replace(/'/g,"\\'")}', this.checked)">
          <span class="toggle-slider"></span>
        </label>
      </div>`;
    }
    html += `<div class="config-card">
      <div class="config-card-header">
        <span class="config-card-provider" style="color:${color.fg}">${provider}</span>
        <span class="config-card-badge ${badgeClass}">${badgeLabel}</span>
      </div>
      <div class="config-card-models">${modelsHTML}</div>
    </div>`;
  }
    html += codexHTML;
  // ─── 小米手环预览卡片 ───
  let bandHTML = '';
  try {
    const br = await fetch('/quota/codex/simple');
    if (br.ok) {
      const bd = await br.json();
      const sc = (s) => s === 'ok' ? 'var(--green)' : s === 'warn' ? 'var(--amber)' : s === 'danger' ? 'var(--red)' : 'var(--muted)';
      const badgeClass = bd.ok
        ? (bd.five_hour_status === 'danger' || bd.weekly_status === 'danger' ? 'warn' : 'ok')
        : 'warn';
      const badgeNote = bd.stale ? 'STALE' : (bd.error ? 'ERROR' : bd.plan);

      const statusText = (s) => {
        if (!bd.ok || bd.error) return '⚠️ 数据获取失败';
        if (bd.stale) return '⏰ 数据已过期';
        if (s === 'danger') return '🔴 告急';
        if (s === 'warn') return '🟡 偏低';
        return '🟢 正常';
      };
      const tip = statusText(bd.five_hour_status === 'danger' || bd.weekly_status === 'danger' ? 'danger' : (bd.five_hour_status === 'warn' || bd.weekly_status === 'warn' ? 'warn' : 'ok'));

      bandHTML = `<div class="config-card band-card" style="grid-column:span 2;">
      <div class="config-card-header">
        <span class="config-card-provider" style="color:#06b6d4">⌚ 小米手环</span>
        <span class="config-card-badge ${badgeClass}">${badgeNote}</span>
      </div>

      <!-- 两列并排：5H | WEEK -->
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:10px;">

        <!-- 5H 额度 -->
        <div>
          <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;">
            <span style="font-size:11px;color:var(--muted);">5H 额度</span>
            <span style="color:${sc(bd.five_hour_status)};font-weight:600;font-size:11px;">${bd.five_hour_status !== 'error' ? bd.five_hour_status.toUpperCase() : 'ERROR'}</span>
          </div>
          <span style="font-size:22px;font-weight:700;color:${sc(bd.five_hour_status)};">${bd.five_hour_left !== null ? bd.five_hour_left + '%' : '--'}</span>
        </div>

        <!-- WEEK 额度 -->
        <div>
          <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;">
            <span style="font-size:11px;color:var(--muted);">周额度</span>
            <span style="color:${sc(bd.weekly_status)};font-weight:600;font-size:11px;">${bd.weekly_status !== 'error' ? bd.weekly_status.toUpperCase() : 'ERROR'}</span>
          </div>
          <span style="font-size:22px;font-weight:700;color:${sc(bd.weekly_status)};">${bd.weekly_left !== null ? bd.weekly_left + '%' : '--'}</span>
        </div>

      </div>

      <!-- 状态提示 -->
      <div style="margin-top:10px;padding:8px 10px;background:rgba(255,255,255,0.03);border-radius:6px;font-size:11px;color:var(--muted);border-left:2px solid var(--accent);">
        ${tip}
      </div>

    </div>`;
    }
  } catch(e) {}
  html += bandHTML;
  container.innerHTML = html;
}

async function toggleModel(name, enable) {
  const r = await fetch(`/api/config/models/${encodeURIComponent(name)}`, {
    method: 'POST',
    headers: {'Content-Type':'application/json', 'Authorization': `Bearer ${getApiKey()}`},
    body: JSON.stringify({enabled: enable})
  });
  if (!r.ok) { alert('操作失败'); return; }
  await loadModels();
}

// ─── Tab switch ───
function switchTab(name) {
  document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.querySelector(`.tab-btn[onclick="switchTab('${name}')"]`).classList.add('active');
  if (name === 'config') loadModels();
}

// ─── Day button events ───
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('#dailyBtns .day-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#dailyBtns .day-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadDaily(parseInt(btn.dataset.days));
    });
  });
  document.querySelectorAll('#tokenBtns .day-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#tokenBtns .day-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadTokenChart(parseInt(btn.dataset.days));
    });
  });
});

async function refreshAll() {
  const btn = document.getElementById('refreshBtn');
  const statusEl = document.getElementById('refreshStatus');
  btn.classList.add('spinning');
  btn.disabled = true;
  statusEl.textContent = '刷新中...';
  statusEl.style.color = 'var(--accent)';
  try {
    await loadAll();
    await loadModels();
    const now = new Date();
    document.getElementById('lastUpdate').textContent = '更新于 ' + now.toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
    statusEl.textContent = '✓ 已刷新';
    statusEl.style.color = 'var(--green)';
  } catch(e) {
    console.error(e);
    statusEl.textContent = '✗ 刷新失败';
    statusEl.style.color = 'var(--red)';
  }
  setTimeout(() => { btn.classList.remove('spinning'); btn.disabled = false; statusEl.textContent = ''; }, 800);
}

async function loadAll() {
  document.getElementById('totalReqs').textContent = '...';
  try {
    await Promise.all([loadStats(), loadDaily(7), loadTokenChart(7), loadLogs(), loadModels()]);
    const now = new Date();
    document.getElementById('lastUpdate').textContent = '更新于 ' + now.toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
  } catch(e) { console.error(e); }
}

loadAll();
</script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML
