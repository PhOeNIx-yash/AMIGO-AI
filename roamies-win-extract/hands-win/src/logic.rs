// The parts of the hands helper that don't touch Windows: which window to use, how text matches, what's risky to
// click, key names, fitting a guessed box to the pixels. Ported from hands/main.swift; checked by `cargo test` on
// any system and by `roamies-hands --self-test`.

#[derive(Clone, Copy, Debug, PartialEq, Default)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

impl Rect {
    pub const fn new(x: f64, y: f64, w: f64, h: f64) -> Rect {
        Rect { x, y, w, h }
    }
    pub fn max_x(&self) -> f64 {
        self.x + self.w
    }
    pub fn max_y(&self) -> f64 {
        self.y + self.h
    }
    pub fn mid_x(&self) -> f64 {
        self.x + self.w / 2.0
    }
    pub fn mid_y(&self) -> f64 {
        self.y + self.h / 2.0
    }
    pub fn area(&self) -> f64 {
        self.w * self.h
    }
    pub fn is_empty(&self) -> bool {
        !(self.w > 0.0 && self.h > 0.0)
    }
    pub fn intersection(&self, o: &Rect) -> Option<Rect> {
        let (x0, y0) = (self.x.max(o.x), self.y.max(o.y));
        let (x1, y1) = (self.max_x().min(o.max_x()), self.max_y().min(o.max_y()));
        (x1 > x0 && y1 > y0).then(|| Rect::new(x0, y0, x1 - x0, y1 - y0))
    }
    pub fn intersects(&self, o: &Rect) -> bool {
        self.intersection(o).is_some()
    }
    pub fn union(&self, o: &Rect) -> Rect {
        let (x0, y0) = (self.x.min(o.x), self.y.min(o.y));
        Rect::new(x0, y0, self.max_x().max(o.max_x()) - x0, self.max_y().max(o.max_y()) - y0)
    }
    pub fn contains(&self, px: f64, py: f64) -> bool {
        px >= self.x && px < self.max_x() && py >= self.y && py < self.max_y()
    }
    pub fn inset(&self, dx: f64, dy: f64) -> Rect {
        Rect::new(self.x + dx, self.y + dy, self.w - 2.0 * dx, self.h - 2.0 * dy)
    }
    pub fn ints(&self) -> [i64; 4] {
        [self.x as i64, self.y as i64, self.w as i64, self.h as i64]
    }
    pub fn tenths(&self) -> [f64; 4] {
        [self.x, self.y, self.w, self.h].map(|v| (v * 10.0).round() / 10.0)
    }
}

/// One app window. `id` is its HWND, `exe` the program's file name without ".exe".
#[derive(Clone, Debug, Default)]
pub struct Win {
    pub id: i64,
    pub pid: u32,
    pub app: String,
    pub exe: String,
    pub title: String,
    pub r: Rect,
}

/// An app's window to use, from its windows front to back: the frontmost one, passing over small untitled ones
/// floating above a much bigger window (CapCut's EditPilot button).
pub fn main_window(wins: &[Win]) -> Option<&Win> {
    let big = wins.iter().map(|w| w.r.area()).fold(0.0, f64::max);
    wins.iter().find(|w| !w.title.is_empty() || w.r.area() >= big * 0.05).or(wins.first())
}

// The same app's windows that opened over the plan's window (a new document, a dialog), front first, at most 3;
// all of that app's windows when the plan's window has closed. Never the older windows behind it.
pub fn newer_windows(all: &[Win], pinned: &Win) -> Vec<Win> {
    let own: Vec<&Win> = all.iter().filter(|w| w.pid == pinned.pid).collect();
    let end = own.iter().position(|w| w.id == pinned.id).unwrap_or(own.len());
    own[..end].iter().take(3).map(|w| (*w).clone()).collect()
}

// The plan's window as it is now (it may have moved or been resized); once it has closed, that app's front window.
// None: the app has no window open.
pub fn current_window(all: &[Win], pinned: &Win) -> Option<Win> {
    all.iter().find(|w| w.id == pinned.id).cloned().or_else(|| {
        let own: Vec<Win> = all.iter().filter(|w| w.pid == pinned.pid).cloned().collect();
        main_window(&own).cloned()
    })
}

const BROWSER_EXES: &[&str] = &["chrome", "msedge", "brave", "firefox", "opera", "vivaldi", "chromium", "arc", "zen", "librewolf", "waterfox", "floorp", "thorium"];

pub fn is_browser(exe: &str) -> bool {
    BROWSER_EXES.contains(&exe.to_lowercase().as_str())
}

/// Fuzzy app match: "browser" finds a known browser; otherwise a case-insensitive match on the app's name or its
/// program's file name ("notepad", "msedge").
pub fn app_matches(app: &str, exe: &str, want: &str) -> bool {
    let w = want.trim().to_lowercase();
    let w = w.strip_suffix(".exe").unwrap_or(&w);
    if w.is_empty() {
        return false;
    }
    if ["browser", "web browser", "my browser"].contains(&w) {
        return is_browser(exe);
    }
    [app.to_lowercase(), exe.to_lowercase()].iter().any(|a| !a.is_empty() && (a == w || a.contains(w) || w.contains(a.as_str())))
}

