# Pet Live Studio

一个可以在 **Windows / macOS / Linux** 上直接运行的互动直播宠物舞台：接收 B 站和抖音的弹幕、礼物、进场和关注，让宠物用表情动作和语音回应，并可添加背景图、背景音乐，作为 OBS 浏览器源使用。

当前版本重点：**好用**、**全平台**、**放大不糊**、**会聊天**。

- 一键启动：自动创建 Python 环境、安装依赖、打开控制台，不需要手动敲 pip。
- 高清动画：动画重新以 **1280×720** 编码，宠物放大后不再明显发虚。
- 位置好调：在控制台预览里直接拖动宠物、滚轮缩放，或用滑杆、快速对齐按钮微调；改动实时同步到 OBS。
- 背景音乐：本机曲库，支持多首上传、切歌、循环、音量，说话时自动压低音乐。
- 互动优先：观众弹幕、礼物、关注排在自言自语前；观众互动到达时立即中断正在生成的冷场内容。
- 可见的回复状态：控制台显示当前生成状态、排队数量、失败和因队列已满未接收的数量。
- 对话记忆：记住最近几轮互动和自言自语，支持多轮对话与连续小故事。
- 抖音 / B 站：B 站内置；抖音通过本机 DyHub 桥接接入。
- 全新布局：左侧导航 + 实时预览 + 分区设置，桌面和手机都能用。

## 快速开始

只需要先安装 **Python 3.10 或更新版本**（Windows 安装时勾选 “Add python.exe to PATH”）。

### Windows

双击 `run.bat`，或在项目目录运行：

```bat
run.bat
```

### macOS / Linux

```bash
chmod +x run.sh          # 第一次需要
./run.sh
```

macOS 也可以直接在 Finder 里双击 `run.command`；如果双击没有反应，先在终端执行 `chmod +x run.command`。

### 任意平台（手动）

```bash
python start.py          # Windows
python3 start.py         # macOS / Linux
```

首次运行会自动创建 `.venv` 并安装依赖（需要联网，通常 1–3 分钟）。完成后浏览器会自动打开控制台：

```
舞台（OBS 浏览器源）  http://127.0.0.1:8775/
控制台                http://127.0.0.1:8775/control/
```

常用参数：

```bash
python start.py --port 8780      # 端口被占用时换一个
python start.py --no-browser     # 不自动打开浏览器
python start.py --check          # 只准备环境，不启动
```

## 接入 OBS

1. 在 OBS 中添加 **浏览器源**，地址填舞台地址 `http://127.0.0.1:8775/`。
2. 宽高按直播场景设置（常见 1920×1080）。
3. 勾选 **控制音频**，背景音乐和语音才会进入直播。
4. 舞台背景默认透明，可以直接叠在游戏或摄像头画面上。

如果浏览器拦截了自动播放，舞台右下角会出现「点击开启声音」，点一下即可。

## 控制台说明

### 总览

- 实时舞台预览（`iframe`），与 OBS 使用同一个舞台。
- 可以直接在预览里 **拖动宠物** 调整位置，**滚轮缩放**。
- 模型、B 站、抖音、背景音乐状态，以及快捷操作。

### 舞台与宠物

- 水平 / 垂直位置滑杆、大小滑杆。
- 快速对齐：左上、居中、右上、左下、右下。
- 水平镜像、地面阴影、左上角状态、互动列表开关。
- 互动列表可放左侧或右侧。
- 背景图库（PNG / JPG / WebP，单张最大 8 MB）：上传的图都留在 `runtime/media/background/`，随时点缩略图切换，不用反复覆盖；切换时舞台交叉淡入，不会硬切。可调压暗程度，「恢复透明」只是不再使用当前图，图片仍留在库里。
- 状态面板（舞台左下角那四条）：可隐藏、可切到四个角、可调不透明度（20%–100%）和大小（0.6×–1.6×），默认左下、72%、1.0×。
- 所有布局都会保存到 `runtime/settings.json`，重启后保留。

### 背景音乐

