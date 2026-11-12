// Windows on screen, the monitors they're on, and which app they belong to.
use crate::logic::{app_matches, hands_off, main_window, Rect, Win};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::ffi::c_void;
use std::sync::{Mutex, OnceLock};
use std::thread::sleep;
use std::time::Duration;
use windows::core::{BOOL, PCWSTR, PWSTR};
use windows::Win32::Foundation::{CloseHandle, HANDLE, HWND, LPARAM, POINT, RECT, TRUE};
use windows::Win32::Graphics::Dwm::{DwmGetWindowAttribute, DWMWA_CLOAKED, DWMWA_EXTENDED_FRAME_BOUNDS};
use windows::Win32::Graphics::Gdi::{EnumDisplayMonitors, GetMonitorInfoW, MonitorFromPoint, HDC, HMONITOR, MONITORINFO, MONITOR_DEFAULTTONEAREST};
use windows::Win32::Security::{GetTokenInformation, TokenElevation, TOKEN_ELEVATION, TOKEN_QUERY};
use windows::Win32::Storage::FileSystem::{GetFileVersionInfoSizeW, GetFileVersionInfoW, VerQueryValueW};
use windows::Win32::System::Diagnostics::ToolHelp::{CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS};
use windows::Win32::System::Threading::{
    AttachThreadInput, GetCurrentProcess, GetCurrentThreadId, OpenProcess, OpenProcessToken, QueryFullProcessImageNameW, PROCESS_NAME_WIN32,
    PROCESS_QUERY_LIMITED_INFORMATION,
};
use windows::Win32::UI::HiDpi::{GetDpiForMonitor, MDT_EFFECTIVE_DPI};
use windows::Win32::UI::Input::KeyboardAndMouse::{SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP, VK_MENU};
use windows::Win32::UI::WindowsAndMessaging::{
    BringWindowToTop, EnumChildWindows, EnumWindows, GetClassNameW, GetForegroundWindow, GetWindowLongW, GetWindowRect, GetWindowTextW,
    GetWindowThreadProcessId, IsIconic, IsWindowVisible, SetForegroundWindow, ShowWindow, GWL_EXSTYLE, SW_RESTORE, WS_EX_TOOLWINDOW,
};

pub fn hwnd(id: i64) -> HWND {
    HWND(id as isize as *mut c_void)
}

// ---------- monitors and units ----------
// Screen points here are physical pixels divided by the scale of the monitor they're on: the units the overlays use
// (an overlay's origin is its monitor's position ÷ that monitor's scale, see safe_js in src/main.rs).

pub struct Mon {
    pub px: RECT,
    pub scale: f64,
    pub primary: bool,
}

impl Mon {
    pub fn pt(&self) -> Rect {
        let s = self.scale;
        Rect::new(self.px.left as f64 / s, self.px.top as f64 / s, (self.px.right - self.px.left) as f64 / s, (self.px.bottom - self.px.top) as f64 / s)
    }
}

fn mon_info(h: HMONITOR) -> Option<Mon> {
    let mut mi = MONITORINFO { cbSize: std::mem::size_of::<MONITORINFO>() as u32, ..Default::default() };
    if !unsafe { GetMonitorInfoW(h, &mut mi) }.as_bool() {
        return None;
    }
    let (mut dx, mut dy) = (96u32, 96u32);
    let _ = unsafe { GetDpiForMonitor(h, MDT_EFFECTIVE_DPI, &mut dx, &mut dy) };
    Some(Mon { px: mi.rcMonitor, scale: dx.max(48) as f64 / 96.0, primary: mi.dwFlags & 1 != 0 })
}

pub fn monitors() -> Vec<Mon> {
    unsafe extern "system" fn cb(h: HMONITOR, _: HDC, _: *mut RECT, l: LPARAM) -> BOOL {
        unsafe { (*(l.0 as *mut Vec<HMONITOR>)).push(h) };
        TRUE
    }
    let mut hs: Vec<HMONITOR> = vec![];
    let _ = unsafe { EnumDisplayMonitors(None, None, Some(cb), LPARAM(&mut hs as *mut _ as isize)) };
    hs.into_iter().filter_map(mon_info).collect()
}