/// Apps Sidekick may point at but never type or click in (by program file name): typing into a terminal would run
/// commands past every check, and the others hold passwords or security settings.
pub fn hands_off(exe: &str) -> Option<&'static str> {
    Some(match exe.to_lowercase().as_str() {
        "cmd" => "Command Prompt",
        "powershell" | "powershell_ise" | "pwsh" => "PowerShell",
        "windowsterminal" | "openconsole" => "Windows Terminal",
        "conhost" => "Console",
        "mintty" => "mintty",
        "wsl" | "wslhost" | "wslg" => "WSL",
        "alacritty" => "Alacritty",
        "wezterm-gui" | "wezterm" => "WezTerm",
        "hyper" => "Hyper",
        "tabby" => "Tabby",
        "warp" => "Warp",
        "putty" => "PuTTY",
        "regedit" => "Registry Editor",
        "mmc" => "Management Console",
        "taskmgr" => "Task Manager",
        "systemsettings" => "Settings",
        "consent" => "User Account Control",
        "credentialuibroker" => "Windows Security",
        "1password" => "1Password",
        "bitwarden" => "Bitwarden",
        "keepass" => "KeePass",
        "keepassxc" => "KeePassXC",
        "devenv" => "Visual Studio",
        _ => return None,
    })
}

// ---------- UI Automation control types, named like the macOS helper names roles ----------

pub const CT_BUTTON: i32 = 50000;
pub const CT_CHECKBOX: i32 = 50002;
pub const CT_COMBOBOX: i32 = 50003;
pub const CT_EDIT: i32 = 50004;
pub const CT_HYPERLINK: i32 = 50005;
pub const CT_IMAGE: i32 = 50006;
pub const CT_LISTITEM: i32 = 50007;
pub const CT_LIST: i32 = 50008;
pub const CT_MENU: i32 = 50009;
pub const CT_MENUBAR: i32 = 50010;
pub const CT_MENUITEM: i32 = 50011;
pub const CT_RADIO: i32 = 50013;
pub const CT_SLIDER: i32 = 50015;
pub const CT_SPINNER: i32 = 50016;
pub const CT_TABITEM: i32 = 50019;
pub const CT_TEXT: i32 = 50020;
pub const CT_TREE: i32 = 50023;
pub const CT_TREEITEM: i32 = 50024;
pub const CT_CUSTOM: i32 = 50025;
pub const CT_GROUP: i32 = 50026;
pub const CT_DATAITEM: i32 = 50029;
pub const CT_DOCUMENT: i32 = 50030;
pub const CT_SPLITBUTTON: i32 = 50031;
pub const CT_WINDOW: i32 = 50032;
pub const CT_PANE: i32 = 50033;
pub const CT_TABLE: i32 = 50036;

/// The role word for a control, as the macOS helper words it ("button", "text field"…). None: not a control.
/// `editable`: a combo box you can type in, or a document you can edit (a text area, not a web page).
pub fn role_word(ct: i32, editable: bool, password: bool) -> Option<&'static str> {
    Some(match ct {
        CT_BUTTON => "button",
        CT_CHECKBOX => "checkbox",
        CT_COMBOBOX if editable => "combo box",
        CT_COMBOBOX => "dropdown",
        CT_EDIT if password => "password field",
        CT_EDIT => "text field",
        CT_DOCUMENT if editable => "text area",
        CT_HYPERLINK => "link",
        CT_RADIO => "radio",
        CT_SLIDER => "slider",
        CT_SPINNER => "stepper",
        CT_TABITEM => "tab",
        CT_SPLITBUTTON => "menu button",
        CT_MENUITEM => "menu item",
        CT_LISTITEM => "list item",
        CT_TREEITEM => "tree item",
        CT_DATAITEM => "item",
        _ => return None,
    })
}

/// Any element's role in find-text results: its control word, else its kind ("text", "image", "group").
pub fn kind_word(ct: i32) -> &'static str {
    match ct {
        CT_TEXT => "statictext",
        CT_IMAGE => "image",
        CT_GROUP => "group",
        CT_LIST => "list",
        CT_MENU => "menu",
        CT_DOCUMENT => "webarea",
        CT_WINDOW => "window",
        CT_PANE => "pane",
        CT_CUSTOM => "custom",
        CT_TABLE => "table",
        _ => "element",
    }
}

/// Whether a type step may go ahead. Typing outside a text field fires page shortcuts, so normally the focused thing
/// must be editable. Apps that don't show their fields to UI Automation report only the window or a pane as focused:
/// there, right after a click at a spot, the typing goes into what was clicked.
pub fn may_type(editable: bool, focus_ct: Option<i32>, clicked_spot: bool) -> bool {
    editable || (clicked_spot && focus_ct.is_none_or(|c| c == CT_WINDOW || c == CT_PANE))
}

// ---------- text ----------

pub fn fold(s: &str) -> String {
    s.to_lowercase().replace(['*', ':'], "").split_whitespace().collect::<Vec<_>>().join(" ")
}

fn is_word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}
const JOINERS: &[char] = &['.', '/', ':', '@', '\\'];