- 支持 MP3、M4A、AAC、OGG、WAV、FLAC、WebM，单首最大 40 MB。
- 上传后自动加入曲库；点击曲目切换，支持上一首 / 下一首 / 循环 / 音量。
- **切歌交叉淡入**：舞台上跑两条音轨，换曲时旧曲淡出、新曲淡入，不会突然静音或硬切。
- 音乐由舞台页面播放；语音播报时背景音乐自动压低到「说话时压低到」设定值（默认 35%，可调到 0 = 完全让开，或 100% = 不压），说完再抬回来。
- **目录即曲库**：把音频直接拷进 `runtime/media/music/`（走 NAS 共享盘也行），在控制台点「扫描目录」即可收录；文件名写成「歌手 - 歌名」会自动拆成标题和歌手。
- **弹幕点歌**：观众发「点歌 歌名」，命中本地曲库就排进队列，没在放歌时直接开播；队列非空时自动关掉单曲循环，一首播完接着下一首，播完恢复循环。歌名没命中会回一句「没找到《…》」。
- 文件保存在 `runtime/media/music/`，曲库索引在同目录的 `index.json`。

### 语音播报

- 默认使用舞台浏览器的中文语音合成，可调整语音、语速、音调、音量；音色及效果受 OBS/浏览器与操作系统影响。
- 可切换到 **OpenAI 兼容 TTS API**：自定义 API 根地址、模型、音色和密钥，通过标准 `POST /audio/speech` 获取 MP3；不绑定 Azure，也支持其他兼容该接口的服务。Azure Speech 仍作为可选的旧配置保留。
- 使用通用固定音色：在控制台选择「OpenAI 兼容 TTS API」，填写服务 API 根地址（通常以 `/v1` 结尾）、模型和服务支持的音色 ID；按服务要求填写 API Key。舞台从本项目后端获取 MP3 播放，因此同一服务音色在 OBS、Chrome 和 Edge 中一致。密钥只保存在本机 `runtime/settings.json`，回复文本会发送到所配置的 TTS 服务。
- 固定语音的音频仍由舞台浏览器/OBS 播放，因此仍需在 OBS 勾选「控制音频」；如果自动播放被拦截，点击舞台的声音提示即可。TTS 请求失败会显示提示并恢复背景音乐，不会悄悄切换到系统音色。
- 合成超时按长句调宽：默认每句最多等 **120 秒**（可用环境变量 `SPEECH_TIMEOUT` 调整，范围 10–300）。本地 CosyVoice 一类模型合成一句 100 字要 25 秒以上，原来的 25 秒上限会把长句整条丢掉，表现为「弹幕回应了，但没有声音」。此外 5xx/429/连接中断会自动重试一次，音频容器按实际内容识别（mp3/wav/ogg 等），不再一律按 mp3 交给浏览器。
- 语音与文字不再互相等待：回复会**先显示气泡和动作**，合成好后接着开口；合成失败时气泡保留并给提示，不会出现「只有字、没有声、也没有解释」的情况。
- 支持试听；语音和音乐共用右下角的声音解锁提示。
- 浏览器音色模式下，控制台会读取 **OBS 舞台实际可用的中文音色**，而不是只按控制台浏览器的音色列表来选。保存的音色在 OBS 不存在时，会显示回退并自动改用实际音色。
- 控制台预览默认静音，声音以 OBS 舞台为准；需要边调边听时可以勾选「预览声音」（使用控制台浏览器音色，可能和 OBS 不同）。
- 冷场连续输出：默认 **60 秒无人互动** 后才进入闲聊，单句控制在 25 字以内；这个时间不是每句话的间隔。为减少固定音色 TTS 的等待，后台最多提前准备下一句；舞台只在当前语音实际开始播放后预合成下一句（不再局限于闲聊，弹幕回应的下一句也会提前合成），舞台侧至多保持一个预合成请求在途。播放完成确认会自动重试；舞台断线或确认丢失时，服务端有超时兜底，避免闲聊永久卡住。语音合成失败时显示错误提示，并保留这条回复的文字气泡。
- **有人说话，闲聊立刻让位**：弹幕、礼物、关注、进场永远走自己的回复队列，不会并进自言自语，也不会被念一句名字就带过去。只要有互动到达，当前这句自言自语播完就结束连播，舞台会把排队中的下一句闲聊丢掉，直接播这条互动回复。回复按「叫出昵称 → 正面接住这句话 → 用一句反问把话头交回去」生成，回应观众时不再读取自言自语的历史上下文，避免顺着自己的话题往下讲。
- 没人说话时就安静等待，不硬找话题。想更热闹可以把控制台的「无人互动 … 后进入闲聊」调小；想更安静就调大或直接关掉冷场。
- **没人就睡**：默认 20 分钟无人互动，宠物会播「睡觉-开始」再进入循环，弹幕/礼物一来先播「睡觉-起来」再回应。睡着期间舞台会通知服务端暂停自言自语，空房间不再空烧模型；控制台「无人 … 后睡觉」可调（0 = 不睡）。
- **弹幕指令**：整句发「喂食 / 陪玩 / 哄睡 / 摸头」会直接改变宠物状态，发「跳舞 / 转圈 / 生气 / 害羞 / 打招呼」会直接播对应动作。这些指令不进模型、不出声，立刻给反馈（长句不会被误判，比如「你会跳舞吗」仍走正常对话）。状态和动作覆盖见 `core/commands.py`。
- **养成状态**：饱食 / 心情 / 精力 / 亲密度四条，按时间自然衰减，喂食陪玩会改变数值，同一观众有心跳冷却。状态存在 `runtime/pet_state.json`，重启接着养；舞台左下角显示状态条与关系等级（陌生 → 熟悉 → 朋友 → 家人），数值变化时会飘一个 `+18 饱食` / `-1 精力` 的小字。
- **作息**：现实 60 分钟 = 宠物的一整天（控制台可调，0 = 关闭）。早上刷牙吃早餐、中午吃饭、傍晚跳舞看烟花、深夜打哈欠睡觉；到点会自己演对应动作，随机动作也有一半概率按当前时段挑。舞台状态条角落显示游戏内时间。
- 多舞台客户端同时打开时，服务端只选择一个可播放的舞台负责语音和闲聊完成确认；其他舞台仍显示互动，但不会重复播报。当前语音舞台断开后，会切换到另一个舞台客户端。
- 会自动检测冷场重复句：先换角度重试，仍重复就改用备用句，不会无限复读同一句话。

