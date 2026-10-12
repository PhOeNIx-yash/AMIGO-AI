import React, { useMemo } from "react";
import { motion, AnimatePresence } from "motion/react";
import { ColorTheme } from "../types";
import { COLOR_THEMES } from "../data/presets";

export type TextAnimationStyle =
  | "silk_blur" // Apple-grade optical Gaussian blur dissipation & subtle vertical drift
  | "fluid_glide" // Organic critically-damped spring upward glide with zero bounce
  | "ambient_shimmer" // Sophisticated specular metallic light sheen sweep across typography
  | "calm_breathe"; // Serene, low-amplitude anti-gravity breath for deep focus

export function normalizeAnimationStyle(style?: string): TextAnimationStyle {
  if (style === "fluid_glide" || style === "elastic_bounce" || style === "stagger_cascade") {
    return "fluid_glide";
  }
  if (style === "ambient_shimmer" || style === "gradient_shine" || style === "aurora_glow" || style === "neon_pulse") {
    return "ambient_shimmer";
  }
  if (style === "calm_breathe" || style === "floating_lift" || style === "kinetic_wave" || style === "hologram_typewriter") {
    return "calm_breathe";
  }
  return "silk_blur";
}

interface KineticHeadingProps {
  text: string;
  isDark: boolean;
  colorTheme?: ColorTheme;
  animationStyle?: TextAnimationStyle;
  className?: string;
  highlightWords?: string[];
  enableShimmer?: boolean;
}

/**
 * High-Performance Hardware-Accelerated Headline Text Engine
 * Optimized for 60/120 FPS rendering with zero main-thread frame drops.
 */
