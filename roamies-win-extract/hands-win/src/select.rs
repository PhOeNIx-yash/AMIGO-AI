// `select`: drag to choose an area of the screen, like macOS's own selection. The screen freezes under a dimmed
// copy of itself, the dragged area shows bright, Esc (or a right click) cancels. The area is saved as a PNG.
use crate::capture::{grab_screen, save, Shot};
use crate::win::force_front;
use std::cell::RefCell;
use std::ffi::c_void;
use windows::core::w;
use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::*;
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, ReleaseCapture, SetCapture, SetFocus, VK_ESCAPE};
use windows::Win32::UI::WindowsAndMessaging::*;

struct Snap {
    full: Vec<u8>,
    bits: *mut u8,
    mem: HDC,
    w: i32,
    h: i32,
    start: Option<(i32, i32)>,
    prev: Option<RECT>,
    done: Option<Option<RECT>>,
}

thread_local! {
    static SNAP: RefCell<Option<Snap>> = const { RefCell::new(None) };
}

const ACCENT: [u8; 4] = [0xe2, 0xff, 0x78, 255];

fn norm(a: (i32, i32), b: (i32, i32)) -> RECT {
    RECT { left: a.0.min(b.0), top: a.1.min(b.1), right: a.0.max(b.0), bottom: a.1.max(b.1) }
}

impl Snap {
    /// Paints one area of the frame: dimmed outside the selection, bright inside it, with its edge drawn.
    fn paint(&mut self, area: RECT, sel: Option<RECT>) {
        let (l, t) = (area.left.max(0), area.top.max(0));
        let (r, b) = (area.right.min(self.w), area.bottom.min(self.h));
        let px = unsafe { std::slice::from_raw_parts_mut(self.bits, (self.w * self.h * 4) as usize) };
        for y in t..b {
            for x in l..r {
                let i = ((y * self.w + x) * 4) as usize;
                let c = match sel {
                    Some(s) if x >= s.left - 2 && x < s.right + 2 && y >= s.top - 2 && y < s.bottom + 2 => {
                        if x < s.left || x >= s.right || y < s.top || y >= s.bottom {
                            ACCENT
                        } else {
                            [self.full[i], self.full[i + 1], self.full[i + 2], 255]
                        }
                    }
                    _ => [self.full[i] / 2, self.full[i + 1] / 2, self.full[i + 2] / 2, 255],
                };
                px[i..i + 4].copy_from_slice(&c);
            }
        }
    }
    fn update(&mut self, hwnd: HWND, sel: Option<RECT>) {
        let grow = |r: RECT| RECT { left: r.left - 3, top: r.top - 3, right: r.right + 3, bottom: r.bottom + 3 };
        let mut dirty = vec![];
        if let Some(p) = self.prev {
            dirty.push(grow(p));
        }
        if let Some(s) = sel {
            dirty.push(grow(s));
        }
        for d in &dirty {
            self.paint(*d, sel);
        }
        if let Some(s) = sel {
            self.paint(grow(s), sel);
        }
        self.prev = sel;
        for d in dirty {
            let _ = unsafe { InvalidateRect(Some(hwnd), Some(&d), false) };
        }
    }
}

fn point(l: LPARAM) -> (i32, i32) {
    ((l.0 & 0xffff) as i16 as i32, ((l.0 >> 16) & 0xffff) as i16 as i32)
}

fn finish(hwnd: HWND, r: Option<RECT>) {
    SNAP.with(|s| {
        if let Some(s) = s.borrow_mut().as_mut() {
            s.done = Some(r);
        }
    });
    let _ = unsafe { DestroyWindow(hwnd) };
}

