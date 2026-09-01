# 阿里云香港 ECS 蓝绿部署

目标架构：香港地域 ECS、私有 OSS、Caddy HTTPS、蓝绿 Web 槽位、独立单 Worker、SQLite 每日备份。已完成的直播结果、同步中英文字幕、弹幕、剪辑和 AI 封面下载公开可见；提交任务、新建剪辑和为历史直播触发英文翻译需要邀请账号，Seedream 封面生成、改字、换版和用于成片仅允许管理员。生产模板把剪辑与 AI 封面限制为单并发、每段最长 10 分钟，临时 HLS、FFmpeg 或 OSS 错误自动重试 3 次。

## 1. 云资源

1. 在中国香港创建 Ubuntu 24.04 ECS，建议至少 2 vCPU / 2 GiB、40 GiB ESSD、按量付费和按使用流量公网带宽。2 GiB 规格应额外配置 2 GiB Swap。
2. 安全组仅开放：
   - TCP 22：限制为管理员当前公网 IP。
   - TCP 80、443：`0.0.0.0/0` 和 `::/0`。
   - 不开放 8000；应用只监听 `127.0.0.1`。
3. 在香港地域创建私有 OSS Bucket，并保持“阻止公共访问”开启。
4. 只给临时音频前缀 `pocket48-summarizer/` 设置 1 天生命周期规则。不要让该规则覆盖永久剪辑和 AI 封面前缀 `pocket48-clips/`。
5. 创建专用 RAM 用户，仅授予上述两个 Bucket 前缀的 `oss:PutObject`、`oss:GetObject` 和 `oss:DeleteObject`。不要使用主账号 AccessKey。

香港 ECS 不要求 ICP 备案。Cloudflare 记录使用 DNS only，由 Caddy 直接终止 TLS。

## 2. 安装

在 ECS 上：

```bash
sudo git clone https://github.com/RuokeZhang/pocket48-summarizer.git \
  /opt/pocket48-summarizer
sudo /opt/pocket48-summarizer/scripts/install-server.sh
sudoedit /etc/pocket48-summarizer/app.env
```

安装脚本会安装 FFmpeg/ffprobe、`fonts-noto-cjk`、
`fonts-lxgw-wenkai` 和 Ubuntu 24.04 官方
`fonts-noto-color-emoji=2.042-1`。包含 emoji 的字幕、弹幕卡片和手动
封面标题会由 Pillow 渲染为透明 RGBA 图集，再按事件时间交给 FFmpeg
合成；部署会实际检查普通 emoji、ZWJ 家庭、国旗、键帽和肤色序列。
填写 OSS、DashScope 和 LLM 凭证。要启用管理员
AI 封面，再填写 Ark 的 `ARK_API_KEY` 和控制台显示的实际
`ARK_SEEDREAM_MODEL`；不要猜测或把它们提交到仓库。未配置时普通总结和
剪辑继续可用，封面面板会明确显示 Seedream 尚未启用。
`ALIYUN_OSS_ENDPOINT` 使用香港内网 Endpoint 上传；
`ALIYUN_OSS_PUBLIC_ENDPOINT` 必须使用公网 Endpoint，供 DashScope
和浏览器读取短期签名 URL。剪辑上传到独立的
`ALIYUN_OSS_CLIP_PREFIX`，不要为该前缀配置自动过期。

房间上麦监控使用一个独立的 `pocket48-voice-monitor.service`，在同一
进程内并发运行杨晔 primary 任务以及王睿琦、杨冰怡命名任务；任一目标的
长时间录音不会阻塞其他目标继续每 60 秒轮询。附加目标只提交 member ID，
服务会使用生产凭证动态解析当前 channel/server，不猜测 server ID。三个
目标都空闲时总 API 负载约为每分钟 3 次请求。目标 ID 和
单次 4 小时/2 GiB、历史总量 20 GiB 和磁盘预留 5 GiB 的安全上限
来自仓库内不含秘密且由 release 管理的
`deploy/room-voice-target.env`；token 和动态 `pa` 种子只写入
`/var/lib/pocket48-summarizer/private/` 的 `0600` 文件。首次部署后
服务会以 `waiting_credentials` 保持就绪。`/room-voice` 对所有访客公开，
展示三个目标的脱敏状态和最近安全完成的本地 MP3 分段，并允许浏览器播放或
下载；旧 `/admin/room-voice` 地址只重定向到该页面。只有站点用户名大小写
折叠后精确等于 `ruoke` 的已登录用户可看到并提交短信/验证码维护表单，其他
用户（包括其他管理员）由服务端拒绝。成功后 monitor 会热加载凭证，无需
SSH 或重启服务。该登录会让同一账号的官方手机 App 退出，手机 App 再登录
也会使 monitor 凭证失效。

