/**
 * BorgorTube – API module
 * Wraps all fetch() calls to the Python FastAPI backend.
 */

const API_BASE = window.location.origin.includes("localhost") || window.location.origin.includes("127.0.0.1")
  ? "http://localhost:8000"
  : window.location.origin;

const BRIDGE_WS_URL = "ws://localhost:8001"; // Deno MPV bridge

// ── Generic fetch helper ────────────────────────────────────────────────

async function apiFetch(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ── Search ──────────────────────────────────────────────────────────────

window.BorgorAPI = {

  async search(query, maxResults = 20) {
    return apiFetch(`/api/search?q=${encodeURIComponent(query)}&max_results=${maxResults}`);
  },

  async history() {
    return apiFetch("/api/history");
  },

  // ── Video ──────────────────────────────────────────────────────────

  async getVideo(url) {
    return apiFetch(`/api/video?url=${encodeURIComponent(url)}`);
  },

  // ── Channel ────────────────────────────────────────────────────────

  async getChannel(url, maxResults = 20) {
    return apiFetch(`/api/channel?url=${encodeURIComponent(url)}&max_results=${maxResults}`);
  },

  // ── Comments ───────────────────────────────────────────────────────

  async getComments(url, scrollCount = 1, maxComments = 50) {
    return apiFetch(
      `/api/comments?url=${encodeURIComponent(url)}&scroll_count=${scrollCount}&max_comments=${maxComments}`
    );
  },

  // ── MPV ────────────────────────────────────────────────────────────

  async mpvLaunch({ url, quality = "360p", start_time = 0, detached = true, low_latency = false }) {
    return apiFetch("/api/mpv/launch", {
      method: "POST",
      body: JSON.stringify({ url, quality, start_time, detached, low_latency }),
    });
  },

  async mpvKill() {
    return apiFetch("/api/mpv/kill", { method: "POST" });
  },

  async mpvStatus() {
    return apiFetch("/api/mpv/status");
  },

  async mpvIPC(command) {
    return apiFetch("/api/mpv/ipc", {
      method: "POST",
      body: JSON.stringify({ command }),
    });
  },
};

// ── Deno WebSocket bridge (MPV IPC) ─────────────────────────────────────

window.MPVBridge = (() => {
  let ws = null;
  let reconnectTimer = null;
  const listeners = {};
  let connected = false;

  function emit(event, data) {
    (listeners[event] || []).forEach((fn) => fn(data));
  }

  function connect() {
    if (ws && ws.readyState === WebSocket.OPEN) return;

    try {
      ws = new WebSocket(BRIDGE_WS_URL);
    } catch {
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      connected = true;
      clearTimeout(reconnectTimer);
      emit("connect", null);
      console.log("[MPVBridge] connected to Deno WS bridge");
    };

    ws.onclose = () => {
      connected = false;
      emit("disconnect", null);
      scheduleReconnect();
    };

    ws.onerror = () => {
      connected = false;
      // quietly fail – Deno bridge is optional
    };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        emit("message", msg);
        if (msg.type === "status") emit("status", msg.data);
      } catch { /* ignore */ }
    };
  }

  function scheduleReconnect() {
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 5000);
  }

  function send(payload) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
      return true;
    }
    return false;
  }

  return {
    connect,
    send,
    isConnected: () => connected,

    on(event, fn) {
      listeners[event] = listeners[event] || [];
      listeners[event].push(fn);
    },

    // Convenience: send an IPC command via the Deno bridge
    ipc(command, request_id) {
      return send({ action: "ipc", command, request_id });
    },
  };
})();
