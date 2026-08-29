# Pocket48 Replay Summarizer

一个可在本机或自托管服务器运行的 Web 应用：粘贴公开的口袋48成员直播分享链接，自动提取回放音频、生成时间戳字幕、解析弹幕，并输出带字幕证据的中文结构化总结。

示例输入：

```text
https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=1297967327104274432
```

## 功能边界

- 仅处理无需登录即可访问的已结束公开回放。
- 不支持实时直播、私有内容、口袋48登录、`pa` 签名或网易云信 QChat。
- 不永久保存原始整场视频；通过 FFmpeg 从 HLS 直接提取临时音频。
- 字幕由可配置的阿里云百炼 DashScope 非实时语音识别模型生成。
- 总结通过可配置的 OpenAI-compatible `/chat/completions` API 生成。
- SQLite 保存任务、中英文字幕、翻译队列、弹幕、ASR 原始 JSON 和总结。
- 成功识别后删除本地临时音频和私有 OSS 临时对象。
- 默认仅监听 `127.0.0.1`。
- 可启用邀请账号：已完成结果公开浏览，提交新任务和剪视频需要登录。
- 同一直播全局去重；邀请账号默认每天最多提交 3 个任务。
- 默认不限制回放时长；如需保护资源，可通过 `MAX_REPLAY_HOURS` 设置正数小时上限，`0` 表示关闭。
- 时间线剪辑会先打开桌面弹窗或移动端全屏编辑器，可调整起止点、吸附字幕与邻近静音、点击锁定红色时间标记，并在标记处分割、删除或恢复片段；预览和导出会按原顺序自动跳过删除区间并拼接所有保留片段。
- 烧录字幕支持按比例调整字号、自定义文字与底色，并提供带对比度检查的主题预设；横屏预览与最终 ASS 导出使用同一套 1920×1080 比例坐标，长字幕固定在左栏换行，弹幕使用与预览等宽的右栏卡片。
- 桌面剪辑器可选择保留原竖屏 9:16 布局，或导出 1920×1080
  横屏：中间放竖屏视频，左右使用米白画布，左侧显示红色字幕，右侧
  使用玫瑰粉作者名和暖深色弹幕卡片。选择横屏后，原右侧时间线与
  设置工具区会移动到 16:9 预览下方。横屏字幕默认使用开源
  霞鹜文楷，也可切换思源宋体或思源黑体；移动端保持原竖屏编辑体验。
- 桌面剪辑器内置管理员专用的 Seedream AI 封面工作流：以精细时间线的
  MARK 画面为参考，一次独立生成 16:9 与 4:3 两种横屏封面。模型保留原
  人物、房间和画面主色，只做自然扩图与轻度修图，也不固定使用粉色或重绘
  宏大场景。封面上的中文标题由 Seedream 直接绘制：管理员填写封面文字，
  并可在网页上直接编辑提交给模型的完整提示词，`{title}` 与 `{ratio}` 两个
  占位符会被替换为封面文字与目标比例。因此改字或改提示词都会重新发起一次
  付费生成。可下载 PNG、保留历史版本或付费换一版。游客和普通账号能看到已有
  结果，但不能调用生成或将封面用于成片。
- 16:9 AI 封面可替换横屏导出视频第 0 帧，不增加片头、总时长或音频
  偏移；4:3 版本仅供下载和平台封面使用。当前视觉字号重新定义为 100%，
  仍可继续缩小或放大。
- 已完成回放会向游客显示完整剪辑编辑器供预览；游客不能提交生成任务，登录后
  才能真正导出视频。
- 每次视频导出都会作为独立版本上传到私有 OSS 并持久化，不会覆盖同一时间线之前生成的剪辑；单片时长与并发数仍可配置限制。
- 结果页可直接播放公开 HLS 回放；点击时间线、高光或字幕时间会跳转到对应位置。
- 播放器默认显示中文字幕，可切换英文或双语；弹幕以右侧现代化气泡同步出现，并支持整栏收起、密度和 ±3 秒高级校准。
- 网站界面支持右上角一键切换中文或英文，并在浏览器中记住选择。
- 新处理完成的直播会自动排队生成英文字幕；升级时现有已完成直播也会统一补入翻译队列，登录用户仍可手动重试。已完成片段会持久化并支持断点续传。
- 单 Worker 会从 SNH48 官方成员目录同步规范姓名、拼音、团体、队伍和状态；管理员可在 `/admin/glossary` 维护成员昵称和团内术语。
- 单 Worker 会把有效成员名和管理员词库编译成当前兼容 DashScope ASR 模型的 `vocabulary_id`，之后提交的新 ASR 任务自动引用该版本。

