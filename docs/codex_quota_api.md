# GET /quota/codex/simple — Codex 额度轻量接口

> 小米手环 / 嵌入式设备专用。扁平 JSON、无需认证、带 CORS。

## 请求

```
GET /quota/codex/simple
```

- 无需认证
- 无需请求体
- 支持 CORS（`Access-Control-Allow-Origin: *`）
- `Cache-Control: no-store`

## 正常响应 (200)

```json
{
  "ok": true,
  "title": "CODEX",
  "plan": "PLUS",
  "five_hour_left": 97,
  "weekly_left": 95,
  "five_hour_used": 3,
  "weekly_used": 5,
  "five_hour_status": "ok",
  "weekly_status": "ok",
  "age_seconds": 14,
  "updated_at": "15:17",
  "stale": false
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `ok` | bool | 数据是否可用 |
| `title` | string | 固定 `"CODEX"` |
| `plan` | string | Codex 订阅计划，大写（如 `PLUS`、`PRO`） |
| `five_hour_left` | int\|null | 5小时额度剩余百分比 (0-100) |
| `weekly_left` | int\|null | 周额度剩余百分比 (0-100) |
| `five_hour_used` | int\|null | 5小时额度已用百分比 (0-100)，= `100 - five_hour_left` |
| `weekly_used` | int\|null | 周额度已用百分比 (0-100)，= `100 - weekly_left` |
| `five_hour_status` | string | 5h 额度状态（见状态规则） |
| `weekly_status` | string | 周额度状态（见状态规则） |
| `age_seconds` | int\|null | 数据新鲜度（距上次采集的秒数） |
| `updated_at` | string | 数据更新时间，`HH:MM` 格式 |
| `stale` | bool | 数据是否过期（`age_seconds > 300`） |

### 状态规则

| 条件 | 状态 |
|------|------|
| `left >= 60` | `"ok"` |
| `30 <= left < 60` | `"warn"` |
| `left < 30` | `"danger"` |
| 数据不可用 | `"error"` |

### 字段命名说明

`hourly_limit.window_minutes` 实际值为 300 分钟（5小时），因此对外字段统一使用 `five_hour_` 前缀，不使用 `hourly_`。

## 异常响应 (500 / 503)

统一结构，`ok = false`：

```json
{
  "ok": false,
  "title": "CODEX",
  "plan": "--",
  "five_hour_left": null,
  "weekly_left": null,
  "five_hour_used": null,
  "weekly_used": null,
  "five_hour_status": "error",
  "weekly_status": "error",
  "age_seconds": null,
  "updated_at": "--:--",
  "stale": true,
  "error": "status_file_not_found"
}
```

### error 值

| error | HTTP Status | 含义 |
|-------|-------------|------|
| `status_file_not_found` | 503 | `/tmp/codex_status.json` 不存在（daemon 未运行） |
| `parsed_field_missing` | 500 | 文件存在但缺少 `parsed` 字段 |
| `quota_parse_failed` | 500 | JSON 解析异常 |
| `quota_parse_failed: <detail>` | 500 | 其他异常 |

## CORS 响应头

所有 `/quota/codex/simple` 响应（含 OPTIONS 预检）均包含：

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, OPTIONS
Access-Control-Allow-Headers: Content-Type, Authorization
Cache-Control: no-store
```

## 数据来源

`/tmp/codex_status.json`，由 `codex_keepalive` daemon 写入。daemon 通过 `codex exec "hi"` + JSONL 解析获取 Codex 额度数据，约 15-25 分钟更新一次（夜间静默 22:00-09:00 不更新）。

## 上游字段映射

```
upstream (/tmp/codex_status.json)        →  /quota/codex/simple
───────────────────────────────────────────────────────────────
parsed.plan                              →  plan (转大写)
parsed.hourly_limit.pct_left             →  five_hour_left
100 - parsed.hourly_limit.pct_left       →  five_hour_used
parsed.weekly_limit.pct_left             →  weekly_left
100 - parsed.weekly_limit.pct_left       →  weekly_used
parsed.data_age_seconds                  →  age_seconds
timestamp (ISO 8601)                     →  updated_at (HH:MM)
age_seconds > 300                        →  stale
```

## 客户端集成示例

```javascript
// 最小化 fetch 示例
const r = await fetch('http://host:8000/quota/codex/simple');
const d = await r.json();
if (d.ok) {
  console.log(`5H: ${d.five_hour_left}% [${d.five_hour_status}]`);
  console.log(`WEEK: ${d.weekly_left}% [${d.weekly_status}]`);
}
```
