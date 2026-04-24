# LLM Gateway

一个基于请求复杂度智能路由的 LLM 网关，直连多家 LLM 提供商，支持多渠道管理、自动限流处理和流式响应。

## 功能特性

- **智能路由**：使用轻量 LLM 分类器自动判断请求复杂度，简单任务路由至 MiniMax M2.7，复杂任务路由至 GLM-5-1
- **直连三提供商**：火山引擎 ARK、Kimi Coding、MiniMax，无需中间网关
- **多渠道降级**：复杂任务 ARK→Kimi 降级，简单任务 MiniMax→ARK 降级
- **429 限流处理**：自动解析上游限流错误中的恢复时间，智能封禁/恢复渠道
- **流式响应支持**：完整透传 SSE 流式响应，兼顾低延迟与稳定性
- **指定模型直通**：model 参数直接传模型名时跳过分类，直连对应提供商

## 架构

```
Client → Router (FastAPI) → 直连各 LLM 提供商
                              ├─ ARK (火山引擎) — glm-5-1, doubao-seed-2-0-pro/lite
                              ├─ Kimi — kimi-for-coding
                              └─ MiniMax — MiniMax-M2.7-highspeed
```

## 路由策略

| 复杂度 | 首选模型 | 首选渠道 | 降级模型 | 降级渠道 |
|--------|---------|---------|---------|---------|
| 简单 | MiniMax-M2.7-highspeed | minimax | doubao-seed-2-0-pro | ark |
| 复杂 | glm-5-1 | ark | kimi-for-coding | kimi |
| 最终降级 | doubao-seed-2-0-pro | ark | — | — |

分类器使用 `doubao-seed-2-0-lite`，分类失败时安全降级为复杂任务。

## 快速开始

### 前置要求

- Python 3.11+ 或 Docker & Docker Compose
- 各 LLM 提供商的 API Key

### 环境变量

```bash
# 火山引擎 ARK
export ARK_API_KEY="your-ark-api-key"
export ARK_BASE_URL="https://ark.cn-beijing.volces.com/api/coding/v3"  # 可选，默认值

# Kimi Coding
export KIMI_API_KEY="your-kimi-api-key"
export KIMI_BASE_URL="https://api.kimi.com/coding/v1"  # 可选，默认值

# MiniMax
export MINIMAX_API_KEY="your-minimax-api-key"
export MINIMAX_BASE_URL="https://v2.aicodee.com/v1"  # 可选，默认值
```

### 本地运行

```bash
cd router
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Docker 运行

```bash
cp .env.example .env
# 编辑 .env 填入各 API Key
docker compose up -d
```

服务启动后监听 `http://localhost:8000`

### 使用

```bash
# 健康检查
curl http://localhost:8000/health

# 查看可用模型
curl http://localhost:8000/v1/models

# 智能路由（model 设为 auto）
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'

# 指定模型直通（跳过分类）
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "glm-5-1",
    "messages": [{"role": "user", "content": "设计一个微服务架构"}],
    "stream": false
  }'
```

## 配置

路由策略在 `router/routing.yaml` 中配置：

```yaml
routing:
  simple: "MiniMax-M2.7-highspeed"     # 简单任务首选 → MiniMax
  complex: "glm-5-1"                    # 复杂任务首选 → ARK
  complex_fallback: "kimi-for-coding"   # 复杂任务降级 → Kimi
  simple_fallback: "doubao-seed-2-0-pro" # 简单任务降级 → ARK
  fallback: "doubao-seed-2-0-pro"       # 最终降级 → ARK
  classifier: "doubao-seed-2-0-lite"    # 分类器模型 → ARK
```

## 项目结构

```
├── docker-compose.yml          # Docker 编排配置
├── .env.example                # 环境变量模板
├── .gitignore
└── router/
    ├── main.py                 # FastAPI 主应用 + 路由决策
    ├── classifier.py           # 请求复杂度分类器
    ├── channel.py              # 渠道状态管理（429封禁/恢复）
    ├── proxy.py                # 请求转发代理（流式/非流式）
    ├── providers.py            # 提供商配置（模型→渠道映射）
    ├── routing.yaml            # 路由策略配置
    ├── requirements.txt        # Python 依赖
    ├── requirements-dev.txt    # 开发依赖
    ├── Dockerfile
    └── tests/                  # 单元测试
        ├── test_proxy.py
        ├── test_classifier.py
        ├── test_channel.py
        └── test_providers.py
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查，返回可用渠道和提供商列表 |
| `/v1/models` | GET | 列出可用模型 |
| `/v1/chat/completions` | POST | 聊天补全，兼容 OpenAI API 格式 |

## 提供商详情

| 提供商 | 渠道标识 | Base URL | 模型 | 特殊 Header |
|--------|---------|----------|------|------------|
| 火山引擎 ARK | `ark` | `https://ark.cn-beijing.volces.com/api/coding/v3` | glm-5-1, doubao-seed-2-0-pro, doubao-seed-2-0-lite | — |
| Kimi Coding | `kimi` | `https://api.kimi.com/coding/v1` | kimi-for-coding | `anthropic-version: 2023-06-01` |
| MiniMax | `minimax` | `https://v2.aicodee.com/v1` | MiniMax-M2.7-highspeed | — |

> ⚠️ Kimi Coding API 目前仅允许 Coding Agent（如 Claude Code、Kimi CLI 等）访问，普通 API 调用可能被拒绝。

## 测试

```bash
cd router
pip install -r requirements-dev.txt
pytest tests/ -v
```

## 许可证

MIT