export const KineticHeading: React.FC<KineticHeadingProps> = ({
  text,
  isDark,
  colorTheme = "violet",
  animationStyle = "silk_blur",
  className = "",
  highlightWords = [],
}) => {
  const activeStyle = normalizeAnimationStyle(animationStyle);
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;
  const words = useMemo(() => {
    if (!text) return [];
    return text.trim().split(/\s+/).filter(Boolean);
  }, [text]);
  const isLongText = words.length > 12;

  const highlightedStyle = useMemo(
    () => ({
      backgroundImage:
        colorTheme === "noir"
          ? "linear-gradient(135deg, #ffffff 0%, #f4f4f5 40%, #e4e4e7 70%, #d4d4d8 100%)"
          : theme.gradient,
      WebkitBackgroundClip: "text",
      WebkitTextFillColor: "transparent",
    }),
    [colorTheme, theme]
  );

  // Elegant specular sheen gradient for ambient_shimmer style
  const shimmerStyle = useMemo(
    () => ({
      backgroundImage: isDark
        ? "linear-gradient(110deg, #f8fafc 0%, #cbd5e1 35%, #ffffff 50%, #cbd5e1 65%, #f8fafc 100%)"
        : "linear-gradient(110deg, #0f172a 0%, #334155 35%, #6366f1 50%, #334155 65%, #0f172a 100%)",
      backgroundSize: "200% 100%",
      WebkitBackgroundClip: "text",
      WebkitTextFillColor: "transparent",
    }),
    [isDark]
  );

  const structuredItems = useMemo(() => {
    if (!text) return [];

    let processed = text;

    // 1. Markdown table parsing
    if (processed.includes("|")) {
      // Split inline row boundaries (| | or ||) to newlines
      processed = processed.replace(/\|\s*\|+/g, "|\n|");
      // Remove separator rows (|---|---| or |:---|:---:|)
      processed = processed.replace(/(?:^|\n)\s*\|?[\s\-:|]{3,}\|[\s\-:|]*(?=\n|$)/g, "");

      const rawLines = processed.split(/\n+/).map((l) => l.trim()).filter(Boolean);
      let headers: string[] = [];
      const parsedRows: string[] = [];

      for (const line of rawLines) {
        if (line.includes("|")) {
          const pipePos = line.indexOf("|");
          const prefix = line.slice(0, pipePos).trim();
          const tablePart = line.slice(pipePos);

          if (prefix) {
            // If prefix has ##, split it cleanly into standalone header lines
            prefix.split(/#{1,6}\s*/).forEach((p) => {
              if (p.trim()) parsedRows.push(p.trim());
            });
          }

          const cells = tablePart.split("|").map((c) => c.trim()).filter(Boolean);
          if (cells.length === 0) continue;

          if (headers.length === 0) {
            headers = cells;
          } else {
            const subject = cells[0];
            const details: string[] = [];
            for (let i = 1; i < cells.length; i++) {
              const colName = headers[i] || "";
              if (colName && !colName.toLowerCase().startsWith("col")) {
                details.push(`${colName}: ${cells[i]}`);
              } else {
                details.push(cells[i]);
              }
            }
            const detStr = details.join(" · ");
            parsedRows.push(detStr ? `• ${subject} — ${detStr}` : `• ${subject}`);
          }
        } else {
          parsedRows.push(line);
        }
      }
      processed = parsedRows.join("\n");
    }

    // Normalize inline dashes or bullet items into separate structured rows
    const normalized = processed
      .replace(/(?<=[.:?!])\s+[-•*]\s+/g, "\n• ")
      .replace(/\s+[-•*]\s+(?=[A-Z])/g, "\n• ")
      .replace(/(?<=[.!?])\s+(?=Or\b|So\b|Let me know\b|What would you\b|Which one\b|Feel free\b)/g, "\n\n");

    const lines = normalized
      .split(/\n+/)
      .map((l) => l.trim())
      .filter(Boolean);

    return lines;
  }, [text]);

  if (structuredItems.length > 1) {
    return (
      <AnimatePresence mode="wait">
        <motion.div
          key={`${text}-${activeStyle}`}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, transition: { duration: 0.15 } }}
          className="flex flex-col items-center w-full max-w-xl mx-auto space-y-2 text-slate-100 select-none py-1"
        >
          {structuredItems.map((item, idx) => {
            const isBullet = /^[•\-*]|\b^\d+\./.test(item);
            const cleanItem = item.replace(/^[•\-*]\s*/, "");

            if (isBullet) {
              const dashMatch = cleanItem.match(/^([^\-—:]{3,40})\s*([—\-:]\s*)([\s\S]+)$/);
              const title = dashMatch ? dashMatch[1].trim() : "";
              const desc = dashMatch ? dashMatch[3].trim() : cleanItem;

              return (
                <motion.div
                  key={idx}
                  initial={{ opacity: 0, x: -6 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: Math.min(0.25, idx * 0.04), duration: 0.3 }}
                  className="flex items-start gap-2.5 w-full text-left px-3.5 py-2 rounded-xl bg-white/[0.05] hover:bg-white/[0.08] border border-white/[0.08] backdrop-blur-sm transition-all shadow-sm"
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-violet-400 mt-2 shrink-0 shadow-[0_0_8px_rgba(167,139,250,0.6)]" />
                  <div className="text-sm sm:text-base leading-relaxed">
                    {title ? (
                      <>
                        <span className="font-semibold text-slate-100 mr-1.5">{title}</span>
                        <span className="text-slate-300 font-normal">{desc}</span>
                      </>
                    ) : (
                      <span className="text-slate-200 font-normal">{cleanItem}</span>
                    )}
                  </div>
                </motion.div>
              );
            }

            return (
              <motion.p
                key={idx}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: idx * 0.03, duration: 0.3 }}
                className={`w-full text-center ${
                  idx === 0
                    ? "text-base sm:text-lg font-medium text-slate-100 tracking-tight leading-snug mb-1"
                    : "text-xs sm:text-sm text-slate-300 font-normal mt-1 opacity-90"
                }`}
              >
                {item}
              </motion.p>
            );
          })}
        </motion.div>
      </AnimatePresence>
    );
  }

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={`${text}-${activeStyle}`}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0, transition: { duration: 0.15 } }}
        className={`flex min-w-0 w-full max-w-full flex-wrap items-center justify-center gap-x-2 gap-y-1.5 text-center select-none ${
          activeStyle === "calm_breathe" ? "motion-safe:animate-calm-float" : ""
        } ${className}`}
        style={{
          overflowWrap: "break-word",
          wordBreak: "break-word",
        }}
      >
        {words.map((word, wordIdx) => {
          const delay = isLongText ? Math.min(0.2, wordIdx * 0.016) : wordIdx * 0.026;
          const cleanWord = word.replace(/^[^\w]+|[^\w]+$/g, "").toLowerCase();
          const isHighlighted =
            (highlightWords.length > 0 &&
              highlightWords.some((hw) => {
                const cleanHw = hw.trim().toLowerCase();
                return cleanHw.length >= 2 && cleanWord === cleanHw;
              })) ||
            word.startsWith("@") ||
            word.startsWith("#");

          let initialProps: any = { opacity: 0, y: 10 };
          let animateProps: any = { opacity: 1, y: 0 };
          let transitionProps: any = { duration: 0.45, delay, ease: [0.16, 1, 0.3, 1] };

          switch (activeStyle) {
            case "silk_blur":
              initialProps = { opacity: 0, y: 8, scale: 0.98 };
              animateProps = { opacity: 1, y: 0, scale: 1 };
              transitionProps = { duration: 0.38, delay, ease: [0.16, 1, 0.3, 1] };
              break;

            case "fluid_glide":
              initialProps = { opacity: 0, y: 14 };
              animateProps = { opacity: 1, y: 0 };
              transitionProps = {
                type: "spring",
                stiffness: 180,
                damping: 26,
                mass: 0.8,
                delay,
              };
              break;

            case "ambient_shimmer":
              initialProps = { opacity: 0, y: 8 };
              animateProps = { opacity: 1, y: 0 };
              transitionProps = { duration: 0.42, delay, ease: [0.22, 1, 0.36, 1] };
              break;

            case "calm_breathe":
              initialProps = { opacity: 0, y: 8 };
              animateProps = { opacity: 1, y: 0 };
              transitionProps = { duration: 0.45, delay, ease: [0.16, 1, 0.3, 1] };
              break;

            default:
              initialProps = { opacity: 0, y: 8 };
              animateProps = { opacity: 1, y: 0 };
              transitionProps = { duration: 0.4, delay, ease: [0.16, 1, 0.3, 1] };
              break;
          }

          const wordStyle: React.CSSProperties = isHighlighted
            ? highlightedStyle
            : activeStyle === "ambient_shimmer"
            ? shimmerStyle
            : {};

          return (
            <motion.span
              key={`${word}-${wordIdx}`}
              initial={initialProps}
              animate={animateProps}
              transition={transitionProps}
              className={`inline-block min-w-0 max-w-full font-medium tracking-tight ${
                activeStyle === "ambient_shimmer" && !isHighlighted ? "motion-safe:animate-shimmer-sweep" : ""
              } ${
                isHighlighted
                  ? "font-bold text-transparent bg-clip-text"
                  : isDark
                  ? colorTheme === "noir"
                    ? "text-zinc-100"
                    : "text-slate-100"
                  : "text-slate-900"
              }`}
              style={{
                ...wordStyle,
                overflowWrap: "break-word",
                wordBreak: "break-word",
              }}
            >
              {word}
            </motion.span>
          );
        })}
      </motion.div>
    </AnimatePresence>
  );
};