### 互动与连接

- **模型 API**：OpenAI 兼容接口，用于生成回复和动作标签。支持 HTTPS，HTTP 仅允许本机地址。
- **B 站直播间**：只读接收弹幕、礼物、进场、关注；可填 SESSDATA 接收登录可见消息。
- **抖音直播间**：通过本机 DyHub 读取弹幕、礼物、进场、关注；不需要把 Cookie 交给本项目。
- **互动优先级**：礼物和关注排在普通弹幕前；进场和弹幕共用回复队列。8 秒内弹幕/进场达到 5 条时，合并为主播模式的欢迎与感谢，不逐条刷屏；后续高频互动每 12 秒汇总一次。普通互动到达时会中断正在生成的自言自语。模型回复队列状态和未能接收的互动数显示在控制台。
- **对话记忆**：记住最近几轮互动与自言自语，用于多轮对话和连续剧情；可调上下文条数、随时清空。
- **互动事件模拟**：不接直播平台也能测试事件流和模型回复。
- 密钥只保存在本机 `runtime/settings.json`，页面不会回显。

#### B 站昵称与登录态

弹幕消息通常已经带有昵称，房间号本身不负责登录。如果遇到登录可见消息收不到、或昵称字段不完整，可在控制台填写自己的 B 站 `SESSDATA`：浏览器先登录 B 站，在开发者工具「应用程序 / Application」→「Cookie」→ `https://www.bilibili.com` 找到 `SESSDATA`，只复制它的值。它是账号登录凭证，不要分享；过期后重新登录并替换。无需填写整段 Cookie。

### 抖音直播间

