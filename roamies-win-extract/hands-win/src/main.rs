// Hands helper for Roamies on Windows, the twin of hands/main.swift: lets the agent see one window or app, read its
// controls and work them. Same commands, same JSON: one command per run, one JSON object on stdout, live progress as
// "@"-prefixed JSON lines on stderr.
//
//   roamies-hands windows                      visible windows, front to back
//   roamies-hands front                        the app, window title and (for a browser) address in front
//   roamies-hands capture <out.jpg> front|app:<name>|window:<id>|screen [max] [thumb thumbMax]
//   roamies-hands locate front|app:<name>|window:<id>|screen
//   roamies-hands select <out.png>             drag to choose an area (Esc cancels)
//   roamies-hands ui front|app:<name>|window:<id> [controls]
//   roamies-hands locate-control <target> <id>
//   roamies-hands find-text <target> <text> [scroll]
//   roamies-hands ocr-find <target> <text> [all]
//   roamies-hands refine '[[x,y,w,h],…]' [window:<id>]
//   roamies-hands act <json>                   {"app":"Notepad","steps":[{"do":"fill","field":"Text editor","text":"hi"}]}
//   roamies-hands --self-test                  the logic checks (no windows needed)
//   roamies-hands --smoke                      a real run against Notepad (CI)
//
// Coordinates are screen points: physical pixels ÷ the scale of the monitor they're on, which is how the overlays
// place things (each overlay's origin is its monitor's position ÷ its scale; see safe_js in src/main.rs). Window ids
// are HWNDs. trusted, guide and screencheck are macOS permissions and answer "unsupported" here.
#![cfg_attr(not(windows), allow(dead_code))]

mod logic;

#[cfg(windows)]
mod act;
#[cfg(windows)]
mod capture;
#[cfg(windows)]
mod ocr;
#[cfg(windows)]
mod select;
#[cfg(windows)]
mod uia;
#[cfg(windows)]
mod win;

use serde_json::{json, Value};
use std::io::Write;

pub fn out(v: Value) -> ! {
    #[cfg(windows)]
    act::give_back();
    let mut o = std::io::stdout().lock();
    let _ = writeln!(o, "{v}");
    let _ = o.flush();
    std::process::exit(0)
}

pub fn fail(msg: &str) -> ! {
    out(json!({ "ok": false, "error": msg }))
}

/// Live progress for the overlay while acting: one JSON line per event on stderr, prefixed with "@".
pub fn progress(v: Value) {
    let mut e = std::io::stderr().lock();
    let _ = writeln!(e, "@{v}");
    let _ = e.flush();
}

fn self_test() -> ! {
    let mut bad = 0;
    for (name, ok) in logic::self_test() {
        println!("{} {name}", if ok { "ok" } else { "FAIL" });
        bad += usize::from(!ok);
    }
    std::process::exit(i32::from(bad > 0))
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(cmd) = args.first().cloned() else { fail("no command") };
    if cmd == "--self-test" {
        self_test();
    }
    #[cfg(windows)]
    windows_main(&cmd, &args);
    #[cfg(not(windows))]
    fail(&format!("{cmd}: this helper only runs on Windows"));
}