发布仍只切换一个 systemd 服务，但 readiness gate 会解析仓库 target
文件并检查 primary 以及每个命名目标的独立状态文件；任一状态报告
`configuration_error` 都会失败。回滚到未声明额外目标的旧 release 时
只要求 primary readiness。

首个真实上麦流出现前无法预先知道 CDN 主机，因此生产 target 文件显式
允许仅解析到全局公网地址、且端口为 1935/443 的 RTMP/RTMPS 主机；
FFmpeg 同时只启用 `tcp,tls,rtmp,rtmps` 协议。首次采集后应把脱敏状态
中显示的主机加入 `POCKET48_VOICE_STREAM_HOSTS`，再在后续 release 中
关闭公网主机回退。

AI 封面会把 MARK 原始帧临时写入该私有前缀，并向 Ark 提供短期签名
HTTPS URL；生成结束后应用删除临时帧，长期保留两种比例的无文字背景和
最终叠字 PNG。Ark Key、签名 URL 和 OSS 对象 Key 不会发送到浏览器。
首版固定使用 `AI_COVER_CONCURRENCY=1`，推荐保持默认的
1440×2560 与 2560×1440 尺寸。

首次发布带烧录字幕的剪辑功能前，确认生产依赖：

```bash
sudo apt-get update
sudo apt-get install -y fontconfig fonts-lxgw-wenkai fonts-noto-cjk \
  fonts-noto-color-emoji=2.042-1
ffprobe -version
ffmpeg -hide_banner -filters | grep -E '(^|[[:space:]])ass([[:space:]]|$)'
fc-match 'Noto Sans CJK SC'
fc-match 'Noto Serif CJK SC'
fc-match 'LXGW WenKai'
fc-match 'Noto Color Emoji'
```

`MAX_REPLAY_HOURS=0` 表示不设置回放小时上限。若已有服务器配置仍为 `3`，部署新版本前需要在 `/etc/pocket48-summarizer/app.env` 中改为 `0`。

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
sudo systemctl stop pocket48-worker 'pocket48-web@*'
sudo install -m 0600 -o pocket48 -g pocket48 \
  /tmp/pocket48-production.sqlite3 \
  /var/lib/pocket48-summarizer/pocket48.sqlite3
sudo rm -f /tmp/pocket48-production.sqlite3
```

不要直接复制活动数据库旁的 `-wal` / `-shm` 文件。历史 `completed` 任务在新服务启动后会自动成为公开结果。已有剪辑如需保留，可另外同步 `data/clips/` 到 `/var/lib/pocket48-summarizer/clips/`；服务启动后会把这些旧剪辑上传到私有 OSS，写入数据库记录并删除 ECS 本地副本。

## 4. 创建邀请账号

命令会从安全终端读取密码，不把密码放进 shell 参数：

```bash
sudo -u pocket48 bash -c '
  set -a
  source /etc/pocket48-summarizer/app.env
  set +a
  active=$(cat /var/lib/pocket48-summarizer/deploy/active-slot)
  exec /opt/pocket48-summarizer-slots/$active/.venv/bin/pocket48-users \
    create --username admin --admin
'
```

普通邀请账号去掉 `--admin`。所有有效邀请账号都能提交任务和剪辑公开结果；管理员还可查看所有未完成任务。管理命令还支持 `list`、`reset-password`、`enable` 和 `disable`。

## 5. DNS、启动与验证

把 `p48.ruokezhang.com` 的 DNS only A 记录指向 ECS 公网 IP。确认解析生效后，从管理 checkout 创建第一个 release：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now pocket48-summarizer-backup.timer
sudo systemctl enable --now caddy
sudo /opt/pocket48-summarizer/scripts/deploy-release.sh HEAD
curl --fail https://p48.ruokezhang.com/healthz
```