/// How text (lowercased, trimmed) holds the query: 0 the whole text, 1 a whole word or phrase in it, 2 only inside
/// other words. Joined by . : _ / @ or \ isn't a word of its own: "speed" in "MTLSVideoP:speed.mp4" is part of a name.
pub fn match_score(low: &str, q: &str) -> usize {
    if low == q {
        return 0;
    }
    let (l, qc): (Vec<char>, Vec<char>) = (low.chars().collect(), q.chars().collect());
    let n = qc.len();
    if n == 0 || n > l.len() {
        return 2;
    }
    for i in 0..=l.len() - n {
        if l[i..i + n] != qc[..] {
            continue;
        }
        let before = i == 0 || !(is_word(l[i - 1]) || JOINERS.contains(&l[i - 1]));
        let j = i + n;
        let after = j == l.len() || (!is_word(l[j]) && !(JOINERS.contains(&l[j]) && j + 1 < l.len() && l[j + 1].is_alphanumeric()));
        if before && after {
            return 1;
        }
    }
    2
}

pub const MATCH_NAMES: [&str; 3] = ["exact", "word", "partial"];

/// What click_text may click: the words as a whole label or a whole word in one, never inside other words.
pub fn click_match(m: &str) -> bool {
    m != "partial"
}

/// A whole label or heading, not the same words buried in a paragraph.
pub fn whole_match(m: &str, text: &str, q: &str) -> bool {
    m != "partial" || text.chars().count() <= 40.max(q.chars().count() * 3)
}

// Clicks that delete, pay, send or sign out run only when the sidecar has asked the user ("confirmed").
// Kept in sync with RISKY_CLICK in sidecar/safety.mjs (its regex, written out).
fn risky_words() -> Vec<String> {
    let mut v: Vec<String> = [
        "delete", "remove", "erase", "destroy", "wipe", "reset", "deactivate", "unsubscribe", "pay", "purchase", "buy", "checkout", "check out",
        "send", "transfer", "withdraw", "uninstall", "empty trash", "discard", "sign out", "log out", "logout", "format", "revoke", "publish",
        "eliminar", "borrar", "pagar", "comprar", "enviar", "cerrar sesion", "cerrar sesión", "supprimer", "effacer", "payer", "acheter", "envoyer",
        "se deconnecter", "se déconnecter", "deconnexion", "déconnexion", "loschen", "löschen", "entfernen", "bezahlen", "kaufen", "senden",
        "abmelden", "excluir", "apagar", "terminar sessao", "terminar sessão", "elimina", "cancella", "paga", "acquista", "invia", "esci",
        "verwijderen", "betalen", "kopen", "verzenden", "uitloggen", "afmelden", "padam", "hapus", "bayar", "beli", "hantar", "kirim",
        "log keluar", "nyahlanggan",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    for who in ["", "my ", "your "] {
        v.push(format!("close {who}account"));
    }
    for who in ["", "my ", "your ", "the "] {
        for what in ["subscription", "plan", "membership", "account", "order"] {
            v.push(format!("cancel {who}{what}"));
        }
    }
    for who in ["", "the ", "your "] {
        v.push(format!("place {who}order"));
    }
    for what in ["payment", "order", "purchase", "transfer"] {
        v.push(format!("confirm {what}"));
    }
    v
}
const RISKY_CJK: &[&str] = &[
    "删除", "刪除", "移除", "清空", "支付", "付款", "购买", "購買", "发送", "發送", "退出登录", "登出", "注销", "削除", "消去", "支払", "購入", "送信", "ログアウト", "退会",
    "삭제", "결제", "구매", "전송", "보내기", "로그아웃",
];

pub fn risky(s: &str) -> bool {
    if RISKY_CJK.iter().any(|w| s.contains(w)) {
        return true;
    }
    let l: Vec<char> = s.to_lowercase().chars().collect();
    risky_words().iter().any(|w| {
        let p: Vec<char> = w.chars().collect();
        (0..(l.len() + 1).saturating_sub(p.len())).any(|i| {
            l[i..i + p.len()] == p[..] && (i == 0 || !l[i - 1].is_alphabetic()) && l.get(i + p.len()).is_none_or(|c| !c.is_alphabetic())
        })
    })
}

/// The waits (by index) that come right after a click or key, so they can end once the page has reacted.
pub fn react_waits(dos: &[&str]) -> Vec<usize> {
    let reacts = ["click", "click_text", "press_button", "key", "click_at", "choose"];
    (1..dos.len()).filter(|&i| dos[i] == "wait" && reacts.contains(&dos[i - 1])).collect()
}

/// Of many matches (every post's "Reply" button), the one that belongs with the anchor text: the first one at or
/// below the anchor's top, nearest first; if none is below, the nearest one above.
pub fn nearest_after(anchor: &Rect, cands: &[Rect]) -> Option<usize> {
    let cost = |r: &Rect| (r.y - anchor.y) + (r.x - anchor.x).abs() * 0.2;
    let below = (0..cands.len()).filter(|&i| cands[i].y >= anchor.y - 4.0).min_by(|&a, &b| cost(&cands[a]).total_cmp(&cost(&cands[b])));
    below.or_else(|| (0..cands.len()).min_by(|&a, &b| (anchor.y - cands[a].max_y()).total_cmp(&(anchor.y - cands[b].max_y()))))
}

/// On-screen text lines (y, x, text), top to bottom, capped.
pub fn page_text(mut found: Vec<(f64, f64, String)>, cap: usize) -> Vec<String> {
    found.sort_by(|a, b| if (a.0 - b.0).abs() > 4.0 { a.0.total_cmp(&b.0) } else { a.1.total_cmp(&b.1) });
    let (mut out, mut total) = (vec![], 0);
    for (k, (_, _, t)) in found.iter().enumerate() {
        let n = t.chars().count();
        if total + n > cap {
            out.push(format!("…({} more lines further down this screen; read_window shows them all)", found.len() - k));
            break;
        }
        out.push(t.clone());
        total += n + 1;
    }
    out
}

/// Text scrolled out of view, top to bottom. When it's long, the start and the end are kept, with a note between.
pub fn offscreen_text(mut found: Vec<(f64, String)>, cap: usize) -> Vec<String> {
    found.sort_by(|a, b| a.0.total_cmp(&b.0));
    let lines: Vec<String> = found.into_iter().map(|f| f.1).collect();
    if lines.iter().map(|l| l.chars().count() + 1).sum::<usize>() <= cap {
        return lines;
    }
    let (mut head, mut tail, mut h, mut t) = (vec![], vec![], 0, 0);
    for l in &lines {
        if h + l.chars().count() > cap / 2 {
            break;
        }
        head.push(l.clone());
        h += l.chars().count() + 1;
    }
    for l in lines.iter().rev() {
        if t + l.chars().count() > cap / 2 {
            break;
        }
        tail.insert(0, l.clone());
        t += l.chars().count() + 1;
    }
    let mid = lines.len() - head.len() - tail.len();
    head.push(format!("…({mid} more lines in between; point_at or click_text with their words scrolls there)"));
    head.extend(tail);
    head
}

// ---------- keys ----------

pub const MOD_CTRL: u8 = 1;
pub const MOD_SHIFT: u8 = 2;
pub const MOD_ALT: u8 = 4;
pub const MOD_WIN: u8 = 8;

fn key_alias(k: &str) -> Option<&'static str> {
    Some(match k {
        "*" | "multiply" | "times" => "shift+8",
        "+" | "plus" | "add" => "shift+=",
        "minus" | "subtract" => "-",
        "divide" | "slash" => "/",
        "equals" | "equal" => "=",
        "period" | "dot" | "decimal" => ".",
        "comma" => ",",
        "%" | "percent" => "shift+5",
        "(" => "shift+9",
        ")" => "shift+0",
        "!" => "shift+1",
        "@" => "shift+2",
        "#" => "shift+3",
        "$" => "shift+4",
        "^" => "shift+6",
        "&" => "shift+7",
        "_" => "shift+-",
        "?" => "shift+/",
        ":" => "shift+;",
        "\"" => "shift+'",
        "<" => "shift+,",
        ">" => "shift+.",
        "{" => "shift+[",
        "}" => "shift+]",
        "|" => "shift+\\",
        "~" => "shift+`",
        "page_up" | "pgup" => "pageup",
        "page_down" | "pgdn" => "pagedown",
        "arrowleft" => "left",
        "arrowright" => "right",
        "arrowup" => "up",
        "arrowdown" => "down",
        "spacebar" => "space",
        "back" => "backspace",
        "del" | "forwarddelete" => "delete",
        "ins" => "insert",
        _ => return None,
    })
}

