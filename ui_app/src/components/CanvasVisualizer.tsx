import React, { useEffect, useRef } from "react";
import { VisualizerMode, AssistantState, ColorTheme } from "../types";
import { COLOR_THEMES } from "../data/presets";
import { audioBus } from "../utils/audioBus";

interface CanvasVisualizerProps {
  mode: VisualizerMode;
  state: AssistantState;
  colorTheme: ColorTheme;
  isDark: boolean;
  compact?: boolean;
  isPaused?: boolean;
}

function createOffscreenBuffer(width: number, height: number): HTMLCanvasElement | OffscreenCanvas {
  if (typeof OffscreenCanvas !== "undefined") {
    return new OffscreenCanvas(width, height);
  }
  const c = document.createElement("canvas");
  c.width = width;
  c.height = height;
  return c;
}

/**
 * Clean Two-Mode High Performance Canvas Visualizer
 * Seamlessly morphs between:
 *  1. Fluid Waveform (Listening state)
 *  2. 3D Fibonacci Particle Orb (Processing & Working states)
 */
export const CanvasVisualizer: React.FC<CanvasVisualizerProps> = React.memo(({
  mode,
  state,
  colorTheme,
  isDark,
  compact = false,
  isPaused = false,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const audioLevelSmooth = useRef<number>(0);
  const isPausedRef = useRef<boolean>(isPaused);
  isPausedRef.current = isPaused;

  // Continuous two-mode smooth transition weights (0.0 to 1.0)
  const ribbonWeightRef = useRef<number>(state === "listening" || mode === "ribbon" ? 1.0 : 0.0);
  const orbWeightRef = useRef<number>(state === "processing" || state === "working" || mode === "orb" ? 1.0 : 0.0);

  // Keep state/mode/theme in mutable refs so RAF never tears down
  const stateRef = useRef({ mode, state, colorTheme, isDark });
  stateRef.current = { mode, state, colorTheme, isDark };

  // Precalculated Fibonacci sphere dot mesh for Orb mode (130 precision dots)
  const sphereDotsRef = useRef<{ x: number; y: number; z: number; baseSize: number }[]>([]);

  // Pre-calculated Lookup Tables (LUT) for Waveform
  const steps = 48;
  const envLUTRef = useRef<Float32Array>(new Float32Array(steps + 1));
  const uLUTRef = useRef<Float32Array>(new Float32Array(steps + 1));

  // Offscreen Canvas Buffers for hardware-accelerated texture blitting
  const ambientGlowCanvasRef = useRef<HTMLCanvasElement | OffscreenCanvas | null>(null);
  const centerNodeCanvasRef = useRef<HTMLCanvasElement | OffscreenCanvas | null>(null);

  // Cache tracking keys
  const cachedThemeKeyRef = useRef<string>("");
  const cachedIsDarkRef = useRef<boolean>(isDark);

  // Initialize particles & lookup tables once
  useEffect(() => {
    // 1. Precalculate Envelope & U LUTs for Waveform
    for (let i = 0; i <= steps; i++) {
      const u = i / steps;
      uLUTRef.current[i] = u;
      const envelope = Math.sin(u * Math.PI);
      envLUTRef.current[i] = Math.pow(envelope, 1.4);
    }

    // 2. 130 sphere dots for 3D Fibonacci Particle Orb
    const count = 130;
    const phi = Math.PI * (3 - Math.sqrt(5));
    const dots: { x: number; y: number; z: number; baseSize: number }[] = [];

    for (let i = 0; i < count; i++) {
      const y = 1 - (i / (count - 1)) * 2;
      const radius = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = phi * i;

      dots.push({
        x: Math.cos(theta) * radius,
        y,
        z: Math.sin(theta) * radius,
        baseSize: (i % 3 === 0 ? 1.8 : 1.2) + (i % 5 === 0 ? 0.6 : 0),
      });
    }
    sphereDotsRef.current = dots;
  }, []);

  // Function to bake offscreen canvas textures
  const bakeOffscreenTextures = (themeKey: string, dark: boolean) => {
    const theme = COLOR_THEMES[themeKey] || COLOR_THEMES.violet;
    const { primary, secondary, accent } = theme;

    // 1. Ambient Wave Glow Sprite (192x192 offscreen buffer)
    const glowSize = 192;
    const glowCanvas = createOffscreenBuffer(glowSize, glowSize);
    const glowCtx = glowCanvas.getContext("2d") as CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null;
    if (glowCtx) {
      const hSize = glowSize / 2;
      const grad = glowCtx.createRadialGradient(hSize, hSize, 2, hSize, hSize, hSize);
      grad.addColorStop(0, dark ? `${accent}40` : `${primary}28`);
      grad.addColorStop(0.4, dark ? `${primary}20` : `${secondary}14`);
      grad.addColorStop(0.8, dark ? `${secondary}08` : `${accent}06`);
      grad.addColorStop(1, "rgba(0,0,0,0)");
      glowCtx.fillStyle = grad;
      glowCtx.beginPath();
      glowCtx.arc(hSize, hSize, hSize, 0, Math.PI * 2);
      glowCtx.fill();
    }
    ambientGlowCanvasRef.current = glowCanvas;

    // 2. Center Focal Pulse Node Sprite (48x48 offscreen buffer)
    const nodeSize = 48;
    const nodeCanvas = createOffscreenBuffer(nodeSize, nodeSize);
    const nodeCtx = nodeCanvas.getContext("2d") as CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null;
    if (nodeCtx) {
      const hSize = nodeSize / 2;
      const grad = nodeCtx.createRadialGradient(hSize, hSize, 0, hSize, hSize, hSize);
      grad.addColorStop(0, dark ? "rgba(255,255,255,0.95)" : `${accent}ff`);
      grad.addColorStop(0.35, dark ? `${accent}aa` : `${primary}88`);
      grad.addColorStop(0.7, dark ? `${primary}44` : `${secondary}33`);
      grad.addColorStop(1, "rgba(0,0,0,0)");
      nodeCtx.fillStyle = grad;
      nodeCtx.beginPath();
      nodeCtx.arc(hSize, hSize, hSize, 0, Math.PI * 2);
      nodeCtx.fill();
    }
    centerNodeCanvasRef.current = nodeCanvas;

    cachedThemeKeyRef.current = themeKey;
    cachedIsDarkRef.current = dark;
  };

  // Main Rendering Loop
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    let width = 0;
    let height = 0;

    const resize = () => {
      if (!canvas || !canvas.parentElement) return;
      const rect = canvas.parentElement.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    resize();
    const resizeObserver = new ResizeObserver(resize);
    if (canvas.parentElement) {
      resizeObserver.observe(canvas.parentElement);
    }

    let time = 0;
    let lastTimestamp = performance.now();

    // Reusable buckets for 3D sphere render
    const frontGlowBucket: { px: number; py: number; radius: number }[] = [];
    const frontCyanBucket: { px: number; py: number; radius: number }[] = [];
    const midVioletBucket: { px: number; py: number; radius: number }[] = [];
    const backDarkBucket: { px: number; py: number; radius: number }[] = [];

    const render = (now: number) => {
      if (isPausedRef.current) {
        lastTimestamp = now;
        animFrameRef.current = requestAnimationFrame(render);
        return;
      }

      const deltaSec = Math.min(0.033, Math.max(0.001, (now - lastTimestamp) / 1000));
      lastTimestamp = now;
      time += deltaSec;

      const currentAudio = audioBus.getLevel();
      const audioSmoothing = currentAudio > audioLevelSmooth.current ? 0.35 : 0.12;
      audioLevelSmooth.current += (currentAudio - audioLevelSmooth.current) * audioSmoothing;

      const {
        mode: currentMode,
        state: currentState,
        colorTheme: currentThemeKey,
        isDark: currentIsDark,
      } = stateRef.current;

      // Two-Mode Target Weights:
      // Listening -> Waveform (Ribbon)
      // Processing (Thinking) & Working -> 3D Particle Orb
      let targetRibbon = 0.0;
      let targetOrb = 0.0;

      if (currentState === "listening") {
        targetRibbon = 1.0;
        targetOrb = 0.0;
      } else if (currentState === "processing" || currentState === "working") {
        targetRibbon = 0.0;
        targetOrb = 1.0;
      } else {
        // Idle or other states: follow user preference
        if (currentMode === "ribbon") {
          targetRibbon = currentState === "idle" ? 0.0 : 0.85;
          targetOrb = 0.0;
        } else {
          targetRibbon = 0.0;
          targetOrb = 0.85;
        }
      }

      const transitionRate = 1 - Math.exp(-deltaSec * 14.0);
      ribbonWeightRef.current += (targetRibbon - ribbonWeightRef.current) * transitionRate;
      orbWeightRef.current += (targetOrb - orbWeightRef.current) * transitionRate;

      const rw = ribbonWeightRef.current;
      const ow = orbWeightRef.current;

      const effectiveAudio = Math.max(
        audioLevelSmooth.current,
        currentState === "listening" ? 0.22 : 0.0
      );

      if (
        cachedThemeKeyRef.current !== currentThemeKey ||
        cachedIsDarkRef.current !== currentIsDark ||
        !ambientGlowCanvasRef.current
      ) {
        bakeOffscreenTextures(currentThemeKey, currentIsDark);
      }

      ctx.clearRect(0, 0, width, height);

      if (rw <= 0.002 && ow <= 0.002) {
        animFrameRef.current = requestAnimationFrame(render);
        return;
      }

      const theme = COLOR_THEMES[currentThemeKey] || COLOR_THEMES.violet;
      const primaryHex = theme.primary;
      const secondaryHex = theme.secondary;
      const accentHex = theme.accent;

      // 1. Waveform Mode (Listening)
      if (rw > 0.002) {
        drawSimpleSleekWaveFast(
          ctx,
          width,
          height,
          time,
          effectiveAudio,
          primaryHex,
          secondaryHex,
          accentHex,
          currentIsDark,
          rw,
          ow,
          envLUTRef.current,
          uLUTRef.current,
          steps,
          ambientGlowCanvasRef.current,
          centerNodeCanvasRef.current
        );
      }

      // 2. 3D Particle Orb Mode (Processing / Working)
      if (ow > 0.002) {
        drawGlebParticleOrbFast(
          ctx,
          width,
          height,
          time,
          effectiveAudio,
          primaryHex,
          secondaryHex,
          accentHex,
          currentIsDark,
          sphereDotsRef.current,
          frontGlowBucket,
          frontCyanBucket,
          midVioletBucket,
          backDarkBucket,
          ow,
          rw,
          currentState === "processing" || currentState === "working"
        );
      }

      animFrameRef.current = requestAnimationFrame(render);
    };

    animFrameRef.current = requestAnimationFrame(render);

    return () => {
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
      resizeObserver.disconnect();
    };
  }, []);

  return (
    <div
      className={`absolute inset-0 pointer-events-none overflow-hidden transition-opacity duration-500 ${
        compact ? "opacity-75" : "opacity-100"
      }`}
      style={{ willChange: "transform" }}
    >
      <canvas
        ref={canvasRef}
        className="w-full h-full block"
        style={{ willChange: "contents" }}
      />
    </div>
  );
});

