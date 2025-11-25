import { useEffect, useMemo, useRef, useState } from "react";

type StreamState = "idle" | "connecting" | "playing" | "error" | "stopped";
type WsState = "idle" | "connecting" | "connected" | "closed" | "error";

function usePersistedInput(key: string, initial: string) {
  const [value, setValue] = useState(() => {
    const urlValue = new URLSearchParams(window.location.search).get("stream");
    if (urlValue) return urlValue;
    return localStorage.getItem(key) || initial;
  });
  useEffect(() => {
    localStorage.setItem(key, value);
  }, [key, value]);
  return [value, setValue] as const;
}

function useWsEvents(wsParam: string | null) {
  const [wsState, setState] = useState<WsState>("idle");
  const [lastEvent, setLastEvent] = useState<string>("暂无");

  useEffect(() => {
    if (!wsParam) {
      setState("idle");
      return;
    }
    let cancelled = false;
    let ws: WebSocket | null = null;

    try {
      ws = new WebSocket(wsParam);
      setState("connecting");
    } catch (err) {
      setState("error");
      console.error("WS connect error", err);
      return;
    }

    if (!ws) return;

    ws.onopen = () => !cancelled && setState("connected");
    ws.onclose = () => !cancelled && setState("closed");
    ws.onerror = () => !cancelled && setState("error");
    ws.onmessage = (evt) => {
      if (cancelled) return;
      const text = typeof evt.data === "string" ? evt.data : "";
      setLastEvent(text.slice(0, 200) || "收到事件");
    };

    return () => {
      cancelled = true;
      ws.close();
    };
  }, [wsParam]);

  return { wsState, lastEvent };
}

function Badge({ text, tone }: { text: string; tone?: "good" | "bad" | "info" }) {
  const className = useMemo(() => {
    const base = ["badge"];
    if (tone === "good") base.push("good");
    if (tone === "bad") base.push("bad");
    if (tone === "info") base.push("info");
    return base.join(" ");
  }, [tone]);
  return <span className={className}>{text}</span>;
}

export default function App() {
  const [streamUrl, setStreamUrl] = usePersistedInput("sg_stream_url", "");
  const [state, setState] = useState<StreamState>("idle");
  const imgRef = useRef<HTMLImageElement | null>(null);
  const placeholderRef = useRef<HTMLDivElement | null>(null);

  const wsParam = useMemo(() => {
    const q = new URLSearchParams(window.location.search).get("ws");
    return q || null;
  }, []);
  const { wsState, lastEvent } = useWsEvents(wsParam);

  const wsTone: "info" | "good" | "bad" = wsState === "connected" ? "good" : wsState === "error" ? "bad" : "info";

  useEffect(() => {
    const img = imgRef.current;
    if (!img) return;
    const onLoad = () => setState("playing");
    const onError = () => {
      setState("error");
      if (placeholderRef.current) {
        placeholderRef.current.style.display = "flex";
        placeholderRef.current.textContent = "流加载失败，请检查地址或跨域限制";
      }
    };
    img.addEventListener("load", onLoad);
    img.addEventListener("error", onError);
    return () => {
      img.removeEventListener("load", onLoad);
      img.removeEventListener("error", onError);
    };
  }, []);

  const start = () => {
    if (!streamUrl.trim()) {
      alert("请先输入 MJPEG 流地址");
      return;
    }
    setState("connecting");
    if (placeholderRef.current) placeholderRef.current.style.display = "none";
    if (imgRef.current) imgRef.current.src = streamUrl.trim();
  };

  const stop = () => {
    setState("stopped");
    if (imgRef.current) imgRef.current.src = "";
    if (placeholderRef.current) placeholderRef.current.style.display = "flex";
  };

  return (
    <>
      <header className="topbar">
        <h1>StudyGuardian Viewer</h1>
        <Badge text={`WS: ${wsState}`} tone={wsTone} />
      </header>

      <main className="layout">
        <section className="card">
          <div className="controls">
            <label htmlFor="stream-input">流地址 (MJPEG/HTTP)</label>
            <div className="control-row">
              <input
                id="stream-input"
                type="text"
                placeholder="例如 http://192.168.x.x:81/stream"
                value={streamUrl}
                onChange={(e) => setStreamUrl(e.target.value)}
              />
              <button onClick={start}>开始</button>
              <button className="secondary" onClick={stop}>
                停止
              </button>
            </div>
          </div>
          <div className="stream-wrap">
            <img ref={imgRef} alt="Video stream" aria-live="polite" />
            <div className="placeholder" ref={placeholderRef}>
              等待开始，输入摄像头 MJPEG URL
            </div>
          </div>
        </section>

        <aside className="card">
          <h3 className="card-title">实时状态</h3>
          <ul className="status-list">
            <li className="status-item">
              <span className="label">流状态</span>
              <Badge text={state} tone={state === "playing" ? "good" : state === "error" ? "bad" : "info"} />
            </li>
            <li className="status-item">
              <span className="label">最后事件</span>
              <span className="muted">{lastEvent}</span>
            </li>
            <li className="status-item">
              <span className="label">提示</span>
              <span className="muted">仅查看，不修改配置</span>
            </li>
          </ul>
        </aside>
      </main>
    </>
  );
}
