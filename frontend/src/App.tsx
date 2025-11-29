import { useEffect, useMemo, useState } from "react";

type FaceCapture = {
  id?: number | string;
  identity?: string;
  timestamp?: string;
  frame_path?: string;
  image?: string;
  face_distance?: number;
};

function normalizeCapture(raw: any): FaceCapture | null {
  if (!raw || typeof raw !== "object") return null;
  const framePath = raw.frame_path || raw.path || raw.image_url || raw.url;
  const image = raw.image || raw.image_base64 || raw.base64;
  const identity = raw.identity || raw.name || raw.who;
  const timestamp = raw.timestamp || raw.time || raw.ts || new Date().toISOString();
  const id = raw.id ?? raw.face_capture_id ?? raw.capture_id;
  const faceDistance = raw.face_distance ?? raw.distance;
  if (!framePath && !image && !identity) return null;
  return {
    id,
    identity,
    timestamp,
    frame_path: framePath,
    image,
    face_distance: faceDistance,
  };
}

function resolveImageSrc(capture: FaceCapture, apiBase: string): string | null {
  const src = capture.image || capture.frame_path;
  if (!src) return null;
  if (src.startsWith("data:") || src.startsWith("http")) return src;
  if (apiBase) {
    const prefix = apiBase.endsWith("/") ? apiBase.slice(0, -1) : apiBase;
    const normalized = src.startsWith("/") ? src.slice(1) : src;
    return `${prefix}/${normalized}`;
  }
  return src;
}

function formatTime(value?: string) {
  if (!value) return "无时间戳";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`;
}

function displayIdentity(identity?: string | null) {
  if (!identity) return "未知";
  const parts = identity.split("/");
  return parts[parts.length - 1] || "未知";
}

export default function App() {
  const [captures, setCaptures] = useState<FaceCapture[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const apiBase = useMemo(() => {
    const q = new URLSearchParams(window.location.search).get("api");
    if (!q) return "";
    return q.endsWith("/") ? q.slice(0, -1) : q;
  }, []);

  const listUrl = apiBase ? `${apiBase}/api/face-captures?limit=40` : "/api/face-captures?limit=40";

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(listUrl);
        if (!res.ok) {
          throw new Error(`接口返回 ${res.status}`);
        }
        const text = await res.text();
        let data: any;
        try {
          data = JSON.parse(text);
        } catch {
          throw new Error(`接口返回的不是 JSON，检查接口地址。响应开头: ${text.slice(0, 120)}`);
        }
        const items: any[] = Array.isArray(data) ? data : data?.items || data?.data || [];
        const normalized = items
          .map((item) => normalizeCapture(item))
          .filter(Boolean) as FaceCapture[];
        if (!cancelled) {
          setCaptures(normalized);
        }
      } catch (err: any) {
        if (!cancelled) {
          console.error(err);
          setError(err?.message || "获取数据失败");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [listUrl]);

  return (
    <div className="page">
      <header className="hero">
        <p className="eyebrow">Face Watch · 实时抓拍</p>
        <div className="hero-row">
          <div>
            <h1>抓拍墙</h1>
            <p className="muted">快速浏览最新画面，一眼锁定重点时刻</p>
          </div>
          <div className="pill">{captures.length} 条记录</div>
        </div>
      </header>

      <section className="card simple-card">
        {error && <p className="error-text">{error}</p>}

        {loading ? (
          <div className="placeholder tall">加载中…</div>
        ) : captures.length === 0 ? (
          <div className="placeholder tall">暂无数据</div>
        ) : (
          <div className="capture-grid">
            {captures.map((capture) => {
              const src = resolveImageSrc(capture, apiBase);
              const key = capture.id ?? capture.frame_path ?? capture.timestamp ?? Math.random().toString(36);
              const identity = displayIdentity(capture.identity);
              return (
                <article className="capture-card" key={key}>
                  <div className="thumb">
                    {src ? <img src={src} alt={capture.identity || "face"} /> : <div className="placeholder mini">无图片</div>}
                  </div>
                  <div className="capture-meta">
                    <p className="identity">{identity}</p>
                    <p className="muted small">{formatTime(capture.timestamp)}</p>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
