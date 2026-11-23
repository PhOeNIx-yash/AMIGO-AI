import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const orbCorePath = path.resolve(__dirname, "../node_modules/@yogesharc/thinking-orbs/dist/orb-core.js");

if (fs.existsSync(orbCorePath)) {
  let content = fs.readFileSync(orbCorePath, "utf8");

  // 1. Remove prefers-reduced-motion check so animation is never disabled by OS settings
  content = content.replace(
    /const still = matchMedia\("\([^"]+\)"\)\.matches;/,
    "const still = false;"
  );

  // 2. Make frame() draw unconditionally on every tick without waiting on or depending on IntersectionObserver
  // Replace the IntersectionObserver and frame implementation
  const targetRegex = /const still = false;[\s\S]*?return \{\s*pause,[\s\S]*?play,[\s\S]*?destroy\(\)[\s\S]*?\};/m;
  
  const replacement = `const still = false;
    let raf = 0, dead = false;
    function frame(now) {
        draw(tick(look, now, speed));
        if (!dead)
            raf = requestAnimationFrame(frame);
    }
    const pause = () => {
        cancelAnimationFrame(raf);
        raf = 0;
    };
    const play = () => {
        if (!raf && !dead)
            raf = requestAnimationFrame(frame);
    };
    play();
    return {
        pause,
        play,
        destroy() {
            dead = true;
            pause();
            svg.replaceChildren();
        },
    };`;

  if (targetRegex.test(content)) {
    content = content.replace(targetRegex, replacement);
    fs.writeFileSync(orbCorePath, content, "utf8");
    console.log("[patch-orbs] Successfully patched orb-core.js for continuous animation.");
  } else {
    // If exact regex doesn't match, check if already patched or patch directly
    if (content.includes("draw(tick(look, now, speed));\n        if (!dead)")) {
      console.log("[patch-orbs] orb-core.js already patched.");
    } else {
      console.warn("[patch-orbs] Could not match target pattern, applying manual replacement.");
      // Fallback safe replacements
      content = content.replace(/let visible = false;/g, "let visible = true;");
      content = content.replace(/visible && draw\(/g, "draw(");
      content = content.replace(/if \(visible\)\s*draw\(/g, "draw(");
      content = content.replace(/const still = matchMedia\([^)]+\)\.matches;/g, "const still = false;");
      fs.writeFileSync(orbCorePath, content, "utf8");
      console.log("[patch-orbs] Applied fallback patches to orb-core.js.");
    }
  }
} else {
  console.warn("[patch-orbs] orb-core.js not found at " + orbCorePath);
}