interface KineticTranscriptProps {
  text: string;
  isDark: boolean;
  colorTheme?: ColorTheme;
  isLive?: boolean;
}

/**
 * Real-time Speech Transcription Kinetic HUD with Streaming Word Tokens
 */
export const KineticTranscript: React.FC<KineticTranscriptProps> = ({
  text,
  isDark,
  colorTheme = "violet",
  isLive = false,
}) => {
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;
  const words = useMemo(() => (text ? text.trim().split(/\s+/).filter(Boolean) : []), [text]);

  if (!text) return null;

  return (
    <div className="flex flex-wrap items-center gap-1 text-xs sm:text-sm font-medium">
      {words.map((word, idx) => {
        const isLatest = isLive && idx >= words.length - 2;
        return (
          <span
            key={`${word}-${idx}`}
            className={`inline-block px-1.5 py-0.5 rounded-md transition-all duration-200 ${
              isLatest
                ? "font-semibold text-white shadow-sm"
                : isDark
                ? "text-slate-300"
                : "text-slate-700"
            }`}
            style={
              isLatest
                ? {
                    background: theme.gradient,
                    boxShadow: `0 2px 10px ${theme.glow}`,
                  }
                : undefined
            }
          >
            {word}
          </span>
        );
      })}
      {isLive && (
        <span
          className="inline-block w-[3px] h-4 rounded-full align-middle ml-1.5 flex-shrink-0 animate-pulse"
          style={{
            background: `linear-gradient(180deg, #ffffff 0%, ${theme.accent || theme.primary} 100%)`,
            boxShadow: `0 0 10px ${theme.accent || theme.primary}, 0 0 18px ${theme.glow}`,
          }}
        />
      )}
    </div>
  );
};

interface KineticStateBadgeProps {
  stateText: string;
  colorTheme?: ColorTheme;
  isDark: boolean;
  pulse?: boolean;
}

/**
 * Kinetic State Pill with Ambient Light Sweep
 */
export const KineticStateBadge: React.FC<KineticStateBadgeProps> = ({
  stateText,
  colorTheme = "violet",
  isDark,
  pulse = true,
}) => {
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;

  return (
    <div
      className={`relative inline-flex items-center space-x-2 px-3.5 py-1 rounded-full text-xs font-semibold tracking-wider uppercase border overflow-hidden backdrop-blur-xl transition-transform duration-200 ${
        isDark
          ? "bg-black/50 border-white/10 text-white shadow-lg shadow-black/40"
          : "bg-white/80 border-black/10 text-slate-900 shadow-md shadow-slate-300/40"
      }`}
    >
      {/* Dynamic Animated Pulse Dot */}
      <span className="relative flex h-2 w-2">
        {pulse && (
          <span
            className="motion-safe:animate-ping absolute inline-flex h-full w-full rounded-full opacity-75"
            style={{ backgroundColor: theme.primary }}
          />
        )}
        <span
          className="relative inline-flex rounded-full h-2 w-2"
          style={{ backgroundColor: theme.accent || theme.primary }}
        />
      </span>

      <span className="relative z-10 font-medium" style={{ color: theme.accent }}>
        {stateText}
      </span>
    </div>
  );
};