/// A key's Windows virtual-key code. "delete" is the Delete key, as on Windows keyboards.
pub fn vk(name: &str) -> Option<u16> {
    let b = name.as_bytes();
    if b.len() == 1 && (b[0].is_ascii_lowercase() || b[0].is_ascii_digit()) {
        return Some(b[0].to_ascii_uppercase() as u16);
    }
    if let Some(n) = name.strip_prefix('f').and_then(|n| n.parse::<u16>().ok()).filter(|n| (1..=24).contains(n)) {
        return Some(0x6F + n);
    }
    Some(match name {
        "return" | "enter" => 0x0D,
        "tab" => 0x09,
        "space" => 0x20,
        "backspace" => 0x08,
        "delete" => 0x2E,
        "insert" => 0x2D,
        "escape" | "esc" => 0x1B,
        "left" => 0x25,
        "up" => 0x26,
        "right" => 0x27,
        "down" => 0x28,
        "home" => 0x24,
        "end" => 0x23,
        "pageup" => 0x21,
        "pagedown" => 0x22,
        "=" => 0xBB,
        "-" => 0xBD,
        "/" => 0xBF,
        "." => 0xBE,
        "," => 0xBC,
        ";" => 0xBA,
        "'" => 0xDE,
        "[" => 0xDB,
        "]" => 0xDD,
        "\\" => 0xDC,
        "`" => 0xC0,
        _ => return None,
    })
}

/// Keys that need the "extended" flag when sent (arrows and the block above them).
pub fn is_extended(vk: u16) -> bool {
    (0x21..=0x28).contains(&vk) || vk == 0x2D || vk == 0x2E
}

