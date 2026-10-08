import React, {
  Component,
  forwardRef,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ComponentProps,
  type InputHTMLAttributes,
  type ReactNode,
} from "react"
import { Warp, type WarpProps } from "@paper-design/shaders-react"
import {
  AnimatePresence,
  motion,
  useInView,
  useReducedMotion,
} from "motion/react"

import { cn } from "@/lib/utils"

/** Placeholder line: snappy stagger. */
const PLACEHOLDER_STAGGER_SEC = 0.018
const PLACEHOLDER_CHAR_DURATION_SEC = 0.16

/** Loading line: deliberate reveal. */
const DEFAULT_LOADING_STAGGER_SEC = 0.038
const DEFAULT_LOADING_CHAR_DURATION_SEC = 0.36

/** Clear control ↔ spinner crossfade / shared layout. */
const TRAILING_ACTION_DURATION_SEC = 0.2

/** Caps right-to-left deletion stagger so long clears stay snappy. */
const DELETION_MAX_STAGGER_SPAN_SEC = 0.75
const MAX_ANIMATED_DELETION_GLYPHS = 50

/**
 * Per-glyph blur timing for `DeletedTextBlurReveal`.
 */
function getDeletionGlyphTiming(args: {
  sweepDurationMs: number
  animatedCount: number
  staggerGlyphCount: number
}) {
  const sweepDurationSec = args.sweepDurationMs / 1000
  const animatedCount = Math.max(1, args.animatedCount)
  const staggerCount = Math.max(1, args.staggerGlyphCount)

  const normalizedLength = Math.min(1, Math.max(0, (animatedCount - 1) / 60))
  const easeOutLength = 1 - (1 - normalizedLength) ** 3
  const speedFactor = 1 - 0.6 * easeOutLength

  const glyphDurationSec = Math.max(
    0.12,
    Math.max(0.22, sweepDurationSec * 0.45) * speedFactor
  )
  let glyphDelayStepSec = Math.max(
    0.004,
    Math.max(0.012, sweepDurationSec * 0.03) * speedFactor
  )

  const gapCount = Math.max(0, staggerCount - 1)
  const uncappedStaggerSpan = gapCount * glyphDelayStepSec
  if (
    uncappedStaggerSpan > DELETION_MAX_STAGGER_SPAN_SEC &&
    uncappedStaggerSpan > 0
  ) {
    glyphDelayStepSec *= DELETION_MAX_STAGGER_SPAN_SEC / uncappedStaggerSpan
  }

  const staggerSpanSec = gapCount * glyphDelayStepSec
  return { glyphDurationSec, glyphDelayStepSec, staggerSpanSec }
}

export interface HaloSearchInputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "defaultValue"> {
  defaultValue?: string
  onClear?: () => void
  onCancel?: () => void
  /**
   * Frosted, semi-transparent fill so animated gradient blobs show through more clearly.
   * Also applied automatically while `isLoading`.
   */
  blobTranslucent?: boolean
  /**
   * When true, query text is hidden and staggered loading copy is shown.
   */
  isLoading?: boolean
  /** Copy shown while `isLoading`; defaults to “Searching…”. */
  loadingText?: string
  /** Delay between each loading character’s fade-in (seconds). Default 0.038. */
  loadingStaggerSec?: number
  /** Duration of each loading character’s fade-in (seconds). Default 0.36. */
  loadingCharDurationSec?: number
  leftSlot?: React.ReactNode
  rightSlot?: React.ReactNode
  isListening?: boolean
  listeningText?: string
}

function XIcon({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      focusable="false"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
    >
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  )
}

function SearchFieldSpinner({
  className,
  reduceMotion,
}: {
  className?: string
  reduceMotion: boolean
}) {
  return (
    <span
      className={cn(
        "inline-flex origin-center text-muted-foreground",
        !reduceMotion && "animate-spin",
        className
      )}
    >
      <svg
        aria-hidden="true"
        className="h-3.5 w-3.5"
        focusable="false"
        viewBox="0 0 24 24"
      >
        <circle
          className="opacity-[0.22]"
          cx="12"
          cy="12"
          fill="none"
          r="9"
          stroke="currentColor"
          strokeWidth="2.25"
        />
        <circle
          cx="12"
          cy="12"
          fill="none"
          r="9"
          stroke="currentColor"
          strokeDasharray="14 42"
          strokeLinecap="round"
          strokeWidth="2.25"
        />
      </svg>
    </span>
  )
}

const trailingMotionTransition = (reduceMotion: boolean) => ({
  duration: reduceMotion ? 0 : TRAILING_ACTION_DURATION_SEC,
  ease: "easeOut" as const,
})

