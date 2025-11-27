# `deploy.sh` 设计（HTTPS-only @ 5009）

面向需求：统一用 5009 端口 + 域名 `5009` 对外，禁用 HTTP，仅走 HTTPS，脚本需要负责 agent 代码格式检查、前端/后端编译，以及 Nginx 配置/上线。证书获取参考 `scripts/issue_ssl_cert.sh`（acme.sh + DNSPod DNS-01），默认生成到 `$HOME/.acme.sh/<domain>_ecc/`，并会把 `ssl.cert_path` / `ssl.key_path` 写回 `config/settings.yaml`（脚本默认 `--force`，确保路径写入）。

## 预设与可配置项
- 端口：`PORT=5009`（外部暴露，Nginx 监听，亦写入 `server.external_port` 供参考）
- 域名：`DOMAIN=5009`（按需替换成真实域名）
- TLS：读取 `config/settings.yaml` 中的 `ssl.cert_path` / `ssl.key_path`（`issue_ssl_cert.sh` 会写入），未设置时回退：  
  `SSL_CERT=${SSL_CERT:-$HOME/.acme.sh/${DOMAIN}_ecc/fullchain.cer}`  
  `SSL_KEY=${SSL_KEY:-$HOME/.acme.sh/${DOMAIN}_ecc/${DOMAIN}.key}`
- 部署路径：`/opt/studyguardian`（代码/产物）；静态文件：`/var/www/studyguardian-5009`
- 后端监听：`BIND_ADDRESS=127.0.0.1:8000`（默认值），也可在 `config/settings.yaml` 的 `server.backend_bind` 设置；env 优先，后端代码已回退读取此字段
- 运行用户：`SYSTEM_USER=studyguardian`
- 环境：`.env.deploy`（可放 `DATABASE_URL`、`BIND_ADDRESS` 等）

## 依赖检查（脚本开头）
- `python3`, `pip`, `ruff`（agent 格式检查）  
- `node`, `npm`（前端 build）  
- `cargo`, `rustc`（后端 build）  
- `nginx`, `setcap`（如需放行非 root 端口）  
缺失则提示并退出。

## `deploy.sh` 流程草案
```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-5009}"
DOMAIN="${DOMAIN:-5009}"
SSL_CERT="${SSL_CERT:-$HOME/.acme.sh/${DOMAIN}_ecc/fullchain.cer}"  # 若 settings.yaml 内 ssl.cert_path 存在，可覆盖
SSL_KEY="${SSL_KEY:-$HOME/.acme.sh/${DOMAIN}_ecc/${DOMAIN}.key}"    # 若 settings.yaml 内 ssl.key_path 存在，可覆盖
BACKEND_BIND="${BIND_ADDRESS:-127.0.0.1:8000}"
SYSTEM_USER="${SYSTEM_USER:-studyguardian}"
FRONTEND_OUT="${FRONTEND_OUT:-/var/www/studyguardian-5009}"
BIN_OUT="${BIN_OUT:-/opt/studyguardian/backend}"

source "$ROOT/.env.deploy" 2>/dev/null || true

# 0) 如需证书：DP_Id=xxx DP_Key=yyy "$ROOT/scripts/issue_ssl_cert.sh" "$DOMAIN" [--wildcard]
#    证书默认在 $HOME/.acme.sh/${DOMAIN}_ecc/{fullchain.cer,${DOMAIN}.key}，并写入 config/settings.yaml 的 ssl.cert_path/key_path
#    可设 ACME_RELOAD_CMD="systemctl reload nginx" 让续期后自动重载

# 1) 依赖探测（python/node/cargo/nginx），缺失则退出

# 2) Agent 代码格式 + 语法检查
python3 -m pip install --upgrade pip
python3 -m pip install -r "$ROOT/requirements.txt" "ruff>=0.5"
python3 -m ruff format --check "$ROOT/agent"
python3 -m ruff check "$ROOT/agent"
python3 -m compileall "$ROOT/agent"

# 3) 后端（Rust）检查 + 编译
pushd "$ROOT/backend"
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo build --release
install -d "$BIN_OUT"
install -m 0755 "target/release/studyguardian-backend" "$BIN_OUT/"
popd

# 4) 前端编译
pushd "$ROOT/frontend"
npm ci
npm run build
popd
install -d "$FRONTEND_OUT"
rsync -a --delete "$ROOT/frontend/dist/" "$FRONTEND_OUT/"

# 5) Nginx 配置渲染到 /etc/nginx/sites-available/studyguardian-5009.conf
#    - 监听 5009 ssl; 不监听 80
#    - /api 反代到 $BACKEND_BIND
#    - / 静态文件，SPA 回退到 /index.html
#    - 限制仅 TLS，HSTS 可按需开启

# 6) Reload 服务
nginx -t
systemctl reload nginx
systemctl restart studyguardian-backend.service  # 如有 systemd 单元
```

## Nginx 配置示例（仅 HTTPS，监听 5009）
`/etc/nginx/sites-available/studyguardian-5009.conf`：
```
server {
    listen 5009 ssl http2;
    server_name 5009;               # 替换为真实域名

    ssl_certificate     /home/<user>/.acme.sh/5009_ecc/fullchain.cer;   # 用真实域名替换 5009
    ssl_certificate_key /home/<user>/.acme.sh/5009_ecc/5009.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;

    # 禁用明文 HTTP：不配置 80；如需硬拒绝，可单独 80 返回 444。

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    location / {
        root /var/www/studyguardian-5009;
        try_files $uri /index.html;
    }
}
```
链接并启用：`ln -sf /etc/nginx/sites-available/studyguardian-5009.conf /etc/nginx/sites-enabled/ && nginx -t && systemctl reload nginx`

## 可选 systemd 单元（后端）
`/etc/systemd/system/studyguardian-backend.service`：
```
[Unit]
Description=StudyGuardian backend
After=network.target

[Service]
User=studyguardian
Group=studyguardian
WorkingDirectory=/opt/studyguardian/backend
EnvironmentFile=/opt/studyguardian/.env.deploy
ExecStart=/opt/studyguardian/backend/studyguardian-backend
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## 验收/检查清单
- [ ] `deploy.sh` 可一键执行，缺依赖时报错退出
- [ ] agent 通过 `ruff format --check` 与 `ruff check`
- [ ] `cargo fmt --check` / `cargo clippy -D warnings` / `cargo build --release` 通过
- [ ] `npm ci && npm run build` 成功，静态文件同步到 `FRONTEND_OUT`
- [ ] Nginx 只监听 5009 + TLS，HTTP 未开启；`nginx -t` 通过，`systemctl reload nginx` 成功
- [ ] 后端进程已重启，`curl -k https://<DOMAIN>:5009/api/face-captures` 返回 200/JSON
