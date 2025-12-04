# StudyGuardian — Smart Child Study Monitoring System

孩子学习桌智能守护系统：依托 Raspberry Pi 5 + ESP32-CAM，实现人脸识别与坐姿监测的本地化解决方案。

---
[点击观看项目介绍视频](https://www.bilibili.com/video/BV1cy2sBpEmN/)

## 1. Overview｜项目简介

StudyGuardian 部署在孩子学习桌前，通过本地推理做到：

- **身份识别**：区分孩子、家庭成员及陌生人。
- **实时姿态检测**：监测低头、驼背、脖子前伸、头部离桌面过近等情况。
- **持续性判定**：滑动窗口避免误报，对持续不良坐姿发出提醒。
- **多种提醒**：声音、屏幕提示或推送通知。
- **事件记录**：保存关键帧、时间戳、身份信息与持续时长，便于家长复盘。

> 所有视频分析均在 Raspberry Pi 5 本地完成，不上传云端，最大化保护隐私。

---

## 2. System Architecture｜系统架构

```
 ┌──────────────────────────────────────────┐
 │                StudyGuardian             │
 │      (Running on Raspberry Pi 5)         │
 │------------------------------------------│
 │ 1. Video Ingest (OpenCV)                 │
 │ 2. Face Recognition (face_recognition)   │
 │ 3. Posture Detection (MediaPipe Pose)    │
 │ 4. Bad Posture Analyzer (Rules + Window) │
 │ 5. Alerts (Audio / UI / Push)            │
 │ 6. Storage (Images + PostgreSQL events)  │
 └──────────────────────────────────────────┘
                     ▲
                     │ MJPEG Stream
                     │
         ┌────────────────────────┐
         │       ESP32-CAM        │
         │  Video Capture Device  │
         └────────────────────────┘
```

| 设备           | 作用                                 |
| -------------- | ------------------------------------ |
| ESP32-CAM      | 采集学习桌前视频并通过 Wi-Fi 推送    |
| Raspberry Pi 5 | 完成人脸识别、姿态识别、提醒与存储流程 |

---

## 3. Feature Highlights｜功能特性

### 3.1 Face Recognition｜人脸识别

- 支持注册多名家庭成员，加载 `data/known/<name>/` 的人脸库。
- `data/known/child/<name>/` 结构允许把多个孩子的照片按名字放在 `child` 分组下，identity 会记录为 `child/<name>` 便于区分。
- 姿态检测的范围由 `face_capture.groups` 控制：识别到的 identity 分组在其中才进入坐姿监测。默认配置中仅 `child` 分组会触发坐姿检测；若不配置分组则对所有人进行姿态检测。为避免偶发识别不到孩子导致跳过坐姿检测，新增滑动窗口：只要最近 `capture.allowed_group_grace_seconds`（默认 5 秒）内识别过目标分组，即便当前帧识别为 unknown 或未检出人脸也继续做姿态检测。
- 未识别的人物统一标记为 `unknown` 并记录事件。
- 陌生人（`unknown`）会自动按日期（`月日`）分目录保存在 `data/unknown/<MMDD>/<identity>_<HHMMSS>.jpg`，便于事后确认是谁靠近了学习桌，相关格式可在 `unknown_capture` 配置。
- `agent/recognition/face.py` 利用 `face_recognition` 提取 128D 特征，按阈值（默认 0.55）判断匹配结果并提供身份 + 置信度。
- `face_recognition.min_face_area_ratio`（默认 0.01）会忽略帧面积低于该比例的人脸框，减少远处衣物/背景误判为“有人”的情况。
- `config/settings.yaml` 中的 `face_capture.groups`（顶级目录，如 `child`）或 `unknown_capture.groups` 用于控制哪些分组进入坐姿检测；留空则所有识别到的人都会做姿态分析。

示例目录：

```
data/known/
  child/
    alice/
    bob/
  parent/
    mom/
```

### 3.2 Posture Detection｜坐姿监测

- 基于 MediaPipe Pose 获取关键点（nose、shoulders、hips 等）。
- 支持检测：过度低头、脖子前伸、头部过近、身体侧倾/弓背。
- 阈值与灵敏度通过配置文件调整。
- `agent/posture/analyze.py` 使用 MediaPipe Pose 计算鼻点相较肩膀的偏移与颈部夹角，集中判断“低头”或“脖子前伸”。

示例规则：

```python
nose_y = landmarks["nose"].y
shoulder_y = (landmarks["left_shoulder"].y + landmarks["right_shoulder"].y) / 2
if nose_y - shoulder_y > 0.12:
    bad_posture = True
```

### 3.3 Alerts｜提醒方式

- 树莓派本地播放提示音（`pygame.mixer`）。
- HDMI 屏幕弹出提醒文字。
- 可选：Telegram 或微信推送至家长手机（MQTT 当前未启用）。

### 3.4 Logging & Storage｜事件记录

- 记录姿势类型、持续时间、抓拍帧路径、时间戳与身份标签。
- `agent/storage/postgres.py` 会把每帧识别结果入表 `posture_events`，目前仅通过 PostgreSQL 存储，便于远程分析与备份。

### 3.5 Environment (optional)｜环境监测（可选）

- 支持读取 DHT22 温湿度传感器（默认关闭）；开启 `config/settings.yaml` 的 `dht22.enable` 即可。
- 依赖 `adafruit-circuitpython-dht` 与 `adafruit-blinka`，采样间隔由 `poll_interval_seconds` 控制，数据写入 PostgreSQL 表 `environment_events`，前端通过 GET `/api/env` 读取最新值。默认只保留最近 3 天（`dht22.retention_days` 可调整或设为 null 关闭自动清理）。
- `environment_events` 表结构：`id BIGSERIAL PRIMARY KEY`、`temperature DOUBLE PRECISION`、`humidity DOUBLE PRECISION`、`timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP`。

#### PostgreSQL 表结构

- `face_captures`：记录所有识别到的人员（包含 `unknown`）

| 字段             | 类型                                                                 | 说明                               |
| ---------------- | -------------------------------------------------------------------- | ---------------------------------- |
| `id`             | `SERIAL PRIMARY KEY`                                                  | 唯一自增标识                        |
| `identity`       | `TEXT NOT NULL`                                                       | 识别身份（如 `child/恩恩`、`unknown`） |
| `group_tag`      | `TEXT NOT NULL`                                                       | 身份分组（child/parent/unknown 等）  |
| `face_distance`  | `DOUBLE PRECISION`                                                    | 人脸比对距离                        |
| `frame_path`     | `TEXT`                                                                | 保存的帧路径                        |
| `snapshot_type`  | `TEXT`                                                                | 预留字段（enter/exit 等）           |
| `timestamp`      | `TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP`                 | 捕获时间                            |

- `posture_events`：仅针对受监控儿童的坐姿记录

| 字段              | 类型                                                                 | 说明                                  |
| ----------------- | -------------------------------------------------------------------- | ------------------------------------- |
| `id`              | `SERIAL PRIMARY KEY`                                                  | 唯一自增标识                           |
| `identity`        | `TEXT NOT NULL`                                                       | 当前识别身份（child/xxx）               |
| `is_bad`          | `BOOLEAN NOT NULL`                                                    | 是否判定为不良坐姿                      |
| `nose_drop`       | `DOUBLE PRECISION`                                                    | 鼻尖相对双肩垂直偏移量                  |
| `neck_angle`      | `DOUBLE PRECISION`                                                    | 颈部与躯干的夹角                        |
| `reasons`         | `TEXT`                                                                | 命中规则／原因文本                      |
| `face_distance`   | `DOUBLE PRECISION`                                                    | 人脸比对距离                            |
| `frame_path`      | `TEXT`                                                                | 坐姿截图路径（复用 face capture 图像） |
| `face_capture_id` | `INTEGER REFERENCES face_captures(id)`                                | 关联的人脸抓拍记录                      |
| `timestamp`       | `TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP`                 | 事件发生时间                           |

---

## 4. Technical Roadmap｜技术路线

1. **Video Ingest**：OpenCV 读取 ESP32-CAM 提供的 MJPEG 流（示例 `http://<esp32-ip>:81/stream`）。
2. **Face Recognition**：使用 `face_recognition`（可替换成 DeepFace/InsightFace），比较实时特征与孩子图库。
3. **Pose Estimation**：MediaPipe Pose 解析关键点，计算夹角、相对位移判断姿势。
4. **Bad Posture Analyzer**：滑动窗口与规则结合，过滤单帧误判。
5. **Alerts**：音频、屏幕、消息推送等多渠道提醒。
6. **Storage**：图像与事件入库（`data/captures`、SQLite）。

---

## 5. Project Structure｜目录结构

```
StudyGuardian/
  README.md
  requirements.txt
  agent/
    __init__.py
    main.py
    capture/
      ingest.py
    recognition/
      face.py
    posture/
      analyze.py
    storage/
      postgres.py
  config/
    settings.yaml
  backend/
    README.md
  frontend/
    README.md
  data/
    known/
      child/
      parent/
    captures/
  logs/
```

模块说明：`agent.capture` 负责 MJPEG 拉流，`agent.recognition` 管理 face_recognition，`agent.posture` 封装 MediaPipe Pose，`agent.storage` 统一 PostgreSQL 写入，`agent.main` 意在将这些功能串联为观察 agent。

---

## 6. Install & Deploy｜安装与部署速览

### 6.1 准备依赖
- 系统：Debian/Ubuntu（Raspberry Pi OS OK）
- 依赖：`python3`、`cargo`、`npm`、`systemd`、`nginx`、PostgreSQL
- 配置：复制 `config/settings.yaml.example` 为 `config/settings.yaml` 并填好 `storage.postgres_dsn`、`camera_url`、SSL 等

### 6.2 一键构建
```bash
scripts/build.sh
```
作用：格式化 + 编译 Rust backend、前端打包、创建 `.venv` 并安装 agent 依赖、ruff 检查与 byte-compile。

### 6.3 部署/启动（含 systemd + nginx）
```bash
sudo scripts/deploy.sh install   # 首次安装：写入 systemd、同步前端静态、配置 nginx、启动服务
sudo scripts/deploy.sh restart   # 重新构建并重启
sudo scripts/deploy.sh status    # 查看服务状态
```
默认服务：
- `studyguardian-agent.service`：Python agent（使用 `.venv/bin/python -m agent.main`）
- `studyguardian-backend.service`：Rust 后端（`backend/target/release/studyguardian-backend`）

静态资源默认同步到 `/var/www/studyguardian`，nginx 监听 `server.external_port`（默认 443）。需要 root 权限。

### 6.4 仅启动 Agent（调试）
```bash
scripts/start_agent.sh
```
作用：创建/复用 `.venv`、安装依赖后前台运行 `python -m agent.main`。

---

## 7. PostgreSQL Support

- 设置 `config/settings.yaml` 中 `storage.postgres_dsn`，示例 `postgresql://guard:secret@raspberrypi/guardian`。
- 依赖 `psycopg2-binary`，启动时会自动创建 `posture_events` 表并持续写入事件，供远端查询或备份。

### PostgreSQL Installation Helper

在 Raspberry Pi 5 这类 Debian/Ubuntu 衍生系统上可以直接运行 `scripts/setup_postgres.sh`（需要 `sudo` 权限）来安装 PostgreSQL、创建数据库与用户名，并打印出一条 DSN；脚本会自动切换到 `/tmp` 避免 `postgres` 用户因为访问不到仓库目录而打印 “could not change directory” 的警告：

```bash
sudo scripts/setup_postgres.sh
```

脚本会默认开启 `postgresql` 服务、将 `listen_addresses` 设置为 `0.0.0.0` 并允许远程 md5 连接，这让你能够在局域网中用 GUI 工具（例如 `pgAdmin` 或 `DataGrip`）连接树莓派。尽管服务监听所有网络，agent 仍然建议在 `config/settings.yaml` 把 `storage.postgres_dsn` 设置为 `postgresql://guardian:study_guardian@127.0.0.1/study_guardian`，以便通过 loopback 访问、避免额外网络开销；如果你需要从其他机器进行查询，可以在 GUI 里使用 `raspberrypi.local` 或实际 IP + 同样的用户名/密码。 如需不同的库名、用户名或密码，可在运行前通过 `PGSETUP_DB_NAME`、`PGSETUP_DB_USER`、`PGSETUP_DB_PASS` 这几个环境变量调整。运行后将输出正确的 DSN，复制到 `config/settings.yaml > storage.postgres_dsn` 即可让 agent 成功连接数据库。

## 7. SSL 证书（DNS-01 + acme.sh + DNSPod）

- 适用于无法开放 80/443 但希望自动签发/续期的场景；脚本仅需普通用户身份。
- 前置：在 DNSPod 控制台创建 API ID/Token，并在运行前导出 `DP_Id`、`DP_Key`（可选 `ACME_EMAIL` 用于注册账号，`ACME_RELOAD_CMD` 用于续期后自动重载如 `systemctl reload nginx`）。
- 也可在 `config/settings.yaml > ssl` 写入 `domain`、`wildcard`、`dp_id`、`dp_key`、`acme_email`、`acme_server`、`reload_cmd`，脚本会自动读取；CLI 参数 / 环境变量优先生效。
- 生成单域名证书：
  ```bash
  DP_Id=xxx DP_Key=yyy scripts/issue_ssl_cert.sh proxy.example.com
  ```
- 生成泛域名（同时覆盖根域名）：
  ```bash
  DP_Id=xxx DP_Key=yyy scripts/issue_ssl_cert.sh example.com --wildcard
  ```
- 脚本会自动安装/升级 acme.sh，默认使用 Let's Encrypt + EC-256；证书安装到 `~/.acme.sh/<domain>_ecc/`，其中：
  - FULLCHAIN：`~/.acme.sh/<domain>_ecc/fullchain.cer`
  - KEY：`~/.acme.sh/<domain>_ecc/<domain>.key`
- Nginx 示例（同一张证书可复用在同一域名的多端口进程）：
  ```nginx
  ssl_certificate     /home/<user>/.acme.sh/proxy.example.com_ecc/fullchain.cer;
  ssl_certificate_key /home/<user>/.acme.sh/proxy.example.com_ecc/proxy.example.com.key;
  ```

---
## 8. Future Work｜未来扩展

- 多摄像头融合（正面 + 侧面）。
- 脊柱侧弯监测、学习时长统计与专注度测量。
- 自动生成学习日报或推送周报。
- 移动端 App（Flutter / React Native）。
- 使用轻量 ML 分类器替代规则坐姿判定。

---

欢迎贡献改进建议或提交 PR，让孩子拥有更健康的学习姿势守护体验。#