## 安全说明

- 应用严格限制输入主机和 Pocket48 返回的媒体主机，不能作为任意 URL 代理。
- FFmpeg 通过参数数组调用，不使用 shell。
- 应用不会在运行时自动下载或安装 FFmpeg、插件、Hook 或 MCP 集成；Chrome HLS 播放使用仓库内固定版本的 `hls.js`。
- 请仅使用经过审核的 Python 包和系统软件来源。
- 阿里云请使用仅能访问指定私有 Bucket/前缀的 RAM 用户，不要使用主账号 AccessKey。
- 字幕和弹幕会被当作不可信模型输入；提示词明确禁止执行其中的指令。
- AI 封面只把一张 MARK 截图通过短期私有 OSS 签名 URL 交给配置的
  Seedream 服务；生成结束后立即删除临时截图对象。Ark Key、签名 URL 和
  私有 OSS Key 不会返回浏览器或写入日志。
- 本项目使用非官方、可能变化的公开接口，仅适合个人处理可公开访问的回放。请自行遵守平台条款、版权和当地法规，不要重新分发未获授权的内容。

## 前置条件

- Python 3.12+
- 经过审核并由可信包管理器安装的 FFmpeg 和 ffprobe；FFmpeg 需包含 libass 的 `ass` 滤镜
- 可供 libass 使用的 CJK 字体，生产默认安装 `fonts-noto-cjk` 与
  `fonts-lxgw-wenkai`
- 阿里云私有 OSS Bucket
- 阿里云百炼 DashScope API Key
- 一个 OpenAI-compatible 模型 API
- 可选的火山方舟 Ark API Key 与 Seedream 模型/Endpoint ID；不配置时普通
  总结和剪辑保持可用，AI 封面入口会明确显示为未启用

本地安装不会替你创建云资源或自动安装系统软件。生产服务器的显式安装脚本见 [`deploy/README.md`](deploy/README.md)。

## 本地安装

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
cp .env.example .env
```

填写 `.env`：

```dotenv
ALIYUN_ACCESS_KEY_ID=...
ALIYUN_ACCESS_KEY_SECRET=...
ALIYUN_OSS_ENDPOINT=https://oss-cn-beijing.aliyuncs.com
ALIYUN_OSS_BUCKET=...
DASHSCOPE_API_KEY=...

LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=...
LLM_MODEL=...
LLM_RESPONSE_FORMAT=json_object

# 可选，仅管理员 AI 封面需要
ARK_API_KEY=...
ARK_SEEDREAM_MODEL=...
```

支持 JSON Schema 的模型可设置
`LLM_RESPONSE_FORMAT=json_schema`，应用会把 Pydantic 响应结构发送给模型并严格校验；
生产使用的 `qwen3.7-plus` 支持该模式。`LLM_MAX_OUTPUT_TOKENS` 控制结构化输出上限，
默认值为 `32768`；仅当模型明确返回长度截断时，应用会使用
`LLM_TRUNCATION_RETRY_MAX_TOKENS`（默认 `65536`）完整重试一次，
`LLM_SCHEMA_RETRY_ATTEMPTS` 控制校验失败后的带反馈重试次数。
`HLS_CONCURRENT_FRAGMENTS` 控制回放分片并行下载数，默认 16；若 Pocket48 CDN
出现限流或下载错误，可降低该值，应用仍会在并行下载失败时自动回退到 FFmpeg。

确认 FFmpeg、ffprobe 和字幕滤镜已可用：

```bash
ffmpeg -version
ffprobe -version
ffmpeg -hide_banner -filters | grep -E '(^|[[:space:]])ass([[:space:]]|$)'
```

启动：

```bash
pocket48-summarizer
```

打开 <http://127.0.0.1:8000>。

如需在本地测试邀请账号：

```bash
AUTH_REQUIRED=true pocket48-users create --username admin --admin
```

再把 `.env` 中 `AUTH_REQUIRED` 设为 `true`。密码通过终端安全输入；重置密码或停用用户会撤销现有会话。

## 官方成员目录与管理员词库

官方成员资料来自
[`https://h5.48.cn/resource/jsonp/allmembers_simple.php?gid=00`](https://h5.48.cn/resource/jsonp/allmembers_simple.php?gid=00)。
应用只允许访问这个固定 HTTPS 端点，限制响应大小并严格校验字段。同步成功后会保存目录版本；
同步失败会保留最后一次成功快照。官方接口中 `status=99` 作为当前状态成员，
其他状态（例如 `44`）仍会保留，但不会作为当前成员使用；从后续快照消失的成员只会标记为失活，不会删除。