#[cfg(windows)]
fn windows_main(cmd: &str, args: &[String]) -> ! {
    use logic::{app_matches, whole_match};
    use win::{no_window, resolve, win_info, windows};
    use windows::Win32::System::Com::{CoInitializeEx, COINIT_MULTITHREADED};
    use windows::Win32::UI::HiDpi::{SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2};
    unsafe {
        let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
    }
    let arg = |i: usize| args.get(i).map(String::as_str);
    match cmd {
        "--smoke" => smoke::run(),
        "trusted" | "guide" | "screencheck" => out(json!({ "ok": false, "unsupported": true, "error": "Not needed on Windows." })),
        "windows" => {
            let front = win::front_pid();
            let list: Vec<Value> = windows().iter().take(30).map(|w| {
                let mut i = win_info(w);
                if Some(w.pid) == front {
                    i["frontApp"] = true.into();
                }
                i
            }).collect();
            out(json!({ "ok": true, "windows": list }))
        }
        // Where the user is right now: the app in front (never Roamies), its window's title, and a browser's address.
        "front" => {
            let mut o = json!({ "ok": true });
            let Some(w) = resolve("front") else { out(o) };
            o["app"] = w.app.clone().into();
            o["exe"] = w.exe.clone().into();
            let title = if win::front_pid() == Some(w.pid) { win::front_title() } else { w.title.clone() };
            if !title.is_empty() {
                o["title"] = title.into();
            }
            if logic::is_browser(&w.exe) {
                if let Some(u) = uia::browser_address(&w) {
                    o["url"] = u.into();
                }
            }
            out(o)
        }
        "locate" => {
            let Some(t) = arg(1) else { fail("usage: locate <target>") };
            if t == "screen" {
                out(json!({ "ok": true, "what": "screen" }));
            }
            let Some(w) = resolve(t) else { fail(&no_window(t)) };
            out(json!({ "ok": true, "window": win_info(&w) }))
        }
        "capture" => {
            let (Some(file), Some(target)) = (arg(1), arg(2)) else { fail("usage: capture <out> <target> [max] [thumb thumbMax]") };
            let max = arg(3).and_then(|m| m.parse().ok());
            let thumb = arg(4);
            let thumb_max = arg(5).and_then(|m| m.parse().ok()).unwrap_or(900);
            let (shot, mut o) = if target == "screen" {
                let m = win::primary();
                let Some(s) = capture::grab_screen(m.px) else { fail("Screen capture failed.") };
                (s, json!({ "ok": true, "file": file, "what": "screen", "bounds": m.pt().ints() }))
            } else {
                let Some(w) = resolve(target) else { fail(&no_window(target)) };
                let Some((s, _)) = capture::grab_window(win::hwnd(w.id)) else { fail("Window capture failed.") };
                (s, json!({ "ok": true, "file": file, "window": win_info(&w) }))
            };
            match capture::save(&shot, file, max, 85) {
                Ok(px) => o["pixels"] = json!(px),
                Err(e) => fail(&format!("Couldn't save the screenshot: {e}")),
            }
            if let Some(t) = thumb {
                let _ = capture::save(&shot, t, Some(thumb_max), 72);
            }
            out(o)
        }
        "select" => {
            let Some(file) = arg(1) else { fail("usage: select <out>") };
            match select::select(file) {
                Ok(Some(_)) => out(json!({ "ok": true, "file": file })),
                Ok(None) => out(json!({ "ok": false, "cancelled": true })),
                Err(e) => fail(&e),
            }
        }
        "ui" => {
            let Some(w) = arg(1).and_then(resolve) else { fail("No matching window is open.") };
            let tree = uia::Tree::of_window(&w);
            let items: Vec<Value> = tree.walk(w.r, 260).into_iter().map(|c| c.1).collect();
            // ui <target> controls: just the controls (to re-find ids before acting), without the page's text.
            if arg(2) == Some("controls") {
                out(json!({ "ok": true, "window": win_info(&w), "controls": items }));
            }
            let (on, off) = tree.scan_text(w.r);
            let note = if items.is_empty() { "This app shares no controls. Use a screenshot plus click at coordinates instead." } else { "" };
            out(json!({ "ok": true, "window": win_info(&w), "controls": items, "texts": logic::page_text(on, 5000), "elsewhere": logic::offscreen_text(off, 4000), "note": note }))
        }
        "locate-control" => {
            let (Some(w), Some(id)) = (arg(1).and_then(resolve), arg(2).and_then(|s| s.parse::<usize>().ok())) else { fail("usage: locate-control <target> <id>") };
            let items = uia::Tree::of_window(&w).walk(w.r, 260);
            if id < 1 || id > items.len() {
                fail(&format!("No control {id} in {}. Read the window again.", w.app));
            }
            out(json!({ "ok": true, "window": win_info(&w), "control": items[id - 1].1 }))
        }
        "refine" => {
            let Some(list) = arg(1).and_then(|s| serde_json::from_str::<Vec<Vec<f64>>>(s).ok()) else { fail("usage: refine '[[x,y,w,h],...]' [window:<id>]") };
            let w = arg(2).and_then(resolve);
            let boxes: Vec<Value> = capture::refine(&list, w.as_ref()).into_iter().map(|r| r.map_or(Value::Null, |r| json!(r.tenths()))).collect();
            out(json!({ "ok": true, "boxes": boxes }))
        }
        "ocr-find" => {
            let (Some(t), Some(text)) = (arg(1), arg(2)) else { fail("usage: ocr-find <target> <text> [all]") };
            let w = if t == "screen" { None } else { Some(resolve(t).unwrap_or_else(|| fail("No matching window is open."))) };
            match ocr::ocr_find(w.as_ref(), text, arg(3) == Some("all")) {
                Ok(m) => out(json!({ "ok": true, "matches": m })),
                Err(e) => fail(&e),
            }
        }
        // Looks through every window of the app ("app:X"), or every window on screen ("screen"), front to back.
        "find-text" => {
            let (Some(t), Some(q)) = (arg(1), arg(2)) else { fail("usage: find-text <target> <text>") };
            let wins: Vec<logic::Win> = if t == "screen" {
                windows()
            } else if t == "front" {
                resolve("front").map(|f| windows().into_iter().filter(|w| w.pid == f.pid).collect()).unwrap_or_default()
            } else if let Some(name) = t.strip_prefix("app:") {
                windows().into_iter().filter(|w| app_matches(&w.app, &w.exe, name)).collect()
            } else {
                resolve(t).into_iter().collect()
            };
            if wins.is_empty() {
                fail("No matching window is open.");
            }
            let search = || {
                let mut all: Vec<Value> = vec![];
                for (z, w) in wins.iter().take(12).enumerate() {
                    for (i, (_, mut m)) in uia::Tree::of_window(w).find_text(w.r, q, false, 8).into_iter().enumerate() {
                        m["window"] = win_info(w);
                        m["rank"] = (z * 100 + i).into();
                        all.push(m);
                    }
                }
                all
            };
            let mut all = search();
            let whole = |m: &Value| whole_match(m["match"].as_str().unwrap_or(""), m["text"].as_str().unwrap_or(""), q);
            // "scroll": nothing whole on screen, so bring it into view in the frontmost window that has it, then look again.
            if arg(3) == Some("scroll") && !all.iter().any(whole) && wins.iter().take(12).any(|w| scroll_to_text(w, q)) {
                all = search();
            }
            // Exact text in any window beats a partial match in the front one.
            let ql = q.trim().to_lowercase();
            let (exact, rest): (Vec<Value>, Vec<Value>) = all.into_iter().partition(|m| m["text"].as_str().unwrap_or("").trim().to_lowercase() == ql);
            out(json!({ "ok": true, "matches": exact.into_iter().chain(rest).take(8).collect::<Vec<_>>() }))
        }
        "act" => {
            let Some(plan) = arg(1) else { fail("bad plan") };
            act::act(plan)
        }
        // secret-* is handled by the sidecar on Windows (secrets.mjs).
        other => fail(&format!("unknown command {other}")),
    }
}