pub fn primary() -> Mon {
    monitors().into_iter().find(|m| m.primary).unwrap_or(Mon { px: RECT { left: 0, top: 0, right: 1920, bottom: 1080 }, scale: 1.0, primary: true })
}

pub fn scale_at_px(x: i32, y: i32) -> f64 {
    mon_info(unsafe { MonitorFromPoint(POINT { x, y }, MONITOR_DEFAULTTONEAREST) }).map_or(1.0, |m| m.scale)
}

/// A physical-pixel rect in screen points (by the monitor its middle is on).
pub fn px_to_pt(r: RECT) -> Rect {
    let s = scale_at_px((r.left + r.right) / 2, (r.top + r.bottom) / 2);
    Rect::new(r.left as f64 / s, r.top as f64 / s, (r.right - r.left) as f64 / s, (r.bottom - r.top) as f64 / s)
}

/// The monitor a screen point is on (or the nearest one).
fn mon_at_pt(x: f64, y: f64) -> Mon {
    let ms = monitors();
    let d = |m: &Mon| {
        let r = m.pt();
        let dx = (r.x - x).max(0.0).max(x - r.max_x());
        let dy = (r.y - y).max(0.0).max(y - r.max_y());
        dx.hypot(dy)
    };
    ms.into_iter().min_by(|a, b| d(a).total_cmp(&d(b))).unwrap_or_else(primary)
}

pub fn pt_to_px(x: f64, y: f64) -> (i32, i32) {
    let s = mon_at_pt(x, y).scale;
    ((x * s).round() as i32, (y * s).round() as i32)
}

pub fn rect_to_px(r: Rect) -> RECT {
    let s = mon_at_pt(r.mid_x(), r.mid_y()).scale;
    RECT { left: (r.x * s).round() as i32, top: (r.y * s).round() as i32, right: (r.max_x() * s).round() as i32, bottom: (r.max_y() * s).round() as i32 }
}

// ---------- processes ----------

pub fn exe_path(pid: u32) -> Option<String> {
    let h = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) }.ok()?;
    let mut buf = [0u16; 1024];
    let mut n = buf.len() as u32;
    let ok = unsafe { QueryFullProcessImageNameW(h, PROCESS_NAME_WIN32, PWSTR(buf.as_mut_ptr()), &mut n) }.is_ok();
    let _ = unsafe { CloseHandle(h) };
    ok.then(|| String::from_utf16_lossy(&buf[..n as usize]))
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(Some(0)).collect()
}

/// The program's own name (its FileDescription: "Notepad", "Google Chrome"), if it has one.
fn file_description(path: &str) -> Option<String> {
    let p = wide(path);
    let size = unsafe { GetFileVersionInfoSizeW(PCWSTR(p.as_ptr()), None) };
    if size == 0 {
        return None;
    }
    let mut data = vec![0u8; size as usize];
    unsafe { GetFileVersionInfoW(PCWSTR(p.as_ptr()), None, size, data.as_mut_ptr() as *mut c_void) }.ok()?;
    let query = |sub: &str| -> Option<(*const u8, u32)> {
        let s = wide(sub);
        let (mut ptr, mut len) = (std::ptr::null_mut::<c_void>(), 0u32);
        unsafe { VerQueryValueW(data.as_ptr() as *const c_void, PCWSTR(s.as_ptr()), &mut ptr, &mut len) }.as_bool().then_some((ptr as *const u8, len))
    };
    let mut langs = vec![];
    if let Some((ptr, len)) = query("\\VarFileInfo\\Translation") {
        for i in 0..(len as usize / 4) {
            let (lang, cp) = unsafe { (*(ptr as *const u16).add(i * 2), *(ptr as *const u16).add(i * 2 + 1)) };
            langs.push(format!("{lang:04x}{cp:04x}"));
        }
    }
    langs.extend(["040904b0".into(), "040904e4".into(), "04090000".into()]);
    for l in langs {
        if let Some((ptr, len)) = query(&format!("\\StringFileInfo\\{l}\\FileDescription")) {
            if len > 1 {
                let s = String::from_utf16_lossy(unsafe { std::slice::from_raw_parts(ptr as *const u16, len as usize - 1) });
                let s = s.trim_matches(char::from(0)).trim().to_string();
                if !s.is_empty() {
                    return Some(s);
                }
            }
        }
    }
    None
}

