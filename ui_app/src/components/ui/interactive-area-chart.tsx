"use client";

import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  AnimatePresence,
  animate,
  motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
} from "framer-motion";

/* -------------------------------------------------------------------------- */
/*  Interactive Area Chart — hand-written SVG, no chart library                */
/*  Enhanced for Amigo Real-Time Stock & Financial Visualizations              */
/* -------------------------------------------------------------------------- */

export type ChartPoint = { date: number; value: number }; // date = UTC ms
export type AreaChartData = Record<string, ChartPoint[]>;

export interface InteractiveAreaChartProps {
  data?: AreaChartData;
  defaultRange?: string;
  title?: string;
  subtitle?: string;
  badge?: string;
  exchange?: string;
  height?: number;
  className?: string;
  currency?: string;
  isLoading?: boolean;
  onRangeChange?: (range: string) => void;
  headerAction?: React.ReactNode;
  footer?: React.ReactNode;
}

const N = 72; // every dataset is resampled to N points so ranges can morph 1:1
const PAD = { t: 40, r: 16, b: 28, l: 52 };
const TIP_W = 136;
const EASE_OUT = [0.23, 1, 0.32, 1] as const;
const EASE_IN_OUT = [0.77, 0, 0.175, 1] as const;

const clamp = (v: number, min: number, max: number) => Math.min(Math.max(v, min), max);
const f = (n: number) => Math.round(n * 100) / 100;

/* ------------------------------- Demo data --------------------------------- */

const DAY = 86_400_000;
const END = Date.now();