管理员页面 `/admin/glossary` 支持：

- 查看只读的官方成员资料和最近同步状态；
- 手动刷新官方目录；
- 为成员添加昵称；
- 添加 CP 名、队伍简称、公演、歌曲、Unit 曲、活动和饭圈术语；
- 为自定义术语添加别名，并停用或重新启用词库记录。

别名按 Unicode 规范化后全局唯一，不能与已有规范成员名或术语冲突，避免同一个听写词指向多个对象。
官方同步不会删除管理员维护的别名。词库变化后，单 Worker 会按确定顺序生成最多 500 个热词：
当前成员规范名优先，其后是成员昵称、管理员术语及其别名、团体和队伍名。默认权重为 4，
含非 ASCII 字符的热词最长 15 个字符，符合 DashScope 预编译热词限制。

热词内容指纹未变化时复用现有 `vocabulary_id`。重建时先创建并确认新列表状态为 `OK`，
再切换数据库中的活动 ID；失败会保留上一版可用 ID。每个新提交的 ASR 任务会记录实际使用的
热词 ID 和指纹。此功能不会重跑或改写任何历史字幕与总结，只影响之后提交给 ASR 的任务。
如需临时关闭，可设置 `DASHSCOPE_VOCABULARY_ENABLED=false`。

## 公开部署

阿里云香港 ECS 的蓝绿 Web 槽位、独立 Worker、Caddy 原子切流量、任务租约过期恢复、自动回滚、备份、历史数据库迁移和首个管理员创建流程见 [`deploy/README.md`](deploy/README.md)。Worker 重启时会把租约已过期的卡死任务恢复到队列；迁移本机 SQLite 快照后，已有的 `completed` 直播会自动显示在公开首页。处理中、失败任务和处理日志仍只对提交者或管理员可见。

剪辑编辑器把 AI 时间线作为初始范围，允许在前后各 10 分钟的上下文中通过长时间轴调整，并在距离字幕边界 1 秒内吸附语句；服务端只分析该边界附近约 3 秒 HLS 来寻找静音点并缓存结果。鼠标悬停时间线只在浏览器本地更新前两句、当前句和后两句，单击会锁定红色时间标记，起止边界会优先吸附该标记且不再进行静音偏移。标记还可用于分割当前保留片段或为 AI 封面提供干净截图；被删除的区间会显示斜纹遮罩，允许随时恢复，成片时长按保留区间之和计算，播放预览和 FFmpeg 导出都会按原顺序自动跳过并拼接。桌面当前句使用无背景、无主题色的纯文字。横屏编辑时长时间轴位于预览下方，歌词和字幕样式左右并排，不再显示独立的开始/结束输入卡片。导出可关闭叠加，也可烧录中文、英文、双语字幕、右侧标准密度弹幕或其组合；弹幕以最多 5 条的自下而上气泡队列显示，新消息进入时旧消息上移，超过上限后最旧消息直接退出，不再按固定 5 秒消失。字幕字号、文字色、底色、横屏字体、画面方向和所选 AI 封面会随每个导出版本持久化。

长直播总结不限制时间线条目数量。字幕总结块除字符上限外还默认限制为最多 6 分钟，
每条最终时间线事件最多 5 分钟且必须与引用字幕的实际时间重叠。服务端继续按这些细分
字幕块检查整场覆盖；如果某个连续时段被模型遗漏，会从该分段候选中补入代表事件，避免
三小时直播的时间线只停留在前一个多小时，或退化成十几分钟到几十分钟的一条。

每个确认导出都有独立 ID、文件名和 OSS 对象，不会覆盖之前版本；同一次浏览器请求会通过请求 ID 幂等复用。视频从 Pocket48 HLS 拉取目标时间段并用 H.264 重新编码，上传成功后删除 ECS 临时 MP4 和 ASS 文件，下载时生成短期签名 URL。对几十秒到数分钟片段，2C4G 单并发通常足够；生产模板限制为单并发且每片最长 10 分钟。

主页显示口袋48原始回放元数据中的开播时间，并统一转换为中国标准时间（Asia/Shanghai），不会使用任务创建时间冒充直播时间。播放器直接访问 `idol-vod.48.cn`，不会把整段回放流量转发经过 ECS。

