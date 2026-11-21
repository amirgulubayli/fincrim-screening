import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { Terminal as XTerm, type ITheme } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "./terminal.css";

/**
 * The embedded `screen` CLI: xterm.js over the /api/terminal WebSocket. The server runs only whitelisted
 * `screen ...` commands (no shell) and streams their colour output; `explain` answers instantly from the
 * server's loaded engine run.
 *
 * Other UI (a guided tour, a button on a case) can run a command with:
 *   runScreenCommand("screen explain H-58A39C")            // or
 *   window.dispatchEvent(new CustomEvent("screen:run", { detail: "screen eval --compare" }))
 * and hear back via the "screen:result" window event ({ command, exitCode }).
 */

export const SCREEN_RUN_EVENT = "screen:run";
export const SCREEN_RESULT_EVENT = "screen:result";

export type ScreenRunDetail = string | { command: string; typed?: boolean };

export function runScreenCommand(command: string, opts: { typed?: boolean } = {}): void {
  window.dispatchEvent(new CustomEvent<ScreenRunDetail>(SCREEN_RUN_EVENT, { detail: { command, typed: opts.typed ?? true } }));
}

export type DemoItem = { command: string; note: string };
export const DEMO_GROUPS: { title: string; items: DemoItem[] }[] = [
  {
    title: "Proof",
    items: [
      { command: "screen summary", note: "dataset and decision counts" },
      { command: "screen eval --compare", note: "core vs check vs combined, live (~75 s)" },
      { command: "screen repro --runs 5", note: "5 fresh processes, byte-identical? (~6 min)" },
      { command: "screen diff", note: "where the two engines disagree" },
    ],
  },
  {
    title: "Explain a hit",
    items: [
      { command: "screen explain H-58A39C", note: "cleared" },
      { command: "screen explain H-4D76E3", note: "match" },
      { command: "screen explain H-B1018D", note: "review" },
      { command: "screen explain H-373F2A", note: "engines disagree" },
    ],
  },
  {
    title: "Queues & policy",
    items: [
      { command: "screen queue --tier BORDERLINE --limit 5", note: "4-eyes queue" },
      { command: "screen policy", note: "policy registry" },
    ],
  },
];
export const DEMO_COMMANDS = DEMO_GROUPS.flatMap((g) => g.items.map((i) => i.command));

type Meta = {
  engine_version: string;
  data: string;
  ready: boolean;
  status: string;
  subcommands: string[];
  flags: Record<string, string[]>;
  tiers: string[];
  engines: string[];
  hit_ids: string[];
  demo: string[];
  timeout_seconds: number;
};

type Conn = "connecting" | "open" | "closed";
type Props = {
  open?: boolean;
  selected?: string | null;
  onCommand?: (command: string) => void;
  initialCommand?: string;
};

const FONT = '"JetBrains Mono", "Cascadia Code", "Cascadia Mono", "Fira Code", "SF Mono", Menlo, Consolas, "DejaVu Sans Mono", monospace';
const PROMPT = "\x1b[32mcompliance\x1b[90m@\x1b[36mscreen\x1b[90m $\x1b[0m ";
const DONE = "\x00done\r\n";
const HISTORY_KEY = "screen-terminal-history";
const PSEUDO = ["help", "clear"];

const DARK: ITheme = {
  background: "#0b1016", foreground: "#e6edf3", cursor: "#58a6ff", cursorAccent: "#0b1016",
  selectionBackground: "#264f7899", selectionForeground: "#ffffff",
  black: "#0b1016", red: "#f85149", green: "#3fb950", yellow: "#d29922", blue: "#388bfd", magenta: "#bc8cff",
  cyan: "#39c5cf", white: "#b1bac4", brightBlack: "#7d8590", brightRed: "#ffa198", brightGreen: "#56d364",
  brightYellow: "#e3b341", brightBlue: "#79c0ff", brightMagenta: "#d2a8ff", brightCyan: "#56d4dd", brightWhite: "#ffffff",
};
const LIGHT: ITheme = {
  background: "#fbfcfd", foreground: "#1f2328", cursor: "#0969da", cursorAccent: "#fbfcfd",
  selectionBackground: "#0969da33", selectionForeground: "#1f2328",
  black: "#1f2328", red: "#cf222e", green: "#1a7f37", yellow: "#9a6700", blue: "#0969da", magenta: "#8250df",
  cyan: "#1b7c83", white: "#6e7781", brightBlack: "#6e7781", brightRed: "#a40e26", brightGreen: "#116329",
  brightYellow: "#7d4e00", brightBlue: "#218bff", brightMagenta: "#a475f9", brightCyan: "#3192aa", brightWhite: "#ffffff",
};

