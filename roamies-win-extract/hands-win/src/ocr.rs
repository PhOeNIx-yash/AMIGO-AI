// Text read off a window (or the main display) with Windows' own OCR, with the box of each match: words anywhere,
// inside paragraphs, images and canvases too, and single characters (case-sensitive), top to bottom.
use crate::capture::{pixels_of, Shot};
use crate::logic::{fit_rect, ocr_matches, Rect, Win};
use image::imageops::FilterType;
use image::RgbaImage;
use serde_json::{json, Value};
use windows::Graphics::Imaging::{BitmapPixelFormat, SoftwareBitmap};
use windows::Media::Ocr::OcrEngine;
use windows::Storage::Streams::DataWriter;

pub const NO_LANGUAGE: &str = "Windows has no OCR language installed (Settings → Time & language → Language: add one with Optical character recognition).";

fn bitmap(s: &Shot) -> windows::core::Result<SoftwareBitmap> {
    let mut bgra = s.rgba.clone();
    for p in bgra.chunks_exact_mut(4) {
        p.swap(0, 2);
    }
    let wr = DataWriter::new()?;
    wr.WriteBytes(&bgra)?;
    SoftwareBitmap::CreateCopyFromBuffer(&wr.DetachBuffer()?, BitmapPixelFormat::Bgra8, s.w as i32, s.h as i32)
}

pub fn ocr_find(win: Option<&Win>, needle: &str, all: bool) -> Result<Vec<Value>, String> {
    let q = needle.trim();
    let Some((shot, area)) = pixels_of(win) else { return Ok(vec![]) };
    if q.is_empty() || area.is_empty() {
        return Ok(vec![]);
    }
    let engine = OcrEngine::TryCreateFromUserProfileLanguages().map_err(|_| NO_LANGUAGE.to_string())?;
    // OCR takes pictures up to a size: shrink bigger ones, and map their boxes back.
    let max = OcrEngine::MaxImageDimension().unwrap_or(4096) as usize;
    let long = shot.w.max(shot.h);
    let small;
    let src = if long > max {
        let k = max as f64 / long as f64;
        let img = RgbaImage::from_raw(shot.w as u32, shot.h as u32, shot.rgba.clone()).ok_or("bad picture")?;
        let r = image::imageops::resize(&img, (shot.w as f64 * k) as u32, (shot.h as f64 * k) as u32, FilterType::Triangle);
        small = Shot { w: r.width() as usize, h: r.height() as usize, rgba: r.into_raw() };
        &small
    } else {
        &shot
    };
    let res = bitmap(src).and_then(|b| engine.RecognizeAsync(&b)?.join()).map_err(|e| format!("Reading the screen failed: {e}"))?;
    let (sx, sy) = (area.w / src.w as f64, area.h / src.h as f64);
    let single = q.chars().count() == 1;
    let mut found: Vec<(Rect, String)> = vec![];
    for line in res.Lines().map_err(|e| e.to_string())? {
        let text = line.Text().map(|t| t.to_string()).unwrap_or_default();
        let words: Vec<(String, Rect)> = line
            .Words()
            .map(|ws| {
                ws.into_iter()
                    .filter_map(|w| {
                        let b = w.BoundingRect().ok()?;
                        Some((w.Text().ok()?.to_string(), Rect::new(b.X as f64, b.Y as f64, b.Width as f64, b.Height as f64)))
                    })
                    .collect()
            })
            .unwrap_or_default();
        for r in ocr_matches(&words, q) {
            let mut rect = Rect::new(area.x + r.x * sx, area.y + r.y * sy, r.w * sx, r.h * sy);
            // The box for one character is cut from its word by position: fit it to the glyph's own pixels.
            if single {
                if let Some(tight) = fit_rect(&shot.rgba, shot.w, shot.h, area, rect) {
                    rect = tight;
                }
            }
            found.push((rect, text.clone()));
        }
    }
    found.sort_by(|a, b| if (a.0.y - b.0.y).abs() > 4.0 { a.0.y.total_cmp(&b.0.y) } else { a.0.x.total_cmp(&b.0.x) });
    if !all {
        found.truncate(1);
    }
    Ok(found.into_iter().map(|(r, s)| json!({ "text": s, "role": "text", "bounds": r.tenths() })).collect())
}