/// Scrolls the best whole match for `q` that's out of view into view. True if something was scrolled.
#[cfg(windows)]
fn scroll_to_text(w: &logic::Win, q: &str) -> bool {
    let tree = uia::Tree::of_window(w);
    let Some((e, _)) = tree.find_text(w.r, q, true, 8).into_iter().find(|m| {
        logic::whole_match(m.1["match"].as_str().unwrap_or(""), m.1["text"].as_str().unwrap_or(""), q) && !m.0.r.is_some_and(|f| w.r.intersects(&f))
    }) else {
        return false;
    };
    let mut cur = tree.index_of(&e.el);
    for _ in 0..4 {
        let Some(c) = cur else { break };
        if uia::scroll_into_view(&tree.nodes[c].el) {
            std::thread::sleep(std::time::Duration::from_millis(350));
            return true;
        }
        cur = tree.nodes[c].parent;
    }
    false
}

/// `--smoke`: the real helper against a real app, the way the sidecar calls it (each command its own run).
#[cfg(windows)]
mod smoke {
    use crate::logic::{app_matches, hands_off};
    use serde_json::{json, Value};
    use std::process::{Command, Stdio};
    use std::thread::sleep;
    use std::time::{Duration, Instant};

    fn hands(args: &[&str]) -> Value {
        let exe = std::env::current_exe().expect("own path");
        let o = Command::new(exe).args(args).stderr(Stdio::null()).output().expect("run helper");
        let text = String::from_utf8_lossy(&o.stdout);
        serde_json::from_str(text.trim().lines().last().unwrap_or("{}")).unwrap_or(json!({}))
    }

    fn wait_window(pred: impl Fn(&Value) -> bool) -> Option<Value> {
        let until = Instant::now() + Duration::from_secs(20);
        while Instant::now() < until {
            if let Some(w) = hands(&["windows"])["windows"].as_array().and_then(|l| l.iter().find(|w| pred(w)).cloned()) {
                return Some(w);
            }
            sleep(Duration::from_millis(300));
        }
        None
    }