function rng(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function makeSeries(n: number, seed: number, start: number, drift: number, vol: number): ChartPoint[] {
  const r = rng(seed);
  let v = start;
  return Array.from({ length: n }, (_, i) => {
    v += drift + (r() - 0.5) * vol + Math.sin(i / (n / 6)) * vol * 0.15;
    v = Math.max(v, start * 0.4);
    return { date: END - (n - 1 - i) * DAY, value: Math.round(v * 100) / 100 };
  });
}

export const DEFAULT_AREA_CHART_DATA: AreaChartData = {
  "1D": makeSeries(24, 5, 185.5, 0.2, 3.2),
  "5D": makeSeries(35, 11, 180.2, 0.4, 4.5),
  "1M": makeSeries(30, 7, 172.0, 0.8, 6.0),
  "1Y": makeSeries(52, 3, 145.0, 1.2, 12.0),
};

/* --------------------------------- Maths ----------------------------------- */

// Catmull-Rom sample so sparse datasets (7 points) resample smoothly
function spline(v: number[], pos: number) {
  const n = v.length;
  if (n <= 1) return v[0] ?? 0;
  const i = Math.min(Math.floor(pos), n - 2);
  const t = pos - i;
  const p0 = v[Math.max(i - 1, 0)];
  const p1 = v[i];
  const p2 = v[i + 1];
  const p3 = v[Math.min(i + 2, n - 1)];
  return p1 + 0.5 * t * (p2 - p0 + t * (2 * p0 - 5 * p1 + 4 * p2 - p3 + t * (3 * (p1 - p2) + p3 - p0)));
}

function niceScale(min: number, max: number, count = 4) {
  const raw = ((max - min) || 1) / (count - 1);
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  const n = raw / (mag || 1);
  const step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * (mag || 1);
  const lo = Math.floor(min / (step || 1)) * step;
  const hi = Math.ceil(max / (step || 1)) * step;
  const ticks: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(v);
  return { lo, hi, ticks };
}

function buildModel(data: AreaChartData) {
  const out: Record<string, { pts: ChartPoint[]; ny: Float64Array; lo: number; hi: number; ticks: number[] }> = {};
  for (const [key, pts] of Object.entries(data)) {
    if (!pts || pts.length === 0) continue;
    const vals = pts.map((p) => p.value);
    const { lo, hi, ticks } = niceScale(Math.min(...vals), Math.max(...vals));
    const denom = (hi - lo) || 1;
    const ny = new Float64Array(N);
    for (let k = 0; k < N; k++) {
      const sp = spline(vals, (k / (N - 1)) * (vals.length - 1));
      ny[k] = (sp - lo) / denom;
    }
    out[key] = { pts, ny, lo, hi, ticks };
  }
  if (Object.keys(out).length === 0) {
    const dummy: ChartPoint[] = [
      { date: Date.now() - 3600000, value: 100 },
      { date: Date.now(), value: 100 },
    ];
    const ny = new Float64Array(N).fill(0.5);
    out["1D"] = { pts: dummy, ny, lo: 90, hi: 110, ticks: [90, 100, 110] };
  }
  return out;
}

function smoothPath(p: [number, number][]) {
  let d = `M${f(p[0][0])} ${f(p[0][1])}`;
  for (let i = 0; i < p.length - 1; i++) {
    const p0 = p[i - 1] ?? p[i];
    const p1 = p[i];
    const p2 = p[i + 1];
    const p3 = p[i + 2] ?? p2;
    d += `C${f(p1[0] + (p2[0] - p0[0]) / 6)} ${f(p1[1] + (p2[1] - p0[1]) / 6)} ${f(p2[0] - (p3[0] - p1[0]) / 6)} ${f(p2[1] - (p3[1] - p1[1]) / 6)} ${f(p2[0])} ${f(p2[1])}`;
  }
  return d;
}

const formatPrice = (val: number, cur: string = "$") => {
  const sym = cur === "USD" ? "$" : cur === "INR" ? "₹" : cur === "EUR" ? "€" : cur === "GBP" ? "£" : (cur || "$");
  return `${sym}${val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

const formatCompact = (val: number, cur: string = "$") => {
  const sym = cur === "USD" ? "$" : cur === "INR" ? "₹" : cur === "EUR" ? "€" : cur === "GBP" ? "£" : (cur || "$");
  if (Math.abs(val) >= 1e9) return `${sym}${(val / 1e9).toFixed(1)}B`;
  if (Math.abs(val) >= 1e6) return `${sym}${(val / 1e6).toFixed(1)}M`;
  if (Math.abs(val) >= 1e3) return `${sym}${(val / 1e3).toFixed(1)}K`;
  return `${sym}${val.toLocaleString(undefined, { maximumFractionDigits: 1 })}`;
};

const formatTipDate = (dateMs: number, curRange: string) => {
  const d = new Date(dateMs);
  if (curRange === "1D" || curRange === "5D") {
    return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  }
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
};

const axisFmt = (len: number, isNarrow = false) =>
  new Intl.DateTimeFormat(
    "en-US",
    len <= 10
      ? { weekday: "short", timeZone: "UTC" }
      : isNarrow
        ? { month: "numeric", day: "numeric", timeZone: "UTC" }
        : len <= 60
          ? { month: "short", day: "numeric", timeZone: "UTC" }
          : { month: "short", timeZone: "UTC" },
  );

/* -------------------------------- Component -------------------------------- */

export function InteractiveAreaChart({
  data = DEFAULT_AREA_CHART_DATA,
  defaultRange = "1D",
  title = "Market Overview",
  subtitle,
  badge,
  exchange,
  height = 240,
  className = "",
  currency = "$",
  isLoading = false,
  onRangeChange,
  headerAction,
  footer,
}: InteractiveAreaChartProps) {
  const uid = useId().replace(/:/g, "");
  const reduce = useReducedMotion();
  const model = useMemo(() => buildModel(data), [data]);
  const keys = Object.keys(model);

  const [range, setRange] = useState(keys.includes(defaultRange) ? defaultRange : keys[0]);
  const [hover, setHover] = useState<number | null>(null);
  const [width, setWidth] = useState(0);

  const currentModel = model[range] || model[keys[0]];

  const wrapRef = useRef<HTMLDivElement>(null);
  const fromRef = useRef<Float64Array>(currentModel.ny);
  const toRef = useRef<Float64Array>(currentModel.ny);
  const morphCtl = useRef<{ stop: () => void } | null>(null);
  const started = useRef(false);
  const lastHover = useRef(0);

  const t = useMotionValue(1); // morph progress between datasets
  const ver = useMotionValue(0); // bumps on every range change
  const wMV = useMotionValue(0);
  const reveal = useMotionValue(reduce ? 1 : 0); // intro draw progress
  const cx = useMotionValue(0);
  const cxS = useSpring(cx, { stiffness: 520, damping: 42, mass: 0.6 });

  // Sync internal refs when model changes dynamically
  useEffect(() => {
    if (model[range]) {
      toRef.current = model[range].ny;
      fromRef.current = model[range].ny;
      ver.set(ver.get() + 1);
    }
  }, [model, range, ver]);

  const innerH = height - PAD.t - PAD.b;
  const bottom = PAD.t + innerH;
  const yPx = (n: number) => PAD.t + (1 - n) * innerH;
  const disp = (k: number) => {
    const a = fromRef.current[k] ?? 0.5;
    const b = toRef.current[k] ?? 0.5;
    return a + (b - a) * t.get();
  };
  const points = (w: number) => {
    const iw = Math.max(1, w - PAD.l - PAD.r);
    return Array.from({ length: N }, (_, k) => [PAD.l + (k / (N - 1)) * iw, yPx(disp(k))] as [number, number]);
  };
  const sampleY = (frac: number) => {
    const k = clamp(frac, 0, 1) * (N - 1);
    const i = Math.min(Math.floor(k), N - 2);
    const a = disp(i);
    return yPx(a + (disp(i + 1) - a) * (k - i));
  };

  const lineD = useTransform([t, wMV, ver], () => {
    const w = wMV.get();
    return w ? smoothPath(points(w)) : "M0 0";
  });
  const areaD = useTransform([t, wMV, ver], () => {
    const w = wMV.get();
    if (!w) return "M0 0";
    const p = points(w);
    return `${smoothPath(p)}L${f(p[N - 1][0])} ${bottom}L${f(p[0][0])} ${bottom}Z`;
  });
  const clipW = useTransform([reveal, wMV], ([r, w]: number[]) =>
    r >= 0.999 ? w : PAD.l + Math.max(0, w - PAD.l - PAD.r) * r,
  );
  const leadX = useTransform([reveal, wMV], ([r, w]: number[]) => PAD.l + Math.max(0, w - PAD.l - PAD.r) * r);
  const leadY = useTransform([reveal, t, wMV, ver], ([r]: number[]) => sampleY(r));
  const leadO = useTransform(reveal, [0, 0.02, 0.96, 1], [0, 1, 1, 0]);
  const dotY = useTransform([cxS, t, wMV, ver], ([c]: number[]) =>
    sampleY((c - PAD.l) / Math.max(1, wMV.get() - PAD.l - PAD.r)),
  );
  const tipX = useTransform([cxS, wMV], ([c, w]: number[]) => clamp(c - (TIP_W + 16) / 2, 0, Math.max(0, w - (TIP_W + 16))));

  // Track width + start the intro draw once we know it
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;

    const initialW = Math.round(el.getBoundingClientRect().width || el.offsetWidth || 500);
    if (initialW > 0 && width === 0) {
      setWidth(initialW);
      wMV.set(initialW);
      if (!started.current) {
        started.current = true;
        if (reduce) reveal.set(1);
        else animate(reveal, 1, { duration: 1.1, ease: EASE_IN_OUT });
      }
    }

    if (typeof ResizeObserver === "undefined") return;

    const ro = new ResizeObserver(([e]) => {
      const w = Math.round(e.contentRect.width || initialW);
      if (w > 0) {
        setWidth(w);
        wMV.set(w);
        if (!started.current) {
          started.current = true;
          if (reduce) reveal.set(1);
          else animate(reveal, 1, { duration: 1.1, ease: EASE_IN_OUT });
        }
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [reduce, reveal, wMV, width]);

  const m = currentModel;
  const len = m.pts.length;

  // Morph: interruptible — always starts from what's currently on screen
  const changeRange = (next: string) => {
    if (next === range) return;
    if (onRangeChange) {
      onRangeChange(next);
    }
    if (!model[next]) return;
    morphCtl.current?.stop();
    const e = t.get();
    const cur = new Float64Array(N);
    for (let k = 0; k < N; k++) cur[k] = fromRef.current[k] + (toRef.current[k] - fromRef.current[k]) * e;
    fromRef.current = cur;
    toRef.current = model[next].ny;
    t.set(0);
    ver.set(ver.get() + 1);
    morphCtl.current = animate(t, 1, { duration: reduce ? 0 : 0.55, ease: EASE_IN_OUT });
    setRange(next);
    setHover(null);
  };

  // Scrubbing: snap to the nearest real data point, spring the visuals
  const moveTo = (idx: number) => {
    const iw = Math.max(1, width - PAD.l - PAD.r);
    const target = PAD.l + (len > 1 ? idx / (len - 1) : 0.5) * iw;
    cx.set(target);
    if (hover === null) cxS.jump(target);
    lastHover.current = idx;
    setHover(idx);
  };
  const onScrub = (e: React.PointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.ownerSVGElement!.getBoundingClientRect();
    const frac = clamp((e.clientX - box.left - PAD.l) / Math.max(1, width - PAD.l - PAD.r), 0, 1);
    moveTo(Math.round(frac * (len - 1)));
  };

  const shown = m.pts[hover ?? len - 1] ?? { value: 0, date: Date.now() };
  const tipPt = m.pts[Math.min(lastHover.current, len - 1)] ?? shown;
  const firstVal = m.pts[0]?.value ?? shown.value;
  const delta = firstVal !== 0 ? (shown.value - firstVal) / firstVal : 0;
  const isPositive = delta >= 0;
  const strokeColor = isPositive ? "#10b981" : "#f43f5e";
  const glowShadow = isPositive ? "rgba(16, 185, 129, 0.22)" : "rgba(244, 63, 94, 0.22)";

  const isNarrow = width > 0 && width < 380;
  const axis = axisFmt(len, isNarrow);
  const labelCount = isNarrow ? 3 : width < 520 ? 4 : 5;
  const xLabels = [...new Set(
    Array.from({ length: labelCount }, (_, j) => Math.round((j / (labelCount - 1)) * (len - 1)))
  )];

  return (
    <div className={`w-full ${className}`}>
      {/* Header */}
      <div className="mb-3.5 flex flex-col gap-2.5 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            {badge && (
              <span className="px-2.5 py-0.5 rounded-lg text-xs font-mono font-black tracking-wider uppercase bg-black/10 dark:bg-white/10 text-neutral-900 dark:text-neutral-100 border border-black/10 dark:border-white/15 shadow-sm">
                {badge}
              </span>
            )}
            {exchange && (
              <span className="text-xs opacity-60 font-semibold tracking-wide uppercase">
                {exchange}
              </span>
            )}
            {subtitle && (
              <span className="text-xs opacity-50 truncate hidden sm:inline">
                • {subtitle}
              </span>
            )}
          </div>
          <h3 className="mt-1 text-base sm:text-lg font-bold tracking-tight text-neutral-900 dark:text-neutral-50 truncate">
            {title}
          </h3>
          <div className="mt-1 flex items-baseline gap-2.5 flex-wrap">
            <span className="text-2xl sm:text-3xl font-black tabular-nums tracking-tight font-mono text-neutral-900 dark:text-neutral-50">
              {formatPrice(shown.value, currency)}
            </span>
            <span
              className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold font-mono tracking-tight shadow-sm ${
                isPositive
                  ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30"
                  : "bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30"
              }`}
            >
              <span>{isPositive ? "+" : ""}{(delta * 100).toFixed(2)}%</span>
            </span>
            <span className="text-[11px] opacity-60 font-mono">
              {hover !== null ? formatTipDate(shown.date, range) : `last ${range}`}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2 self-start sm:self-auto shrink-0">
          {headerAction}
          <div role="tablist" aria-label="Range" className="flex shrink-0 rounded-xl bg-black/5 dark:bg-white/5 p-1 border border-black/5 dark:border-white/5">
            {keys.map((k) => (
              <button
                key={k}
                role="tab"
                type="button"
                aria-selected={k === range}
                onClick={() => changeRange(k)}
                className={`relative z-10 rounded-lg px-2.5 sm:px-3 py-1 text-xs font-bold tracking-wider outline-none transition-[color,transform] duration-150 ease-out active:scale-[0.96] ${
                  k === range
                    ? "text-neutral-900 dark:text-neutral-950 font-black"
                    : "opacity-60 hover:opacity-100 text-neutral-600 dark:text-neutral-400"
                }`}
              >
                {k === range && (
                  <motion.span
                    layoutId={`${uid}-pill`}
                    className="absolute inset-0 -z-10 rounded-lg bg-white shadow-md dark:bg-white/90"
                    transition={{ type: "spring", duration: 0.3, bounce: 0.15 }}
                  />
                )}
                {k}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Chart */}
      <div
        ref={wrapRef}
        className={`relative transition-opacity duration-300 ${isLoading ? "opacity-40" : "opacity-100"}`}
        style={{ height }}
      >
        {width > 0 && (
          <>
            <svg
              width={width}
              height={height}
              role="img"
              tabIndex={0}
              aria-label={`${title}, last ${range}: from ${formatPrice(m.pts[0].value, currency)} to ${formatPrice(m.pts[len - 1].value, currency)}.`}
              className={`block select-none outline-none ${isPositive ? "text-emerald-500" : "text-rose-500"}`}
              onKeyDown={(e) => {
                if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
                  e.preventDefault();
                  moveTo(clamp((hover ?? len - 1) + (e.key === "ArrowRight" ? 1 : -1), 0, len - 1));
                } else if (e.key === "Escape") setHover(null);
              }}
              onBlur={() => setHover(null)}
            >
              <defs>
                <linearGradient id={`${uid}-fill`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="currentColor" stopOpacity={0.28} />
                  <stop offset="85%" stopColor="currentColor" stopOpacity={0.02} />
                  <stop offset="100%" stopColor="currentColor" stopOpacity={0} />
                </linearGradient>
                <clipPath id={`${uid}-clip`}>
                  <motion.rect x={0} y={0} height={height} width={clipW} />
                </clipPath>
              </defs>

              {/* Grid + axes: crossfade per range */}
              <AnimatePresence>
                <motion.g
                  key={range}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2, ease: EASE_OUT }}
                >
                  {m.ticks.map((v) => {
                    const y = yPx((v - m.lo) / (m.hi - m.lo || 1));
                    return (
                      <g key={v}>
                        <line
                          x1={PAD.l}
                          x2={width - PAD.r}
                          y1={y}
                          y2={y}
                          stroke="currentColor"
                          strokeDasharray="4 4"
                          strokeWidth="0.8"
                          className="text-neutral-200/80 dark:text-neutral-800/80"
                        />
                        <text
                          x={PAD.l - 8}
                          y={y}
                          textAnchor="end"
                          dominantBaseline="middle"
                          className="fill-neutral-400 dark:fill-neutral-500 text-[10px] font-mono tabular-nums"
                        >
                          {formatCompact(v, currency)}
                        </text>
                      </g>
                    );
                  })}
                  {xLabels.map((idx, j) => (
                    <text
                      key={idx}
                      x={PAD.l + (idx / Math.max(1, len - 1)) * (width - PAD.l - PAD.r)}
                      y={height - 6}
                      textAnchor={j === 0 ? "start" : j === xLabels.length - 1 ? "end" : "middle"}
                      className="fill-neutral-400 dark:fill-neutral-500 text-[10px] font-mono"
                    >
                      {m.pts[idx] ? axis.format(m.pts[idx].date) : ""}
                    </text>
                  ))}
                </motion.g>
              </AnimatePresence>

              {/* Area + line (revealed left→right on load) */}
              <g clipPath={`url(#${uid}-clip)`}>
                <motion.path d={areaD} fill={`url(#${uid}-fill)`} />
                <motion.path
                  d={lineD}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2.4}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  style={{ filter: `drop-shadow(0 2px 8px ${glowShadow})` }}
                />
              </g>
              <motion.circle cx={leadX} cy={leadY} r={4} fill="currentColor" style={{ opacity: leadO }} />

              {/* Crosshair + dot */}
              <motion.g
                initial={false}
                animate={{ opacity: hover !== null ? 1 : 0 }}
                transition={{ duration: 0.15, ease: EASE_OUT }}
                pointerEvents="none"
              >
                <motion.line
                  x1={cxS}
                  x2={cxS}
                  y1={PAD.t - 4}
                  y2={bottom}
                  stroke="currentColor"
                  strokeOpacity={0.4}
                  strokeDasharray="3 3"
                />
                <motion.circle
                  cx={cxS}
                  cy={dotY}
                  r={5}
                  stroke="currentColor"
                  strokeWidth={2}
                  className="fill-white dark:fill-neutral-950"
                  style={{ filter: `drop-shadow(0 0 6px ${strokeColor})` }}
                />
              </motion.g>

              {/* Pointer surface */}
              <rect
                x={0}
                y={0}
                width={width}
                height={height}
                fill="transparent"
                style={{ touchAction: "pan-y", cursor: "crosshair" }}
                onPointerMove={onScrub}
                onPointerDown={onScrub}
                onPointerLeave={() => setHover(null)}
              />
            </svg>

            {/* Tooltip */}
            <motion.div
              className="pointer-events-none absolute left-0 top-0 rounded-xl border border-neutral-200/90 bg-white/95 px-3 py-2 shadow-xl backdrop-blur-md dark:border-neutral-800 dark:bg-neutral-950/95 z-20"
              style={{ x: tipX, width: TIP_W + 16 }}
              initial={false}
              animate={{ opacity: hover !== null ? 1 : 0 }}
              transition={{ duration: 0.15, ease: EASE_OUT }}
            >
              <p className="text-[10px] font-mono uppercase font-semibold text-neutral-400">
                {formatTipDate(tipPt.date, range)}
              </p>
              <p className="text-sm font-bold tabular-nums font-mono text-neutral-900 dark:text-neutral-50 mt-0.5">
                {formatPrice(tipPt.value, currency)}
              </p>
            </motion.div>
          </>
        )}
      </div>

      {footer}
    </div>
  );
}

export default InteractiveAreaChart;