function pageTheme(): "light" | "dark" {
  const t = document.documentElement.dataset.theme;
  return t === "light" ? "light" : "dark"; // a terminal is dark unless the page says otherwise
}

// Welcome banner: two lines here + the server's one-line status = three lines, ASCII only.
const BANNER =
  "\x1b[1mscreen\x1b[0m\x1b[90m - the real, deterministic screening CLI (python system/screen.py) on the server: no model, no network.\x1b[0m\r\n" +
  "\x1b[90mRun a demo command from the menu, or type \x1b[0mhelp\x1b[90m. Tab completes, Up/Down history, Ctrl+C stops, Ctrl+L clears.\x1b[0m\r\n";

/** Fixed position for the demo menu (escapes the footer's clipping): below the trigger if it fits, else above. */
function menuPlacement(trigger: HTMLElement): CSSProperties {
  const r = trigger.getBoundingClientRect();
  const right = Math.max(8, window.innerWidth - r.right);
  const below = window.innerHeight - r.bottom - 12;
  if (below >= 380) return { position: "fixed", top: r.bottom + 6, right, maxHeight: below };
  return { position: "fixed", bottom: window.innerHeight - r.top + 6, right, maxHeight: Math.max(200, r.top - 12) };
}

function loadHistory(): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list.filter((x) => typeof x === "string").slice(-100) : [];
  } catch {
    return [];
  }
}

function saveHistory(list: string[]) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(-100)));
  } catch {
    /* private window: history lives for this page only */
  }
}

function commonPrefix(words: string[]): string {
  if (!words.length) return "";
  let p = words[0];
  for (const w of words) while (!w.startsWith(p)) p = p.slice(0, -1);
  return p;
}

/** Candidates for the token under the cursor (the end of the line). */
function candidates(line: string, meta: Meta | null): { start: number; options: string[] } {
  const start = line.lastIndexOf(" ") + 1;
  const cur = line.slice(start);
  const before = line.slice(0, start).trim().split(/\s+/).filter(Boolean);
  const subs = meta?.subcommands ?? ["diff", "eval", "explain", "policy", "queue", "repro", "run", "summary"];
  let pool: string[];
  if (before.length === 0) pool = ["screen", ...PSEUDO, ...subs];
  else {
    const words = before[0] === "screen" ? before.slice(1) : before;
    const sub = words[0];
    const prev = words[words.length - 1];
    if (words.length === 0) pool = subs;
    else if (prev === "--tier") pool = meta?.tiers ?? ["AUTO_CLEAR", "AUTO_ESCALATE", "BORDERLINE", "HUMAN_REVIEW"];
    else if (prev === "--engine") pool = meta?.engines ?? ["combined", "core", "check"];
    else if (prev === "--pair") pool = ["D:R", "S:R", "D:S", "R:D", "R:S", "S:D"];
    else if (cur.startsWith("-")) pool = meta?.flags?.[sub] ?? ["--json"];
    else if (sub === "explain" && words.length === 1) pool = meta?.hit_ids ?? [];
    else pool = meta?.flags?.[sub] ?? [];
  }
  const up = cur.toUpperCase();
  const options = pool.filter((o) => o.startsWith(cur) || (o.startsWith("H-") && o.startsWith(up)));
  return { start, options };
}

