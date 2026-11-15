(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const controlId = `control-${Math.random().toString(36).slice(2)}`;
  const FALLBACK_ACTIONS = [
    ["idle", "待机"], ["welcome_wave", "挥手欢迎"], ["happy_jump", "开心跳跃"],
    ["shy", "害羞"], ["grumpy", "傲娇生气"], ["laugh", "开心大笑"],
    ["thinking", "思考"], ["busy", "忙碌"], ["celebrate", "雀跃庆祝"],
    ["bored", "无聊"], ["surprised", "吓一跳"], ["gift", "拆礼物"], ["sleepy", "打瞌睡"],
  ];
  const ANCHORS = {
    "left-top": [26, 34],
    center: [50, 52],
    "right-top": [74, 34],
    "left-bottom": [26, 68],
    "right-bottom": [74, 68],
  };

  const state = {
    settings: {},
    backgrounds: [],
    music: { tracks: [], active: "", enabled: false, volume: 0.6, duck: 0.35, loop: true, title: "", queue: [] },
    bilibili: {},
    douyin: {},
    replyQueue: null,
    stageVoices: { stage: null, preview: null },
    memory: { enabled: true, turns: 6, exchanges: 0 },
    actions: [],
    socket: null,
    trackSignature: "",
  };

  let toastTimer;
  let pendingPatch = {};
  let patchTimer = null;
  let patchInFlight = false;

  /* ------------------------------------------------------------------ utils */

  const formatPercent = (value) => `${Math.round(Number(value) || 0)}%`;
  const formatScale = (value) => `${(Number(value) || 1).toFixed(2)}×`;
  const formatVolume = (value) => `${Math.round((Number(value) || 0) * 100)}%`;
  const formatBytes = (bytes) => {
    const size = Number(bytes) || 0;
    if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
    return `${(size / 1024 / 1024).toFixed(1)} MB`;
  };

  function setOutput(selector, text) {
    const element = $(selector);
    if (element) element.textContent = text;
  }

  function setMessage(selector, message, isError = false) {
    const element = $(selector);
    if (!element) return;
    element.textContent = message;
    element.classList.toggle("error", isError);
    element.classList.toggle("success", !isError && Boolean(message));
  }

  function notify(message, isError = false) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("visible");
    toast.style.borderColor = isError ? "rgba(240,127,142,.5)" : "rgba(95,214,160,.4)";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("visible"), 3400);
  }

  async function request(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || `请求失败：${response.status}`);
    }
    if (response.status === 204) return null;
    const type = response.headers.get("content-type") || "";
    if (!type.includes("application/json")) return response;
    return response.json();
  }

  function postJSON(url, data) {
    return request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  }

  /* -------------------------------------------------------------- save queue */

  function queuePatch(patch, delay = 140) {
    Object.assign(pendingPatch, patch);
    clearTimeout(patchTimer);
    patchTimer = setTimeout(flushPatch, delay);
  }

  async function flushPatch() {
    if (patchInFlight || Object.keys(pendingPatch).length === 0) return;
    const payload = pendingPatch;
    pendingPatch = {};
    patchInFlight = true;
    try {
      const result = await postJSON("/api/settings", payload);
      applySettingsPayload(result);
    } catch (error) {
      notify(`保存失败：${error.message}`, true);
    } finally {
      patchInFlight = false;
      if (Object.keys(pendingPatch).length) {
        clearTimeout(patchTimer);
        patchTimer = setTimeout(flushPatch, 60);
      }
    }
  }

  /* ----------------------------------------------------------------- status */

  function applySettingsPayload(result) {
    if (!result) return;
    if (result.settings) state.settings = { ...state.settings, ...result.settings };
    if (result.music) state.music = { ...state.music, ...result.music };
    if (result.bilibili) state.bilibili = result.bilibili;
    if (result.douyin) state.douyin = result.douyin;
    if (Object.hasOwn(result, "reply_queue")) updateReplyQueue(result.reply_queue);
    if (result.memory) state.memory = { ...state.memory, ...result.memory };
    if (typeof result.clients === "number") $("#client-count").textContent = String(result.clients);
    updateSummary();
    updateBiliStatus(state.bilibili);
    updateDouyinStatus(state.douyin);
    updateMemory();
    updateVoiceStatus();
    if (result.settings && Object.hasOwn(result.settings, "background")) {
      state.backgrounds = state.backgrounds.map((item) => (
        { ...item, active: item.name === result.settings.background }
      ));
      renderBackgrounds();
    }
    renderMusic();
  }

  function updateSummary() {
    const settings = state.settings;
    if (!settings) return;
    $("#llm-summary").textContent = settings.llm_base_url ? "已配置" : "未配置";
    $("#llm-detail").textContent = settings.llm_model || "自动选择模型";
  }

  function updateReplyQueue(replyQueue) {
    state.replyQueue = replyQueue || null;
    if (!replyQueue) {
      setOutput("#reply-queue-status", "AI 回复不可用：请先配置并启动模型 API。");
      return;
    }
    const activity = replyQueue.generating
      ? `正在处理${replyQueue.current_kind === "monologue" ? "自言自语" : "观众互动"}`
      : "当前空闲";
    const folded = replyQueue.pending ? ` · 待并入下一句 ${replyQueue.pending} 条` : "";
    setOutput(
      "#reply-queue-status",
      `${activity} · 排队 ${replyQueue.queued || 0} 条 · 已回复 ${replyQueue.completed || 0} 条 · 未接收 ${replyQueue.dropped || 0} 条 · 失败 ${replyQueue.failed || 0} 条${folded}`,
    );
  }

  function updateReplyStatus(packet) {
    const kind = packet.kind === "gift" ? "礼物"
      : (packet.kind === "follow" ? "关注" : "弹幕");
    const messages = {
      processing: `正在生成这条${kind}的回复…`,
      replied: `已生成并发送${kind}回复。`,
      folded: `闲聊正在连着说，这条${kind}已记下，会在下一句里一起回应。`,
      failed: `${kind}已收到，但模型生成失败；请检查模型服务状态。`,
      dropped: `回复队列已满，这条${kind}没有进入 AI 回复队列。`,
      unavailable: `已收到${kind}，但 AI 模型尚未配置或启动。`,
    };
    const message = messages[packet.status];
    if (!message) return;
    setMessage("#reply-last-status", message, ["failed", "dropped", "unavailable"].includes(packet.status));
    if (packet.status === "dropped") notify(message, true);
  }

  function updateBiliStatus(bilibili) {
    if (!bilibili) return;
    const room = bilibili.room_id || state.settings.bili_room_id || "";
    $("#room-summary").textContent = room || "未连接";
    $("#room-detail").textContent = bilibili.message || "—";
    $("#bili-status").textContent = bilibili.state === "connected"
      ? "连接正常"
      : (bilibili.message || "尚未连接");
    $("#bili-status-detail").textContent = room
      ? `房间 ${room} · 只读接收`
      : "设置房间号后开始只读连接";
    $("#bili-dot").className = bilibili.state || "";
  }

  function updateDouyinStatus(douyin) {
    if (!douyin) return;
    const room = douyin.room_id || state.settings.douyin_room_id || "";
    $("#douyin-status").textContent = douyin.state === "connected"
      ? "连接正常"
      : (douyin.message || "尚未连接");
    $("#douyin-status-detail").textContent = room
      ? `房间 ${room} · 本机 DyHub 桥接`
      : "先启动 DyHub，再填房间号";
    $("#douyin-dot").className = douyin.state || "";
    let base = douyin.bridge_url || state.settings.douyin_bridge_url || "http://127.0.0.1:8757";
    base = base.replace(/^ws/, "http").replace(/\/+$/, "").replace(/\/ws$/, "");
    $("#open-dyhub").href = base;
  }

  function updateMemory() {
    const memory = state.memory || {};
    const enabled = memory.enabled ?? state.settings.memory_enabled ?? true;
    const turns = memory.turns ?? state.settings.memory_turns ?? 6;
    $("#memory-enabled").checked = Boolean(enabled);
    $("#memory-turns").value = turns;
    setOutput("#memory-turns-value", String(turns));
    $("#memory-count").textContent = String(memory.exchanges ?? 0);
  }

  /* ------------------------------------------------------------------ music */

  function activeTrack() {
    return state.music.tracks.find((track) => track.id === state.music.active) || null;
  }

  function renderMusic() {
    const music = state.music;
    const track = activeTrack();
    setOutput("#track-count", `${music.tracks.length} 首`);
    $("#music-summary").textContent = track ? track.title : "未选择";
    const queued = (music.queue || []).length;
    const queueHint = queued ? ` · 点歌队列 ${queued} 首` : "";
    $("#music-detail").textContent = (track
      ? `${music.tracks.length} 首 · ${music.enabled ? "播放中" : "已暂停"}`
      : (music.tracks.length ? `${music.tracks.length} 首 · 未播放` : "还没有曲目")) + queueHint;
    $("#music-now").textContent = track
      ? `${track.title} · ${music.enabled ? "播放中" : "已暂停"}`
      : "未选择曲目";
    $("#player-title").textContent = track ? track.title : "背景音乐";
    $("#music-toggle").textContent = music.enabled ? "❚❚" : "▶";
    $("#music-enabled").checked = Boolean(music.enabled);
    $("#music-loop").checked = Boolean(music.loop);
    if (document.activeElement !== $("#music-volume")) {
      $("#music-volume").value = music.volume;
    }
    setOutput("#music-volume-value", formatVolume(music.volume));
    if (document.activeElement !== $("#music-duck")) {
      $("#music-duck").value = music.duck;
    }
    setOutput("#music-duck-value", formatVolume(music.duck));
    renderTracks();
  }

  function renderTracks() {
    const music = state.music;
    const signature = `${music.tracks.map((track) => `${track.id}:${track.title}`).join("|")}|${music.active}`;
    if (signature === state.trackSignature) return;
    state.trackSignature = signature;
    const list = $("#track-list");
    list.replaceChildren();
    if (!music.tracks.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "还没有音乐，先上传一首吧。";
      list.append(empty);
      return;
    }
    music.tracks.forEach((track, index) => {
      const row = document.createElement("div");
      row.className = `track${track.id === music.active ? " active" : ""}`;
      row.tabIndex = 0;
      row.title = "点击切换到此曲目";
      const number = document.createElement("span");
      number.className = "index";
      number.textContent = String(index + 1).padStart(2, "0");
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = track.title;
      const size = document.createElement("span");
      size.className = "size";
      size.textContent = formatBytes(track.size);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "remove";
      remove.title = "删除这首音乐";
      remove.textContent = "✕";
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        removeTrack(track);
      });
      row.append(number, name, size, remove);
      row.addEventListener("click", () => selectTrack(track));
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectTrack(track);
        }
      });
      list.append(row);
    });
  }

  function selectTrack(track) {
    if (track.id === state.music.active) {
      toggleMusic();
      return;
    }
    state.music.active = track.id;
    state.music.enabled = true;
    state.settings.music_track = track.id;
    renderMusic();
    queuePatch({ music_track: track.id, music_enabled: true }, 0);
    notify(`已切换到「${track.title}」`);
  }

  function toggleMusic() {
    const enabled = !state.music.enabled;
    const patch = { music_enabled: enabled };
    if (enabled && !state.music.active && state.music.tracks.length) {
      patch.music_track = state.music.tracks[0].id;
      state.music.active = patch.music_track;
    }
    state.music.enabled = enabled;
    renderMusic();
    queuePatch(patch, 0);
  }

  function stepTrack(direction) {
    const tracks = state.music.tracks;
    if (!tracks.length) {
      notify("曲库里还没有音乐", true);
      return;
    }
    let index = tracks.findIndex((track) => track.id === state.music.active);
    if (index < 0) index = direction > 0 ? -1 : 0;
    const next = tracks[(index + direction + tracks.length) % tracks.length];
    selectTrack(next);
  }

  async function removeTrack(track) {
    if (!window.confirm(`确定从曲库删除「${track.title}」吗？`)) return;
    try {
      const result = await request(`/api/music/${encodeURIComponent(track.id)}`, { method: "DELETE" });
      state.music = { ...state.music, ...result };
      if (result.tracks && !result.tracks.some((item) => item.id === state.music.active)) {
        state.music.active = result.active || "";
      }
      state.trackSignature = "";
      renderMusic();
      notify("曲目已删除");
    } catch (error) {
      notify(error.message, true);
    }
  }

  function applyMusicPayload(payload) {
    if (!payload) return;
    state.music = { ...state.music, ...payload };
    state.settings.music_track = payload.active || "";
    state.trackSignature = "";
    renderMusic();
  }

  /* ----------------------------------------------------------------- layout */

  function syncLayoutControls(values, skipFocused = false) {
    const fields = [
      ["pet_x", "#pet-x", "#pet-x-value", formatPercent],
      ["pet_y", "#pet-y", "#pet-y-value", formatPercent],
      ["pet_scale", "#pet-scale", "#pet-scale-value", formatScale],
    ];
    for (const [key, inputSelector, outputSelector, format] of fields) {
      if (!(key in values)) continue;
      const value = Number(values[key]);
      if (!Number.isFinite(value)) continue;
      const input = $(inputSelector);
      if (!skipFocused || document.activeElement !== input) input.value = value;
      setOutput(outputSelector, format(value));
    }
    markAnchor();
  }

  function markAnchor() {
    const x = Number(state.settings.pet_x);
    const y = Number(state.settings.pet_y);
    for (const button of $$("#anchor-buttons button")) {
      const anchor = ANCHORS[button.dataset.anchor];
      const active = anchor && Math.abs(anchor[0] - x) < 1.5 && Math.abs(anchor[1] - y) < 1.5;
      button.classList.toggle("active", Boolean(active));
    }
  }

  function sendLayout(values) {
    const socket = state.socket;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "stage_layout", origin: controlId, ...values }));
      return true;
    }
    queuePatch(values);
    return false;
  }

  function updateLayoutField(key, value) {
    state.settings[key] = value;
    const formats = {
      pet_x: ["#pet-x-value", formatPercent],
      pet_y: ["#pet-y-value", formatPercent],
      pet_scale: ["#pet-scale-value", formatScale],
    };
    if (formats[key]) setOutput(formats[key][0], formats[key][1](value));
    markAnchor();
    sendLayout({ [key]: value });
  }

  /* ------------------------------------------------------------------ forms */

  function applyVoiceLabels() {
    setOutput("#rate-value", (Number($("#speech-rate").value) || 1).toFixed(1));
    setOutput("#pitch-value", (Number($("#speech-pitch").value) || 1).toFixed(1));
    setOutput("#volume-value", formatVolume($("#speech-volume").value));
  }

  function voiceSource() {
    const stage = state.stageVoices && state.stageVoices.stage;
    if (stage && Array.isArray(stage.voices) && stage.voices.length) {
      return { report: stage, label: "OBS 舞台" };
    }
    const preview = state.stageVoices && state.stageVoices.preview;
    if (preview && Array.isArray(preview.voices) && preview.voices.length) {
      return { report: preview, label: "预览浏览器" };
    }
    return { report: null, label: "控制台系统" };
  }

  function maybeAdoptStageVoice() {
    if (state.settings.speech_provider !== "browser") return;
    const report = state.stageVoices && state.stageVoices.stage;
    if (!report || !report.missing || !report.active || !report.wanted) return;
    if (report.active === report.wanted) return;
    if ((state.settings.speech_voice || "") !== report.wanted) return;
    // Keep preview and OBS on the same actual voice: if the saved voice does
    // not exist in the output browser, adopt the fallback once.
    state.settings.speech_voice = report.active;
    $("#speech-voice").dataset.selected = report.active;
    queuePatch({ speech_voice: report.active }, 0);
    notify(`OBS 没有「${report.wanted}」，已改用「${report.active}」`);
  }

  function populateVoices() {
    const select = $("#speech-voice");
    const source = voiceSource();
    const chosen = select.dataset.selected
      || (source.report && source.report.wanted)
      || select.value;
    select.replaceChildren(new Option(
      source.report ? `${source.label}默认中文音色` : "系统默认中文语音",
      "",
    ));
    let voices = [];
    if (source.report) {
      voices = source.report.voices;
    } else if ("speechSynthesis" in window) {
      voices = window.speechSynthesis.getVoices()
        .filter((voice) => voice.lang && voice.lang.toLowerCase().startsWith("zh"))
        .map((voice) => ({ name: voice.name, lang: voice.lang, local: voice.localService }));
    }
    for (const voice of voices) {
      const suffix = voice.local === false ? " · 在线" : "";
      select.add(new Option(`${voice.name} · ${voice.lang}${suffix}`, voice.name));
    }
    if (chosen && !voices.some((voice) => voice.name === chosen)) {
      select.add(new Option(`已保存（当前舞台不支持）：${chosen}`, chosen));
    }
    select.value = chosen || "";
    updateVoiceStatus();
  }

  function updateVoiceStatus() {
    const element = $("#voice-actual");
    if (!element) return;
    const provider = $("#speech-provider").value;
    if (provider === "openai") {
      const voice = $("#speech-openai-voice").value.trim();
      const model = $("#speech-model").value.trim();
      const configured = Boolean(model && voice);
      element.textContent = `OpenAI 兼容音色：${voice || "未配置"}`
        + `${model ? ` · ${model}` : ""}`
        + `${state.settings.speech_api_key_set ? " · API Key 已保存" : ""}`;
      element.classList.toggle("error", !configured);
      return;
    }
    if (provider === "azure") {
      const voice = $("#azure-speech-voice").selectedOptions[0]?.textContent || "Azure Speech";
      const configured = Boolean(state.settings.speech_key_set);
      element.textContent = `固定音色：${voice}${configured ? " · 密钥已保存" : " · 尚未配置密钥"}`;
      element.classList.toggle("error", !configured);
      return;
    }
    const stage = state.stageVoices.stage;
    const preview = state.stageVoices.preview;
    const useStage = stage && Array.isArray(stage.voices) && stage.voices.length;
    const report = useStage
      ? stage
      : (preview && Array.isArray(preview.voices) && preview.voices.length ? preview : null);
    if (!report) {
      element.textContent = "舞台实际使用：等待舞台连接（当前用控制台系统音色）";
      element.classList.remove("error");
      return;
    }
    const label = useStage ? "OBS 舞台" : "预览浏览器";
    if (!report.voices.length) {
      element.textContent = `${label}没有检测到中文音色`;
      element.classList.add("error");
      return;
    }
    const wanted = report.wanted || state.settings.speech_voice || "";
    if (report.missing && wanted) {
      element.textContent = `${label}没有「${wanted}」，实际回退到「${report.active || "默认中文音色"}」`;
      element.classList.add("error");
    } else {
      element.textContent = `${label}实际使用：${report.active || "默认中文音色"}`;
      element.classList.remove("error");
    }
  }

  function voicePayload() {
    const provider = $("#speech-provider").value;
    return {
      speech_enabled: $("#speech-enabled").checked,
      speech_provider: provider,
      speech_voice: provider === "azure"
        ? $("#azure-speech-voice").value
        : $("#speech-voice").value,
      speech_openai_voice: $("#speech-openai-voice").value.trim(),
      speech_base_url: $("#speech-base-url").value.trim(),
      speech_model: $("#speech-model").value.trim(),
      speech_region: $("#speech-region").value.trim(),
      speech_rate: Number($("#speech-rate").value),
      speech_pitch: Number($("#speech-pitch").value),
      speech_volume: Number($("#speech-volume").value),
    };
  }

  function voiceFormPayload() {
    const payload = voicePayload();
    const key = $("#speech-key").value;
    const clearKey = $("#clear-speech-key").checked;
    if (payload.speech_provider === "azure") {
      payload.speech_key = key;
      payload.clear_speech_key = clearKey;
    } else if (payload.speech_provider === "openai") {
      payload.speech_api_key = key;
      payload.clear_speech_api_key = clearKey;
    }
    return payload;
  }

  function updateSpeechProviderControls() {
    const provider = $("#speech-provider").value;
    const azure = provider === "azure";
    const openai = provider === "openai";
    $("#browser-speech-settings").hidden = provider !== "browser";
    $("#openai-speech-settings").hidden = !openai;
    $("#azure-speech-settings").hidden = !azure;
    $("#speech-api-key-settings").hidden = provider === "browser";
    $("#speech-pitch-setting").hidden = openai;
    $("#speech-key-label").textContent = azure ? "Azure Speech Key" : "API Key（可选）";
    const keyConfigured = azure
      ? state.settings.speech_key_set
      : (openai && state.settings.speech_api_key_set);
    $("#speech-key").value = "";
    $("#speech-key").placeholder = keyConfigured
      ? "已保存密钥；留空保持不变"
      : "输入 API Key（按服务要求选填）";
    $("#clear-speech-key").checked = false;
    updateVoiceStatus();
  }

  function queueVoiceSettings(delay = 250) {
    const patch = voicePayload();
    state.settings = { ...state.settings, ...patch };
    queuePatch(patch, delay);
  }

  function fillSettings(settings, music) {
    $("#api-form").elements.llm_base_url.value = settings.llm_base_url || "";
    $("#api-form").elements.llm_model.value = settings.llm_model || "";
    $("#api-form").elements.persona.value = settings.persona || "";
    $("#api-form").elements.api_key.value = "";
    $("#api-form").elements.api_key.placeholder = settings.api_key_set
      ? "已保存密钥；留空保持不变"
      : "输入 API Key（可选）";
    $("#api-form").elements.clear_api_key.checked = false;

    const biliForm = $("#bili-form");
    biliForm.elements.bili_room_id.value = settings.bili_room_id || "";
    biliForm.elements.sessdata.value = "";
    biliForm.elements.sessdata.placeholder = settings.sessdata_set
      ? "已保存登录态；留空保持不变"
      : "可选，仅用于需要登录的房间";
    biliForm.elements.clear_sessdata.checked = false;

    $("#speech-enabled").checked = Boolean(settings.speech_enabled);
    $("#speech-provider").value = settings.speech_provider || "browser";
    $("#speech-rate").value = settings.speech_rate;
    $("#speech-pitch").value = settings.speech_pitch;
    $("#speech-volume").value = settings.speech_volume;
    $("#speech-base-url").value = settings.speech_base_url || "https://api.openai.com/v1";
    $("#speech-model").value = settings.speech_model || "";
    $("#speech-openai-voice").value = settings.speech_openai_voice || "";
    $("#speech-region").value = settings.speech_region || "";
    $("#speech-key").value = "";
    const apiKeySet = settings.speech_provider === "azure"
      ? settings.speech_key_set
      : settings.speech_api_key_set;
    $("#speech-key").placeholder = apiKeySet
      ? "已保存密钥；留空保持不变"
      : "输入 API Key（按服务要求选填）";
    $("#clear-speech-key").checked = false;
    if (settings.speech_provider === "azure") {
      $("#azure-speech-voice").value = settings.speech_voice || "zh-CN-XiaoxiaoNeural";
      $("#speech-voice").dataset.selected = "";
    } else {
      $("#speech-voice").dataset.selected = settings.speech_voice || "";
    }
    applyVoiceLabels();
    updateSpeechProviderControls();
    populateVoices();
    maybeAdoptStageVoice();

    $("#idle-chatter-enabled").checked = Boolean(settings.idle_chatter_enabled);
    $("#idle-chatter-interval").value = settings.idle_chatter_interval;
    setOutput("#idle-chatter-value", `${settings.idle_chatter_interval} 秒`);

    const douyinForm = $("#douyin-form");
    douyinForm.elements.douyin_room_id.value = settings.douyin_room_id || "";
    douyinForm.elements.douyin_bridge_url.value = settings.douyin_bridge_url || "http://127.0.0.1:8757";
    $("#memory-enabled").checked = Boolean(settings.memory_enabled);
    $("#memory-turns").value = settings.memory_turns;
    setOutput("#memory-turns-value", String(settings.memory_turns));

    syncLayoutControls({
      pet_x: settings.pet_x,
      pet_y: settings.pet_y,
      pet_scale: settings.pet_scale,
    });
    $("#pet-flip").checked = Boolean(settings.pet_flip);
    $("#pet-shadow").checked = Boolean(settings.pet_shadow);
    $("#show-brand").checked = Boolean(settings.show_brand);
    $("#feed-enabled").checked = Boolean(settings.feed_enabled);
    $("#feed-position").value = settings.feed_position || "left";

    $("#background-dim").value = settings.background_dim;
    setOutput("#dim-value", formatPercent(settings.background_dim * 100));
    $("#sleep-after").value = settings.sleep_after;
    setOutput("#sleep-after-value", settings.sleep_after ? `${settings.sleep_after} 分钟` : "不睡");
    $("#day-cycle").value = settings.day_cycle_minutes;
    setOutput("#day-cycle-value", settings.day_cycle_minutes ? `${settings.day_cycle_minutes} 分钟` : "关闭");
    $("#hud-enabled").checked = Boolean(settings.hud_enabled);
    $("#hud-position").value = settings.hud_position || "left-bottom";
    $("#hud-opacity").value = settings.hud_opacity;
    setOutput("#hud-opacity-value", formatPercent(settings.hud_opacity * 100));
    $("#hud-scale").value = settings.hud_scale;
    setOutput("#hud-scale-value", formatScale(settings.hud_scale));
    renderBackgrounds();
    setOutput("#background-count", `${state.backgrounds.length} 张`);

    $("#music-enabled").checked = Boolean(settings.music_enabled);
    $("#music-volume").value = settings.music_volume;
    $("#music-duck").value = settings.music_duck;
    $("#music-loop").checked = Boolean(settings.music_loop);
    setOutput("#music-volume-value", formatVolume(settings.music_volume));
    setOutput("#music-duck-value", formatVolume(settings.music_duck));
    if (music) state.music = { ...state.music, ...music };
  }

  function renderBackgrounds() {
    const grid = $("#background-grid");
    const items = state.backgrounds;
    setOutput("#background-count", `${items.length} 张`);
    grid.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "还没有背景图，先上传一张吧。";
      grid.append(empty);
      return;
    }
    for (const item of items) {
      // 外层用 div：button 里不能再套删除按钮，否则浏览器会把结构拆坏。
      const card = document.createElement("div");
      card.className = `background-item${item.active ? " active" : ""}`;
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.title = item.active ? "当前背景" : "点击切换到这张背景";
      card.style.backgroundImage = `url("${item.url}")`;
      const label = document.createElement("span");
      label.className = "label";
      label.textContent = formatBytes(item.size);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "remove";
      remove.title = "从背景库删除";
      remove.textContent = "✕";
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        removeBackground(item);
      });
      card.append(label, remove);
      card.addEventListener("click", () => selectBackground(item));
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectBackground(item);
        }
      });
      grid.append(card);
    }
  }

  function applyBackgroundPayload(payload) {
    if (!payload) return;
    state.backgrounds = payload.items || [];
    state.settings.background = payload.active || "";
    renderBackgrounds();
  }

  function selectBackground(item) {
    if (item.active) return;
    state.settings.background = item.name;
    state.backgrounds = state.backgrounds.map((entry) => ({ ...entry, active: entry.name === item.name }));
    renderBackgrounds();
    queuePatch({ background: item.name }, 0);
    notify("背景已切换，舞台会淡入");
  }

  async function removeBackground(item) {
    if (!window.confirm("确定从背景库里删掉这张图吗？")) return;
    try {
      const result = await request(`/api/backgrounds/${encodeURIComponent(item.name)}`, { method: "DELETE" });
      applyBackgroundPayload(result);
      notify("背景已删除");
    } catch (error) {
      notify(error.message, true);
    }
  }

  /* ----------------------------------------------------------------- actions */

  function renderActions() {
    const grid = $("#action-grid");
    grid.replaceChildren();
    const actions = state.actions.length ? state.actions : FALLBACK_ACTIONS.map(([id, label]) => ({ id, label }));
    for (const action of actions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "action-button";
      button.textContent = `✦ ${action.label}`;
      button.addEventListener("click", () => sendCommand({ type: "action", action: action.id }));
      grid.append(button);
    }
  }

  function sendCommand(packet) {
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
      notify("控制通道未连接，请稍后再试", true);
      return false;
    }
    state.socket.send(JSON.stringify(packet));
    return true;
  }

  /* ------------------------------------------------------------------ socket */

  function connectSocket() {
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${scheme}//${location.host}/ws`);
    state.socket = socket;
    socket.addEventListener("open", () => {
      $("#ws-status").textContent = "舞台已连接";
      $("#ws-dot").className = "connected";
    });
    socket.addEventListener("close", () => {
      $("#ws-status").textContent = "正在重连";
      $("#ws-dot").className = "error";
      setTimeout(connectSocket, 1600);
    });
    socket.addEventListener("error", () => socket.close());
    socket.addEventListener("message", (message) => {
      let packet;
      try {
        packet = JSON.parse(message.data);
      } catch {
        return;
      }
      if (packet.type === "bilibili_status") {
        state.bilibili = packet;
        updateBiliStatus(packet);
      }
      if (packet.type === "voice_report" && (packet.role === "stage" || packet.role === "preview")) {
        state.stageVoices[packet.role] = packet;
        maybeAdoptStageVoice();
        populateVoices();
      }
      if (packet.type === "stage_layout") {
        const layout = {};
        for (const key of ["pet_x", "pet_y", "pet_scale"]) {
          if (Number.isFinite(Number(packet[key]))) {
            layout[key] = Number(packet[key]);
            state.settings[key] = Number(packet[key]);
          }
        }
        if (Object.keys(layout).length) syncLayoutControls(layout, true);
      }
      if (packet.type === "command_error") notify(packet.message, true);
      if (packet.type === "reply_status") updateReplyStatus(packet);
    });
  }

  /* ------------------------------------------------------------------- setup */

  async function loadSettings() {
    const data = await request("/api/settings");
    state.settings = data.settings || {};
    state.music = data.music || state.music;
    state.bilibili = data.bilibili || {};
    state.douyin = data.douyin || {};
    updateReplyQueue(data.reply_queue);
    state.stageVoices = data.stage_voices || { stage: null, preview: null };
    state.memory = data.memory || state.memory;
    state.actions = data.actions || [];
    try {
      applyBackgroundPayload(await request("/api/backgrounds"));
    } catch (error) {
      state.backgrounds = [];
    }
    fillSettings(state.settings, state.music);
    updateSummary();
    updateBiliStatus(state.bilibili);
    updateDouyinStatus(state.douyin);
    updateMemory();
    $("#client-count").textContent = String(data.clients || 0);
    renderMusic();
    renderActions();
  }

  function bindLayout() {
    const slider = (selector, key) => {
      $(selector).addEventListener("input", (event) => {
        updateLayoutField(key, Number(event.target.value));
      });
    };
    slider("#pet-x", "pet_x");
    slider("#pet-y", "pet_y");
    slider("#pet-scale", "pet_scale");

    for (const button of $$("#anchor-buttons button")) {
      button.addEventListener("click", () => {
        const anchor = ANCHORS[button.dataset.anchor];
        if (!anchor) return;
        state.settings.pet_x = anchor[0];
        state.settings.pet_y = anchor[1];
        syncLayoutControls({ pet_x: anchor[0], pet_y: anchor[1] });
        sendLayout({ pet_x: anchor[0], pet_y: anchor[1] });
      });
    }

    $("#reset-layout").addEventListener("click", () => {
      const values = { pet_x: 70, pet_y: 55, pet_scale: 1 };
      Object.assign(state.settings, values);
      syncLayoutControls(values);
      sendLayout(values);
      notify("宠物布局已恢复默认");
    });

    for (const [selector, key] of [
      ["#pet-flip", "pet_flip"],
      ["#pet-shadow", "pet_shadow"],
      ["#show-brand", "show_brand"],
      ["#feed-enabled", "feed_enabled"],
      ["#speech-enabled", "speech_enabled"],
    ]) {
      $(selector).addEventListener("change", (event) => {
        state.settings[key] = event.target.checked;
        if (key === "speech_enabled") {
          queueVoiceSettings(0);
          return;
        }
        queuePatch({ [key]: event.target.checked }, 0);
      });
    }

    $("#music-enabled").addEventListener("change", (event) => {
      const enabled = event.target.checked;
      const patch = { music_enabled: enabled };
      if (enabled && !state.music.active && state.music.tracks.length) {
        state.music.active = state.music.tracks[0].id;
        patch.music_track = state.music.active;
      }
      state.settings.music_enabled = enabled;
      state.music.enabled = enabled;
      renderMusic();
      queuePatch(patch, 0);
    });

    $("#feed-position").addEventListener("change", (event) => {
      queuePatch({ feed_position: event.target.value }, 0);
    });

    $("#background-dim").addEventListener("input", (event) => {
      const value = Number(event.target.value);
      setOutput("#dim-value", formatPercent(value * 100));
      queuePatch({ background_dim: value });
    });

    $("#sleep-after").addEventListener("input", (event) => {
      const value = Number(event.target.value);
      setOutput("#sleep-after-value", value ? `${value} 分钟` : "不睡");
      queuePatch({ sleep_after: value });
    });

    $("#day-cycle").addEventListener("input", (event) => {
      const value = Number(event.target.value);
      setOutput("#day-cycle-value", value ? `${value} 分钟` : "关闭");
      queuePatch({ day_cycle_minutes: value });
    });

    $("#hud-enabled").addEventListener("change", (event) => {
      queuePatch({ hud_enabled: event.target.checked }, 0);
    });
    $("#hud-position").addEventListener("change", (event) => {
      queuePatch({ hud_position: event.target.value }, 0);
    });
    $("#hud-opacity").addEventListener("input", (event) => {
      const value = Number(event.target.value);
      setOutput("#hud-opacity-value", formatPercent(value * 100));
      queuePatch({ hud_opacity: value });
    });
    $("#hud-scale").addEventListener("input", (event) => {
      const value = Number(event.target.value);
      setOutput("#hud-scale-value", formatScale(value));
      queuePatch({ hud_scale: value });
    });

    $("#idle-chatter-interval").addEventListener("input", (event) => {
      setOutput("#idle-chatter-value", `${Number(event.target.value)} 秒`);
    });
    $("#memory-turns").addEventListener("input", (event) => {
      setOutput("#memory-turns-value", String(Number(event.target.value)));
    });
    $("#speech-rate").addEventListener("input", () => {
      applyVoiceLabels();
      queueVoiceSettings();
    });
    $("#speech-pitch").addEventListener("input", () => {
      applyVoiceLabels();
      queueVoiceSettings();
    });
    $("#speech-volume").addEventListener("input", () => {
      applyVoiceLabels();
      queueVoiceSettings();
    });
    $("#speech-voice").addEventListener("change", (event) => {
      event.target.dataset.selected = event.target.value;
      state.settings.speech_voice = event.target.value;
      queueVoiceSettings(0);
      updateVoiceStatus();
    });
    $("#speech-provider").addEventListener("change", () => {
      if ($("#speech-provider").value === "azure" && !$("#azure-speech-voice").value) {
        $("#azure-speech-voice").value = "zh-CN-XiaoxiaoNeural";
      }
      updateSpeechProviderControls();
    });
    for (const selector of ["#speech-model", "#speech-openai-voice"]) {
      $(selector).addEventListener("input", updateVoiceStatus);
    }
    $("#azure-speech-voice").addEventListener("change", () => {
      updateVoiceStatus();
      queueVoiceSettings(0);
    });
    $("#music-volume").addEventListener("input", (event) => {
      const value = Number(event.target.value);
      state.music.volume = value;
      setOutput("#music-volume-value", formatVolume(value));
      queuePatch({ music_volume: value });
    });
    $("#music-duck").addEventListener("input", (event) => {
      const value = Number(event.target.value);
      state.music.duck = value;
      setOutput("#music-duck-value", formatVolume(value));
      queuePatch({ music_duck: value });
    });
  }

  function bindMusic() {
    $("#music-toggle").addEventListener("click", toggleMusic);
    $("#music-prev").addEventListener("click", () => stepTrack(-1));
    $("#music-next").addEventListener("click", () => stepTrack(1));
    $("#quick-music").addEventListener("click", toggleMusic);

    $("#music-loop").addEventListener("change", (event) => {
      state.music.loop = event.target.checked;
      queuePatch({ music_loop: event.target.checked }, 0);
    });

    $("#music-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const file = form.elements.file.files[0];
      if (!file) {
        setMessage("#music-result", "请选择音频文件", true);
        return;
      }
      const result = $("#music-result");
      setMessage("#music-result", "正在上传…");
      try {
        const payload = await request("/api/music", { method: "POST", body: new FormData(form) });
        applyMusicPayload(payload);
        form.reset();
        setMessage("#music-result", `已添加「${payload.track ? payload.track.title : file.name}」`);
        notify("音乐已加入曲库");
      } catch (error) {
        setMessage("#music-result", error.message, true);
      }
    });

    $("#music-scan").addEventListener("click", async () => {
      setMessage("#music-result", "正在扫描目录…");
      try {
        const payload = await request("/api/music/scan", { method: "POST" });
        applyMusicPayload(payload);
        const added = payload.added ? payload.added.length : 0;
        setMessage("#music-result", added ? `新收录 ${added} 首。` : "目录里没有新音频。");
        if (added) notify(`扫描到 ${added} 首新音乐`);
      } catch (error) {
        setMessage("#music-result", error.message, true);
      }
    });
  }

  function bindBackground() {
    $("#background-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      try {
        const payload = await request("/api/background", { method: "POST", body: new FormData(form) });
        applyBackgroundPayload(payload);
        setMessage("#background-result", "背景已上传并应用到舞台。");
        form.reset();
      } catch (error) {
        setMessage("#background-result", error.message, true);
      }
    });

    $("#remove-background").addEventListener("click", async () => {
      try {
        const payload = await request("/api/background", { method: "DELETE" });
        applyBackgroundPayload(payload);
        setMessage("#background-result", "已恢复透明背景，图片仍留在背景库里。");
      } catch (error) {
        setMessage("#background-result", error.message, true);
      }
    });
  }

  function bindForms() {
    $("#api-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      setMessage("#api-result", "正在保存…");
      try {
        const result = await postJSON("/api/settings", {
          llm_base_url: data.get("llm_base_url"),
          llm_model: data.get("llm_model"),
          persona: data.get("persona"),
          api_key: data.get("api_key"),
          clear_api_key: data.get("clear_api_key") === "on",
        });
        applySettingsPayload(result);
        form.elements.api_key.value = "";
        form.elements.api_key.placeholder = result.settings.api_key_set
          ? "已保存密钥；留空保持不变"
          : "输入 API Key（可选）";
        setMessage("#api-result", "API 设置已保存并应用。");
      } catch (error) {
        setMessage("#api-result", error.message, true);
      }
    });

    $("#test-api").addEventListener("click", async () => {
      setMessage("#api-result", "正在测试模型列表接口…");
      try {
        const data = await request("/api/llm/test", { method: "POST" });
        setMessage("#api-result", data.models && data.models.length
          ? `连接成功：${data.models.join("、")}`
          : "连接成功，服务端未返回模型列表。");
      } catch (error) {
        setMessage("#api-result", error.message, true);
      }
    });

    $("#bili-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      try {
        const result = await postJSON("/api/settings", {
          bili_room_id: data.get("bili_room_id"),
          sessdata: data.get("sessdata"),
          clear_sessdata: data.get("clear_sessdata") === "on",
        });
        applySettingsPayload(result);
        form.elements.sessdata.value = "";
        form.elements.sessdata.placeholder = result.settings.sessdata_set
          ? "已保存登录态；留空保持不变"
          : "可选，仅用于需要登录的房间";
        notify("房间设置已保存");
      } catch (error) {
        notify(error.message, true);
      }
    });

    $("#connect-room").addEventListener("click", async () => {
      const form = $("#bili-form");
      const data = new FormData(form);
      const roomId = String(data.get("bili_room_id") || "").trim();
      if (!/^\d+$/.test(roomId) || Number(roomId) <= 0) {
        notify("请先填写有效的房间号", true);
        return;
      }
      try {
        const payload = await postJSON("/api/bilibili", {
          action: "connect",
          room_id: roomId,
          sessdata: data.get("sessdata"),
          clear_sessdata: data.get("clear_sessdata") === "on",
        });
        form.elements.sessdata.value = "";
        form.elements.clear_sessdata.checked = false;
        state.bilibili = payload.bilibili;
        updateBiliStatus(payload.bilibili);
        notify(`正在连接 B 站房间 ${roomId}（只读）`);
      } catch (error) {
        notify(error.message, true);
      }
    });

    $("#disconnect-room").addEventListener("click", async () => {
      try {
        const payload = await postJSON("/api/bilibili", { action: "disconnect" });
        state.bilibili = payload.bilibili;
        updateBiliStatus(payload.bilibili);
        notify("B 站连接已断开");
      } catch (error) {
        notify(error.message, true);
      }
    });

    $("#douyin-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      try {
        const result = await postJSON("/api/settings", {
          douyin_room_id: String(data.get("douyin_room_id") || "").trim(),
          douyin_bridge_url: String(data.get("douyin_bridge_url") || "").trim(),
        });
        applySettingsPayload(result);
        notify("抖音房间设置已保存");
      } catch (error) {
        notify(error.message, true);
      }
    });

    $("#connect-douyin").addEventListener("click", async () => {
      const form = $("#douyin-form");
      const data = new FormData(form);
      const roomId = String(data.get("douyin_room_id") || "").trim();
      const bridgeUrl = String(data.get("douyin_bridge_url") || "").trim();
      if (!/^\d+$/.test(roomId) || Number(roomId) <= 0) {
        notify("请先填写有效的抖音房间号（web_rid）", true);
        return;
      }
      try {
        const payload = await postJSON("/api/douyin", {
          action: "connect",
          room_id: roomId,
          bridge_url: bridgeUrl,
        });
        state.douyin = payload.douyin;
        updateDouyinStatus(payload.douyin);
        notify(`正在通过 DyHub 连接抖音房间 ${roomId}`);
      } catch (error) {
        notify(error.message, true);
      }
    });

    $("#disconnect-douyin").addEventListener("click", async () => {
      try {
        const payload = await postJSON("/api/douyin", { action: "disconnect" });
        state.douyin = payload.douyin;
        updateDouyinStatus(payload.douyin);
        notify("抖音连接已断开");
      } catch (error) {
        notify(error.message, true);
      }
    });

    $("#memory-enabled").addEventListener("change", (event) => {
      state.memory.enabled = event.target.checked;
      queuePatch({ memory_enabled: event.target.checked }, 0);
    });

    $("#memory-turns").addEventListener("change", (event) => {
      state.memory.turns = Number(event.target.value);
      queuePatch({ memory_turns: Number(event.target.value) }, 0);
    });

    $("#clear-memory").addEventListener("click", async () => {
      if (!window.confirm("确定清空最近的对话记忆吗？这会重置连续剧情。")) return;
      try {
        const payload = await request("/api/memory", { method: "DELETE" });
        state.memory.exchanges = 0;
        $("#memory-count").textContent = "0";
        notify(`已清空 ${payload.cleared} 条记忆`);
      } catch (error) {
        notify(error.message, true);
      }
    });

    $("#voice-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const result = await postJSON("/api/settings", voiceFormPayload());
        applySettingsPayload(result);
        $("#speech-key").value = "";
        $("#clear-speech-key").checked = false;
        $("#speech-voice").dataset.selected = $("#speech-voice").value;
        setMessage("#voice-result", "语音设置已保存并同步到舞台。");
      } catch (error) {
        setMessage("#voice-result", error.message, true);
      }
    });

    $("#test-voice").addEventListener("click", async () => {
      try {
        const result = await postJSON("/api/settings", voiceFormPayload());
        applySettingsPayload(result);
        $("#speech-key").value = "";
        $("#clear-speech-key").checked = false;
        $("#speech-voice").dataset.selected = $("#speech-voice").value;
        if (!sendCommand({ type: "speech_test" })) return;
        const stageConnected = state.settings.speech_provider !== "browser"
          || Boolean(state.stageVoices.stage
            && Array.isArray(state.stageVoices.stage.voices)
            && state.stageVoices.stage.voices.length);
        setMessage(
          "#voice-result",
          state.settings.speech_provider !== "browser"
            ? "固定音色试听已发送到舞台。"
            : (stageConnected
              ? "已向 OBS 舞台发送试听。"
              : "已发送试听，但还没检测到 OBS 舞台；请打开舞台，或勾选「预览声音」。"),
          !stageConnected,
        );
      } catch (error) {
        setMessage("#voice-result", error.message, true);
      }
    });

    $("#chatter-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const result = await postJSON("/api/settings", {
          idle_chatter_enabled: $("#idle-chatter-enabled").checked,
          idle_chatter_interval: Number($("#idle-chatter-interval").value),
        });
        applySettingsPayload(result);
        setMessage("#chatter-result", "冷场连续输出设置已保存并同步。");
      } catch (error) {
        setMessage("#chatter-result", error.message, true);
      }
    });

    $("#event-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      try {
        await postJSON("/api/events", {
          platform: "local",
          kind: data.get("kind"),
          user: { name: data.get("name") },
          text: data.get("text"),
        });
        setMessage("#event-result", "测试事件已发送。");
      } catch (error) {
        setMessage("#event-result", error.message, true);
      }
    });
  }

  function bindMisc() {
    const stageUrl = `${location.origin}/`;
    $("#obs-url").textContent = stageUrl;

    const copyText = async (value, message) => {
      try {
        await navigator.clipboard.writeText(value);
        notify(message);
      } catch {
        const field = document.createElement("textarea");
        field.value = value;
        document.body.append(field);
        field.select();
        document.execCommand("copy");
        field.remove();
        notify(message);
      }
    };
    $("#copy-obs").addEventListener("click", () => copyText(stageUrl, "舞台地址已复制，粘贴到 OBS 浏览器源即可"));
    $("#copy-obs-2").addEventListener("click", () => copyText(stageUrl, "舞台地址已复制，粘贴到 OBS 浏览器源即可"));

    const reloadPreview = () => {
      const audio = $("#preview-audio").checked ? "&preview_audio=1" : "";
      $("#stage-preview").src = `/?preview=1${audio}&t=${Date.now()}`;
    };
    $("#refresh-preview").addEventListener("click", () => {
      reloadPreview();
      notify("预览已刷新");
    });
    $("#preview-audio").addEventListener("change", () => {
      reloadPreview();
      notify($("#preview-audio").checked
        ? "预览声音已开启：使用控制台浏览器音色，可能与 OBS 不同"
        : "预览已静音，声音以 OBS 舞台为准");
    });

    $("#quick-wave").addEventListener("click", () => sendCommand({ type: "action", action: "welcome_wave" }));
    $("#quick-jump").addEventListener("click", () => sendCommand({ type: "action", action: "happy_jump" }));
    $("#quick-event").addEventListener("click", () => {
      $("#event-form").scrollIntoView({ behavior: "smooth", block: "center" });
      const input = $("#event-form [name=text]");
      input.focus();
      input.select();
    });
  }

  function bindScrollSpy() {
    const links = $$(".nav-link");
    const sections = links
      .map((link) => document.querySelector(link.getAttribute("href")))
      .filter(Boolean);
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        for (const link of links) {
          link.classList.toggle("active", link.getAttribute("href") === `#${entry.target.id}`);
        }
      }
    }, { rootMargin: "-18% 0px -66% 0px", threshold: 0 });
    sections.forEach((section) => observer.observe(section));
  }

  async function pollHealth() {
    try {
      const health = await request("/health");
      $("#client-count").textContent = String(health.clients || 0);
      $("#llm-summary").textContent = health.llm_enabled ? "运行中" : "未配置";
      if (health.bilibili) {
        state.bilibili = health.bilibili;
        updateBiliStatus(health.bilibili);
      }
      if (health.douyin) {
        state.douyin = health.douyin;
        updateDouyinStatus(health.douyin);
      }
      updateReplyQueue(health.reply_queue);
      if (typeof health.memory_exchanges === "number") {
        state.memory.exchanges = health.memory_exchanges;
        $("#memory-count").textContent = String(health.memory_exchanges);
      }
    } catch {
      $("#ws-status").textContent = "状态读取失败";
      $("#ws-dot").className = "error";
    }
  }

  /* -------------------------------------------------------------------- init */

  populateVoices();
  if ("speechSynthesis" in window) {
    window.speechSynthesis.addEventListener("voiceschanged", populateVoices);
  }
  bindLayout();
  bindMusic();
  bindBackground();
  bindForms();
  bindMisc();
  bindScrollSpy();
  connectSocket();
  loadSettings().catch((error) => notify(`读取设置失败：${error.message}`, true));
  pollHealth();
  setInterval(pollHealth, 5000);
})();
