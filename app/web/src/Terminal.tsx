import { useEffect, useRef } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";

const PROMPT = "\x1b[36m$\x1b[0m ";

export default function Terminal({ open }: { open: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const fitRef = useRef<FitAddon | null>(null);

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
    let busy = true;
    const history: string[] = [];
    let hIdx = 0;

    ws.onmessage = (ev) => {
      const text = String(ev.data);
      if (text.includes("\x00done")) {
        term.write(text.replace("\x00done\r\n", ""));
        busy = false;
        term.write(PROMPT);
        return;
      }
      term.write(text);
      if (text.startsWith("screening engine")) {
        busy = false;
        term.write(PROMPT);
      }
    };
    ws.onclose = () => term.write("\r\n\x1b[31m[disconnected]\x1b[0m\r\n");

    const replaceLine = (next: string) => {
      term.write("\b \b".repeat(line.length));
      line = next;
      term.write(line);
    };

    term.onData((data) => {
      if (busy) return;
      if (data.startsWith("\x1b")) {
        if (data === "\x1b[A" && hIdx > 0) replaceLine(history[--hIdx]);
        if (data === "\x1b[B" && hIdx < history.length) replaceLine(history[++hIdx] ?? "");
        return;
      }
      for (const ch of data) {
        if (ch === "\r") {
          term.write("\r\n");
          if (line.trim()) history.push(line);
          hIdx = history.length;
          busy = true;
          ws.send(line);
          line = "";
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
      ws.close();
      term.dispose();
    };
  }, []);

  useEffect(() => {
    if (open) setTimeout(() => fitRef.current?.fit(), 0);
  }, [open]);

  return <div className="term-host" ref={host} />;
}