/// (app name, program file name without .exe) for a process, cached.
pub fn proc_names(pid: u32) -> (String, String) {
    static CACHE: OnceLock<Mutex<HashMap<u32, (String, String)>>> = OnceLock::new();
    let cache = CACHE.get_or_init(Default::default);
    if let Some(v) = cache.lock().unwrap().get(&pid) {
        return v.clone();
    }
    let path = exe_path(pid).unwrap_or_default();
    let file = path.rsplit(['\\', '/']).next().unwrap_or("").to_string();
    let exe = file.strip_suffix(".exe").or_else(|| file.strip_suffix(".EXE")).unwrap_or(&file).to_string();
    let app = file_description(&path).filter(|d| !d.eq_ignore_ascii_case(&file)).unwrap_or_else(|| exe.clone());
    let v = (if app.is_empty() { "?".into() } else { app }, exe);
    cache.lock().unwrap().insert(pid, v.clone());
    v
}

pub fn parent_pid(pid: u32) -> Option<u32> {
    let snap = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) }.ok()?;
    let mut e = PROCESSENTRY32W { dwSize: std::mem::size_of::<PROCESSENTRY32W>() as u32, ..Default::default() };
    let mut found = None;
    if unsafe { Process32FirstW(snap, &mut e) }.is_ok() {
        loop {
            if e.th32ProcessID == pid {
                found = Some(e.th32ParentProcessID);
                break;
            }
            if unsafe { Process32NextW(snap, &mut e) }.is_err() {
                break;
            }
        }
    }
    let _ = unsafe { CloseHandle(snap) };
    found
}

fn token_elevated(process: HANDLE) -> Option<bool> {
    let mut tok = HANDLE::default();
    unsafe { OpenProcessToken(process, TOKEN_QUERY, &mut tok) }.ok()?;
    let mut el = TOKEN_ELEVATION::default();
    let mut n = 0u32;
    let ok = unsafe { GetTokenInformation(tok, TokenElevation, Some(&mut el as *mut _ as *mut c_void), std::mem::size_of::<TOKEN_ELEVATION>() as u32, &mut n) }.is_ok();
    let _ = unsafe { CloseHandle(tok) };
    ok.then_some(el.TokenIsElevated != 0)
}

/// The app runs as administrator and this helper doesn't: Windows drops input sent to it (UIPI).
pub fn elevated_above_me(pid: u32) -> bool {
    if token_elevated(unsafe { GetCurrentProcess() }) == Some(true) {
        return false;
    }
    let Ok(h) = (unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) }) else { return false };
    let el = token_elevated(h);
    let _ = unsafe { CloseHandle(h) };
    el == Some(true)
}

/// Never point at Roamies itself (its overlay windows cover every screen).
pub fn is_roamies(pid: u32, exe: &str) -> bool {
    static ME: OnceLock<(u32, Option<u32>)> = OnceLock::new();
    let (me, parent) = *ME.get_or_init(|| {
        let me = std::process::id();
        (me, parent_pid(me))
    });
    pid == me || Some(pid) == parent || ["roamies", "roamies-voice", "roamies-hands"].contains(&exe.to_lowercase().as_str())
}

// ---------- windows ----------

fn text_of(h: HWND) -> String {
    let mut b = [0u16; 512];
    let n = unsafe { GetWindowTextW(h, &mut b) };
    String::from_utf16_lossy(&b[..n.max(0) as usize])
}

pub fn class_of(h: HWND) -> String {
    let mut b = [0u16; 256];
    let n = unsafe { GetClassNameW(h, &mut b) };
    String::from_utf16_lossy(&b[..n.max(0) as usize])
}

pub fn pid_of(h: HWND) -> u32 {
    let mut pid = 0u32;
    unsafe { GetWindowThreadProcessId(h, Some(&mut pid)) };
    pid
}