const trailingCrossfade = (reduceMotion: boolean) => ({
  exit: {
    opacity: reduceMotion ? 1 : 0,
    scale: reduceMotion ? 1 : 0.88,
  },
  initial: {
    opacity: reduceMotion ? 1 : 0,
    scale: reduceMotion ? 1 : 0.88,
  },
})

// forwardRef required for popLayout child measuring in React 18
const TrailingLoadingSlot = forwardRef<
  HTMLButtonElement,
  { reduceMotion: boolean; layoutId: string; onCancel?: () => void }
>(function TrailingLoadingSlot({ reduceMotion, layoutId, onCancel }, ref) {
  const cross = trailingCrossfade(reduceMotion)
  return (
    <motion.button
      ref={ref}
      animate={{ opacity: 1, scale: 1 }}
      aria-label="Cancel search"
      className={cn(
        "absolute inset-0 flex items-center justify-center rounded-full cursor-pointer",
        "bg-muted/80 text-muted-foreground shadow-sm ring-1 ring-foreground/10",
        "hover:bg-accent hover:text-accent-foreground transition-colors duration-150"
      )}
      exit={cross.exit}
      initial={cross.initial}
      key="trailing-loading"
      layoutId={layoutId}
      onClick={onCancel}
      title="Cancel search"
      transition={trailingMotionTransition(reduceMotion)}
      type="button"
    >
      <SearchFieldSpinner reduceMotion={reduceMotion} />
    </motion.button>
  )
})

// forwardRef required for popLayout child measuring in React 18
const TrailingClearSlot = forwardRef<
  HTMLButtonElement,
  { reduceMotion: boolean; onClear: () => void; layoutId: string }
>(function TrailingClearSlot({ reduceMotion, onClear, layoutId }, ref) {
  const cross = trailingCrossfade(reduceMotion)
  return (
    <motion.button
      ref={ref}
      animate={{ opacity: 1, scale: 1 }}
      aria-label="Clear search"
      className={cn(
        "absolute inset-0 flex items-center justify-center rounded-full cursor-pointer",
        "bg-muted text-muted-foreground",
        "shadow-foreground/10 shadow-sm ring-1 ring-foreground/10",
        "hover:bg-accent hover:text-accent-foreground",
        "transition-[background-color,color] duration-150 ease-out"
      )}
      exit={cross.exit}
      initial={cross.initial}
      key="trailing-clear"
      layoutId={layoutId}
      onClick={onClear}
      title="Clear text"
      transition={trailingMotionTransition(reduceMotion)}
      type="button"
    >
      <XIcon className="h-3.5 w-3.5" />
    </motion.button>
  )
})

function SearchTrailingAction({
  hasValue,
  isLoading,
  reduceMotion,
  layoutId,
  onClear,
  onCancel,
}: {
  hasValue: boolean
  isLoading: boolean
  reduceMotion: boolean
  layoutId: string
  onClear: () => void
  onCancel?: () => void
}) {
  if (!(hasValue || isLoading)) {
    return null
  }

  return (
    <div className="relative size-6 shrink-0">
      <AnimatePresence initial={false} mode="popLayout">
        {isLoading ? (
          <TrailingLoadingSlot
            key="loading"
            layoutId={layoutId}
            onCancel={onCancel || onClear}
            reduceMotion={reduceMotion}
          />
        ) : (
          <TrailingClearSlot
            key="clear"
            layoutId={layoutId}
            onClear={onClear}
            reduceMotion={reduceMotion}
          />
        )}
      </AnimatePresence>
    </div>
  )
}

/** Overlay placeholder with overflow truncation protection. */
function AnimatedPlaceholder({
  text,
  animateKey,
  reduceMotion,
  staggerSec = PLACEHOLDER_STAGGER_SEC,
  charDurationSec = PLACEHOLDER_CHAR_DURATION_SEC,
}: {
  text: string
  animateKey: number
  reduceMotion: boolean
  staggerSec?: number
  charDurationSec?: number
}) {
  const chars = useMemo(() => {
    const counts = new Map<string, number>()
    return Array.from(text, (char) => {
      const count = counts.get(char) ?? 0
      counts.set(char, count + 1)
      return { char, key: `${char}-${count}` }
    })
  }, [text])

  if (reduceMotion) {
    return (
      <span className="pointer-events-none absolute inset-y-0 left-0 pr-10 flex items-center text-muted-foreground select-none truncate overflow-hidden">
        {text}
      </span>
    )
  }

  return (
    <motion.span
      animate="visible"
      className="pointer-events-none absolute inset-y-0 left-0 pr-10 flex items-center text-muted-foreground select-none truncate overflow-hidden"
      initial="hidden"
      key={animateKey}
      variants={{
        hidden: {},
        visible: {
          transition: {
            staggerChildren: staggerSec,
          },
        },
      }}
    >
      {chars.map((glyph) => (
        <motion.span
          className="inline-block whitespace-pre"
          key={glyph.key}
          transition={{
            duration: charDurationSec,
            ease: "easeOut",
          }}
          variants={{
            hidden: { opacity: 0 },
            visible: { opacity: 1 },
          }}
        >
          {glyph.char}
        </motion.span>
      ))}
    </motion.span>
  )
}

