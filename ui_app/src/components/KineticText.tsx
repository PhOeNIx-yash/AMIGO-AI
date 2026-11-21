import React, { useMemo } from "react";
import { motion, AnimatePresence, type Variants } from "motion/react";
import { ColorTheme } from "../types";
import { COLOR_THEMES } from "../data/presets";

const staggeredContainerVariants: Variants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.045, // 45ms per block
      delayChildren: 0.02,
    },
  },
};

const staggeredItemVariants: Variants = {
  hidden: { opacity: 0, y: 6 },
  visible: {
    opacity: 1,
    y: 0,
    transition: {
      duration: 0.22, // 220ms ease-out
      ease: [0.16, 1, 0.3, 1],
    },
  },
};

export function renderInlineFormatting(text: string, heroMode = false): React.ReactNode {
  if (!text) return null;

  // Split on code `...`, bold **...**, links [text](url), or raw URLs
  const tokens = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^\)]+\)|https?:\/\/[^\s\)]+)/g);

  return tokens.map((part, index) => {
    if (!part) return null;

    // Inline code, metrics, or numbers in backticks: `...`
    if (part.startsWith("`") && part.endsWith("`") && part.length >= 2) {
      return (
        <code
          key={index}
          className="inline-block px-1.5 py-[1px] mx-0.5 rounded text-[13px] font-mono font-medium bg-[#2dd4bf]/10 text-[#5eead4] border border-[#2dd4bf]/20 align-baseline"
        >
          {part.slice(1, -1)}
        </code>
      );
    }

    // Bold text: **...**
    if (part.startsWith("**") && part.endsWith("**") && part.length >= 4) {
      return (
        <strong
          key={index}
          className="font-semibold text-[#ffffff] mr-0.5"
          style={
            heroMode
              ? {
                  textShadow:
                    "0 0 18px rgba(45, 212, 191, 0.35), 0 2px 16px rgba(0, 0, 0, 0.95)",
                }
              : undefined
          }
        >
          {part.slice(2, -2)}
        </strong>
      );
    }

    // Markdown link: [Title](URL)
    const mdLink = part.match(/^\[([^\]]+)\]\((https?:\/\/[^\s\)]+)\)$/);
    if (mdLink) {
      return (
        <a
          key={index}
          href={mdLink[2]}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-0.5 px-2 py-0.5 rounded bg-white/[0.04] hover:bg-[#2dd4bf]/10 text-[12.5px] font-medium text-[#94a3b8] hover:text-[#2dd4bf] transition-colors cursor-pointer border border-white/[0.06] hover:border-[#2dd4bf]/20"
        >
          {mdLink[1]}
        </a>
      );
    }

    // Raw URL
    if (/^https?:\/\/[^\s\)]+$/.test(part)) {
      return (
        <a
          key={index}
          href={part}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-0.5 px-2 py-0.5 rounded bg-white/[0.04] hover:bg-[#2dd4bf]/10 text-[12.5px] font-medium text-[#94a3b8] hover:text-[#2dd4bf] transition-colors break-all cursor-pointer border border-white/[0.06] hover:border-[#2dd4bf]/20"
        >
          {part}
        </a>
      );
    }

    return <span key={index}>{part}</span>;
  });
}

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
  turnId?: string;
  isHero?: boolean;
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
  turnId,
  isHero,
}) => {
  const containerRef = React.useRef<HTMLDivElement>(null);

  // Hard reset scroll position whenever text or turnId changes
  React.useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = 0;
    }
  }, [text, turnId]);

  const activeStyle = normalizeAnimationStyle(animationStyle);
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;
  const words = useMemo(() => {
    if (!text) return [];
    return text.trim().split(/\s+/).filter(Boolean);
  }, [text]);
  const isLongText = words.length > 12;

  const isShortDirect = isHero !== undefined ? isHero : (() => {
    if (!text) return true;
    const raw = text.trim();
    if (raw.length > 240) return false;
    if (raw.includes("|") && raw.includes("\n")) return false;
    if (/^[•\-*]\s+|\b^\d+\.\s+/m.test(raw)) return false;
    const paragraphs = raw.split(/\n+/).map((l) => l.trim()).filter(Boolean);
    return paragraphs.length <= 1;
  })();

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

  const { tableData, listItems } = useMemo(() => {
    if (!text) return { tableData: null, listItems: [] as string[] };

    let processed = text;

    // 1. Check for markdown data tables
    if (processed.includes("|")) {
      const inlineRows = processed
        .replace(/\|\s*\|+/g, "|\n|")
        .replace(/(?:^|\n)\s*\|?[\s\-:|]{3,}\|[\s\-:|]*(?=\n|$)/g, "");

      const rawLines = inlineRows.split(/\n+/).map((l) => l.trim()).filter(Boolean);
      let foundHeaders: string[] = [];
      const foundRows: string[][] = [];
      let foundTitle = "";

      for (const line of rawLines) {
        if (line.includes("|")) {
          const pipePos = line.indexOf("|");
          const pre = line.slice(0, pipePos).trim();
          const tablePart = line.slice(pipePos);

          if (pre && !foundTitle) {
            foundTitle = pre.replace(/#{1,6}\s*/g, "").trim();
          }

          const cells = tablePart.split("|").map((c) => c.trim()).filter(Boolean);
          if (cells.length === 0) continue;

          if (foundHeaders.length === 0) {
            foundHeaders = cells;
          } else {
            foundRows.push(cells);
          }
        }
      }

      if (foundHeaders.length >= 2 && foundRows.length >= 1) {
        return {
          tableData: {
            title: foundTitle,
            headers: foundHeaders,
            rows: foundRows,
          },
          listItems: [],
        };
      }
    }

    // 2. Check for multi-item lists (numbered or bullet points), expanding ## and inline lists
    const cleaned = processed
      .replace(/\s*##+\s*/g, "\n")
      .replace(/^[#]+\s*/gm, "")
      .replace(/(?<=[.:?!])\s+(\d+\.\s+)/g, "\n$1");

    const lines = cleaned
      .split(/\n+/)
      .map((l) => l.trim())
      .filter(Boolean);

    const bulletCount = lines.filter((l) => /^[•\-*]\s+|\b^\d+\.\s+/.test(l)).length;
    if (bulletCount >= 1 || lines.length >= 2) {
      return { tableData: null, listItems: lines };
    }

    return { tableData: null, listItems: [] };
  }, [text]);

  const uniqueTurnKey = `${turnId || "turn"}-${text.slice(0, 32)}-${activeStyle}`;

  if (tableData) {
    return (
      <AnimatePresence mode="wait">
        <motion.div
          key={uniqueTurnKey}
          ref={containerRef}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, transition: { duration: 0.15 } }}
          className="relative w-full max-w-[680px] mx-auto text-left py-1 select-text"
          style={{
            textShadow: "0 2px 16px rgba(0, 0, 0, 0.95)",
          }}
        >
          {tableData.title && (
            <h3 className="text-[18px] font-semibold text-[#ffffff] mb-3 tracking-tight">
              {tableData.title}
            </h3>
          )}
          <div className="w-full overflow-x-auto no-scrollbar">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-white/[0.08]">
                  {tableData.headers.map((h, i) => (
                    <th
                      key={i}
                      className="py-2.5 px-3 text-[12px] uppercase font-semibold text-[#2dd4bf] tracking-[0.06em]"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.08]">
                {tableData.rows.map((row, rIdx) => (
                  <tr key={rIdx} className="hover:bg-white/[0.02] transition-colors">
                    {row.map((cell, cIdx) => (
                      <td
                        key={cIdx}
                        className={`py-2 px-3 text-[15.5px] leading-relaxed ${
                          cIdx === 0 ? "font-medium text-[#ffffff]" : "text-[#cbd5e1]"
                        }`}
                      >
                        {renderInlineFormatting(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </motion.div>
      </AnimatePresence>
    );
  }

  if (listItems.length > 1) {
    const firstBulletIdx = listItems.findIndex((l) => /^[•\-*]|\b^\d+\./.test(l));
    let lastBulletIdx = -1;
    for (let i = 0; i < listItems.length; i++) {
      if (/^[•\-*]|\b^\d+\./.test(listItems[i])) {
        lastBulletIdx = i;
      }
    }

    return (
      <AnimatePresence mode="wait">
        <motion.div
          key={uniqueTurnKey}
          ref={containerRef}
          variants={staggeredContainerVariants}
          initial="hidden"
          animate="visible"
          exit={{ opacity: 0, transition: { duration: 0.15 } }}
          className="relative w-full max-w-[680px] mx-auto text-left py-1 select-text"
          style={{
            textShadow: "0 2px 16px rgba(0, 0, 0, 0.95)",
          }}
        >
          <div className="w-full">
            {listItems.map((item, idx) => {
              const cleanItem = item.replace(/##+/g, "").replace(/^#+\s*/, "").trim();
              if (!cleanItem) return null;
              const numMatch = cleanItem.match(/^(\d+)\.\s+/);
              const isBullet = /^[•\-*]|\b^\d+\./.test(cleanItem);

              // Check for source/citation row
              const isSourceLine =
                /^(?:Sources?|References?|Citations?):/i.test(cleanItem) ||
                /^\s*\[?(?:Source|Ref)\s*\d*\]?:/i.test(cleanItem);

              if (isSourceLine) {
                return (
                  <motion.div
                    key={idx}
                    variants={staggeredItemVariants}
                    className="flex flex-wrap items-center gap-2 mt-4 pt-2.5 text-[12.5px] text-[#94a3b8] border-t border-white/[0.08] whitespace-pre-wrap break-words"
                  >
                    <span className="text-[11px] font-mono uppercase tracking-wider text-[#2dd4bf]/80">Sources:</span>
                    {renderInlineFormatting(cleanItem.replace(/^(?:Sources?|References?|Citations?):\s*/i, ""))}
                  </motion.div>
                );
              }

              if (isBullet) {
                let content = cleanItem.replace(/^(\d+\.|[•\-*])\s*/, "");
                
                // Highlight lead-in phrases: Label: desc or **Label**: desc
                const boldMatch = content.match(/^(\*\*[^*]+\*\*)\s*(?:[:\-—]\s*)([\s\S]+)$/);
                const colonMatch = !boldMatch ? content.match(/^([^:\-—*#]{2,45})\s*(?:[:\-—]\s*)([\s\S]+)$/) : null;
                const leadingBoldMatch = !boldMatch && !colonMatch ? content.match(/^(\*\*[^*]+\*\*)([\s\S]*)$/) : null;

                let label = "";
                let detail = content;

                if (boldMatch) {
                  label = boldMatch[1].replace(/\*\*/g, "").trim();
                  detail = boldMatch[2].trim();
                } else if (colonMatch) {
                  label = colonMatch[1].replace(/\*\*/g, "").trim();
                  detail = colonMatch[2].trim();
                } else if (leadingBoldMatch) {
                  label = leadingBoldMatch[1].replace(/\*\*/g, "").trim();
                  detail = leadingBoldMatch[2].trim();
                }

                return (
                  <motion.div
                    key={idx}
                    variants={staggeredItemVariants}
                    className="flex items-start gap-3 my-2 text-[16px] sm:text-[16.5px] leading-[1.65] font-normal text-left"
                  >
                    {/* Clean teal dot bullets (#2dd4bf) with hanging indents and 8px vertical spacing */}
                    {numMatch ? (
                      <span className="font-mono text-[13px] font-semibold text-[#2dd4bf] select-none shrink-0 w-5 text-right pt-[2px]">
                        {numMatch[1]}.
                      </span>
                    ) : (
                      <span
                        className="w-1.5 h-1.5 rounded-full bg-[#2dd4bf] shrink-0 select-none mt-[10px] shadow-[0_0_8px_rgba(45,212,191,0.6)]"
                        aria-hidden="true"
                      />
                    )}
                    <div className="flex-1 min-w-0">
                      {label ? (
                        <>
                          <span className="text-[#ffffff] font-semibold mr-1.5 whitespace-pre-wrap">{label}:</span>
                          <span className="text-[#e2e8f0] font-normal whitespace-pre-wrap break-words">
                            {renderInlineFormatting(detail)}
                          </span>
                        </>
                      ) : (
                        <span className="text-[#e2e8f0] font-normal whitespace-pre-wrap break-words">
                          {renderInlineFormatting(content)}
                        </span>
                      )}
                    </div>
                  </motion.div>
                );
              }

              const cleanParagraph = cleanItem.replace(/\*\*([^*]+)\*\*/g, "$1");
              const isLead = idx === 0 || (firstBulletIdx !== -1 && idx < firstBulletIdx);
              const isClosing = (lastBulletIdx !== -1 && idx > lastBulletIdx) || idx === listItems.length - 1;

              if (isLead) {
                return (
                  <motion.h3
                    key={idx}
                    variants={staggeredItemVariants}
                    className="text-[18px] font-semibold text-[#ffffff] mb-3 text-left tracking-tight leading-snug whitespace-pre-wrap break-words"
                  >
                    {cleanParagraph}
                  </motion.h3>
                );
              }

              if (isClosing) {
                return (
                  <motion.p
                    key={idx}
                    variants={staggeredItemVariants}
                    className="text-[15.5px] font-normal leading-[1.6] text-[#94a3b8] mt-3 text-left whitespace-pre-wrap break-words"
                  >
                    {renderInlineFormatting(cleanParagraph)}
                  </motion.p>
                );
              }

              return (
                <motion.p
                  key={idx}
                  variants={staggeredItemVariants}
                  className="text-[16px] sm:text-[16.5px] font-normal leading-[1.65] text-[#e2e8f0] my-2 text-left whitespace-pre-wrap break-words"
                >
                  {renderInlineFormatting(cleanParagraph)}
                </motion.p>
              );
            })}
          </div>
        </motion.div>
      </AnimatePresence>
    );
  }

  // Short / Direct Answers (Hero Mode, center-aligned under orb)
  if (isShortDirect) {
    return (
      <AnimatePresence mode="wait">
        <motion.div
          key={uniqueTurnKey}
          ref={containerRef}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, transition: { duration: 0.15 } }}
          className="relative w-full max-w-[700px] mx-auto text-center py-1 select-text"
          style={{
            textShadow: "0 2px 16px rgba(0, 0, 0, 0.95)",
          }}
        >
          <p className="text-[20px] sm:text-[22px] leading-[1.5] font-normal text-[#f1f5f9] text-center whitespace-pre-wrap break-words">
            {renderInlineFormatting(text, true)}
          </p>
        </motion.div>
      </AnimatePresence>
    );
  }

  // Structured / Multi-paragraph answers without bullet lists (left-aligned in 680px column)
  if (listItems.length <= 1 && text && (text.length > 55 || text.includes("\n"))) {
    return (
      <AnimatePresence mode="wait">
        <motion.div
          key={uniqueTurnKey}
          ref={containerRef}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, transition: { duration: 0.15 } }}
          className="relative w-full max-w-[680px] mx-auto text-left py-1 select-text"
          style={{
            textShadow: "0 2px 16px rgba(0, 0, 0, 0.95)",
          }}
        >
          <div className="w-full space-y-3">
            {text.split(/\n+/).map((para, pIdx) => {
              const cleanPara = para.trim();
              if (!cleanPara) return null;
              return (
                <p
                  key={pIdx}
                  className="text-[16px] sm:text-[16.5px] font-normal leading-[1.65] text-[#e2e8f0] text-left whitespace-pre-wrap break-words"
                >
                  {renderInlineFormatting(cleanPara)}
                </p>
              );
            })}
          </div>
        </motion.div>
      </AnimatePresence>
    );
  }

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={uniqueTurnKey}
        ref={containerRef}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0, transition: { duration: 0.15 } }}
        className={`flex min-w-0 w-full max-w-[700px] mx-auto flex-wrap items-center justify-center gap-x-2 gap-y-1.5 text-center select-none ${
          activeStyle === "calm_breathe" ? "motion-safe:animate-calm-float" : ""
        } ${className}`}
        style={{
          overflowWrap: "break-word",
          wordBreak: "break-word",
          textShadow: "0 2px 16px rgba(0, 0, 0, 0.95)",
        }}
      >
        {words.map((word, wordIdx) => {
          const delay = isLongText ? Math.min(0.2, wordIdx * 0.016) : wordIdx * 0.026;
          const displayWord = word.replace(/^\*+|\*+$/g, "").replace(/^#+/, "");
          if (!displayWord) return null;
          const cleanWord = displayWord.replace(/^[^\w]+|[^\w]+$/g, "").toLowerCase();
          const isHighlighted =
            (highlightWords.length > 0 &&
              highlightWords.some((hw) => {
                const cleanHw = hw.trim().toLowerCase();
                return cleanHw.length >= 2 && cleanWord === cleanHw;
              })) ||
            displayWord.startsWith("@");

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
              {displayWord}
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
