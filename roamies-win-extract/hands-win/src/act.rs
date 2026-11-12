// `act`: runs a list of steps in another app, like the act command in hands/main.swift, with the same steps, checks
// and messages. Whatever UI Automation can do happens without the cursor (press, select, set a value); real clicks,
// keys, typing and scrolling borrow the user's cursor and keyboard (with the same "borrow"/"return" progress
// events), then give them back when the run ends.
use crate::logic::*;
use crate::uia::*;
use crate::win::*;
use crate::{out, progress};
use serde_json::{json, Value};
use std::sync::Mutex;
use std::thread::sleep;
use std::time::{Duration, Instant};
use windows::Win32::Foundation::{HWND, POINT};
use windows::Win32::System::SystemInformation::GetTickCount;
use windows::Win32::UI::Input::KeyboardAndMouse::*;
use windows::Win32::UI::WindowsAndMessaging::{GetCursorPos, SetCursorPos};

fn ms(n: u64) {
    sleep(Duration::from_millis(n));
}

// ---------- input ----------

fn send(inputs: &[INPUT]) {
    unsafe { SendInput(inputs, std::mem::size_of::<INPUT>() as i32) };
}
fn key_input(vk: u16, scan: u16, flags: KEYBD_EVENT_FLAGS) -> INPUT {
    INPUT { r#type: INPUT_KEYBOARD, Anonymous: INPUT_0 { ki: KEYBDINPUT { wVk: VIRTUAL_KEY(vk), wScan: scan, dwFlags: flags, ..Default::default() } } }
}
fn mouse_input(flags: MOUSE_EVENT_FLAGS, data: i32) -> INPUT {
    INPUT { r#type: INPUT_MOUSE, Anonymous: INPUT_0 { mi: MOUSEINPUT { mouseData: data as _, dwFlags: flags, ..Default::default() } } }
}

pub fn press_combo(mods: u8, vk: u16) {
    let mod_keys: Vec<u16> = [(crate::logic::MOD_CTRL, 0x11), (crate::logic::MOD_SHIFT, 0x10), (crate::logic::MOD_ALT, 0x12), (crate::logic::MOD_WIN, 0x5B)].iter().filter(|m| mods & m.0 != 0).map(|m| m.1).collect();
    let ext = if is_extended(vk) { KEYEVENTF_EXTENDEDKEY } else { KEYBD_EVENT_FLAGS(0) };
    let mut seq: Vec<INPUT> = mod_keys.iter().map(|&m| key_input(m, 0, KEYBD_EVENT_FLAGS(0))).collect();
    seq.push(key_input(vk, 0, ext));
    seq.push(key_input(vk, 0, ext | KEYEVENTF_KEYUP));
    seq.extend(mod_keys.iter().rev().map(|&m| key_input(m, 0, KEYEVENTF_KEYUP)));
    send(&seq);
    ms(50);
}

/// "cmd+a", "shift+tab", "return", "*" (cmd is Ctrl).
pub fn press_key(combo: &str) -> bool {
    match parse_combo(combo) {
        Some((m, k)) => {
            press_combo(m, k);
            true
        }
        None => false,
    }
}

/// Types text as characters (any language), 16 at a time; new lines and tabs as their keys.
pub fn type_text(s: &str) {
    let mut batch: Vec<INPUT> = vec![];
    let flush = |b: &mut Vec<INPUT>| {
        if !b.is_empty() {
            send(b);
            b.clear();
            ms(12);
        }
    };
    for ch in s.chars() {
        match ch {
            '\r' => {}
            '\n' | '\t' => {
                flush(&mut batch);
                press_combo(0, if ch == '\n' { 0x0D } else { 0x09 });
            }
            _ => {
                let mut u = [0u16; 2];
                for &unit in ch.encode_utf16(&mut u).iter() {
                    batch.push(key_input(0, unit, KEYEVENTF_UNICODE));
                    batch.push(key_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP));
                }
                if batch.len() >= 32 {
                    flush(&mut batch);
                }
            }
        }
    }
    flush(&mut batch);
}

pub fn move_to(x: f64, y: f64) {
    let (px, py) = pt_to_px(x, y);
    let _ = unsafe { SetCursorPos(px, py) };
}

pub fn click(x: f64, y: f64) {
    move_to(x, y);
    ms(40);
    send(&[mouse_input(MOUSEEVENTF_LEFTDOWN, 0)]);
    ms(40);
    send(&[mouse_input(MOUSEEVENTF_LEFTUP, 0)]);
    ms(40);
}

/// Turns the wheel at a spot: notches up (positive) or down, and right (positive) or left.
pub fn wheel(x: f64, y: f64, dy: i32, dx: i32) {
    move_to(x, y);
    ms(30);
    if dy != 0 {
        send(&[mouse_input(MOUSEEVENTF_WHEEL, dy * 120)]);
    }
    if dx != 0 {
        send(&[mouse_input(MOUSEEVENTF_HWHEEL, dx * 120)]);
    }
}

// ---------- borrowing the real cursor ----------

struct Borrow {
    /// Background mode (the default; ROAMIES_BG=0 turns it off): the target app stays behind until a step needs the
    /// real cursor or keyboard.
    bg: bool,
    win: Option<Win>,
    fg_active: bool,
    saved: Option<(POINT, isize)>,
    used_real: bool,
    user_busy: bool,
}
static B: Mutex<Borrow> = Mutex::new(Borrow { bg: true, win: None, fg_active: false, saved: None, used_real: false, user_busy: false });

fn idle_ms() -> u32 {
    let mut li = LASTINPUTINFO { cbSize: std::mem::size_of::<LASTINPUTINFO>() as u32, dwTime: 0 };
    if !unsafe { GetLastInputInfo(&mut li) }.as_bool() {
        return u32::MAX;
    }
    unsafe { GetTickCount() }.wrapping_sub(li.dwTime)
}

/// Makes sure this run has the real cursor and keyboard. The first time: tell the user (the Sidekick says so), and
/// if they're using the mouse or keyboard give them a moment to let go; if they keep going, don't take over (false).
fn take_real() -> bool {
    {
        let b = B.lock().unwrap();
        if !b.bg || b.fg_active {
            return true;
        }
    }
    B.lock().unwrap().used_real = true;
    progress(json!({ "ev": "borrow" }));
    ms(900);
    let until = Instant::now() + Duration::from_secs(8);
    while idle_ms() < 1000 {
        if Instant::now() > until {
            progress(json!({ "ev": "return" }));
            B.lock().unwrap().user_busy = true;
            return false;
        }
        ms(100);
    }
    let mut p = POINT::default();
    let _ = unsafe { GetCursorPos(&mut p) };
    let mut b = B.lock().unwrap();
    b.saved = Some((p, front_hwnd().0 as isize));
    b.fg_active = true;
    true
}

/// Gives the cursor and the front app back, if a step borrowed them.
pub fn give_back() {
    let (saved, win) = {
        let mut b = B.lock().unwrap();
        b.fg_active = false;
        (b.saved.take(), b.win.clone())
    };
    let (Some((p, fg)), Some(w)) = (saved, win) else { return };
    let fg = HWND(fg as *mut std::ffi::c_void);
    let _ = unsafe { SetCursorPos(p.x, p.y) };
    let pid = pid_of(fg);
    if !fg.0.is_null() && pid != w.pid && !is_roamies(pid, &proc_names(pid).1) {
        force_front(fg);
    }
    progress(json!({ "ev": "return" }));
}

// ---------- the run ----------

const FIELD_CTS: [i32; 3] = [CT_EDIT, CT_COMBOBOX, CT_DOCUMENT];
const DROP_CTS: [i32; 4] = [CT_COMBOBOX, CT_SPLITBUTTON, CT_EDIT, CT_BUTTON];

struct Ctx {
    w: Win,
    target: String,
    controls: Vec<(Node, Value)>,
    done: Vec<String>,
    i: usize,
    clicked_spot: bool,
    switched: bool,
}

fn s_str<'a>(s: &'a Value, k: &str) -> Option<&'a str> {
    s.get(k).and_then(Value::as_str)
}
fn label_of(info: &Value) -> String {
    info["label"].as_str().unwrap_or("").to_string()
}
fn prefix(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}
fn center(r: &Rect) -> (f64, f64) {
    (r.mid_x(), r.mid_y())
}
fn walk(w: &Win) -> Vec<(Node, Value)> {
    Tree::of_window(w).walk(w.r, 260)
}
fn page_lines(w: &Win, cap: usize) -> Vec<String> {
    page_text(Tree::of_window(w).scan_text(w.r).0, cap)
}

