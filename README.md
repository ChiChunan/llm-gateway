# LLM Gateway

一个基于请求复杂度智能路由的 LLM 网关，支持多渠道管理、自动限流处理和余额监控。

## 功能特性

- **智能路由**：使用轻量 LLM 分类器自动判断请求复杂度，简单任务路由至 `doubao-lite-32k`，复杂任务路由至 `doubao-pro-128k`
- **多渠道管理**：支持火山引擎豆包（lite/pro）、Kimi（8k/128k）等多渠道，自动故障切换
- **429 限流处理**：自动解析上游限流错误中的恢复时间，智能封禁/恢复渠道
- **Kimi 余额监控**：定时检查 Kimi 账户余额，余额不足时自动禁用相关渠道
- **流式响应支持**：完整透传 SSE 流式响应，兼顾低延迟与稳定性

## 架构

```
Client → Router (FastAPI) → new-api → 各 LLM 提供商
```

- **Router**：请求分类、路由决策、渠道管理
- **new-api**：统一 API 网关，管理各渠道 API Key 和配额

## 快速开始

### 前置要求

- Docker & Docker Compose
- 各 LLM 提供商的 API Key

### 部署

1. 克隆仓库并进入项目目录

2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入各 API Key
```

3. 启动服务

```bash
docker compose up -d
```

服务启动后：
- Router 监听 `http://localhost:8000`
- new-api 监听 `http://127.0.0.1:3000`

### 使用

```bash
# 查看可用模型
curl http://localhost:8000/v1/models

# 健康检查
curl http://localhost:8000/health

# 发送聊天请求（model 设为 auto 启用智能路由）
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

## 配置

路由策略在 `router/routing.yaml` 中配置：

```yaml
routing:
  simple: "doubao-lite-32k"      # 简单任务模型
  complex: "doubao-pro-128k"     # 复杂任务模型
  fallback: "doubao-pro-128k"    # 降级模型
  classifier: "doubao-lite-32k"  # 分类器使用的模型

thresholds:
  kimi_balance_warn: 10.0        # Kimi 余额告警阈值

kimi:
  balance_check_interval: 300    # 余额检查间隔（秒）
  balance_api: "https://platform.kimi.com/v1/users/me/balance"
```

## 项目结构

```
├── docker-compose.yml          # Docker 编排配置
├── .env.example                # 环境变量模板
├── .gitignore
└── router/
    ├── main.py                 # FastAPI 主应用
    ├── classifier.py           # 请求复杂度分类器
    ├── channel.py              # 渠道状态管理
    ├── proxy.py                # 请求转发代理
    ├── routing.yaml            # 路由策略配置
    ├── requirements.txt        # Python 依赖
    ├── Dockerfile
    └── tests/                  # 单元测试
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查，返回可用渠道列表 |
| `/v1/models` | GET | 列出可用模型 |
| `/v1/chat/completions` | POST | 聊天补全，兼容 OpenAI API 格式 |

## 许可证

MIT
