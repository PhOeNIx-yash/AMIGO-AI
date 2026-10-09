import React, { useEffect, useRef } from 'react'

import { cn } from '@/lib/utils'


const VERT = `
attribute vec2 a_pos;
void main() {
  gl_Position = vec4(a_pos, 0.0, 1.0);
}
`

const FRAG = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif

uniform vec2 u_resolution;
uniform float u_time;
uniform vec3 u_color;

float hash(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(hash(i + vec2(0.0, 0.0)), hash(i + vec2(1.0, 0.0)), u.x),
    mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x),
    u.y
  );
}

float fbm(vec2 p) {
  float v = 0.0;
  float a = 0.6;
  for (int i = 0; i < 3; i++) {
    v += a * noise(p);
    p *= 2.0;
    a *= 0.5;
  }
  return v;
}

void main() {
  vec2 uv = gl_FragCoord.xy / u_resolution.xy;
  float t = u_time * 0.22;

  vec2 drift = vec2(
    sin(t) + 0.6 * sin(t * 1.7 + 1.3),
    cos(t * 0.8) + 0.6 * cos(t * 1.3 + 2.1)
  );

  vec2 p = vec2(uv.x * 1.8, uv.y * 1.0) + drift * 0.7;

  vec2 q = vec2(fbm(p + drift), fbm(p + vec2(3.2, 1.5) - drift));
  float f = fbm(p + 1.2 * q);

  float g = clamp(1.0 - uv.y, 0.0, 1.0);
  float anchor = smoothstep(0.0, 0.3, uv.y);
  float shade = clamp(g + (f - 0.5) * 0.8 * anchor, 0.0, 1.0);

  vec3 white = vec3(0.99, 1.0, 1.0);
  vec3 light = mix(white, u_color, 0.5);
  vec3 dark = u_color;

  vec3 col = white;
  col = mix(col, light, smoothstep(0.28, 0.52, shade));
  col = mix(col, dark, smoothstep(0.58, 0.88, shade));

  float edge = smoothstep(0.5, 0.49, distance(uv, vec2(0.5)));

  gl_FragColor = vec4(col * edge, edge);
}
`

function hexToRgb(hex: string): [number, number, number] {
  let h = hex.replace('#', '').trim()
  if (h.length === 3) {
    h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2]
  }
  const n = parseInt(h, 16)
  if (h.length !== 6 || Number.isNaN(n)) return [0.1, 0.45, 0.95]
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]
}

function compile(gl: WebGLRenderingContext, type: number, src: string) {
  const shader = gl.createShader(type)
  if (!shader) return null
  gl.shaderSource(shader, src)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    console.error(gl.getShaderInfoLog(shader))
    gl.deleteShader(shader)
    return null
  }
  return shader
}

export type FluidOrbProps = React.ComponentProps<'div'> & {
  size?: number;
  color?: string;
  isSpeaking?: boolean;
};

// ... Shader constants (VERT / FRAG) remain the same ...
const FluidOrb = ({
  size = 240,
  color = '#1A73F2',
  isSpeaking = false,
  className,
  style,
  ...props
}: FluidOrbProps) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const isVisible = useRef(false);

  // WebGL Renderer with IntersectionObserver optimization
  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;

    let gl: WebGLRenderingContext | null = null;
    let program: WebGLProgram | null = null;
    let raf = 0;
    let vert: WebGLShader | null = null;
    let frag: WebGLShader | null = null;
    let buffer: WebGLBuffer | null = null;
    let isInitialized = false;

    const initWebGL = () => {
      if (isInitialized) return;
      
      gl = canvas.getContext('webgl', { antialias: false, alpha: true, powerPreference: 'low-power' });
      if (!gl) return;

      program = gl.createProgram();
      vert = compile(gl, gl.VERTEX_SHADER, VERT);
      frag = compile(gl, gl.FRAGMENT_SHADER, FRAG);
      if (!program || !vert || !frag) return;

      gl.attachShader(program, vert);
      gl.attachShader(program, frag);
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        console.error(gl.getProgramInfoLog(program));
        return;
      }
      gl.useProgram(program);

      buffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(
        gl.ARRAY_BUFFER,
        new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
        gl.STATIC_DRAW,
      );
      const aPos = gl.getAttribLocation(program, 'a_pos');
      gl.enableVertexAttribArray(aPos);
      gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const px = Math.round(size * dpr);
      canvas.width = px;
      canvas.height = px;
      gl.viewport(0, 0, px, px);
      
      isInitialized = true;
    };

    const start = performance.now();
    const render = (now: number) => {
      if (!isVisible.current) {
        raf = requestAnimationFrame(render);
        return;
      }

      if (!isInitialized) initWebGL();

      if (gl && program) {
        const uResolution = gl.getUniformLocation(program, 'u_resolution');
        const uTime = gl.getUniformLocation(program, 'u_time');
        const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        
        gl.uniform2f(uResolution, canvas.width, canvas.height);
        gl.uniform3f(gl.getUniformLocation(program, 'u_color'), ...hexToRgb(color));
        gl.uniform1f(uTime, reduce ? 0 : (now - start) / 1000);
        
        gl.drawArrays(gl.TRIANGLES, 0, 6);
      }

      raf = requestAnimationFrame(render);
    };

    // Use Intersection Observer to only render when visible (bypasses context limit bottlenecks & saves CPU)
    const observer = new IntersectionObserver(([entry]) => {
      isVisible.current = entry.isIntersecting;
    }, { threshold: 0.1 });
    
    observer.observe(container);
    raf = requestAnimationFrame(render);

    return () => {
      observer.disconnect();
      cancelAnimationFrame(raf);
      if (gl && program) {
        gl.deleteProgram(program);
        if (vert) gl.deleteShader(vert);
        if (frag) gl.deleteShader(frag);
        if (buffer) gl.deleteBuffer(buffer);
        // Do NOT call WEBGL_lose_context here, it breaks React StrictMode's remount behavior
      }
    };
  }, [size, color]);

  return (
    <div
      ref={containerRef}
      data-slot="fluid-orb"
      className={cn('relative overflow-visible rounded-full flex-shrink-0 transition-all duration-300', className)}
      style={{
        width: size,
        height: size,
        boxShadow: isSpeaking ? `0 0 24px ${color}90` : `0 0 12px ${color}40`,
        transform: isSpeaking ? "scale(1.05) translateZ(0)" : "scale(1) translateZ(0)",
        ...style,
      }}
      {...props}
    >
      <canvas ref={canvasRef} className="h-full w-full rounded-full" />
    </div>
  );
};

export default FluidOrb;