unsafe extern "system" fn proc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    match msg {
        WM_ERASEBKGND => return LRESULT(1),
        WM_PAINT => {
            let mut ps = PAINTSTRUCT::default();
            let hdc = unsafe { BeginPaint(hwnd, &mut ps) };
            SNAP.with(|s| {
                if let Some(s) = s.borrow().as_ref() {
                    let r = ps.rcPaint;
                    let _ = unsafe { BitBlt(hdc, r.left, r.top, r.right - r.left, r.bottom - r.top, Some(s.mem), r.left, r.top, SRCCOPY) };
                }
            });
            let _ = unsafe { EndPaint(hwnd, &ps) };
            return LRESULT(0);
        }
        WM_LBUTTONDOWN => {
            let p = point(lp);
            SNAP.with(|s| {
                if let Some(s) = s.borrow_mut().as_mut() {
                    s.start = Some(p);
                }
            });
            unsafe { SetCapture(hwnd) };
            return LRESULT(0);
        }
        WM_MOUSEMOVE => {
            let p = point(lp);
            SNAP.with(|s| {
                if let Some(s) = s.borrow_mut().as_mut() {
                    if let Some(a) = s.start {
                        s.update(hwnd, Some(norm(a, p)));
                    }
                }
            });
            return LRESULT(0);
        }
        WM_LBUTTONUP => {
            let p = point(lp);
            let _ = unsafe { ReleaseCapture() };
            let sel = SNAP.with(|s| s.borrow_mut().as_mut().and_then(|s| s.start.take().map(|a| norm(a, p))));
            match sel {
                Some(r) if r.right - r.left >= 4 && r.bottom - r.top >= 4 => finish(hwnd, Some(r)),
                // A click without a drag: let them try again.
                _ => SNAP.with(|s| {
                    if let Some(s) = s.borrow_mut().as_mut() {
                        s.update(hwnd, None);
                    }
                }),
            }
            return LRESULT(0);
        }
        WM_RBUTTONDOWN => {
            finish(hwnd, None);
            return LRESULT(0);
        }
        WM_KEYDOWN if wp.0 == VK_ESCAPE.0 as usize => {
            finish(hwnd, None);
            return LRESULT(0);
        }
        // Esc while something else still has the keyboard.
        WM_TIMER => {
            if unsafe { GetAsyncKeyState(VK_ESCAPE.0 as i32) } < 0 {
                finish(hwnd, None);
            }
            return LRESULT(0);
        }
        WM_DESTROY => {
            unsafe { PostQuitMessage(0) };
            return LRESULT(0);
        }
        _ => {}
    }
    unsafe { DefWindowProcW(hwnd, msg, wp, lp) }
}

/// Lets the user drag an area. Some(size) when an area was saved to `file`, None when they cancelled.
pub fn select(file: &str) -> Result<Option<[u32; 2]>, String> {
    let (vx, vy, vw, vh) = unsafe { (GetSystemMetrics(SM_XVIRTUALSCREEN), GetSystemMetrics(SM_YVIRTUALSCREEN), GetSystemMetrics(SM_CXVIRTUALSCREEN), GetSystemMetrics(SM_CYVIRTUALSCREEN)) };
    let shot = grab_screen(RECT { left: vx, top: vy, right: vx + vw, bottom: vy + vh }).ok_or("Couldn't capture the screen.")?;
    let mut full = shot.rgba;
    for p in full.chunks_exact_mut(4) {
        p.swap(0, 2);
    }
    unsafe {
        let screen = GetDC(None);
        let mem = CreateCompatibleDC(Some(screen));
        ReleaseDC(None, screen);
        let bmi = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER { biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32, biWidth: vw, biHeight: -vh, biPlanes: 1, biBitCount: 32, biCompression: BI_RGB.0, ..Default::default() },
            ..Default::default()
        };
        let mut bits: *mut c_void = std::ptr::null_mut();
        let bmp = CreateDIBSection(Some(mem), &bmi, DIB_RGB_COLORS, &mut bits, None, 0).map_err(|e| e.to_string())?;
        let old = SelectObject(mem, bmp.into());
        let mut snap = Snap { full, bits: bits as *mut u8, mem, w: vw, h: vh, start: None, prev: None, done: None };
        snap.paint(RECT { left: 0, top: 0, right: vw, bottom: vh }, None);
        SNAP.with(|s| *s.borrow_mut() = Some(snap));

        let inst = GetModuleHandleW(None).map_err(|e| e.to_string())?;
        let class = WNDCLASSW {
            lpfnWndProc: Some(proc),
            hInstance: inst.into(),
            lpszClassName: w!("RoamiesSnap"),
            hCursor: LoadCursorW(None, IDC_CROSS).unwrap_or_default(),
            ..Default::default()
        };
        RegisterClassW(&class);
        let hwnd = CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW, w!("RoamiesSnap"), w!("Roamies: drag an area"), WS_POPUP, vx, vy, vw, vh, None, None, Some(inst.into()), None)
            .map_err(|e| e.to_string())?;
        let _ = ShowWindow(hwnd, SW_SHOW);
        force_front(hwnd);
        let _ = SetFocus(Some(hwnd));
        SetTimer(Some(hwnd), 1, 50, None);
        let mut msg = MSG::default();
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        let snap = SNAP.with(|s| s.borrow_mut().take()).ok_or("lost the selection")?;
        SelectObject(mem, old);
        let _ = DeleteObject(bmp.into());
        let _ = DeleteDC(mem);
        let Some(Some(r)) = snap.done else { return Ok(None) };
        let (w, h) = ((r.right - r.left) as usize, (r.bottom - r.top) as usize);
        let mut rgba = Vec::with_capacity(w * h * 4);
        for y in r.top..r.bottom {
            let i = ((y * vw + r.left) * 4) as usize;
            rgba.extend_from_slice(&snap.full[i..i + w * 4]);
        }
        for p in rgba.chunks_exact_mut(4) {
            p.swap(0, 2);
        }
        save(&Shot { w, h, rgba }, file, Some(1568), 90).map(Some)
    }
}
