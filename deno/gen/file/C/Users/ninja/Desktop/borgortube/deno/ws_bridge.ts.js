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
 */ const WS_PORT = parseInt(Deno.env.get("WS_PORT") ?? "8001");
const IS_WINDOWS = Deno.build.os === "windows";
const DEFAULT_SOCKET = IS_WINDOWS ? String.raw`\\.\pipe\mpvsocket` : "/tmp/mpvsocket";
const MPV_SOCK = Deno.env.get("MPV_SOCKET") ?? DEFAULT_SOCKET;
const POLL_MS = parseInt(Deno.env.get("POLL_MS") ?? "1000");
// ─── Persistent MPV socket connection ────────────────────────────────────
class MPVConnection {
  conn = null;
  buf = "";
  reqId = 100;
  pending = new Map();
  _onEvent = null;
  _onClose = null;
  _reading = false;
  get connected() {
    return this.conn !== null;
  }
  onEvent(fn) {
    this._onEvent = fn;
  }
  onClose(fn) {
    this._onClose = fn;
  }
  async connect() {
    if (this.conn) return true;
    try {
      // Windows uses named pipes (connectPipe); Unix uses Unix socket
      this.conn = IS_WINDOWS ? await Deno.connectPipe?.(MPV_SOCK) ?? (()=>{
        throw new Error("connectPipe not available");
      })() : await Deno.connect({
        path: MPV_SOCK,
        transport: "unix"
      });
      this._startRead();
      await this._subscribeProperties();
      return true;
    } catch  {
      this.conn = null;
      return false;
    }
  }
  async _subscribeProperties() {
    const props = [
      "time-pos",
      "pause",
      "duration",
      "volume",
      "fullscreen",
      "percent-pos",
      "media-title",
      "idle-active"
    ];
    for(let i = 0; i < props.length; i++){
      await this._sendRaw({
        command: [
          "observe_property",
          i + 1,
          props[i]
        ]
      });
    }
  }
  async _startRead() {
    if (this._reading) return;
    this._reading = true;
    const dec = new TextDecoder();
    const tmpBuf = new Uint8Array(8192);
    try {
      while(this.conn){
        const n = await this.conn.read(tmpBuf);
        if (n === null) break;
        this.buf += dec.decode(tmpBuf.subarray(0, n));
        let nl;
        while((nl = this.buf.indexOf("\n")) !== -1){
          const line = this.buf.slice(0, nl).trim();
          this.buf = this.buf.slice(nl + 1);
          if (!line) continue;
          try {
            const msg = JSON.parse(line);
            this._dispatch(msg);
          } catch  {}
        }
      }
    } catch  {}
    this._reading = false;
    this.conn = null;
    this._onClose?.();
    // Reject all pending requests
    for (const [, p] of this.pending){
      clearTimeout(p.timer);
      p.reject(new Error("mpv socket closed"));
    }
    this.pending.clear();
  }
  _dispatch(msg) {
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
  async _sendRaw(payload) {
    if (!this.conn) return;
    try {
      const data = new TextEncoder().encode(JSON.stringify(payload) + "\n");
      await this.conn.write(data);
    } catch  {
      this.close();
    }
  }
  /** Send a command and await the response (with timeout). */ async command(cmd, timeoutMs = 3000) {
    if (!this.conn) throw new Error("mpv not connected");
    const id = ++this.reqId;
    const payload = {
      command: cmd,
      request_id: id
    };
    return new Promise((resolve, reject)=>{
      const timer = setTimeout(()=>{
        this.pending.delete(id);
        reject(new Error("mpv command timed out"));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: resolve,
        reject,
        timer
      });
      this._sendRaw(payload).catch((e)=>{
        this.pending.delete(id);
        clearTimeout(timer);
        reject(e);
      });
    });
  }
  close() {
    try {
      this.conn?.close();
    } catch  {}
    this.conn = null;
  }
}
// ─── Global state ─────────────────────────────────────────────────────────
const mpv = new MPVConnection();
const clients = new Set();
let lastStatus = {
  connected: false
};
function broadcast(msg) {
  const text = JSON.stringify(msg);
  for (const ws of clients){
    if (ws.readyState === WebSocket.OPEN) ws.send(text);
  }
}
// ─── mpv event → browser broadcast ───────────────────────────────────────
mpv.onEvent((e)=>{
  if (e.event === "property-change") {
    const key = e.name?.replace(/-/g, "_") ?? "unknown";
    lastStatus[key] = e.data;
    lastStatus.connected = true;
    broadcast({
      type: "property",
      name: e.name,
      value: e.data,
      status: lastStatus
    });
  } else if (e.event === "end-file") {
    broadcast({
      type: "mpv_ended",
      reason: e.reason ?? "unknown"
    });
  } else if (e.event === "idle") {
    broadcast({
      type: "mpv_idle"
    });
  } else {
    broadcast({
      type: "mpv_event",
      event: e.event,
      data: e
    });
  }
});
mpv.onClose(()=>{
  lastStatus = {
    connected: false
  };
  broadcast({
    type: "status",
    data: lastStatus
  });
  console.log("[bridge] mpv socket closed");
});
// ─── Reconnect loop ───────────────────────────────────────────────────────
async function reconnectLoop() {
  while(true){
    if (!mpv.connected) {
      const ok = await mpv.connect();
      if (ok) {
        console.log("[bridge] connected to mpv IPC socket");
        lastStatus = {
          connected: true
        };
        broadcast({
          type: "status",
          data: lastStatus
        });
      }
    }
    await new Promise((r)=>setTimeout(r, POLL_MS));
  }
}
// ─── WebSocket server ─────────────────────────────────────────────────────
Deno.serve({
  port: WS_PORT
}, (req)=>{
  if (req.method === "OPTIONS") {
    return new Response(null, {
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*"
      }
    });
  }
  const url = new URL(req.url);
  if (url.pathname === "/health") {
    return new Response(JSON.stringify({
      status: "ok",
      mpv_connected: mpv.connected,
      clients: clients.size
    }), {
      headers: {
        "Content-Type": "application/json"
      }
    });
  }
  if (req.headers.get("upgrade") !== "websocket") {
    return new Response("BorgorTube MPV Bridge – connect via WebSocket", {
      status: 426
    });
  }
  const { socket: ws, response } = Deno.upgradeWebSocket(req);
  ws.onopen = ()=>{
    clients.add(ws);
    ws.send(JSON.stringify({
      type: "hello",
      bridge: "BorgorTube MPV Bridge v2 (Phase 3)",
      port: WS_PORT,
      mpv_connected: mpv.connected
    }));
    // Immediately push current status
    ws.send(JSON.stringify({
      type: "status",
      data: {
        ...lastStatus,
        connected: mpv.connected
      }
    }));
    console.log(`[bridge] client connected (total: ${clients.size})`);
  };
  ws.onclose = ()=>{
    clients.delete(ws);
    console.log(`[bridge] client disconnected (total: ${clients.size})`);
  };
  ws.onerror = ()=>clients.delete(ws);
  ws.onmessage = async (evt)=>{
    let msg;
    try {
      msg = JSON.parse(evt.data);
    } catch  {
      ws.send(JSON.stringify({
        type: "error",
        detail: "invalid JSON"
      }));
      return;
    }
    const action = msg.action;
    switch(action){
      case "ipc":
        {
          const cmd = msg.command;
          if (!cmd) {
            ws.send(JSON.stringify({
              type: "error",
              detail: "command required"
            }));
            return;
          }
          if (!mpv.connected) {
            ws.send(JSON.stringify({
              type: "ipc_response",
              request_id: msg.request_id,
              data: {
                error: "mpv not connected"
              }
            }));
            return;
          }
          try {
            const result = await mpv.command(cmd);
            ws.send(JSON.stringify({
              type: "ipc_response",
              request_id: msg.request_id,
              data: result
            }));
          } catch (e) {
            ws.send(JSON.stringify({
              type: "ipc_response",
              request_id: msg.request_id,
              data: {
                error: String(e)
              }
            }));
          }
          break;
        }
      case "get_status":
        ws.send(JSON.stringify({
          type: "status",
          data: {
            ...lastStatus,
            connected: mpv.connected
          }
        }));
        break;
      case "ping":
        ws.send(JSON.stringify({
          type: "pong"
        }));
        break;
      default:
        ws.send(JSON.stringify({
          type: "error",
          detail: `unknown action: ${action}`
        }));
    }
  };
  return response;
});
console.log(`[BorgorTube MPV Bridge v2] ws://0.0.0.0:${WS_PORT}`);
console.log(`[BorgorTube MPV Bridge v2] mpv socket: ${MPV_SOCK}`);
console.log(`[BorgorTube MPV Bridge v2] reconnect poll: ${POLL_MS}ms`);
reconnectLoop();
//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozLCJzb3VyY2VzIjpbImZpbGU6Ly8vQzovVXNlcnMvbmluamEvRGVza3RvcC9ib3Jnb3J0dWJlL2Rlbm8vd3NfYnJpZGdlLnRzIl0sInNvdXJjZXNDb250ZW50IjpbIi8qKlxuICogQm9yZ29yVHViZSDigJMgRGVubyBNUFYgV2ViU29ja2V0IEJyaWRnZSAgKFBoYXNlIDMgdXBkYXRlKVxuICpcbiAqIEJyaWRnZXMgdGhlIGJyb3dzZXIgKFdlYlNvY2tldCkg4oaUIG1wdidzIFVOSVggSVBDIHNvY2tldC5cbiAqXG4gKiBQaGFzZSAzIGFkZHM6XG4gKiAgLSBBIFBFUlNJU1RFTlQgc29ja2V0IGNvbm5lY3Rpb24gdG8gbXB2IChpbnN0ZWFkIG9mIGNvbm5lY3QtcGVyLWNvbW1hbmQpXG4gKiAgLSBvYnNlcnZlX3Byb3BlcnR5IHN1YnNjcmlwdGlvbnMgcHVzaGVkIHRvIGFsbCBicm93c2VyIGNsaWVudHMgaW4gcmVhbCB0aW1lXG4gKiAgLSBNdWx0aXBsZXhlZCByZXF1ZXN0L3Jlc3BvbnNlIG1hdGNoaW5nIHZpYSByZXF1ZXN0X2lkXG4gKiAgLSBtcHYgRU9GIC8gaWRsZSBkZXRlY3Rpb24g4oaSIGJyb2FkY2FzdHMgXCJtcHZfZW5kZWRcIiBldmVudFxuICpcbiAqIFVzYWdlOlxuICogICBkZW5vIHJ1biAtLWFsbG93LW5ldCAtLWFsbG93LXJlYWQgLS1hbGxvdy13cml0ZSB3c19icmlkZ2UudHNcbiAqXG4gKiBFbnYgdmFyczpcbiAqICAgV1NfUE9SVCAgICAgIFdlYlNvY2tldCBzZXJ2ZXIgcG9ydCAgKGRlZmF1bHQ6IDgwMDEpXG4gKiAgIE1QVl9TT0NLRVQgICBQYXRoIHRvIG1wdiBJUEMgc29ja2V0IChkZWZhdWx0OiAvdG1wL21wdnNvY2tldClcbiAqICAgUE9MTF9NUyAgICAgIFJlY29ubmVjdCAvIGhlYWx0aCBwb2xsIG1zIChkZWZhdWx0OiAxMDAwKVxuICovXG5cbmNvbnN0IFdTX1BPUlQgID0gcGFyc2VJbnQoRGVuby5lbnYuZ2V0KFwiV1NfUE9SVFwiKSAgPz8gXCI4MDAxXCIpO1xuY29uc3QgSVNfV0lORE9XUyA9IERlbm8uYnVpbGQub3MgPT09IFwid2luZG93c1wiO1xuY29uc3QgREVGQVVMVF9TT0NLRVQgPSBJU19XSU5ET1dTID8gU3RyaW5nLnJhd2BcXFxcLlxccGlwZVxcbXB2c29ja2V0YCA6IFwiL3RtcC9tcHZzb2NrZXRcIjtcbmNvbnN0IE1QVl9TT0NLID0gRGVuby5lbnYuZ2V0KFwiTVBWX1NPQ0tFVFwiKSA/PyBERUZBVUxUX1NPQ0tFVDtcbmNvbnN0IFBPTExfTVMgID0gcGFyc2VJbnQoRGVuby5lbnYuZ2V0KFwiUE9MTF9NU1wiKSAgID8/IFwiMTAwMFwiKTtcblxuLy8g4pSA4pSA4pSAIFR5cGVzIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgFxuXG5pbnRlcmZhY2UgTVBWRXZlbnQge1xuICBldmVudD86IHN0cmluZztcbiAgaWQ/OiBudW1iZXI7XG4gIGRhdGE/OiB1bmtub3duO1xuICBuYW1lPzogc3RyaW5nO1xuICByZXF1ZXN0X2lkPzogbnVtYmVyO1xuICBlcnJvcj86IHN0cmluZztcbn1cblxuaW50ZXJmYWNlIFBlbmRpbmdSZXF1ZXN0IHtcbiAgcmVzb2x2ZTogKHY6IHVua25vd24pID0+IHZvaWQ7XG4gIHJlamVjdDogIChlOiBFcnJvcikgICA9PiB2b2lkO1xuICB0aW1lcjogICBSZXR1cm5UeXBlPHR5cGVvZiBzZXRUaW1lb3V0Pjtcbn1cblxuLy8g4pSA4pSA4pSAIFBlcnNpc3RlbnQgTVBWIHNvY2tldCBjb25uZWN0aW9uIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgFxuXG5jbGFzcyBNUFZDb25uZWN0aW9uIHtcbiAgcHJpdmF0ZSBjb25uOiBEZW5vLkNvbm4gfCBudWxsID0gbnVsbDtcbiAgcHJpdmF0ZSBidWYgPSBcIlwiO1xuICBwcml2YXRlIHJlcUlkID0gMTAwO1xuICBwcml2YXRlIHBlbmRpbmcgPSBuZXcgTWFwPG51bWJlciwgUGVuZGluZ1JlcXVlc3Q+KCk7XG4gIHByaXZhdGUgX29uRXZlbnQ6ICgoZTogTVBWRXZlbnQpID0+IHZvaWQpIHwgbnVsbCA9IG51bGw7XG4gIHByaXZhdGUgX29uQ2xvc2U6ICgoKSA9PiB2b2lkKSB8IG51bGwgPSBudWxsO1xuICBwcml2YXRlIF9yZWFkaW5nID0gZmFsc2U7XG5cbiAgZ2V0IGNvbm5lY3RlZCgpIHsgcmV0dXJuIHRoaXMuY29ubiAhPT0gbnVsbDsgfVxuXG4gIG9uRXZlbnQoZm46IChlOiBNUFZFdmVudCkgPT4gdm9pZCkgeyB0aGlzLl9vbkV2ZW50ID0gZm47IH1cbiAgb25DbG9zZShmbjogKCkgPT4gdm9pZCkgICAgICAgICAgICAgeyB0aGlzLl9vbkNsb3NlID0gZm47IH1cblxuICBhc3luYyBjb25uZWN0KCk6IFByb21pc2U8Ym9vbGVhbj4ge1xuICAgIGlmICh0aGlzLmNvbm4pIHJldHVybiB0cnVlO1xuICAgIHRyeSB7XG4gICAgICAvLyBXaW5kb3dzIHVzZXMgbmFtZWQgcGlwZXMgKGNvbm5lY3RQaXBlKTsgVW5peCB1c2VzIFVuaXggc29ja2V0XG4gICAgICB0aGlzLmNvbm4gPSBJU19XSU5ET1dTXG4gICAgICAgID8gYXdhaXQgKERlbm8gYXMgdW5rbm93biBhcyB7Y29ubmVjdFBpcGU/OiAocDpzdHJpbmcpID0+IFByb21pc2U8RGVuby5Db25uPn0pLmNvbm5lY3RQaXBlPy4oTVBWX1NPQ0spID8/ICgoKSA9PiB7IHRocm93IG5ldyBFcnJvcihcImNvbm5lY3RQaXBlIG5vdCBhdmFpbGFibGVcIik7IH0pKClcbiAgICAgICAgOiBhd2FpdCBEZW5vLmNvbm5lY3QoeyBwYXRoOiBNUFZfU09DSywgdHJhbnNwb3J0OiBcInVuaXhcIiB9KTtcbiAgICAgIHRoaXMuX3N0YXJ0UmVhZCgpO1xuICAgICAgYXdhaXQgdGhpcy5fc3Vic2NyaWJlUHJvcGVydGllcygpO1xuICAgICAgcmV0dXJuIHRydWU7XG4gICAgfSBjYXRjaCB7XG4gICAgICB0aGlzLmNvbm4gPSBudWxsO1xuICAgICAgcmV0dXJuIGZhbHNlO1xuICAgIH1cbiAgfVxuXG4gIHByaXZhdGUgYXN5bmMgX3N1YnNjcmliZVByb3BlcnRpZXMoKSB7XG4gICAgY29uc3QgcHJvcHMgPSBbXCJ0aW1lLXBvc1wiLCBcInBhdXNlXCIsIFwiZHVyYXRpb25cIiwgXCJ2b2x1bWVcIixcbiAgICAgICAgICAgICAgICAgICBcImZ1bGxzY3JlZW5cIiwgXCJwZXJjZW50LXBvc1wiLCBcIm1lZGlhLXRpdGxlXCIsIFwiaWRsZS1hY3RpdmVcIl07XG4gICAgZm9yIChsZXQgaSA9IDA7IGkgPCBwcm9wcy5sZW5ndGg7IGkrKykge1xuICAgICAgYXdhaXQgdGhpcy5fc2VuZFJhdyh7IGNvbW1hbmQ6IFtcIm9ic2VydmVfcHJvcGVydHlcIiwgaSArIDEsIHByb3BzW2ldXSB9KTtcbiAgICB9XG4gIH1cblxuICBwcml2YXRlIGFzeW5jIF9zdGFydFJlYWQoKSB7XG4gICAgaWYgKHRoaXMuX3JlYWRpbmcpIHJldHVybjtcbiAgICB0aGlzLl9yZWFkaW5nID0gdHJ1ZTtcbiAgICBjb25zdCBkZWMgPSBuZXcgVGV4dERlY29kZXIoKTtcbiAgICBjb25zdCB0bXBCdWYgPSBuZXcgVWludDhBcnJheSg4MTkyKTtcbiAgICB0cnkge1xuICAgICAgd2hpbGUgKHRoaXMuY29ubikge1xuICAgICAgICBjb25zdCBuID0gYXdhaXQgdGhpcy5jb25uLnJlYWQodG1wQnVmKTtcbiAgICAgICAgaWYgKG4gPT09IG51bGwpIGJyZWFrO1xuICAgICAgICB0aGlzLmJ1ZiArPSBkZWMuZGVjb2RlKHRtcEJ1Zi5zdWJhcnJheSgwLCBuKSk7XG4gICAgICAgIGxldCBubDogbnVtYmVyO1xuICAgICAgICB3aGlsZSAoKG5sID0gdGhpcy5idWYuaW5kZXhPZihcIlxcblwiKSkgIT09IC0xKSB7XG4gICAgICAgICAgY29uc3QgbGluZSA9IHRoaXMuYnVmLnNsaWNlKDAsIG5sKS50cmltKCk7XG4gICAgICAgICAgdGhpcy5idWYgPSB0aGlzLmJ1Zi5zbGljZShubCArIDEpO1xuICAgICAgICAgIGlmICghbGluZSkgY29udGludWU7XG4gICAgICAgICAgdHJ5IHtcbiAgICAgICAgICAgIGNvbnN0IG1zZzogTVBWRXZlbnQgPSBKU09OLnBhcnNlKGxpbmUpO1xuICAgICAgICAgICAgdGhpcy5fZGlzcGF0Y2gobXNnKTtcbiAgICAgICAgICB9IGNhdGNoIHsgLyogbWFsZm9ybWVkIGxpbmUgKi8gfVxuICAgICAgICB9XG4gICAgICB9XG4gICAgfSBjYXRjaCB7IC8qIHNvY2tldCBjbG9zZWQgKi8gfVxuICAgIHRoaXMuX3JlYWRpbmcgPSBmYWxzZTtcbiAgICB0aGlzLmNvbm4gPSBudWxsO1xuICAgIHRoaXMuX29uQ2xvc2U/LigpO1xuICAgIC8vIFJlamVjdCBhbGwgcGVuZGluZyByZXF1ZXN0c1xuICAgIGZvciAoY29uc3QgWywgcF0gb2YgdGhpcy5wZW5kaW5nKSB7XG4gICAgICBjbGVhclRpbWVvdXQocC50aW1lcik7XG4gICAgICBwLnJlamVjdChuZXcgRXJyb3IoXCJtcHYgc29ja2V0IGNsb3NlZFwiKSk7XG4gICAgfVxuICAgIHRoaXMucGVuZGluZy5jbGVhcigpO1xuICB9XG5cbiAgcHJpdmF0ZSBfZGlzcGF0Y2gobXNnOiBNUFZFdmVudCkge1xuICAgIC8vIFByb3BlcnR5IGNoYW5nZSBldmVudCAocHVzaGVkIGJ5IG1wdilcbiAgICBpZiAobXNnLmV2ZW50ID09PSBcInByb3BlcnR5LWNoYW5nZVwiKSB7XG4gICAgICB0aGlzLl9vbkV2ZW50Py4obXNnKTtcbiAgICAgIHJldHVybjtcbiAgICB9XG4gICAgLy8gRW5kLW9mLWZpbGUgLyBpZGxlIGV2ZW50c1xuICAgIGlmIChtc2cuZXZlbnQgPT09IFwiZW5kLWZpbGVcIiB8fCBtc2cuZXZlbnQgPT09IFwiaWRsZVwiKSB7XG4gICAgICB0aGlzLl9vbkV2ZW50Py4obXNnKTtcbiAgICAgIHJldHVybjtcbiAgICB9XG4gICAgLy8gUmVzcG9uc2UgdG8gYSBjb21tYW5kIHdlIHNlbnRcbiAgICBpZiAobXNnLnJlcXVlc3RfaWQgIT09IHVuZGVmaW5lZCkge1xuICAgICAgY29uc3QgcCA9IHRoaXMucGVuZGluZy5nZXQobXNnLnJlcXVlc3RfaWQpO1xuICAgICAgaWYgKHApIHtcbiAgICAgICAgY2xlYXJUaW1lb3V0KHAudGltZXIpO1xuICAgICAgICB0aGlzLnBlbmRpbmcuZGVsZXRlKG1zZy5yZXF1ZXN0X2lkKTtcbiAgICAgICAgcC5yZXNvbHZlKG1zZyk7XG4gICAgICB9XG4gICAgICByZXR1cm47XG4gICAgfVxuICAgIC8vIE90aGVyIGV2ZW50cyAoZS5nLiBcInBsYXliYWNrLXJlc3RhcnRcIiwgXCJzZWVrXCIpXG4gICAgaWYgKG1zZy5ldmVudCkge1xuICAgICAgdGhpcy5fb25FdmVudD8uKG1zZyk7XG4gICAgfVxuICB9XG5cbiAgcHJpdmF0ZSBhc3luYyBfc2VuZFJhdyhwYXlsb2FkOiBvYmplY3QpOiBQcm9taXNlPHZvaWQ+IHtcbiAgICBpZiAoIXRoaXMuY29ubikgcmV0dXJuO1xuICAgIHRyeSB7XG4gICAgICBjb25zdCBkYXRhID0gbmV3IFRleHRFbmNvZGVyKCkuZW5jb2RlKEpTT04uc3RyaW5naWZ5KHBheWxvYWQpICsgXCJcXG5cIik7XG4gICAgICBhd2FpdCB0aGlzLmNvbm4ud3JpdGUoZGF0YSk7XG4gICAgfSBjYXRjaCB7IHRoaXMuY2xvc2UoKTsgfVxuICB9XG5cbiAgLyoqIFNlbmQgYSBjb21tYW5kIGFuZCBhd2FpdCB0aGUgcmVzcG9uc2UgKHdpdGggdGltZW91dCkuICovXG4gIGFzeW5jIGNvbW1hbmQoY21kOiB1bmtub3duW10sIHRpbWVvdXRNcyA9IDMwMDApOiBQcm9taXNlPE1QVkV2ZW50PiB7XG4gICAgaWYgKCF0aGlzLmNvbm4pIHRocm93IG5ldyBFcnJvcihcIm1wdiBub3QgY29ubmVjdGVkXCIpO1xuICAgIGNvbnN0IGlkID0gKyt0aGlzLnJlcUlkO1xuICAgIGNvbnN0IHBheWxvYWQgPSB7IGNvbW1hbmQ6IGNtZCwgcmVxdWVzdF9pZDogaWQgfTtcbiAgICByZXR1cm4gbmV3IFByb21pc2UoKHJlc29sdmUsIHJlamVjdCkgPT4ge1xuICAgICAgY29uc3QgdGltZXIgPSBzZXRUaW1lb3V0KCgpID0+IHtcbiAgICAgICAgdGhpcy5wZW5kaW5nLmRlbGV0ZShpZCk7XG4gICAgICAgIHJlamVjdChuZXcgRXJyb3IoXCJtcHYgY29tbWFuZCB0aW1lZCBvdXRcIikpO1xuICAgICAgfSwgdGltZW91dE1zKTtcbiAgICAgIHRoaXMucGVuZGluZy5zZXQoaWQsIHsgcmVzb2x2ZTogcmVzb2x2ZSBhcyAodjogdW5rbm93bikgPT4gdm9pZCwgcmVqZWN0LCB0aW1lciB9KTtcbiAgICAgIHRoaXMuX3NlbmRSYXcocGF5bG9hZCkuY2F0Y2goKGUpID0+IHtcbiAgICAgICAgdGhpcy5wZW5kaW5nLmRlbGV0ZShpZCk7XG4gICAgICAgIGNsZWFyVGltZW91dCh0aW1lcik7XG4gICAgICAgIHJlamVjdChlKTtcbiAgICAgIH0pO1xuICAgIH0pO1xuICB9XG5cbiAgY2xvc2UoKSB7XG4gICAgdHJ5IHsgdGhpcy5jb25uPy5jbG9zZSgpOyB9IGNhdGNoIHsgLyogaWdub3JlICovIH1cbiAgICB0aGlzLmNvbm4gPSBudWxsO1xuICB9XG59XG5cbi8vIOKUgOKUgOKUgCBHbG9iYWwgc3RhdGUg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAXG5cbmNvbnN0IG1wdiA9IG5ldyBNUFZDb25uZWN0aW9uKCk7XG5jb25zdCBjbGllbnRzID0gbmV3IFNldDxXZWJTb2NrZXQ+KCk7XG5sZXQgbGFzdFN0YXR1czogUmVjb3JkPHN0cmluZywgdW5rbm93bj4gPSB7IGNvbm5lY3RlZDogZmFsc2UgfTtcblxuZnVuY3Rpb24gYnJvYWRjYXN0KG1zZzogdW5rbm93bikge1xuICBjb25zdCB0ZXh0ID0gSlNPTi5zdHJpbmdpZnkobXNnKTtcbiAgZm9yIChjb25zdCB3cyBvZiBjbGllbnRzKSB7XG4gICAgaWYgKHdzLnJlYWR5U3RhdGUgPT09IFdlYlNvY2tldC5PUEVOKSB3cy5zZW5kKHRleHQpO1xuICB9XG59XG5cbi8vIOKUgOKUgOKUgCBtcHYgZXZlbnQg4oaSIGJyb3dzZXIgYnJvYWRjYXN0IOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgFxuXG5tcHYub25FdmVudCgoZSkgPT4ge1xuICBpZiAoZS5ldmVudCA9PT0gXCJwcm9wZXJ0eS1jaGFuZ2VcIikge1xuICAgIGNvbnN0IGtleSA9IGUubmFtZT8ucmVwbGFjZSgvLS9nLCBcIl9cIikgPz8gXCJ1bmtub3duXCI7XG4gICAgbGFzdFN0YXR1c1trZXldID0gZS5kYXRhO1xuICAgIGxhc3RTdGF0dXMuY29ubmVjdGVkID0gdHJ1ZTtcbiAgICBicm9hZGNhc3QoeyB0eXBlOiBcInByb3BlcnR5XCIsIG5hbWU6IGUubmFtZSwgdmFsdWU6IGUuZGF0YSwgc3RhdHVzOiBsYXN0U3RhdHVzIH0pO1xuICB9IGVsc2UgaWYgKGUuZXZlbnQgPT09IFwiZW5kLWZpbGVcIikge1xuICAgIGJyb2FkY2FzdCh7IHR5cGU6IFwibXB2X2VuZGVkXCIsIHJlYXNvbjogKGUgYXMgUmVjb3JkPHN0cmluZyx1bmtub3duPikucmVhc29uID8/IFwidW5rbm93blwiIH0pO1xuICB9IGVsc2UgaWYgKGUuZXZlbnQgPT09IFwiaWRsZVwiKSB7XG4gICAgYnJvYWRjYXN0KHsgdHlwZTogXCJtcHZfaWRsZVwiIH0pO1xuICB9IGVsc2Uge1xuICAgIGJyb2FkY2FzdCh7IHR5cGU6IFwibXB2X2V2ZW50XCIsIGV2ZW50OiBlLmV2ZW50LCBkYXRhOiBlIH0pO1xuICB9XG59KTtcblxubXB2Lm9uQ2xvc2UoKCkgPT4ge1xuICBsYXN0U3RhdHVzID0geyBjb25uZWN0ZWQ6IGZhbHNlIH07XG4gIGJyb2FkY2FzdCh7IHR5cGU6IFwic3RhdHVzXCIsIGRhdGE6IGxhc3RTdGF0dXMgfSk7XG4gIGNvbnNvbGUubG9nKFwiW2JyaWRnZV0gbXB2IHNvY2tldCBjbG9zZWRcIik7XG59KTtcblxuLy8g4pSA4pSA4pSAIFJlY29ubmVjdCBsb29wIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgFxuXG5hc3luYyBmdW5jdGlvbiByZWNvbm5lY3RMb29wKCkge1xuICB3aGlsZSAodHJ1ZSkge1xuICAgIGlmICghbXB2LmNvbm5lY3RlZCkge1xuICAgICAgY29uc3Qgb2sgPSBhd2FpdCBtcHYuY29ubmVjdCgpO1xuICAgICAgaWYgKG9rKSB7XG4gICAgICAgIGNvbnNvbGUubG9nKFwiW2JyaWRnZV0gY29ubmVjdGVkIHRvIG1wdiBJUEMgc29ja2V0XCIpO1xuICAgICAgICBsYXN0U3RhdHVzID0geyBjb25uZWN0ZWQ6IHRydWUgfTtcbiAgICAgICAgYnJvYWRjYXN0KHsgdHlwZTogXCJzdGF0dXNcIiwgZGF0YTogbGFzdFN0YXR1cyB9KTtcbiAgICAgIH1cbiAgICB9XG4gICAgYXdhaXQgbmV3IFByb21pc2UoKHIpID0+IHNldFRpbWVvdXQociwgUE9MTF9NUykpO1xuICB9XG59XG5cbi8vIOKUgOKUgOKUgCBXZWJTb2NrZXQgc2VydmVyIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgFxuXG5EZW5vLnNlcnZlKHsgcG9ydDogV1NfUE9SVCB9LCAocmVxKSA9PiB7XG4gIGlmIChyZXEubWV0aG9kID09PSBcIk9QVElPTlNcIikge1xuICAgIHJldHVybiBuZXcgUmVzcG9uc2UobnVsbCwge1xuICAgICAgaGVhZGVyczoge1xuICAgICAgICBcIkFjY2Vzcy1Db250cm9sLUFsbG93LU9yaWdpblwiOiBcIipcIixcbiAgICAgICAgXCJBY2Nlc3MtQ29udHJvbC1BbGxvdy1NZXRob2RzXCI6IFwiR0VULCBPUFRJT05TXCIsXG4gICAgICAgIFwiQWNjZXNzLUNvbnRyb2wtQWxsb3ctSGVhZGVyc1wiOiBcIipcIixcbiAgICAgIH0sXG4gICAgfSk7XG4gIH1cblxuICBjb25zdCB1cmwgPSBuZXcgVVJMKHJlcS51cmwpO1xuXG4gIGlmICh1cmwucGF0aG5hbWUgPT09IFwiL2hlYWx0aFwiKSB7XG4gICAgcmV0dXJuIG5ldyBSZXNwb25zZShcbiAgICAgIEpTT04uc3RyaW5naWZ5KHsgc3RhdHVzOiBcIm9rXCIsIG1wdl9jb25uZWN0ZWQ6IG1wdi5jb25uZWN0ZWQsIGNsaWVudHM6IGNsaWVudHMuc2l6ZSB9KSxcbiAgICAgIHsgaGVhZGVyczogeyBcIkNvbnRlbnQtVHlwZVwiOiBcImFwcGxpY2F0aW9uL2pzb25cIiB9IH1cbiAgICApO1xuICB9XG5cbiAgaWYgKHJlcS5oZWFkZXJzLmdldChcInVwZ3JhZGVcIikgIT09IFwid2Vic29ja2V0XCIpIHtcbiAgICByZXR1cm4gbmV3IFJlc3BvbnNlKFwiQm9yZ29yVHViZSBNUFYgQnJpZGdlIOKAkyBjb25uZWN0IHZpYSBXZWJTb2NrZXRcIiwgeyBzdGF0dXM6IDQyNiB9KTtcbiAgfVxuXG4gIGNvbnN0IHsgc29ja2V0OiB3cywgcmVzcG9uc2UgfSA9IERlbm8udXBncmFkZVdlYlNvY2tldChyZXEpO1xuXG4gIHdzLm9ub3BlbiA9ICgpID0+IHtcbiAgICBjbGllbnRzLmFkZCh3cyk7XG4gICAgd3Muc2VuZChKU09OLnN0cmluZ2lmeSh7XG4gICAgICB0eXBlOiBcImhlbGxvXCIsXG4gICAgICBicmlkZ2U6IFwiQm9yZ29yVHViZSBNUFYgQnJpZGdlIHYyIChQaGFzZSAzKVwiLFxuICAgICAgcG9ydDogV1NfUE9SVCxcbiAgICAgIG1wdl9jb25uZWN0ZWQ6IG1wdi5jb25uZWN0ZWQsXG4gICAgfSkpO1xuICAgIC8vIEltbWVkaWF0ZWx5IHB1c2ggY3VycmVudCBzdGF0dXNcbiAgICB3cy5zZW5kKEpTT04uc3RyaW5naWZ5KHsgdHlwZTogXCJzdGF0dXNcIiwgZGF0YTogeyAuLi5sYXN0U3RhdHVzLCBjb25uZWN0ZWQ6IG1wdi5jb25uZWN0ZWQgfSB9KSk7XG4gICAgY29uc29sZS5sb2coYFticmlkZ2VdIGNsaWVudCBjb25uZWN0ZWQgKHRvdGFsOiAke2NsaWVudHMuc2l6ZX0pYCk7XG4gIH07XG5cbiAgd3Mub25jbG9zZSA9ICgpID0+IHtcbiAgICBjbGllbnRzLmRlbGV0ZSh3cyk7XG4gICAgY29uc29sZS5sb2coYFticmlkZ2VdIGNsaWVudCBkaXNjb25uZWN0ZWQgKHRvdGFsOiAke2NsaWVudHMuc2l6ZX0pYCk7XG4gIH07XG5cbiAgd3Mub25lcnJvciA9ICgpID0+IGNsaWVudHMuZGVsZXRlKHdzKTtcblxuICB3cy5vbm1lc3NhZ2UgPSBhc3luYyAoZXZ0KSA9PiB7XG4gICAgbGV0IG1zZzogUmVjb3JkPHN0cmluZywgdW5rbm93bj47XG4gICAgdHJ5IHsgbXNnID0gSlNPTi5wYXJzZShldnQuZGF0YSk7IH1cbiAgICBjYXRjaCB7IHdzLnNlbmQoSlNPTi5zdHJpbmdpZnkoeyB0eXBlOiBcImVycm9yXCIsIGRldGFpbDogXCJpbnZhbGlkIEpTT05cIiB9KSk7IHJldHVybjsgfVxuXG4gICAgY29uc3QgYWN0aW9uID0gbXNnLmFjdGlvbiBhcyBzdHJpbmc7XG5cbiAgICBzd2l0Y2ggKGFjdGlvbikge1xuICAgICAgY2FzZSBcImlwY1wiOiB7XG4gICAgICAgIGNvbnN0IGNtZCA9IG1zZy5jb21tYW5kIGFzIHVua25vd25bXTtcbiAgICAgICAgaWYgKCFjbWQpIHsgd3Muc2VuZChKU09OLnN0cmluZ2lmeSh7IHR5cGU6IFwiZXJyb3JcIiwgZGV0YWlsOiBcImNvbW1hbmQgcmVxdWlyZWRcIiB9KSk7IHJldHVybjsgfVxuICAgICAgICBpZiAoIW1wdi5jb25uZWN0ZWQpIHtcbiAgICAgICAgICB3cy5zZW5kKEpTT04uc3RyaW5naWZ5KHsgdHlwZTogXCJpcGNfcmVzcG9uc2VcIiwgcmVxdWVzdF9pZDogbXNnLnJlcXVlc3RfaWQsIGRhdGE6IHsgZXJyb3I6IFwibXB2IG5vdCBjb25uZWN0ZWRcIiB9IH0pKTtcbiAgICAgICAgICByZXR1cm47XG4gICAgICAgIH1cbiAgICAgICAgdHJ5IHtcbiAgICAgICAgICBjb25zdCByZXN1bHQgPSBhd2FpdCBtcHYuY29tbWFuZChjbWQpO1xuICAgICAgICAgIHdzLnNlbmQoSlNPTi5zdHJpbmdpZnkoeyB0eXBlOiBcImlwY19yZXNwb25zZVwiLCByZXF1ZXN0X2lkOiBtc2cucmVxdWVzdF9pZCwgZGF0YTogcmVzdWx0IH0pKTtcbiAgICAgICAgfSBjYXRjaCAoZSkge1xuICAgICAgICAgIHdzLnNlbmQoSlNPTi5zdHJpbmdpZnkoeyB0eXBlOiBcImlwY19yZXNwb25zZVwiLCByZXF1ZXN0X2lkOiBtc2cucmVxdWVzdF9pZCwgZGF0YTogeyBlcnJvcjogU3RyaW5nKGUpIH0gfSkpO1xuICAgICAgICB9XG4gICAgICAgIGJyZWFrO1xuICAgICAgfVxuICAgICAgY2FzZSBcImdldF9zdGF0dXNcIjpcbiAgICAgICAgd3Muc2VuZChKU09OLnN0cmluZ2lmeSh7IHR5cGU6IFwic3RhdHVzXCIsIGRhdGE6IHsgLi4ubGFzdFN0YXR1cywgY29ubmVjdGVkOiBtcHYuY29ubmVjdGVkIH0gfSkpO1xuICAgICAgICBicmVhaztcbiAgICAgIGNhc2UgXCJwaW5nXCI6XG4gICAgICAgIHdzLnNlbmQoSlNPTi5zdHJpbmdpZnkoeyB0eXBlOiBcInBvbmdcIiB9KSk7XG4gICAgICAgIGJyZWFrO1xuICAgICAgZGVmYXVsdDpcbiAgICAgICAgd3Muc2VuZChKU09OLnN0cmluZ2lmeSh7IHR5cGU6IFwiZXJyb3JcIiwgZGV0YWlsOiBgdW5rbm93biBhY3Rpb246ICR7YWN0aW9ufWAgfSkpO1xuICAgIH1cbiAgfTtcblxuICByZXR1cm4gcmVzcG9uc2U7XG59KTtcblxuY29uc29sZS5sb2coYFtCb3Jnb3JUdWJlIE1QViBCcmlkZ2UgdjJdIHdzOi8vMC4wLjAuMDoke1dTX1BPUlR9YCk7XG5jb25zb2xlLmxvZyhgW0JvcmdvclR1YmUgTVBWIEJyaWRnZSB2Ml0gbXB2IHNvY2tldDogJHtNUFZfU09DS31gKTtcbmNvbnNvbGUubG9nKGBbQm9yZ29yVHViZSBNUFYgQnJpZGdlIHYyXSByZWNvbm5lY3QgcG9sbDogJHtQT0xMX01TfW1zYCk7XG5yZWNvbm5lY3RMb29wKCk7XG4iXSwibmFtZXMiOltdLCJtYXBwaW5ncyI6IkFBQUE7Ozs7Ozs7Ozs7Ozs7Ozs7OztDQWtCQyxHQUVELE1BQU0sVUFBVyxTQUFTLEtBQUssR0FBRyxDQUFDLEdBQUcsQ0FBQyxjQUFlO0FBQ3RELE1BQU0sYUFBYSxLQUFLLEtBQUssQ0FBQyxFQUFFLEtBQUs7QUFDckMsTUFBTSxpQkFBaUIsYUFBYSxPQUFPLEdBQUcsQ0FBQyxrQkFBa0IsQ0FBQyxHQUFHO0FBQ3JFLE1BQU0sV0FBVyxLQUFLLEdBQUcsQ0FBQyxHQUFHLENBQUMsaUJBQWlCO0FBQy9DLE1BQU0sVUFBVyxTQUFTLEtBQUssR0FBRyxDQUFDLEdBQUcsQ0FBQyxj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
// denoCacheMetadata=12099297555592302428,844007014932987284