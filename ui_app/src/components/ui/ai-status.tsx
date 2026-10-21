'use client'

import {
    AnimatePresence,
    motion,
    type Transition,
    type Variants,
} from 'motion/react'
import React, { useEffect, useState } from 'react'
import useMeasure from 'react-use-measure'

import { cn } from '@/lib/utils'

export type AiStatusState = 'thinking' | 'researching' | 'browsing' | 'analyzing' | 'writing' | 'done' | 'error'

export type AiStatusStateConfig = {
    label?: string | ((elapsed: string | undefined) => string)
    icon?: React.ReactNode
    active?: boolean
}

const DEFAULT_STATES: Record<AiStatusState, AiStatusStateConfig> = {
    thinking: { label: 'Thinking', active: true },
    researching: { label: 'Researching', active: true },
    browsing: { label: 'Browsing', active: true },
    analyzing: { label: 'Analyzing', active: true },
    writing: { label: 'Writing', active: true },
    done: { label: (elapsed) => (elapsed ? `Worked for ${elapsed}` : 'Done'), active: false },
    error: { label: (elapsed) => (elapsed ? `Failed after ${elapsed}` : 'Failed'), active: false },
}

const PEN_PATH = 'M3.78181 16.3092L3 21L7.69086 20.2182C8.50544 20.0825 9.25725 19.6956 9.84119 19.1116L20.4198 8.53288C21.1934 7.75922 21.1934 6.5049 20.4197 5.73126L18.2687 3.58024C17.495 2.80658 16.2406 2.80659 15.4669 3.58027L4.88841 14.159C4.30447 14.7429 3.91757 15.4947 3.78181 16.3092Z'
const SPARKLE_PATH = 'M11 5Q12 12 19 13Q12 14 11 21Q10 14 3 13Q10 12 11 5Z'
const WAVE_PATH = 'M3 19C4.11 16.3 5.06 16.3 6.17 19C7.27 21.7 8.22 21.7 9.33 19C10.44 16.3 11.39 16.3 12.5 19'
const WAVE_X = [3, 3.05, 3.18, 3.42, 3.78, 4.42, 5.26, 5.83, 6.29, 6.84, 7.76, 8.67, 9.22, 9.68, 10.25, 11.09, 11.72, 12.09, 12.33, 12.46, 12.5]
const WAVE_Y = [19, 18.88, 18.58, 18.1, 17.51, 17, 17.36, 18.25, 19.29, 20.33, 21.02, 20.32, 19.27, 18.24, 17.35, 17, 17.52, 18.11, 18.6, 18.9, 19]
const WAVE_PROGRESS = [0, 0.007, 0.028, 0.061, 0.104, 0.156, 0.216, 0.282, 0.352, 0.425, 0.5, 0.575, 0.648, 0.718, 0.784, 0.844, 0.896, 0.939, 0.972, 0.993, 1]
const DRAW_END = 0.7
const HOLD_END = 0.85
const SCRIBBLE: Transition = {
    duration: 2.4,
    ease: 'linear',
    repeat: Infinity,
    times: [...WAVE_PROGRESS.map((_, i) => (i / (WAVE_PROGRESS.length - 1)) * DRAW_END), HOLD_END, 1],
}
const TWINKLE_PATH = 'M18 2Q18.4 4.6 21 5Q18.4 5.4 18 8Q17.6 5.4 15 5Q17.6 4.6 18 2Z'

const SWAP: Transition = { type: 'spring', duration: 0.45, bounce: 0.12 }
const ICON: Transition = { type: 'spring', duration: 0.5, bounce: 0.25 }

const swapVariants: Variants = {
    enter: { y: 6, opacity: 0 },
    center: { y: 0, opacity: 1 },
    exit: { y: -6, opacity: 0 },
}

const iconVariants: Variants = {
    enter: { scale: 0.7, opacity: 0, rotate: -20 },
    center: { scale: 1, opacity: 1, rotate: 0 },
    exit: { scale: 0.7, opacity: 0, rotate: 20 },
}

const STROKE = {
    fill: 'none',
    stroke: 'currentColor',
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
} as const

const isBuiltIn = (state: string): state is AiStatusState => Object.hasOwn(DEFAULT_STATES, state)

const titleCase = (state: string) => state.charAt(0).toUpperCase() + state.slice(1)

