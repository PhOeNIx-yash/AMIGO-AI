// Circling something while talking (the twin of voice/ink.swift). While the talk keys are held, the cursor's path is
// followed; a stroke counts only once it closes round something: it snaps shut and stays. One circle per talk. Lines
// and scribbles that never close fade and are never sent. The overlay draws the trail ({"type":"ink"} messages, in
// screen points: physical pixels ÷ that monitor's scale, the overlays' units). When the talk ends, the circled part
// of the screen is captured (without Roamies' overlays, which are left out of captures) with the strokes drawn onto
// the picture here, and sent with what was said as `area`, exactly like macOS.
use serde_json::{json, Value};

pub type Pt = (f64, f64);

const CLOSE_GAP: f64 = 22.0;
const MIN_RUN: f64 = 110.0;
const MIN_SIDE: f64 = 26.0;
const MIN_FILL: f64 = 0.3;
const START_AFTER: f64 = 6.0;
const PAUSE_ENDS: f64 = 0.35;
/// The pen's radius in points (ink.swift's InkStroke.base).
const PEN: f64 = 2.8;

/// Adds a cursor position to a stroke, smoothed a little (takes the jitter out without lagging behind the pointer).
/// False when it was too close to the last one to count.
pub fn add_point(pts: &mut Vec<Pt>, p: Pt) -> bool {
    let Some(&last) = pts.last() else {
        pts.push(p);
        return true;
    };
    if (p.0 - last.0).hypot(p.1 - last.1) < 2.0 {
        return false;
    }
    pts.push((last.0 + (p.0 - last.0) * 0.65, last.1 + (p.1 - last.1) * 0.65));
    true
}

/// Where the loop the path has just closed starts (an index into p), or None: its newest point comes back near an
/// earlier one, round a shape with room inside it. A line, a line drawn back over itself, a zigzag or a tiny
/// scribble never closes.
pub fn closing(p: &[Pt], gap: f64) -> Option<usize> {
    if p.len() < 8 {
        return None;
    }
    let end = *p.last()?;
    let mut run = 0.0;
    let mut i = p.len() - 1;
    while i > 0 {
        run += (p[i].0 - p[i - 1].0).hypot(p[i].1 - p[i - 1].1);
        i -= 1;
        if run >= MIN_RUN && (p[i].0 - end.0).hypot(p[i].1 - end.1) <= gap && encloses(&p[i..]) {
            return Some(i);
        }
    }
    None
}

/// Big enough both ways, and its outline holds a fair share of its box (a closed zigzag or a sliver doesn't).
pub fn encloses(q: &[Pt]) -> bool {
    if q.len() < 3 {
        return false;
    }
    let (x0, x1) = q.iter().fold((f64::MAX, f64::MIN), |a, p| (a.0.min(p.0), a.1.max(p.0)));
    let (y0, y1) = q.iter().fold((f64::MAX, f64::MIN), |a, p| (a.0.min(p.1), a.1.max(p.1)));
    let (w, h) = (x1 - x0, y1 - y0);
    if w < MIN_SIDE || h < MIN_SIDE {
        return false;
    }
    let a: f64 = (0..q.len()).map(|k| {
        let (u, v) = (q[k], q[(k + 1) % q.len()]);
        u.0 * v.1 - v.0 * u.1
    }).sum();
    a.abs() / 2.0 >= MIN_FILL * w * h
}

/// The part of the screen to send for these loops: round them with 48 points to spare, at least 320×220, and on
/// their screen (`screen`: x, y, w, h in points). Whole points.
pub fn capture_box(loops: &[Vec<Pt>], screen: (f64, f64, f64, f64)) -> Option<(f64, f64, f64, f64)> {
    let pts: Vec<&Pt> = loops.iter().flatten().collect();
    if pts.is_empty() {
        return None;
    }
    let (mut x0, mut x1) = pts.iter().fold((f64::MAX, f64::MIN), |a, p| (a.0.min(p.0), a.1.max(p.0)));
    let (mut y0, mut y1) = pts.iter().fold((f64::MAX, f64::MIN), |a, p| (a.0.min(p.1), a.1.max(p.1)));
    (x0, y0, x1, y1) = (x0 - 48.0, y0 - 48.0, x1 + 48.0, y1 + 48.0);
    if x1 - x0 < 320.0 {
        let d = (320.0 - (x1 - x0)) / 2.0;
        (x0, x1) = (x0 - d, x1 + d);
    }
    if y1 - y0 < 220.0 {
        let d = (220.0 - (y1 - y0)) / 2.0;
        (y0, y1) = (y0 - d, y1 + d);
    }
    let (sx, sy, sw, sh) = screen;
    let (x0, y0, x1, y1) = (x0.max(sx).floor(), y0.max(sy).floor(), x1.min(sx + sw).ceil(), y1.min(sy + sh).ceil());
    (x1 > x0 && y1 > y0).then_some((x0, y0, x1 - x0, y1 - y0))
}