抖音没有公开稳定、适合个人项目直接使用的弹幕接口；自己维护网页签名或抓取代码容易失效。**最省事的方式是继续用 DyHub**：它提供可视化控制台和默认的浏览器采集内核，Pet Live Studio 只连接本机转发的弹幕流，不需要在本项目里登录抖音或粘贴 Cookie。

Windows 上先安装并启动 Docker Desktop，然后在 PowerShell 执行：

```powershell
git clone https://github.com/ymstar/dyhub
cd dyhub
docker compose up -d --build
```

首次构建完成后打开 DyHub 控制台 `http://127.0.0.1:8757`，按其页面提示连接直播间；再回到 Pet Live Studio 控制台，填写：

- 抖音房间号：直播地址里的数字 `web_rid`，例如 `708764876300`
- DyHub 地址：默认 `http://127.0.0.1:8757`

点击「连接 / 测试」。DyHub 也可以在控制台里手动连接房间；本项目会自动尝试调用它的 `/api/rooms/connect`，失败时仍会连接已经加入的房间。

DyHub 默认用浏览器内核适配抖音页面变化，稳定性优先但会多占用一些内存。抖音更新页面协议时可能仍需更新 DyHub；这是比在本项目中自行维护私有抓取逻辑更简单的方案。

### 对话记忆与连续剧情

- 记忆保存在 `runtime/memory.json`，只在本机读取和写入，不会上传。
- 默认记住最近 6 条互动/自言自语；可以在控制台调到 2–12 条，或随时清空。
- 启动时会自动折叠重复的冷场自言自语（同一句最多保留 2 条），避免重复句把模型越带越偏。
- 同一观众的连续消息会优先带上该观众上一轮的上下文。
- 冷场连续输出会延续最近的话题或故事，而不是每次重新开始。
- 关闭「对话记忆」后，模型既不读取也不写入上下文。

### 动作测试

13 个预设动作可直接播放，不调用模型：待机、挥手欢迎、开心跳跃、害羞、傲娇生气、开心大笑、思考、忙碌、雀跃庆祝、无聊、吓一跳、拆礼物、打瞌睡。

## 配置

复制 `.env.example` 为 `.env` 可设置启动参数：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HOST` | `127.0.0.1` | 绑定地址；局域网/Docker 用 `0.0.0.0` |
| `PORT` | `8775` | HTTP / WebSocket 端口 |
| `PET_LIVE_ALLOWED_HOSTS` | `127.0.0.1,localhost` | 允许访问的主机名/IP，逗号分隔；局域网访问时必须加入 NAS 的 IP 或域名 |
| `BILI_ROOM_ID` | 空 | B 站直播间号，留空不连接 |
| `BILI_SESSDATA` | 空 | 可选的 B 站登录凭证值（只填 SESSDATA，不填整段 Cookie） |
| `LLM_BASE_URL` | `http://127.0.0.1:11434/v1` | OpenAI 兼容 API 根地址；HTTPS，或本机/局域网私有地址的 HTTP |
| `LLM_ALLOW_HTTP_HOSTS` | 空 | 额外允许 HTTP 的主机名（默认已放行私有 IP、`.local`、`host.docker.internal`） |
| `LLM_MODEL` | 空 | 模型 ID；留空自动发现 |
| `LLM_API_KEY` | 空 | 模型服务 API Key |
| `PET_PERSONA` | 通用宠物人设 | AI 回复风格 |
| `SPEECH_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容 TTS API 根地址；可在控制台改为其他服务 |
| `SPEECH_MODEL` | 空 | OpenAI 兼容 TTS 模型 ID |
| `SPEECH_VOICE` | 空 | TTS 服务支持的音色 ID |
| `SPEECH_API_KEY` | 空 | 可选的 TTS API Key；只在本机保存，不回传到控制台 |
| `SPEECH_TIMEOUT` | `120` | 单句合成的超时秒数（10–300）；本地模型慢时不要调小，否则长句会被丢掉 |
| `AZURE_SPEECH_REGION` | 空 | 可选的旧版 Azure Speech 资源区域 |
| `AZURE_SPEECH_KEY` | 空 | 可选的旧版 Azure Speech API Key |
| `DOUYIN_ROOM_ID` | 空 | 抖音 `web_rid`，留空不连接 |
| `DOUYIN_BRIDGE_URL` | `http://127.0.0.1:8757` | DyHub 桥接地址；本机或局域网私有地址 |
| `DOUYIN_ALLOW_HTTP_HOSTS` | 空 | 额外允许 HTTP 的 DyHub 主机名 |