export const HaloSearchInput = forwardRef<
  HTMLInputElement,
  HaloSearchInputProps
>(function HaloSearchInput(
  {
    className,
    value,
    defaultValue,
    onChange,
    onClear,
    onCancel,
    placeholder = "Ask anything or search",
    blobTranslucent = false,
    isLoading = false,
    loadingText = "Searching…",
    loadingStaggerSec = DEFAULT_LOADING_STAGGER_SEC,
    loadingCharDurationSec = DEFAULT_LOADING_CHAR_DURATION_SEC,
    leftSlot,
    rightSlot,
    isListening = false,
    listeningText = "",
    disabled,
    readOnly,
    id,
    name,
    autoFocus,
    ...props
  },
  forwardedRef
) {
  const uniqueInstanceId = useId()
  const trailingLayoutId = `${uniqueInstanceId}-halo-search-trailing`

  const showBlobTranslucent = blobTranslucent || isLoading
  const [internalValue, setInternalValue] = useState(defaultValue ?? "")
  const [isFocused, setIsFocused] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deletionGhostText, setDeletionGhostText] = useState<string | null>(null)
  const [deletionIndexes, setDeletionIndexes] = useState<number[]>([])
  const [deletionKey, setDeletionKey] = useState(0)
  const [placeholderAnimKey, setPlaceholderAnimKey] = useState(0)
  const [loadingAnimKey, setLoadingAnimKey] = useState(0)

  const inputRef = useRef<HTMLInputElement | null>(null)
  const deletionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const typingIdleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isComposingRef = useRef(false)
  const [isTypingBurst, setIsTypingBurst] = useState(false)

  const wasDeletingRef = useRef(false)
  const wasLoadingRef = useRef(false)
  const reduceMotion = useReducedMotion() ?? false
  const deleteClipDurationMs = 320
  const deleteSweepDurationMs = deleteClipDurationMs + deleteClipDurationMs / 2

  // Page visibility awareness: pause continuous effects when document is hidden
  const [isPageVisible, setIsPageVisible] = useState(
    typeof document !== "undefined" ? document.visibilityState === "visible" : true
  )
  useEffect(() => {
    const handleVisibility = () => {
      setIsPageVisible(document.visibilityState === "visible")
    }
    document.addEventListener("visibilitychange", handleVisibility)
    return () => document.removeEventListener("visibilitychange", handleVisibility)
  }, [])

  // Merge forwarded ref with internal ref
  const setInputRef = useCallback(
    (node: HTMLInputElement | null) => {
      inputRef.current = node
      if (typeof forwardedRef === "function") {
        forwardedRef(node)
      } else if (forwardedRef) {
        forwardedRef.current = node
      }
    },
    [forwardedRef]
  )

  // Fix: Reset isFocused whenever listening starts or disabled changes (Chrome ignores blur on disabled/unmount)
  useEffect(() => {
    if (isListening || disabled) {
      setIsFocused(false)
    }
  }, [isListening, disabled])

  // Clean up timers on unmount
  useEffect(() => {
    return () => {
      setIsFocused(false)
      if (deletionTimerRef.current) clearTimeout(deletionTimerRef.current)
      if (typingIdleTimerRef.current) clearTimeout(typingIdleTimerRef.current)
    }
  }, [])

  // Controlled vs uncontrolled: sanitize null/undefined
  const currentValue = value === undefined ? internalValue : (value ?? "")
  const hasValue = String(currentValue).length > 0

  // Replay placeholder stagger once empty again
  useEffect(() => {
    if (wasDeletingRef.current && !isDeleting && !hasValue) {
      setPlaceholderAnimKey((k) => k + 1)
    }
    wasDeletingRef.current = isDeleting
  }, [hasValue, isDeleting])

  // Replay loading line stagger each time loading starts
  useEffect(() => {
    if (isLoading && !wasLoadingRef.current) {
      setLoadingAnimKey((k) => k + 1)
    }
    wasLoadingRef.current = isLoading
  }, [isLoading])

  const scheduleDeletionEnd = useCallback(
    (deletedCount: number) => {
      if (deletionTimerRef.current) {
        clearTimeout(deletionTimerRef.current)
      }

      const { glyphDurationSec, staggerSpanSec } = getDeletionGlyphTiming({
        sweepDurationMs: deleteSweepDurationMs,
        animatedCount: deletedCount,
        staggerGlyphCount: deletedCount,
      })
      const totalGlyphRunMs = (staggerSpanSec + glyphDurationSec) * 1000
      const teardownDelayMs =
        Math.max(deleteSweepDurationMs, totalGlyphRunMs) + 120

      deletionTimerRef.current = setTimeout(() => {
        setIsDeleting(false)
        setDeletionGhostText(null)
        setDeletionIndexes([])
        deletionTimerRef.current = null
      }, teardownDelayMs)
    },
    [deleteSweepDurationMs]
  )

  const handleChange = (e: ChangeEvent<HTMLInputElement>) => {
    const newValue = e.target.value
    const prevValue = String(currentValue)
    const newLength = newValue.length
    const prevLen = prevValue.length

    // Fix: Immediately terminate deletion overlay if user resumes typing
    if (newLength > 0 && isDeleting) {
      if (deletionTimerRef.current) {
        clearTimeout(deletionTimerRef.current)
        deletionTimerRef.current = null
      }
      setIsDeleting(false)
      setDeletionGhostText(null)
      setDeletionIndexes([])
    }

    // Full clear (select all + delete) with glyph cap to avoid expensive render
    if (
      newLength === 0 &&
      prevLen > 0 &&
      !reduceMotion &&
      !isComposingRef.current
    ) {
      const codeUnits = Array.from(prevValue)
      if (codeUnits.length <= MAX_ANIMATED_DELETION_GLYPHS) {
        setDeletionIndexes(Array.from({ length: codeUnits.length }, (_, idx) => idx))
        setDeletionGhostText(prevValue)
        setDeletionKey((k) => k + 1)
        setIsDeleting(true)
        scheduleDeletionEnd(codeUnits.length)
      }
    }

    if (onChange) {
      onChange(e)
    } else {
      setInternalValue(newValue)
    }

    if (!reduceMotion && newLength > 0) {
      if (typingIdleTimerRef.current) {
        clearTimeout(typingIdleTimerRef.current)
      }
      setIsTypingBurst(true)
      typingIdleTimerRef.current = setTimeout(() => {
        setIsTypingBurst(false)
        typingIdleTimerRef.current = null
      }, 420)
    }
  }

  const handleClear = () => {
    if (typingIdleTimerRef.current) {
      clearTimeout(typingIdleTimerRef.current)
      typingIdleTimerRef.current = null
    }
    setIsTypingBurst(false)

    const strVal = String(currentValue)
    const codeUnits = Array.from(strVal)
    if (hasValue && !reduceMotion && codeUnits.length <= MAX_ANIMATED_DELETION_GLYPHS) {
      setDeletionIndexes(Array.from({ length: codeUnits.length }, (_, idx) => idx))
      setDeletionGhostText(strVal)
      setDeletionKey((k) => k + 1)
      setIsDeleting(true)
      scheduleDeletionEnd(codeUnits.length)
    } else {
      if (deletionTimerRef.current) clearTimeout(deletionTimerRef.current)
      setIsDeleting(false)
      setDeletionGhostText(null)
      setDeletionIndexes([])
    }

    if (onClear) {
      onClear()
    } else {
      setInternalValue("")
    }
    inputRef.current?.focus()
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape" && hasValue) {
      e.preventDefault()
      handleClear()
    }
    props.onKeyDown?.(e)
  }

  // Border glow using Amigo cyan/indigo/sky palette with GPU transforms (translateX)
  const borderGlow = useMemo(() => {
    const isAnimActive = isFocused || isLoading || isListening || isDeleting

    if (isDeleting) {
      return {
        opacityIdle: 0.85,
        primaryBg: "linear-gradient(90deg, #38bdf8, #818cf8, #a855f7, #06b6d4)",
        secondaryBg: "linear-gradient(90deg, #0ea5e9, #6366f1, #38bdf8)",
        tertiaryBg: "linear-gradient(90deg, #0284c7, #4f46e5, #0ea5e9)",
        xPrimary: ["-10%", "90%", "-10%"],
        xSecondary: ["70%", "-10%", "70%"],
        xTertiary: ["30%", "60%", "30%"],
        durationMain: 2.2,
        durationSec: 1.8,
        durationTer: 1.5,
        isAnimActive,
      }
    }
    if (isLoading) {
      return {
        opacityIdle: 0.95,
        primaryBg: "linear-gradient(90deg, #00d4ff, #6366f1, #38bdf8, #0ea5e9)",
        secondaryBg: "linear-gradient(90deg, #38bdf8, #818cf8, #00d4ff)",
        tertiaryBg: "linear-gradient(90deg, #0284c7, #00d4ff, #6366f1)",
        xPrimary: ["-15%", "85%", "-15%"],
        xSecondary: ["75%", "-10%", "75%"],
        xTertiary: ["15%", "65%", "15%"],
        durationMain: 2.0,
        durationSec: 1.6,
        durationTer: 1.4,
        isAnimActive: true,
      }
    }

    const idleOpacity = showBlobTranslucent
      ? (isFocused ? 0.90 : 0.65)
      : (isFocused ? 0.75 : 0.35)

    return {
      opacityIdle: idleOpacity,
      primaryBg: "linear-gradient(90deg, #00d4ff, #6366f1, #38bdf8, #0ea5e9)",
      secondaryBg: "linear-gradient(90deg, #38bdf8, #818cf8, #00d4ff)",
      tertiaryBg: "linear-gradient(90deg, #0284c7, #00d4ff, #6366f1)",
      xPrimary: ["-5%", "75%", "-5%"],
      xSecondary: ["65%", "10%", "65%"],
      xTertiary: ["25%", "55%", "25%"],
      durationMain: 5.5,
      durationSec: 4.8,
      durationTer: 3.8,
      isAnimActive,
    }
  }, [showBlobTranslucent, isDeleting, isFocused, isLoading, isListening])

  const shouldAnimateGlow =
    borderGlow.isAnimActive && isPageVisible && !reduceMotion

  const accessibleLabel =
    (props["aria-label"] as string) ||
    (typeof placeholder === "string" && placeholder
      ? placeholder
      : "Search or ask anything")

  return (
    <div className={cn("relative w-full max-w-2xl", className)}>
      {/* Outer container */}
      <div className="relative rounded-full p-px">
        {/* Animated GPU-composited gradient glow layer (runs on transforms, pauses when idle/hidden) */}
        <div className="pointer-events-none absolute inset-0 overflow-hidden rounded-full">
          <motion.div
            animate={{
              opacity: borderGlow.opacityIdle,
            }}
            className={cn(
              "absolute inset-x-0 bottom-0",
              isLoading ? "h-full" : "h-1/2"
            )}
            transition={{ duration: 0.25, ease: "easeOut" }}
          >
            {/* Main flowing gradient blob */}
            <motion.div
              animate={
                shouldAnimateGlow
                  ? { x: borderGlow.xPrimary }
                  : { x: "25%" }
              }
              className={cn(
                "absolute -bottom-4",
                isLoading ? "inset-y-0 h-full w-64 blur-2xl" : "h-16 w-48 blur-xl"
              )}
              style={{
                background: borderGlow.primaryBg,
                transform: "translateZ(0)",
              }}
              transition={{
                duration: borderGlow.durationMain,
                repeat: shouldAnimateGlow ? Number.POSITIVE_INFINITY : 0,
                ease: "easeInOut",
              }}
            />
            {/* Secondary accent */}
            <motion.div
              animate={
                shouldAnimateGlow
                  ? { x: borderGlow.xSecondary }
                  : { x: "50%" }
              }
              className={cn(
                "absolute -bottom-3",
                isLoading ? "inset-y-0 h-full w-48 blur-xl" : "h-12 w-32 blur-lg"
              )}
              style={{
                background: borderGlow.secondaryBg,
                transform: "translateZ(0)",
              }}
              transition={{
                duration: borderGlow.durationSec,
                repeat: shouldAnimateGlow ? Number.POSITIVE_INFINITY : 0,
                ease: "easeInOut",
              }}
            />
            {/* Tertiary accent */}
            <motion.div
              animate={
                shouldAnimateGlow
                  ? { x: borderGlow.xTertiary }
                  : { x: "40%" }
              }
              className={cn(
                "absolute -bottom-2",
                isLoading ? "inset-y-0 h-full w-36 blur-xl" : "h-10 w-24 blur-lg"
              )}
              style={{
                background: borderGlow.tertiaryBg,
                transform: "translateZ(0)",
              }}
              transition={{
                duration: borderGlow.durationTer,
                repeat: shouldAnimateGlow ? Number.POSITIVE_INFINITY : 0,
                ease: "easeInOut",
              }}
            />
          </motion.div>
        </div>

        {/* Universal glassmorphic input container */}
        <div
          className={cn(
            "relative flex items-center gap-2 sm:gap-3 rounded-full px-3 sm:px-4 py-2 sm:py-2.5",
            "backdrop-blur-xl backdrop-saturate-150",
            "border border-slate-200/50 dark:border-white/15",
            "bg-white/80 dark:bg-slate-900/80",
            "shadow-[0_8px_32px_rgba(0,0,0,0.06),inset_0_1px_1px_rgba(255,255,255,0.7)] dark:shadow-[0_8px_32px_rgba(0,0,0,0.4),inset_0_1px_1px_rgba(255,255,255,0.12)]",
            "transition-[background,border-color,box-shadow] duration-200",
            (isFocused || isListening) &&
              "border-cyan-500/60 dark:border-cyan-400/60 shadow-[0_0_24px_rgba(34,211,238,0.28),inset_0_1px_1px_rgba(255,255,255,0.25)]"
          )}
        >
          {leftSlot}

          <AiBlobWarpAvatar
            isLoading={isLoading}
            isListening={isListening}
            isTypingBurst={isTypingBurst}
          />

          {/* Screen reader live region */}
          <div aria-atomic="true" aria-live="polite" className="sr-only">
            {isListening
              ? listeningText || "Listening to your voice..."
              : isLoading
              ? loadingText || "Searching..."
              : ""}
          </div>

          {/* Input field + animated overlays */}
          <div className="relative flex-1 min-w-0">
            {isListening ? (
              <div className="flex items-center justify-between py-0.5 min-h-[36px]">
                <div className="flex-1 min-w-0 pr-3">
                  {listeningText ? (
                    <p className="min-w-0 break-words text-sm font-medium leading-relaxed text-foreground">
                      {listeningText}
                    </p>
                  ) : (
                    <div className="flex items-center space-x-2.5">
                      <span
                        className={cn(
                          "text-xs sm:text-sm font-medium tracking-wide truncate text-cyan-600 dark:text-cyan-300",
                          !reduceMotion && "animate-pulse"
                        )}
                      >
                        Listening to your voice...
                      </span>
                    </div>
                  )}
                </div>
                <span
                  className={cn(
                    "amigo-listening-dot h-2.5 w-2.5 flex-shrink-0 rounded-full bg-cyan-400 shadow-[0_0_14px_#22d3ee]",
                    !reduceMotion && "animate-pulse"
                  )}
                />
              </div>
            ) : (
              <>
                <input
                  {...props}
                  aria-busy={isLoading || undefined}
                  aria-label={accessibleLabel}
                  autoComplete="off"
                  className={cn(
                    "w-full bg-transparent text-sm sm:text-base text-foreground outline-none",
                    "caret-foreground",
                    "placeholder:text-muted-foreground",
                    (isDeleting || isLoading) &&
                      "text-transparent caret-transparent"
                  )}
                  disabled={disabled}
                  enterKeyHint="search"
                  id={id}
                  name={name}
                  onBlur={(e) => {
                    setIsFocused(false)
                    props.onBlur?.(e)
                  }}
                  onChange={handleChange}
                  onCompositionEnd={() => {
                    isComposingRef.current = false
                  }}
                  onCompositionStart={() => {
                    isComposingRef.current = true
                  }}
                  onFocus={(e) => {
                    setIsFocused(true)
                    props.onFocus?.(e)
                  }}
                  onKeyDown={handleKeyDown}
                  placeholder=""
                  readOnly={readOnly || isLoading}
                  ref={setInputRef}
                  role="searchbox"
                  type="search"
                  value={currentValue}
                />
                {isLoading && (
                  <AnimatedPlaceholder
                    animateKey={loadingAnimKey}
                    charDurationSec={loadingCharDurationSec}
                    reduceMotion={reduceMotion}
                    staggerSec={loadingStaggerSec}
                    text={loadingText}
                  />
                )}
                {!(hasValue || isDeleting || isLoading) && (
                  <AnimatedPlaceholder
                    animateKey={placeholderAnimKey}
                    reduceMotion={reduceMotion}
                    text={placeholder}
                  />
                )}
                {/* Deletion animation: matched text-sm sm:text-base size & code points */}
                <AnimatePresence>
                  {isDeleting && deletionGhostText !== null && (
                    <DeletedTextBlurReveal
                      animatedIndexes={deletionIndexes}
                      clipDurationMs={deleteClipDurationMs}
                      key={deletionKey}
                      reduceMotion={reduceMotion}
                      showSweep={false}
                      text={deletionGhostText}
                      textClassName="text-foreground"
                    />
                  )}
                </AnimatePresence>
              </>
            )}
          </div>

          <SearchTrailingAction
            hasValue={hasValue && !isListening}
            isLoading={isLoading && !isListening}
            layoutId={trailingLayoutId}
            onCancel={onCancel}
            onClear={handleClear}
            reduceMotion={reduceMotion}
          />

          {!isLoading && rightSlot}
        </div>
      </div>
    </div>
  )
})