/// At most 48 points per stroke, whole points: plenty to tell a circle from a line.
pub fn marks(loops: &[Vec<Pt>]) -> Vec<Vec<[i64; 2]>> {
    loops.iter().map(|s| {
        let step = (s.len() / 48).max(1);
        let mut picked: Vec<Pt> = s.iter().step_by(step).copied().collect();
        if let (Some(&l), Some(&p)) = (s.last(), picked.last()) {
            if l != p {
                picked.push(l);
            }
        }
        picked.iter().map(|p| [p.0.round() as i64, p.1.round() as i64]).collect()
    }).collect()
}

/// Draws strokes (pixel coordinates) onto an RGBA picture: the pen's teal with a dark edge, smooth at the rim.
pub fn draw_strokes(rgba: &mut [u8], w: usize, h: usize, strokes: &[Vec<Pt>], r: f64) {
    let mut blend = |x: i64, y: i64, c: [f64; 3], a: f64| {
        if x < 0 || y < 0 || x as usize >= w || y as usize >= h || a <= 0.0 {
            return;
        }
        let i = (y as usize * w + x as usize) * 4;
        for k in 0..3 {
            rgba[i + k] = (rgba[i + k] as f64 * (1.0 - a) + c[k] * a).round() as u8;
        }
    };
    // Coverage of each pixel by a stroke of radius `rad` (distance to the nearest segment), so joins stay smooth.
    let pass = |rad: f64, c: [f64; 3], alpha: f64, blend: &mut dyn FnMut(i64, i64, [f64; 3], f64)| {
        for s in strokes {
            if s.is_empty() {
                continue;
            }
            let (x0, x1) = s.iter().fold((f64::MAX, f64::MIN), |a, p| (a.0.min(p.0), a.1.max(p.0)));
            let (y0, y1) = s.iter().fold((f64::MAX, f64::MIN), |a, p| (a.0.min(p.1), a.1.max(p.1)));
            let pad = rad + 1.0;
            for y in (y0 - pad).floor() as i64..=(y1 + pad).ceil() as i64 {
                for x in (x0 - pad).floor() as i64..=(x1 + pad).ceil() as i64 {
                    let (px, py) = (x as f64 + 0.5, y as f64 + 0.5);
                    let mut d = f64::MAX;
                    for k in 0..s.len() {
                        let (a, b) = (s[k], s[(k + 1).min(s.len() - 1)]);
                        let (dx, dy) = (b.0 - a.0, b.1 - a.1);
                        let len2 = dx * dx + dy * dy;
                        let t = if len2 > 0.0 { (((px - a.0) * dx + (py - a.1) * dy) / len2).clamp(0.0, 1.0) } else { 0.0 };
                        d = d.min((px - a.0 - t * dx).hypot(py - a.1 - t * dy));
                    }
                    let cover = (rad + 0.5 - d).clamp(0.0, 1.0);
                    blend(x, y, c, cover * alpha);
                }
            }
        }
    };
    pass(r + 1.25 * r / PEN, [3.0, 39.0, 34.0], 0.6, &mut blend);
    pass(r, [0x78 as f64, 255.0, 0xe2 as f64], 1.0, &mut blend);
}