界面里的设置保存在 `runtime/settings.json`（已被 Git 忽略），包括舞台位置、大小、背景、音乐、语音和连接信息。密钥以本地明文保存，请保护工作目录。

模型回复必须只输出一行固定格式，例如 `【你好呀，见到你很高兴！/开心跳跃/】`；舞台读取末尾的动作标签并播放对应动画。

## Docker / NAS 部署（OMV）

项目根目录自带 `Dockerfile`、`docker-compose.yml` 和 `.dockerignore`。推荐把整个项目放到 OMV 的 stack 目录：

```text
/dockerdata/appdata/pet-live-studio/
├── docker-compose.yml
├── Dockerfile
├── app.py
├── core/
├── adapters/
├── web/
├── vendor/
├── .env
└── runtime/          # 设置、记忆、音乐、背景，挂载进容器
```

### 1. 准备配置

```bash
cd /dockerdata/appdata/pet-live-studio
cp .env.docker.example .env
```

按需修改 `.env`：

```dotenv
PUID=1000
PGID=100
TZ=Asia/Shanghai
PET_LIVE_ALLOWED_HOSTS=192.168.2.22,omvnas,omvnas.local,127.0.0.1,localhost
LLM_BASE_URL=http://192.168.2.20:8080/v1
DOUYIN_BRIDGE_URL=http://192.168.2.22:8757
```

`PUID`/`PGID` 用 `id` 查看，通常 OMV 用户是 `1000:100`。`LLM_BASE_URL` 用 NAS 的局域网 IP，不要写 `127.0.0.1`——在容器里那是容器自己。

### 2. 启动

```bash
mkdir -p runtime
chown -R 1000:100 runtime
docker compose up -d --build
docker compose logs -f pet-live-studio
```

之后 OMV 的 Compose 插件可以直接管理 `/dockerdata/appdata/pet-live-studio` 这个 stack。

### 3. 访问

- 舞台 / OBS 浏览器源：`http://192.168.2.22:8775/`
- 控制台：`http://192.168.2.22:8775/control/`
- 健康检查：`http://192.168.2.22:8775/health`

如果通过 `omvnas.local` 等域名访问，把该域名加入 `PET_LIVE_ALLOWED_HOSTS`。

### 局域网 HTTP 模型

现在 `LLM_BASE_URL` 允许：

- `https://...`
- `http://127.0.0.1:...` / `http://localhost:...`
- `http://192.168.x.x:...`、`http://10.x.x.x:...`、`http://172.16.x.x:...` 等私有地址
- `.local` / `.lan` / `host.docker.internal` 等本机/局域网主机名

公网 HTTP 地址仍会被拒绝。额外的主机名可用 `LLM_ALLOW_HTTP_HOSTS` 放行。

> 部署到局域网后，控制台没有登录密码；请只放在可信局域网，或在 OMV/Nginx 前面加访问控制，不要把 `8775` 直接暴露到公网。

## 动作库

舞台的宠物有两套素材，优先用前者：

1. **`webm/`：119 段 VP9-alpha 动作，每段约 10 秒、640×360、合计约 64 MB**（移植自 dsh-pet-main）。
   舞台用两层 `<video>` 交叉切换，按分类权重随机自播（小动作 / 玩耍 / 吃什么 / 时节 / 文字）。
   素材变更后重新生成索引：

   ```bash
   python tools/build_action_catalog.py        # 扫描 webm/ → web/actions/catalog.json
   python tools/build_action_catalog.py --check # 只校验索引是否最新
   ```

   `catalog.json` 是唯一真相源：LLM 的 13 个中文标签（待机/害羞/开心…）在这里映射到具体片段，
   Web 端按它加权抽样，服务端按它做白名单校验。新增动作只要把 webm 丢进 `webm/`、
   在 `tools/build_action_catalog.py` 的分类里加一行、重新生成即可。