fn cloaked(h: HWND) -> bool {
    let mut c = 0u32;
    unsafe { DwmGetWindowAttribute(h, DWMWA_CLOAKED, &mut c as *mut _ as *mut c_void, 4) }.is_ok() && c != 0
}

/// The window's visible frame in physical pixels (without the invisible resize border).
pub fn frame_px(h: HWND) -> RECT {
    let mut r = RECT::default();
    if unsafe { DwmGetWindowAttribute(h, DWMWA_EXTENDED_FRAME_BOUNDS, &mut r as *mut _ as *mut c_void, std::mem::size_of::<RECT>() as u32) }.is_err() {
        let _ = unsafe { GetWindowRect(h, &mut r) };
    }
    r
}

pub fn window_rect_px(h: HWND) -> RECT {
    let mut r = RECT::default();
    let _ = unsafe { GetWindowRect(h, &mut r) };
    r
}

/// Store apps sit in an ApplicationFrameHost window; the app's own process owns a child of it.
fn real_pid(h: HWND, pid: u32) -> u32 {
    unsafe extern "system" fn cb(c: HWND, l: LPARAM) -> BOOL {
        let st = unsafe { &mut *(l.0 as *mut (u32, u32)) };
        let p = pid_of(c);
        if p != st.0 {
            st.1 = p;
            return BOOL(0);
        }
        TRUE
    }
    let mut st = (pid, pid);
    let _ = unsafe { EnumChildWindows(Some(h), Some(cb), LPARAM(&mut st as *mut _ as isize)) };
    st.1
}

fn top_level() -> Vec<HWND> {
    unsafe extern "system" fn cb(h: HWND, l: LPARAM) -> BOOL {
        unsafe { (*(l.0 as *mut Vec<HWND>)).push(h) };
        TRUE
    }
    let mut v: Vec<HWND> = vec![];
    let _ = unsafe { EnumWindows(Some(cb), LPARAM(&mut v as *mut _ as isize)) };
    v
}

const SHELL: &[&str] = &["Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow", "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow"];

fn win_of(h: HWND, min: bool) -> Option<Win> {
    unsafe {
        if !IsWindowVisible(h).as_bool() || (!min && IsIconic(h).as_bool()) || cloaked(h) {
            return None;
        }
        if GetWindowLongW(h, GWL_EXSTYLE) as u32 & WS_EX_TOOLWINDOW.0 != 0 {
            return None;
        }
    }
    let class = class_of(h);
    if SHELL.contains(&class.as_str()) {
        return None;
    }
    let r = px_to_pt(frame_px(h));
    if !min && (r.w <= 80.0 || r.h <= 60.0) {
        return None;
    }
    let mut pid = pid_of(h);
    let title = text_of(h);
    if class == "ApplicationFrameWindow" {
        pid = real_pid(h, pid);
    }
    let (mut app, exe) = proc_names(pid);
    if is_roamies(pid, &exe) {
        return None;
    }
    if exe.eq_ignore_ascii_case("ApplicationFrameHost") && !title.is_empty() {
        app = title.clone();
    }
    Some(Win { id: h.0 as isize as i64, pid, app, exe, title, r })
}

/// Visible app windows, front to back.
pub fn windows() -> Vec<Win> {
    top_level().into_iter().filter_map(|h| win_of(h, false)).collect()
}

/// Every top-level window of a process that's showing (menus, pop-ups and small ones too), front to back.
pub fn popups_of(pid: u32) -> Vec<HWND> {
    top_level().into_iter().filter(|&h| unsafe { IsWindowVisible(h).as_bool() } && !cloaked(h) && pid_of(h) == pid).collect()
}

pub fn front_hwnd() -> HWND {
    unsafe { GetForegroundWindow() }
}

/// The app in front (its process), unless that's Roamies.
pub fn front_pid() -> Option<u32> {
    let h = front_hwnd();
    if h.0.is_null() {
        return None;
    }
    let mut pid = pid_of(h);
    if class_of(h) == "ApplicationFrameWindow" {
        pid = real_pid(h, pid);
    }
    let (_, exe) = proc_names(pid);
    (!is_roamies(pid, &exe)).then_some(pid)
}

