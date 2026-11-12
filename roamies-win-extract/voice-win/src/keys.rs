// The talk chord (Win32 key state on Windows, the X server's on Linux X11 sessions) and the Windows microphone
// privacy switches.
// Modifier bits: control, option (Alt), shift, command (Win), the same names the Mac helper uses.

pub const CONTROL: u8 = 1;
pub const OPTION: u8 = 2;
pub const SHIFT: u8 = 4;
pub const COMMAND: u8 = 8;

pub fn mask(names: &[String]) -> Option<u8> {
    let mut m = 0u8;
    for n in names {
        m |= match n.to_lowercase().as_str() {
            "control" | "ctrl" => CONTROL,
            "option" | "alt" => OPTION,
            "shift" => SHIFT,
            "command" | "cmd" | "win" => COMMAND,
            _ => return None,
        };
    }
    (names.len() == 2 && m.count_ones() == 2).then_some(m)
}

pub struct Held {
    pub mods: u8,
    // Right Alt is AltGr on many layouts (Windows reports it as Ctrl+Alt), used to type characters such
    // as @ or €. It never counts as the chord's Alt, and holding it blocks a talk.
    pub altgr: bool,
}

#[cfg(windows)]
mod imp {
    use super::*;
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::GetAsyncKeyState;

    fn down(vk: i32) -> bool {
        // SAFETY: plain Win32 call with a virtual-key code; no pointers involved.
        unsafe { (GetAsyncKeyState(vk) as u16) & 0x8000 != 0 }
    }

    const VK_SHIFT: i32 = 0x10;
    const VK_CONTROL: i32 = 0x11;
    const VK_LMENU: i32 = 0xA4;
    const VK_RMENU: i32 = 0xA5;
    const VK_LWIN: i32 = 0x5B;
    const VK_RWIN: i32 = 0x5C;

    pub fn held() -> Held {
        let mut mods = 0;
        if down(VK_CONTROL) {
            mods |= CONTROL;
        }
        if down(VK_LMENU) {
            mods |= OPTION;
        }
        if down(VK_SHIFT) {
            mods |= SHIFT;
        }
        if down(VK_LWIN) || down(VK_RWIN) {
            mods |= COMMAND;
        }
        Held { mods, altgr: down(VK_RMENU) }
    }

    /// Any non-modifier key held (mouse buttons don't count).
    pub fn other_key_down() -> bool {
        (0x08..=0xFE).filter(|vk| !matches!(vk, 0x10..=0x12 | 0x5B | 0x5C | 0xA0..=0xA5)).any(down)
    }

    /// Windows has no prompt for desktop apps: the microphone is either allowed or switched off in
    /// Settings > Privacy & security > Microphone (for the whole PC, for this user, or for desktop apps).
    pub fn mic_allowed() -> bool {
        use windows_sys::Win32::System::Registry::{RegGetValueW, HKEY, HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, RRF_RT_REG_SZ};
        const KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone";
        let wide = |s: &str| s.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
        let denied = |root: HKEY, sub: &str| {
            let (sub, name) = (wide(sub), wide("Value"));
            let mut buf = [0u16; 16];
            let mut size = (buf.len() * 2) as u32;
            // SAFETY: valid NUL-terminated strings and a buffer whose byte size is passed in `size`.
            let rc = unsafe { RegGetValueW(root, sub.as_ptr(), name.as_ptr(), RRF_RT_REG_SZ, std::ptr::null_mut(), buf.as_mut_ptr().cast(), &mut size) };
            rc == 0 && String::from_utf16_lossy(&buf[..(size as usize / 2).saturating_sub(1)]).eq_ignore_ascii_case("Deny")
        };
        !(denied(HKEY_LOCAL_MACHINE, KEY) || denied(HKEY_CURRENT_USER, KEY) || denied(HKEY_CURRENT_USER, &format!(r"{KEY}\NonPackaged")))
    }
}

/// X11's modifier state (Shift 1, Control 4, Mod1 = Alt 8, Mod4 = Super 64) as the chord's bits. Lock, Num Lock
/// and Mod5 (AltGr, which X11 doesn't report as Ctrl+Alt) aren't part of any chord.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub fn mods_from_x11(mask: u16) -> u8 {
    let mut mods = 0;
    for (bit, m) in [(0x04, CONTROL), (0x08, OPTION), (0x01, SHIFT), (0x40, COMMAND)] {
        if mask & bit != 0 {
            mods |= m;
        }
    }
    mods
}

/// Any key held that isn't a modifier: X11's key bitmap (one bit per keycode), less the keycodes in its modifier map.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub fn other_key(keymap: &[u8], modifiers: &[u8]) -> bool {
    (8..keymap.len() * 8).any(|k| keymap[k / 8] & (1 << (k % 8)) != 0 && !modifiers.contains(&(k as u8)))
}

/// Whether the keyboard can be watched: only on an X11 session. On Wayland an X11 client (through XWayland) sees
/// keys only while an X11 window has the keyboard, so there the talk shortcut is `roamies --talk` ({"cmd":"toggle"}).
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub fn x11_session(session_type: Option<&str>, wayland_display: Option<&str>, display: Option<&str>) -> bool {
    let wayland = session_type.is_some_and(|s| s.eq_ignore_ascii_case("wayland")) || wayland_display.is_some_and(|s| !s.is_empty());
    !wayland && display.is_some_and(|s| !s.is_empty())
}