/// Shrinks an RGBA picture to at most `max` px on its long side (an average of each block), never enlarging.
pub fn downscale(rgba: &[u8], w: usize, h: usize, max: usize) -> (Vec<u8>, usize, usize) {
    if w.max(h) <= max || w == 0 || h == 0 {
        return (rgba.to_vec(), w, h);
    }
    let k = max as f64 / w.max(h) as f64;
    let (nw, nh) = (((w as f64 * k).round() as usize).max(1), ((h as f64 * k).round() as usize).max(1));
    let mut out = vec![0u8; nw * nh * 4];
    for ny in 0..nh {
        let (sy0, sy1) = (ny * h / nh, ((ny + 1) * h / nh).max(ny * h / nh + 1));
        for nx in 0..nw {
            let (sx0, sx1) = (nx * w / nw, ((nx + 1) * w / nw).max(nx * w / nw + 1));
            let mut acc = [0u32; 4];
            for y in sy0..sy1 {
                for x in sx0..sx1 {
                    let i = (y * w + x) * 4;
                    for c in 0..4 {
                        acc[c] += rgba[i + c] as u32;
                    }
                }
            }
            let n = ((sy1 - sy0) * (sx1 - sx0)) as u32;
            let o = (ny * nw + nx) * 4;
            for c in 0..4 {
                out[o + c] = (acc[c] / n) as u8;
            }
        }
    }
    (out, nw, nh)
}

/// The talk's pen: follows the cursor while armed, keeps the loops that snapped shut, and captures them at the end.
#[derive(Default)]
pub struct Ink {
    armed: bool,
    last: Option<Pt>,
    last_moved: f64,
    stroke: Option<(Vec<Pt>, usize)>,
    snapped: bool,
    loops: Vec<(Vec<Pt>, usize)>,
    area: Option<Value>,
}

impl Ink {
    /// The cursor draws only while the talk keys are down.
    pub fn set_armed(&mut self, on: bool, now: f64) {
        if on == self.armed {
            return;
        }
        self.armed = on;
        if on {
            self.last = imp::cursor().map(|c| c.0);
            self.last_moved = now;
            self.stroke = None;
            self.snapped = false;
        } else {
            self.end();
            self.last = None;
        }
    }

    /// Called every tick: while armed, the cursor's path becomes ink. A stroke starts once the cursor has clearly
    /// moved (not a nudge) and ends after a short pause or on another screen.
    pub fn follow(&mut self, now: f64) {
        if !self.armed || self.snapped {
            return;
        }
        let (Some(prev), Some((m, mon))) = (self.last, imp::cursor()) else { return };
        if self.stroke.is_some() && now - self.last_moved > PAUSE_ENDS {
            self.snapped = self.end();
            if self.snapped {
                return;
            }
        }
        let need = if self.stroke.is_none() { START_AFTER } else { 0.5 };
        if (m.0 - prev.0).hypot(m.1 - prev.1) < need {
            return;
        }
        self.last = Some(m);
        self.last_moved = now;
        if self.stroke.as_ref().is_none_or(|s| s.1 != mon) {
            if self.end() {
                self.snapped = true;
                return;
            }
            self.stroke = Some((vec![prev], mon));
            emit(json!({ "type": "ink", "ev": "start", "x": prev.0, "y": prev.1 }));
        }
        let Some((pts, _)) = self.stroke.as_mut() else { return };
        if add_point(pts, m) {
            let p = *pts.last().unwrap();
            emit(json!({ "type": "ink", "ev": "pt", "x": (p.0 * 10.0).round() / 10.0, "y": (p.1 * 10.0).round() / 10.0 }));
        }
        if let Some(i) = closing(pts, CLOSE_GAP) {
            self.snap(i);
            self.snapped = true;
        }
    }

    fn snap(&mut self, from: usize) {
        let Some((mut pts, mon)) = self.stroke.take() else { return };
        pts.drain(..from);
        pts.push(pts[0]);
        self.loops.push((pts, mon));
        emit(json!({ "type": "ink", "ev": "snap", "from": from }));
    }

    /// The stroke ended: if it nearly closed it snaps shut now (true); otherwise it was a line and fades.
    fn end(&mut self) -> bool {
        let Some((pts, _)) = &self.stroke else { return false };
        if let Some(i) = closing(pts, CLOSE_GAP * 1.8) {
            self.snap(i);
            return true;
        }
        self.stroke = None;
        emit(json!({ "type": "ink", "ev": "drop" }));
        false
    }