export default function Terminal({ open = true, selected = null, onCommand, initialCommand }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const runRef = useRef<((cmd: string, typed: boolean) => void) | null>(null);
  const stopRef = useRef<(() => void) | null>(null);
  const clearRef = useRef<(() => void) | null>(null);
  const metaRef = useRef<Meta | null>(null);
  const onCommandRef = useRef(onCommand);
  onCommandRef.current = onCommand;
  const initialRef = useRef(initialCommand);

  const [conn, setConn] = useState<Conn>("connecting");
  const [busy, setBusy] = useState(true);
  const [running, setRunning] = useState<{ cmd: string; t0: number } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">(pageTheme);
  const [expanded, setExpanded] = useState(false);
  const fontSize = 13;
  const [flash, setFlash] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<CSSProperties>({});
  const menuRef = useRef<HTMLDivElement>(null);

  // ---- terminal + socket (once)
  useEffect(() => {
    const term = new XTerm({
      fontFamily: FONT,
      fontSize: 13,
      lineHeight: 1.15,
      cursorBlink: true,
      cursorStyle: "bar",
      convertEol: true,
      scrollback: 5000,
      minimumContrastRatio: 4.5,
      allowProposedApi: false,
      theme: pageTheme() === "light" ? LIGHT : DARK,
    });
    const fit = new FitAddon();
    termRef.current = term;
    fitRef.current = fit;
    term.loadAddon(fit);
    term.open(host.current!);
    try { fit.fit(); } catch { /* hidden on mount */ }
    term.write(BANNER);

    let disposed = false;
    let ws: WebSocket | null = null;
    let line = "";
    let isBusy = true;
    let typing = false;
    let current = "";
    let lastExit: number | null = null;
    let retry = 0;
    let everConnected = false;
    const pending: { cmd: string; typed: boolean }[] = [];
    const history = loadHistory();
    let hIdx = history.length;

    const setB = (b: boolean) => { isBusy = b; setBusy(b); };
    const prompt = () => term.write(PROMPT + line);
    const redraw = () => term.write("\r\x1b[2K" + PROMPT + line);
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

    const finishCommand = () => {
      if (current) {
        window.dispatchEvent(new CustomEvent(SCREEN_RESULT_EVENT, { detail: { command: current, exitCode: lastExit } }));
      }
      current = "";
      lastExit = null;
      setRunning(null);
    };

    const drain = () => {
      const next = pending.shift();
      if (next) setTimeout(() => runRef.current?.(next.cmd, next.typed), 120);
    };

    const send = (cmd: string) => {
      const trimmed = cmd.trim();
      if (trimmed && history[history.length - 1] !== trimmed) {
        history.push(trimmed);
        saveHistory(history);
      }
      hIdx = history.length;
      line = "";
      if (!trimmed) { prompt(); return; }
      if (trimmed === "clear" || trimmed === "cls") {
        term.clear();
        term.write("\x1b[2K\r");
        prompt();
        return;
      }
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        term.write("\x1b[33m[not connected - the command will run when the server is back]\x1b[0m\r\n");
        pending.push({ cmd: trimmed, typed: false });
        return;
      }
      current = trimmed;
      setB(true);
      setRunning({ cmd: trimmed, t0: performance.now() });
      onCommandRef.current?.(trimmed);
      sendCols();
      ws.send(trimmed);
    };

    const typeAndRun = async (cmd: string, typed: boolean) => {
      if (isBusy || typing) { pending.push({ cmd, typed }); return; }
      term.focus();
      if (!typed) { line = cmd; redraw(); term.write("\r\n"); send(cmd); return; }
      typing = true;
      line = "";
      redraw();
      // Paced by the clock, not by timer ticks: a throttled tab still finishes in about a second.
      const msPerChar = 28;
      const t0 = performance.now();
      while (line.length < cmd.length) {
        await sleep(16);
        if (disposed) return;
        const n = Math.min(cmd.length, Math.floor((performance.now() - t0) / msPerChar) + 1);
        if (n > line.length) {
          term.write(cmd.slice(line.length, n));
          line = cmd.slice(0, n);
        }
      }
      await sleep(220);
      typing = false;
      term.write("\r\n");
      send(cmd);
    };
    runRef.current = (cmd, typed) => { void typeAndRun(cmd, typed); };
    stopRef.current = () => {
      pending.length = 0;
      if (isBusy && ws?.readyState === WebSocket.OPEN) ws.send("\x03");
    };
    clearRef.current = () => {
      term.clear();
      if (!isBusy && !typing) redraw();
    };

    let sentCols = 0;
    const sendCols = () => {
      // case cards wrap to the visible width (the server clamps it to 60..100)
      if (ws?.readyState === WebSocket.OPEN && term.cols && term.cols !== sentCols) {
        sentCols = term.cols;
        ws.send(`\x01cols:${term.cols}`);
      }
    };

    const connect = () => {
      if (disposed) return;
      setConn("connecting");
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const sock = new WebSocket(`${proto}://${location.host}/api/terminal`);
      ws = sock;
      sock.onopen = () => { retry = 0; everConnected = true; setConn("open"); sendCols(); };
      sock.onmessage = (ev) => {
        const text = String(ev.data);
        const m = /exit (-?\d+)/.exec(text.replace(/\x1b\[[0-9;]*m/g, ""));
        if (m) lastExit = Number(m[1]);
        if (text.includes(DONE)) {
          term.write(text.replace(DONE, ""));
          const wasRunning = current !== "";
          finishCommand();
          setB(false);
          prompt();
          if (!wasRunning && initialRef.current) {
            const first = initialRef.current;
            initialRef.current = undefined;
            pending.unshift({ cmd: first, typed: true });
          }
          drain();
          return;
        }
        term.write(text);
      };
      sock.onclose = () => {
        if (disposed) return;
        setConn("closed");
        if (current) { term.write("\r\n\x1b[31m[connection lost while running]\x1b[0m\r\n"); finishCommand(); }
        if (!isBusy) term.write("\r\x1b[2K");
        setB(true);
        const wait = Math.min(8000, 800 * 2 ** retry++);
        term.write(`\x1b[90m[${everConnected ? "disconnected" : "server not reachable"} - reconnecting in ${Math.round(wait / 1000)}s]\x1b[0m\r\n`);
        setTimeout(connect, wait);
      };
    };
    connect();

    const complete = () => {
      const { start, options } = candidates(line, metaRef.current);
      if (!options.length) { term.write("\x07"); return; }
      if (options.length === 1) {
        line = line.slice(0, start) + options[0] + " ";
        redraw();
        return;
      }
      const prefix = commonPrefix(options);
      if (prefix.length >= line.length - start && prefix !== line.slice(start)) {
        line = line.slice(0, start) + prefix;
        redraw();
        return;
      }
      const shown = options.slice(0, 48);
      const width = Math.max(...shown.map((o) => o.length)) + 2;
      const perRow = Math.max(1, Math.floor((term.cols - 2) / width));
      let out = "\r\n";
      shown.forEach((o, i) => { out += `\x1b[36m${o.padEnd(width)}\x1b[0m` + ((i + 1) % perRow === 0 ? "\r\n" : ""); });
      if (shown.length % perRow !== 0) out += "\r\n";
      if (options.length > shown.length) out += `\x1b[90m... ${options.length - shown.length} more - type more characters\x1b[0m\r\n`;
      term.write(out);
      prompt();
    };

    term.attachCustomKeyEventHandler((e) => {
      if (e.type !== "keydown") return true;
      if (e.ctrlKey && (e.key === "l" || e.key === "L")) { clearRef.current?.(); e.preventDefault(); return false; }
      if (e.key === "Tab") { e.preventDefault(); if (!isBusy && !typing) complete(); return false; }
      if (e.ctrlKey && (e.key === "c" || e.key === "C") && term.hasSelection()) return false; // let the browser copy
      return true;
    });

    term.onData((data) => {
      if (data === "\x03") {
        if (isBusy && current) {
          pending.length = 0;
          term.write("^C");
          ws?.send("\x03");
        } else if (!isBusy && !typing) {
          term.write("^C\r\n");
          line = "";
          prompt();
        }
        return;
      }
      if (isBusy || typing) return;
      if (data.startsWith("\x1b")) {
        if (data === "\x1b[A" && hIdx > 0) { line = history[--hIdx]; redraw(); }
        else if (data === "\x1b[B" && hIdx < history.length) { line = history[++hIdx] ?? ""; redraw(); }
        return;
      }
      for (const ch of data) {
        if (ch === "\r") {
          term.write("\r\n");
          send(line);
          if (isBusy) return;
        } else if (ch === "\x7f" || ch === "\b") {
          if (line.length) { line = line.slice(0, -1); term.write("\b \b"); }
        } else if (ch === "\x15") { // Ctrl+U
          line = "";
          redraw();
        } else if (ch >= " ") {
          line += ch;
          term.write(ch);
        }
      }
    });

    term.onSelectionChange(() => {
      const sel = term.getSelection();
      if (sel && sel.trim() && navigator.clipboard) {
        navigator.clipboard.writeText(sel).then(() => {
          setFlash("copied");
          setTimeout(() => setFlash(null), 1200);
        }).catch(() => undefined);
      }
    });

    const onRun = (ev: Event) => {
      const d = (ev as CustomEvent<ScreenRunDetail>).detail;
      const cmd = typeof d === "string" ? d : d?.command;
      if (cmd) runRef.current?.(cmd, typeof d === "string" ? true : d.typed !== false);
    };
    window.addEventListener(SCREEN_RUN_EVENT, onRun);

    const ro = new ResizeObserver(() => {
      try { fit.fit(); } catch { /* hidden */ }
      if (!isBusy) sendCols();
    });
    ro.observe(host.current!);

    return () => {
      disposed = true;
      window.removeEventListener(SCREEN_RUN_EVENT, onRun);
      ro.disconnect();
      runRef.current = null;
      stopRef.current = null;
      clearRef.current = null;
      ws?.close();
      term.dispose();
      termRef.current = null;
    };
  }, []);

  // ---- meta: data path, readiness, hit ids for Tab completion
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const r = await fetch("/api/terminal/meta");
        if (r.ok) {
          const m = (await r.json()) as Meta;
          metaRef.current = m;
          if (!stop) setMeta(m);
          if (m.ready) return;
        }
      } catch { /* server not up yet */ }
      if (!stop) timer = setTimeout(load, 4000);
    };
    void load();
    return () => { stop = true; clearTimeout(timer); };
  }, []);

  // ---- theme follows <html data-theme>
  useEffect(() => {
    const obs = new MutationObserver(() => setTheme(pageTheme()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  useEffect(() => {
    if (termRef.current) termRef.current.options.theme = theme === "light" ? LIGHT : DARK;
  }, [theme]);

  // ---- font size / expanded / visibility -> refit
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    term.options.fontSize = expanded ? fontSize + 2 : fontSize;
    const t = setTimeout(() => { try { fitRef.current?.fit(); } catch { /* hidden */ } }, 30);
    return () => clearTimeout(t);
  }, [fontSize, expanded, open]);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setExpanded(false); };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [expanded]);

  // ---- elapsed timer while a command runs
  useEffect(() => {
    if (!running) { setElapsed(0); return; }
    const id = setInterval(() => setElapsed((performance.now() - running.t0) / 1000), 200);
    return () => clearInterval(id);
  }, [running]);

  const run = useCallback((cmd: string) => {
    setMenuOpen(false);
    runRef.current?.(cmd, true);
  }, []);

  // ---- demo menu: close on outside click / Escape
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); setMenuOpen(false); }
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [menuOpen]);

  const groups = selected && !DEMO_COMMANDS.includes(`screen explain ${selected}`)
    ? [{ title: "Selected in the queue", items: [{ command: `screen explain ${selected}`, note: "instant" }] }, ...DEMO_GROUPS]
    : DEMO_GROUPS;
  const dot = conn !== "open" ? conn : busy ? "busy" : "ready";
  const status =
    conn === "closed" ? "reconnecting..." :
    conn === "connecting" ? "connecting..." :
    running ? `${running.cmd.replace(/^screen /, "")} - ${elapsed.toFixed(1)}s` :
    flash ?? (meta && !meta.ready ? "engine loading" : "");

  return (
    <div className={`scr-term${expanded ? " scr-expanded" : ""}`} data-theme={theme}>
      {expanded && <div className="scr-backdrop" onClick={() => setExpanded(false)} />}
      <div className="scr-shell">
        <div className="scr-titlebar">
          <span className={`scr-dot scr-dot-${dot}`} title={`connection: ${conn}${meta ? ` - engine ${meta.engine_version}` : ""}`} />
          <span className="scr-title">screen <span className="scr-dim">- deterministic CLI</span></span>
          {meta && <span className="scr-path" title="engine data folder (--data defaults to it)">{meta.data}</span>}
          <span className="scr-grow" />
          {status && <span className={`scr-status${running ? " scr-status-busy" : ""}`}>{status}</span>}
          {running ? (
            <button className="scr-btn scr-stop" onClick={() => stopRef.current?.()} title="Stop the running command (Ctrl+C)">
              stop
            </button>
          ) : (
            <div className="scr-menu" ref={menuRef}>
              <button
                className="scr-btn scr-menu-trigger"
                disabled={busy}
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                onClick={(e) => { setMenuPos(menuPlacement(e.currentTarget)); setMenuOpen((o) => !o); }}
              >
                Run a demo command <span aria-hidden="true">&#9662;</span>
              </button>
              {menuOpen && (
                <div className="scr-menu-list" role="menu" style={menuPos}>
                  {groups.map((g) => (
                    <div key={g.title} className="scr-menu-group" role="group" aria-label={g.title}>
                      <div className="scr-menu-title">{g.title}</div>
                      {g.items.map((it) => (
                        <button key={it.command} role="menuitem" className="scr-menu-item" onClick={() => run(it.command)}>
                          <span className="scr-menu-cmd">{it.command.replace(/^screen /, "")}</span>
                          <span className="scr-menu-note">{it.note}</span>
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <button
            className="scr-btn scr-ghost"
            onClick={() => setExpanded((x) => !x)}
            title={expanded ? "Restore (Esc)" : "Expand for the audience"}
            aria-label={expanded ? "Restore terminal size" : "Expand terminal"}
          >
            {expanded ? "restore" : "expand"}
          </button>
        </div>
        <div className="scr-host" ref={host} onClick={() => termRef.current?.focus()} />
      </div>
    </div>
  );
}