浏览器未登录时应看到最近公开结果和已有剪辑下载，并能打开剪辑编辑器体验设置但不能提交导出；普通账号可提交剪辑，但 AI 封面操作保持禁用。管理员应能在保留片段内 MARK 一个画面，填写封面文字并按需修改提示词，生成独立的 16:9 和 4:3 横屏 PNG；文字由模型直接绘制，改字或改提示词都会发起一次新的付费生成。16:9 可用于横屏成片第 0 帧，4:3 仅供下载。

## 6. 蓝绿发布与回滚

发布脚本把指定 Git ref 安装到独立 release/venv，启动备用 Web 槽并检查健康，然后通过 Caddy reload 原子切流量。发布期间已有页面和下载保持可用；新剪辑、边界分析和 AI 封面写操作会短暂返回维护提示。脚本先取得剪辑操作锁，再同时排空旧 `video_clips`、新 `video_clip_exports` 的运行任务，以及 `ai_cover_generations` 的排队/运行任务，避免在 FFmpeg、静音分析、付费 Seedream 请求或本地叠字期间切槽。独立 Worker 会在当前直播任务、上麦录音 ASR/总结任务或字幕翻译任务结束后切换，新任务可继续排队。Worker 每次启动都会回收租约已过期的卡死任务、上麦处理任务和翻译任务并重新排队；任务已持久化的 DashScope ID、ASR 结果、总结分块和英文字幕片段会继续复用，不会从头重复提交。独立房间上麦 monitor 也会切换到同一 release；若发布时正在录音，SIGINT 会先保留已完成分段，新进程随后可以继续采集仍在进行的同一条流。

推荐使用手动触发的 GitHub Actions 工作流。一次性初始化会生成独立部署密钥；该密钥在服务器端绑定强制命令，只能部署已经进入 `origin/main` 的完整提交 SHA，不能打开任意 root shell，也不会把现有管理员 SSH 私钥上传到 GitHub：

```bash
cd /Users/roxzhang/Desktop/pocket48-summarizer
./scripts/bootstrap-github-actions-deploy.sh
```

该命令会完成初始化、触发第一次发布，并在终端等待 GitHub Actions 结束。只初始化而不立即发布时可设置 `SKIP_INITIAL_DEPLOY=true`。

之后可在 GitHub 的 **Actions → Deploy production → Run workflow** 点击发布，或运行：

```bash
gh workflow run deploy-production.yml \
  --repo RuokeZhang/pocket48-summarizer \
  --ref main
gh run watch --repo RuokeZhang/pocket48-summarizer
```

工作流会先执行完整离线测试，再通过受限密钥部署同一个提交，最后检查生产健康状态、迁移表和旧剪辑回填。仓库 `production` Environment 可按需增加 required reviewers，部署并发固定为一个且不会取消正在执行的发布。

```bash
cd /opt/pocket48-summarizer
sudo git fetch origin
sudo git pull --ff-only
sudo ./scripts/deploy-release.sh origin/main
```

健康检查失败时脚本不会切流量。需要回到上一 Web release：

```bash
sudo /opt/pocket48-summarizer/scripts/rollback-release.sh
```

Migration 必须采用 expand/contract：发布时只新增兼容字段或表，不能在旧 Web/Worker 仍运行时删除或改名。Release 目录位于 `/opt/pocket48-summarizer-releases/`，不要删除 blue、green 或 Worker symlink 当前引用的目录。

## 7. 运维

```bash
sudo systemctl status 'pocket48-web@*' pocket48-worker \
  pocket48-voice-monitor caddy
sudo journalctl -u pocket48-worker -n 200 --no-pager
sudo journalctl -u pocket48-voice-monitor -n 200 --no-pager
sudo systemctl list-timers pocket48-summarizer-backup.timer
sudo ls -lh /var/backups/pocket48-summarizer
```

备份保留 14 天。`active-slot` 和 `previous-slot` 位于 `/var/lib/pocket48-summarizer/deploy/`；Caddy 当前上游位于 `/etc/caddy/pocket48-upstream.caddy`。
