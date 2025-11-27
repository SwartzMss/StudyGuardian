# StudyGuardian — Smart Child Study Monitoring System

孩子学习桌智能守护系统：依托 Raspberry Pi 5 + ESP32-CAM，实现人脸识别与坐姿监测的本地化解决方案。

---

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
- 仅当识别为孩子本人时才进入坐姿监测流程，可通过 `monitored_groups` 或 `monitored_identities` 控制允许的目录。
- 未识别的人物统一标记为 `unknown` 并记录事件。
- 陌生人（`unknown`）会自动按日期（`月日`）分目录保存在 `data/unknown/<MMDD>/<identity>_<HHMMSS>.jpg`，便于事后确认是谁靠近了学习桌，相关格式可在 `unknown_capture` 配置。
- `agent/recognition/face.py` 利用 `face_recognition` 提取 128D 特征，按阈值（默认 0.55）判断匹配结果并提供身份 + 置信度。
- `config/settings.yaml` 中的 `monitored_groups`（按顶级目录，如 `child`）或 `monitored_identities`（按完整路径，如 `child/alice`）控制哪些身份需要进入坐姿检测，默认仅监测 `child`，其余身份直接跳过以节省算力。

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

## 6. PostgreSQL Support

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
