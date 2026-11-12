// Pictures of a window (its own pixels, even under other windows) or of part of the screen, and saving them.
use crate::logic::{fit_rect, Rect, Win};
use crate::win::{frame_px, hwnd, primary, px_to_pt, rect_to_px, window_rect_px};
use image::codecs::jpeg::JpegEncoder;
use image::imageops::FilterType;
use image::RgbaImage;
use std::ffi::c_void;
use windows::Win32::Foundation::{HWND, RECT};
use windows::Win32::Graphics::Gdi::{
    BitBlt, CreateCompatibleDC, CreateDIBSection, DeleteDC, DeleteObject, GetDC, ReleaseDC, SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB,
    CAPTUREBLT, DIB_RGB_COLORS, HDC, SRCCOPY,
};
use windows::Win32::Storage::Xps::{PrintWindow, PRINT_WINDOW_FLAGS};

/// RGBA pixels, top row first.
pub struct Shot {
    pub w: usize,
    pub h: usize,
    pub rgba: Vec<u8>,
}

/// Draws into a fresh top-down 32-bit bitmap and hands back its pixels (BGRA turned into RGBA, opaque).
fn with_bitmap(w: i32, h: i32, draw: impl FnOnce(HDC) -> bool) -> Option<Shot> {
    if w <= 0 || h <= 0 {
        return None;
    }
    unsafe {
        let screen = GetDC(None);
        let mem = CreateCompatibleDC(Some(screen));
        let bmi = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: w,
                biHeight: -h,
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                ..Default::default()
            },
            ..Default::default()
        };
        let mut bits: *mut c_void = std::ptr::null_mut();
        let Ok(bmp) = CreateDIBSection(Some(mem), &bmi, DIB_RGB_COLORS, &mut bits, None, 0) else {
            let _ = DeleteDC(mem);
            ReleaseDC(None, screen);
            return None;
        };
        let old = SelectObject(mem, bmp.into());
        let ok = draw(mem);
        let n = (w * h * 4) as usize;
        let mut rgba = std::slice::from_raw_parts(bits as *const u8, n).to_vec();
        SelectObject(mem, old);
        let _ = DeleteObject(bmp.into());
        let _ = DeleteDC(mem);
        ReleaseDC(None, screen);
        if !ok {
            return None;
        }
        for p in rgba.chunks_exact_mut(4) {
            p.swap(0, 2);
            p[3] = 255;
        }
        Some(Shot { w: w as usize, h: h as usize, rgba })
    }
}

/// What's on screen in a physical-pixel rect. Roamies' overlays are left out (they're excluded from capture).
pub fn grab_screen(r: RECT) -> Option<Shot> {
    let (w, h) = (r.right - r.left, r.bottom - r.top);
    with_bitmap(w, h, |mem| unsafe {
        let screen = GetDC(None);
        let ok = BitBlt(mem, 0, 0, w, h, Some(screen), r.left, r.top, SRCCOPY | CAPTUREBLT).is_ok();
        ReleaseDC(None, screen);
        ok
    })
}

fn crop(s: &Shot, x: usize, y: usize, w: usize, h: usize) -> Shot {
    let (w, h) = (w.min(s.w.saturating_sub(x)), h.min(s.h.saturating_sub(y)));
    let mut rgba = Vec::with_capacity(w * h * 4);
    for row in y..y + h {
        rgba.extend_from_slice(&s.rgba[(row * s.w + x) * 4..(row * s.w + x + w) * 4]);
    }
    Shot { w, h, rgba }
}

/// A window's own pixels, as it draws them (even when it's covered), cut to its visible frame. Falls back to what's on
/// screen there when the window draws nothing for PrintWindow (some GPU apps).
pub fn grab_window(h: HWND) -> Option<(Shot, RECT)> {
    let (wr, fr) = (window_rect_px(h), frame_px(h));
    let full = with_bitmap(wr.right - wr.left, wr.bottom - wr.top, |mem| unsafe { PrintWindow(h, mem, PRINT_WINDOW_FLAGS(2)).as_bool() });
    if let Some(full) = full {
        let s = crop(&full, (fr.left - wr.left).max(0) as usize, (fr.top - wr.top).max(0) as usize, (fr.right - fr.left) as usize, (fr.bottom - fr.top) as usize);
        let blank = s.rgba.chunks_exact(4).all(|p| p[0] == 0 && p[1] == 0 && p[2] == 0);
        if s.w > 0 && s.h > 0 && !blank {
            return Some((s, fr));
        }
    }
    grab_screen(fr).map(|s| (s, fr))
}

/// A window's pixels and the area they cover in screen points, or the main display's when `win` is None.
pub fn pixels_of(win: Option<&Win>) -> Option<(Shot, Rect)> {
    match win {
        Some(w) => grab_window(hwnd(w.id)).map(|(s, r)| (s, px_to_pt(r))),
        None => {
            let m = primary();
            grab_screen(m.px).map(|s| (s, m.pt()))
        }
    }
}

/// Saves the picture shrunk to at most `max` px on its long side (never enlarged): JPEG, or PNG for a .png file.
/// Returns its final size.
pub fn save(s: &Shot, file: &str, max: Option<u32>, quality: u8) -> Result<[u32; 2], String> {
    let img = RgbaImage::from_raw(s.w as u32, s.h as u32, s.rgba.clone()).ok_or("bad picture")?;
    let long = s.w.max(s.h) as u32;
    let img = match max {
        Some(m) if long > m && m > 0 => {
            let k = m as f64 / long as f64;
            let (nw, nh) = (((s.w as f64 * k).round() as u32).max(1), ((s.h as f64 * k).round() as u32).max(1));
            image::imageops::resize(&img, nw, nh, FilterType::Triangle)
        }
        _ => img,
    };
    let size = [img.width(), img.height()];
    if file.to_lowercase().ends_with(".png") {
        img.save_with_format(file, image::ImageFormat::Png).map_err(|e| e.to_string())?;
    } else {
        let rgb = image::DynamicImage::ImageRgba8(img).to_rgb8();
        let f = std::fs::File::create(file).map_err(|e| e.to_string())?;
        JpegEncoder::new_with_quality(std::io::BufWriter::new(f), quality).encode_image(&rgb).map_err(|e| e.to_string())?;
    }
    Ok(size)
}

/// Boxes guessed from a screenshot (screen points) fitted to what's really drawn there; None where nothing clear is.
/// With a window: its own pixels, as its screenshot showed them; else the screen around each guess.
pub fn refine(boxes: &[Vec<f64>], win: Option<&Win>) -> Vec<Option<Rect>> {
    let shown = win.and_then(|w| pixels_of(Some(w)));
    boxes
        .iter()
        .map(|b| {
            if b.len() != 4 || b[2] <= 0.0 || b[3] <= 0.0 {
                return None;
            }
            let g = Rect::new(b[0], b[1], b[2], b[3]);
            if let Some((s, area)) = &shown {
                return fit_rect(&s.rgba, s.w, s.h, *area, g);
            }
            let pad = 12f64.max(0.6 * g.w.max(g.h));
            let px = rect_to_px(g.inset(-pad, -pad));
            let region = px_to_pt(px);
            let s = grab_screen(px)?;
            fit_rect(&s.rgba, s.w, s.h, region, g)
        })
        .collect()
}
