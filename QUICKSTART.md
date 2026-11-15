# Pet Live Studio · 快速开始

一个在本机跑起来、用 OBS 当浏览器源的互动直播宠物：119 段动作、作息、
状态养成、弹幕指令、点歌，都能离线工作。

## 你需要

- **Python 3.10 或更新**（Windows 安装时勾选 *pip* 和 *Add python.exe to PATH*）
- 可选：一个 OpenAI 兼容的大模型服务（Ollama / llama.cpp / 云端 API），用来让宠物说话
- 可选：OpenAI 兼容的 TTS 服务；没有就用浏览器自带语音，或者干脆不发声

## 一键启动

| 系统 | 操作 |
|---|---|
| Windows | 双击 `run.bat` |
| macOS | 双击 `run.command`（首次若没反应，在终端跑 `chmod +x run.command`） |
| Linux / macOS 终端 | `./run.sh` |

首次运行会自动创建 `.venv`、安装依赖（约 1–3 分钟），然后打开控制台：

- 舞台（给 OBS 用）：http://127.0.0.1:8775/
- 控制台：http://127.0.0.1:8775/control/

端口被占用时：`python start.py --port 8780`。

## 接进 OBS

1. 添加「浏览器」源，地址填 `http://127.0.0.1:8775/`
2. 宽高设 1920×1080
3. **勾选「控制音频」**，否则听不到宠物说话和背景音乐

## 没有大模型能不能用？

能。动作、作息、喂食/陪玩/摸头、点歌、睡觉，全部在本地，不需要模型。
只有「说话」需要模型：在控制台「模型 API」里填地址即可。

## 常用设置

- 控制台里改的设置存在 `runtime/settings.json`
- 想用环境变量：复制 `.env.example` 为 `.env` 再改
- 想完全重置：删掉 `runtime/` 目录（设置、记忆、音乐、背景都会清空）

## 目录说明

```
webm/            119 段动作片段（VP9-alpha，10 秒/段）
web/actions/     动作索引 catalog.json（权重、别名、事件档位）
core/            弹幕、模型、TTS、宠物状态、点歌队列
runtime/         运行时数据，首次启动自动生成
```

## 素材授权

**公开分发或商用前请先读 `ASSETS-NOTICE.md`。**