impl Ctx {
    fn n(&self) -> usize {
        self.i + 1
    }
    fn busy(&self) -> String {
        format!("Step {}: needs the real mouse, but the user was typing or using the mouse, so I didn't take over (their keys would have gone into {}). Nothing after it ran. Tell them, and offer to finish when they're free.", self.n(), self.w.app)
    }
    fn stop(&self, msg: String) -> ! {
        out(json!({ "ok": false, "error": msg, "done": self.done }))
    }
    fn tree(&self) -> Tree {
        Tree::of_window(&self.w)
    }
    fn el(&self, s: &Value) -> Option<(Node, Value)> {
        let id = s.get("id").and_then(Value::as_i64)?;
        (id >= 1 && id as usize <= self.controls.len()).then(|| self.controls[id as usize - 1].clone())
    }
    /// The real cursor, with the target app in front.
    fn real(&self) -> Result<(), String> {
        if !take_real() {
            return Err(self.busy());
        }
        if !bring_to_front(&self.w) {
            return Err(format!("Step {}: Couldn't bring {} to the front, so nothing was typed or clicked. Ask the user to click its window once, then try again.", self.n(), self.w.app));
        }
        Ok(())
    }
    fn real_click(&self, x: f64, y: f64) -> Result<(), String> {
        if let Some(name) = window_at(x, y).and_then(|u| hands_off(&u.exe)) {
            return Err(format!("Blocked for safety: that spot is on {name}, and I never click in it."));
        }
        self.real()?;
        click(x, y);
        Ok(())
    }
    /// Presses an element, or clicks its middle if it can't be pressed. Scrolls it into view first.
    fn activate(&self, e: &Node) -> Result<(), String> {
        scroll_into_view(&e.el);
        if press(&e.el) {
            return Ok(());
        }
        match live_rect(&e.el).or(e.r) {
            Some(f) => self.real_click(f.mid_x(), f.mid_y()),
            None => Ok(()),
        }
    }

    fn emit_step(&self, what: &str, e: Option<&Node>, at: Option<(f64, f64)>, label: &str, text: Option<&str>) {
        let mut d = json!({ "ev": "step", "i": self.i, "do": what, "label": prefix(label, 60) });
        if let Some(f) = e.and_then(|e| live_rect(&e.el).or(e.r)) {
            d["bounds"] = json!(f.ints());
        } else if let Some((x, y)) = at {
            d["bounds"] = json!([x as i64 - 6, y as i64 - 6, 12, 12]);
        }
        if let Some(t) = text {
            let secure = e.is_some_and(|e| e.password);
            d["text"] = if secure { "•".repeat(t.chars().count().min(24)) } else { prefix(t, 80) }.into();
            if secure {
                d["secure"] = true.into();
            }
        }
        progress(d);
    }

    /// Puts the cursor in a field, replaces its text and reads back what it shows. A native field takes the value
    /// directly; a web page's (or one that refuses) gets real keystrokes, and only once it really has focus.
    fn type_into(&self, e: &Node, text: &str) -> (bool, String) {
        if let Some(f) = live_rect(&e.el) {
            if !self.w.r.contains(f.mid_x(), f.mid_y().min(f.y + 12.0)) {
                scroll_into_view(&e.el);
                ms(300);
            }
        }
        focus(&e.el);
        ms(80);
        if !e.web && set_value(&e.el, text) && value(&e.el) == text {
            return (true, text.to_string());
        }
        if self.real().is_err() {
            return (false, value(&e.el));
        }
        if let Some(f) = live_rect(&e.el) {
            click(f.mid_x(), f.mid_y().min(f.y + 12.0));
        }
        ms(80);
        let here = is_focused(&e.el)
            || focused().is_some_and(|(f, _, _)| same(&f, &e.el) || live_rect(&f).zip(live_rect(&e.el)).is_some_and(|(a, b)| a.intersects(&b)));
        if !here {
            return (false, value(&e.el));
        }
        press_key("ctrl+a");
        type_text(text);
        ms(120);
        (true, value(&e.el))
    }