2. **`web/assets/animations/*.webp`：旧的 13 段 1280×720 动画**，现在只作为兜底——
   某段 webm 加载失败时退回同名动作，不至于黑屏。

> 640×360 在 1080p 舞台上是 2–3 倍放大，宠物显示高度建议不超过 700px；想更清晰只能离线超分。

## 高清动画（旧素材，兜底用）

`web/assets/animations/` 里的 webp 由下面的脚本生成（`anim/` 源帧已不在仓库中，仅作记录）：

```bash
python -m pip install pillow          # 仅重建动画时需要
python tools/build_animations.py       # 默认 1280×720, quality 78
```

可选参数：

```bash
python tools/build_animations.py --width 960 --quality 82 --sharpen 60
python tools/build_animations.py --only idle --force
```

调低 `--width` 可减小文件体积；调高 `--quality` / `--sharpen` 会更清晰但文件更大。重建后刷新舞台即可生效（页面会带版本号请求新文件）。

## 项目结构

```
app.py                     HTTP / WebSocket 服务与 API
start.py                   跨平台启动器（自动建 .venv、装依赖）
Dockerfile                 容器镜像
docker-compose.yml         OMV / NAS 部署
core/                      事件、动作、设置、音乐库、对话记忆、LLM
core/net.py                局域网/私有地址判断
core/memory.py             多轮对话与连续剧情记忆
adapters/bilibili.py       B 站只读接入
adapters/douyin.py         抖音 / DyHub 只读接入
web/index.html             舞台页面（OBS 使用）
web/control.html           控制台
web/assets/                CSS / JS / 动画
anim/                      原始动画帧（体积较大）
tools/build_animations.py  高清动画构建脚本
runtime/                   本地设置、背景图、音乐（运行时生成）
tests/                     单元与接口测试
```

## 开发与测试

测试需要把项目根目录放进 `PYTHONPATH`（`tests/` 不是包，`unittest discover` 直接跑会 `ModuleNotFoundError`）。

```powershell
# Windows PowerShell
$env:PYTHONPATH = (Get-Location)
python tests\test_app.py
python tests\test_events.py
python tests\test_bilibili.py
node tests\stage_speech_smoke.js
```

```bash
# macOS / Linux
PYTHONPATH=. python tests/test_app.py
PYTHONPATH=. python tests/test_events.py
PYTHONPATH=. python tests/test_bilibili.py
node tests/stage_speech_smoke.js
```

`stage_speech_smoke.js` 在 Node 里用最小浏览器桩运行 `web/assets/stage.js`，覆盖「先上屏再发声、合成失败重试并保留气泡、队列不卡死」，不需要联网或真实 TTS。

测试使用项目内 `runtime/test-tmp/` 作为临时目录，不会写入系统临时目录。

## 安全说明

- 服务默认只监听回环地址；设为 `0.0.0.0` 时务必同时配置 `PET_LIVE_ALLOWED_HOSTS`，并只在可信局域网使用。
- 控制台没有内置登录；不要把 `8775` 直接暴露到公网，建议放在 OMV 防火墙/Nginx 访问控制之后。
- B 站适配器为只读连接，不发送弹幕、礼物或执行账号操作。
- 抖音通过本机 DyHub 桥接，Pet Live Studio 不保存抖音 Cookie；DyHub 自身的数据和登录态由你自行管理。
- 观众消息作为不可信内容传给模型，不会被当作系统指令；对话历史同样只作为上下文。
- `/api/settings` 不回传 API Key 和 SESSDATA 明文。

## 许可

本项目新增代码以 MIT 许可发布，见 `LICENSE`。B 站协议依赖为 MIT，见 `vendor/NOTICE.txt` 与 `vendor/BLIVEDM-LICENSE`。抖音桥接使用外部 [DyHub](https://github.com/ymstar/dyhub)（MIT），本项目不打包其代码。角色素材不属于本项目 MIT 代码许可，分发前请确认来源许可。
