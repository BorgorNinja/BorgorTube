/**
 * BorgorTube – Deno MPV WebSocket Bridge  (Phase 3 update)
 *
 * Bridges the browser (WebSocket) ↔ mpv's UNIX IPC socket.
 *
 * Phase 3 adds:
 *  - A PERSISTENT socket connection to mpv (instead of connect-per-command)
 *  - observe_property subscriptions pushed to all browser clients in real time
 *  - Multiplexed request/response matching via request_id
 *  - mpv EOF / idle detection → broadcasts "mpv_ended" event
 *
 * Usage:
 *   deno run --allow-net --allow-read --allow-write ws_bridge.ts
 *
 * Env vars:
 *   WS_PORT      WebSocket server port  (default: 8001)
 *   MPV_SOCKET   Path to mpv IPC socket (default: /tmp/mpvsocket)
 *   POLL_MS      Reconnect / health poll ms (default: 1000)
 */

const WS_PORT  = parseInt(Deno.env.get("WS_PORT")  ?? "8001");
const IS_WINDOWS = Deno.build.os === "windows";
const DEFAULT_SOCKET = IS_WINDOWS ? String.raw`\\.\pipe\mpvsocket` : "/tmp/mpvsocket";
const MPV_SOCK = Deno.env.get("MPV_SOCKET") ?? DEFAULT_SOCKET;
const POLL_MS  = parseInt(Deno.env.get("POLL_MS")   ?? "1000");

// ─── Types ────────────────────────────────────────────────────────────────

interface MPVEvent {
  event?: string;
  id?: number;
  data?: unknown;
  name?: string;
  request_id?: number;
  error?: string;
}

interface PendingRequest {
  resolve: (v: unknown) => void;
  reject:  (e: Error)   => void;
  timer:   ReturnType<typeof setTimeout>;
}

// ─── Persistent MPV socket connection ────────────────────────────────────

class MPVConnection {
  private conn: Deno.Conn | null = null;
  private buf = "";
  private reqId = 100;
  private pending = new Map<number, PendingRequest>();
  private _onEvent: ((e: MPVEvent) => void) | null = null;
  private _onClose: (() => void) | null = null;
  private _reading = false;

  get connected() { return this.conn !== null; }

  onEvent(fn: (e: MPVEvent) => void) { this._onEvent = fn; }
  onClose(fn: () => void)             { this._onClose = fn; }

  async connect(): Promise<boolean> {
    if (this.conn) return true;
    try {
      // Windows uses named pipes (connectPipe); Unix uses Unix socket
      this.conn = IS_WINDOWS
        ? await (Deno as unknown as {connectPipe?: (p:string) => Promise<Deno.Conn>}).connectPipe?.(MPV_SOCK) ?? (() => { throw new Error("connectPipe not available"); })()
        : await Deno.connect({ path: MPV_SOCK, transport: "unix" });
      this._startRead();
      await this._subscribeProperties();
      return true;
    } catch {
      this.conn = null;
      return false;
    }
  }

  private async _subscribeProperties() {
    const props = ["time-pos", "pause", "duration", "volume",
                   "fullscreen", "percent-pos", "media-title", "idle-active"];
    for (let i = 0; i < props.length; i++) {
      await this._sendRaw({ command: ["observe_property", i + 1, props[i]] });
    }
  }

  private async _startRead() {
    if (this._reading) return;
    this._reading = true;
    const dec = new TextDecoder();
    const tmpBuf = new Uint8Array(8192);
    try {
      while (this.conn) {
        const n = await this.conn.read(tmpBuf);
        if (n === null) break;
        this.buf += dec.decode(tmpBuf.subarray(0, n));
        let nl: number;
        while ((nl = this.buf.indexOf("\n")) !== -1) {
          const line = this.buf.slice(0, nl).trim();
          this.buf = this.buf.slice(nl + 1);
          if (!line) continue;
          try {
            const msg: MPVEvent = JSON.parse(line);
            this._dispatch(msg);
          } catch { /* malformed line */ }
        }
      }
    } catch { /* socket closed */ }
    this._reading = false;
    this.conn = null;
    this._onClose?.();
    // Reject all pending requests
    for (const [, p] of this.pending) {
      clearTimeout(p.timer);
      p.reject(new Error("mpv socket closed"));
    }
    this.pending.clear();
  }

  private _dispatch(msg: MPVEvent) {
    // Property change event (pushed by mpv)
    if (msg.event === "property-change") {
      this._onEvent?.(msg);
      return;
    }
    // End-of-file / idle events
    if (msg.event === "end-file" || msg.event === "idle") {
      this._onEvent?.(msg);
      return;
    }
    // Response to a command we sent
    if (msg.request_id !== undefined) {
      const p = this.pending.get(msg.request_id);
      if (p) {
        clearTimeout(p.timer);
        this.pending.delete(msg.request_id);
        p.resolve(msg);
      }
      return;
    }
    // Other events (e.g. "playback-restart", "seek")
    if (msg.event) {
      this._onEvent?.(msg);
    }
  }

  private async _sendRaw(payload: object): Promise<void> {
    if (!this.conn) return;
    try {
      const data = new TextEncoder().encode(JSON.stringify(payload) + "\n");
      await this.conn.write(data);
    } catch { this.close(); }
  }