/// "cmd+a", "shift+tab", "return", "*": the modifiers (cmd is Ctrl on Windows) and the key's code.
pub fn parse_combo(combo: &str) -> Option<(u8, u16)> {
    let mut c = combo.trim().to_lowercase().replace(' ', "");
    if let Some(a) = key_alias(&c) {
        c = a.to_string();
    }
    // "+" alone, or "cmd++": the last + is the key itself.
    let mut parts: Vec<String> = c.split('+').filter(|p| !p.is_empty()).map(String::from).collect();
    if c.ends_with("++") || c == "+" {
        parts.extend(["shift".into(), "=".into()]);
    }
    let parts: Vec<String> = parts.into_iter().flat_map(|p| key_alias(&p).map_or(vec![p.clone()], |a| a.split('+').map(String::from).collect())).collect();
    let (mut mods, mut key) = (0u8, None);
    for p in &parts {
        match p.as_str() {
            "cmd" | "command" | "ctrl" | "control" => mods |= MOD_CTRL,
            "opt" | "option" | "alt" => mods |= MOD_ALT,
            "shift" => mods |= MOD_SHIFT,
            "win" | "windows" | "meta" | "super" => mods |= MOD_WIN,
            other => key = vk(other),
        }
    }
    key.map(|k| (mods, k))
}

// ---------- precise places: a guess fitted to the pixels ----------

/// Grows `m` by `r` pixels (square), so nearby ink counts as one piece: a word's letters, an icon's strokes.
fn dilate(m: &[bool], w: usize, h: usize, r: usize) -> Vec<bool> {
    if r == 0 {
        return m.to_vec();
    }
    let mut a = vec![false; w * h];
    for y in 0..h {
        let mut d = r + 1;
        for x in 0..w {
            d = if m[y * w + x] { 0 } else { d + 1 };
            if d <= r {
                a[y * w + x] = true;
            }
        }
        d = r + 1;
        for x in (0..w).rev() {
            d = if m[y * w + x] { 0 } else { d + 1 };
            if d <= r {
                a[y * w + x] = true;
            }
        }
    }
    let mut b = vec![false; w * h];
    for x in 0..w {
        let mut d = r + 1;
        for y in 0..h {
            d = if a[y * w + x] { 0 } else { d + 1 };
            if d <= r {
                b[y * w + x] = true;
            }
        }
        d = r + 1;
        for y in (0..h).rev() {
            d = if a[y * w + x] { 0 } else { d + 1 };
            if d <= r {
                b[y * w + x] = true;
            }
        }
    }
    b
}

/// The tight box of what's drawn in `px` (RGBA, w×h, top row first) that the guessed box `g` (pixels) was meant for.
/// The background is read off the edges; whatever differs from it is ink; ink within `join` px counts as one piece;
/// the pieces centred in the guess make the box. Else the nearest piece of about the right size, else None.
pub fn fit_box(px: &[u8], w: usize, h: usize, g: Rect, join: usize) -> Option<Rect> {
    if w < 3 || h < 3 || px.len() < w * h * 4 || g.is_empty() {
        return None;
    }
    let (mut rs, mut gs, mut bs) = (vec![], vec![], vec![]);
    let mut edge = |x: usize, y: usize| {
        let i = (y * w + x) * 4;
        rs.push(px[i]);
        gs.push(px[i + 1]);
        bs.push(px[i + 2]);
    };
    for x in 0..w {
        edge(x, 0);
        edge(x, h - 1);
    }
    for y in 1..h - 1 {
        edge(0, y);
        edge(w - 1, y);
    }
    let median = |a: &mut Vec<u8>| {
        a.sort_unstable();
        a[a.len() / 2] as i32
    };
    let bg = (median(&mut rs), median(&mut gs), median(&mut bs));
    let mut ink = vec![false; w * h];
    let mut count = 0;
    for i in 0..w * h {
        let d = (px[i * 4] as i32 - bg.0).abs().max((px[i * 4 + 1] as i32 - bg.1).abs()).max((px[i * 4 + 2] as i32 - bg.2).abs());
        if d > 38 {
            ink[i] = true;
            count += 1;
        }
    }
    if count == 0 || count as f64 > 0.6 * (w * h) as f64 {
        return None;
    }
    let joined = dilate(&ink, w, h, join);
    let mut label = vec![-1i32; w * h];
    let (mut pieces, mut sizes, mut stack): (Vec<Option<Rect>>, Vec<usize>, Vec<usize>) = (vec![], vec![], vec![]);
    for start in 0..w * h {
        if !joined[start] || label[start] >= 0 {
            continue;
        }
        let id = pieces.len() as i32;
        let (mut x0, mut y0, mut x1, mut y1, mut n) = (usize::MAX, usize::MAX, 0usize, 0usize, 0usize);
        label[start] = id;
        stack.push(start);
        while let Some(i) = stack.pop() {
            let (x, y) = (i % w, i / w);
            if ink[i] {
                x0 = x0.min(x);
                y0 = y0.min(y);
                x1 = x1.max(x);
                y1 = y1.max(y);
                n += 1;
            }
            let near = [(x > 0).then(|| i - 1), (x < w - 1).then(|| i + 1), (y > 0).then(|| i - w), (y < h - 1).then(|| i + w)];
            for j in near.into_iter().flatten() {
                if joined[j] && label[j] < 0 {
                    label[j] = id;
                    stack.push(j);
                }
            }
        }
        pieces.push((n > 0).then(|| Rect::new(x0 as f64, y0 as f64, (x1 - x0 + 1) as f64, (y1 - y0 + 1) as f64)));
        sizes.push(n);
    }
    let area = g.area();
    let roomy = g.inset(-g.w * 0.15, -g.h * 0.15);
    let mut pick: Option<Rect> = None;
    // Specks (anti-aliasing) and panels far bigger than the guess don't count.
    for (r, &n) in pieces.iter().zip(&sizes) {
        if let Some(r) = r {
            if n >= 3 && r.area() <= 6.0 * area && roomy.contains(r.mid_x(), r.mid_y()) {
                pick = Some(pick.map_or(*r, |p| p.union(r)));
            }
        }
    }
    if pick.is_none() {
        let mut best = f64::MAX;
        for (r, &n) in pieces.iter().zip(&sizes) {
            if let (Some(r), true) = (r, n >= 3) {
                let ratio = r.area() / area;
                let d = (r.mid_x() - g.mid_x()).hypot(r.mid_y() - g.mid_y());
                if ratio > 0.08 && ratio < 6.0 && d <= g.w.max(g.h) * 0.75 && d < best {
                    best = d;
                    pick = Some(*r);
                }
            }
        }
    }
    let p = pick?;
    let ratio = p.area() / area;
    (ratio > 0.04 && ratio < 6.0).then_some(p)
}