    /// The talk ended: capture the circled part of the screen with the strokes on it, then fade them.
    pub fn capture(&mut self, now: f64) {
        self.set_armed(false, now);
        self.discard();
        let Some(&(_, mon)) = self.loops.last() else { return };
        let loops: Vec<Vec<Pt>> = self.loops.drain(..).filter(|l| l.1 == mon).map(|l| l.0).collect();
        self.area = imp::capture(&loops, mon);
        crate::log(&format!("drawing: {} circled area(s){}", loops.len(), if self.area.is_some() { " captured" } else { ", capture failed" }));
        emit(json!({ "type": "ink", "ev": "fade", "after": 0.5 }));
    }

    /// What was captured (None: nothing was circled, or the capture failed).
    pub fn take(&mut self) -> Option<Value> {
        self.area.take()
    }

    fn discard(&mut self) {
        if let Some(f) = self.area.take().and_then(|a| a["file"].as_str().map(String::from)) {
            let _ = std::fs::remove_file(f);
        }
    }

    /// The talk was dropped (a shortcut, or nothing said): wipe the strokes and anything captured.
    pub fn clear(&mut self, now: f64) {
        self.set_armed(false, now);
        self.discard();
        if !self.loops.is_empty() {
            self.loops.clear();
            emit(json!({ "type": "ink", "ev": "fade", "after": 0 }));
        }
    }
}

fn emit(v: Value) {
    crate::emit(v);
}

#[cfg(windows)]
mod imp {
    use super::{capture_box, downscale, draw_strokes, marks, Pt, PEN};
    use serde_json::{json, Value};
    use std::ffi::c_void;
    use windows_sys::Win32::Foundation::{POINT, RECT};
    use windows_sys::Win32::Graphics::Gdi::*;
    use windows_sys::Win32::UI::HiDpi::{GetDpiForMonitor, MDT_EFFECTIVE_DPI};
    use windows_sys::Win32::UI::WindowsAndMessaging::GetCursorPos;

    /// The monitor's place (physical pixels) and scale.
    fn monitor(h: HMONITOR) -> Option<(RECT, f64)> {
        // SAFETY: MONITORINFO with its size set; GetDpiForMonitor writes two u32s.
        unsafe {
            let mut mi: MONITORINFO = std::mem::zeroed();
            mi.cbSize = std::mem::size_of::<MONITORINFO>() as u32;
            if GetMonitorInfoW(h, &mut mi) == 0 {
                return None;
            }
            let (mut dx, mut dy) = (96u32, 96u32);
            GetDpiForMonitor(h, MDT_EFFECTIVE_DPI, &mut dx, &mut dy);
            Some((mi.rcMonitor, dx.max(48) as f64 / 96.0))
        }
    }

    /// The cursor in screen points, and which monitor it's on.
    pub fn cursor() -> Option<(Pt, usize)> {
        let mut p = POINT { x: 0, y: 0 };
        // SAFETY: GetCursorPos writes one POINT.
        if unsafe { GetCursorPos(&mut p) } == 0 {
            return None;
        }
        // SAFETY: plain call with a point.
        let h = unsafe { MonitorFromPoint(p, MONITOR_DEFAULTTONEAREST) };
        let (_, s) = monitor(h)?;
        Some(((p.x as f64 / s, p.y as f64 / s), h as usize))
    }

