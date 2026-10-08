/**
 * J.A.R.V.I.S. MARK VII - Universal Web HUD & In-Browser Cognitive Client
 * Stark Industries Autonomous Defense & Cognitive Assistant Interface
 *
 * Supported Operating Modes:
 * 1. LOCAL BRIDGE: Connects via WebSocket (ws://127.0.0.1:8000/ws) to local Python agent.
 * 2. STANDALONE CLOUD: Runs 100% in-browser on GitHub Pages using direct Groq API + Web Speech.
 */

(function () {
  "use strict";

  // -------------------------------------------------------------------------
  // 1. Session Token Extraction & Storage
  // -------------------------------------------------------------------------
  let sessionToken = "";
  if (window.location.hash) {
    const rawHash = window.location.hash.startsWith("#")
      ? window.location.hash.substring(1)
      : window.location.hash;
    const hashParams = new URLSearchParams(rawHash);
    sessionToken = hashParams.get("token") || "";
    if (sessionToken) {
      try {
        sessionStorage.setItem("jarvis_session_token", sessionToken);
      } catch (e) {}
      if (window.history && window.history.replaceState) {
        window.history.replaceState(null, document.title, window.location.pathname + window.location.search);
      }
    }
  }
  if (!sessionToken) {
    try {
      sessionToken = sessionStorage.getItem("jarvis_session_token") || "";
    } catch (e) {
      sessionToken = "";
    }
  }

  // Detect widget mode from URL hash
  if (window.location.hash && window.location.hash.includes("mode=widget")) {
    document.body.classList.add("widget-mode");
  }

  // -------------------------------------------------------------------------
  // 2. DOM Elements
  // -------------------------------------------------------------------------
  const statePill = document.getElementById("agent-state-pill");
  const stateText = document.getElementById("agent-state-text");
  const killBtn = document.getElementById("kill-btn");
  const sfxToggleBtn = document.getElementById("sfx-toggle-btn");
  const configBtn = document.getElementById("config-btn");
  const userTitleEl = document.getElementById("hud-user-title");
  const transcriptContainer = document.getElementById("transcript-container");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const voiceInputBtn = document.getElementById("voice-input-btn");
  const screenVisionBtn = document.getElementById("screen-vision-btn");

  // Mode Elements
  const hudModePill = document.getElementById("hud-mode-pill");
  const hudModeText = document.getElementById("hud-mode-text");

  // Telemetry Gauges
  const cpuStat = document.getElementById("cpu-stat");
  const cpuCircle = document.getElementById("cpu-circle");
  const cpuFill = document.getElementById("cpu-fill");

  const ramStat = document.getElementById("ram-stat");
  const ramCircle = document.getElementById("ram-circle");
  const ramFill = document.getElementById("ram-fill");

  const diskStat = document.getElementById("disk-stat");
  const diskCircle = document.getElementById("disk-circle");
  const diskFill = document.getElementById("disk-fill");

  const auditFeed = document.getElementById("audit-feed");
  const coreOutputVal = document.getElementById("core-output-val");

  // Confirmation Modal elements
  const confirmModal = document.getElementById("confirmation-modal");
  const confirmActionText = document.getElementById("confirm-action-text");
  const confirmWarningBanner = document.getElementById("confirm-warning-banner");
  const confirmWarningText = document.getElementById("confirm-warning-text");
  const confirmCountdown = document.getElementById("confirm-countdown");
  const modalApproveBtn = document.getElementById("modal-approve-btn");
  const modalDenyBtn = document.getElementById("modal-deny-btn");

  // Settings Modal elements
  const settingsModal = document.getElementById("settings-modal");
  const settingsCloseX = document.getElementById("settings-close-x");
  const settingsCancelBtn = document.getElementById("settings-cancel-btn");
  const settingsSaveBtn = document.getElementById("settings-save-btn");
  const cfgGroqKey = document.getElementById("cfg-groq-key");
  const cfgGroqModel = document.getElementById("cfg-groq-model");
  const cfgUserTitle = document.getElementById("cfg-user-title");

  // App State Variables
  let socket = null;
  let activeConfirmId = null;
  let confirmTimerInterval = null;
  let currentState = "idle";
  let userTitle = "Sir";
  let activeMode = "searching"; // 'local' | 'cloud' | 'searching'
  let isSpeakingAudio = false;
  let isListeningRecognition = false;
  let activeRecognition = null;
  let conversationHistory = [];

  // SVG Gauge Circumference for r=38: 2 * PI * 38 = 238.761
  const GAUGE_CIRCUMFERENCE = 238.76;

  // -------------------------------------------------------------------------
  // 3. User Preferences & Storage
  // -------------------------------------------------------------------------
  function getStoredModePreference() {
    return localStorage.getItem("jarvis_conn_mode") || "auto";
  }

  function getStoredGroqKey() {
    return localStorage.getItem("jarvis_groq_key") || "";
  }

  function getStoredGroqModel() {
    return localStorage.getItem("jarvis_groq_model") || "openai/gpt-oss-120b";
  }

  function getStoredUserTitle() {
    return localStorage.getItem("jarvis_user_title") || "Sir";
  }

  userTitle = getStoredUserTitle();
  if (userTitleEl) userTitleEl.textContent = userTitle.toUpperCase();

  // -------------------------------------------------------------------------
  // 4. Procedural Web Audio API Sound Synthesizer
  // -------------------------------------------------------------------------
  let audioCtx = null;
  let sfxEnabled = true;

  try {
    const savedSfx = localStorage.getItem("jarvis_sfx_enabled");
    if (savedSfx !== null) sfxEnabled = savedSfx === "true";
  } catch (e) {
    sfxEnabled = true;
  }

  function updateSfxButtonUI() {
    if (!sfxToggleBtn) return;
    if (sfxEnabled) {
      sfxToggleBtn.classList.remove("muted");
      sfxToggleBtn.innerHTML = '<span class="sfx-icon">&#128266;</span> <span class="sfx-label">SFX: ON</span>';
    } else {
      sfxToggleBtn.classList.add("muted");
      sfxToggleBtn.innerHTML = '<span class="sfx-icon">&#128263;</span> <span class="sfx-label">SFX: MUTED</span>';
    }
  }
  updateSfxButtonUI();

  if (sfxToggleBtn) {
    sfxToggleBtn.addEventListener("click", () => {
      sfxEnabled = !sfxEnabled;
      try {
        localStorage.setItem("jarvis_sfx_enabled", String(sfxEnabled));
      } catch (e) {}
      updateSfxButtonUI();
      if (sfxEnabled) {
        initAudio();
        playBlipSound(1200);
      }
    });
  }

  function initAudio() {
    if (!audioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) audioCtx = new AudioContextClass();
    }
    if (audioCtx && audioCtx.state === "suspended") {
      audioCtx.resume();
    }
  }

  ["click", "keydown", "touchstart"].forEach((evt) => {
    window.addEventListener(evt, () => initAudio(), { once: true });
  });

  function playBlipSound(freq = 1200) {
    if (!sfxEnabled || !audioCtx) return;
    try {
      const now = audioCtx.currentTime;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(freq, now);
      osc.frequency.exponentialRampToValueAtTime(freq * 0.5, now + 0.04);
      gain.gain.setValueAtTime(0.08, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.04);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start(now);
      osc.stop(now + 0.04);
    } catch (e) {}
  }

  function playActionSound() {
    if (!sfxEnabled || !audioCtx) return;
    try {
      const now = audioCtx.currentTime;
      const osc1 = audioCtx.createOscillator();
      const osc2 = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc1.type = "triangle";
      osc1.frequency.setValueAtTime(520, now);
      osc1.frequency.exponentialRampToValueAtTime(880, now + 0.08);
      osc2.type = "sine";
      osc2.frequency.setValueAtTime(1040, now);
      osc2.frequency.exponentialRampToValueAtTime(1760, now + 0.08);
      gain.gain.setValueAtTime(0.1, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.1);
      osc1.connect(gain);
      osc2.connect(gain);
      gain.connect(audioCtx.destination);
      osc1.start(now);
      osc2.start(now);
      osc1.stop(now + 0.1);
      osc2.stop(now + 0.1);
    } catch (e) {}
  }

  function playAlertSound() {
    if (!sfxEnabled || !audioCtx) return;
    try {
      const now = audioCtx.currentTime;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "square";
      osc.frequency.setValueAtTime(650, now);
      osc.frequency.setValueAtTime(950, now + 0.07);
      osc.frequency.setValueAtTime(650, now + 0.14);
      osc.frequency.setValueAtTime(950, now + 0.21);
      gain.gain.setValueAtTime(0.12, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.3);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start(now);
      osc.stop(now + 0.3);
    } catch (e) {}
  }

  function playKillSound() {
    if (!sfxEnabled || !audioCtx) return;
    try {
      const now = audioCtx.currentTime;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "sawtooth";
      osc.frequency.setValueAtTime(800, now);
      osc.frequency.exponentialRampToValueAtTime(60, now + 0.5);
      gain.gain.setValueAtTime(0.18, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.55);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start(now);
      osc.stop(now + 0.55);
    } catch (e) {}
  }

  // -------------------------------------------------------------------------
  // 5. Visualizer Animations: Arc Reactor, Ambient Wave & Soundwave
  // -------------------------------------------------------------------------
  const reactorCanvas = document.getElementById("arc-reactor-canvas");
  const reactorCtx = reactorCanvas ? reactorCanvas.getContext("2d") : null;

  const ambientCanvas = document.getElementById("ambient-canvas");
  const ambientCtx = ambientCanvas ? ambientCanvas.getContext("2d") : null;

  const soundwaveCanvas = document.getElementById("soundwave-canvas");
  const soundwaveCtx = soundwaveCanvas ? soundwaveCanvas.getContext("2d") : null;

  let isTabVisible = !document.hidden;
  let animFrameId = null;
  let lastFrameTime = 0;
  const targetFPS = 30;
  const frameInterval = 1000 / targetFPS;

  let reactorAngleOuter = 0;
  let reactorAngleInner = 0;
  let pulsePhase = 0;
  let ambientWavePhase = 0;
  let soundwavePhase = 0;

  function renderVisualizers(timestamp) {
    if (!isTabVisible) {
      animFrameId = null;
      return;
    }

    if (timestamp - lastFrameTime >= frameInterval) {
      lastFrameTime = timestamp;

      if (reactorCtx && reactorCanvas) renderArcReactor();
      if (ambientCtx && ambientCanvas) renderAmbientWave();
      if (soundwaveCtx && soundwaveCanvas) renderSoundwave();
    }

    animFrameId = requestAnimationFrame(renderVisualizers);
  }

  function renderArcReactor() {
    const w = reactorCanvas.width;
    const h = reactorCanvas.height;
    const cx = w / 2;
    const cy = h / 2;
    const ctx = reactorCtx;

    let rotationSpeedOuter = 0.02;
    let rotationSpeedInner = -0.035;
    let pulseSpeed = 0.05;
    let primaryColor = "rgba(0, 240, 255, 0.9)";
    let glowColor = "rgba(0, 240, 255, 0.4)";
    let coreColor = "#ffffff";

    if (currentState === "thinking") {
      rotationSpeedOuter = 0.07;
      rotationSpeedInner = -0.09;
      pulseSpeed = 0.12;
      primaryColor = "rgba(255, 183, 0, 0.95)";
      glowColor = "rgba(255, 183, 0, 0.5)";
      coreColor = "#fff8e0";
    } else if (currentState === "acting") {
      rotationSpeedOuter = 0.09;
      rotationSpeedInner = -0.12;
      pulseSpeed = 0.16;
      primaryColor = "rgba(0, 255, 136, 0.95)";
      glowColor = "rgba(0, 255, 136, 0.5)";
      coreColor = "#e6fff2";
    } else if (currentState === "listening") {
      rotationSpeedOuter = 0.04;
      rotationSpeedInner = -0.05;
      pulseSpeed = 0.09;
      primaryColor = "rgba(0, 240, 255, 1.0)";
      glowColor = "rgba(0, 240, 255, 0.6)";
      coreColor = "#ffffff";
    } else if (currentState === "halted") {
      rotationSpeedOuter = 0.003;
      rotationSpeedInner = -0.003;
      pulseSpeed = 0.03;
      primaryColor = "rgba(255, 42, 75, 0.85)";
      glowColor = "rgba(255, 42, 75, 0.35)";
      coreColor = "#ff2a4b";
    }

    reactorAngleOuter += rotationSpeedOuter;
    reactorAngleInner += rotationSpeedInner;
    pulsePhase += pulseSpeed;

    const pulseScale = 1 + Math.sin(pulsePhase) * 0.06;

    ctx.clearRect(0, 0, w, h);

    // 1. Outermost Ring
    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, 98, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(0, 240, 255, 0.15)";
    ctx.lineWidth = 1;
    ctx.stroke();

    // 2. Notched Outer Compass Ring
    ctx.translate(cx, cy);
    ctx.rotate(reactorAngleOuter);
    ctx.beginPath();
    ctx.arc(0, 0, 88, 0, Math.PI * 2);
    ctx.strokeStyle = primaryColor;
    ctx.lineWidth = 2;
    ctx.stroke();

    for (let i = 0; i < 24; i++) {
      const angle = (i * Math.PI) / 12;
      const isMajor = i % 4 === 0;
      const r1 = isMajor ? 82 : 85;
      const r2 = 91;
      ctx.beginPath();
      ctx.moveTo(Math.cos(angle) * r1, Math.sin(angle) * r1);
      ctx.lineTo(Math.cos(angle) * r2, Math.sin(angle) * r2);
      ctx.strokeStyle = isMajor ? primaryColor : "rgba(0, 240, 255, 0.35)";
      ctx.lineWidth = isMajor ? 2 : 1;
      ctx.stroke();
    }
    ctx.restore();

    // 3. 10 Electromagnetic Repulsor Coils
    const coilCount = 10;
    const coilRadius = 74;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(reactorAngleOuter * 0.5);

    for (let i = 0; i < coilCount; i++) {
      const angle = (i * 2 * Math.PI) / coilCount;
      const bx = Math.cos(angle) * coilRadius;
      const by = Math.sin(angle) * coilRadius;

      ctx.save();
      ctx.translate(bx, by);
      ctx.rotate(angle + Math.PI / 2);

      ctx.fillStyle = "rgba(10, 24, 48, 0.85)";
      ctx.fillRect(-10, -5, 20, 10);
      ctx.strokeStyle = primaryColor;
      ctx.lineWidth = 1.5;
      ctx.strokeRect(-10, -5, 20, 10);

      ctx.fillStyle = "rgba(255, 183, 0, 0.75)";
      ctx.fillRect(-6, -4, 3, 8);
      ctx.fillRect(3, -4, 3, 8);

      ctx.restore();
    }
    ctx.restore();

    // 4. Inner Gyro Dash Ring
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(reactorAngleInner);
    ctx.beginPath();
    ctx.arc(0, 0, 52, 0, Math.PI * 2);
    ctx.setLineDash([12, 8]);
    ctx.strokeStyle = primaryColor;
    ctx.lineWidth = 2.5;
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();

    // 5. Glowing Core
    const coreRadius = 28 * pulseScale;
    const radGrad = ctx.createRadialGradient(cx, cy, 4, cx, cy, coreRadius * 1.5);
    radGrad.addColorStop(0, coreColor);
    radGrad.addColorStop(0.3, primaryColor);
    radGrad.addColorStop(0.7, glowColor);
    radGrad.addColorStop(1, "transparent");

    ctx.beginPath();
    ctx.arc(cx, cy, coreRadius * 1.5, 0, Math.PI * 2);
    ctx.fillStyle = radGrad;
    ctx.fill();

    // 6. Tri-Core Inscription
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(-reactorAngleOuter * 0.75);
    ctx.beginPath();
    for (let i = 0; i < 3; i++) {
      const triAngle = (i * 2 * Math.PI) / 3 - Math.PI / 2;
      const tx = Math.cos(triAngle) * 16;
      const ty = Math.sin(triAngle) * 16;
      if (i === 0) ctx.moveTo(tx, ty);
      else ctx.lineTo(tx, ty);
    }
    ctx.closePath();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.restore();
  }

  function renderAmbientWave() {
    const w = ambientCanvas.width;
    const h = ambientCanvas.height;
    const midY = h / 2;
    const ctx = ambientCtx;

    ctx.clearRect(0, 0, w, h);
    ctx.beginPath();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "rgba(0, 240, 255, 0.6)";

    const amp = currentState === "thinking" ? 8 : (currentState === "listening" ? 10 : 4);
    const freq = currentState === "thinking" ? 0.08 : 0.04;

    for (let x = 0; x < w; x += 3) {
      const y = midY + Math.sin(x * freq + ambientWavePhase) * amp;
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ambientWavePhase += 0.06;
  }

  function renderSoundwave() {
    if (!soundwaveCtx || !soundwaveCanvas) return;
    const w = soundwaveCanvas.width;
    const h = soundwaveCanvas.height;
    const midY = h / 2;

    soundwaveCtx.clearRect(0, 0, w, h);
    soundwaveCtx.beginPath();
    soundwaveCtx.lineWidth = 1.5;
    soundwaveCtx.strokeStyle = isSpeakingAudio ? "rgba(0, 240, 255, 0.9)" : "rgba(0, 240, 255, 0.25)";

    const amp = isSpeakingAudio ? 11 : (currentState === "thinking" ? 7 : (isListeningRecognition ? 9 : 2));
    const freq = isSpeakingAudio ? 0.08 : 0.04;

    for (let x = 0; x < w; x += 2) {
      const y = midY + Math.sin(x * freq + soundwavePhase) * amp * Math.sin((x / w) * Math.PI);
      if (x === 0) soundwaveCtx.moveTo(x, y);
      else soundwaveCtx.lineTo(x, y);
    }
    soundwaveCtx.stroke();
    soundwavePhase += isSpeakingAudio ? 0.16 : 0.05;
  }

  document.addEventListener("visibilitychange", () => {
    isTabVisible = !document.hidden;
    if (isTabVisible) {
      if (!animFrameId) {
        lastFrameTime = performance.now();
        animFrameId = requestAnimationFrame(renderVisualizers);
      }
    } else if (animFrameId) {
      cancelAnimationFrame(animFrameId);
      animFrameId = null;
    }
  });

  animFrameId = requestAnimationFrame(renderVisualizers);

  // -------------------------------------------------------------------------
  // 6. Dual-Mode Connection Manager (Local WebSocket vs Standalone Cloud)
  // -------------------------------------------------------------------------
  function updateModeUI(mode) {
    activeMode = mode;
    if (!hudModePill || !hudModeText) return;

    hudModePill.className = `status-pill mode ${mode}`;
    if (mode === "local") {
      hudModeText.textContent = "LOCAL BRIDGE";
      hudModePill.title = "Connected to Local Jarvis Desktop Daemon (Real System & Screen Control Active)";
    } else if (mode === "cloud") {
      hudModeText.textContent = "STANDALONE AI";
      hudModePill.title = "Operating in In-Browser Standalone Cloud Mode via Groq";
    } else {
      hudModeText.textContent = "CONNECTING...";
      hudModePill.title = "Searching for local Jarvis daemon or cloud neural core...";
    }
  }

  function initConnection() {
    const pref = getStoredModePreference();
    if (pref === "standalone") {
      updateModeUI("cloud");
      appendChatMessage("system", "Operating in Standalone Cloud Mode. Neural core connected directly to browser.");
      return;
    }

    // Attempt local WebSocket
    updateModeUI("searching");
    connectWebSocket();
  }

  function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    // If running on local server, use window.location.host; if hosted on GitHub Pages, attempt localhost:8000
    const host = (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1")
      ? window.location.host
      : "127.0.0.1:8000";

    const wsUrl = `${protocol}//${host}/ws`;

    try {
      socket = new WebSocket(wsUrl);
    } catch (e) {
      fallbackToCloudMode();
      return;
    }

    let authTimeout = setTimeout(() => {
      if (activeMode !== "local") {
        console.log("[HUD] Local daemon unreachable within timeout. Switching to Standalone Cloud Mode.");
        fallbackToCloudMode();
      }
    }, 2500);

    socket.onopen = () => {
      clearTimeout(authTimeout);
      console.log("[HUD] Connected to local desktop daemon. Authenticating...");
      socket.send(JSON.stringify({ type: "auth", token: sessionToken }));
      updateModeUI("local");
      updateState("idle");
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleServerMessage(data);
      } catch (err) {
        console.error("[HUD] Error parsing incoming message:", err);
      }
    };

    socket.onclose = (event) => {
      clearTimeout(authTimeout);
      console.warn("[HUD] WebSocket closed:", event.code, event.reason);

      if (activeMode === "local") {
        updateState("offline");
        // Reconnect attempt for local mode
        setTimeout(connectWebSocket, 3000);
      } else {
        fallbackToCloudMode();
      }
    };

    socket.onerror = (err) => {
      clearTimeout(authTimeout);
      fallbackToCloudMode();
    };
  }

  function fallbackToCloudMode() {
    if (socket) {
      try { socket.close(); } catch (e) {}
      socket = null;
    }
    updateModeUI("cloud");
    if (currentState === "offline" || currentState === "halted") {
      updateState("idle");
    }
  }

  function sendWSMessage(msg) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(msg));
      return true;
    }
    return false;
  }

  // -------------------------------------------------------------------------
  // 7. Standalone In-Browser Cognitive Engine (Direct Groq API Call)
  // -------------------------------------------------------------------------
  async function executeStandaloneTurn(userText) {
    const apiKey = getStoredGroqKey();
    if (!apiKey) {
      appendChatMessage("assistant", "Neural core is offline. Please enter your Groq API key in the CONFIG menu to activate J.A.R.V.I.S., Sir.");
      openSettingsModal();
      return;
    }

    updateState("thinking");
    playActionSound();

    const systemPrompt = `You are J.A.R.V.I.S. (Just A Rather Very Intelligent System), the sophisticated British AI companion created by Tony Stark.
Address the user respectfully as '${userTitle}'. Speak with dry wit, calm technical brilliance, and authentic Marvel Iron Man cadence. Keep responses concise, articulate, and conversational for audio speech synthesis.`;

    conversationHistory.push({ role: "user", content: userText });
    if (conversationHistory.length > 10) {
      conversationHistory = conversationHistory.slice(-10);
    }

    const model = getStoredGroqModel();

    try {
      const res = await fetch("https://api.groq.com/openai/v1/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          model: model,
          messages: [
            { role: "system", content: systemPrompt },
            ...conversationHistory,
          ],
          max_tokens: 1024,
          temperature: 0.7,
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error?.message || `HTTP ${res.status}: ${res.statusText}`);
      }

      const data = await res.json();
      const reply = data.choices?.[0]?.message?.content || "All systems nominal, Sir.";
      conversationHistory.push({ role: "assistant", content: reply });

      updateState("idle");
      appendChatMessage("assistant", reply);
      speakBrowserTTS(reply);

    } catch (err) {
      updateState("idle");
      appendChatMessage("assistant", `Neural communications disruption, Sir: ${err.message}`);
    }
  }

  // -------------------------------------------------------------------------
  // 8. In-Browser Speech Synthesis & Voice Recognition
  // -------------------------------------------------------------------------
  function speakBrowserTTS(text) {
    if (!window.speechSynthesis) return;
    window.speechSynthesis.cancel();

    const cleanText = text.replace(/[*#_`]/g, "").replace(/https?:\/\/\S+/g, "link").trim();
    const utterance = new SpeechSynthesisUtterance(cleanText);

    // Look for British English voice for authentic Paul Bettany cadence
    const voices = window.speechSynthesis.getVoices();
    const britishVoice = voices.find(
      (v) => v.lang.includes("en-GB") || v.name.includes("UK") || v.name.includes("George") || v.name.includes("Daniel") || v.name.includes("Oliver")
    );
    if (britishVoice) {
      utterance.voice = britishVoice;
    }
    utterance.pitch = 0.94;
    utterance.rate = 1.02;

    utterance.onstart = () => {
      isSpeakingAudio = true;
      updateState("acting");
    };
    utterance.onend = () => {
      isSpeakingAudio = false;
      updateState("idle");
    };
    utterance.onerror = () => {
      isSpeakingAudio = false;
      updateState("idle");
    };

    window.speechSynthesis.speak(utterance);
  }

  if (window.speechSynthesis) {
    window.speechSynthesis.onvoiceschanged = () => {
      window.speechSynthesis.getVoices();
    };
  }

  function toggleVoiceRecognition() {
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRec) {
      alert("Speech recognition is not natively supported in this browser. Please use Chrome or Edge.");
      return;
    }

    if (isListeningRecognition) {
      if (activeRecognition) activeRecognition.stop();
      return;
    }

    const recognition = new SpeechRec();
    recognition.lang = "en-US";
    recognition.continuous = false;
    recognition.interimResults = false;

    recognition.onstart = () => {
      isListeningRecognition = true;
      if (voiceInputBtn) voiceInputBtn.classList.add("listening");
      updateState("listening");
      playBlipSound(1200);
    };

    recognition.onresult = (e) => {
      const transcript = e.results[0][0].transcript;
      if (chatInput) chatInput.value = transcript;
      if (chatForm) chatForm.dispatchEvent(new Event("submit"));
    };

    recognition.onend = () => {
      isListeningRecognition = false;
      if (voiceInputBtn) voiceInputBtn.classList.remove("listening");
      if (currentState === "listening") updateState("idle");
    };

    recognition.onerror = () => {
      isListeningRecognition = false;
      if (voiceInputBtn) voiceInputBtn.classList.remove("listening");
      if (currentState === "listening") updateState("idle");
    };

    activeRecognition = recognition;
    recognition.start();
  }

  if (voiceInputBtn) {
    voiceInputBtn.addEventListener("click", () => {
      initAudio();
      toggleVoiceRecognition();
    });
  }

  // -------------------------------------------------------------------------
  // 9. Inbound Server Message Handlers (Local Daemon)
  // -------------------------------------------------------------------------
  function handleServerMessage(data) {
    switch (data.type) {
      case "state":
        if (data.user_title) updateUserTitle(data.user_title);
        updateState(data.state || "idle");
        break;

      case "chat":
        appendChatMessage(data.role || "assistant", data.content || "");
        playBlipSound(1000);
        break;

      case "stats":
        updateStats(data.cpu, data.ram, data.disk);
        break;

      case "audit":
        appendAuditRecord(data.record);
        break;

      case "confirm_request":
        showConfirmationDialog(data.id, data.action, data.risk_tier, data.warning, data.timeout_seconds || 30);
        playAlertSound();
        break;

      case "confirm_timeout":
        if (activeConfirmId === data.id) hideConfirmationDialog();
        break;

      case "kill_state":
        if (data.active) {
          updateState("halted");
          playKillSound();
        } else {
          updateState("idle");
        }
        break;

      default:
        break;
    }
  }

  // -------------------------------------------------------------------------
  // 10. UI Update Helpers
  // -------------------------------------------------------------------------
  function updateUserTitle(title) {
    userTitle = title || "Sir";
    if (userTitleEl) userTitleEl.textContent = userTitle.toUpperCase();
  }

  function updateState(state) {
    currentState = (state || "idle").toLowerCase();
    if (statePill) statePill.className = `status-pill ${currentState}`;
    if (stateText) stateText.textContent = currentState.toUpperCase();

    if (killBtn) {
      if (currentState === "halted") {
        killBtn.innerHTML = '<span class="kill-icon">&#9889;</span> RE-ENGAGE';
        killBtn.title = "Re-engage Stark Core / Reset Kill Switch";
        killBtn.style.background = "rgba(0, 229, 255, 0.2)";
        killBtn.style.borderColor = "var(--stark-cyan)";
        killBtn.style.color = "var(--stark-cyan)";
      } else {
        killBtn.innerHTML = '<span class="kill-icon">&#9888;</span> OVERRIDE';
        killBtn.title = "Emergency Stop / Trigger Kill Switch";
        killBtn.style.background = "";
        killBtn.style.borderColor = "";
        killBtn.style.color = "";
      }
    }

    if (coreOutputVal) {
      if (currentState === "halted") {
        coreOutputVal.textContent = "0.0 GW // HALTED";
        coreOutputVal.style.color = "var(--stark-red)";
      } else if (currentState === "offline") {
        coreOutputVal.textContent = "STANDBY // OFFLINE";
        coreOutputVal.style.color = "var(--stark-gold)";
      } else if (currentState === "thinking") {
        coreOutputVal.textContent = "9.8 GW // HIGH LOAD";
        coreOutputVal.style.color = "var(--stark-gold)";
      } else if (currentState === "acting") {
        coreOutputVal.textContent = "8.9 GW // DISPATCH";
        coreOutputVal.style.color = "var(--stark-cyan)";
      } else {
        coreOutputVal.textContent = "8.2 GW // OPTIMAL";
        coreOutputVal.style.color = "var(--stark-gold)";
      }
    }
  }

  function appendChatMessage(role, content) {
    if (!transcriptContainer) return;

    const msgDiv = document.createElement("div");
    msgDiv.className = `chat-message ${role}`;

    const author = document.createElement("span");
    author.className = "msg-author";

    if (role === "user") {
      author.textContent = userTitle ? `${userTitle.toUpperCase()} // DIRECTIVE` : "USER // DIRECTIVE";
    } else if (role === "system") {
      author.textContent = "STARK DEFENSE PROTOCOL";
    } else {
      author.textContent = "J.A.R.V.I.S. // MARK VII";
    }

    const body = document.createElement("div");
    body.className = "msg-body";
    body.textContent = content;

    msgDiv.appendChild(author);
    msgDiv.appendChild(body);
    transcriptContainer.appendChild(msgDiv);

    transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
  }

  function updateStats(cpu, ram, disk) {
    const safeCpu = Math.max(0, Math.min(100, Math.round(cpu || 0)));
    const safeRam = Math.max(0, Math.min(100, Math.round(ram || 0)));

    if (cpuStat) cpuStat.textContent = `${safeCpu}%`;
    if (cpuCircle) {
      const offset = GAUGE_CIRCUMFERENCE - (safeCpu / 100) * GAUGE_CIRCUMFERENCE;
      cpuCircle.style.strokeDashoffset = offset.toFixed(2);
    }
    if (cpuFill) cpuFill.style.width = `${safeCpu}%`;

    if (ramStat) ramStat.textContent = `${safeRam}%`;
    if (ramCircle) {
      const offset = GAUGE_CIRCUMFERENCE - (safeRam / 100) * GAUGE_CIRCUMFERENCE;
      ramCircle.style.strokeDashoffset = offset.toFixed(2);
    }
    if (ramFill) ramFill.style.width = `${safeRam}%`;

    if (diskStat) {
      if (disk !== undefined && disk !== null) {
        diskStat.textContent = `${Math.round(disk)} GB`;
      } else {
        diskStat.textContent = "-- GB";
      }
    }
    if (diskCircle) diskCircle.style.strokeDashoffset = "60";
  }

  function appendAuditRecord(record) {
    if (!auditFeed || !record) return;

    const item = document.createElement("div");
    item.className = "audit-item info";

    const timestamp = record.timestamp || new Date().toISOString();
    const timeStr = timestamp.substring(11, 19);

    const timeSpan = document.createElement("span");
    timeSpan.className = "audit-time";
    timeSpan.textContent = timeStr;

    const eventSpan = document.createElement("span");
    eventSpan.className = "audit-event";
    eventSpan.textContent = record.event_type || "EVENT";

    const detailSpan = document.createElement("span");
    detailSpan.className = "audit-detail";
    detailSpan.textContent = record.action || JSON.stringify(record.details || {});

    item.appendChild(timeSpan);
    item.appendChild(eventSpan);
    item.appendChild(detailSpan);

    auditFeed.appendChild(item);
    auditFeed.scrollTop = auditFeed.scrollHeight;
  }

  // -------------------------------------------------------------------------
  // 11. Modal Dialog Handlers
  // -------------------------------------------------------------------------
  function showConfirmationDialog(id, action, riskTier, warning, timeoutSec) {
    activeConfirmId = id;
    if (confirmActionText) confirmActionText.textContent = action;
    if (warning && confirmWarningBanner && confirmWarningText) {
      confirmWarningText.textContent = warning;
      confirmWarningBanner.classList.remove("hidden");
    } else if (confirmWarningBanner) {
      confirmWarningBanner.classList.add("hidden");
    }

    let remaining = timeoutSec;
    if (confirmCountdown) confirmCountdown.textContent = `${remaining}s`;

    if (confirmTimerInterval) clearInterval(confirmTimerInterval);
    confirmTimerInterval = setInterval(() => {
      remaining -= 1;
      if (confirmCountdown) confirmCountdown.textContent = `${remaining}s`;
      if (remaining <= 0) {
        clearInterval(confirmTimerInterval);
        hideConfirmationDialog();
      }
    }, 1000);

    if (confirmModal) confirmModal.classList.remove("hidden");
  }

  function hideConfirmationDialog() {
    if (confirmTimerInterval) {
      clearInterval(confirmTimerInterval);
      confirmTimerInterval = null;
    }
    activeConfirmId = null;
    if (confirmModal) confirmModal.classList.add("hidden");
  }

  if (modalApproveBtn) {
    modalApproveBtn.addEventListener("click", () => {
      if (activeConfirmId) {
        sendWSMessage({ type: "confirm_response", id: activeConfirmId, approved: true });
        hideConfirmationDialog();
      }
    });
  }

  if (modalDenyBtn) {
    modalDenyBtn.addEventListener("click", () => {
      if (activeConfirmId) {
        sendWSMessage({ type: "confirm_response", id: activeConfirmId, approved: false });
        hideConfirmationDialog();
      }
    });
  }

  // Settings Modal Handlers
  function openSettingsModal() {
    if (!settingsModal) return;
    if (cfgGroqKey) cfgGroqKey.value = getStoredGroqKey();
    if (cfgGroqModel) cfgGroqModel.value = getStoredGroqModel();
    if (cfgUserTitle) cfgUserTitle.value = getStoredUserTitle();

    const mode = getStoredModePreference();
    const radio = document.querySelector(`input[name="conn-mode"][value="${mode}"]`);
    if (radio) radio.checked = true;

    settingsModal.classList.remove("hidden");
  }

  function closeSettingsModal() {
    if (settingsModal) settingsModal.classList.add("hidden");
  }

  if (configBtn) configBtn.addEventListener("click", openSettingsModal);
  if (settingsCloseX) settingsCloseX.addEventListener("click", closeSettingsModal);
  if (settingsCancelBtn) settingsCancelBtn.addEventListener("click", closeSettingsModal);

  if (settingsSaveBtn) {
    settingsSaveBtn.addEventListener("click", () => {
      const key = cfgGroqKey ? cfgGroqKey.value.trim() : "";
      const model = cfgGroqModel ? cfgGroqModel.value : "openai/gpt-oss-120b";
      const title = cfgUserTitle ? cfgUserTitle.value.trim() : "Sir";
      const modeRadio = document.querySelector('input[name="conn-mode"]:checked');
      const mode = modeRadio ? modeRadio.value : "auto";

      localStorage.setItem("jarvis_groq_key", key);
      localStorage.setItem("jarvis_groq_model", model);
      localStorage.setItem("jarvis_user_title", title || "Sir");
      localStorage.setItem("jarvis_conn_mode", mode);

      updateUserTitle(title);
      closeSettingsModal();
      playActionSound();

      if (mode === "standalone") {
        fallbackToCloudMode();
      } else {
        initConnection();
      }
    });
  }

  // -------------------------------------------------------------------------
  // 12. Directive Input and Kill Switch Actions
  // -------------------------------------------------------------------------
  if (screenVisionBtn) {
    screenVisionBtn.addEventListener("click", () => {
      initAudio();
      playActionSound();
      const promptText = "Jarvis, please analyze my current desktop screen and report what you see, noting any errors or active windows.";
      appendChatMessage("user", promptText);

      if (activeMode === "local" && sendWSMessage({ type: "chat", text: promptText })) {
        return;
      }
      executeStandaloneTurn(promptText);
    });
  }

  if (chatForm && chatInput) {
    chatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const text = chatInput.value.trim();
      if (!text) return;

      initAudio();
      playBlipSound(1400);
      appendChatMessage("user", text);
      chatInput.value = "";

      if (activeMode === "local" && sendWSMessage({ type: "chat", text: text })) {
        return;
      }
      executeStandaloneTurn(text);
    });
  }

  if (killBtn) {
    killBtn.addEventListener("click", () => {
      if (currentState === "halted") {
        playBlipSound(1400);
        if (activeMode === "local") {
          sendWSMessage({ type: "resume" });
        }
        updateState("idle");
      } else {
        if (confirm("STARK DEFENSE PROTOCOL OVERRIDE: Confirm Emergency Kill Switch activation?")) {
          playKillSound();
          if (activeMode === "local") {
            sendWSMessage({ type: "kill" });
          }
          updateState("halted");
        }
      }
    });
  }

  // -------------------------------------------------------------------------
  // 13. Initialization
  // -------------------------------------------------------------------------
  initConnection();
})();
