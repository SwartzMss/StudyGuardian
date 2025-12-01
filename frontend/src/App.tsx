import { FormEvent, useEffect, useMemo, useState } from "react";

type SessionInfo = {
  username: string;
  token: string;
  expiresAt: number; // epoch seconds
};

const SESSION_KEY = "sg-auth-session";

type FaceCapture = {
  id?: number | string;
  identity?: string;
  group_tag?: string | null;
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
  const groupTag = raw.group_tag || raw.group || raw.groupTag || null;
  if (!framePath && !image && !identity) return null;
  return {
    id,
    identity,
    group_tag: groupTag,
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

function withToken(src: string | null, token?: string | null): string | null {
  if (!src) return null;
  if (!token) return src;
  if (src.startsWith("data:")) return src;
  const joiner = src.includes("?") ? "&" : "?";
  return `${src}${joiner}token=${encodeURIComponent(token)}`;
}

function readStoredSession(): SessionInfo | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as SessionInfo;
    if (!parsed?.username || !parsed?.token || !parsed?.expiresAt) return null;
    if (Date.now() >= parsed.expiresAt * 1000) {
      window.localStorage.removeItem(SESSION_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export default function App() {
  const [session, setSession] = useState<SessionInfo | null>(() => readStoredSession());
  const [usernameInput, setUsernameInput] = useState("");
  const [passwordInput, setPasswordInput] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [logoutReason, setLogoutReason] = useState<string | null>(null);
  const [captures, setCaptures] = useState<FaceCapture[]>([]);
  const [postures, setPostures] = useState<PostureEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [postureLoading, setPostureLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [postureError, setPostureError] = useState<string | null>(null);

  const apiBase = useMemo(() => {
    const q = new URLSearchParams(window.location.search).get("api");
    if (!q) return "";
    return q.endsWith("/") ? q.slice(0, -1) : q;
  }, []);

  const captureGroupFilter = useMemo(() => {
    const raw = new URLSearchParams(window.location.search).get("group_tag");
    if (raw === null) return "child";
    const trimmed = raw.trim();
    if (!trimmed || trimmed === "*" || trimmed.toLowerCase() === "all") return null;
    return trimmed;
  }, []);

  const loginUrl = apiBase ? `${apiBase}/api/login` : "/api/login";
  const listUrl = useMemo(() => {
    const base = apiBase ? `${apiBase}/api/face-captures` : "/api/face-captures";
    const params = new URLSearchParams({ limit: "40" });
    if (captureGroupFilter) params.set("group_tag", captureGroupFilter);
    return `${base}?${params.toString()}`;
  }, [apiBase, captureGroupFilter]);
  const postureUrl = apiBase ? `${apiBase}/api/posture-events?is_bad=true&limit=50` : "/api/posture-events?is_bad=true&limit=50";

  useEffect(() => {
    if (!session) {
      setCaptures([]);
      setPostures([]);
      setLoading(false);
      setPostureLoading(false);
      return;
    }
  }, [session]);

  useEffect(() => {
    if (!session) return;

    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(listUrl, {
          headers: session ? { Authorization: `Bearer ${session.token}` } : undefined,
        });
        if (!res.ok) {
          if (res.status === 401) {
            handleLogout("登录已过期，请重新登录");
            return;
          }
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
        const normalized = items.map((item) => normalizeCapture(item)).filter(Boolean) as FaceCapture[];
        const filtered = captureGroupFilter
          ? normalized.filter((item) => item.group_tag === captureGroupFilter)
          : normalized;
        if (!cancelled) {
          setCaptures(filtered);
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
  }, [listUrl, session, captureGroupFilter]);

  useEffect(() => {
    if (!session) return;

    let cancelled = false;
    async function loadPostures() {
      setPostureLoading(true);
      setPostureError(null);
      try {
        const res = await fetch(postureUrl, {
          headers: session ? { Authorization: `Bearer ${session.token}` } : undefined,
        });
        if (!res.ok) {
          if (res.status === 401) {
            handleLogout("登录已过期，请重新登录");
            return;
          }
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
  }, [postureUrl, session]);

  useEffect(() => {
    if (!session) return;
    const remaining = session.expiresAt * 1000 - Date.now();
    if (remaining <= 0) {
      handleLogout("登录已过期，请重新登录");
      return;
    }
    const timer = window.setTimeout(() => {
      handleLogout("登录已过期，请重新登录");
    }, remaining);
    return () => window.clearTimeout(timer);
  }, [session]);

  function handleLogout(reason?: string) {
    setSession(null);
    setLogoutReason(reason || null);
    setPasswordInput("");
    setAuthError(null);
    window.localStorage.removeItem(SESSION_KEY);
  }

  function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const username = usernameInput.trim();
    setAuthError(null);
    fetch(loginUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password: passwordInput }),
    })
      .then(async (res) => {
        if (!res.ok) {
          const msg = res.status === 401 ? "用户名或密码不正确" : `登录失败（${res.status}）`;
          throw new Error(msg);
        }
        const data = await res.json();
        const payload: SessionInfo = {
          username: data.username || username,
          token: data.token,
          expiresAt: Number(data.expires_at),
        };
        if (!payload.token || !payload.expiresAt) {
          throw new Error("登录响应格式不正确");
        }
        setSession(payload);
        setLogoutReason(null);
        window.localStorage.setItem(SESSION_KEY, JSON.stringify(payload));
      })
      .catch((err: any) => {
        setAuthError(err?.message || "登录失败");
      });
  }

  if (!session) {
    return (
      <div className="login-page">
        <div className="login-card">
          <p className="eyebrow">Study Guardian</p>
          <div className="login-visual">
            <img className="login-illustration" src="/mascot.svg" alt="Study Guardian mascot" />
          </div>
          {logoutReason && <div className="session-note">{logoutReason}</div>}
          {authError && <div className="session-note error">{authError}</div>}
          <form className="login-form" onSubmit={handleLogin}>
            <label className="field">
              <span>用户名</span>
              <input
                name="username"
                value={usernameInput}
                onChange={(e) => setUsernameInput(e.target.value)}
                required
                autoComplete="username"
              />
            </label>
            <label className="field">
              <span>密码</span>
              <input
                name="password"
                type="password"
                value={passwordInput}
                onChange={(e) => setPasswordInput(e.target.value)}
                required
                autoComplete="current-password"
              />
            </label>
            <button className="primary-btn" type="submit">
              登录
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <header className="hero">
        <div className="hero-header">
          <p className="eyebrow">Study Guardian · 学习桌守护</p>
          <button className="ghost-btn" onClick={() => handleLogout()}>
            退出登录
          </button>
        </div>
        <div className="hero-row">
          <div>
            <h1>学习桌智能守护系统</h1>
            <p className="muted">实时关注学习桌前的画面与坐姿，守护专注与健康</p>
          </div>
        </div>
      </header>

      <section className="card combined-card">
        <div className="subcard">
          <div className="card-head">
            <div>
              <p className="eyebrow">学习画面</p>
              <h3 className="card-title">实时抓拍</h3>
            </div>
          </div>
          {error && <p className="error-text">{error}</p>}

          {loading ? (
            <div className="placeholder tall">加载中…</div>
          ) : captures.length === 0 ? (
            <div className="placeholder tall">暂无数据</div>
          ) : (
            <div className="capture-grid">
              {captures.map((capture) => {
                const src = withToken(resolveImageSrc(capture, apiBase), session?.token);
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
        </div>

        <div className="subcard">
          <div className="card-head">
            <div>
              <p className="eyebrow">坐姿预警</p>
              <h3 className="card-title">异常提醒</h3>
            </div>
          </div>
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
                    const src = withToken(resolvePostureImageSrc(event, apiBase), session?.token);
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
        </div>
      </section>
    </div>
  );
}
