# Pocket48 Replay Summarizer

一个仅绑定本机的 Web 应用：粘贴公开的口袋48成员直播分享链接，自动提取回放音频、生成时间戳字幕、解析弹幕，并输出带字幕证据的中文结构化总结。

示例输入：

```text
https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=1297967327104274432
```

## 功能边界

- 仅处理无需登录即可访问的已结束公开回放。
- 不支持实时直播、私有内容、口袋48登录、`pa` 签名或网易云信 QChat。
- 不永久保存视频；通过 FFmpeg 从 HLS 直接提取临时音频。
- 字幕由阿里云百炼 DashScope `paraformer-v2` 生成。
- 总结通过可配置的 OpenAI-compatible `/chat/completions` API 生成。
- SQLite 保存任务、字幕、弹幕、ASR 原始 JSON 和总结。
- 成功识别后删除本地临时音频和私有 OSS 临时对象。
- 默认仅监听 `127.0.0.1`，不包含账号系统。

## 安全说明

- 应用严格限制输入主机和 Pocket48 返回的媒体主机，不能作为任意 URL 代理。
- FFmpeg 通过参数数组调用，不使用 shell。
- 应用不会自动下载或安装 FFmpeg、插件、Hook、MCP 集成或第三方脚本。
- 请仅使用经过审核的 Python 包和系统软件来源。
- 阿里云请使用仅能访问指定私有 Bucket/前缀的 RAM 用户，不要使用主账号 AccessKey。
- 字幕和弹幕会被当作不可信模型输入；提示词明确禁止执行其中的指令。
- 本项目使用非官方、可能变化的公开接口，仅适合个人处理可公开访问的回放。请自行遵守平台条款、版权和当地法规，不要重新分发未获授权的内容。

## 前置条件

- Python 3.12+
- 经过审核并由可信包管理器安装的 FFmpeg
- 阿里云私有 OSS Bucket
- 阿里云百炼 DashScope API Key
- 一个 OpenAI-compatible 模型 API

项目不会替你创建云资源，也不会自动安装系统软件。

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
```

确认 FFmpeg 已可用：

```bash
ffmpeg -version
```

启动：

```bash
pocket48-summarizer
```

打开 <http://127.0.0.1:8000>。

## 云端数据流

1. 应用从公开 Pocket48 HLS 回放提取 16 kHz 单声道 MP3。
2. 音频上传到私有 OSS 对象。
3. 应用创建短期签名 GET URL并提交给 DashScope。
4. 应用持久化 DashScope 任务 ID并轮询原任务，重启不会重复提交。
5. 结果保存到 SQLite 后，应用删除 OSS 对象和本地临时音频。
6. 字幕按时间边界分块后发送到配置的模型 API；已完成分块会持久化并可恢复。

建议同时给 OSS 前缀配置生命周期规则，作为应用清理失败时的兜底。

## 结果

- 网页结构化总结：摘要、时间线、主要话题、高光、待核实项
- 时间戳字幕和弹幕列表
- 弹幕活跃峰值
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

## 参考与独立实现

公开行为调研参考：

- [duan602728596/48tools](https://github.com/duan602728596/48tools)（GPL-3.0）
- [30466/wrs-fansite](https://github.com/30466/wrs-fansite)（未提供许可证）
- [48tools/idol-grab-utils](https://github.com/48tools/idol-grab-utils)（LGPL-3.0）
- [阿里云非实时语音识别文档](https://help.aliyun.com/zh/model-studio/non-realtime-speech-recognition-user-guide)
- [Paraformer REST API](https://help.aliyun.com/zh/model-studio/paraformer-recorded-speech-recognition-restful-api)

本项目没有复制上述项目代码，也没有包含逆向 `pa` 签名或 QChat 实现。