#[cfg(target_os = "linux")]
mod imp {
    use super::*;
    use std::sync::OnceLock;
    use x11rb::connection::Connection;
    use x11rb::protocol::xproto::ConnectionExt;
    use x11rb::rust_connection::RustConnection;

    struct X {
        conn: RustConnection,
        root: u32,
        modifiers: Vec<u8>,
    }

    // One connection for the helper's life; None on Wayland or when the X server can't be reached.
    fn x() -> Option<&'static X> {
        static X11: OnceLock<Option<X>> = OnceLock::new();
        X11.get_or_init(|| {
            let env = |k: &str| std::env::var(k).ok();
            if !x11_session(env("XDG_SESSION_TYPE").as_deref(), env("WAYLAND_DISPLAY").as_deref(), env("DISPLAY").as_deref()) {
                crate::log("not an X11 session: the talk keys can't be watched (use roamies --talk)");
                return None;
            }
            let (conn, screen) = x11rb::connect(None).map_err(|e| crate::log(&format!("X11: {e}"))).ok()?;
            let root = conn.setup().roots.get(screen)?.root;
            let modifiers = conn.get_modifier_mapping().ok()?.reply().ok()?.keycodes;
            Some(X { conn, root, modifiers })
        })
        .as_ref()
    }

    pub fn held() -> Held {
        // The pointer query carries the modifier state, whichever window has the keyboard.
        let mask = x().and_then(|x| x.conn.query_pointer(x.root).ok()?.reply().ok()).map_or(0, |r| u16::from(r.mask));
        Held { mods: mods_from_x11(mask), altgr: false }
    }

    pub fn other_key_down() -> bool {
        let Some(x) = x() else { return false };
        x.conn.query_keymap().ok().and_then(|c| c.reply().ok()).is_some_and(|r| other_key(&r.keys, &x.modifiers))
    }

    /// No privacy switch to read: PipeWire and PulseAudio hand the microphone to desktop apps.
    pub fn mic_allowed() -> bool {
        true
    }
}

// Elsewhere there's no hotkey (only {"cmd":"listen"} starts a take) and no privacy switch to read.
#[cfg(not(any(windows, target_os = "linux")))]
mod imp {
    use super::*;
    pub fn held() -> Held {
        Held { mods: 0, altgr: false }
    }
    pub fn other_key_down() -> bool {
        false
    }
    pub fn mic_allowed() -> bool {
        true
    }
}

pub use imp::*;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chords_need_two_different_known_modifiers() {
        let m = |a: &[&str]| mask(&a.iter().map(|s| s.to_string()).collect::<Vec<_>>());
        assert_eq!(m(&["control", "option"]), Some(CONTROL | OPTION));
        assert_eq!(m(&["Ctrl", "shift"]), Some(CONTROL | SHIFT));
        assert_eq!(m(&["command", "option"]), Some(COMMAND | OPTION));
        assert_eq!(m(&["control", "control"]), None);
        assert_eq!(m(&["control"]), None);
        assert_eq!(m(&["control", "option", "shift"]), None);
        assert_eq!(m(&["control", "hyper"]), None);
    }

    #[test]
    fn x11_modifier_state_maps_to_chord_bits() {
        assert_eq!(mods_from_x11(0x04 | 0x08), CONTROL | OPTION);
        assert_eq!(mods_from_x11(0x01 | 0x40), SHIFT | COMMAND);
        // Caps Lock (2), Num Lock (16) and AltGr (128) never count.
        assert_eq!(mods_from_x11(0x02 | 0x10 | 0x80), 0);
        assert_eq!(mods_from_x11(0x04 | 0x10), CONTROL);
    }

    #[test]
    fn a_held_key_that_is_not_a_modifier_counts() {
        let mut map = [0u8; 32];
        let modifiers = [37u8, 64, 0, 0, 50, 133];
        assert!(!other_key(&map, &modifiers));
        map[37 / 8] |= 1 << (37 % 8); // Control_L
        map[64 / 8] |= 1 << (64 % 8); // Alt_L
        assert!(!other_key(&map, &modifiers), "only the chord's own keys are down");
        map[39 / 8] |= 1 << (39 % 8); // S
        assert!(other_key(&map, &modifiers));
    }

    #[test]
    fn keys_are_only_watched_on_x11_sessions() {
        assert!(x11_session(Some("x11"), None, Some(":0")));
        assert!(x11_session(None, None, Some(":99")), "Xvfb sets no session type");
        assert!(!x11_session(Some("wayland"), Some("wayland-0"), Some(":0")), "XWayland's DISPLAY doesn't make it X11");
        assert!(!x11_session(None, Some("wayland-0"), Some(":0")));
        assert!(!x11_session(Some("Wayland"), None, Some(":0")));
        assert!(!x11_session(Some("tty"), None, None));
        assert!(!x11_session(Some("x11"), Some(""), Some("")));
    }
}
