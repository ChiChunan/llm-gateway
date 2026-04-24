# LLM Gateway

基于请求复杂度和 session 一致性哈希的智能路由网关，直连多家 LLM 提供商，支持自动限流处理、plan 级联动降级和流式响应透传。

## 功能特性

- **智能路由**：轻量分类器判断请求复杂度（simple/complex），自动选择合适模型
- **Session 负载均衡**：同一 session 固定路由到同一模型，不同 session 均匀分散
- **直连三家服务商**：火山引擎 ARK、Kimi Coding、MiniMax，无中间层
- **Plan 级联动降级**：ARK plan 内任意模型超限，同 plan 所有模型同时标记不可用
- **429 精确恢复**：解析限流响应中的 `reset at` 时间，到点自动恢复，无需轮询
- **Thinking 自动关闭**：按模型注入对应参数，避免 thinking token 浪费
- **用量追踪**：SQLite 记录每次请求的 token 消耗，Dashboard 可视化

## 架构

```
Client → Router :8000 (FastAPI)
           │
           ├─ 分类器 (doubao-seed-2-0-lite / ARK)
           │   └─ simple / complex
           │
           ├─ Session 一致性哈希 → 固定起始模型
           │
           ├─ 遍历 candidates → 第一个可用模型
           │
           └─ 直连服务商
               ├─ ARK  → glm-5-1, doubao-seed-2-0-pro/lite
               ├─ Kimi → kimi-for-coding
               └─ MiniMax → MiniMax-M2.7-highspeed
```

## 路由策略

| 复杂度 | 候选顺序 | 说明 |
|--------|---------|------|
| simple | MiniMax → doubao-seed-2-0-pro → kimi-for-coding | MiniMax 超限降 ARK，ARK 超限降 Kimi |
| complex | glm-5-1 → kimi-for-coding → MiniMax | ARK plan 超限时 glm 和 doubao 同时不可用 |

分类器使用 `doubao-seed-2-0-lite`，分类失败时默认 complex。

## 快速开始

### 环境变量

```bash
cp .env.example .env
# 编辑 .env，填入各服务商 API Key
```

`.env.example` 内容：
```bash
ARK_API_KEY=your-ark-api-key
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3

KIMI_API_KEY=your-kimi-api-key
KIMI_BASE_URL=https://api.kimi.com/coding/v1

MINIMAX_API_KEY=your-minimax-api-key
MINIMAX_BASE_URL=https://v2.aicodee.com/v1
```

### Docker 部署（推荐）

```bash
docker compose up -d --build
```

服务启动后监听 `http://0.0.0.0:8000`

### 本地运行

```bash
cd router
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

## 客户端接入

将任意 OpenAI 兼容客户端的 `base_url` 指向网关：

```bash
# Claude Code / mc
export ANTHROPIC_BASE_URL=http://服务器IP:8000

# 通用 OpenAI 兼容客户端
base_url: http://服务器IP:8000/v1
api_key:  任意字符串（网关不鉴权）
model:    auto
```

## API 示例

```bash
# 健康检查
curl http://localhost:8000/health

# 智能路由（推荐）
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"你好"}],"stream":true}'

# 指定模型直通（跳过分类器）
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-5-1","messages":[{"role":"user","content":"设计一个微服务架构"}]}'
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 各模型可用状态 + 最早恢复时间 |
| `/v1/models` | GET | 可用模型列表 |
| `/v1/chat/completions` | POST | 聊天补全，兼容 OpenAI API |
| `/dashboard` | GET | 用量统计 Dashboard |
| `/stats` | GET | token 用量统计 API |

## 路由配置

编辑 `router/routing.yaml` 调整路由策略，无需重启（通过 volume mount 热加载）：

```yaml
routing:
  classifier: "doubao-seed-2-0-lite"
  simple_candidates:
    - "MiniMax-M2.7-highspeed"
    - "doubao-seed-2-0-pro"
  complex_candidates:
    - "glm-5-1"
    - "kimi-for-coding"
    - "MiniMax-M2.7-highspeed"
  simple_fallback_candidates:
    - "kimi-for-coding"

plans:
  ark:
    channels: ["doubao-seed-2-0-pro", "glm-5-1"]
  kimi:
    channels: ["kimi-for-coding"]
  minimax:
    channels: ["MiniMax-M2.7-highspeed"]
```

## 项目结构

```
├── docker-compose.yml
├── .env.example
└── router/
    ├── main.py          # FastAPI 主应用，路由决策
    ├── classifier.py    # 复杂度分类器
    ├── channel.py       # 渠道状态管理（429 封禁/恢复，plan 联动）
    ├── proxy.py         # 请求转发，SSE 透传，用量提取
    ├── providers.py     # 服务商配置，模型→渠道映射
    ├── usage.py         # SQLite 用量记录
    ├── api_stats.py     # 统计查询 API
    ├── dashboard.py     # 可视化 Dashboard
    ├── routing.yaml     # 路由策略配置
    ├── requirements.txt
    ├── requirements-dev.txt
    ├── Dockerfile
    └── tests/
```

## 测试

```bash
cd router
pip install -r requirements-dev.txt
pytest tests/ -v
```

## 许可证

MIT