// =========================================================================================
// HIGH-PERFORMANCE 60FPS WAVEFORM (ZERO ALLOCATION, OFFSCREEN TEXTURES)
// =========================================================================================
function drawSimpleSleekWaveFast(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  time: number,
  audio: number,
  primaryColor: string,
  secondaryColor: string,
  accentColor: string,
  isDark: boolean,
  ribbonWeight: number,
  orbWeight: number,
  envLUT: Float32Array,
  uLUT: Float32Array,
  steps: number,
  ambientGlowCanvas: HTMLCanvasElement | OffscreenCanvas | null,
  centerNodeCanvas: HTMLCanvasElement | OffscreenCanvas | null
) {
  const blend = Math.max(0, Math.min(1.0, ribbonWeight));
  if (blend <= 0.002) return;

  const centerX = width / 2;
  const centerY = height * 0.46;

  const widthCollapse = 0.25 + 0.75 * Math.min(1.0, blend / (blend + orbWeight * 0.8 + 0.001));
  const waveWidth = Math.min(width * 0.78, 560) * widthCollapse;
  const startX = centerX - waveWidth / 2;

  // 1. Offscreen Ambient Glow GPU Blit
  if (ambientGlowCanvas) {
    const glowDiameter = Math.min(waveWidth * 0.7, 240) * (0.85 + audio * 0.35) * blend;
    ctx.globalCompositeOperation = isDark ? "screen" : "source-over";
    ctx.globalAlpha = blend * (1.0 - orbWeight * 0.5);
    ctx.drawImage(
      ambientGlowCanvas as CanvasImageSource,
      centerX - glowDiameter / 2,
      centerY - glowDiameter / 2,
      glowDiameter,
      glowDiameter
    );
  }

  // 2. Render Harmonic Waves
  const baseAmplitude = (16 + audio * 42) * blend * (0.3 + 0.7 * widthCollapse);
  ctx.globalCompositeOperation = isDark ? "screen" : "source-over";

  const waveConfigs = [
    { speed: 2.2, freq: 2.5, phase: 0, ampMult: 1.0, color: isDark ? "#ffffff" : primaryColor, lineWidth: 1.8, alpha: 0.9 * blend },
    { speed: -1.8, freq: 3.2, phase: 1.2, ampMult: 0.7, color: "#38bdf8", lineWidth: 1.4, alpha: 0.7 * blend },
    { speed: 2.8, freq: 4.1, phase: 2.4, ampMult: 0.5, color: secondaryColor, lineWidth: 1.2, alpha: 0.6 * blend },
  ];

  for (let c = 0; c < waveConfigs.length; c++) {
    const cfg = waveConfigs[c];
    ctx.beginPath();
    ctx.strokeStyle = cfg.color;
    ctx.lineWidth = cfg.lineWidth;
    ctx.globalAlpha = cfg.alpha;

    const waveTime = time * cfg.speed;
    const effAmp = baseAmplitude * cfg.ampMult;

    for (let i = 0; i <= steps; i++) {
      const u = uLUT[i];
      const x = startX + u * waveWidth;
      const shapedEnv = envLUT[i];
      const mainWave = Math.sin(u * Math.PI * cfg.freq - waveTime + cfg.phase);
      const y = centerY + mainWave * effAmp * shapedEnv;

      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
  }

  // 3. Center Focal Node
  if (centerNodeCanvas) {
    const centerAmp = Math.sin(time * 2.2) * 3 * blend;
    const nodeDiameter = (22 + audio * 28) * blend + (orbWeight * 18 * (1 - blend));
    ctx.globalAlpha = Math.max(blend, orbWeight * 0.6) * (isDark ? 0.9 : 0.75);
    ctx.drawImage(
      centerNodeCanvas as CanvasImageSource,
      centerX - nodeDiameter / 2,
      centerY + centerAmp - nodeDiameter / 2,
      nodeDiameter,
      nodeDiameter
    );
  }
}

// =========================================================================================
// 3D FIBONACCI PARTICLE ORB (OPTIMIZED 130 DOTS, FAST BATCHING)
// =========================================================================================
function drawGlebParticleOrbFast(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  time: number,
  audio: number,
  color1: string,
  color2: string,
  accentColor: string,
  isDark: boolean,
  sphereDots: { x: number; y: number; z: number; baseSize: number }[],
  frontGlowBucket: { px: number; py: number; radius: number }[],
  frontCyanBucket: { px: number; py: number; radius: number }[],
  midVioletBucket: { px: number; py: number; radius: number }[],
  backDarkBucket: { px: number; py: number; radius: number }[],
  orbWeight: number = 1.0,
  ribbonWeight: number = 0.0,
  isActiveOrb: boolean = false
) {
  const blend = Math.max(0, Math.min(1.0, orbWeight));
  if (blend <= 0.002) return;

  const centerX = width / 2;
  const centerY = height * 0.46;

  const morphRatio = Math.min(1.0, Math.max(0.0, blend / (blend + ribbonWeight * 0.6 + 0.0001)));
  const morphEase = Math.sin((morphRatio * Math.PI) / 2);

  const speedMultiplier = isActiveOrb ? 1.35 : 1.0;
  const pulseAmp = isActiveOrb ? Math.sin(time * 3.6) * 0.08 : 0.0;
  const baseRadius = (Math.min(width, height) * 0.22 + audio * 36) * (0.28 + 0.72 * morphEase) * (1.0 + pulseAmp);

  const rotY = time * (0.45 * speedMultiplier);
  const rotX = Math.sin(time * 0.35 * speedMultiplier) * 0.35 + 0.25;

  const cosY = Math.cos(rotY);
  const sinY = Math.sin(rotY);
  const cosX = Math.cos(rotX);
  const sinX = Math.sin(rotX);

  frontGlowBucket.length = 0;
  frontCyanBucket.length = 0;
  midVioletBucket.length = 0;
  backDarkBucket.length = 0;

  const fov = 3.2;
  const len = sphereDots.length;

  for (let i = 0; i < len; i++) {
    const dot = sphereDots[i];

    const waveX = dot.x * 2.2;
    const waveY = dot.y * 0.12 + Math.sin(dot.x * 3.8 + time * 3.0) * 0.16;
    const waveZ = dot.z * 0.22;

    const curX = waveX + (dot.x - waveX) * morphEase;
    const curY = waveY + (dot.y - waveY) * morphEase;
    const curZ = waveZ + (dot.z - waveZ) * morphEase;

    const x1 = curX * cosY - curZ * sinY;
    const z1 = curZ * cosY + curX * sinY;
    const y2 = curY * cosX - z1 * sinX;
    const z2 = z1 * cosX + curY * sinX;

    const scale = fov / (fov + z2);
    const px = centerX + x1 * baseRadius * scale;
    const py = centerY + y2 * baseRadius * scale;
    const normDepth = (z2 + 1) * 0.5;
    const radius = dot.baseSize * scale * (0.7 + normDepth * 0.8) * (0.5 + 0.5 * morphEase);

    if (normDepth > 0.8) {
      frontGlowBucket.push({ px, py, radius });
    } else if (normDepth > 0.55) {
      frontCyanBucket.push({ px, py, radius });
    } else if (normDepth > 0.3) {
      midVioletBucket.push({ px, py, radius });
    } else {
      backDarkBucket.push({ px, py, radius });
    }
  }

  // Batched renders
  ctx.globalCompositeOperation = isDark ? "screen" : "source-over";

  if (backDarkBucket.length > 0) {
    ctx.globalAlpha = 0.3 * blend;
    ctx.fillStyle = color1;
    ctx.beginPath();
    for (let i = 0; i < backDarkBucket.length; i++) {
      const p = backDarkBucket[i];
      ctx.moveTo(p.px + p.radius, p.py);
      ctx.arc(p.px, p.py, p.radius, 0, Math.PI * 2);
    }
    ctx.fill();
  }

  if (midVioletBucket.length > 0) {
    ctx.globalAlpha = 0.65 * blend;
    ctx.fillStyle = color2;
    ctx.beginPath();
    for (let i = 0; i < midVioletBucket.length; i++) {
      const p = midVioletBucket[i];
      ctx.moveTo(p.px + p.radius, p.py);
      ctx.arc(p.px, p.py, p.radius, 0, Math.PI * 2);
    }
    ctx.fill();
  }

  if (frontCyanBucket.length > 0) {
    ctx.globalAlpha = 0.9 * blend;
    ctx.fillStyle = "#38bdf8";
    ctx.beginPath();
    for (let i = 0; i < frontCyanBucket.length; i++) {
      const p = frontCyanBucket[i];
      ctx.moveTo(p.px + p.radius, p.py);
      ctx.arc(p.px, p.py, p.radius, 0, Math.PI * 2);
    }
    ctx.fill();
  }

  if (frontGlowBucket.length > 0) {
    ctx.globalAlpha = 1.0 * blend;
    ctx.fillStyle = "#ffffff";
    ctx.beginPath();
    for (let i = 0; i < frontGlowBucket.length; i++) {
      const p = frontGlowBucket[i];
      ctx.moveTo(p.px + p.radius, p.py);
      ctx.arc(p.px, p.py, p.radius, 0, Math.PI * 2);
    }
    ctx.fill();
  }
}
