import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";

/* ============================================================
   数据层 —— 双模态物理隔离
   ============================================================ */

type PoolType = "dark" | "light";
type TabKey = "text" | "image";
type PoolFilter = "all" | "dark" | "light";

interface ModelRow {
  name: string;
  uuid: string;
  time: string;
  pool: PoolType;
  org: string;
  tags: string[];
}

interface ProviderGroup {
  provider: string;
  code: string; // 两位缩写徽标
  models: ModelRow[];
}

interface PanelDef {
  key: TabKey;
  icon: string;
  title: string;
  en: string;
  standard: string;
  providers: ProviderGroup[];
}

const PANELS: PanelDef[] = [
  {
    key: "text",
    icon: "💬",
    title: "纯文本对话模型",
    en: "TEXT · CHAT MODELS",
    standard: "仅收录纯文本对话 / Web 输出模型，严格排除生图、视频与音频输出。",
    providers: [
      {
        provider: "DeepSeek",
        code: "DS",
        models: [
          {
            name: "deepseek-v4-flash-internal-test-v2",
            uuid: "01a00197-e3fe-7965-9978-b4e35974daf4",
            time: "2026-08-15 02:45",
            pool: "dark",
            org: "DeepSeek",
            tags: ["传图 (Vision)", "Web 输出", "深度思考"],
          },
        ],
      },
    ],
  },
  {
    key: "image",
    icon: "🎨",
    title: "图像生图生成模型",
    en: "IMAGE · GENERATION MODELS",
    standard: "仅收录具备图像生成能力（文生图 / 图生图）的模型，严格排除纯视频与纯音频。",
    providers: [
      {
        provider: "ByteDance / Seedream",
        code: "SD",
        models: [
          {
            name: "seedream-5.0-pro",
            uuid: "019f42b5-8c52-7793-9be8-de35eecf7ea9",
            time: "2026-07-09 01:10",
            pool: "dark",
            org: "ByteDance / Seedream",
            tags: ["图像生成", "图生图", "多比例输出"],
          },
        ],
      },
    ],
  },
];

const TICKER_ITEMS = [
  "SYS ▸ 双模态数据隔离校验通过",
  "NET ▸ 直连隧道延迟 42ms",
  "SEC ▸ Cloudflare 过盾会话心跳正常",
  "POOL ▸ 暗池扫描器运行中",
  "SYNC ▸ 快照校验和 9F3A-C1D8 匹配",
  "ARENA ▸ 榜单监听 WebSocket 已建立",
];

const MONTH_LABEL = (m: string) => `${m.slice(0, 4)} 年 ${parseInt(m.slice(5), 10)} 月`;
const slug = (s: string) => s.replace(/[^a-zA-Z0-9]+/g, "-").toLowerCase();

function panelStats(p: PanelDef) {
  const all = p.providers.flatMap((g) => g.models);
  return {
    total: all.length,
    dark: all.filter((m) => m.pool === "dark").length,
    light: all.filter((m) => m.pool === "light").length,
    providers: p.providers.length,
  };
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}

/* ============================================================
   Hooks
   ============================================================ */

