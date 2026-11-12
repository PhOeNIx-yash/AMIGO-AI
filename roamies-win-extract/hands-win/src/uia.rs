// A window's controls and text through UI Automation: the whole tree in one cached round trip, then walked here
// like walk()/findTextEls()/scanText() in hands/main.swift, with the same roles, ids and output.
use crate::logic::*;
use crate::win::{hwnd, monitors, Mon};
use serde_json::{json, Value};
use std::sync::OnceLock;
use windows::core::{Interface, BSTR};
use windows::Win32::Foundation::RECT;
use windows::Win32::System::Com::{CoCreateInstance, CLSCTX_INPROC_SERVER};
use windows::Win32::System::Variant::{VariantClear, VARIANT, VT_BOOL, VT_BSTR, VT_I4};
use windows::Win32::UI::Accessibility::*;

pub struct Uia {
    pub a: IUIAutomation,
    cr: IUIAutomationCacheRequest,
}
// The helper runs on one thread in the multithreaded apartment, where UI Automation's objects may be used freely.
unsafe impl Send for Uia {}
unsafe impl Sync for Uia {}

pub fn uia() -> &'static Uia {
    static U: OnceLock<Uia> = OnceLock::new();
    U.get_or_init(|| unsafe {
        let a: IUIAutomation = CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER).expect("UI Automation");
        // An app that stops answering mustn't hang the helper.
        if let Ok(a2) = a.cast::<IUIAutomation2>() {
            let _ = a2.SetConnectionTimeout(4000);
            let _ = a2.SetTransactionTimeout(8000);
        }
        let cr = a.CreateCacheRequest().expect("cache request");
        for p in PROPS {
            let _ = cr.AddProperty(p);
        }
        let _ = cr.SetTreeScope(TreeScope_Subtree);
        Uia { a, cr }
    })
}

const PROPS: [UIA_PROPERTY_ID; 16] = [
    UIA_ControlTypePropertyId,
    UIA_NamePropertyId,
    UIA_BoundingRectanglePropertyId,
    UIA_IsEnabledPropertyId,
    UIA_HasKeyboardFocusPropertyId,
    UIA_IsKeyboardFocusablePropertyId,
    UIA_IsPasswordPropertyId,
    UIA_HelpTextPropertyId,
    UIA_ValueValuePropertyId,
    UIA_IsValuePatternAvailablePropertyId,
    UIA_ValueIsReadOnlyPropertyId,
    UIA_IsInvokePatternAvailablePropertyId,
    UIA_IsTogglePatternAvailablePropertyId,
    UIA_IsSelectionItemPatternAvailablePropertyId,
    UIA_IsExpandCollapsePatternAvailablePropertyId,
    UIA_IsTextPatternAvailablePropertyId,
];

fn var_bool(v: &VARIANT) -> bool {
    unsafe { v.Anonymous.Anonymous.vt == VT_BOOL && v.Anonymous.Anonymous.Anonymous.boolVal.as_bool() }
}
fn var_str(v: &VARIANT) -> Option<String> {
    unsafe { (v.Anonymous.Anonymous.vt == VT_BSTR).then(|| v.Anonymous.Anonymous.Anonymous.bstrVal.to_string()) }
}
fn cached(el: &IUIAutomationElement, p: UIA_PROPERTY_ID) -> VARIANT {
    unsafe { el.GetCachedPropertyValue(p) }.unwrap_or_default()
}
fn cached_bool(el: &IUIAutomationElement, p: UIA_PROPERTY_ID) -> bool {
    let mut v = cached(el, p);
    let b = var_bool(&v);
    let _ = unsafe { VariantClear(&mut v) };
    b
}
fn cached_str(el: &IUIAutomationElement, p: UIA_PROPERTY_ID) -> String {
    let mut v = cached(el, p);
    let s = var_str(&v).unwrap_or_default();
    let _ = unsafe { VariantClear(&mut v) };
    s
}
pub fn i4(n: i32) -> VARIANT {
    let mut v = VARIANT::default();
    unsafe {
        let inner = &mut *v.Anonymous.Anonymous;
        inner.vt = VT_I4;
        inner.Anonymous.lVal = n;
    }
    v
}

