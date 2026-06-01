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
<style>
:root {
  --bg: #0f172a; --card: #1e293b; --border: #334155;
  --text: #e2e8f0; --muted: #94a3b8; --accent: #3b82f6;
  --green: #22c55e; --amber: #f59e0b; --red: #ef4444; --purple: #a855f7;
  --cyan: #06b6d4; --pink: #ec4899;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; -webkit-text-size-adjust: 100%; }

/* Header */
.header { padding: 16px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; background: var(--bg); z-index: 10; }
.header h1 { font-size: 18px; font-weight: 600; }
.header .refresh { background: var(--accent); color: #fff; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; white-space: nowrap; }
.header .refresh:active { opacity: 0.7; }

/* Container */
.container { max-width: 1280px; margin: 0 auto; padding: 16px; }

/* Tab nav */
.tab-nav { display: flex; gap: 0; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
.tab-btn { background: none; border: none; color: var(--muted); padding: 10px 16px; cursor: pointer; font-size: 14px; border-bottom: 2px solid transparent; margin-bottom: -1px; white-space: nowrap; transition: color 0.15s; -webkit-tap-highlight-color: transparent; }
.tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }
.tab-pane { display: none; }
.tab-pane.active { display: block; }

/* Stats cards */
.stats-row { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-bottom: 0; }
@media (min-width: 768px) {
  .stats-row { grid-template-columns: repeat(4, 1fr); }
}
.stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
.stat-card .label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
.stat-card .value { font-size: 24px; font-weight: 700; }
.stat-card .value.blue { color: var(--accent); }
.stat-card .value.green { color: var(--green); }
.stat-card .value.amber { color: var(--amber); }
.stat-card .value.purple { color: var(--purple); }

/* Bar chart (CSS only) */
.chart-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px; display: flex; flex-direction: column; }
.chart-card h3 { font-size: 13px; color: var(--muted); margin-bottom: 12px; font-weight: 500; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; flex-shrink: 0; }
.bar-group { margin-bottom: 10px; }
.bar-label { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; font-size: 12px; }
.bar-label .name { color: var(--text); max-width: 60%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-label .val { color: var(--muted); font-family: 'SF Mono', 'Fira Code', monospace; font-size: 11px; }
.bar-track { height: 8px; background: var(--border); border-radius: 4px; overflow: hidden; }
.bar-fill { height: 100%; border-radius: 4px; transition: width 0.5s ease; min-width: 2px; }

/* Chart content area */
.chart-scroll {
  overflow-y: auto;
  overflow-x: hidden;
  flex: 1;
  min-height: 0;
  padding-right: 4px;
}
@media (max-width: 767px) {
  .chart-scroll {
    height: 290px;
  }
}
.chart-scroll::-webkit-scrollbar { width: 4px; }
.chart-scroll::-webkit-scrollbar-track { background: transparent; }
.chart-scroll::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
.chart-scroll::-webkit-scrollbar-thumb:hover { background: var(--muted); }

/* Scrollable table area */
.table-scroll {
  overflow-y: auto;
  overflow-x: auto;
  max-height: 400px;
  padding-right: 4px;
}
@media (max-width: 767px) {
  .table-scroll {
    max-height: 320px;
  }
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
.day-btn { background: var(--border); border: none; color: var(--muted); padding: 3px 8px; border-radius: 4px; cursor: pointer; font-size: 10px; -webkit-tap-highlight-color: transparent; }
.day-btn.active { background: var(--accent); color: #fff; }

/* Model table */
.table-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px; overflow: hidden; display: flex; flex-direction: column; }
.table-card h3 { font-size: 13px; color: var(--muted); margin-bottom: 12px; font-weight: 500; flex-shrink: 0; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; padding: 8px 10px; border-bottom: 1px solid var(--border); }
td { padding: 10px; font-size: 13px; border-bottom: 1px solid var(--border); }
tr:last-child td { border-bottom: none; }
.mono { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; }
.badge { display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; }
.badge.ark { background: #1e3a5f; color: #60a5fa; }
.badge.kimi { background: #3b1f4a; color: #c084fc; }
.badge.minimax { background: #1a3d2e; color: #4ade80; }
.badge.deepseek { background: #1a2e3d; color: #38bdf8; }
.badge.xiaomi { background: #3d1a1a; color: #f87171; }
.badge.longcat { background: #2d2a1a; color: #fbbf24; }
.badge.aliyuncs { background: #2a1a3d; color: #e879f9; }
.badge.dashscope { background: #2a1a3d; color: #e879f9; }

/* Provider config cards */
.config-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
.config-card-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
.config-card-provider { font-weight:600; font-size:14px; color:var(--accent); text-transform:capitalize; }
.config-card-badge { font-size:11px; padding:2px 8px; border-radius:10px; font-weight:600; }
.config-card-badge.ok { background:#1a3d2e; color:#4ade80; }
.config-card-badge.warn { background:#3d2a1a; color:#fb923c; }
.config-card-models { display:flex; flex-direction:column; gap:6px; }
.config-model-row { display:flex; justify-content:space-between; align-items:center; padding:6px 10px; background:rgba(255,255,255,0.03); border-radius:6px; }
.config-model-info { display:flex; align-items:center; gap:8px; min-width:0; }
.config-model-name { font-family:'SF Mono','Fira Code',monospace; font-size:12px; color:var(--text); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:130px; }
.config-status-dot { width:7px; height:7px; border-radius:50%; flex-shrink:0; }
.config-status-dot.on { background:var(--green); }
.config-status-dot.off { background:var(--red); }

/* Toggle button */
.toggle-btn { color:#fff; border:none; padding:4px 10px; border-radius:4px; cursor:pointer; font-size:11px; font-weight:600; -webkit-tap-highlight-color:transparent; transition:opacity 0.15s; }
.toggle-btn:hover { opacity:0.8; }
.toggle-btn:active { opacity:0.6; }

/* Loading */
.loading { text-align: center; padding: 40px; color: var(--muted); font-size: 14px; }

/* Status indicator */
.status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.status-dot.on { background: var(--green); }
.status-dot.off { background: var(--red); }
.status-dot.draining { background: var(--amber); }

/* Two-column layout for charts on desktop */
@media (min-width: 768px) {
  .charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .charts-row .chart-card { margin-bottom: 0; }
}
</style>
</head>
<body>
<div class="header">
  <h1>🤖 LLM Gateway</h1>
  <button class="refresh" onclick="loadAll(); loadModels();">↻ 刷新</button>
</div>
<div class="container">
  <div class="tab-nav">
    <button class="tab-btn active" onclick="switchTab('overview')">概览</button>
    <button class="tab-btn" onclick="switchTab('config')">配置</button>
  </div>

  <div id="tab-overview" class="tab-pane active">
    <div class="overview-sections">
    <div class="stats-row" id="statsRow">
      <div class="stat-card"><div class="label">总请求数</div><div class="value blue" id="totalReqs">-</div></div>
      <div class="stat-card"><div class="label">输入 Tokens</div><div class="value green" id="totalPrompt">-</div></div>
      <div class="stat-card"><div class="label">输出 Tokens</div><div class="value amber" id="totalComp">-</div></div>
      <div class="stat-card"><div class="label">缓存命中</div><div class="value purple" id="totalCached">-</div></div>
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
    'deepseek-v4-pro': '#3b82f6', 'deepseek-v4-flash': '#60a5fa',
    'doubao-seed-2-0-pro': '#06b6d4', 'doubao-seed-2-0-lite': '#67e8f9',
    'MiniMax-M2.7-highspeed': '#22c55e', 'mimo-v2.5-pro': '#f59e0b',
    'mimo-v2.5': '#f87171', 'LongCat-Flash-Lite': '#a855f7',
    'LongCat-2.0-Preview': '#c084fc', 'LongCat-Flash-Chat': '#fbbf24',
    'glm-5-1': '#60a5fa', 'kimi-for-coding': '#ec4899',
  };
  return m[key] || '#64748b';
}

async function fetchJSON(url) {
  const r = await fetch(url);
  return r.json();
}

// ─── Stats ───
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

  const tbody = document.getElementById('modelTable');
  tbody.innerHTML = '';
  const rows = summary.data || [];
  for (const r of rows) {
    const ch = r.grp_channel || r.channel || '';
    const chBadge = ch ? `<span class="badge ${ch}">${ch}</span>` : '-';
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
        <span>🟣 缓存 ${showCached}</span>
        <span>🟢 输入 ${fmt(nonCached)}</span>
        <span>🟡 输出 ${fmt(comp)}</span>
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

  // 表头
  const headerHTML = `<div style="display:grid;grid-template-columns:18% 24% 10% 14% 14% 20%;gap:4px;padding:6px 10px 8px;border-bottom:2px solid var(--border);font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;">
    <span>时间</span>
    <span>模型</span>
    <span>渠道</span>
    <span style="text-align:right;">输入</span>
    <span style="text-align:right;">输出</span>
    <span style="text-align:right;">延迟</span>
  </div>`;

  // 数据行
  const rowsHTML = rows.map(r => {
    const ch = r.channel || '';
    return `<div style="display:grid;grid-template-columns:18% 24% 10% 14% 14% 20%;gap:4px;padding:8px 10px;border-bottom:1px solid var(--border);font-size:12px;align-items:center;">
      <span style="color:var(--muted)">${fmtTime(r.timestamp)}</span>
      <span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${r.model || ''}">${r.model || '-'}</span>
      <span class="badge ${ch}" style="display:inline-block;width:fit-content;">${ch}</span>
      <span class="mono" style="text-align:right;">${fmt(r.prompt_tokens)}</span>
      <span class="mono" style="text-align:right;">${fmt(r.completion_tokens)}</span>
      <span class="mono" style="text-align:right;">${fmtMs(r.latency_ms)}</span>
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
  ark:      { bg: '#1e3a5f', fg: '#60a5fa' },
  minimax:  { bg: '#1a3d2e', fg: '#4ade80' },
  longcat:  { bg: '#2d2a1a', fg: '#fbbf24' },
  xiaomi:   { bg: '#3d1a1a', fg: '#f87171' },
  deepseek: { bg: '#1a2e3d', fg: '#38bdf8' },
  kimi:     { bg: '#3b1f4a', fg: '#c084fc' },
  aliyuncs: { bg: '#2a1a3d', fg: '#e879f9' },
};
const DEFAULT_COLOR = { bg: '#1e293b', fg: '#94a3b8' };

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
  // 同时拉 Codex 额度
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
      const btnLabel = m.enabled ? '关闭' : '开启';
      const btnColor = m.enabled ? 'var(--red)' : 'var(--green)';
      modelsHTML += `<div class="config-model-row">
        <div class="config-model-info">
          <span class="config-status-dot ${dotClass}"></span>
          <span class="config-model-name" title="${m.name}">${m.name}</span>
        </div>
        <button class="toggle-btn" style="background:${btnColor}" onclick="toggleModel(\'${m.name.replace(/\'/g,"\\\\\'")}\',${!m.enabled})">${btnLabel}</button>
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

async function loadAll() {
  document.getElementById('totalReqs').textContent = '...';
  try {
    await Promise.all([loadStats(), loadDaily(7), loadTokenChart(7), loadLogs(), loadModels()]);
  } catch(e) { console.error(e); }
}

loadAll();
</script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML
