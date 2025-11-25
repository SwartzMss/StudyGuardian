# Frontend Roadmap

目标：给家长提供实时监控面板、事件日志与配置管理，优先兼容手机与桌面浏览器。

## 技术栈建议
- Vite + React + TypeScript。
- 状态/数据：TanStack Query（请求缓存与刷新），Zustand/Context 存放 UI 状态。
- 组件：轻量 UI 库（例如 Mantine/Chakra 或精简 Ant Design），图表用 `recharts` 或 `@tanstack/react-charts`。
- 实时：WebSocket 订阅 `/ws/events`，回退到轮询。

## 信息架构（查看为主）
1) **实时面板**：左侧/顶部展示 MJPEG/RTSP 视频（或占位图），右侧显示当前身份、姿态状态、最近告警。  
2) **事件时间线**：列表 + 过滤器（身份、时间、姿态好/坏），点击行弹出侧边抽屉查看帧截图与具体指标。  
3) **统计概览**：今日/本周不良姿态次数、平均持续时间、Top 原因。  
4) **设备状态/诊断**：显示 agent 运行状态、PostgreSQL 连接、磁盘占用、最近错误日志摘要。  
> 仅做查看，不提供在线修改 `settings.yaml` 或上传人脸库的入口；需要修改配置时仍通过树莓派本地文件/CLI 处理。

## 组件拆分
- `LiveStreamPlayer`：播放流或占位图；显示当前 FPS/延迟。  
- `PostureBadge`：根据 posture 状态显示颜色与原因。  
- `EventFilters` + `EventTable/Timeline`：复用在姿态和人脸事件。  
- `StatsCards`、`TrendChart`：统计页。  
- `DiagnosticsPanel`：健康信息、日志片段。  
- （可选只读）`FaceLibraryView`：展示已知身份列表，若后端暴露只读清单。

## 交互与刷新策略
- 拉取接口：`useQuery` 每 30–60s 轮询健康状态；事件列表支持“即时刷新”按钮。  
- 实时流：连接 WebSocket 后把 posture/face 事件推到全局 store，再驱动实时面板和通知。  
- 错误兜底：流断开时在 UI 顶部出现 toast/banner；展示重连倒计时。  

## 目录草案
```
frontend/
  src/
    api/           # fetch 封装 + 类型
    components/    # 共享 UI
    pages/         # dashboard/events/settings/diagnostics
    store/         # zustand 或 context
    hooks/
    styles/
    main.tsx
```

## UI 风格提示
- 儿童学习场景：明亮、低饱和的色彩（淡蓝/薄荷/暖黄），大号易读字体。
- 提醒状态用明确的色彩与文案（Good/Needs Attention）。
- 移动端首屏优先：实时状态卡片在上方，过滤器折叠，表格切成卡片式列表。

## 快速预览（仅查看）
- 已改为 React + Vite。首次安装依赖：
  ```bash
  cd frontend
  npm install
  ```
- 开发/预览（dev server 监听 0.0.0.0，可用局域网 IP 访问）：
  ```bash
  npm run dev   # http://localhost:5173
  # 浏览器访问时可带查询参数：
  # ?stream=http://<esp32-ip>:81/stream  （MJPEG 流）
  # 可选只读事件 WS：&ws=ws://<backend-host>/ws/events
  ```
- 构建/静态发布：
  ```bash
  npm run build      # 输出到 dist/
  npm run preview    # 本地预览构建产物
  # 将 dist/ 部署到任意静态服务器，同样通过 ?stream=/?ws= 参数查看
  ```