/// Physical pixels to screen points, with the monitors looked up once for a whole tree.
pub struct Units(Vec<Mon>);
impl Units {
    pub fn now() -> Units {
        Units(monitors())
    }
    pub fn pt(&self, r: RECT) -> Option<Rect> {
        if r.right <= r.left || r.bottom <= r.top {
            return None;
        }
        let (cx, cy) = ((r.left + r.right) / 2, (r.top + r.bottom) / 2);
        let s = self.0.iter().find(|m| cx >= m.px.left && cx < m.px.right && cy >= m.px.top && cy < m.px.bottom).or(self.0.iter().find(|m| m.primary)).map_or(1.0, |m| m.scale);
        Some(Rect::new(r.left as f64 / s, r.top as f64 / s, (r.right - r.left) as f64 / s, (r.bottom - r.top) as f64 / s))
    }
}

#[derive(Clone)]
pub struct Node {
    pub el: IUIAutomationElement,
    pub ct: i32,
    pub name: String,
    pub help: String,
    pub value: String,
    pub r: Option<Rect>,
    pub enabled: bool,
    pub focused: bool,
    pub focusable: bool,
    pub password: bool,
    pub val_avail: bool,
    pub val_ro: bool,
    pub invoke: bool,
    pub toggle: bool,
    pub select: bool,
    pub expand: bool,
    pub text_pat: bool,
    pub parent: Option<usize>,
    pub kids: Vec<usize>,
    /// Inside a web page (a browser's or an Electron app's document).
    pub web: bool,
    /// A web page's document itself.
    pub web_root: bool,
    /// A document you type in (Notepad's page, a rich text box), not a web page.
    pub text_area: bool,
}

impl Node {
    fn of(el: IUIAutomationElement, u: &Units, parent: Option<usize>) -> Node {
        let ct = unsafe { el.CachedControlType() }.map_or(0, |c| c.0);
        let r = unsafe { el.CachedBoundingRectangle() }.ok().and_then(|r| u.pt(r));
        let s = |p| cached_str(&el, p);
        let b = |p| cached_bool(&el, p);
        Node {
            ct,
            name: s(UIA_NamePropertyId),
            help: s(UIA_HelpTextPropertyId),
            value: s(UIA_ValueValuePropertyId),
            r,
            enabled: b(UIA_IsEnabledPropertyId),
            focused: b(UIA_HasKeyboardFocusPropertyId),
            focusable: b(UIA_IsKeyboardFocusablePropertyId),
            password: b(UIA_IsPasswordPropertyId),
            val_avail: b(UIA_IsValuePatternAvailablePropertyId),
            val_ro: b(UIA_ValueIsReadOnlyPropertyId),
            invoke: b(UIA_IsInvokePatternAvailablePropertyId),
            toggle: b(UIA_IsTogglePatternAvailablePropertyId),
            select: b(UIA_IsSelectionItemPatternAvailablePropertyId),
            expand: b(UIA_IsExpandCollapsePatternAvailablePropertyId),
            text_pat: b(UIA_IsTextPatternAvailablePropertyId),
            parent,
            kids: vec![],
            web: false,
            web_root: false,
            text_area: false,
            el,
        }
    }
    pub fn editable(&self) -> bool {
        self.val_avail && !self.val_ro
    }
    /// Somewhere to type text: a text field, an editable combo box or a text area.
    pub fn is_field(&self) -> bool {
        self.ct == CT_EDIT || (self.ct == CT_COMBOBOX && self.editable()) || self.text_area
    }
    /// The control's role word, or None when it isn't one of the controls `ui` lists.
    pub fn role(&self) -> Option<&'static str> {
        if [CT_LISTITEM, CT_TREEITEM, CT_DATAITEM].contains(&self.ct) && !(self.select || self.invoke || self.expand || self.toggle) {
            return None;
        }
        role_word(self.ct, if self.ct == CT_DOCUMENT { self.text_area } else { self.editable() }, self.password)
    }
    pub fn kind(&self) -> String {
        self.role().map_or_else(|| kind_word(self.ct).to_string(), String::from)
    }
    /// What the control is called: its name, else its help text (where web pages put a placeholder).
    pub fn label(&self) -> String {
        if !self.name.trim().is_empty() { self.name.clone() } else { self.help.clone() }
    }
}

pub struct Tree {
    pub nodes: Vec<Node>,
}

const CONTENT: [i32; 11] = [CT_HYPERLINK, CT_TEXT, CT_GROUP, CT_BUTTON, CT_EDIT, CT_IMAGE, CT_LIST, CT_CUSTOM, CT_TABLE, CT_COMBOBOX, CT_CHECKBOX];