    fn kill(pid: u64) {
        let _ = Command::new("taskkill").args(["/F", "/PID", &pid.to_string()]).stdout(Stdio::null()).stderr(Stdio::null()).status();
    }

    pub fn run() -> ! {
        let mut bad = 0;
        let mut check = |name: &str, ok: bool, detail: String| {
            println!("{} {name}{}", if ok { "ok" } else { "FAIL" }, if ok || detail.is_empty() { String::new() } else { format!(" ({detail})") });
            bad += usize::from(!ok);
        };
        let phrase = "Roamies smoke 4711";
        let _ = Command::new("notepad.exe").spawn();
        let is_notepad = |w: &Value| app_matches(w["app"].as_str().unwrap_or(""), "", "notepad") || w["title"].as_str().unwrap_or("").contains("Notepad");
        let Some(win) = wait_window(is_notepad) else {
            println!("FAIL windows lists Notepad ({})", hands(&["windows"]));
            std::process::exit(1)
        };
        check("windows lists Notepad", true, String::new());
        let id = win["id"].as_i64().unwrap_or(0);
        let target = format!("window:{id}");

        let loc = hands(&["locate", "app:Notepad"]);
        check("locate app:Notepad finds it", loc["window"]["id"].as_i64() == Some(id), loc.to_string());

        let file = std::env::temp_dir().join(format!("roamies-smoke-{}.jpg", std::process::id()));
        let file_s = file.to_string_lossy().to_string();
        let cap = hands(&["capture", &file_s, &target, "1568"]);
        let size = std::fs::metadata(&file).map(|m| m.len()).unwrap_or(0);
        check("capture writes a JPEG", cap["ok"] == json!(true) && size > 1000, cap.to_string());
        let (px, b) = (&cap["pixels"], &cap["window"]["bounds"]);
        let ratio = |a: &Value, i: usize, j: usize| a[i].as_f64().unwrap_or(0.0) / a[j].as_f64().unwrap_or(1.0).max(1.0);
        check("its picture has the window's shape", (ratio(px, 0, 1) / ratio(b, 2, 3).max(0.01) - 1.0).abs() <= 0.02, format!("pixels {px}, bounds {b}"));
        let _ = std::fs::remove_file(&file);

        let ui = hands(&["ui", &target]);
        let field = ui["controls"].as_array().and_then(|c| c.iter().find(|c| ["text field", "text area"].contains(&c["role"].as_str().unwrap_or(""))).cloned());
        check("ui finds the text box", field.is_some(), ui.to_string());
        if let Some(f) = field {
            let plan = json!({ "window": id, "steps": [{ "do": "fill", "id": f["id"], "text": phrase }] }).to_string();
            let r = hands(&["act", &plan]);
            check("act fills it", r["ok"] == json!(true), r.to_string());
            let ui = hands(&["ui", &target, "controls"]);
            let shows = ui["controls"].as_array().is_some_and(|c| c.iter().any(|c| c["value"].as_str().unwrap_or("").contains(phrase)));
            check("ui shows what was typed", shows, ui.to_string());
            sleep(Duration::from_millis(400));
            let o = hands(&["ocr-find", &target, "4711"]);
            if o["error"].as_str().is_some_and(|e| e.contains("no OCR language")) {
                println!("skip ocr-find: {}", o["error"].as_str().unwrap_or(""));
            } else {
                check("ocr-find reads it off the window", o["matches"].as_array().is_some_and(|m| !m.is_empty()), o.to_string());
            }
        }

        // A terminal is never typed in.
        let mut term = Command::new("cmd.exe").args(["/k", "title RoamiesSmoke"]).spawn().ok();
        match wait_window(|w| w["title"].as_str().unwrap_or("").contains("RoamiesSmoke")) {
            Some(t) => {
                let plan = json!({ "window": t["id"], "steps": [{ "do": "type", "text": "echo hi" }] }).to_string();
                let r = hands(&["act", &plan]);
                check("act refuses a terminal", r["error"].as_str().unwrap_or("").starts_with("Blocked for safety"), r.to_string());
            }
            None => check("act refuses a terminal", hands_off("cmd").is_some(), "no console window showed up".into()),
        }
        if let Some(t) = term.as_mut() {
            let _ = t.kill();
        }
        if let Some(pid) = notepad_pid(id) {
            kill(pid);
        }
        std::process::exit(i32::from(bad > 0))
    }

    fn notepad_pid(id: i64) -> Option<u64> {
        Some(crate::win::pid_of(crate::win::hwnd(id)) as u64).filter(|&p| p != 0)
    }
}
