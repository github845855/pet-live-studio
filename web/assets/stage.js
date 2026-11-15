(() => {
  const stageEl = document.querySelector("#stage");
  const backdrops = [document.querySelector("#backdrop-a"), document.querySelector("#backdrop-b")];
  const brand = document.querySelector("#brand");
  const liveDot = document.querySelector("#live-dot");
  const connection = document.querySelector("#connection");
  const petWrap = document.querySelector("#pet-wrap");
  const pet = document.querySelector("#pet");
  const clipEls = [document.querySelector("#pet-a"), document.querySelector("#pet-b")];
  const shadow = document.querySelector("#shadow");
  const bubble = document.querySelector("#bubble");
  const feed = document.querySelector("#feed");
  const feedList = document.querySelector("#feed-list");
  const feedCount = document.querySelector("#feed-count");
  const soundHint = document.querySelector("#sound-hint");
  const petHud = document.querySelector("#pet-hud");
  const musicPlayers = [document.querySelector("#bgm-a"), document.querySelector("#bgm-b")];

  const params = new URLSearchParams(location.search);
  const previewMode = params.get("preview") === "1";
  const previewAudio = params.get("preview_audio") === "1";
  const audioAllowed = !previewMode || previewAudio;
  if (previewMode) stageEl.classList.add("edit");
  const clientId = window.crypto && crypto.randomUUID
    ? crypto.randomUUID()
    : `stage-${Date.now()}-${Math.random().toString(16).slice(2)}`;

  const ACTIONS = new Set([
    "idle", "welcome_wave", "happy_jump", "shy", "grumpy", "laugh",
    "thinking", "busy", "celebrate", "bored", "surprised", "gift", "sleepy",
  ]);
  const KIND_LABELS = {
    danmaku: "弹幕", gift: "礼物", enter: "进场",
    follow: "关注", live: "开播", offline: "下播",
  };
  const EVENT_LABELS = {
    gift: "送来一份礼物", enter: "来到直播间", follow: "成为支持者",
    live: "直播开始啦", offline: "直播结束啦",
  };

  let manifest = { generated_at: 2, actions: {} };
  let stageConfig = {
    pet_scale: 1, pet_x: 70, pet_y: 55, pet_flip: false, pet_shadow: true,
    show_brand: true, feed_enabled: true, feed_position: "left",
    background_dim: 0.35, background_url: "",
  };
  let speechConfig = { enabled: false, provider: "browser", voice: "", rate: 1, pitch: 1, volume: 1 };
  let speechConfigVersion = 0;
  let musicConfig = { enabled: false, volume: 0.6, duck: 0.35, loop: true, url: "", title: "" };

  let bubbleTimer;
  let actionTimer;
  let actionVersion = 0;
  let lastSpeechText = "";
  let feedItems = 0;
  let ducked = false;
  let musicActive = 0;
  let musicRaf = 0;
  let musicCross = null;
  let musicPauseTimer;
  let backdropIndex = 0;
  let backdropUrl = "";
  let layoutTimer;
  let drag = null;
  const speechAudio = new Audio();
  let speechRequestController = null;
  let speechObjectUrl = "";
  let speechVersion = 0;
  let activeSpeechFinish = null;
  let activeSpeechItem = null;
  let speechPhase = "";
  let speechPrefetchSlot = null;
  let monologueCompletionTimer = 0;
  let speechQueue = [];
  let speechQueueRunning = false;
  let speechSequence = 0;
  const pendingMonologueCompletions = new Map();
  let speechOwnerId = "";
  // Replies that arrive before the server has told us who owns audio are
  // buffered for a moment instead of being dropped (the stage reloads often,
  // and the old code lost every line in that window).
  let speechOwnerKnown = false;
  let pendingReplies = [];
  let pendingReplyTimer = 0;
  let activeUtterance = null;
  let socket;
  let reconnectDelay = 900;

  const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));
  const round = (value) => Math.round(value * 100) / 100;

  function delay(ms, signal) {
    return new Promise((resolve, reject) => {
      if (signal && signal.aborted) {
        reject(new DOMException("Aborted", "AbortError"));
        return;
      }
      const timer = setTimeout(() => {
        if (signal) signal.removeEventListener("abort", onAbort);
        resolve();
      }, ms);
      function onAbort() {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      }
      if (signal) signal.addEventListener("abort", onAbort, { once: true });
    });
  }

  function formatNumber(value) {
    return Number.isFinite(Number(value)) ? String(value) : "";
  }

  /* ------------------------------------------------------------- animations */

  // 动作片段：webm（VP9 alpha），两层 video 交叉切换。catalog 由服务端随
  // stage_config 下发，包含时长、别名、分类权重与事件档位。
  let actionCatalog = { clips: {}, aliases: {}, categories: [], events: {}, weights: {} };
  let videoReady = false;
  let clipFront = 0;
  let clipToken = 0;
  let clipErrors = 0;
  let lastClip = "";
  let motionTimer = 0;
  let asleep = false;
  let sleepTimer = 0;
  let sleepAfterMs = 20 * 60 * 1000;
  let dayCycleMs = 0;
  let dayStartMs = 0;
  let dayPhase = "";
  let lastPetStats = null;
  let hudEnabled = true;
  let hudHasState = false;

  function assetUrl(action) {
    const version = manifest.generated_at || 2;
    return `/assets/animations/${action}.webp?v=${version}`;
  }

  function actionDuration(action) {
    const info = manifest.actions && manifest.actions[action];
    if (info && Number.isFinite(Number(info.duration)) && Number(info.duration) > 0) {
      return Math.max(1200, Number(info.duration) * 1000);
    }
    return 10200;
  }

  async function loadManifest() {
    try {
      const response = await fetch("/assets/animations/manifest.json", { cache: "no-store" });
      if (response.ok) {
        const data = await response.json();
        if (data && typeof data === "object" && data.actions) manifest = data;
      }
    } catch {
      /* the stage still works with the built-in fallback durations */
    }
  }

  function setActionCatalog(next) {
    if (!next || typeof next !== "object" || !next.clips) return;
    actionCatalog = {
      clips: next.clips || {},
      aliases: next.aliases || {},
      categories: Array.isArray(next.categories) ? next.categories : [],
      events: next.events || {},
      weights: next.weights || {},
    };
    if (!videoReady && Object.keys(actionCatalog.clips).length) {
      videoReady = true;
      clipErrors = 0;
      pet.hidden = true;
      clipEls.forEach((element) => { element.hidden = false; });
      playAction("idle");
      // 开播后先待机，过一会儿再开始随机小动作；再久没人就睡。
      scheduleNextMotion();
      scheduleSleep();
    }
  }

  function clipDuration(name) {
    const seconds = Number(actionCatalog.clips[name]);
    return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : 10000;
  }

  function clipUrl(name) {
    return `/actions/${encodeURIComponent(name)}.webm`;
  }

  function pickFrom(pool, avoid) {
    const list = Array.isArray(pool) ? pool : [];
    if (!list.length) return "";
    const filtered = list.length > 1 && avoid ? list.filter((item) => item !== avoid) : list;
    const source = filtered.length ? filtered : list;
    return source[Math.floor(Math.random() * source.length)];
  }

  // 播一段动作；once=false 表示循环（待机用）。世代号保证迟到的加载不会顶掉新动作。
  function playClip(name, once = true) {
    if (!videoReady || !actionCatalog.clips[name]) return false;
    const token = ++clipToken;
    const front = clipEls[clipFront];
    const back = clipEls[1 - clipFront];
    clearTimeout(motionTimer);
    back.onended = null;
    back.onerror = () => {
      if (token === clipToken) handleClipError();
    };
    back.onplaying = () => {
      if (token === clipToken) clipErrors = 0;
    };
    back.loop = !once;
    back.src = clipUrl(name);
    lastClip = name;
    const start = () => {
      if (token !== clipToken) return;
      back.classList.add("front");
      front.classList.remove("front");
      clipFront = 1 - clipFront;
      const playing = back.play();
      if (playing && typeof playing.catch === "function") playing.catch(() => {});
      if (once) {
        back.onended = () => {
          if (token !== clipToken) return;
          finishMotion();
        };
      }
    };
    if (back.readyState >= 2) start();
    else back.addEventListener("loadeddata", start, { once: true });
    return true;
  }

  function finishMotion() {
    if (asleep) return;
    playAction("idle");
    scheduleNextMotion();
  }

  // 片段加载失败：先回待机；连续失败说明素材整个缺失，退回静态 webp，别空转。
  function handleClipError() {
    clipErrors += 1;
    if (clipErrors < 4) {
      finishMotion();
      return;
    }
    videoReady = false;
    clearTimeout(motionTimer);
    clipEls.forEach((element) => {
      element.classList.remove("front");
      element.hidden = true;
    });
    pet.hidden = false;
    playAction("idle");
  }

  /* ---------------------------------------------------------------- sleeping */

  // 长时间没人互动就睡：start（一次）→ loop（循环）；有人说话先播 end 再办事。
  function sleepClips() {
    return (actionCatalog.events && actionCatalog.events.sleep) || {};
  }

  function scheduleSleep() {
    clearTimeout(sleepTimer);
    if (!videoReady || !sleepAfterMs) return;
    sleepTimer = setTimeout(() => enterSleep(), sleepAfterMs);
  }

  function enterSleep() {
    if (asleep || !videoReady) return;
    const clips = sleepClips();
    const start = pickFrom(clips.start, "");
    if (!start) return;
    asleep = true;
    clearTimeout(motionTimer);
    sendStageSleep(true);
    const loop = pickFrom(clips.loop, "");
    playClip(start, true, () => {
      if (asleep && loop) playClip(loop, false);
    });
  }

  function wakeUp(then) {
    clearTimeout(sleepTimer);
    scheduleSleep();
    if (!asleep) {
      if (then) then();
      return;
    }
    asleep = false;
    sendStageSleep(false);
    const end = pickFrom(sleepClips().end, "");
    if (!end) {
      if (then) then();
      else finishMotion();
      return;
    }
    playClip(end, true, () => {
      if (then) then();
      else finishMotion();
    });
  }

  function sendStageSleep(value) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    try {
      socket.send(JSON.stringify({ type: "stage_state", asleep: value }));
    } catch {
      /* 服务端连不上时舞台自己也能醒 */
    }
  }

  function noteActivity() {
    if (!videoReady) return;
    wakeUp(null);
  }

  function playEventClip(name) {
    if (!name || !videoReady) return;
    if (asleep) wakeUp(() => playClip(name, true));
    else playClip(name, true);
  }

  /* -------------------------------------------------------------- pet stats */

  // 四条状态：饱食 / 心情 / 精力 / 亲密。值由服务端算，舞台只负责显示。
  function applyPetState(state) {
    if (!state || typeof state !== "object") return;
    hudHasState = true;
    petHud.hidden = !hudEnabled;
    const labels = { hunger: "饱食", mood: "心情", energy: "精力", bond: "亲密" };
    if (lastPetStats) {
      for (const key of Object.keys(labels)) {
        const before = Number(lastPetStats[key]);
        const after = Number(state[key]);
        if (!Number.isFinite(before) || !Number.isFinite(after)) continue;
        const delta = after - before;
        if (Math.abs(delta) < 1) continue;
        spawnDelta(`${delta > 0 ? "+" : ""}${Math.round(delta)} ${labels[key]}`, delta > 0);
      }
    }
    lastPetStats = {
      hunger: Number(state.hunger) || 0,
      mood: Number(state.mood) || 0,
      energy: Number(state.energy) || 0,
      bond: Number(state.bond) || 0,
    };
    const bars = [
      ["#hud-hunger", state.hunger, 100],
      ["#hud-mood", state.mood, 100],
      ["#hud-energy", state.energy, 100],
      ["#hud-bond", state.bond, 120],
    ];
    for (const [selector, value, ceiling] of bars) {
      const bar = document.querySelector(selector);
      if (!bar) continue;
      const percent = clamp((Number(value) || 0) / ceiling, 0, 1) * 100;
      bar.style.width = `${Math.round(percent)}%`;
    }
    const level = document.querySelector("#hud-level");
    if (level) level.textContent = state.level || "";
  }

  // 弹幕指令：服务端已经判定过了，舞台只管播片段 + 冒一句话。
  function playMotion(packet) {
    if (packet.text) showBubble(packet.text, 3200);
    if (packet.clip) playEventClip(packet.clip);
  }

  /* ------------------------------------------------------------ 一天 60 分钟 */

  // 现实 N 分钟 = 宠物的一天。到点就演该时段该做的事，随机动作也会偏向该时段。
  const DAY_PHASES = [
    { until: 6, id: "night", pool: ["睡觉-循环", "原地小憩沉眠"] },
    { until: 9, id: "morning", pool: ["晨间刷牙", "吃早餐", "超大伸懒腰"] },
    { until: 12, id: "forenoon", pool: ["悠闲哼歌", "写代码", "下五子棋"] },
    { until: 14, id: "noon", pool: ["吃午餐", "吃饭团", "摇扇纳凉"] },
    { until: 18, id: "afternoon", pool: ["原地专心玩魔方", "撸猫", "三球抛接"] },
    { until: 21, id: "evening", pool: ["吃晚餐", "优雅女仆舞", "放烟花"] },
    { until: 23, id: "late", pool: ["哈欠连天", "碎碎念-发呆碎碎念"] },
    { until: 24, id: "night", pool: ["睡觉-循环"] },
  ];

  function phaseForHour(hour) {
    for (const phase of DAY_PHASES) {
      if (hour < phase.until) return phase;
    }
    return DAY_PHASES[DAY_PHASES.length - 1];
  }

  function phasePool(phase) {
    return (phase.pool || []).filter((name) => actionCatalog.clips && actionCatalog.clips[name]);
  }

  function setDayCycle(minutes, startedAt) {
    const value = Number(minutes);
    dayCycleMs = Number.isFinite(value) && value > 0 ? value * 60000 : 0;
    const start = Number(startedAt);
    dayStartMs = Number.isFinite(start) && start > 0 ? start : Date.now();
    dayPhase = "";
    updateDayClock(true);
  }

  function dayFraction() {
    if (!dayCycleMs) return 0;
    return (((Date.now() - dayStartMs) % dayCycleMs) + dayCycleMs) % dayCycleMs / dayCycleMs;
  }

  function updateDayClock(force = false) {
    if (!dayCycleMs) {
      setClockLabel("");
      return;
    }
    const minutes = dayFraction() * 1440;
    const hour = minutes / 60;
    const hh = String(Math.floor(hour) % 24).padStart(2, "0");
    const mm = String(Math.floor(minutes % 60)).padStart(2, "0");
    setClockLabel(`${hh}:${mm}`);
    const phase = phaseForHour(hour);
    if (!force && dayPhase && phase.id !== dayPhase) {
      const pool = phasePool(phase);
      const routine = pool.length ? pool[Math.floor(Math.random() * pool.length)] : "";
      if (asleep) {
        // 作息到点先醒，再演这个时段该做的事；否则会出现「09:39 还在睡」。
        wakeUp(routine ? () => playClip(routine, true) : null);
      } else if (routine) {
        playEventClip(routine);
      }
    }
    dayPhase = phase.id;
  }

  function setClockLabel(text) {
    const label = document.querySelector("#hud-clock");
    if (label) label.textContent = text;
  }

  function currentPhase() {
    if (!dayCycleMs) return null;
    return phaseForHour(dayFraction() * 24);
  }

  setInterval(() => updateDayClock(false), 15000);

  /* --------------------------------------------------------------- 状态浮字 */

  function spawnDelta(text, positive) {
    const box = document.querySelector("#hud-deltas");
    if (!box) return;
    const chip = document.createElement("span");
    chip.className = `hud-delta ${positive ? "up" : "down"}`;
    chip.textContent = text;
    box.append(chip);
    setTimeout(() => chip.remove(), 1700);
  }

  // 状态面板：显隐、四角位置、透明度、大小都由服务端下发。
  function applyHudConfig(cfg) {
    if (!cfg || typeof cfg !== "object") return;
    hudEnabled = cfg.enabled !== false;
    if (cfg.position) petHud.dataset.position = String(cfg.position);
    if (Number.isFinite(Number(cfg.opacity))) {
      petHud.style.setProperty("--hud-opacity", String(clamp(Number(cfg.opacity), 0.2, 1)));
    }
    if (Number.isFinite(Number(cfg.scale))) {
      petHud.style.setProperty("--hud-scale", String(clamp(Number(cfg.scale), 0.6, 1.6)));
    }
    petHud.hidden = !hudEnabled || !hudHasState;
  }

  function scheduleNextMotion(delay) {
    clearTimeout(motionTimer);
    const wait = Number.isFinite(delay) ? delay : 9000 + Math.random() * 12000;
    motionTimer = setTimeout(() => rollMotion(), wait);
  }

  // 加权抽一个分类，再在分类里避开上一段。
  function rollMotion() {
    if (!videoReady) return;
    // 一半概率按「现在几点」挑，另一半走分类权重，作息和随机都有。
    const phase = currentPhase();
    if (phase && Math.random() < 0.5) {
      const name = pickFrom(phasePool(phase), lastClip);
      if (name && playClip(name, true)) return;
    }
    const categories = actionCatalog.categories || [];
    if (!categories.length) return;
    const weights = actionCatalog.weights || {};
    const idleWeight = Number(weights.idle) || 0;
    if (Math.random() * 100 < idleWeight) {
      playAction("idle");
      scheduleNextMotion();
      return;
    }
    const total = Number(weights.categories) || 100;
    let roll = Math.random() * total;
    let chosen = categories[categories.length - 1];
    for (const category of categories) {
      if (roll < category.weight) {
        chosen = category;
        break;
      }
      roll -= category.weight;
    }
    const name = pickFrom(chosen.actions, lastClip);
    if (!name || !playClip(name, true)) scheduleNextMotion();
  }

  function playAction(action) {
    if (!ACTIONS.has(action)) return;
    clearTimeout(actionTimer);
    clearTimeout(motionTimer);
    if (asleep) {
      wakeUp(() => playAction(action));
      return;
    }
    const version = ++actionVersion;
    const clip = (actionCatalog.aliases || {})[action] || "";
    if (videoReady && clip && actionCatalog.clips[clip]) {
      if (action === "idle") {
        playClip(clip, false);
        return;
      }
      if (playClip(clip, true)) return;
    }
    // 兜底：动作片段不可用时退回旧的 webp 动画。
    const isIdle = action === "idle";
    pet.onload = null;
    pet.onerror = null;
    pet.src = assetUrl("idle");
    if (isIdle) return;
    requestAnimationFrame(() => {
      if (version !== actionVersion) return;
      pet.onload = () => {
        if (version !== actionVersion) return;
        pet.onload = null;
        actionTimer = setTimeout(() => {
          if (version !== actionVersion) return;
          pet.onerror = null;
          pet.src = assetUrl("idle");
        }, actionDuration(action));
      };
      pet.onerror = () => {
        if (version !== actionVersion) return;
        pet.onload = null;
        pet.onerror = null;
        pet.src = assetUrl("idle");
      };
      pet.src = assetUrl(action);
    });
  }
  /* ------------------------------------------------------------------ bubble */

  function positionBubble() {
    if (!bubble.textContent) return;
    const stageRect = stageEl.getBoundingClientRect();
    if (!stageRect.width || !stageRect.height) return;
    const petRect = petWrap.getBoundingClientRect();
    const width = Math.min(bubble.offsetWidth || 280, stageRect.width - 28);
    const height = bubble.offsetHeight || 0;
    let center = petRect.left - stageRect.left + petRect.width / 2;
    center = clamp(center, width / 2 + 14, stageRect.width - width / 2 - 14);

    // top/below describe the edge the bubble is anchored to. Prefer above the
    // pet; fall back to below; if neither fits (small preview windows), pin it
    // to the top so it never gets clipped.
    const above = petRect.top - stageRect.top - 16;
    const below = petRect.bottom - stageRect.top + 16;
    const minTop = height + 10;
    const maxTop = stageRect.height - 10;
    let useBelow = false;
    let top;
    if (above >= minTop) {
      top = above;
    } else if (below <= maxTop - height) {
      useBelow = true;
      top = below;
    } else {
      top = Math.min(maxTop, Math.max(minTop, above));
    }
    bubble.classList.toggle("below", useBelow);
    bubble.style.left = `${Math.round(center)}px`;
    bubble.style.top = `${Math.round(top)}px`;
  }

  function showBubble(text, duration) {
    bubble.textContent = text;
    positionBubble();
    bubble.classList.add("visible");
    clearTimeout(bubbleTimer);
    if (duration === null) return 0;
    const visibleFor = duration === undefined
      ? Math.min(9000, Math.max(4200, text.length * 150))
      : duration;
    bubbleTimer = setTimeout(() => bubble.classList.remove("visible"), visibleFor);
    return visibleFor;
  }

  function hideBubble() {
    clearTimeout(bubbleTimer);
    bubble.classList.remove("visible");
  }

  function scheduleBubbleHide(ms) {
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(() => bubble.classList.remove("visible"), ms);
  }

  /* -------------------------------------------------------------------- feed */

  function addFeed(event) {
    feedItems += 1;
    feedCount.textContent = String(Math.min(feedItems, 999));
    const row = document.createElement("div");
    row.className = `feed-item ${event.kind || ""}`;
    const meta = document.createElement("div");
    meta.className = "meta";
    const time = document.createElement("time");
    const date = new Date((Number(event.timestamp) || 0) * 1000);
    time.textContent = Number.isNaN(date.getTime())
      ? ""
      : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    const name = document.createElement("strong");
    name.textContent = (event.user && event.user.name) || "观众";
    const kind = document.createElement("span");
    kind.className = "kind";
    kind.textContent = KIND_LABELS[event.kind] || "互动";
    meta.append(time, name, kind);
    const text = document.createElement("p");
    text.textContent = event.text || EVENT_LABELS[event.kind] || "有新互动";
    row.append(meta, text);
    feedList.append(row);
    feed.classList.add("has-items");
    while (feedList.children.length > 40) feedList.firstElementChild.remove();
    feedList.scrollTop = feedList.scrollHeight;
  }

  function react(event) {
    addFeed(event);
    if (speechQueueRunning || speechQueue.length) return;
    if (event.kind === "enter") return;
    const name = (event.user && event.user.name) || "朋友";
    if (event.kind === "danmaku") {
      showBubble("…", 1600);
    } else if (event.kind === "gift") {
      showBubble(`谢谢 ${name} 的支持！`, 4800);
    } else if (event.kind === "follow") {
      showBubble(`谢谢关注，${name}！`, 4800);
    } else if (event.kind === "live") {
      showBubble("直播开始啦，欢迎进来玩～", 5200);
    } else if (event.kind === "offline") {
      showBubble("今天就到这里，晚安啦～", 5200);
    } else {
      showBubble(EVENT_LABELS[event.kind] || "有新互动");
    }
    // 礼物/关注配一段专门的动作（说话时不抢，react 开头已经返回了）。
    if (event.kind === "gift" || event.kind === "follow") {
      const pool = (actionCatalog.events || {})[event.kind];
      playEventClip(pickFrom(pool, lastClip));
    }
  }

  /* ------------------------------------------------------------------ layout */

  function applyStageConfig(next) {
    stageConfig = { ...stageConfig, ...next };
    stageEl.style.setProperty("--pet-x", formatNumber(round(stageConfig.pet_x)));
    stageEl.style.setProperty("--pet-y", formatNumber(round(stageConfig.pet_y)));
    stageEl.style.setProperty("--pet-scale", formatNumber(stageConfig.pet_scale));
    stageEl.style.setProperty("--pet-flip", stageConfig.pet_flip ? "-1" : "1");
    stageEl.style.setProperty("--dim", formatNumber(clamp(Number(stageConfig.background_dim), 0, 1)));
    shadow.hidden = !stageConfig.pet_shadow;
    brand.hidden = !stageConfig.show_brand;
    feed.dataset.hidden = String(!stageConfig.feed_enabled);
    feed.classList.toggle("right", stageConfig.feed_position === "right");
    feed.classList.toggle("left", stageConfig.feed_position !== "right");
    applyBackdrop(stageConfig.background_url || "");
    if (Object.hasOwn(next, "sleep_after")) setSleepAfter(next.sleep_after);
    if (next.hud) applyHudConfig(next.hud);
    if (Object.hasOwn(next, "day_cycle_minutes")) {
      setDayCycle(next.day_cycle_minutes, next.day_cycle_started_at);
    }
    requestAnimationFrame(positionBubble);
  }

  function setSleepAfter(minutes) {
    const value = Number(minutes);
    sleepAfterMs = Number.isFinite(value) && value >= 0 ? value * 60000 : 20 * 60000;
    scheduleSleep();
  }

  // 双层交叉淡入：换背景不再是硬切。
  function applyBackdrop(url) {
    if (url === backdropUrl) return;
    backdropUrl = url;
    const next = backdrops[1 - backdropIndex];
    next.style.setProperty("--stage-bg", url ? `url("${url}")` : "none");
    void next.offsetWidth; // 先把新图挂上去再淡入，避免同帧两层一起变
    next.classList.toggle("show", Boolean(url));
    backdrops[backdropIndex].classList.remove("show");
    backdropIndex = 1 - backdropIndex;
  }

  function setPetPosition(x, y) {
    stageConfig.pet_x = x;
    stageConfig.pet_y = y;
    stageEl.style.setProperty("--pet-x", formatNumber(round(x)));
    stageEl.style.setProperty("--pet-y", formatNumber(round(y)));
    requestAnimationFrame(positionBubble);
  }

  function sendLayout(values, immediate = true) {
    const message = JSON.stringify({ type: "stage_layout", origin: clientId, ...values });
    if (immediate) {
      if (socket && socket.readyState === WebSocket.OPEN) socket.send(message);
      return;
    }
    clearTimeout(layoutTimer);
    layoutTimer = setTimeout(() => {
      if (socket && socket.readyState === WebSocket.OPEN) socket.send(message);
    }, 320);
  }

  /* ------------------------------------------------------------------- audio */

  // 音乐和语音各走一条音轨：说话时只把 BGM 压到 music_duck，说完再抬回来，
  // 而不是停掉音乐。切歌用两条 <audio> 交叉淡入，避免硬切。
  function duckFactor() {
    const value = Number(musicConfig.duck);
    return Number.isFinite(value) ? clamp(value, 0, 1) : 0.35;
  }

  function musicTargetVolume() {
    if (!musicConfig.enabled || !musicConfig.url) return 0;
    return clamp(Number(musicConfig.volume) || 0, 0, 1) * (ducked ? duckFactor() : 1);
  }

  function musicTick() {
    musicRaf = 0;
    const active = musicPlayers[musicActive];
    const target = musicTargetVolume();
    let running = false;
    if (musicCross) {
      const cross = musicCross;
      const progress = Math.min(1, (performance.now() - cross.startedAt) / cross.duration);
      active.volume = clamp(target * progress, 0, 1);
      const previous = musicPlayers[cross.from];
      previous.volume = clamp(cross.fromVolume * (1 - progress), 0, 1);
      if (progress < 1) {
        running = true;
      } else {
        previous.pause();
        previous.removeAttribute("src");
        previous.load();
        musicCross = null;
      }
    }
    if (!musicCross) {
      const diff = target - active.volume;
      if (Math.abs(diff) > 0.005) {
        active.volume = clamp(active.volume + diff * 0.22, 0, 1);
        running = true;
      } else {
        active.volume = target;
      }
    }
    if (running) musicRaf = requestAnimationFrame(musicTick);
  }

  function syncMusicVolume() {
    if (!musicRaf) musicRaf = requestAnimationFrame(musicTick);
  }

  function showSoundHint(text = "点击开启声音（音乐 / 语音）") {
    if (!audioAllowed) return;
    soundHint.textContent = text;
    soundHint.hidden = false;
  }

  async function tryPlayMusic() {
    if (!audioAllowed || !musicConfig.enabled || !musicConfig.url) return;
    try {
      await musicPlayers[musicActive].play();
      soundHint.hidden = true;
    } catch (error) {
      if (error && error.name === "NotAllowedError") showSoundHint();
    }
  }

  function applyMusic(next) {
    const previousUrl = musicConfig.url;
    musicConfig = { ...musicConfig, ...next };
    clearTimeout(musicPauseTimer);

    if (!audioAllowed) {
      musicCross = null;
      cancelAnimationFrame(musicRaf);
      musicRaf = 0;
      musicPlayers.forEach((player) => {
        player.pause();
        player.removeAttribute("src");
        player.volume = 0;
      });
      return;
    }

    if (musicConfig.url !== previousUrl) {
      musicCross = null;
      cancelAnimationFrame(musicRaf);
      musicRaf = 0;
      if (musicConfig.url) {
        const oldIndex = musicActive;
        const oldPlayer = musicPlayers[oldIndex];
        const incoming = musicPlayers[1 - oldIndex];
        incoming.loop = Boolean(musicConfig.loop);
        incoming.src = musicConfig.url;
        incoming.volume = 0;
        incoming.load();
        musicActive = 1 - oldIndex;
        musicCross = {
          from: oldIndex,
          startedAt: performance.now(),
          duration: 800,
          fromVolume: oldPlayer.volume,
        };
        tryPlayMusic().then(syncMusicVolume);
        return;
      }
      // 清空曲目：先把当前这条淡出，再停掉
      musicPauseTimer = setTimeout(() => {
        if (!musicConfig.url) {
          const player = musicPlayers[musicActive];
          player.pause();
          player.removeAttribute("src");
          player.volume = 0;
        }
      }, 700);
      syncMusicVolume();
      return;
    }

    const active = musicPlayers[musicActive];
    active.loop = Boolean(musicConfig.loop);
    if (musicConfig.enabled) {
      tryPlayMusic().then(syncMusicVolume);
    } else {
      musicPauseTimer = setTimeout(() => {
        if (!musicConfig.enabled) musicPlayers[musicActive].pause();
      }, 700);
      syncMusicVolume();
    }
  }

  function unlockAudio() {
    if (!audioAllowed) return;
    if (musicConfig.enabled && musicConfig.url) tryPlayMusic().then(syncMusicVolume);
    if ("speechSynthesis" in window) window.speechSynthesis.resume();
  }
  window.addEventListener("pointerdown", unlockAudio, { passive: true });
  window.addEventListener("keydown", unlockAudio);

  soundHint.addEventListener("click", () => {
    soundHint.hidden = true;
    unlockAudio();
    if (speechConfig.enabled && lastSpeechText) {
      enqueueSpeech({
        kind: "speech_test",
        text: lastSpeechText,
        displayText: lastSpeechText,
        action: "idle",
        force: true,
      });
    }
  });

  musicPlayers.forEach((player) => {
    player.addEventListener("error", () => {
      if (audioAllowed && musicConfig.url) showSoundHint("背景音乐加载失败，请重新选择曲目");
    });
    // 队列模式下 loop=false，播完一首就回报服务端换下一首点歌。
    player.addEventListener("ended", () => {
      if (player !== musicPlayers[musicActive] || musicConfig.loop) return;
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      try {
        socket.send(JSON.stringify({ type: "track_ended" }));
      } catch {
        /* 忽略 */
      }
    });
  });

  /* ------------------------------------------------------------------ speech */

  let voiceReportTimer;

  function restoreSpeechVolume() {
    ducked = false;
    syncMusicVolume();
  }

  function releaseSpeechObjectUrl() {
    if (!speechObjectUrl) return;
    URL.revokeObjectURL(speechObjectUrl);
    speechObjectUrl = "";
  }

  async function requestSpeechAudio(text, signal) {
    let lastError = null;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const response = await fetch("/api/speech", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
          signal,
        });
        if (response.ok) return response.blob();
        const detail = (await response.text()).trim();
        const error = new Error(detail || `HTTP ${response.status}`);
        error.status = response.status;
        lastError = error;
        // 4xx 是配置/文本问题，重试没有意义；5xx 与 429 是上游抖动，值得再试一次。
        if (response.status < 500 && response.status !== 429) throw error;
      } catch (error) {
        if (error.name === "AbortError") throw error;
        lastError = error;
      }
      if (attempt === 0) await delay(700, signal);
    }
    throw lastError || new Error("Speech synthesis failed");
  }

  function prefetchSpeechAudio(item) {
    if (item.prefetchedAudio || (speechPrefetchSlot && speechPrefetchSlot !== item)) return;
    const controller = new AbortController();
    speechPrefetchSlot = item;
    item.prefetchController = controller;
    item.prefetchConfigVersion = speechConfigVersion;
    item.prefetchedAudio = requestSpeechAudio(item.text, controller.signal)
      .then((blob) => ({ blob }))
      .catch((error) => ({ error }));
  }

  function prefetchNextItem() {
    if (!audioAllowed || speechPhase !== "playing" || speechPrefetchSlot) return;
    if (speechConfig.provider === "browser" || !speechConfig.enabled) return;
    const next = speechQueue.find((entry) => !entry.cancelled);
    if (!next) return;
    prefetchSpeechAudio(next);
  }

  function stopSpeech() {
    speechVersion += 1;
    if (speechPhase === "preparing" && activeSpeechItem
        && activeSpeechItem.prefetchController) {
      activeSpeechItem.prefetchController.abort();
    }
    if (activeSpeechFinish) activeSpeechFinish();
    if (speechRequestController) {
      speechRequestController.abort();
      speechRequestController = null;
    }
    speechAudio.pause();
    speechAudio.removeAttribute("src");
    speechAudio.load();
    releaseSpeechObjectUrl();
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    activeUtterance = null;
    hideBubble();
    restoreSpeechVolume();
  }

  speechAudio.addEventListener("ended", () => {
    releaseSpeechObjectUrl();
    restoreSpeechVolume();
  });
  speechAudio.addEventListener("error", () => {
    releaseSpeechObjectUrl();
    restoreSpeechVolume();
  });

  function chineseVoices() {
    if (!("speechSynthesis" in window)) return [];
    return window.speechSynthesis.getVoices()
      .filter((voice) => voice.lang && voice.lang.toLowerCase().startsWith("zh"));
  }

  function resolveVoice() {
    if (!("speechSynthesis" in window)) return null;
    const voices = window.speechSynthesis.getVoices();
    const wanted = String(speechConfig.voice || "").trim();
    if (wanted) {
      const exact = voices.find((voice) => voice.name === wanted);
      if (exact) return exact;
      const normalized = wanted.toLowerCase().replace(/\s+/g, " ");
      const fuzzy = voices.find(
        (voice) => voice.name.toLowerCase().replace(/\s+/g, " ") === normalized,
      );
      if (fuzzy) return fuzzy;
    }
    const chinese = voices.filter((voice) => voice.lang && voice.lang.toLowerCase().startsWith("zh"));
    return chinese.find((voice) => /zh[-_]?cn/i.test(voice.lang))
      || chinese.find((voice) => /zh[-_]?tw/i.test(voice.lang))
      || chinese.find((voice) => /zh[-_]?hk/i.test(voice.lang))
      || chinese[0]
      || voices[0]
      || null;
  }

  function reportVoices() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const voices = chineseVoices().slice(0, 50).map((voice) => ({
      name: voice.name,
      lang: voice.lang || "",
      local: Boolean(voice.localService),
    }));
    const active = resolveVoice();
    socket.send(JSON.stringify({
      type: "voice_report",
      client_id: clientId,
      role: previewMode ? "preview" : "stage",
      audio: audioAllowed,
      voices,
      wanted: speechConfig.voice || "",
      active: active ? active.name : "",
      missing: Boolean(speechConfig.voice && active && active.name !== speechConfig.voice),
    }));
  }

  function scheduleVoiceReport() {
    clearTimeout(voiceReportTimer);
    voiceReportTimer = setTimeout(reportVoices, 250);
  }

  if ("speechSynthesis" in window) {
    window.speechSynthesis.addEventListener("voiceschanged", scheduleVoiceReport);
    setTimeout(scheduleVoiceReport, 800);
  }

  async function speak(text, displayText = text, action = "idle", item = null) {
    lastSpeechText = text;
    // 先把文字和动作放上屏。固定音色合成一句话常要 5 秒以上，旧逻辑要等音频
    // 真正开始播才显示气泡，合成慢或失败时观众看到的就是"什么都没发生"。
    const displayNow = () => {
      showBubble(displayText, null);
      playAction(action);
    };
    if (speechConfig.provider !== "browser") {
      stopSpeech();
      speechPhase = "preparing";
      displayNow();
      const version = speechVersion;
      const controller = new AbortController();
      speechRequestController = controller;
      ducked = true;
      syncMusicVolume();
      try {
        let prefetched = null;
        if (item && item.prefetchedAudio
            && item.prefetchConfigVersion === speechConfigVersion) {
          prefetched = await item.prefetchedAudio;
          if (prefetched.error) throw prefetched.error;
        } else {
          if (item && item.kind === "monologue") {
            if (item.prefetchController) item.prefetchController.abort();
            if (speechPrefetchSlot === item) speechPrefetchSlot = null;
            delete item.prefetchController;
            delete item.prefetchedAudio;
            prefetchSpeechAudio(item);
            prefetched = await item.prefetchedAudio;
            if (prefetched.error) throw prefetched.error;
          } else {
            prefetched = { blob: await requestSpeechAudio(text, controller.signal) };
          }
        }
        const audio = prefetched.blob;
        if (version !== speechVersion || (item && item.cancelled)) return;
        speechObjectUrl = URL.createObjectURL(audio);
        speechAudio.src = speechObjectUrl;
        const configuredVolume = Number(speechConfig.volume);
        speechAudio.volume = Number.isFinite(configuredVolume)
          ? clamp(configuredVolume, 0, 1)
          : 1;
        await new Promise((resolve, reject) => {
          let completionTimer;
          const cleanup = () => {
            clearTimeout(completionTimer);
            speechAudio.removeEventListener("playing", onPlaying);
            speechAudio.removeEventListener("ended", onEnded);
            speechAudio.removeEventListener("error", onError);
            speechAudio.removeEventListener("loadedmetadata", scheduleCompletionFallback);
            speechAudio.removeEventListener("durationchange", scheduleCompletionFallback);
            if (activeSpeechFinish === onEnded) activeSpeechFinish = null;
          };
          const onEnded = () => {
            cleanup();
            resolve();
          };
          const onError = () => {
            cleanup();
            reject(new Error("Speech audio playback failed"));
          };
          const scheduleCompletionFallback = () => {
            clearTimeout(completionTimer);
            const duration = Number(speechAudio.duration);
            const fallbackMs = Number.isFinite(duration) && duration > 0
              ? duration * 1000 + 8000
              : Math.min(120000, Math.max(15000, text.length * 550 + 10000));
            completionTimer = setTimeout(() => {
              speechAudio.pause();
              onEnded();
            }, fallbackMs);
          };
          const onPlaying = () => {
            if (speechPhase === "playing" && activeSpeechItem === item) return;
            speechPhase = "playing";
            if (speechPrefetchSlot === item) speechPrefetchSlot = null;
            scheduleCompletionFallback();
            prefetchNextItem();
          };
          activeSpeechFinish = onEnded;
          speechAudio.addEventListener("playing", onPlaying, { once: true });
          speechAudio.addEventListener("ended", onEnded, { once: true });
          speechAudio.addEventListener("error", onError, { once: true });
          speechAudio.addEventListener("loadedmetadata", scheduleCompletionFallback);
          speechAudio.addEventListener("durationchange", scheduleCompletionFallback);
          speechAudio.play().then(() => {
            onPlaying();
            soundHint.hidden = true;
          }).catch((error) => {
            cleanup();
            reject(error);
          });
        });
        // stopSpeech() 会通过 activeSpeechFinish 结束这里的等待；这时新的语音
        // 已经接管画面和音量，不能再收气泡、抬音量。
        if (version !== speechVersion) return;
        soundHint.hidden = true;
        // 音频结束再留一小会儿，让最后一句读完。
        scheduleBubbleHide(700);
        restoreSpeechVolume();
      } catch (error) {
        if (error.name === "AbortError" || version !== speechVersion
            || (item && item.cancelled)) return;
        speechPhase = "failed";
        restoreSpeechVolume();
        // 合成失败也保留文字，至少观众能看到这句回复。
        scheduleBubbleHide(Math.min(9000, Math.max(3500, displayText.length * 160)));
        showSoundHint(error.name === "NotAllowedError"
          ? "点击开启固定音色语音"
          : error.message.includes("MP3 metadata without audio frames")
            ? "TTS 服务未生成音频，请检查本地语音模型服务"
            : error.message.includes("timed out")
              ? "语音合成超时（本地 TTS 太慢），这句先跳过；可调大 SPEECH_TIMEOUT"
              : "固定语音合成失败，请检查 TTS 服务配置");
        console.error("[stage] Speech API synthesis failed", error.message);
      } finally {
        if (speechRequestController === controller) speechRequestController = null;
        if (speechPrefetchSlot === item) speechPrefetchSlot = null;
        // 只有没被新的语音接管时才恢复背景音乐音量，避免把正在播报的那句盖掉。
        if (version === speechVersion) restoreSpeechVolume();
      }
      return;
    }
    if (!("speechSynthesis" in window)) {
      stopSpeech();
      speechPhase = "displaying";
      playAction(action);
      const visibleFor = showBubble(displayText);
      await new Promise((resolve) => setTimeout(resolve, visibleFor));
      return;
    }
    stopSpeech();
    speechPhase = "preparing";
    displayNow();
    const version = speechVersion;
    // Chromium 里 cancel() 与 speak() 紧跟在同一轮调用会把第一句吞掉，
    // 先 cancel 让出来，等一小段时间再开口。
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
      await delay(120, null);
    }
    const utterance = new SpeechSynthesisUtterance(text);
    const voice = resolveVoice();
    utterance.lang = voice ? voice.lang : "zh-CN";
    utterance.rate = Number(speechConfig.rate) || 1;
    utterance.pitch = Number(speechConfig.pitch) || 1;
    utterance.volume = Number.isFinite(Number(speechConfig.volume))
      ? Number(speechConfig.volume)
      : 1;
    if (voice) utterance.voice = voice;
    utterance.onstart = () => {
      speechPhase = "playing";
      ducked = true;
      syncMusicVolume();
      soundHint.hidden = true;
    };
    let speechError = "";
    await new Promise((resolve) => {
      utterance.onend = () => resolve();
      utterance.onerror = (event) => {
        speechError = event.error || "speech synthesis failed";
        if (event.error === "not-allowed") {
          showSoundHint("浏览器拦截了语音，点击此处开启");
        }
        resolve();
      };
      const synthesis = window.speechSynthesis;
      synthesis.resume();
      activeUtterance = utterance;
      synthesis.speak(utterance);
    });
    if (version !== speechVersion) return;
    activeUtterance = null;
    restoreSpeechVolume();
    if (speechError) {
      console.error("[stage] Browser speech synthesis failed", speechError);
      scheduleBubbleHide(Math.min(9000, Math.max(3500, displayText.length * 160)));
      return;
    }
    scheduleBubbleHide(700);
  }

  function finishMonologue(item) {
    if (previewMode || speechOwnerId !== clientId || !item.eventId) return;
    pendingMonologueCompletions.set(item.eventId, 0);
    flushMonologueCompletions();
  }

  function scheduleMonologueCompletionRetry() {
    clearTimeout(monologueCompletionTimer);
    if (pendingMonologueCompletions.size) {
      monologueCompletionTimer = setTimeout(flushMonologueCompletions, 1500);
    }
  }

  function flushMonologueCompletions() {
    if (previewMode || speechOwnerId !== clientId
        || !socket || socket.readyState !== WebSocket.OPEN) return;
    const now = Date.now();
    for (const [eventId, lastSentAt] of pendingMonologueCompletions) {
      if (now - lastSentAt < 1500) continue;
      try {
        socket.send(JSON.stringify({
          type: "monologue_finished",
          event_id: eventId,
          client_id: clientId,
        }));
        pendingMonologueCompletions.set(eventId, now);
      } catch (error) {
        console.warn("[stage] Could not report monologue playback completion", error);
      }
    }
    scheduleMonologueCompletionRetry();
  }

  async function drainSpeechQueue() {
    if (speechQueueRunning) return;
    speechQueueRunning = true;
    try {
      while (speechQueue.length) {
        const item = speechQueue.shift();
        activeSpeechItem = item;
        try {
          if (audioAllowed && (speechConfig.enabled || item.force)) {
            await speak(item.text, item.displayText, item.action, item);
          } else {
            playAction(item.action);
            const visibleFor = showBubble(item.displayText);
            await new Promise((resolve) => setTimeout(resolve, visibleFor));
          }
        } catch (error) {
          console.error("[stage] Reply playback failed", error);
        } finally {
          if (item.kind === "monologue") finishMonologue(item);
          if (speechPrefetchSlot === item) speechPrefetchSlot = null;
          if (activeSpeechItem === item) activeSpeechItem = null;
          speechPhase = "";
        }
      }
    } finally {
      speechQueueRunning = false;
    }
  }

  function enqueueSpeech(item) {
    const priority = { speech_test: -1, gift: 0, follow: 1, danmaku: 10, monologue: 100 };
    const entry = { ...item, sequence: ++speechSequence };
    speechQueue.push(entry);
    speechQueue.sort((left, right) => (
      (priority[left.kind] ?? 50) - (priority[right.kind] ?? 50)
      || left.sequence - right.sequence
    ));
    prefetchNextItem();
    void drainSpeechQueue();
  }

  function discardQueuedMonologues() {
    for (const item of speechQueue) {
      if (item.kind === "monologue") {
        item.cancelled = true;
        if (item.prefetchController) item.prefetchController.abort();
        if (speechPrefetchSlot === item) speechPrefetchSlot = null;
      }
    }
    speechQueue = speechQueue.filter((item) => item.kind !== "monologue");
    if (activeSpeechItem && activeSpeechItem.kind === "monologue"
        && speechPhase === "preparing") {
      activeSpeechItem.cancelled = true;
      if (activeSpeechItem.prefetchController) activeSpeechItem.prefetchController.abort();
      if (speechPrefetchSlot === activeSpeechItem) speechPrefetchSlot = null;
      stopSpeech();
    }
  }

  function updateSpeechOwner(nextOwnerId) {
    const wasOwner = speechOwnerId === clientId;
    speechOwnerId = typeof nextOwnerId === "string" ? nextOwnerId : "";
    if (!wasOwner || speechOwnerId === clientId) return;
    clearTimeout(monologueCompletionTimer);
    pendingMonologueCompletions.clear();
    discardQueuedMonologues();
    for (const item of speechQueue) {
      if (item.prefetchController) item.prefetchController.abort();
    }
    speechPrefetchSlot = null;
    speechQueue = [];
    if (activeSpeechItem) {
      activeSpeechItem.cancelled = true;
      stopSpeech();
    }
  }

  /* ------------------------------------------------------------------ socket */

  function speechSignature(config) {
    return [config.provider, config.voice, config.rate].join("|");
  }

  function applyConfig(packet) {
    if (packet.speech) {
      const previousSignature = speechSignature(speechConfig);
      speechConfig = { ...speechConfig, ...packet.speech };
      // 只有影响合成结果（音色/语速/引擎）的改动才作废预合成音频；
      // 否则调一次音量就会把已经合成好的下一句丢掉重做。
      if (speechSignature(speechConfig) !== previousSignature) {
        speechConfigVersion += 1;
        for (const item of speechQueue) {
          if (item.prefetchController) {
            item.prefetchController.abort();
            if (speechPrefetchSlot === item) speechPrefetchSlot = null;
            delete item.prefetchController;
            delete item.prefetchedAudio;
          }
        }
      }
      if (!speechConfig.enabled) {
        stopSpeech();
      } else {
        prefetchNextItem();
      }
      scheduleVoiceReport();
    }
    if (packet.stage) applyStageConfig(packet.stage);
    if (packet.music) applyMusic(packet.music);
    if (packet.actions) setActionCatalog(packet.actions);
    if (packet.pet) applyPetState(packet.pet);
  }

  function bufferReply(packet) {
    pendingReplies.push(packet);
    clearTimeout(pendingReplyTimer);
    pendingReplyTimer = setTimeout(() => {
      pendingReplies = [];
    }, 2500);
  }

  function flushPendingReplies() {
    clearTimeout(pendingReplyTimer);
    const buffered = pendingReplies;
    pendingReplies = [];
    for (const packet of buffered) playReply(packet);
  }

  function playReply(packet) {
    if (speechOwnerId !== clientId) return;
    if (asleep && packet.kind === "monologue") {
      // 睡着时不播自言自语：既不该出声，也不该被自己的话"叫醒"。
      // 仍然回执，服务端不会卡在等待播放确认上。
      if (packet.event_id) finishMonologue({ eventId: packet.event_id });
      return;
    }
    const name = packet.user && packet.user.name ? packet.user.name : "朋友";
    const displayText = ["monologue", "crowd"].includes(packet.kind)
      ? packet.text
      : `${name}，${packet.text}`;
    enqueueSpeech({
      kind: packet.kind || "danmaku",
      eventId: packet.event_id || "",
      text: packet.text,
      displayText,
      action: packet.action,
    });
  }

  function handlePacket(packet) {
    if (!packet || typeof packet !== "object") return;
    if (packet.type === "speech_owner") {
      speechOwnerKnown = true;
      updateSpeechOwner(packet.client_id);
      if (speechOwnerId === clientId) {
        flushPendingReplies();
      } else {
        clearTimeout(pendingReplyTimer);
        pendingReplies = [];
      }
    }
    if (packet.type === "monologue_finished_ack" && packet.event_id) {
      pendingMonologueCompletions.delete(packet.event_id);
    }
    if (packet.type === "event") {
      noteActivity();
      react(packet.event);
    }
    if (packet.type === "reply") {
      if (packet.kind !== "monologue") noteActivity();
      // 只有独立回复（弹幕/礼物等）才丢掉还没开始的旧闲聊；闲聊连播是一句接
      // 一句排好的，清掉排队那一条就等于下一句要从头合成，又变成一段沉默。
      if (packet.kind !== "monologue") discardQueuedMonologues();
      if (speechOwnerKnown) playReply(packet);
      else bufferReply(packet);
    }
    if (packet.type === "action") {
      playAction(packet.action);
      noteActivity();
    }
    if (packet.type === "pet_state") applyPetState(packet);
    if (packet.type === "motion") {
      noteActivity();
      playMotion(packet);
    }
    if (packet.type === "speech_test" && speechOwnerId === clientId) {
      enqueueSpeech({
        kind: "speech_test",
        text: "语音测试，欢迎来到直播间。",
        displayText: "语音测试，欢迎来到直播间。",
        action: "idle",
        force: true,
      });
    }
    if (packet.type === "stage_config") applyConfig(packet);
    if (packet.type === "stage_layout" && packet.origin !== clientId) {
      const next = {};
      for (const key of ["pet_x", "pet_y", "pet_scale"]) {
        if (Number.isFinite(Number(packet[key]))) next[key] = Number(packet[key]);
      }
      if (Object.keys(next).length) applyStageConfig(next);
    }
    if (packet.type === "history") {
      feedList.replaceChildren();
      feedItems = 0;
      feedCount.textContent = "0";
      feed.classList.remove("has-items");
      packet.items.forEach(addFeed);
    }
    if (packet.type === "command_error") console.warn("[stage]", packet.message);
  }

  function connect() {
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${scheme}//${location.host}/ws`);
    socket.addEventListener("open", () => {
      connection.textContent = "互动已连接";
      liveDot.classList.remove("offline");
      reconnectDelay = 900;
      // 立刻上报，缩短"还不知道谁是语音舞台"的空窗；之前这里是 250ms 防抖，
      // 这段时间里到达的 reply 会被直接丢掉。
      reportVoices();
      flushMonologueCompletions();
    });
    socket.addEventListener("close", () => {
      connection.textContent = "正在重连";
      liveDot.classList.add("offline");
      speechOwnerKnown = false;
      clearTimeout(pendingReplyTimer);
      pendingReplies = [];
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 1.7, 15000);
    });
    socket.addEventListener("error", () => socket.close());
    socket.addEventListener("message", (message) => {
      let packet;
      try {
        packet = JSON.parse(message.data);
      } catch {
        return;
      }
      handlePacket(packet);
    });
  }

  /* -------------------------------------------------------------------- drag */

  petWrap.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const stageRect = stageEl.getBoundingClientRect();
    drag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      petX: stageConfig.pet_x,
      petY: stageConfig.pet_y,
      stageRect,
      moved: false,
    };
    try {
      petWrap.setPointerCapture(event.pointerId);
    } catch {
      /* ignore */
    }
    petWrap.classList.add("dragging");
    stageEl.classList.add("dragging");
    event.preventDefault();
  });

  petWrap.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (!drag.moved) {
      if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 4) return;
      drag.moved = true;
    }
    const dx = ((event.clientX - drag.startX) / drag.stageRect.width) * 100;
    const dy = ((event.clientY - drag.startY) / drag.stageRect.height) * 100;
    setPetPosition(clamp(drag.petX + dx, 2, 98), clamp(drag.petY + dy, 4, 98));
  });

  function endDrag(event) {
    if (!drag || (event && event.pointerId !== drag.pointerId)) return;
    const moved = drag.moved;
    try {
      petWrap.releasePointerCapture(drag.pointerId);
    } catch {
      /* ignore */
    }
    petWrap.classList.remove("dragging");
    stageEl.classList.remove("dragging");
    drag = null;
    if (moved) {
      sendLayout({
        pet_x: round(stageConfig.pet_x),
        pet_y: round(stageConfig.pet_y),
      });
    }
  }
  petWrap.addEventListener("pointerup", endDrag);
  petWrap.addEventListener("pointercancel", endDrag);

  petWrap.addEventListener("wheel", (event) => {
    event.preventDefault();
    const next = round(clamp(Number(stageConfig.pet_scale) + (event.deltaY > 0 ? -0.05 : 0.05), 0.5, 3));
    applyStageConfig({ pet_scale: next });
    sendLayout({ pet_scale: next }, false);
  }, { passive: false });

  window.addEventListener("resize", () => requestAnimationFrame(positionBubble));

  /* -------------------------------------------------------------------- init */

  async function init() {
    await loadManifest();
    pet.src = assetUrl("idle");
    if (!audioAllowed) soundHint.hidden = true;
    applyStageConfig(stageConfig);
    connect();
    setTimeout(() => showBubble("嗨，欢迎来到直播间！", 4200), 700);
  }

  init();
})();