impl Tree {
    pub fn empty() -> Tree {
        Tree { nodes: vec![] }
    }
    /// Everything under `root`, in reading order (depth first), at most `max` elements.
    pub fn read(root: &IUIAutomationElement, max: usize) -> Tree {
        let Ok(c) = (unsafe { root.BuildUpdatedCache(&uia().cr) }) else { return Tree::empty() };
        let u = Units::now();
        let mut nodes: Vec<Node> = vec![];
        let mut stack: Vec<(IUIAutomationElement, Option<usize>, usize)> = vec![(c, None, 0)];
        while let Some((el, parent, depth)) = stack.pop() {
            if nodes.len() >= max {
                break;
            }
            let kids = if depth < 60 { unsafe { el.GetCachedChildren() }.ok() } else { None };
            let i = nodes.len();
            nodes.push(Node::of(el, &u, parent));
            if let Some(p) = parent {
                nodes[p].kids.push(i);
            }
            if let Some(k) = kids {
                let n = unsafe { k.Length() }.unwrap_or(0);
                for j in (0..n).rev() {
                    if let Ok(e) = unsafe { k.GetElement(j) } {
                        stack.push((e, Some(i), depth + 1));
                    }
                }
            }
        }
        // A document holding links, text or controls is a web page; one without them is a text area.
        let mut content = vec![false; nodes.len()];
        for i in (0..nodes.len()).rev() {
            if let Some(p) = nodes[i].parent {
                if content[i] || CONTENT.contains(&nodes[i].ct) {
                    content[p] = true;
                }
            }
        }
        for i in 0..nodes.len() {
            let n = &nodes[i];
            let web_root = n.ct == CT_DOCUMENT && content[i];
            let text_area = n.ct == CT_DOCUMENT && !web_root && (n.editable() || (n.text_pat && n.focusable));
            let web = n.parent.is_some_and(|p| nodes[p].web || nodes[p].web_root);
            let n = &mut nodes[i];
            (n.web_root, n.text_area, n.web) = (web_root, text_area, web);
        }
        Tree { nodes }
    }
    pub fn of_window(w: &Win) -> Tree {
        match unsafe { uia().a.ElementFromHandle(hwnd(w.id)) } {
            Ok(root) => Tree::read(&root, 12000),
            Err(_) => Tree::empty(),
        }
    }

    /// A field with no label of its own: the text just before it in its row.
    fn nearby_label(&self, i: usize) -> Option<String> {
        let mut cur = i;
        for _ in 0..3 {
            let p = self.nodes[cur].parent?;
            let kids = &self.nodes[p].kids;
            let at = kids.iter().position(|&k| k == cur)?;
            for &sib in kids[..at].iter().rev() {
                let mut stack = vec![(sib, 0)];
                while let Some((e, d)) = stack.pop() {
                    let n = &self.nodes[e];
                    if n.is_field() || n.role().is_some() {
                        break;
                    }
                    let t = n.name.trim();
                    if n.ct == CT_TEXT && t.chars().count() >= 2 {
                        return Some(t.chars().take(60).collect());
                    }
                    if d < 3 {
                        stack.extend(n.kids.iter().rev().map(|&k| (k, d + 1)));
                    }
                }
            }
            cur = p;
        }
        None
    }

    /// Every control, numbered. Ones scrolled out of view come after the visible ones (marked offscreen).
    pub fn walk(&self, view: Rect, limit: usize) -> Vec<(Node, Value)> {
        let (mut found, mut hidden): (Vec<(Node, Value)>, Vec<(Node, Value)>) = (vec![], vec![]);
        for (i, n) in self.nodes.iter().enumerate() {
            if found.len() >= limit {
                break;
            }
            let (Some(role), Some(f)) = (n.role(), n.r) else { continue };
            if f.w <= 1.0 || f.h <= 1.0 || !n.enabled {
                continue;
            }
            let mut label = n.label();
            if label.trim().is_empty() && n.is_field() {
                label = self.nearby_label(i).unwrap_or_default();
            }
            // A placeholder alone hides the real label: add the label too.
            if n.is_field() && n.name.trim().is_empty() && !n.help.is_empty() && label == n.help {
                if let Some(near) = self.nearby_label(i).filter(|l| *l != n.help) {
                    label = format!("{near} ({})", n.help);
                }
            }
            let mut item = json!({ "role": role, "label": label.chars().take(80).collect::<String>(), "bounds": f.ints() });
            if !n.password && !n.value.is_empty() {
                item["value"] = n.value.chars().take(80).collect::<String>().into();
            }
            if n.focused {
                item["focused"] = true.into();
            }
            // Mostly on screen counts as visible; a sliver at the edge doesn't.
            if view.intersection(&f).is_some_and(|s| s.area() >= f.area() * 0.5) {
                found.push((n.clone(), item));
            } else if hidden.len() < 40 {
                item["offscreen"] = true.into();
                hidden.push((n.clone(), item));
            }
        }
        let room = limit.saturating_sub(found.len());
        found.into_iter().chain(hidden.into_iter().take(room)).enumerate().map(|(i, (n, mut item))| {
            item["id"] = (i + 1).into();
            (n, item)
        }).collect()
    }