/// A guessed box (screen points) fitted to what's drawn around it in `img` (RGBA, iw×ih), a capture of `area`
/// (screen points). Letter-sized guesses join nothing, so neighbouring letters stay apart; bigger ones join ink
/// up to 3 points apart.
pub fn fit_rect(img: &[u8], iw: usize, ih: usize, area: Rect, g: Rect) -> Option<Rect> {
    let scale = iw as f64 / area.w.max(1.0);
    let pad = 12f64.max(0.6 * g.w.max(g.h));
    let want = g.inset(-pad, -pad).intersection(&area)?;
    let cut = Rect::new(((want.x - area.x) * scale).floor(), ((want.y - area.y) * scale).floor(), (want.w * scale).ceil(), (want.h * scale).ceil())
        .intersection(&Rect::new(0.0, 0.0, iw as f64, ih as f64))?;
    let (cx, cy, w, h) = (cut.x as usize, cut.y as usize, cut.w as usize, cut.h as usize);
    let mut buf = Vec::with_capacity(w * h * 4);
    for y in cy..cy + h {
        buf.extend_from_slice(&img[(y * iw + cx) * 4..(y * iw + cx + w) * 4]);
    }
    let (ox, oy) = (area.x + cut.x / scale, area.y + cut.y / scale);
    let gp = Rect::new((g.x - ox) * scale, (g.y - oy) * scale, g.w * scale, g.h * scale);
    let side = g.w.min(g.h);
    let join = if side < 16.0 { 0 } else { (3f64.min(side * 0.1) * scale).round() as usize };
    let r = fit_box(&buf, w, h, gp, join)?;
    Some(Rect::new(ox + r.x / scale, oy + r.y / scale, r.w / scale, r.h / scale))
}

// ---------- text read off the screen ----------

/// Where `q` is in one OCR'd line, given its words and their boxes. Phrases match whole words, ignoring case; a
/// single character matches anywhere, case and all, with a box cut from its word by position (refined later).
pub fn ocr_matches(words: &[(String, Rect)], q: &str) -> Vec<Rect> {
    let qc: Vec<char> = q.trim().chars().collect();
    if qc.is_empty() {
        return vec![];
    }
    if qc.len() == 1 {
        let mut out = vec![];
        for (t, r) in words {
            let cs: Vec<char> = t.chars().collect();
            for (i, c) in cs.iter().enumerate() {
                if *c == qc[0] {
                    let cw = r.w / cs.len() as f64;
                    out.push(Rect::new(r.x + cw * i as f64, r.y, cw, r.h));
                }
            }
        }
        return out;
    }
    // The line as one string, with where each word starts and ends in it.
    let (mut line, mut spans) = (Vec::<char>::new(), vec![]);
    for (t, _) in words {
        if !line.is_empty() {
            line.push(' ');
        }
        let s = line.len();
        line.extend(t.chars().flat_map(char::to_lowercase));
        spans.push((s, line.len()));
    }
    let ql: Vec<char> = qc.iter().flat_map(|c| c.to_lowercase()).collect();
    let mut out = vec![];
    let mut i = 0;
    while i + ql.len() <= line.len() {
        let end = i + ql.len();
        let edge = |c: Option<&char>| c.is_none_or(|c| !c.is_alphanumeric());
        if line[i..end] == ql[..] && edge(i.checked_sub(1).and_then(|j| line.get(j))) && edge(line.get(end)) {
            let r = spans.iter().zip(words).filter(|((s, e), _)| *s < end && *e > i).map(|(_, (_, r))| *r).reduce(|a, b| a.union(&b));
            out.extend(r);
            i = end;
        } else {
            i += 1;
        }
    }
    out
}

// ---------- checks shared by `cargo test` and `--self-test` ----------