    /// The field a form label names: one whose own label or help text matches, else the field nearest the visible
    /// label text (to its right on the same row, or just below it).
    fn field_for(&self, name: &str, cts: &[i32]) -> Option<(Node, Value)> {
        let q = fold(name);
        if q.is_empty() {
            return None;
        }
        let fits = |c: &&(Node, Value)| if cts == FIELD_CTS { c.0.is_field() } else { cts.contains(&c.0.ct) };
        let off = |c: &(Node, Value)| c.1["offscreen"].as_bool() == Some(true);
        let cands: Vec<&(Node, Value)> = self.controls.iter().filter(fits).filter(|c| !off(c)).chain(self.controls.iter().filter(fits).filter(|c| off(c))).collect();
        let names = |c: &(Node, Value)| -> Vec<String> {
            let l = label_of(&c.1);
            let base = l.split(" (").next().unwrap_or(&l).to_string();
            [l.clone(), base, c.0.help.clone(), c.0.name.clone()].iter().map(|s| fold(s)).filter(|s| !s.is_empty()).collect()
        };
        if let Some(m) = cands.iter().find(|c| names(c).contains(&q)) {
            return Some((*m).clone());
        }
        if let Some(m) = cands.iter().find(|c| names(c).iter().any(|n| n.starts_with(&q) || (q.starts_with(n.as_str()) && n.chars().count() >= 4))) {
            return Some((*m).clone());
        }
        let tree = self.tree();
        let lab = tree.find_text(self.w.r, name, true, 8).into_iter().find(|m| whole_match(m.1["match"].as_str().unwrap_or(""), m.1["text"].as_str().unwrap_or(""), name))?;
        let l = lab.0.r?;
        // A field on the label's own row beats one below it; "below" means stacked under the label (same left edge).
        let (mut row, mut under): (Option<(f64, &(Node, Value))>, Option<(f64, &(Node, Value))>) = (None, None);
        for c in &cands {
            let Some(f) = c.0.r else { continue };
            if f.x >= l.mid_x() && f.y <= l.mid_y() && f.max_y() >= l.mid_y() && f.x - l.max_x() < 500.0 {
                let d = f.x - l.max_x();
                if row.is_none_or(|r| d < r.0) {
                    row = Some((d, c));
                }
            } else if f.y >= l.y + 2.0 && f.y - l.max_y() <= 60.0 && (f.x - l.x).abs() <= 60.0 {
                let d = f.y - l.max_y();
                if under.is_none_or(|u| d < u.0) {
                    under = Some((d, c));
                }
            }
        }
        row.or(under).map(|r| r.1.clone())
    }

    fn anchor(&self, tree: &Tree, near: &str) -> Option<Rect> {
        tree.find_text(self.w.r, near, true, 8).first().and_then(|m| m.0.r)
    }

    /// Visible text isn't there, but it may be further down the page: scroll the best match into view.
    fn scroll_to_text(&self, tree: &Tree, q: &str) -> bool {
        let Some((e, _)) = tree.find_text(self.w.r, q, true, 8).into_iter().find(|m| {
            whole_match(m.1["match"].as_str().unwrap_or(""), m.1["text"].as_str().unwrap_or(""), q) && !m.0.r.is_some_and(|f| self.w.r.intersects(&f))
        }) else {
            return false;
        };
        let mut cur = tree.index_of(&e.el);
        for _ in 0..4 {
            let Some(c) = cur else { break };
            if scroll_into_view(&tree.nodes[c].el) {
                ms(350);
                return true;
            }
            cur = tree.nodes[c].parent;
        }
        false
    }

    /// Trees for the window and the app's pop-ups (open dropdown lists and menus often live in their own windows).
    fn trees_with_popups(&self) -> Vec<Tree> {
        let mut out = vec![self.tree()];
        for h in popups_of(self.w.pid) {
            if h.0 as isize as i64 == self.w.id {
                continue;
            }
            if let Ok(root) = unsafe { uia().a.ElementFromHandle(h) } {
                out.push(Tree::read(&root, 4000));
            }
        }
        out
    }

    /// A menu item or list option with this text in the window or its pop-ups. Exact text first, then text that
    /// starts with it.
    fn find_option(&self, option: &str, excluding: &Node) -> Option<Node> {
        let q = fold(option);
        let roles = [CT_MENUITEM, CT_TEXT, CT_DATAITEM, CT_LISTITEM, CT_BUTTON, CT_RADIO, CT_HYPERLINK, CT_GROUP, CT_TREEITEM, CT_CUSTOM];
        let mut prefix: Option<Node> = None;
        for t in self.trees_with_popups() {
            let ex = t.index_of(&excluding.el);
            let mut skip = vec![false; t.nodes.len()];
            for (i, n) in t.nodes.iter().enumerate() {
                let up = n.parent.is_some_and(|p| skip[p]);
                // The menu bar isn't a dropdown's options, nor is the text the dropdown itself shows.
                skip[i] = up || n.ct == CT_MENUBAR;
                let inside_ex = ex.is_some_and(|x| {
                    let mut c = n.parent;
                    while let Some(p) = c {
                        if p == x {
                            return true;
                        }
                        c = t.nodes[p].parent;
                    }
                    false
                });
                if skip[i] || Some(i) == ex || (inside_ex && n.ct == CT_EDIT) || !roles.contains(&n.ct) {
                    continue;
                }
                for txt in [&n.name, &n.value, &n.help].map(|s| fold(s)).into_iter().filter(|s| !s.is_empty()) {
                    if txt == q {
                        return Some(n.clone());
                    }
                    if prefix.is_none() && txt.starts_with(&q) && txt.chars().count() <= q.chars().count() + 25 {
                        prefix = Some(n.clone());
                    }
                }
            }
        }
        prefix
    }