const formatElapsed = (seconds: number) =>
    seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`

export type AiStatusProps = Omit<React.ComponentProps<'div'>, 'children'> & {
    state?: AiStatusState | (string & {})
    states?: Record<string, AiStatusStateConfig>
    label?: string
    icon?: React.ReactNode
    detail?: string
    favicon?: string | React.ReactNode
    timer?: boolean
    elapsed?: number
    shimmer?: boolean
}

function AiStatus({
    state = 'thinking',
    states,
    label,
    icon,
    detail,
    favicon,
    timer = true,
    elapsed,
    shimmer = true,
    className,
    ...props
}: AiStatusProps) {
    const config: AiStatusStateConfig = { ...(isBuiltIn(state) ? DEFAULT_STATES[state] : undefined), ...states?.[state] }
    const active = config.active ?? true
    const [counted, setCounted] = useState(0)
    const [wasActive, setWasActive] = useState(active)
    const [hasRun, setHasRun] = useState(active)
    const counting = timer && elapsed === undefined

    if (active !== wasActive) {
        setWasActive(active)
        if (active) {
            setCounted(0)
            setHasRun(true)
        }
    }

    useEffect(() => {
        if (!active || !counting) {
            return
        }
        const start = Date.now()
        const id = window.setInterval(() => setCounted(Math.floor((Date.now() - start) / 1000)), 1000)
        return () => window.clearInterval(id)
    }, [active, counting])

    const [measureRef, bounds] = useMeasure()
    const seconds = elapsed ?? (hasRun ? counted : undefined)
    const time = timer && seconds !== undefined ? formatElapsed(Math.max(0, Math.floor(seconds))) : undefined
    const text = label ?? (typeof config.label === 'function' ? config.label(time) : (config.label ?? titleCase(state)))
    const shimmering = shimmer ?? active

    return (
        <div
            data-slot="ai-status"
            data-state={state}
            data-active={active || undefined}
            role="status"
            className={cn('flex h-7 w-fit items-center gap-2 font-medium text-[13px] text-slate-800 dark:text-slate-200', className)}
            {...props}
        >
            <span data-slot="ai-status-icon" className="relative flex size-5 shrink-0 items-center justify-center">
                <AnimatePresence initial={false} mode="popLayout">
                    <motion.span
                        key={state}
                        aria-hidden
                        variants={iconVariants}
                        initial="enter"
                        animate="center"
                        exit="exit"
                        transition={ICON}
                        className="flex items-center justify-center"
                    >
                        {icon ?? config.icon ?? <AiStatusIcon state={state} />}
                    </motion.span>
                </AnimatePresence>
            </span>

            <motion.div
                className="relative overflow-x-clip"
                initial={false}
                animate={{ width: bounds.width || 'auto' }}
                transition={SWAP}
                style={{ willChange: 'width' }}
            >
                <div ref={measureRef} className="flex w-max items-center gap-2 whitespace-nowrap">
                    <span className="relative grid">
                        <AnimatePresence initial={false} mode="popLayout">
                            <motion.span
                                key={text}
                                variants={swapVariants}
                                initial="enter"
                                animate="center"
                                exit="exit"
                                transition={SWAP}
                                data-slot="ai-status-label"
                                className="font-semibold text-slate-700 dark:text-slate-200"
                            >
                                {shimmering ? <Shimmer>{text}</Shimmer> : text}
                            </motion.span>
                        </AnimatePresence>
                    </span>
                    <AnimatePresence initial={false} mode="popLayout">
                        {detail && (
                            <motion.span
                                key={detail}
                                data-slot="ai-status-detail"
                                variants={swapVariants}
                                initial="enter"
                                animate="center"
                                exit="exit"
                                transition={SWAP}
                                className="flex min-w-0 items-center gap-1.5 font-normal text-slate-500 dark:text-slate-400"
                            >
                                <span className="opacity-40">•</span>
                                {typeof favicon === 'string' ? (
                                    <img
                                        src={favicon}
                                        alt=""
                                        draggable={false}
                                        onError={(event) => {
                                            event.currentTarget.style.display = 'none'
                                        }}
                                        className="size-3.5 shrink-0 rounded-[3px] object-contain"
                                    />
                                ) : (
                                    favicon && <span className="flex size-3.5 shrink-0 items-center justify-center">{favicon}</span>
                                )}
                                <span className="max-w-64 truncate">{detail}</span>
                            </motion.span>
                        )}
                    </AnimatePresence>
                    {time && active && (
                        <span data-slot="ai-status-timer" aria-hidden className="font-mono text-xs tabular-nums text-slate-400 dark:text-slate-500">
                            {time}
                        </span>
                    )}
                </div>
            </motion.div>
        </div>
    )
}

export type AiStatusIconProps = {
    state: AiStatusState | (string & {})
}

function AiStatusIcon({ state }: AiStatusIconProps) {
    switch (state) {
        case 'thinking':
            return (
                <svg viewBox="0 0 24 24" width={18} height={18} className="text-amber-400 overflow-visible">
                    <motion.path
                        d={SPARKLE_PATH}
                        fill="currentColor"
                        style={{ transformOrigin: '11px 13px' }}
                        animate={{ rotate: [0, 90, 180, 270, 360], scale: [1, 0.85, 1] }}
                        transition={{ duration: 3.5, repeat: Infinity, ease: 'linear' }}
                    />
                    <motion.path
                        d={TWINKLE_PATH}
                        fill="currentColor"
                        style={{ transformOrigin: '18px 5px' }}
                        animate={{ scale: [0.3, 1, 0.3], opacity: [0.2, 1, 0.2] }}
                        transition={{ duration: 1.4, repeat: Infinity, ease: 'easeInOut' }}
                    />
                </svg>
            )
        case 'researching':
        case 'browsing':
            return (
                <svg viewBox="0 0 24 24" width={18} height={18} className="text-cyan-400 overflow-visible">
                    <motion.g
                        animate={{
                            x: [0, 2, 0, -2, 0],
                            y: [-1.5, 0, 1.5, 0, -1.5],
                            rotate: [0, 4, 0, -4, 0],
                        }}
                        transition={{ duration: 2.2, repeat: Infinity, ease: 'easeInOut' }}
                        style={{ transformOrigin: '12px 12px' }}
                    >
                        <circle {...STROKE} cx={10.5} cy={10.5} r={5.5} strokeWidth={2.25} />
                        <path {...STROKE} d="M14.75 14.75L19 19" strokeWidth={2.5} />
                    </motion.g>
                </svg>
            )
        case 'analyzing':
            return (
                <svg viewBox="0 0 24 24" width={18} height={18} className="text-indigo-400 overflow-visible">
                    {[6, 12, 18].map((x, i) => (
                        <motion.line
                            key={x}
                            {...STROKE}
                            x1={x}
                            x2={x}
                            y1={10}
                            y2={19}
                            strokeWidth={2.5}
                            animate={{ y1: [15, 6, 15] }}
                            transition={{ duration: 0.8, delay: i * 0.18, repeat: Infinity, ease: 'easeInOut' }}
                        />
                    ))}
                </svg>
            )
        case 'writing':
            return (
                <svg viewBox="0 0 24 24" width={18} height={18} className="text-pink-400 overflow-visible">
                    <g transform="translate(-1.2 -2)">
                        <motion.path
                            {...STROKE}
                            d={WAVE_PATH}
                            strokeWidth={2.25}
                            style={{ pathSpacing: 2 }}
                            animate={{ pathLength: [...WAVE_PROGRESS, 1, 1], opacity: [...WAVE_PROGRESS.map(() => 1), 1, 0] }}
                            transition={SCRIBBLE}
                        />
                        <motion.g
                            animate={{
                                x: [...WAVE_X, WAVE_X.at(-1)!, WAVE_X[0]],
                                y: [...WAVE_Y, WAVE_Y.at(-1)!, WAVE_Y[0]],
                            }}
                            transition={SCRIBBLE}
                        >
                            <path d={PEN_PATH} fill="currentColor" transform="scale(0.66) translate(-3 -21)" />
                        </motion.g>
                    </g>
                </svg>
            )
        case 'done':
            return (
                <svg viewBox="0 0 24 24" width={18} height={18} className="text-emerald-400">
                    <motion.path
                        {...STROKE}
                        d="M5 12.5L10 17.5L19 7"
                        strokeWidth={2.5}
                        initial={{ pathLength: 0 }}
                        animate={{ pathLength: 1 }}
                        transition={{ type: 'spring', duration: 0.6, bounce: 0, delay: 0.1 }}
                    />
                </svg>
            )
        case 'error':
            return (
                <svg viewBox="0 0 24 24" width={18} height={18} className="text-rose-400">
                    <motion.path
                        {...STROKE}
                        d="M7 7L17 17M17 7L7 17"
                        strokeWidth={2.5}
                        animate={{ x: [0, -2, 2, -1, 1, 0] }}
                        transition={{ duration: 0.45, delay: 0.1 }}
                    />
                </svg>
            )
        default:
            return (
                <motion.span
                    className="size-2.5 rounded-full bg-cyan-400"
                    animate={{ scale: [1, 0.7, 1], opacity: [1, 0.5, 1] }}
                    transition={{ duration: 1.2, repeat: Infinity, ease: 'easeInOut' }}
                />
            )
    }
}

function Shimmer({ children }: { children: React.ReactNode }) {
    return (
        <motion.span
            className="inline-block"
            style={{
                backgroundImage: 'linear-gradient(90deg, currentColor 20%, #38bdf8 50%, currentColor 80%)',
                backgroundSize: '200% 100%',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
                willChange: 'background-position',
            }}
            animate={{ backgroundPosition: ['200% 0', '-200% 0'] }}
            transition={{ duration: 2.2, ease: 'linear', repeat: Infinity }}
        >
            {children}
        </motion.span>
    )
}

export { AiStatus, AiStatusIcon }
export default AiStatus
