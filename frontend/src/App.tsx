import { useEffect, useMemo, useState } from "react";

type FaceCapture = {
  id?: number | string;
  identity?: string;
  timestamp?: string;
  frame_path?: string;
  image?: string;
  image_url?: string;
  face_distance?: number;
};

type PostureEvent = {
  id?: number | string;
  identity?: string;
  timestamp?: string;
  is_bad?: boolean;
  nose_drop?: number;
  neck_angle?: number;
  reasons?: string[];
  frame_path?: string;
  image?: string;
  image_url?: string;
  face_capture_id?: string;
};

function normalizeCapture(raw: any): FaceCapture | null {
  if (!raw || typeof raw !== "object") return null;
  const framePath = raw.frame_path || raw.path;
  const imageUrl = raw.image_url || raw.url;
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
    image_url: imageUrl,
    face_distance: faceDistance,
  };
}

function resolveImageSrc(capture: FaceCapture, apiBase: string): string | null {
  const src = capture.image_url || capture.image || capture.frame_path;
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

function normalizePostureEvent(raw: any): PostureEvent | null {
  if (!raw || typeof raw !== "object") return null;
  const framePath = raw.frame_path;
  const imageUrl = raw.image_url || raw.url;
  const image = raw.image || raw.image_base64 || raw.base64;
  const identity = raw.identity || raw.name;
  const timestamp = raw.timestamp || raw.time || raw.ts;
  const id = raw.id ?? raw.posture_id ?? raw.event_id;
  const isBad = raw.is_bad ?? raw.bad ?? raw.alert;
  const noseDrop = raw.nose_drop ?? raw.drop ?? raw.head_drop;
  const neckAngle = raw.neck_angle ?? raw.angle ?? raw.head_angle;
  const reasons = Array.isArray(raw.reasons)
    ? raw.reasons
    : typeof raw.reasons === "string"
      ? raw.reasons.split(",").map((r: string) => r.trim()).filter(Boolean)
      : [];

  if (!identity && !framePath && !image) return null;
  return {
    id,
    identity,
    timestamp,
    is_bad: isBad,
    nose_drop: noseDrop,
    neck_angle: neckAngle,
    reasons,
    frame_path: framePath,
    image_url: imageUrl,
    image,
    face_capture_id: raw.face_capture_id,
  };
}

function formatNumber(value?: number, digits = 2) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(digits);
}

function resolvePostureImageSrc(event: PostureEvent, apiBase: string): string | null {
  const src = event.image_url || event.image || event.frame_path;
  if (!src) return null;
  if (src.startsWith("data:") || src.startsWith("http")) return src;
  if (apiBase) {
    const prefix = apiBase.endsWith("/") ? apiBase.slice(0, -1) : apiBase;
    const normalized = src.startsWith("/") ? src.slice(1) : src;
    return `${prefix}/${normalized}`;
  }
  return src;
}

export default function App() {
  const [captures, setCaptures] = useState<FaceCapture[]>([]);
  const [postures, setPostures] = useState<PostureEvent[]>([]);
  const [activeTab, setActiveTab] = useState<"captures" | "postures">("captures");
  const [loading, setLoading] = useState(true);
  const [postureLoading, setPostureLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [postureError, setPostureError] = useState<string | null>(null);

  const apiBase = useMemo(() => {
    const q = new URLSearchParams(window.location.search).get("api");
    if (!q) return "";
    return q.endsWith("/") ? q.slice(0, -1) : q;
  }, []);

  const listUrl = apiBase ? `${apiBase}/api/face-captures?limit=40` : "/api/face-captures?limit=40";
  const postureUrl = apiBase ? `${apiBase}/api/posture-events?is_bad=true&limit=50` : "/api/posture-events?is_bad=true&limit=50";

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

  useEffect(() => {
    let cancelled = false;
    async function loadPostures() {
      setPostureLoading(true);
      setPostureError(null);
      try {
        const res = await fetch(postureUrl);
        if (!res.ok) {
          throw new Error(`姿势接口返回 ${res.status}`);
        }
        const text = await res.text();
        let data: any;
        try {
          data = JSON.parse(text);
        } catch {
          throw new Error(`姿势接口返回的不是 JSON，检查接口地址。响应开头: ${text.slice(0, 120)}`);
        }
        const items: any[] = Array.isArray(data) ? data : data?.items || data?.data || [];
        const normalized = items
          .map((item) => normalizePostureEvent(item))
          .filter(Boolean) as PostureEvent[];
        if (!cancelled) setPostures(normalized);
      } catch (err: any) {
        if (!cancelled) {
          console.error(err);
          setPostureError(err?.message || "获取姿势数据失败");
        }
      } finally {
        if (!cancelled) setPostureLoading(false);
      }
    }
    loadPostures();
    return () => {
      cancelled = true;
    };
  }, [postureUrl]);

  return (
    <div className="page">
      <header className="hero">
        <p className="eyebrow">Study Guardian · 学习桌守护</p>
        <div className="hero-row">
          <div>
            <h1>学习桌智能守护系统</h1>
            <p className="muted">实时关注学习桌前的画面与坐姿，守护专注与健康</p>
          </div>
        </div>
        <div className="tabs">
          <button
            className={activeTab === "captures" ? "tab active" : "tab"}
            onClick={() => setActiveTab("captures")}
            type="button"
          >
            学习画面
          </button>
          <button
            className={activeTab === "postures" ? "tab active" : "tab"}
            onClick={() => setActiveTab("postures")}
            type="button"
          >
            坐姿预警
          </button>
        </div>
      </header>

      {activeTab === "captures" && (
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
      )}

      {activeTab === "postures" && (
        <section className="card simple-card">
          {postureError && <p className="error-text">{postureError}</p>}

          {postureLoading ? (
            <div className="placeholder tall">加载中…</div>
          ) : postures.length === 0 ? (
            <div className="placeholder tall">暂无异常姿势记录</div>
          ) : (
            <div className="table-wrap">
              <table className="posture-table">
                <thead>
                  <tr>
                    <th>截图</th>
                    <th>时间</th>
                    <th>原因</th>
                  </tr>
                </thead>
                <tbody>
                  {postures.map((event) => {
                    const key = event.id ?? event.timestamp ?? Math.random().toString(36);
                    const src = resolvePostureImageSrc(event, apiBase);
                    const reasons = (event.reasons && event.reasons.length > 0 ? event.reasons : ["姿势异常"]).join(" / ");
                    return (
                      <tr key={key}>
                        <td>
                          <div className="posture-thumb mini">
                            {src ? <img src={src} alt="posture" /> : <div className="placeholder mini">无图</div>}
                          </div>
                        </td>
                        <td className="muted small">{formatTime(event.timestamp)}</td>
                        <td className="posture-reasons">{reasons}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
