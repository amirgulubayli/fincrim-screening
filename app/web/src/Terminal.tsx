import { useEffect, useRef, useState } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";

const PROMPT = "\x1b[36m$\x1b[0m ";

export default function Terminal({ open, selected }: { open: boolean; selected: string | null }) {
  const host = useRef<HTMLDivElement>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const runRef = useRef<((cmd: string) => void) | null>(null);
  const stopRef = useRef<(() => void) | null>(null);
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    const term = new XTerm({
      fontFamily: "ui-monospace, Consolas, monospace",
      fontSize: 13,
      cursorBlink: true,
      convertEol: true,
      theme: { background: "#0f1419", foreground: "#d6dde6" },
    });
    const fit = new FitAddon();
    fitRef.current = fit;
    term.loadAddon(fit);
    term.open(host.current!);
    fit.fit();

    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/terminal`);
    let line = "";
    let isBusy = true;
    const setB = (b: boolean) => { isBusy = b; setBusy(b); };
    const history: string[] = [];
    let hIdx = 0;

    ws.onmessage = (ev) => {
      const text = String(ev.data);
      if (text.includes("\x00done")) {
        term.write(text.replace("\x00done\r\n", ""));
        setB(false);
        term.write(PROMPT);
        return;
      }
      term.write(text);
      if (text.startsWith("screening engine")) {
        setB(false);
        term.write(PROMPT);
      }
    };
    ws.onclose = () => { setB(true); term.write("\r\n\x1b[31m[disconnected — reload the page to reconnect]\x1b[0m\r\n"); };

    const replaceLine = (next: string) => {
      term.write("\b \b".repeat(line.length));
      line = next;
      term.write(line);
    };

    const send = (cmd: string) => {
      if (line.trim() || cmd.trim()) history.push(cmd);
      hIdx = history.length;
      setB(true);
      ws.send(cmd);
      line = "";
    };

    runRef.current = (cmd: string) => {
      if (isBusy || ws.readyState !== WebSocket.OPEN) return;
      replaceLine(cmd);
      term.write("\r\n");
      send(cmd);
      term.focus();
    };
    stopRef.current = () => {
      if (isBusy && ws.readyState === WebSocket.OPEN) ws.send("\x03");
    };

    term.onData((data) => {
      if (data === "\x03") {
        if (isBusy) {
          if (ws.readyState === WebSocket.OPEN) ws.send("\x03");
        } else {
          term.write("^C\r\n" + PROMPT);
          line = "";
        }
        return;
      }
      if (isBusy) return;
      if (data.startsWith("\x1b")) {
        if (data === "\x1b[A" && hIdx > 0) replaceLine(history[--hIdx]);
        if (data === "\x1b[B" && hIdx < history.length) replaceLine(history[++hIdx] ?? "");
        return;
      }
      for (const ch of data) {
        if (ch === "\r") {
          term.write("\r\n");
          send(line);
        } else if (ch === "\x7f") {
          if (line.length) {
            line = line.slice(0, -1);
            term.write("\b \b");
          }
        } else if (ch >= " ") {
          line += ch;
          term.write(ch);
        }
      }
    });

    const onResize = () => fit.fit();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      runRef.current = null;
      stopRef.current = null;
      ws.close();
      term.dispose();
    };
  }, []);

  useEffect(() => {
    if (open) setTimeout(() => fitRef.current?.fit(), 0);
  }, [open]);

  const presets = ["screen summary", "screen eval", selected ? `screen explain ${selected}` : null, "screen repro --runs 3"].filter(Boolean) as string[];

  return (
    <div className="term-wrap">
      <div className="term-presets">
        {presets.map((p) => (
          <button key={p} className="term-btn" disabled={busy} onClick={() => runRef.current?.(p)}>{p}</button>
        ))}
        <span className="grow" />
        <button className="term-btn stop" disabled={!busy} onClick={() => stopRef.current?.()} title="Stop the running command (Ctrl+C)">
          Stop
        </button>
      </div>
      <div className="term-host" ref={host} />
    </div>
  );
}