    /// Any element whose text matches `query`, best first: exact, then whole words, then contains; words on the page
    /// before the browser's own tabs and toolbar; smallest first.
    pub fn find_text(&self, win: Rect, query: &str, hidden: bool, limit: usize) -> Vec<(Node, Value)> {
        let q = query.trim().to_lowercase();
        if q.is_empty() {
            return vec![];
        }
        let mut hits: Vec<(usize, bool, f64, &Node, Value)> = vec![];
        for n in &self.nodes {
            let Some(t) = [&n.value, &n.name, &n.help].into_iter().find(|t| t.to_lowercase().contains(&q)) else { continue };
            let Some(f) = n.r.filter(|f| f.w > 1.0 && f.h > 1.0) else { continue };
            if !hidden && !win.intersects(&f) {
                continue;
            }
            let score = match_score(t.trim().to_lowercase().as_str(), &q);
            hits.push((score, n.web, f.area(), n, json!({ "text": t.chars().take(80).collect::<String>(), "match": MATCH_NAMES[score], "role": n.kind(), "bounds": f.ints() })));
        }
        hits.sort_by(|a, b| a.0.cmp(&b.0).then(b.1.cmp(&a.1)).then(a.2.total_cmp(&b.2)));
        hits.into_iter().take(limit).map(|h| (h.3.clone(), h.4)).collect()
    }

    /// The text in the window: on screen as (y, x, text), and scrolled out of view as (y, text).
    pub fn scan_text(&self, win: Rect) -> (Vec<(f64, f64, String)>, Vec<(f64, String)>) {
        let (mut on, mut off) = (vec![], vec![]);
        let (mut seen_on, mut seen_off) = (std::collections::HashSet::new(), std::collections::HashSet::new());
        for n in &self.nodes {
            let Some(f) = n.r.filter(|_| n.ct == CT_TEXT) else { continue };
            let line = n.name.split_whitespace().collect::<Vec<_>>().join(" ");
            if line.chars().count() < 2 {
                continue;
            }
            let line: String = line.chars().take(160).collect();
            if win.intersects(&f) {
                if seen_on.insert(line.clone()) {
                    on.push((f.y, f.x, line));
                }
            } else if f.h > 1.0 && seen_off.insert(line.clone()) {
                off.push((f.y, line));
            }
        }
        (on, off)
    }

    /// Activates what a piece of text belongs to: focuses a text field, else presses the element or the nearest
    /// ancestor that can be pressed. False if nothing could.
    pub fn press_text(&self, i: usize) -> bool {
        let n = &self.nodes[i];
        if [CT_TEXT, CT_IMAGE, CT_GROUP, CT_CUSTOM].contains(&n.ct) {
            let mut up = n.parent;
            for _ in 0..3 {
                let Some(c) = up else { break };
                let p = &self.nodes[c];
                if [CT_BUTTON, CT_HYPERLINK, CT_MENUITEM, CT_SPLITBUTTON, CT_TABITEM, CT_CHECKBOX, CT_RADIO, CT_LISTITEM, CT_TREEITEM].contains(&p.ct) && press(&p.el) {
                    return true;
                }
                up = p.parent;
            }
        }
        let mut cur = Some(i);
        for _ in 0..6 {
            let Some(c) = cur else { break };
            let p = &self.nodes[c];
            if p.is_field() {
                return focus(&p.el);
            }
            if (p.invoke || p.toggle || p.select) && press(&p.el) {
                return true;
            }
            cur = p.parent;
        }
        false
    }

    /// The index of a node for an element (from an earlier walk), matched by UI Automation's identity.
    pub fn index_of(&self, el: &IUIAutomationElement) -> Option<usize> {
        self.nodes.iter().position(|n| same(&n.el, el))
    }
}

// ---------- acting on one element (no cursor needed) ----------

pub fn same(a: &IUIAutomationElement, b: &IUIAutomationElement) -> bool {
    unsafe { uia().a.CompareElements(a, b) }.is_ok_and(|b| b.as_bool())
}