interface DeletedTextBlurRevealProps {
  text: string
  animatedIndexes?: number[]
  reduceMotion?: boolean
  className?: string
  textClassName?: string
  clipDurationMs?: number
  blurPx?: number
  saturatePercent?: number
  showSweep?: boolean
}

/**
 * Absolute overlay: clones string as per-glyph spans with matching input font sizing.
 */
export function DeletedTextBlurReveal({
  text,
  animatedIndexes,
  reduceMotion = false,
  className,
  textClassName,
  clipDurationMs = 320,
}: DeletedTextBlurRevealProps) {
  const sweepDurationMs = clipDurationMs + clipDurationMs / 2

  // Uses Array.from for Unicode-safe surrogate pairs and emoji handling
  const glyphs = useMemo(() => {
    const counts = new Map<string, number>()
    let order = 0

    return Array.from(text, (char) => {
      const count = counts.get(char) ?? 0
      counts.set(char, count + 1)
      const glyph = {
        char,
        key: `${char}-${count}`,
        order,
      }
      order += 1
      return glyph
    })
  }, [text])

  const animatedIndexSet = useMemo(() => {
    if (!animatedIndexes || animatedIndexes.length === 0) return null
    return new Set(animatedIndexes)
  }, [animatedIndexes])

  const animatedCount =
    animatedIndexes && animatedIndexes.length > 0
      ? animatedIndexes.length
      : glyphs.length

  const { glyphDurationSec, glyphDelayStepSec } = getDeletionGlyphTiming({
    sweepDurationMs,
    animatedCount,
    staggerGlyphCount: glyphs.length,
  })

  return (
    <motion.div
      aria-hidden
      className={cn("pointer-events-none absolute inset-0 isolate", className)}
      exit={{
        opacity: 0,
        transition: {
          duration: reduceMotion ? 0 : 0.12,
          delay: reduceMotion ? 0 : 0.18,
          ease: "easeOut",
        },
      }}
      initial={{ opacity: 1 }}
      style={{ borderRadius: 4, overflow: "hidden" }}
    >
      <div className="absolute inset-y-0 left-0 flex w-fit max-w-full overflow-hidden">
        <div className="relative h-full w-fit max-w-full overflow-hidden">
          <span
            className={cn(
              "relative z-10 flex items-center select-none text-sm sm:text-base text-current",
              textClassName
            )}
            style={{
              height: "100%",
              whiteSpace: "pre",
            }}
          >
            {glyphs.map((glyph) => (
              <motion.span
                animate={
                  animatedIndexSet === null || animatedIndexSet.has(glyph.order)
                    ? {
                        opacity: 0.04,
                        filter: "blur(8px)",
                        y: -1,
                      }
                    : {
                        opacity: 1,
                        filter: "blur(0px)",
                        y: 0,
                      }
                }
                className="inline-block"
                initial={{
                  opacity: 1,
                  filter: "blur(0px)",
                  y: 0,
                }}
                key={glyph.key}
                transition={{
                  duration:
                    reduceMotion ||
                    (animatedIndexSet && !animatedIndexSet.has(glyph.order))
                      ? 0
                      : glyphDurationSec,
                  ease: "easeOut",
                  delay: reduceMotion
                    ? 0
                    : (glyphs.length - 1 - glyph.order) * glyphDelayStepSec,
                }}
              >
                {glyph.char === " " ? "\u00A0" : glyph.char}
              </motion.span>
            ))}
          </span>
        </div>
      </div>
    </motion.div>
  )
}