## 云端数据流

1. 应用从公开 Pocket48 HLS 回放提取 16 kHz 单声道 MP3。
2. 音频上传到私有 OSS 对象。
3. Worker 根据官方成员和管理员词库构建或复用 DashScope `vocabulary_id`。
4. 应用创建短期签名 GET URL，并连同活动热词 ID 提交给 DashScope。
5. 应用持久化 DashScope 任务 ID、热词 ID 和词库指纹，并轮询原任务，重启不会重复提交。
6. 结果保存到 SQLite 后，应用删除 OSS 对象和本地临时音频。
7. 字幕按时间边界分块后发送到配置的模型 API；已完成分块会持久化并可恢复。
8. 主任务完成后，独立翻译队列按原字幕序号生成英文字幕；翻译失败不会改变直播主任务的完成状态。
9. 用户确认剪辑范围和叠加选项后，应用生成临时 ASS（如需要）和 MP4，随后上传到独立的永久 OSS 前缀并删除本地文件。
10. 管理员生成 AI 封面时，应用提取 MARK 原始帧并短暂上传私有 OSS，
    Seedream 按管理员提交的提示词分别生成带标题的 16:9 和 4:3 横屏封面并
    持久化；修改封面文字或提示词都会创建一次新的付费生成，临时参考图在成功
    或失败后都会删除。

只应给临时音频前缀配置生命周期规则；不要让规则覆盖永久剪辑及 AI 封面前缀。

## 结果

- 网页结构化总结：摘要、时间线和高光
- 基于视频帧媒体时间同步的中文、英文或双语字幕
- 桌面端为可收起的右侧同步弹幕卡片，移动端为视频右侧高透明弹幕浮层
- 时间戳字幕和弹幕列表
- 弹幕活跃峰值
- 可调整、预览并保留多个版本的 MP4 剪辑导出
- 管理员生成并保留 16:9、4:3 两种横屏比例的 AI 封面 PNG
- SRT 下载
- DashScope 原始 JSON 下载
- Markdown 总结下载

## 测试

默认测试全部离线，不调用 Pocket48、OSS、DashScope 或模型 API：

```bash
python -m pytest -q
python -m compileall -q src
```

真实服务验证必须显式配置凭证，会产生网络流量和云端费用。不要在 CI 中默认运行。

只验证公开元数据、HLS 和弹幕，不上传音频、不调用付费模型：

```bash
P48_RUN_NETWORK_SMOKE=1 \
  .venv/bin/python scripts/metadata_smoke.py \
  'https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=1297967327104274432'
```

完整流水线会下载回放、上传音频并产生 ASR/LLM 费用，必须提供明确确认：

```bash
P48_RUN_PAID_SMOKE=I_UNDERSTAND_THIS_UPLOADS_AUDIO_AND_COSTS_MONEY \
  .venv/bin/python scripts/full_pipeline_smoke.py \
  'https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=...'
```

只验证当前 Ark Seedream 请求契约时，使用一张确认可外发、可通过 HTTPS
访问的非敏感测试图。脚本只从环境读取 Key，不接受也不会打印 Key：

```bash
P48_RUN_SEEDREAM_PROBE=I_UNDERSTAND_THIS_UPLOADS_AN_IMAGE_AND_COSTS_MONEY \
AI_COVER_PROBE_IMAGE_URL=https://example.com/non-sensitive-test.jpg \
AI_COVER_PROBE_ORIENTATION=portrait \
AI_COVER_PROBE_OUTPUT=/tmp/seedream-probe.png \
  .venv/bin/python scripts/seedream_cover_probe.py
```

## 参考与独立实现

公开行为调研参考：

- [duan602728596/48tools](https://github.com/duan602728596/48tools)（GPL-3.0）
- [30466/wrs-fansite](https://github.com/30466/wrs-fansite)（未提供许可证）
- [48tools/idol-grab-utils](https://github.com/48tools/idol-grab-utils)（LGPL-3.0）
- [阿里云非实时语音识别文档](https://help.aliyun.com/zh/model-studio/non-realtime-speech-recognition-user-guide)
- [Paraformer REST API](https://help.aliyun.com/zh/model-studio/paraformer-recorded-speech-recognition-restful-api)
- [提升语音识别准确率](https://help.aliyun.com/zh/model-studio/improve-asr-accuracy)

本项目没有复制上述项目代码，也没有包含逆向 `pa` 签名或 QChat 实现。
