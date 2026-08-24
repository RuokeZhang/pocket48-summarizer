# 阿里云北京 ECS 部署

目标架构：北京地域 2 vCPU / 4 GiB ECS、私有 OSS、Caddy HTTPS、systemd 单 Worker、SQLite 每日备份。已完成的直播结果公开可见；提交任务和剪视频需要邀请账号。生产模板把剪辑限制为单并发、每段最长 10 分钟。

## 1. 云资源

1. 在华北 2（北京）创建 Ubuntu 24.04 ECS，建议 40 GiB ESSD、按量付费和按使用流量公网带宽。
2. 安全组仅开放：
   - TCP 22：限制为管理员当前公网 IP。
   - TCP 80、443：`0.0.0.0/0` 和 `::/0`。
   - 不开放 8000；应用只监听 `127.0.0.1`。
3. 在同一地域创建私有 OSS Bucket，并保持“阻止公共访问”开启。
4. 给 `pocket48-summarizer/` 前缀设置 1 天生命周期规则，兜底清理失败的临时音频。
5. 创建专用 RAM 用户，仅授予该 Bucket 前缀的 `oss:PutObject`、`oss:GetObject` 和 `oss:DeleteObject`。不要使用主账号 AccessKey。

国内 ECS 绑定公开域名前通常需要 ICP 备案。先确认 `ruokezhang.com` 已备案；否则 DNS 和 HTTPS 即使配置成功，也可能被云厂商阻断。

## 2. 安装

在 ECS 上：

```bash
sudo git clone https://github.com/RuokeZhang/pocket48-summarizer.git \
  /opt/pocket48-summarizer
sudo /opt/pocket48-summarizer/scripts/install-server.sh
sudoedit /etc/pocket48-summarizer/app.env
```

填写 OSS、DashScope 和 LLM 凭证。`ALIYUN_OSS_ENDPOINT` 使用北京内网 Endpoint 上传；`ALIYUN_OSS_PUBLIC_ENDPOINT` 必须使用公网 Endpoint，供 DashScope 读取短期签名 URL。

环境文件权限默认为 `root:pocket48 0640`。不要把它复制进 Git 仓库或粘贴到日志。

## 3. 迁移本机已有结果

先停止本机正在写数据库的应用，再用 SQLite 在线备份生成单文件快照：

```bash
cd /Users/roxzhang/Desktop/pocket48-summarizer
sqlite3 data/pocket48.sqlite3 \
  ".backup '/tmp/pocket48-production.sqlite3'"
scp /tmp/pocket48-production.sqlite3 root@<ECS_IP>:/tmp/
```

在 ECS 上安装快照：

```bash
sudo systemctl stop pocket48-summarizer
sudo install -m 0600 -o pocket48 -g pocket48 \
  /tmp/pocket48-production.sqlite3 \
  /var/lib/pocket48-summarizer/pocket48.sqlite3
sudo rm -f /tmp/pocket48-production.sqlite3
```

不要直接复制活动数据库旁的 `-wal` / `-shm` 文件。历史 `completed` 任务在新服务启动后会自动成为公开结果。已有剪辑如需保留，可另外同步 `data/clips/` 到 `/var/lib/pocket48-summarizer/clips/`。

## 4. 创建邀请账号

命令会从安全终端读取密码，不把密码放进 shell 参数：

```bash
sudo -u pocket48 bash -c '
  set -a
  source /etc/pocket48-summarizer/app.env
  set +a
  exec /opt/pocket48-summarizer/.venv/bin/pocket48-users \
    create --username admin --admin
'
```

普通邀请账号去掉 `--admin`。所有有效邀请账号都能提交任务和剪辑公开结果；管理员还可查看所有未完成任务。管理命令还支持 `list`、`reset-password`、`enable` 和 `disable`。

## 5. DNS、启动与验证

把 `p48.ruokezhang.com` 的 A 记录指向 ECS 公网 IP。确认解析生效后：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now pocket48-summarizer
sudo systemctl enable --now pocket48-summarizer-backup.timer
sudo systemctl enable --now caddy
curl --fail http://127.0.0.1:8000/healthz
curl --fail https://p48.ruokezhang.com/healthz
```

浏览器未登录时应看到最近公开结果，但看不到提交表单和剪辑按钮；登录后应能提交任务并剪辑时间线。

## 6. 运维

```bash
sudo systemctl status pocket48-summarizer caddy
sudo journalctl -u pocket48-summarizer -n 200 --no-pager
sudo systemctl list-timers pocket48-summarizer-backup.timer
sudo ls -lh /var/backups/pocket48-summarizer
```

备份保留 14 天。升级代码后，在 `/opt/pocket48-summarizer` 拉取最新提交、重新安装依赖和项目，再重启服务。