    /// What's on screen in a physical-pixel rect, as RGBA.
    fn grab(x: i32, y: i32, w: i32, h: i32) -> Option<Vec<u8>> {
        // SAFETY: a memory DC and a top-down 32-bit DIB of w×h, read once BitBlt has filled it, then freed.
        unsafe {
            let screen = GetDC(std::ptr::null_mut());
            let mem = CreateCompatibleDC(screen);
            let mut bmi: BITMAPINFO = std::mem::zeroed();
            bmi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
            bmi.bmiHeader.biWidth = w;
            bmi.bmiHeader.biHeight = -h;
            bmi.bmiHeader.biPlanes = 1;
            bmi.bmiHeader.biBitCount = 32;
            bmi.bmiHeader.biCompression = BI_RGB;
            let mut bits: *mut c_void = std::ptr::null_mut();
            let bmp = CreateDIBSection(mem, &bmi, DIB_RGB_COLORS, &mut bits, std::ptr::null_mut(), 0);
            let mut out = None;
            if !bmp.is_null() && !bits.is_null() {
                let old = SelectObject(mem, bmp);
                if BitBlt(mem, 0, 0, w, h, screen, x, y, SRCCOPY | CAPTUREBLT) != 0 {
                    let mut px = std::slice::from_raw_parts(bits as *const u8, (w * h * 4) as usize).to_vec();
                    for p in px.chunks_exact_mut(4) {
                        p.swap(0, 2);
                        p[3] = 255;
                    }
                    out = Some(px);
                }
                SelectObject(mem, old);
                DeleteObject(bmp);
            }
            DeleteDC(mem);
            ReleaseDC(std::ptr::null_mut(), screen);
            out
        }
    }

    fn save_png(file: &str, rgba: &[u8], w: usize, h: usize) -> Result<(), String> {
        let f = std::fs::File::create(file).map_err(|e| e.to_string())?;
        let mut enc = png::Encoder::new(std::io::BufWriter::new(f), w as u32, h as u32);
        enc.set_color(png::ColorType::Rgba);
        enc.set_depth(png::BitDepth::Eight);
        let mut wr = enc.write_header().map_err(|e| e.to_string())?;
        wr.write_image_data(rgba).map_err(|e| e.to_string())
    }

    pub fn capture(loops: &[Vec<Pt>], mon: usize) -> Option<Value> {
        let (r, s) = monitor(mon as HMONITOR)?;
        let screen = (r.left as f64 / s, r.top as f64 / s, (r.right - r.left) as f64 / s, (r.bottom - r.top) as f64 / s);
        let (bx, by, bw, bh) = capture_box(loops, screen)?;
        let (px, py, pw, ph) = ((bx * s).round() as i32, (by * s).round() as i32, (bw * s).round() as i32, (bh * s).round() as i32);
        let mut rgba = grab(px, py, pw, ph)?;
        let strokes: Vec<Vec<Pt>> = loops.iter().map(|l| l.iter().map(|p| (p.0 * s - px as f64, p.1 * s - py as f64)).collect()).collect();
        draw_strokes(&mut rgba, pw as usize, ph as usize, &strokes, PEN * s);
        // 1568 px is the model's own cap (macOS shrinks it in the sidecar; there's no sips here).
        let (rgba, w, h) = downscale(&rgba, pw as usize, ph as usize, 1568);
        let ms = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_millis());
        let file = std::env::temp_dir().join(format!("sidekick-marked-{ms}.png")).to_string_lossy().into_owned();
        if let Err(e) = save_png(&file, &rgba, w, h) {
            crate::log(&format!("marked-area save failed: {e}"));
            return None;
        }
        Some(json!({ "file": file, "rect": [bx as i64, by as i64, bw as i64, bh as i64], "strokes": marks(loops) }))
    }
}

