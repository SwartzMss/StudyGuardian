# Backend Roadmap

面向树莓派侧 agent 的轻量网关：统一暴露 API、实时事件流与健康检查，并把后续手机/PC 前端都指到这一层（不修改 agent 的 `settings.yaml`，仅读取数据库与状态）。

## 架构建议
- 语言/框架：Rust + Axum（或 Actix）。直接读 agent 已写入的 PostgreSQL，避免重复算力。
- 传输：HTTP/JSON 为主，WebSocket 推送实时事件；后台任务用 Tokio 定时器即可。
- 鉴权：本地部署可用 API Key，开放到局域网时再接入 JWT/简单账号体系。
- 部署：单二进制 + `.env`，通过 systemd 常驻；开放 0.0.0.0 但在局域网路由器内做白名单。

## 服务模块
- `api`：REST 入口；健康检查、事件查询（只读）。
- `realtime`：基于 PostgreSQL `LISTEN/NOTIFY` 或轮询，将新 posture/face 事件推到 WebSocket。
- `storage`：PostgreSQL 访问层，复用现有表 `posture_events`、`face_captures`。
- `alerts`：封装通知（声光提示、Webhook/Telegram），供 API 或后台任务调用。
- `jobs`：周期性统计（每日/每周坐姿报告），生成概要 JSON。

## API 草案
- `GET /api/health`：数据库可用性 + agent 运行状态（可通过最近心跳或日志文件时间推断）。
- `GET /api/events/posture`：分页查询，支持 `identity/group/is_bad/date_range` 过滤。
- `GET /api/events/faces`：查询 face 捕获记录，支持 `identity=unknown` 过滤。
- `GET /api/stats/summary`：今日/本周次数、平均持续时间、最常见原因。
- `WS /ws/events`：推送最新 posture/face 事件，前端用于实时面板。

## 数据流
1) agent 持续写入 PostgreSQL。  
2) 后端监听数据库，转成 API 与 WS 推送。  

## 目录与代码组织（示例）
```
backend/
  src/
    api/        # handlers + routers
    storage/    # Postgres models + queries
    realtime/   # LISTEN/NOTIFY + WS broadcaster
    alerts/     # 推送/本地声音调用接口
    jobs/       # 定时统计
    main.rs
  config.example.env
```

## 开发要点
- 只读：不写入 `settings.yaml`，不执行控制类 API；后端仅读数据库/状态并向前端展示。
- WebSocket 推送可做简单 backpressure（队列长度、掉线自动清理）。
- 所有查询默认限速/分页，防止前端一口气扫全表拖慢树莓派。
- 提供简单的 `make dev`：启动 Postgres（或指向外部）、跑后端、开启热重载（`cargo watch -x run`）。

## 快速运行
```bash
cd backend
# 可选：在环境变量中设置 DATABASE_URL（默认从 config/settings.yaml 的 storage.postgres_dsn 读取）
cargo run         # 监听 0.0.0.0:8000
```

接口示例：
- `GET /api/face-captures?limit=40`