/** 数字滚动动画 */
function useCountUp(target: number, duration = 900) {
  const [val, setVal] = useState(0);
  useEffect(() => {
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setVal(Math.round(target * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return val;
}

/** 实时时钟 */
function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())} ${p(now.getHours())}:${p(now.getMinutes())}:${p(now.getSeconds())}`;
}

/* ============================================================
   装饰组件
   ============================================================ */

function Backdrop() {
  return (
    <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div className="absolute inset-0 bg-[#05060e]" />
      <div className="aurora-a absolute -top-40 -left-32 h-[560px] w-[560px] rounded-full bg-indigo-600/25 blur-[130px]" />
      <div className="aurora-b absolute top-1/4 -right-40 h-[500px] w-[500px] rounded-full bg-fuchsia-600/16 blur-[130px]" />
      <div className="aurora-c absolute bottom-[-15%] left-1/3 h-[460px] w-[460px] rounded-full bg-cyan-500/14 blur-[130px]" />
      <div className="bg-grid absolute inset-0" />
      <div className="bg-noise absolute inset-0" />
      <div className="scanline" />
    </div>
  );
}

/** HUD 四角括号容器 */
function Hud({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`hud-corners ${className}`}>
      <span className="hud-c" />
      {children}
    </div>
  );
}

/** 暗池/明池比例环形图 */
function Donut({ dark, light }: { dark: number; light: number }) {
  const total = Math.max(1, dark + light);
  const R = 26;
  const C = 2 * Math.PI * R;
  const darkLen = (dark / total) * C;
  return (
    <svg viewBox="0 0 64 64" className="h-16 w-16 -rotate-90">
      <circle cx="32" cy="32" r={R} fill="none" stroke="rgba(148,163,184,0.15)" strokeWidth="7" />
      <circle
        cx="32" cy="32" r={R} fill="none"
        stroke="#34d399" strokeWidth="7"
        strokeDasharray={`${C - darkLen} ${C}`}
        strokeDashoffset={-darkLen}
        strokeLinecap="round"
      />
      <motion.circle
        cx="32" cy="32" r={R} fill="none"
        stroke="#a78bfa" strokeWidth="7"
        strokeLinecap="round"
        initial={{ strokeDasharray: `0 ${C}` }}
        animate={{ strokeDasharray: `${darkLen} ${C}` }}
        transition={{ duration: 1, ease: "easeOut", delay: 0.2 }}
      />
    </svg>
  );
}

/* ============================================================
   主组件
   ============================================================ */

export default function App() {
  const [tab, setTab] = useState<TabKey>("text");
  const [query, setQuery] = useState<Record<TabKey, string>>({ text: "", image: "" });
  const [poolFilter, setPoolFilter] = useState<Record<TabKey, PoolFilter>>({
    text: "all",
    image: "all",
  });
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [copiedLock, setCopiedLock] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<{ id: number; msg: string; ok: boolean } | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clock = useClock();

  const genTime = useMemo(() => {
    const d = new Date();
    const p = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }, []);

  const panel = PANELS.find((p) => p.key === tab)!;
  const stats = panelStats(panel);
  const q = query[tab].trim().toLowerCase();
  const pf = poolFilter[tab];

  const nTotal = useCountUp(stats.total);
  const nDark = useCountUp(stats.dark);
  const nLight = useCountUp(stats.light);
  const nProv = useCountUp(stats.providers);

  /* ---------- 过滤：搜索 × 池类型交集 ---------- */
  const filtered = useMemo(() => {
    return panel.providers
      .map((g) => {
        const rows = g.models.filter((m) => {
          const hitQ =
            !q ||
            m.name.toLowerCase().includes(q) ||
            m.uuid.toLowerCase().includes(q) ||
            m.org.toLowerCase().includes(q);
          const hitP = pf === "all" || m.pool === pf;
          return hitQ && hitP;
        });
        const byMonth = new Map<string, ModelRow[]>();
        for (const r of rows) {
          const mo = r.time.slice(0, 7);
          if (!byMonth.has(mo)) byMonth.set(mo, []);
          byMonth.get(mo)!.push(r);
        }
        return {
          ...g,
          months: [...byMonth.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1)),
          count: rows.length,
        };
      })
      .filter((g) => g.count > 0);
  }, [panel, q, pf]);

  const totalMatched = filtered.reduce((s, g) => s + g.count, 0);

  /* ---------- Toast ---------- */
  const showToast = (msg: string, ok = true) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ id: Date.now(), msg, ok });
    toastTimer.current = setTimeout(() => setToast(null), 2200);
  };

  /* ---------- 复制（状态机防重入） ---------- */
  const handleCopy = async (uuid: string) => {
    if (copiedLock.has(uuid)) return;
    setCopiedLock((s) => new Set(s).add(uuid));
    const ok = await copyToClipboard(uuid);
    showToast(
      ok ? `UUID 已写入剪贴板 · ${uuid.slice(0, 18)}…` : "复制失败，请手动选择文本",
      ok
    );
    setTimeout(() => {
      setCopiedLock((s) => {
        const n = new Set(s);
        n.delete(uuid);
        return n;
      });
    }, 1800);
  };

  /* ---------- 折叠 ---------- */
  const cardKey = (provider: string) => `${tab}:${provider}`;
  const toggleCard = (provider: string) =>
    setCollapsed((s) => {
      const n = new Set(s);
      const k = cardKey(provider);
      n.has(k) ? n.delete(k) : n.add(k);
      return n;
    });
  const setAll = (fold: boolean) =>
    setCollapsed((s) => {
      const n = new Set(s);
      panel.providers.forEach((g) =>
        fold ? n.add(cardKey(g.provider)) : n.delete(cardKey(g.provider))
      );
      return n;
    });

  /* ---------- 模态切换 ---------- */
  const switchTab = (k: TabKey) => {
    if (k === tab) return;
    setTab(k);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  /* ---------- 快捷键 ---------- */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      const typing =
        el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable;
      if (e.key === "/" && !typing) {
        e.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
      } else if (e.key === "Escape") {
        setQuery((s) => ({ ...s, [tab]: "" }));
        searchRef.current?.blur();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab]);

  const jumpTo = (provider: string) =>
    document
      .getElementById(`prov-${tab}-${slug(provider)}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });

  const kpis = [
    { label: "模型总数", en: "TOTAL MODELS", value: nTotal, color: "text-cyan-300", bar: "from-cyan-400/70" },
    { label: "暗池模型", en: "DARK POOL", value: nDark, color: "text-violet-300", bar: "from-violet-400/70" },
    { label: "明池模型", en: "LIGHT POOL", value: nLight, color: "text-emerald-300", bar: "from-emerald-400/70" },
    { label: "覆盖生态厂商", en: "PROVIDERS", value: nProv, color: "text-amber-300", bar: "from-amber-400/70" },
  ];

  return (
    <div className="min-h-screen pb-14">
      <Backdrop />

      {/* ================= 顶部状态条 ================= */}
      <div className="border-b border-white/6 bg-black/30 backdrop-blur-sm">
        <div className="mx-auto flex max-w-6xl items-center gap-4 overflow-hidden px-5 py-1.5 font-mono text-[11px] text-slate-500">
          <span className="flex shrink-0 items-center gap-1.5 text-emerald-400">
            <span className="pulse-dot inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 text-emerald-400" />
            LINK ONLINE
          </span>
          <span className="shrink-0 text-slate-600">|</span>
          <span className="shrink-0 tabular-nums">{clock}</span>
          <span className="shrink-0 text-slate-600">|</span>
          <div className="relative flex-1 overflow-hidden whitespace-nowrap">
            <div className="ticker inline-block">
              {[0, 1].map((i) => (
                <span key={i}>
                  {TICKER_ITEMS.map((t) => (
                    <span key={t} className="mx-6 text-slate-500">
                      {t}
                    </span>
                  ))}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* ================= 头部 ================= */}
      <header className="mx-auto max-w-6xl px-5 pt-10 pb-2">
        <motion.div
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
        >
          <div className="font-mono text-[11px] tracking-[0.35em] text-cyan-400/80 uppercase">
            ◢ Arena Direct-Link Observatory
          </div>
          <h1 className="mt-3 text-3xl font-black tracking-tight sm:text-5xl">
            <span className="bg-gradient-to-r from-white via-slate-200 to-slate-400 bg-clip-text text-transparent">
              全模态模型直连
            </span>
            <span className="bg-gradient-to-r from-cyan-300 via-sky-400 to-violet-400 bg-clip-text text-transparent glow-num">
              全景仪表盘
            </span>
          </h1>
          <p className="mt-3 font-mono text-xs text-slate-500 sm:text-sm">
            <span className="typewriter">
              $ arena --scan all-modalities --isolate --snapshot {genTime}
            </span>
          </p>
        </motion.div>

        {/* 模态切换 */}
        <div className="mt-8 grid gap-4 sm:grid-cols-2">
          {PANELS.map((p, idx) => {
            const s = panelStats(p);
            const active = tab === p.key;
            const isTextCard = p.key === "text";
            return (
              <motion.button
                key={p.key}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.55, delay: 0.15 + idx * 0.1 }}
                onClick={() => switchTab(p.key)}
                className="group relative text-left"
              >
                <Hud
                  className={`relative overflow-hidden rounded-xl border p-5 transition-all duration-300 ${
                    active
                      ? isTextCard
                        ? "border-cyan-400/40 bg-gradient-to-br from-cyan-500/12 via-transparent to-indigo-500/10 shadow-[0_0_40px_rgba(34,211,238,0.15)]"
                        : "border-fuchsia-400/40 bg-gradient-to-br from-fuchsia-500/12 via-transparent to-violet-500/10 shadow-[0_0_40px_rgba(232,121,249,0.15)]"
                      : "border-white/8 bg-white/[0.02] hover:border-white/20 hover:bg-white/[0.05]"
                  }`}
                >
                  {active && <span className="beam-line" />}
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="font-mono text-[10px] tracking-[0.3em] text-slate-500 uppercase">
                        {p.en}
                      </div>
                      <div className="mt-1.5 text-lg font-bold text-white">
                        <span className="mr-2">{p.icon}</span>
                        {p.title}
                      </div>
                    </div>
                    <div
                      className={`rounded-md border px-2 py-0.5 font-mono text-[10px] tracking-wider ${
                        active
                          ? isTextCard
                            ? "border-cyan-400/50 text-cyan-300"
                            : "border-fuchsia-400/50 text-fuchsia-300"
                          : "border-white/10 text-slate-600"
                      }`}
                    >
                      {active ? "● ACTIVE" : "○ STANDBY"}
                    </div>
                  </div>
                  <div className="mt-4 flex items-end justify-between">
                    <div className="flex gap-5 font-mono">
                      <div>
                        <div className="text-2xl font-bold text-white tabular-nums">{s.total}</div>
                        <div className="text-[10px] tracking-wider text-slate-500">TOTAL</div>
                      </div>
                      <div>
                        <div className="text-2xl font-bold text-violet-300 tabular-nums">{s.dark}</div>
                        <div className="text-[10px] tracking-wider text-slate-500">🔒 DARK</div>
                      </div>
                      <div>
                        <div className="text-2xl font-bold text-emerald-300 tabular-nums">{s.light}</div>
                        <div className="text-[10px] tracking-wider text-slate-500">🌐 LIGHT</div>
                      </div>
                    </div>
                    <span
                      className={`font-mono text-xs transition-transform duration-300 group-hover:translate-x-1 ${
                        active ? (isTextCard ? "text-cyan-400" : "text-fuchsia-400") : "text-slate-600"
                      }`}
                    >
                      ▸▸
                    </span>
                  </div>
                </Hud>
              </motion.button>
            );
          })}
        </div>
      </header>

      {/* ================= 面板主体 ================= */}
      <AnimatePresence mode="wait">
        <motion.main
          key={tab}
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -10 }}
          transition={{ duration: 0.3 }}
          className="mx-auto max-w-6xl px-5"
        >
          {/* 收录标准 */}
          <div className="mt-8 flex items-start gap-3 rounded-xl border border-white/8 bg-white/[0.02] px-4 py-3 backdrop-blur-sm">
            <span className="mt-0.5 font-mono text-xs text-cyan-400">[STD]</span>
            <p className="text-sm leading-relaxed text-slate-400">
              <span className="mr-2 font-semibold text-slate-200">收录标准</span>
              {panel.standard}
            </p>
          </div>

          {/* KPI */}
          <section className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-5">
            {kpis.map((k) => (
              <Hud
                key={k.label}
                className="relative overflow-hidden rounded-xl border border-white/8 bg-white/[0.02] p-4 backdrop-blur-sm"
              >
                <div className="font-mono text-[10px] tracking-[0.25em] text-slate-500 uppercase">
                  {k.en}
                </div>
                <div className={`glow-num mt-2 font-mono text-4xl font-bold tabular-nums ${k.color}`}>
                  {String(k.value).padStart(2, "0")}
                </div>
                <div className="mt-1 text-xs text-slate-500">{k.label}</div>
                <div
                  className={`absolute bottom-0 left-0 h-0.5 w-full bg-gradient-to-r ${k.bar} to-transparent`}
                />
              </Hud>
            ))}
            {/* 池比例环形图 */}
            <Hud className="relative flex items-center gap-3 rounded-xl border border-white/8 bg-white/[0.02] p-4 backdrop-blur-sm max-lg:col-span-2">
              <Donut dark={stats.dark} light={stats.light} />
              <div className="font-mono text-[11px] leading-relaxed">
                <div className="text-slate-500 tracking-widest">POOL RATIO</div>
                <div className="mt-1 text-violet-300">
                  ■ 暗池 {stats.total ? Math.round((stats.dark / stats.total) * 100) : 0}%
                </div>
                <div className="text-emerald-300">
                  ■ 明池 {stats.total ? Math.round((stats.light / stats.total) * 100) : 0}%
                </div>
              </div>
            </Hud>
          </section>

          {/* 厂商快速导航 */}
          <section className="mt-6">
            <div className="mb-2.5 flex items-center gap-2 font-mono text-[10px] tracking-[0.3em] text-slate-500 uppercase">
              <span className="text-cyan-400">◈</span> Provider Quick-Nav
              <span className="h-px flex-1 bg-gradient-to-r from-white/15 to-transparent" />
            </div>
            <div className="flex flex-wrap gap-2">
              {panel.providers.map((g) => (
                <button
                  key={g.provider}
                  onClick={() => jumpTo(g.provider)}
                  className="group flex items-center gap-2.5 rounded-lg border border-white/10 bg-white/[0.03] py-2 pr-4 pl-2 transition-all hover:-translate-y-0.5 hover:border-cyan-400/50 hover:bg-cyan-400/5 hover:shadow-[0_4px_20px_rgba(34,211,238,0.12)]"
                >
                  <span className="flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br from-slate-700 to-slate-800 font-mono text-[10px] font-bold text-cyan-300 ring-1 ring-white/10">
                    {g.code}
                  </span>
                  <span className="text-sm text-slate-300 group-hover:text-white">
                    {g.provider}
                  </span>
                  <span className="font-mono text-[10px] text-slate-600 group-hover:text-cyan-400">
                    ×{g.models.length}
                  </span>
                </button>
              ))}
            </div>
          </section>

          {/* ============ Sticky 工具栏 ============ */}
          <div className="sticky top-0 z-40 -mx-5 mt-6 px-5 py-3">
            <div className="pointer-events-none absolute inset-0 border-y border-white/8 bg-[#070a16]/85 backdrop-blur-xl" />
            <div className="relative mx-auto flex max-w-6xl flex-wrap items-center gap-3">
              {/* 搜索 */}
              <div className="relative min-w-[220px] flex-1">
                <span className="pointer-events-none absolute top-1/2 left-3.5 -translate-y-1/2 font-mono text-xs text-cyan-500">
                  ❯
                </span>
                <input
                  ref={searchRef}
                  value={query[tab]}
                  onChange={(e) => setQuery((s) => ({ ...s, [tab]: e.target.value }))}
                  placeholder="检索模型名 / UUID / 组织 …"
                  className="w-full rounded-lg border border-white/12 bg-black/40 py-2.5 pr-16 pl-9 font-mono text-sm text-white placeholder-slate-600 transition-all outline-none focus:border-cyan-400/60 focus:bg-black/60 focus:shadow-[0_0_20px_rgba(34,211,238,0.12)]"
                />
                <div className="absolute top-1/2 right-2.5 flex -translate-y-1/2 items-center gap-1.5">
                  {query[tab] ? (
                    <button
                      onClick={() => {
                        setQuery((s) => ({ ...s, [tab]: "" }));
                        searchRef.current?.focus();
                      }}
                      className="rounded px-1.5 py-0.5 text-xs text-slate-500 transition-colors hover:bg-white/10 hover:text-white"
                    >
                      ✕
                    </button>
                  ) : (
                    <kbd className="rounded border border-white/12 bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-slate-500">
                      /
                    </kbd>
                  )}
                </div>
              </div>

              {/* 池过滤（滑动指示） */}
              <div className="flex rounded-lg border border-white/12 bg-black/40 p-1">
                {(
                  [
                    ["all", "全部"],
                    ["dark", "🔒 暗池"],
                    ["light", "🌐 明池"],
                  ] as [PoolFilter, string][]
                ).map(([v, label]) => (
                  <button
                    key={v}
                    onClick={() => setPoolFilter((s) => ({ ...s, [tab]: v }))}
                    className={`relative rounded-md px-3.5 py-1.5 text-sm transition-colors ${
                      pf === v ? "text-white" : "text-slate-500 hover:text-slate-300"
                    }`}
                  >
                    {pf === v && (
                      <motion.span
                        layoutId={`pool-pill-${tab}`}
                        className="absolute inset-0 rounded-md bg-gradient-to-b from-cyan-500/30 to-cyan-600/15 ring-1 ring-cyan-400/40"
                        transition={{ type: "spring", stiffness: 500, damping: 35 }}
                      />
                    )}
                    <span className="relative">{label}</span>
                  </button>
                ))}
              </div>

              {/* 折叠控制 */}
              <div className="flex gap-2 font-mono text-xs">
                <button
                  onClick={() => setAll(true)}
                  className="rounded-lg border border-white/12 bg-black/40 px-3 py-2 text-slate-400 transition-colors hover:border-white/25 hover:text-white"
                >
                  ▲ 折叠全部
                </button>
                <button
                  onClick={() => setAll(false)}
                  className="rounded-lg border border-white/12 bg-black/40 px-3 py-2 text-slate-400 transition-colors hover:border-white/25 hover:text-white"
                >
                  ▼ 展开全部
                </button>
              </div>

              <span className="ml-auto hidden font-mono text-[11px] text-slate-600 md:block">
                MATCH <span className="text-cyan-400">{totalMatched}</span> / {stats.total}
              </span>
            </div>
          </div>

          {/* ============ 厂商板块 ============ */}
          <section className="mt-6 space-y-5">
            {filtered.length === 0 && (
              <div className="relative overflow-hidden rounded-xl border border-dashed border-white/12 bg-white/[0.015] py-16 text-center">
                {/* 雷达空状态 */}
                <div className="relative mx-auto h-24 w-24">
                  <span className="radar-ring absolute inset-0 rounded-full border border-cyan-400/50" />
                  <span
                    className="radar-ring absolute inset-0 rounded-full border border-cyan-400/50"
                    style={{ animationDelay: "0.7s" }}
                  />
                  <svg viewBox="0 0 96 96" className="absolute inset-0">
                    <circle cx="48" cy="48" r="30" fill="none" stroke="rgba(103,232,249,0.2)" />
                    <circle cx="48" cy="48" r="16" fill="none" stroke="rgba(103,232,249,0.15)" />
                    <g className="radar-sweep">
                      <path d="M48 48 L48 10 A38 38 0 0 1 74 21 Z" fill="rgba(103,232,249,0.18)" />
                    </g>
                    <circle cx="48" cy="48" r="2.5" fill="#67e8f9" />
                  </svg>
                </div>
                <p className="mt-6 font-mono text-sm tracking-widest text-cyan-300 uppercase">
                  No Signal Detected
                </p>
                <p className="mt-2 text-sm text-slate-500">
                  检索「{query[tab] || "空"}」×「
                  {pf === "all" ? "全部" : pf === "dark" ? "仅暗池" : "仅明池"}
                  」无匹配目标
                </p>
                <button
                  onClick={() => {
                    setQuery((s) => ({ ...s, [tab]: "" }));
                    setPoolFilter((s) => ({ ...s, [tab]: "all" }));
                  }}
                  className="mt-6 rounded-lg border border-cyan-400/40 bg-cyan-400/10 px-5 py-2 font-mono text-sm text-cyan-300 transition-all hover:bg-cyan-400/20 hover:shadow-[0_0_24px_rgba(34,211,238,0.2)]"
                >
                  ↺ RESET FILTERS
                </button>
              </div>
            )}

            {filtered.map((g) => {
              const isCollapsed = collapsed.has(cardKey(g.provider));
              return (
                <Hud
                  key={g.provider}
                  className="scroll-mt-24 overflow-hidden rounded-xl border border-white/8 bg-[#0a0e1e]/60 backdrop-blur-sm"
                >
                  <div id={`prov-${tab}-${slug(g.provider)}`} className="absolute -top-24" />
                  {/* 头部 */}
                  <button
                    onClick={() => toggleCard(g.provider)}
                    className="flex w-full items-center justify-between px-5 py-4 text-left transition-colors hover:bg-white/[0.03]"
                  >
                    <div className="flex items-center gap-3.5">
                      <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br from-slate-700 to-slate-900 font-mono text-xs font-bold text-cyan-300 ring-1 ring-white/15">
                        {g.code}
                      </span>
                      <div>
                        <div className="font-bold text-white">{g.provider}</div>
                        <div className="mt-0.5 font-mono text-[10px] tracking-wider text-slate-500">
                          {g.count} MODELS MATCHED · {g.months.length} MONTH GROUPS
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="hidden rounded-md border border-white/10 px-2 py-0.5 font-mono text-[10px] text-slate-500 sm:block">
                        {isCollapsed ? "COLLAPSED" : "EXPANDED"}
                      </span>
                      <motion.span
                        animate={{ rotate: isCollapsed ? -90 : 0 }}
                        transition={{ duration: 0.25 }}
                        className="text-cyan-400/70"
                      >
                        ▼
                      </motion.span>
                    </div>
                  </button>

                  {/* 主体 */}
                  <div className={`collapse-body ${isCollapsed ? "collapsed" : ""}`}>
                    <div className="collapse-inner">
                      {g.months.map(([month, rows]) => (
                        <div key={month} className="border-t border-white/6">
                          <div className="flex items-center gap-2 px-5 py-2 font-mono text-[11px] tracking-widest text-slate-500">
                            <span className="text-cyan-400">▸</span> {MONTH_LABEL(month)}
                            <span className="h-px flex-1 bg-gradient-to-r from-white/10 to-transparent" />
                          </div>
                          <div className="overflow-x-auto">
                            <table className="w-full min-w-[920px] text-sm">
                              <thead>
                                <tr className="border-y border-white/6 bg-black/20 text-left font-mono text-[10px] tracking-[0.2em] text-slate-500 uppercase">
                                  <th className="px-5 py-2.5 font-medium">Time</th>
                                  <th className="px-3 py-2.5 font-medium">Model</th>
                                  <th className="px-3 py-2.5 font-medium">Pool</th>
                                  <th className="px-3 py-2.5 font-medium">UUID</th>
                                  <th className="px-3 py-2.5 font-medium">Org</th>
                                  <th className="px-3 py-2.5 font-medium">Capabilities</th>
                                </tr>
                              </thead>
                              <tbody>
                                {rows.map((m) => {
                                  const locked = copiedLock.has(m.uuid);
                                  return (
                                    <tr
                                      key={m.uuid}
                                      className="row-hud border-b border-white/4 transition-colors last:border-b-0 hover:bg-cyan-400/[0.03]"
                                    >
                                      <td className="px-5 py-4 font-mono text-xs whitespace-nowrap text-slate-500 tabular-nums">
                                        {m.time}
                                      </td>
                                      <td className="px-3 py-4">
                                        <span className="font-mono text-[13px] font-semibold text-white">
                                          {m.name}
                                        </span>
                                      </td>
                                      <td className="px-3 py-4 whitespace-nowrap">
                                        {m.pool === "dark" ? (
                                          <span className="inline-flex items-center gap-1.5 rounded-md border border-violet-400/30 bg-violet-500/10 px-2.5 py-1 font-mono text-[11px] text-violet-300">
                                            🔒 纯正暗池
                                          </span>
                                        ) : (
                                          <span className="inline-flex items-center gap-1.5 rounded-md border border-emerald-400/30 bg-emerald-500/10 px-2.5 py-1 font-mono text-[11px] text-emerald-300">
                                            🌐 明池
                                          </span>
                                        )}
                                      </td>
                                      <td className="px-3 py-4">
                                        <div className="flex items-center gap-2">
                                          <code className="rounded-md border border-cyan-400/15 bg-black/50 px-2 py-1 font-mono text-[11px] text-cyan-300">
                                            {m.uuid}
                                          </code>
                                          <button
                                            onClick={() => handleCopy(m.uuid)}
                                            disabled={locked}
                                            className={`relative shrink-0 overflow-hidden rounded-md border px-2.5 py-1 font-mono text-[11px] font-medium transition-all ${
                                              locked
                                                ? "cursor-default border-emerald-400/50 bg-emerald-500/15 text-emerald-300"
                                                : "border-white/15 bg-white/[0.04] text-slate-400 hover:border-cyan-400/60 hover:text-cyan-300 hover:shadow-[0_0_14px_rgba(34,211,238,0.25)] active:scale-95"
                                            }`}
                                          >
                                            {locked ? "✓ COPIED" : "⧉ COPY"}
                                          </button>
                                        </div>
                                      </td>
                                      <td className="px-3 py-4 whitespace-nowrap text-slate-300">
                                        {m.org}
                                      </td>
                                      <td className="px-3 py-4">
                                        <div className="flex flex-wrap gap-1.5">
                                          {m.tags.map((t) => (
                                            <span
                                              key={t}
                                              className="rounded border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[11px] whitespace-nowrap text-slate-300"
                                            >
                                              {t}
                                            </span>
                                          ))}
                                        </div>
                                      </td>
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </Hud>
              );
            })}
          </section>

          {/* ============ 页脚：终端式说明 ============ */}
          <footer className="mt-12">
            <Hud className="overflow-hidden rounded-xl border border-white/8 bg-[#080b17]/80 backdrop-blur-sm">
              <div className="flex items-center gap-2 border-b border-white/6 px-4 py-2.5">
                <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
                <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" />
                <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
                <span className="ml-2 font-mono text-[11px] text-slate-500">
                  arena-ops — usage-notes.log
                </span>
              </div>
              <div className="space-y-4 p-5 font-mono text-[13px] leading-relaxed">
                <div>
                  <div className="text-cyan-400">$ cat override_model.md</div>
                  <p className="mt-1.5 text-slate-400">
                    表格提取的模型 UUID 可直接作为反代配置的{" "}
                    <code className="rounded border border-cyan-400/20 bg-black/50 px-1.5 py-0.5 text-cyan-300">
                      override_model
                    </code>{" "}
                    参数，实现目标模型定向直连。暗池（🔒）模型未在公开榜单露出，建议先验证可用窗口再投产。
                  </p>
                </div>
                <div>
                  <div className="text-amber-400">$ cat security_warning.md</div>
                  <p className="mt-1.5 text-slate-400">
                    <span className="mr-1.5 rounded border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 text-[11px] text-amber-300">
                      ⚠ CF SHIELD
                    </span>
                    Arena 接口受 Cloudflare 盾防护——务必配合反代服务的
                    <span className="text-amber-200"> 过盾会话 </span>
                    使用，切勿裸连官方接口：裸连将触发人机校验失败，并可能导致会话指纹被标记限流。
                  </p>
                </div>
                <div className="border-t border-white/6 pt-3 text-[11px] text-slate-600">
                  ARENA OBSERVATORY · DEMO BUILD · SNAPSHOT {genTime} · FOR INTERNAL RESEARCH
                  ONLY <span className="cursor-blink text-cyan-500">▊</span>
                </div>
              </div>
            </Hud>
          </footer>
        </motion.main>
      </AnimatePresence>

      {/* ================= Toast ================= */}
      <AnimatePresence>
        {toast && (
          <motion.div
            key={toast.id}
            initial={{ opacity: 0, y: 24, x: "-50%", scale: 0.94 }}
            animate={{ opacity: 1, y: 0, x: "-50%", scale: 1 }}
            exit={{ opacity: 0, y: 12, x: "-50%", scale: 0.96 }}
            transition={{ type: "spring", stiffness: 500, damping: 32 }}
            className="fixed bottom-8 left-1/2 z-50 overflow-hidden rounded-xl border border-white/12 bg-[#0c1122]/95 shadow-2xl shadow-black/60 backdrop-blur-xl"
          >
            <div className="flex items-center gap-3 px-5 py-3.5">
              <span
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full font-mono text-sm ${
                  toast.ok
                    ? "bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-400/40"
                    : "bg-rose-500/20 text-rose-300 ring-1 ring-rose-400/40"
                }`}
              >
                {toast.ok ? "✓" : "!"}
              </span>
              <span className="font-mono text-[13px] text-slate-200">{toast.msg}</span>
            </div>
            <div
              className={`toast-bar h-0.5 ${toast.ok ? "bg-gradient-to-r from-emerald-400 to-cyan-400" : "bg-rose-400"}`}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