// Elsewhere there's no cursor to follow and no screen to capture.
#[cfg(not(windows))]
mod imp {
    use super::Pt;
    pub fn cursor() -> Option<(Pt, usize)> {
        None
    }
    pub fn capture(_: &[Vec<Pt>], _: usize) -> Option<serde_json::Value> {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn arc(cx: f64, cy: f64, r: f64, turns: f64) -> Vec<Pt> {
        (0..=80).map(|k| {
            let a = turns * 2.0 * std::f64::consts::PI * k as f64 / 80.0;
            (cx + r * a.cos(), cy + r * a.sin())
        }).collect()
    }

    // The same cases as voice/main.swift --test-snap.
    #[test]
    fn which_paths_count_as_a_circled_area() {
        let lead: Vec<Pt> = (0..20).map(|i| (100.0 + i as f64 * 4.0, 300.0)).collect();
        assert!(closing(&arc(300.0, 300.0, 60.0, 1.0), CLOSE_GAP).is_some(), "a full circle snaps");
        let run_up: Vec<Pt> = lead.iter().copied().chain(arc(200.0, 300.0, 60.0, 1.08)).collect();
        assert!(closing(&run_up, CLOSE_GAP).is_some_and(|i| i >= lead.len() - 2), "snaps from where the loop starts");
        assert!(closing(&arc(300.0, 300.0, 60.0, 0.75), CLOSE_GAP).is_none(), "three-quarters of a circle doesn't");
        assert!(closing(&arc(300.0, 300.0, 60.0, 0.75), CLOSE_GAP * 1.8).is_none(), "nor even at the end");
        assert!(closing(&arc(300.0, 300.0, 60.0, 0.93), CLOSE_GAP * 1.8).is_some(), "nearly closed closes at the end");
        assert!(closing(&(0..80).map(|i| (i as f64 * 5.0, 200.0)).collect::<Vec<_>>(), CLOSE_GAP).is_none(), "a straight line doesn't");
        let there: Vec<Pt> = (0..40).map(|i| (i as f64 * 5.0, 200.0)).collect();
        let back: Vec<Pt> = there.iter().copied().chain(there.iter().rev().map(|p| (p.0, p.1 + 3.0))).collect();
        assert!(closing(&back, CLOSE_GAP).is_none(), "a line drawn back over itself doesn't");
        let zig: Vec<Pt> = (0..60).map(|i| (i as f64 * 6.0, 200.0 + if i % 2 == 0 { 0.0 } else { 30.0 })).collect();
        assert!(closing(&zig, CLOSE_GAP).is_none(), "a zigzag doesn't");
        assert!(closing(&arc(300.0, 300.0, 8.0, 3.0), CLOSE_GAP).is_none(), "a tiny scribble doesn't");
        let mut b: Vec<Pt> = (0..=20).map(|i| (i as f64 * 6.0, 0.0)).collect();
        b.extend((0..=15).map(|i| (120.0, i as f64 * 6.0)));
        b.extend((0..=20).map(|i| (120.0 - i as f64 * 6.0, 90.0)));
        b.extend((0..=15).map(|i| (2.0, 90.0 - i as f64 * 6.0)));
        assert!(closing(&b, CLOSE_GAP).is_some(), "a rough box snaps");
    }

    #[test]
    fn the_capture_is_round_the_circle_and_on_its_screen() {
        let c = arc(300.0, 300.0, 60.0, 1.0);
        assert_eq!(capture_box(&[c.clone()], (0.0, 0.0, 1920.0, 1080.0)), Some((140.0, 190.0, 320.0, 220.0)));
        let edge = arc(30.0, 30.0, 20.0, 1.0);
        assert_eq!(capture_box(&[edge], (0.0, 0.0, 1920.0, 1080.0)).map(|b| (b.0, b.1)), Some((0.0, 0.0)), "cut to the screen");
        let long: Vec<Pt> = (0..200).map(|k| (k as f64, 0.0)).collect();
        let m = marks(&[long]);
        assert!(m[0].len() <= 52 && m[0][0] == [0, 0] && m[0].last() == Some(&[199, 0]), "a long stroke is thinned, its ends kept");
    }

    #[test]
    fn strokes_are_drawn_onto_the_picture() {
        let (w, h) = (40, 20);
        let mut px = vec![255u8; w * h * 4];
        draw_strokes(&mut px, w, h, &[vec![(5.0, 10.0), (35.0, 10.0)]], 2.8);
        let at = |x: usize, y: usize| &px[(y * w + x) * 4..(y * w + x) * 4 + 3];
        assert_eq!(at(20, 10), &[0x78, 0xff, 0xe2], "the pen's teal on the line");
        assert_eq!(at(20, 1), &[255, 255, 255], "untouched away from it");
        let (small, sw, sh) = downscale(&px, w, h, 10);
        assert_eq!((sw, sh, small.len()), (10, 5, 10 * 5 * 4));
    }

    #[test]
    fn smoothing_skips_tiny_moves() {
        let mut p = vec![];
        assert!(add_point(&mut p, (0.0, 0.0)));
        assert!(!add_point(&mut p, (1.0, 1.0)));
        assert!(add_point(&mut p, (10.0, 0.0)));
        assert_eq!(p[1], (6.5, 0.0));
    }
}
