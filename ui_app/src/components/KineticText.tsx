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
    // Ensure tokens have natural break opportunities at punctuation without severing words mid-syllable
    const formatted = text.replace(/([,:]|(?<=\w)\/)(?=[^\s\d])/g, "$1 ");
    return formatted.trim().split(/\s+/).filter(Boolean);
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

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={`${text}-${activeStyle}`}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0, transition: { duration: 0.15 } }}
        className={`flex min-w-0 w-full max-w-full flex-wrap items-center justify-center gap-x-2 gap-y-1.5 text-center select-none ${
          activeStyle === "calm_breathe" ? "animate-calm-float" : ""
        } ${className}`}
        style={{
          transform: "translateZ(0)",
          overflowWrap: "break-word",
          wordBreak: "break-word",
        }}
      >
        {words.map((word, wordIdx) => {
          const delay = isLongText ? Math.min(0.2, wordIdx * 0.016) : wordIdx * 0.026;
          const isHighlighted =
            (highlightWords.length > 0 &&
              highlightWords.some((hw) => word.toLowerCase().includes(hw.toLowerCase()))) ||
            word.startsWith("@") ||
            word.startsWith("#") ||
            (words.length <= 6 && (word.startsWith('"') || word.endsWith('"') || (words.length <= 3 && wordIdx === 0)));

          let initialProps: any = { opacity: 0, y: 10 };
          let animateProps: any = { opacity: 1, y: 0 };
          let transitionProps: any = { duration: 0.45, delay, ease: [0.16, 1, 0.3, 1] };
          let customWordStyle: React.CSSProperties = {};

          switch (activeStyle) {
            case "silk_blur":
              // 1. Silk Emerge: Optical transform dissipation & gentle drift (zero blur overhead)
              initialProps = { opacity: 0, y: 8, scale: 0.98 };
              animateProps = { opacity: 1, y: 0, scale: 1 };
              transitionProps = { duration: 0.38, delay, ease: [0.16, 1, 0.3, 1] };
              break;

            case "fluid_glide":
              // 2. Liquid Glide: Organic critically-damped spring upward glide with zero bounce
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
              // 3. Specular Sheen: Refined metallic light sweep across typography
              initialProps = { opacity: 0, y: 8 };
              animateProps = { opacity: 1, y: 0 };
              transitionProps = { duration: 0.42, delay, ease: [0.22, 1, 0.36, 1] };
              customWordStyle = shimmerStyle;
              break;

            case "calm_breathe":
              // 4. Serene Float: Smooth stagger entrance (continuous float handled on compositor thread)
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

          return (
            <motion.span
              key={`${word}-${wordIdx}`}
              initial={initialProps}
              animate={animateProps}
              transition={transitionProps}
              className={`inline-block min-w-0 max-w-full font-medium tracking-tight ${
                activeStyle === "ambient_shimmer" ? "animate-shimmer-sweep" : ""
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
                willChange: "transform, opacity",
                ...(isHighlighted ? highlightedStyle : {}),
                ...customWordStyle,
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
  const words = useMemo(() => (text ? text.trim().split(" ") : []), [text]);

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
            style={{
              willChange: "transform, opacity",
              ...(isLatest
                ? {
                    background: theme.gradient,
                    boxShadow: `0 2px 10px ${theme.glow}`,
                  }
                : {}),
            }}
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
 * Kinetic State Pill with Shimmering Light Sweep and Harmonic Wave
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
      style={{ willChange: "transform, opacity" }}
    >
      {/* Dynamic Animated Pulse Dot */}
      <span className="relative flex h-2 w-2">
        {pulse && (
          <span
            className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75"
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