    /// The list a dropdown opened: the on-screen list, menu or table nearest below the dropdown (or above it).
    fn open_list(&self, near: Rect) -> Option<Node> {
        let mut best: Option<(f64, Node)> = None;
        for t in self.trees_with_popups() {
            for n in &t.nodes {
                if let (true, Some(f)) = ([CT_LIST, CT_MENU, CT_TABLE, CT_TREE].contains(&n.ct), n.r) {
                    if f.h > 20.0 && (f.intersects(&self.w.r) || t.nodes.first().is_some_and(|r| r.r == n.r)) {
                        let d = (f.y - near.max_y()).abs() + (f.x - near.x).abs() * 0.5;
                        if best.as_ref().is_none_or(|b| d < b.0) {
                            best = Some((d, n.clone()));
                        }
                    }
                }
            }
        }
        best.map(|b| b.1)
    }

    fn option_in(list: &Node, q: &str) -> Option<Node> {
        let t = Tree::read(&list.el, 3000);
        let mut prefix = None;
        for n in &t.nodes {
            let texts: Vec<String> = [&n.name, &n.value, &n.help].map(|s| fold(s)).into_iter().collect();
            if texts.iter().any(|t| t == q) {
                return Some(n.clone());
            }
            if prefix.is_none() && texts.iter().any(|t| t.starts_with(q) && t.chars().count() <= q.chars().count() + 25) {
                prefix = Some(n.clone());
            }
        }
        prefix
    }

    /// Scrolls an open list a screenful at a time, down to its end and then back up, until the option shows up.
    /// Works for long lists and virtualized ones, where rows out of view don't exist until scrolled to.
    fn scroll_list_for(&self, list: &Node, option: &str) -> Result<Option<Node>, String> {
        let q = fold(option);
        if let Some(o) = Self::option_in(list, &q) {
            return Ok(Some(o));
        }
        self.real()?;
        let deadline = Instant::now() + Duration::from_secs(20);
        for dir in [-1, 1] {
            let (mut last, mut same) = (String::new(), 0);
            while Instant::now() < deadline {
                if let Some(o) = Self::option_in(list, &q) {
                    return Ok(Some(o));
                }
                let t = Tree::read(&list.el, 3000);
                let rows: Vec<&Node> = t.nodes.first().map(|r| r.kids.iter().map(|&k| &t.nodes[k]).filter(|n| n.r.is_some_and(|f| f.intersects(&self.w.r) || list.r.is_some_and(|l| l.intersects(&f)))).collect()).unwrap_or_default();
                let Some(bx) = rows.iter().filter_map(|n| n.r).reduce(|a, b| a.union(&b)) else { return Ok(None) };
                if bx.h <= 4.0 {
                    return Ok(None);
                }
                let sig = rows.iter().map(|n| fold(&n.name)).collect::<Vec<_>>().join("|");
                if sig == last {
                    same += 1;
                    if same >= 2 {
                        break;
                    }
                } else {
                    same = 0;
                }
                last = sig;
                // Most of a screenful, so a virtualized list never skips rows between looks.
                let notches = ((bx.h * 0.7) / 40.0).max(1.0) as i32;
                wheel(bx.mid_x(), bx.mid_y(), dir * notches, 0);
                ms(70);
            }
        }
        Ok(Self::option_in(list, &q))
    }

    fn page_sig(&self) -> String {
        let cur = resolve(&self.target).unwrap_or_else(|| self.w.clone());
        format!("{}|{}#", cur.id, cur.title) + &walk(&cur).iter().map(|c| format!("{}|{}", c.1["role"].as_str().unwrap_or(""), label_of(&c.1))).collect::<Vec<_>>().join(";")
    }