pub fn self_test() -> Vec<(&'static str, bool)> {
    let win = |id: i64, title: &str, w: f64, h: f64| Win { id, pid: 1, app: "App".into(), exe: "app".into(), title: title.into(), r: Rect::new(0.0, 0.0, w, h) };
    let mw = |v: Vec<Win>| main_window(&v).map(|w| w.id);
    let pw = |id: i64, pid: u32| Win { id, pid, ..Default::default() };
    let nw = |v: Vec<Win>| newer_windows(&v, &pw(2, 1)).iter().map(|w| w.id).collect::<Vec<_>>();
    let cw = |v: Vec<Win>| current_window(&v, &pw(2, 1)).map(|w| (w.id, w.r.x as i64, w.r.y as i64));
    let moved = Win { id: 2, pid: 1, r: Rect::new(40.0, 60.0, 600.0, 400.0), ..Default::default() };
    let canvas = |w: usize, h: usize, rects: &[Rect]| {
        let mut b = vec![0u8; w * h * 4];
        for i in 0..w * h {
            b[i * 4..i * 4 + 4].copy_from_slice(&[22, 24, 29, 255]);
        }
        for r in rects {
            for y in r.y as usize..r.max_y() as usize {
                for x in r.x as usize..r.max_x() as usize {
                    b[(y * w + x) * 4..(y * w + x) * 4 + 3].copy_from_slice(&[230, 230, 230]);
                }
            }
        }
        b
    };
    let img = canvas(100, 60, &[Rect::new(30.0, 10.0, 20.0, 20.0), Rect::new(70.0, 40.0, 6.0, 6.0)]);
    let strokes = canvas(80, 40, &[Rect::new(20.0, 10.0, 6.0, 20.0), Rect::new(28.0, 10.0, 6.0, 20.0)]);
    let words = |ws: &[(&str, f64)]| ws.iter().map(|(t, x)| (t.to_string(), Rect::new(*x, 0.0, 10.0 * t.chars().count() as f64, 12.0))).collect::<Vec<_>>();
    let line = words(&[("Harbor", 0.0), ("harbors", 70.0), ("Open", 150.0), ("now", 200.0)]);
    vec![
        ("a small untitled button over the main window is passed over", mw(vec![win(1, "", 183.0, 88.0), win(2, "CapCut", 1800.0, 1066.0)]) == Some(2)),
        ("even when the main window is untitled", mw(vec![win(1, "", 183.0, 88.0), win(2, "", 1800.0, 1066.0)]) == Some(2)),
        ("a titled small window in front is kept", mw(vec![win(1, "Export", 300.0, 200.0), win(2, "Doc", 1800.0, 1066.0)]) == Some(1)),
        ("a sizeable untitled window in front is kept", mw(vec![win(1, "", 900.0, 600.0), win(2, "Doc", 1800.0, 1066.0)]) == Some(1)),
        ("an app with only a small window still has it", mw(vec![win(1, "", 183.0, 88.0)]) == Some(1)),
        // Regression: click_text "Delete" searched the window the plan started on, not the document it had opened since.
        ("a window in front of the plan's window is searched", nw(vec![pw(5, 1), pw(2, 1)]) == vec![5]),
        ("windows behind the plan's window are not", nw(vec![pw(2, 1), pw(1, 1)]).is_empty()),
        ("another app's window in front is not", nw(vec![pw(9, 7), pw(5, 1), pw(2, 1)]) == vec![5]),
        ("the plan's window gone: that app's windows", nw(vec![pw(9, 7), pw(5, 1), pw(4, 1)]) == vec![5, 4]),
        ("at most 3, front first", nw(vec![pw(8, 1), pw(7, 1), pw(6, 1), pw(5, 1), pw(2, 1)]) == vec![8, 7, 6]),
        // Regression: the plan's window had closed, and click_text "Delete" still looked inside its old rectangle, so it
        // picked the dialog's sentence containing "delete" over the Delete button beside it.
        ("the plan's window still open: as it is now", cw(vec![pw(5, 1), moved]) == Some((2, 40, 60))),
        ("the plan's window gone: that app's front window", cw(vec![pw(9, 7), pw(5, 1), pw(4, 1)]) == Some((5, 0, 0))),
        ("the plan's window gone and none left: nothing", cw(vec![]).is_none()),
        ("another app's windows are never taken", cw(vec![pw(9, 7), pw(8, 7)]).is_none()),
        ("an app is found by its program name", app_matches("Microsoft Edge", "msedge", "msedge") && app_matches("Notepad", "notepad", "notepad.exe")),
        ("an app is found by its own name", app_matches("Microsoft Edge", "msedge", "edge") && !app_matches("Microsoft Edge", "msedge", "")),
        ("'browser' finds a browser, not other apps", app_matches("Google Chrome", "chrome", "browser") && !app_matches("Notepad", "notepad", "browser")),
        ("typing after a click in an app that hides its fields", may_type(false, Some(CT_WINDOW), true) && may_type(false, Some(CT_PANE), true)),
        ("no typing in such an app without a click first", !may_type(false, Some(CT_WINDOW), false)),
        ("no typing on a web page outside a field, even after a click", !may_type(false, Some(CT_DOCUMENT), true)),
        ("typing in a real text field", may_type(true, Some(CT_EDIT), false)),
        ("a word inside a file name isn't a word match", match_score("mtlsvideop:speed.mp4", "speed") == 2),
        ("so click_text won't click it", !click_match(MATCH_NAMES[match_score("mtlsvideop:speed.mp4", "speed")])),
        ("inside a longer word isn't clickable", !click_match(MATCH_NAMES[match_score("following", "follow")])),
        ("the whole label matches", match_score("speed", "speed") == 0),
        ("a whole word in a label matches", match_score("save changes", "save") == 1 && match_score("speed:", "speed") == 1 && match_score("next \u{2192}", "next") == 1),
        ("an amount with symbols matches", match_score("total $1,250", "$1,250") == 1),
        ("wait after a click, before a screenshot, can end early", react_waits(&["click_at", "wait", "take_screenshot"]) == vec![1]),
        ("wait at the end after a key can end early", react_waits(&["fill", "key", "wait"]) == vec![2]),
        ("a wait first or after typing runs in full", react_waits(&["wait", "type", "wait", "read_window"]).is_empty()),
        ("each wait after a click counts", react_waits(&["click_at", "wait", "click_at", "wait", "take_screenshot"]) == vec![1, 3]),
        ("risky labels need a yes", risky("Delete account") && risky("Place your order") && risky("Cancel my subscription") && risky("Löschen") && risky("削除する")),
        ("everyday labels don't", !risky("Save") && !risky("Undelete") && !risky("Sender name") && !risky("Cancel") && !risky("Payment details")),
        ("cmd is Ctrl on Windows", parse_combo("cmd+a") == Some((MOD_CTRL, 0x41)) && parse_combo("Cmd + S") == Some((MOD_CTRL, 0x53))),
        ("named and symbol keys", parse_combo("return") == Some((0, 0x0D)) && parse_combo("shift+tab") == Some((MOD_SHIFT, 0x09)) && parse_combo("*") == Some((MOD_SHIFT, 0x38)) && parse_combo("+") == Some((MOD_SHIFT, 0xBB))),
        ("cmd++ zooms in, ctrl+end goes to the end", parse_combo("cmd++") == Some((MOD_CTRL | MOD_SHIFT, 0xBB)) && parse_combo("ctrl+end") == Some((MOD_CTRL, 0x23))),
        ("unknown keys fail", parse_combo("cmd+banana").is_none() && parse_combo("").is_none()),
        ("terminals and password apps are hands-off", hands_off("cmd") == Some("Command Prompt") && hands_off("WindowsTerminal").is_some() && hands_off("KeePassXC").is_some()),
        ("everyday apps aren't", hands_off("notepad").is_none() && hands_off("msedge").is_none()),
        ("a guess a few pixels off fits the square", fit_box(&img, 100, 60, Rect::new(25.0, 5.0, 20.0, 20.0), 0) == Some(Rect::new(30.0, 10.0, 20.0, 20.0))),
        ("a loose guess round a small dot fits the dot", fit_box(&img, 100, 60, Rect::new(63.0, 33.0, 20.0, 20.0), 0) == Some(Rect::new(70.0, 40.0, 6.0, 6.0))),
        ("a guess on empty space fits nothing", fit_box(&canvas(100, 60, &[Rect::new(2.0, 2.0, 4.0, 4.0)]), 100, 60, Rect::new(60.0, 30.0, 20.0, 20.0), 0).is_none()),
        ("an icon's strokes join", fit_box(&strokes, 80, 40, Rect::new(18.0, 8.0, 18.0, 24.0), 3) == Some(Rect::new(20.0, 10.0, 14.0, 20.0))),
        ("a letter-sized guess keeps neighbours apart", fit_box(&strokes, 80, 40, Rect::new(19.0, 9.0, 8.0, 22.0), 0) == Some(Rect::new(20.0, 10.0, 6.0, 20.0))),
        ("a fitted rect maps back to screen points", fit_rect(&img, 100, 60, Rect::new(500.0, 300.0, 50.0, 30.0), Rect::new(512.0, 302.0, 10.0, 10.0)) == Some(Rect::new(515.0, 305.0, 10.0, 10.0))),
        ("OCR words match whole words only", ocr_matches(&line, "harbor") == vec![Rect::new(0.0, 0.0, 60.0, 12.0)]),
        ("an OCR phrase spans its words", ocr_matches(&line, "open now") == vec![Rect::new(150.0, 0.0, 80.0, 12.0)]),
        ("one character is cut from its word, case and all", ocr_matches(&line, "O") == vec![Rect::new(150.0, 0.0, 10.0, 12.0)]),
        ("the item below the anchor wins", nearest_after(&Rect::new(0.0, 100.0, 50.0, 10.0), &[Rect::new(0.0, 50.0, 20.0, 10.0), Rect::new(0.0, 130.0, 20.0, 10.0), Rect::new(0.0, 300.0, 20.0, 10.0)]) == Some(1)),
        ("long scrolled-off text keeps its start and end", offscreen_text((0..50).map(|i| (i as f64, format!("line number {i}"))).collect(), 100).last().map(String::as_str) == Some("line number 49")),
    ]
}

#[cfg(test)]
mod tests {
    #[test]
    fn self_test_passes() {
        let bad: Vec<_> = super::self_test().into_iter().filter(|c| !c.1).map(|c| c.0).collect();
        assert!(bad.is_empty(), "failed: {bad:?}");
    }
}