pub fn invoke(el: &IUIAutomationElement) -> bool {
    unsafe { el.GetCurrentPatternAs::<IUIAutomationInvokePattern>(UIA_InvokePatternId).and_then(|p| p.Invoke()) }.is_ok()
}
pub fn toggle(el: &IUIAutomationElement) -> bool {
    unsafe { el.GetCurrentPatternAs::<IUIAutomationTogglePattern>(UIA_TogglePatternId).and_then(|p| p.Toggle()) }.is_ok()
}
pub fn select(el: &IUIAutomationElement) -> bool {
    unsafe { el.GetCurrentPatternAs::<IUIAutomationSelectionItemPattern>(UIA_SelectionItemPatternId).and_then(|p| p.Select()) }.is_ok()
}
pub fn expand(el: &IUIAutomationElement) -> bool {
    unsafe { el.GetCurrentPatternAs::<IUIAutomationExpandCollapsePattern>(UIA_ExpandCollapsePatternId).and_then(|p| p.Expand()) }.is_ok()
}
pub fn collapse(el: &IUIAutomationElement) -> bool {
    unsafe { el.GetCurrentPatternAs::<IUIAutomationExpandCollapsePattern>(UIA_ExpandCollapsePatternId).and_then(|p| p.Collapse()) }.is_ok()
}
/// Presses a control the way it can be pressed: invoke, toggle, select, or open it.
pub fn press(el: &IUIAutomationElement) -> bool {
    invoke(el) || toggle(el) || select(el) || expand(el)
}
pub fn focus(el: &IUIAutomationElement) -> bool {
    unsafe { el.SetFocus() }.is_ok()
}
pub fn scroll_into_view(el: &IUIAutomationElement) -> bool {
    unsafe { el.GetCurrentPatternAs::<IUIAutomationScrollItemPattern>(UIA_ScrollItemPatternId).and_then(|p| p.ScrollIntoView()) }.is_ok()
}
pub fn set_value(el: &IUIAutomationElement, text: &str) -> bool {
    unsafe { el.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId).and_then(|p| p.SetValue(&BSTR::from(text))) }.is_ok()
}
/// What a field shows now: its value, else its document text.
pub fn value(el: &IUIAutomationElement) -> String {
    unsafe {
        if let Ok(v) = el.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId).and_then(|p| p.CurrentValue()) {
            return v.to_string();
        }
        if let Ok(t) = el.GetCurrentPatternAs::<IUIAutomationTextPattern>(UIA_TextPatternId).and_then(|p| p.DocumentRange()).and_then(|r| r.GetText(4000)) {
            return t.to_string();
        }
        el.CurrentName().map(|n| n.to_string()).unwrap_or_default()
    }
}
pub fn live_rect(el: &IUIAutomationElement) -> Option<Rect> {
    Units::now().pt(unsafe { el.CurrentBoundingRectangle() }.ok()?)
}
pub fn is_focused(el: &IUIAutomationElement) -> bool {
    unsafe { el.CurrentHasKeyboardFocus() }.is_ok_and(|b| b.as_bool())
}

/// The element with keyboard focus anywhere, with its process and control type.
pub fn focused() -> Option<(IUIAutomationElement, u32, i32)> {
    unsafe {
        let el = uia().a.GetFocusedElement().ok()?;
        let pid = el.CurrentProcessId().unwrap_or(0) as u32;
        let ct = el.CurrentControlType().map_or(0, |c| c.0);
        Some((el, pid, ct))
    }
}
/// Somewhere typing goes into text: a field, or anything whose value can be edited.
pub fn editable_now(el: &IUIAutomationElement, ct: i32) -> bool {
    if [CT_EDIT, CT_DOCUMENT].contains(&ct) {
        return true;
    }
    unsafe { el.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId).and_then(|p| p.CurrentIsReadOnly()) }.is_ok_and(|ro| !ro.as_bool())
}

/// A browser's address: the first text box in its window is the address bar (Chromium browsers and Firefox).
pub fn browser_address(w: &Win) -> Option<String> {
    unsafe {
        let root = uia().a.ElementFromHandle(hwnd(w.id)).ok()?;
        let cond = uia().a.CreatePropertyCondition(UIA_ControlTypePropertyId, &i4(CT_EDIT)).ok()?;
        let bar = root.FindFirst(TreeScope_Descendants, &cond).ok()?;
        let v = bar.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId).ok()?.CurrentValue().ok()?.to_string();
        let v = v.trim();
        if v.is_empty() || v.contains(char::is_whitespace) {
            return None;
        }
        // Chromium leaves the https:// off while the bar isn't being edited.
        Some(if v.contains("://") || !v.contains('.') { v.to_string() } else { format!("https://{v}") })
    }
}