    fn step(&mut self, s: &Value, what: &str, sig_before: &mut Option<String>, early: &[usize]) -> Result<(), String> {
        // Earlier steps may have moved the plan's window, or closed it and opened another (Ctrl+N over a dialog):
        // text was looked for inside the old window's rectangle.
        if what != "wait" {
            if let Some(cur) = current_window(&windows(), &self.w) {
                let moved = cur.id != self.w.id;
                B.lock().unwrap().win = Some(cur.clone());
                self.w = cur;
                if moved {
                    self.switched = true;
                    self.controls = walk(&self.w);
                }
            }
        }
        let n = self.n();
        let confirmed = s.get("confirmed").and_then(Value::as_bool) == Some(true);
        match what {
            "focus" | "click" | "set" | "press_button" => {
                let Some((e, info)) = self.el(s) else {
                    return Err(format!("Step {n}: no control with id {}. Call read_window again.", s.get("id").map_or("?".into(), |v| v.to_string())));
                };
                let label = label_of(&info);
                let pressing = what == "click" || what == "press_button";
                self.emit_step(what, Some(&e), None, &label, if what == "set" { s_str(s, "text") } else { None });
                if pressing && risky(&label) && !confirmed {
                    return Err(format!("Stopped before clicking \"{label}\": it could delete, pay, send or sign out, and the user hasn't said yes to that. Tell them, and let them click it themselves."));
                }
                let or = |d: &str| if label.is_empty() { d.to_string() } else { label.clone() };
                if what == "set" {
                    let text = s_str(s, "text").unwrap_or("");
                    if !self.type_into(&e, text).0 {
                        if B.lock().unwrap().user_busy {
                            return Err(self.busy());
                        }
                        return Err(format!("Step {n}: couldn't put the cursor in \"{}\", so nothing was typed.", or("that field")));
                    }
                    self.done.push(format!("set {}", or(&format!("field {}", info["id"]))));
                } else if what == "focus" {
                    focus(&e.el);
                    self.done.push(format!("focused {}", or("field")));
                } else if e.is_field() {
                    // "Pressing" a text box doesn't put the cursor in it: focus it so typing lands there.
                    focus(&e.el);
                    self.done.push(format!("clicked {}", or("field")));
                } else {
                    if !press(&e.el) {
                        if let Some(f) = live_rect(&e.el).or(e.r) {
                            self.real_click(f.mid_x(), f.mid_y())?;
                        }
                    }
                    self.done.push(format!("clicked {}", or("control")));
                }
                ms(150);
                if pressing {
                    self.controls = walk(&self.w);
                }
            }
            "click_text" => {
                let q = s_str(s, "text").unwrap_or("").to_string();
                let mut tree = self.tree();
                // A checkbox or radio by its label: press the control itself; clicking the words doesn't always tick it.
                let boxes: Vec<(Node, Value)> = self.controls.iter().filter(|c| [CT_CHECKBOX, CT_RADIO].contains(&c.0.ct) && fold(&label_of(&c.1)) == fold(&q) && c.1["offscreen"].as_bool() != Some(true)).cloned().collect();
                let mut hit: Option<(Node, String)> = None;
                let mut opened = String::new();
                if let Some(b) = boxes.iter().find(|b| b.0.web) {
                    hit = Some((b.0.clone(), label_of(&b.1)));
                } else if let Some(near) = s_str(s, "near").filter(|n| !n.is_empty()) {
                    // Many matches (a "Reply" under every post): the one that goes with the near text.
                    let Some(a) = self.anchor(&tree, near) else { return Err(format!("Step {n}: no text \"{near}\" on this page to find \"{q}\" near.")) };
                    let all: Vec<(Node, Value)> = tree.find_text(self.w.r, &q, true, 200).into_iter().filter(|m| click_match(m.1["match"].as_str().unwrap_or(""))).collect();
                    let best = all.first().map(|m| m.1["match"].clone());
                    let same: Vec<&(Node, Value)> = all.iter().filter(|m| Some(&m.1["match"]) == best.as_ref()).collect();
                    let rects: Vec<Rect> = same.iter().map(|m| m.0.r.unwrap_or_default()).collect();
                    if let Some(k) = nearest_after(&a, &rects) {
                        hit = Some((same[k].0.clone(), same[k].1["text"].as_str().unwrap_or(&q).to_string()));
                        if !rects[k].is_empty() && !self.w.r.contains(rects[k].mid_x(), rects[k].mid_y()) {
                            scroll_into_view(&same[k].0.el);
                            ms(300);
                        }
                    }
                } else {
                    let first = |t: &Tree| t.find_text(self.w.r, &q, false, 8).into_iter().find(|m| click_match(m.1["match"].as_str().unwrap_or(""))).map(|m| (m.0, m.1["text"].as_str().unwrap_or("").to_string()));
                    hit = first(&tree);
                    if let Some(b) = boxes.first() {
                        if hit.as_ref().is_none_or(|h| !h.0.web) {
                            hit = Some((b.0.clone(), label_of(&b.1)));
                        }
                    }
                    if hit.is_none() && !q.is_empty() && self.scroll_to_text(&tree, &q) {
                        tree = self.tree();
                        hit = first(&tree);
                    }
                    // Not in the plan's window: an earlier step may have opened a new one (Ctrl+N, a dialog). Look there.
                    if hit.is_none() && !q.is_empty() {
                        for nw in newer_windows(&windows(), &self.w) {
                            let t = Tree::of_window(&nw);
                            let Some(m) = t.find_text(nw.r, &q, false, 8).into_iter().find(|m| click_match(m.1["match"].as_str().unwrap_or(""))) else { continue };
                            hit = Some((m.0, m.1["text"].as_str().unwrap_or("").to_string()));
                            opened = if nw.title.is_empty() { " (in the window that opened)".into() } else { format!(" (in the window that opened: {})", nw.title) };
                            tree = t;
                            B.lock().unwrap().win = Some(nw.clone());
                            self.w = nw;
                            self.switched = true;
                            break;
                        }
                    }
                }
                let (Some((e, text)), false) = (hit, q.is_empty()) else {
                    return Err(format!("Step {n}: no visible text \"{q}\" in {}. Check the exact words with a screenshot or read_window.", self.w.app));
                };
                let text = if text.is_empty() { q.clone() } else { text };
                if risky(&text) && !confirmed {
                    return Err(format!("Stopped before clicking \"{text}\": it could delete, pay, send or sign out, and the user hasn't said yes to that. Tell them, and let them click it themselves."));
                }
                self.emit_step("click_text", Some(&e), None, &text, None);
                let pressed = match tree.index_of(&e.el) {
                    Some(k) => tree.press_text(k),
                    None => press(&e.el),
                };
                if !pressed {
                    let Some(f) = live_rect(&e.el).or(e.r) else { return Err(format!("Step {n}: couldn't click \"{q}\".")) };
                    self.real_click(f.mid_x(), f.mid_y())?;
                }
                self.done.push(format!("clicked \"{text}\"{opened}"));
                ms(150);
                self.controls = walk(&self.w);
            }
            "type" => {
                // Typing outside a text field fires page shortcuts, so the cursor must be in one (or just clicked there).
                let focus_el = focused().filter(|f| f.1 == self.w.pid);
                let editable = focus_el.as_ref().is_some_and(|f| editable_now(&f.0, f.2));
                if !may_type(editable, focus_el.as_ref().map(|f| f.2), self.clicked_spot) {
                    let opaque = focus_el.as_ref().is_none_or(|f| [CT_WINDOW, CT_PANE].contains(&f.2));
                    let tip = if opaque { "This app doesn't show its fields: click_at the field (from a screenshot) as the step right before typing." } else { "Use {do:'fill', field, text} to fill a field by its label." };
                    return Err(format!("Step {n}: the cursor isn't in a text field, so typing would go nowhere. {tip}"));
                }
                let text = s_str(s, "text").unwrap_or("");
                let password = focus_el.as_ref().is_some_and(|f| unsafe { f.0.CurrentIsPassword() }.is_ok_and(|b| b.as_bool()));
                let before = focus_el.as_ref().map(|f| value(&f.0));
                let into = focus_el.as_ref().map(|f| empty_node(f.0.clone(), password));
                self.emit_step("type", into.as_ref(), None, "", Some(text));
                type_text(text);
                // Typing the app dropped must stop here: a following Return would submit whatever the field held before.
                if let (Some(f), Some(before)) = (&focus_el, before) {
                    if !text.is_empty() && !password && editable {
                        ms(120);
                        let now = value(&f.0);
                        if now == before && !fold(&now).contains(&prefix(&fold(text), 40)) {
                            return Err(format!("Step {n}: typed, but the text field still shows \"{}\", so I stopped before the next step. Use {{do:'fill', field, text}} instead, or the open tool for a web address or a site's search results page.", prefix(&now, 80)));
                        }
                    }
                }
                self.done.push(if editable { "typed text".into() } else { "typed text (the app doesn't show its fields, so check the result on screen)".into() });
            }
            "fill" => {
                let text = s_str(s, "text").unwrap_or("");
                let mut s = s.clone();
                if s.get("id").is_none() {
                    if let Ok(k) = s_str(&s, "field").unwrap_or("").trim().parse::<i64>() {
                        s["id"] = k.into();
                    }
                }
                let name = s_str(&s, "field").unwrap_or("").to_string();
                if self.el(&s).is_none() && self.field_for(&name, &FIELD_CTS).is_none() {
                    ms(300);
                    self.controls = walk(&self.w);
                }
                // Same field under every post ("Write a reply…"): the one that goes with the near text.
                let mut near_field = None;
                if let Some(near) = s_str(&s, "near").filter(|n| !n.is_empty()) {
                    let tree = self.tree();
                    let Some(a) = self.anchor(&tree, near) else { return Err(format!("Step {n}: no text \"{near}\" on this page to find the field near.")) };
                    let q = fold(&name);
                    let fields: Vec<(Node, Value)> = tree.walk(self.w.r, 400).into_iter().filter(|c| c.0.is_field() && (q.is_empty() || [label_of(&c.1), c.0.help.clone()].iter().any(|t| fold(t).contains(&q)))).collect();
                    let rects: Vec<Rect> = fields.iter().map(|c| c.0.r.unwrap_or_default()).collect();
                    near_field = nearest_after(&a, &rects).map(|k| fields[k].clone());
                }
                let Some((e, info)) = near_field.or_else(|| self.el(&s)).or_else(|| self.field_for(&name, &FIELD_CTS)) else {
                    return Err(format!("Step {n}: no text field called \"{name}\" here. Check its label with read_window."));
                };
                let label = Some(label_of(&info)).filter(|l| !l.is_empty()).unwrap_or(name.clone());
                self.emit_step("fill", Some(&e), None, &label, Some(text));
                let r = self.type_into(&e, text);
                if B.lock().unwrap().user_busy {
                    return Err(self.busy());
                }
                if !r.0 {
                    return Err(format!("Step {n}: couldn't put the cursor in \"{label}\", so nothing was typed."));
                }
                let shows = r.1.trim().to_string();
                // Password fields show dots: the right number of them is a match.
                if e.password && shows.chars().count() != text.chars().count() && !shows.is_empty() {
                    return Err(format!("Step {n}: the password field shows {} characters, not {}. Fill it again.", shows.chars().count(), text.chars().count()));
                }
                let plain = !e.password && FIELD_CTS.contains(&e.ct);
                if plain && (!shows.is_empty() || !text.is_empty()) && !fold(&shows).contains(&prefix(&fold(text), 40)) {
                    if shows.is_empty() && !e.editable() && e.ct != CT_DOCUMENT {
                        return Err(format!("Step {n}: \"{label}\" can't be typed in; it opens a picker. Use click_text on it, then on the month arrows (e.g. 'Next Month') and the day (its label reads like 'Choose Monday, November 16th, 2026')."));
                    }
                    return Err(format!("Step {n}: typed into \"{label}\" but it shows \"{}\". If a cookie banner or pop-up covers the page, close it first (click_text its button), then fill again; otherwise check the field with read_window.", prefix(&shows, 80)));
                }
                self.done.push(format!("{label} = \"{}\"", if e.password { "••••".into() } else { prefix(&shows, 60) }));
                self.controls = walk(&self.w);
            }
            "choose" => {
                // A dropdown by label or id: open it, find the option (scrolling the list if needed) and pick it. Falls
                // back to typing the option and Return, which most dropdowns accept.
                let option = s_str(s, "option").or(s_str(s, "text")).unwrap_or("").to_string();
                let mut s = s.clone();
                if s.get("id").is_none() {
                    if let Ok(k) = s_str(&s, "field").unwrap_or("").trim().parse::<i64>() {
                        s["id"] = k.into();
                    }
                }
                let name = s_str(&s, "field").unwrap_or("").to_string();
                // No usable name and the page has exactly one dropdown in view: that's the one.
                let drops: Vec<&(Node, Value)> = self.controls.iter().filter(|c| c.0.ct == CT_COMBOBOX && c.1["offscreen"].as_bool() != Some(true) && c.0.web).collect();
                let only = (drops.len() == 1).then(|| drops[0].clone());
                let found = self.el(&s).or_else(|| self.field_for(&name, &DROP_CTS)).or(only);
                let (Some((e, info)), false) = (found, option.is_empty()) else {
                    return Err(format!("Step {n}: no dropdown called \"{name}\" here, or no option given. Check with read_window."));
                };
                let label = Some(label_of(&info)).filter(|l| !l.is_empty()).unwrap_or(name.clone());
                self.emit_step("choose", Some(&e), None, &label, Some(&option));
                if risky(&option) && !confirmed {
                    return Err(format!("Stopped before choosing \"{option}\": it could delete, pay, send or sign out, and the user hasn't said yes to that."));
                }
                if !expand(&e.el) {
                    self.activate(&e)?;
                }
                ms(350);
                let typeable = (e.ct == CT_COMBOBOX && e.editable()) || e.ct == CT_EDIT;
                let pick = |o: &Node| -> Result<(), String> {
                    scroll_into_view(&o.el);
                    if !(select(&o.el) || invoke(&o.el)) {
                        self.activate(o)?;
                    }
                    collapse(&e.el);
                    Ok(())
                };
                if let Some(o) = self.find_option(&option, &e) {
                    pick(&o)?;
                } else if typeable && self.type_into(&e, &option).0 && {
                    ms(500);
                    true
                } {
                    // Type-to-search: the suggestions appear once there's text in the box.
                    match self.find_option(&option, &e) {
                        Some(o) => pick(&o)?,
                        None => {
                            self.real()?;
                            press_key("return");
                        }
                    }
                } else if let Some(o) = match (!typeable).then(|| live_rect(&e.el).or(e.r)).flatten().and_then(|f| self.open_list(f)) {
                    Some(list) => self.scroll_list_for(&list, &option)?,
                    None => None,
                } {
                    pick(&o)?;
                } else if typeable {
                    if B.lock().unwrap().user_busy {
                        return Err(self.busy());
                    }
                    self.real()?;
                    press_key("return");
                } else {
                    // Typing only goes where focus is a field or the dropdown itself.
                    self.real()?;
                    let f = focused();
                    if f.as_ref().is_some_and(|f| same(&f.0, &e.el) || editable_now(&f.0, f.2)) || e.ct == CT_COMBOBOX {
                        type_text(&option);
                        ms(350);
                        press_key("return");
                    } else {
                        press_key("escape");
                        return Err(format!("Step {n}: opened \"{label}\" but no option \"{option}\" showed up. Take a screenshot to see the choices."));
                    }
                }
                ms(450);
                let shows = Some(value(&e.el)).filter(|v| !v.is_empty()).unwrap_or_default();
                let took = shows.is_empty() || fold(&shows).contains(&prefix(&fold(&option), 40));
                let showing = if shows.is_empty() { String::new() } else { format!(" (shows \"{}\")", prefix(&shows, 60)) };
                self.done.push(if took { format!("chose \"{option}\" in {label}{showing}") } else { format!("tried to choose \"{option}\" in {label}, but it still shows \"{}\"", prefix(&shows, 60)) });
                self.controls = walk(&self.w);
            }
            "scroll" => {
                let dir = s_str(s, "direction").unwrap_or("down").to_lowercase();
                let count = s.get("amount").and_then(Value::as_i64).unwrap_or(5).clamp(1, 30);
                let inner = self.w.r.inset(4.0, 4.0);
                let in_win = |f: Rect| inner.contains(f.mid_x(), f.mid_y()).then(|| center(&f));
                let mut p = center(&self.w.r);
                if let Some(c) = self.el(s).and_then(|(e, _)| live_rect(&e.el).or(e.r)).and_then(in_win) {
                    p = c;
                } else if let Some(q) = s_str(s, "text") {
                    if let Some(c) = self.tree().find_text(self.w.r, q, false, 8).iter().filter_map(|m| m.0.r.and_then(in_win)).next() {
                        p = c;
                    }
                }
                let (dy, dx) = match dir.as_str() {
                    "up" => (1, 0),
                    "left" => (0, -1),
                    "right" => (0, 1),
                    _ => (-1, 0),
                };
                let to_end = s.get("to_end").and_then(Value::as_bool) == Some(true);
                self.emit_step("scroll", None, Some(p), if to_end { format!("{dir} to the end") } else { dir.clone() }.as_str(), None);
                self.real()?;
                if to_end && (dir == "up" || dir == "down") {
                    // To the very end (or top): keep scrolling, giving a feed time to load more, until nothing changes.
                    let deadline = Instant::now() + Duration::from_secs(25);
                    let (mut last, mut same, mut rounds) = (String::new(), 0, 0);
                    while Instant::now() < deadline && same < 2 {
                        for _ in 0..4 {
                            wheel(p.0, p.1, dy * 15, 0);
                            ms(50);
                        }
                        ms(900);
                        let sig = page_lines(&self.w, 4000).join("|");
                        if sig == last {
                            same += 1;
                        } else {
                            same = 0;
                            rounds += 1;
                        }
                        last = sig;
                    }
                    let more = if rounds > 2 { format!(" (more loaded {} times)", rounds - 1) } else { String::new() };
                    self.done.push(format!("scrolled to the {}{more}", if dir == "up" { "top" } else { "end" }));
                } else {
                    for _ in 0..count {
                        wheel(p.0, p.1, dy, dx);
                        ms(40);
                    }
                    ms(250);
                    self.done.push(format!("scrolled {dir}"));
                }
                self.controls = walk(&self.w);
            }
            "key" => {
                let key = s_str(s, "key").unwrap_or("");
                self.emit_step("key", None, None, key, None);
                if !press_key(key) {
                    return Err(format!("Step {n}: unknown key {key}"));
                }
                self.done.push(format!("pressed {key}"));
            }
            "click_at" => {
                let (Some(x), Some(y)) = (s.get("x").and_then(Value::as_f64), s.get("y").and_then(Value::as_f64)) else {
                    return Err(format!("Step {n}: click_at needs x and y"));
                };
                let global = s.get("global").and_then(Value::as_bool) == Some(true);
                let (gx, gy) = if global { (x, y) } else { (self.w.r.x + x, self.w.r.y + y) };
                self.emit_step("click_at", None, Some((gx, gy)), "", None);
                click(gx, gy);
                self.done.push(format!("clicked at {},{}", x as i64, y as i64));
                self.clicked_spot = true;
            }
            "wait" => {
                let secs = s.get("seconds").and_then(Value::as_f64).unwrap_or(0.5).clamp(0.0, 5.0);
                let end = Instant::now() + Duration::from_secs_f64(secs);
                match sig_before.take().filter(|_| early.contains(&self.i)) {
                    // A wait right after a click or key ends once the page has changed and held still.
                    Some(before) => {
                        let (mut last, mut same) = (String::new(), 0);
                        while Instant::now() < end {
                            let now = self.page_sig();
                            if now != before {
                                same = if now == last { same + 1 } else { 0 };
                                if same >= 2 {
                                    break;
                                }
                            }
                            last = now;
                            ms(150);
                        }
                    }
                    None => sleep(end.saturating_duration_since(Instant::now())),
                }
                self.done.push("waited".into());
            }
            other => return Err(format!("Step {n}: unknown action {other}")),
        }
        Ok(())
    }
}