pub fn front_hands_off() -> Option<&'static str> {
    let h = front_hwnd();
    if h.0.is_null() {
        return None;
    }
    let mut pid = pid_of(h);
    if class_of(h) == "ApplicationFrameWindow" {
        pid = real_pid(h, pid);
    }
    hands_off(&proc_names(pid).1)
}

pub fn win_info(w: &Win) -> Value {
    json!({ "id": w.id, "app": w.app, "title": w.title, "bounds": w.r.ints() })
}

pub fn no_window(target: &str) -> String {
    format!("No matching window is open{}.", target.strip_prefix("app:").map(|n| format!(" for {n}")).unwrap_or_default())
}

/// A target to a window. "front" = the frontmost app that isn't Roamies.
pub fn resolve(target: &str) -> Option<Win> {
    let all = windows();
    if target == "front" {
        if let Some(pid) = front_pid() {
            let mine: Vec<Win> = all.iter().filter(|w| w.pid == pid).cloned().collect();
            if let Some(w) = main_window(&mine) {
                return Some(w.clone());
            }
        }
        return all.into_iter().next();
    }
    if let Some(id) = target.strip_prefix("window:").and_then(|s| s.parse::<i64>().ok()) {
        return all.into_iter().find(|w| w.id == id);
    }
    let name = target.strip_prefix("app:")?;
    let mine: Vec<Win> = all.iter().filter(|w| app_matches(&w.app, &w.exe, name)).cloned().collect();
    if let Some(w) = main_window(&mine) {
        return Some(w.clone());
    }
    // Running but minimised: bring it back and look again.
    let h = top_level().into_iter().find(|&h| unsafe { IsIconic(h).as_bool() } && win_of(h, true).is_some_and(|w| app_matches(&w.app, &w.exe, name)))?;
    let pid = win_of(h, true)?.pid;
    unsafe {
        let _ = ShowWindow(h, SW_RESTORE);
    }
    force_front(h);
    sleep(Duration::from_millis(600));
    windows().into_iter().find(|w| w.pid == pid)
}

fn alt_tap() {
    let key = |up: bool| INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 { ki: KEYBDINPUT { wVk: VK_MENU, dwFlags: if up { KEYEVENTF_KEYUP } else { Default::default() }, ..Default::default() } },
    };
    unsafe { SendInput(&[key(false), key(true)], std::mem::size_of::<INPUT>() as i32) };
}

/// Asks Windows to put a window in front. A background process may not take the foreground, so it borrows the
/// front app's input queue for the call, then (if that wasn't enough) taps Alt, which Windows counts as the user's.
pub fn force_front(h: HWND) {
    unsafe {
        if IsIconic(h).as_bool() {
            let _ = ShowWindow(h, SW_RESTORE);
        }
        let fg = GetForegroundWindow();
        let (me, them) = (GetCurrentThreadId(), GetWindowThreadProcessId(fg, None));
        let attached = them != 0 && them != me && AttachThreadInput(me, them, true).as_bool();
        let ok = SetForegroundWindow(h).as_bool();
        let _ = BringWindowToTop(h);
        if attached {
            let _ = AttachThreadInput(me, them, false);
        }
        if !ok || GetForegroundWindow() != h {
            alt_tap();
            let _ = SetForegroundWindow(h);
        }
    }
}

/// Brings the window's app forward and waits until Windows reports it in front (up to ~1.2 s).
pub fn bring_to_front(w: &Win) -> bool {
    if front_pid() == Some(w.pid) {
        return true;
    }
    for _ in 0..3 {
        force_front(hwnd(w.id));
        for _ in 0..8 {
            sleep(Duration::from_millis(50));
            if front_pid() == Some(w.pid) {
                sleep(Duration::from_millis(100));
                return true;
            }
        }
    }
    front_pid() == Some(w.pid)
}

/// The topmost window (not Roamies) under a screen point.
pub fn window_at(x: f64, y: f64) -> Option<Win> {
    windows().into_iter().find(|w| w.r.contains(x, y))
}

pub fn front_title() -> String {
    text_of(front_hwnd())
}
