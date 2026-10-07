/**
 * J.A.R.V.I.S. MARK VII - Web HUD Client Application
 * Stark Industries Autonomous Defense & Cognitive Assistant Interface
 *
 * Features:
 * - Canvas 2D Animated Arc Reactor Core with state-reactive energy kinetics
 * - Procedural Web Audio API sound synthesizer (100% native, zero MP3 assets)
 * - WebSocket telemetry, real-time audit streaming, and Stark protocol override dialog
 * - Visibility-aware animation loop (0% idle CPU when tab is hidden)
 * - Circular SVG gauges for CPU, RAM, and Disk storage
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
      } catch (e) {
        // storage disabled
      }
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
  const userTitleEl = document.getElementById("hud-user-title");
  const transcriptContainer = document.getElementById("transcript-container");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");

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

  // Modal elements
  const confirmModal = document.getElementById("confirmation-modal");
  const confirmActionText = document.getElementById("confirm-action-text");
  const confirmWarningBanner = document.getElementById("confirm-warning-banner");
  const confirmWarningText = document.getElementById("confirm-warning-text");
  const confirmCountdown = document.getElementById("confirm-countdown");
  const modalApproveBtn = document.getElementById("modal-approve-btn");
  const modalDenyBtn = document.getElementById("modal-deny-btn");

  let socket = null;
  let activeConfirmId = null;
  let confirmTimerInterval = null;
  let currentState = "idle";
  let userTitle = "Sir";

  // SVG Gauge Circumference for r=38: 2 * PI * 38 = 238.761
  const GAUGE_CIRCUMFERENCE = 238.76;

  // -------------------------------------------------------------------------
  // 3. Procedural Web Audio API Sound Synthesizer
  // -------------------------------------------------------------------------
  let audioCtx = null;
  let sfxEnabled = true;

  try {
    const savedSfx = localStorage.getItem("jarvis_sfx_enabled");
    if (savedSfx !== null) {
      sfxEnabled = savedSfx === "true";
    }
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
      if (AudioContextClass) {
        audioCtx = new AudioContextClass();
      }
    }
    if (audioCtx && audioCtx.state === "suspended") {
      audioCtx.resume();
    }
  }

  // Resume AudioContext on first user interaction
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
  // 4. Arc Reactor & Ambient Canvas Animations (Visibility-Aware, 30 FPS Cap)
  // -------------------------------------------------------------------------
  const reactorCanvas = document.getElementById("arc-reactor-canvas");
  const reactorCtx = reactorCanvas ? reactorCanvas.getContext("2d") : null;

  const ambientCanvas = document.getElementById("ambient-canvas");
  const ambientCtx = ambientCanvas ? ambientCanvas.getContext("2d") : null;

  let isTabVisible = !document.hidden;
  let animFrameId = null;
  let lastFrameTime = 0;
  const targetFPS = 30;
  const frameInterval = 1000 / targetFPS;

  let reactorAngleOuter = 0;
  let reactorAngleInner = 0;
  let pulsePhase = 0;
  let ambientWavePhase = 0;

  function renderVisualizers(timestamp) {
    if (!isTabVisible) {
      animFrameId = null;
      return;
    }

    if (timestamp - lastFrameTime >= frameInterval) {
      lastFrameTime = timestamp;

      // Render 1: Arc Reactor
      if (reactorCtx && reactorCanvas) {
        renderArcReactor();
      }

      // Render 2: Ambient Waveform in Header
      if (ambientCtx && ambientCanvas) {
        renderAmbientWave();
      }
    }

    animFrameId = requestAnimationFrame(renderVisualizers);
  }

  function renderArcReactor() {
    const w = reactorCanvas.width;
    const h = reactorCanvas.height;
    const cx = w / 2;
    const cy = h / 2;
    const ctx = reactorCtx;

    ctx.clearRect(0, 0, w, h);

    // Color theme based on state
    let primaryColor = "rgba(0, 240, 255, 0.9)";
    let glowColor = "rgba(0, 240, 255, 0.4)";
    let coreColor = "#ffffff";
    let speedMult = 1.0;

    if (currentState === "thinking") {
      primaryColor = "rgba(255, 183, 0, 0.95)";
      glowColor = "rgba(255, 183, 0, 0.5)";
      speedMult = 2.4;
    } else if (currentState === "acting") {
      primaryColor = "rgba(56, 189, 248, 0.95)";
      glowColor = "rgba(56, 189, 248, 0.6)";
      speedMult = 1.8;
    } else if (currentState === "halted") {
      primaryColor = "rgba(255, 42, 75, 0.95)";
      glowColor = "rgba(255, 42, 75, 0.55)";
      coreColor = "#ff2a4b";
      speedMult = 0.2;
    }

    reactorAngleOuter += 0.012 * speedMult;
    reactorAngleInner -= 0.02 * speedMult;
    pulsePhase += 0.05 * speedMult;

    const pulseScale = 1 + Math.sin(pulsePhase) * 0.06;

    // Outer Thin Ring
    ctx.beginPath();
    ctx.arc(cx, cy, 98, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(0, 240, 255, 0.25)";
    ctx.lineWidth = 1;
    ctx.stroke();

    // Outer Tick Ring (36 radial marks)
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(reactorAngleOuter);
    for (let i = 0; i < 36; i++) {
      const angle = (i * Math.PI) / 18;
      const isMajor = i % 9 === 0;
      const len = isMajor ? 8 : 4;
      const r1 = 92;
      const r2 = r1 - len;
      ctx.beginPath();
      ctx.moveTo(Math.cos(angle) * r1, Math.sin(angle) * r1);
      ctx.lineTo(Math.cos(angle) * r2, Math.sin(angle) * r2);
      ctx.strokeStyle = isMajor ? primaryColor : "rgba(0, 240, 255, 0.35)";
      ctx.lineWidth = isMajor ? 2 : 1;
      ctx.stroke();
    }
    ctx.restore();

    // 10 Electromagnetic Repulsor Coils
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

      // Coil base block
      ctx.fillStyle = "rgba(10, 24, 48, 0.85)";
      ctx.fillRect(-10, -5, 20, 10);
      ctx.strokeStyle = primaryColor;
      ctx.lineWidth = 1.5;
      ctx.strokeRect(-10, -5, 20, 10);

      // Coil copper wraps
      ctx.fillStyle = "rgba(255, 183, 0, 0.75)";
      ctx.fillRect(-6, -4, 3, 8);
      ctx.fillRect(3, -4, 3, 8);

      ctx.restore();
    }
    ctx.restore();

    // Inner Gyro Dash Ring
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

    // Inner Glowing Core
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

    // Center Core Triangular Inscription (Stark Mark VII Tri-Core)
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

  function startAnimation() {
    if (!animFrameId && isTabVisible) {
      lastFrameTime = performance.now();
      animFrameId = requestAnimationFrame(renderVisualizers);
    }
  }

  function stopAnimation() {
    if (animFrameId) {
      cancelAnimationFrame(animFrameId);
      animFrameId = null;
    }
  }

  document.addEventListener("visibilitychange", () => {
    isTabVisible = !document.hidden;
    if (isTabVisible) {
      startAnimation();
    } else {
      stopAnimation();
    }
  });

  startAnimation();

  // -------------------------------------------------------------------------
  // 5. WebSocket Communication & Reconnection
  // -------------------------------------------------------------------------
  function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      console.log("[HUD] WebSocket connected. Authenticating with session token...");
      socket.send(JSON.stringify({ type: "auth", token: sessionToken }));
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
      console.warn("[HUD] WebSocket connection closed:", event.code, event.reason);
      updateState("halted");
      if (event.code !== 1008 && event.code !== 4001) {
        setTimeout(connectWebSocket, 2000);
      }
    };

    socket.onerror = (err) => {
      console.error("[HUD] WebSocket error:", err);
    };
  }

  function sendWSMessage(msg) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(msg));
    }
  }

  // -------------------------------------------------------------------------
  // 6. Inbound Server Message Handlers
  // -------------------------------------------------------------------------
  function handleServerMessage(data) {
    switch (data.type) {
      case "state":
        if (data.user_title) {
          updateUserTitle(data.user_title);
        }
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
        playAlertSound();
        showConfirmationModal(data.id, data.action, data.warning, data.timeout || 30);
        break;

      case "confirm_dismissed":
        dismissConfirmationModal(data.id);
        break;

      case "kill_state":
        playKillSound();
        updateState("halted");
        appendChatMessage("system", "Stark Protocol Override engaged. Emergency Kill Switch activated.");
        break;

      default:
        console.log("[HUD] Unhandled message payload:", data);
    }
  }

  function updateUserTitle(title) {
    if (!title) return;
    userTitle = title;
    if (userTitleEl) {
      userTitleEl.textContent = title.toUpperCase();
    }
  }

  function updateState(state) {
    currentState = (state || "idle").toLowerCase();
    if (statePill) {
      statePill.className = `status-pill ${currentState}`;
    }
    if (stateText) {
      stateText.textContent = currentState.toUpperCase();
    }
    if (coreOutputVal) {
      if (currentState === "halted") {
        coreOutputVal.textContent = "0.0 GW // HALTED";
        coreOutputVal.style.color = "var(--stark-red)";
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
    // CPU
    if (cpu !== undefined && cpu !== null) {
      const cpuVal = Math.min(100, Math.max(0, Math.round(cpu)));
      if (cpuStat) cpuStat.textContent = `${cpuVal}%`;
      if (cpuCircle) {
        const offset = GAUGE_CIRCUMFERENCE - (cpuVal / 100) * GAUGE_CIRCUMFERENCE;
        cpuCircle.setAttribute("stroke-dashoffset", offset.toFixed(2));
      }
      if (cpuFill) cpuFill.style.width = `${cpuVal}%`;
    }

    // RAM
    if (ram !== undefined && ram !== null) {
      const ramVal = Math.min(100, Math.max(0, Math.round(ram)));
      if (ramStat) ramStat.textContent = `${ramVal}%`;
      if (ramCircle) {
        const offset = GAUGE_CIRCUMFERENCE - (ramVal / 100) * GAUGE_CIRCUMFERENCE;
        ramCircle.setAttribute("stroke-dashoffset", offset.toFixed(2));
      }
      if (ramFill) ramFill.style.width = `${ramVal}%`;
    }

    // Disk
    if (disk !== undefined && disk !== null) {
      const diskVal = Math.max(0, Math.round(disk));
      if (diskStat) diskStat.textContent = `${diskVal} GB`;
      if (diskCircle) {
        // Proportional arc based on standard 100GB reference
        const pct = Math.min(100, (diskVal / 100) * 100);
        const offset = GAUGE_CIRCUMFERENCE - (pct / 100) * GAUGE_CIRCUMFERENCE;
        diskCircle.setAttribute("stroke-dashoffset", offset.toFixed(2));
      }
    }
  }

  function appendAuditRecord(rec) {
    if (!rec || !auditFeed) return;

    const item = document.createElement("div");
    const eventClass = (rec.event || "info").toLowerCase();
    item.className = `audit-item ${eventClass}`;

    const timeSpan = document.createElement("span");
    timeSpan.className = "audit-time";
    timeSpan.textContent = (rec.timestamp || "").split("T")[1]?.slice(0, 8) || "00:00:00";

    const eventSpan = document.createElement("span");
    eventSpan.className = "audit-event";
    eventSpan.textContent = `[${rec.event || "SEC_LOG"}] ${rec.tool_name || ""}`;

    const detailSpan = document.createElement("span");
    detailSpan.className = "audit-detail";
    const detail = rec.result_summary || rec.decision_reason || JSON.stringify(rec.arguments || {});
    detailSpan.textContent = detail;

    item.appendChild(timeSpan);
    item.appendChild(eventSpan);
    item.appendChild(detailSpan);

    auditFeed.appendChild(item);
    auditFeed.scrollTop = auditFeed.scrollHeight;
  }

  // -------------------------------------------------------------------------
  // 7. Stark Protocol Confirmation Dialog
  // -------------------------------------------------------------------------
  function showConfirmationModal(id, action, warning, timeoutSec) {
    activeConfirmId = id;
    if (confirmActionText) confirmActionText.textContent = action || "";

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
      if (remaining <= 0) {
        clearInterval(confirmTimerInterval);
        dismissConfirmationModal(id);
      } else if (confirmCountdown) {
        confirmCountdown.textContent = `${remaining}s`;
      }
    }, 1000);

    if (confirmModal) confirmModal.classList.remove("hidden");
  }

  function dismissConfirmationModal(id) {
    if (!id || id === activeConfirmId) {
      if (confirmTimerInterval) clearInterval(confirmTimerInterval);
      if (confirmModal) confirmModal.classList.add("hidden");
      activeConfirmId = null;
    }
  }

  if (modalApproveBtn) {
    modalApproveBtn.addEventListener("click", () => {
      if (activeConfirmId) {
        playActionSound();
        sendWSMessage({
          type: "confirm_response",
          id: activeConfirmId,
          approved: true,
        });
        dismissConfirmationModal(activeConfirmId);
      }
    });
  }

  if (modalDenyBtn) {
    modalDenyBtn.addEventListener("click", () => {
      if (activeConfirmId) {
        playBlipSound(600);
        sendWSMessage({
          type: "confirm_response",
          id: activeConfirmId,
          approved: false,
        });
        dismissConfirmationModal(activeConfirmId);
      }
    });
  }

  // Keyboard shortcuts Y/N for modal
  window.addEventListener("keydown", (e) => {
    if (activeConfirmId && confirmModal && !confirmModal.classList.contains("hidden")) {
      if (e.key === "y" || e.key === "Y") {
        modalApproveBtn?.click();
      } else if (e.key === "n" || e.key === "N" || e.key === "Escape") {
        modalDenyBtn?.click();
      }
    }
  });

  // -------------------------------------------------------------------------
  // 8. User Directive Input and Emergency Kill Actions
  // -------------------------------------------------------------------------
  const screenVisionBtn = document.getElementById("screen-vision-btn");
  if (screenVisionBtn) {
    screenVisionBtn.addEventListener("click", () => {
      initAudio();
      playActionSound();
      const promptText = "Jarvis, please analyze my current desktop screen and report what you see, noting any errors or active windows.";
      appendChatMessage("user", promptText);
      sendWSMessage({
        type: "chat",
        text: promptText,
      });
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

      sendWSMessage({
        type: "chat",
        text: text,
      });
    });
  }

  if (killBtn) {
    killBtn.addEventListener("click", () => {
      if (confirm("STARK DEFENSE PROTOCOL OVERRIDE: Confirm Emergency Kill Switch activation?")) {
        playKillSound();
        sendWSMessage({ type: "kill" });
        updateState("halted");
      }
    });
  }

  // Initialize WebSocket connection
  connectWebSocket();
})();