fn empty_node(el: windows::Win32::UI::Accessibility::IUIAutomationElement, password: bool) -> Node {
    Node {
        el,
        ct: 0,
        name: String::new(),
        help: String::new(),
        value: String::new(),
        r: None,
        enabled: true,
        focused: true,
        focusable: true,
        password,
        val_avail: false,
        val_ro: false,
        invoke: false,
        toggle: false,
        select: false,
        expand: false,
        text_pat: false,
        parent: None,
        kids: vec![],
        web: false,
        web_root: false,
        text_area: false,
    }
}

/// After a click or key press the page may be loading: re-read until the controls stop changing (max ~1 s).
fn settle(w: &Win) -> Vec<(Node, Value)> {
    let sig = |c: &[(Node, Value)]| c.iter().map(|c| format!("{}|{}", c.1["role"], c.1["label"])).collect::<Vec<_>>();
    let mut cur = walk(w);
    for _ in 0..4 {
        ms(200);
        let prev = sig(&cur);
        cur = walk(w);
        if sig(&cur) == prev {
            break;
        }
    }
    cur
}

pub fn act(arg: &str) -> ! {
    let plan: Value = serde_json::from_str(arg).unwrap_or(Value::Null);
    let Some(steps) = plan.get("steps").and_then(Value::as_array).cloned() else { crate::fail("bad plan") };
    let target = plan.get("window").and_then(Value::as_i64).map(|id| format!("window:{id}")).or_else(|| s_str(&plan, "app").map(|a| format!("app:{a}"))).unwrap_or("front".into());
    let Some(w) = resolve(&target) else { crate::fail("No matching window is open.") };
    if let Some(name) = hands_off(&w.exe) {
        crate::fail(&format!("Blocked for safety: I can point at things in {name}, but I never type or click in it. Show the user where to click instead."));
    }
    if elevated_above_me(w.pid) {
        crate::fail(&format!("{} is running as administrator, so Windows doesn't let Roamies type or click in it. Show the user where to click instead.", w.app));
    }
    let bg = std::env::var("ROAMIES_BG").as_deref() != Ok("0");
    {
        let mut b = B.lock().unwrap();
        b.bg = bg;
        b.win = Some(w.clone());
    }
    // Not in background mode: bring the app forward first so keystrokes land there, not in Roamies.
    if !bg && !bring_to_front(&w) {
        out(json!({ "ok": false, "error": format!("Couldn't bring {} to the front, so nothing was typed or clicked. Ask the user to click its window once, then try again.", w.app), "done": [] }));
    }
    let mut c = Ctx { controls: walk(&w), w, target, done: vec![], i: 0, clicked_spot: false, switched: false };
    progress(json!({ "ev": "start", "window": win_info(&c.w), "steps": steps.len() }));
    let dos: Vec<&str> = steps.iter().map(|s| s_str(s, "do").unwrap_or("")).collect();
    let early = react_waits(&dos);
    let mut sig_before: Option<String> = None;
    for (i, s) in steps.iter().enumerate() {
        c.i = i;
        let what = dos[i];
        {
            let mut b = B.lock().unwrap();
            b.used_real = false;
            b.user_busy = false;
        }
        if early.contains(&(i + 1)) {
            sig_before = Some(c.page_sig());
        }
        // A click at a spot only vouches for the typing that follows it; any step that moves focus elsewhere ends it.
        if !["click_at", "key", "wait", "type"].contains(&what) {
            c.clicked_spot = false;
        }
        // An earlier step may have switched apps (a click, alt+tab); check before every action.
        if what != "wait" {
            if let Some(name) = front_hands_off() {
                c.stop(format!("Blocked for safety: {name} came to the front, and I never type or click in it."));
            }
        }
        if what == "click_at" {
            if let (Some(x), Some(y)) = (s.get("x").and_then(Value::as_f64), s.get("y").and_then(Value::as_f64)) {
                let global = s.get("global").and_then(Value::as_bool) == Some(true);
                let (gx, gy) = if global { (x, y) } else { (c.w.r.x + x, c.w.r.y + y) };
                if let Some(name) = window_at(gx, gy).and_then(|u| hands_off(&u.exe)) {
                    c.stop(format!("Blocked for safety: that spot is on {name}, and I never click in it."));
                }
            }
        }
        // Raw keys and typing land in the front app: take the cursor and keyboard, and if another app is in front
        // stop instead of typing into it.
        if ["key", "type", "click_at"].contains(&what) {
            if !take_real() {
                let msg = c.busy();
                c.stop(msg);
            }
            if front_pid() != Some(c.w.pid) && !bring_to_front(&c.w) {
                let other = front_pid().map_or("another app".to_string(), |p| proc_names(p).0);
                c.stop(format!("Step {}: {other} is in front instead of {}, so I stopped before typing into the wrong app.", i + 1, c.w.app));
            }
        }
        if let Err(e) = c.step(s, what, &mut sig_before, &early) {
            c.stop(e);
        }
        if B.lock().unwrap().used_real {
            if let Some(last) = c.done.last_mut() {
                last.push_str(" (needed the real cursor)");
            }
        }
    }
    // A click or key may have changed the page: hand back what's there now, so no read_window round trip is needed.
    let changing = dos.iter().any(|d| ["click", "click_text", "press_button", "key", "click_at", "fill", "choose", "scroll", "set"].contains(d));
    if changing {
        // After a switch to a window the plan opened, show that one while it is still open.
        let w2 = c.switched.then(|| windows().into_iter().find(|x| x.id == c.w.id)).flatten().or_else(|| resolve(&c.target)).unwrap_or_else(|| c.w.clone());
        let now: Vec<Value> = settle(&w2).into_iter().map(|c| c.1).collect();
        out(json!({ "ok": true, "done": c.done, "window": win_info(&w2), "controls": now, "texts": page_lines(&w2, 2000) }));
    }
    out(json!({ "ok": true, "done": c.done, "window": win_info(&c.w) }))
}