  /** Send a command and await the response (with timeout). */
  async command(cmd: unknown[], timeoutMs = 3000): Promise<MPVEvent> {
    if (!this.conn) throw new Error("mpv not connected");
    const id = ++this.reqId;
    const payload = { command: cmd, request_id: id };
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("mpv command timed out"));
      }, timeoutMs);
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer });
      this._sendRaw(payload).catch((e) => {
        this.pending.delete(id);
        clearTimeout(timer);
        reject(e);
      });
    });
  }

  close() {
    try { this.conn?.close(); } catch { /* ignore */ }
    this.conn = null;
  }
}

// ─── Global state ─────────────────────────────────────────────────────────

const mpv = new MPVConnection();
const clients = new Set<WebSocket>();
let lastStatus: Record<string, unknown> = { connected: false };

function broadcast(msg: unknown) {
  const text = JSON.stringify(msg);
  for (const ws of clients) {
    if (ws.readyState === WebSocket.OPEN) ws.send(text);
  }
}

// ─── mpv event → browser broadcast ───────────────────────────────────────

mpv.onEvent((e) => {
  if (e.event === "property-change") {
    const key = e.name?.replace(/-/g, "_") ?? "unknown";
    lastStatus[key] = e.data;
    lastStatus.connected = true;
    broadcast({ type: "property", name: e.name, value: e.data, status: lastStatus });
  } else if (e.event === "end-file") {
    broadcast({ type: "mpv_ended", reason: (e as Record<string,unknown>).reason ?? "unknown" });
  } else if (e.event === "idle") {
    broadcast({ type: "mpv_idle" });
  } else {
    broadcast({ type: "mpv_event", event: e.event, data: e });
  }
});

mpv.onClose(() => {
  lastStatus = { connected: false };
  broadcast({ type: "status", data: lastStatus });
  console.log("[bridge] mpv socket closed");
});

// ─── Reconnect loop ───────────────────────────────────────────────────────

async function reconnectLoop() {
  while (true) {
    if (!mpv.connected) {
      const ok = await mpv.connect();
      if (ok) {
        console.log("[bridge] connected to mpv IPC socket");
        lastStatus = { connected: true };
        broadcast({ type: "status", data: lastStatus });
      }
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}

// ─── WebSocket server ─────────────────────────────────────────────────────

Deno.serve({ port: WS_PORT }, (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, {
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
      },
    });
  }

  const url = new URL(req.url);

  if (url.pathname === "/health") {
    return new Response(
      JSON.stringify({ status: "ok", mpv_connected: mpv.connected, clients: clients.size }),
      { headers: { "Content-Type": "application/json" } }
    );
  }

  if (req.headers.get("upgrade") !== "websocket") {
    return new Response("BorgorTube MPV Bridge – connect via WebSocket", { status: 426 });
  }

  const { socket: ws, response } = Deno.upgradeWebSocket(req);

  ws.onopen = () => {
    clients.add(ws);
    ws.send(JSON.stringify({
      type: "hello",
      bridge: "BorgorTube MPV Bridge v2 (Phase 3)",
      port: WS_PORT,
      mpv_connected: mpv.connected,
    }));
    // Immediately push current status
    ws.send(JSON.stringify({ type: "status", data: { ...lastStatus, connected: mpv.connected } }));
    console.log(`[bridge] client connected (total: ${clients.size})`);
  };

  ws.onclose = () => {
    clients.delete(ws);
    console.log(`[bridge] client disconnected (total: ${clients.size})`);
  };

  ws.onerror = () => clients.delete(ws);

  ws.onmessage = async (evt) => {
    let msg: Record<string, unknown>;
    try { msg = JSON.parse(evt.data); }
    catch { ws.send(JSON.stringify({ type: "error", detail: "invalid JSON" })); return; }

    const action = msg.action as string;

    switch (action) {
      case "ipc": {
        const cmd = msg.command as unknown[];
        if (!cmd) { ws.send(JSON.stringify({ type: "error", detail: "command required" })); return; }
        if (!mpv.connected) {
          ws.send(JSON.stringify({ type: "ipc_response", request_id: msg.request_id, data: { error: "mpv not connected" } }));
          return;
        }
        try {
          const result = await mpv.command(cmd);
          ws.send(JSON.stringify({ type: "ipc_response", request_id: msg.request_id, data: result }));
        } catch (e) {
          ws.send(JSON.stringify({ type: "ipc_response", request_id: msg.request_id, data: { error: String(e) } }));
        }
        break;
      }
      case "get_status":
        ws.send(JSON.stringify({ type: "status", data: { ...lastStatus, connected: mpv.connected } }));
        break;
      case "ping":
        ws.send(JSON.stringify({ type: "pong" }));
        break;
      default:
        ws.send(JSON.stringify({ type: "error", detail: `unknown action: ${action}` }));
    }
  };

  return response;
});

console.log(`[BorgorTube MPV Bridge v2] ws://0.0.0.0:${WS_PORT}`);
console.log(`[BorgorTube MPV Bridge v2] mpv socket: ${MPV_SOCK}`);
console.log(`[BorgorTube MPV Bridge v2] reconnect poll: ${POLL_MS}ms`);
reconnectLoop();