class SafeWarpBoundary extends Component<
  { children: ReactNode; fallback?: ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false }
  static getDerivedStateFromError() {
    return { hasError: true }
  }
  componentDidCatch(error: unknown) {
    console.warn("Warp shader failed to render, switching to fallback:", error)
  }
  render() {
    if (this.state.hasError) {
      return this.props.fallback ?? null
    }
    return this.props.children
  }
}

const DEFAULT_WARP: Partial<WarpProps> = {
  colors: ["#00d4ff", "#38bdf8", "#6366f1", "#14b8a6", "#a855f7"],
  distortion: 0.35,
  proportion: 0.52,
  scale: 1.1,
  shape: "checks",
  shapeScale: 0.6,
  softness: 1.2,
  speed: 0.7,
  swirl: 0.88,
  swirlIterations: 8,
}

export type AiBlobWarpAvatarProps = Omit<ComponentProps<"div">, "children"> & {
  pulseDurationSec?: number
  warpProps?: Partial<WarpProps>
  isLoading?: boolean
  isListening?: boolean
  isTypingBurst?: boolean
}

/**
 * Memoized Avatar: eliminates continuous CSS marble compositing when WebGL is healthy,
 * pauses WebGL rendering when tab is hidden or off-screen.
 */
export const AiBlobWarpAvatar = React.memo(
  forwardRef<HTMLDivElement, AiBlobWarpAvatarProps>(function AiBlobWarpAvatarImpl(
    {
      className,
      pulseDurationSec = 4.6,
      warpProps,
      isLoading = false,
      isListening = false,
      isTypingBurst = false,
      ...props
    },
    forwardedRef
  ) {
    const rootRef = useRef<HTMLDivElement | null>(null)
    const reduceMotion = useReducedMotion() ?? false
    const inView = useInView(rootRef, { amount: 0.05 })

    const [isPageVisible, setIsPageVisible] = useState(
      typeof document !== "undefined" ? document.visibilityState === "visible" : true
    )
    useEffect(() => {
      const handleVisibility = () => {
        setIsPageVisible(document.visibilityState === "visible")
      }
      document.addEventListener("visibilitychange", handleVisibility)
      return () => document.removeEventListener("visibilitychange", handleVisibility)
    }, [])

    const live = Boolean(inView && isPageVisible && !reduceMotion)

    const setRefs = useCallback(
      (node: HTMLDivElement | null) => {
        rootRef.current = node
        if (typeof forwardedRef === "function") {
          forwardedRef(node)
        } else if (forwardedRef) {
          forwardedRef.current = node
        }
      },
      [forwardedRef]
    )

    const warpSpeed = useMemo(() => {
      if (!live) return 0
      if (isLoading || isListening) return 3.2
      if (isTypingBurst) return 2.4
      return 1.1
    }, [live, isLoading, isListening, isTypingBurst])

    // CSS marble rendered strictly as a fallback when WebGL fails
    const cssMarbleFallback = (
      <div
        className="pointer-events-none absolute inset-0 size-full rounded-full overflow-hidden"
        style={{ background: "#00d4ff" }}
      >
        <div
          className="absolute -inset-1 rounded-full opacity-90"
          style={{
            background:
              "conic-gradient(from 180deg at 50% 50%, #00d4ff 0deg, #38bdf8 70deg, #6366f1 150deg, #a855f7 240deg, #00d4ff 360deg)",
          }}
        />
      </div>
    )

    return (
      <div
        className={cn(
          "relative isolate size-8 shrink-0 overflow-hidden rounded-full shadow-[0_0_12px_rgba(0,212,255,0.25)] ring-1 ring-white/25 dark:ring-white/15 select-none",
          className
        )}
        ref={setRefs}
        {...props}
      >
        {/* WebGL Warp Shader layer with safe error boundary & fallback */}
        <motion.div
          animate={
            live
              ? {
                  scale: [1, 1.04, 0.98, 1],
                  rotate: [0, 2.5, -2, 0],
                }
              : { scale: 1, rotate: 0 }
          }
          aria-hidden
          className="pointer-events-none absolute inset-0 size-full origin-center rounded-full overflow-hidden [&>div]:size-full [&_canvas]:!block [&_canvas]:!size-full [&_canvas]:!relative [&_canvas]:!z-10 [&_canvas]:rounded-full"
          transition={{
            duration: isLoading ? 2.2 : pulseDurationSec,
            ease: "easeInOut",
            repeat: live ? Number.POSITIVE_INFINITY : 0,
          }}
        >
          <SafeWarpBoundary fallback={cssMarbleFallback}>
            <Warp
              {...DEFAULT_WARP}
              {...warpProps}
              className="size-full"
              speed={warpSpeed}
              style={{ width: "100%", height: "100%" }}
            />
          </SafeWarpBoundary>
        </motion.div>

        {/* Specular highlight rim */}
        <div className="pointer-events-none absolute inset-0 size-full rounded-full ring-1 ring-inset ring-white/35 dark:ring-white/20 shadow-[inset_0_1px_2px_rgba(255,255,255,0.45)]" />
      </div>
    )
  })
)
