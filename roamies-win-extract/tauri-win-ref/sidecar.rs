// The Node sidecar (the agent): finding Node, starting it and reading its messages.

use super::*;
use std::io::{BufRead, BufReader};

fn find_node() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("ROAMIES_NODE") {
        return Some(PathBuf::from(p));
    }
    // The Node.js shipped inside the app (bundle.externalBin), so users don't need Node installed.
    if let Some(p) = helper_binary("node") {
        return Some(p);
    }
    // GUI apps don't get the shell's PATH on macOS, so check the usual installs too.
    let exe = if cfg!(windows) { "node.exe" } else { "node" };
    let mut dirs: Vec<PathBuf> = std::env::var_os("PATH").map(|p| std::env::split_paths(&p).collect()).unwrap_or_default();
    for d in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "C:\\Program Files\\nodejs"] {
        dirs.push(PathBuf::from(d));
    }
    dirs.into_iter().map(|d| d.join(exe)).find(|p| p.is_file())
}

fn sidecar_script(app: &AppHandle) -> Option<PathBuf> {
    let bundled = app.path().resource_dir().map(|d| d.join("sidecar").join("sidekick.mjs"));
    match bundled {
        Ok(p) if p.is_file() => Some(p),
        #[cfg(debug_assertions)]
        _ => Some(PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../sidecar/sidekick.mjs"))),
        #[cfg(not(debug_assertions))]
        _ => None,
    }
}

// Set once the app is quitting: the sidecar then exits on purpose and must not be restarted.
pub(crate) static QUITTING: AtomicBool = AtomicBool::new(false);
// Bumped per spawn, so a process replaced by a manual restart doesn't trigger an automatic one.
static GENERATION: AtomicUsize = AtomicUsize::new(0);
static CRASHES: Mutex<Vec<Instant>> = Mutex::new(Vec::new());
const BACKOFF_SECS: [u64; 3] = [1, 3, 10];

pub(crate) fn spawn_sidecar(app: &AppHandle) {
    let Some(node) = find_node() else {
        fallback_menu(app, "Roamies is missing a part; reinstall it");
        return;
    };
    let Some(script) = sidecar_script(app) else {
        fallback_menu(app, "Roamies is missing a part; reinstall it");
        return;
    };
    let generation = GENERATION.fetch_add(1, Ordering::SeqCst) + 1;
    let stderr = log_stdio("sidekick.log");
    let mut cmd = Command::new(node);
    hide_console(&mut cmd);
    cmd.arg(&script)
        .env("ROAMIES_VERSION", app.package_info().version.to_string())
        .env("ROAMIES_HANDS", helper_binary("roamies-hands").map(|p| p.to_string_lossy().into_owned()).unwrap_or_default())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(stderr);
    // Linux: "x11" or "wayland", for what the sidecar offers and how it words things (platform.mjs).
    #[cfg(target_os = "linux")]
    cmd.env("ROAMIES_SESSION", crate::linux::session().name()).env("ROAMIES_LAUNCH", crate::linux::launch_command());
    if let Some(dir) = script.parent() {
        cmd.current_dir(dir);
    }
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            fallback_menu(app, &format!("Roamies failed to start: {e}"));
            return;
        }
    };
    *app.state::<Sidecar>().0.lock().unwrap() = child.stdin.take();
    let stdout = child.stdout.take().expect("piped stdout");
    let app = app.clone();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let Ok(msg) = serde_json::from_str::<Value>(&line) else { continue };
            let app2 = app.clone();
            let _ = app.run_on_main_thread(move || handle_sidecar(&app2, msg));
        }
        let _ = child.wait();
        *app.state::<Sidecar>().0.lock().unwrap() = None;
        if QUITTING.load(Ordering::SeqCst) || GENERATION.load(Ordering::SeqCst) != generation {
            return;
        }
        let attempt = {
            let mut crashes = CRASHES.lock().unwrap();
            crashes.retain(|t| t.elapsed() < Duration::from_secs(300));
            crashes.push(Instant::now());
            crashes.len()
        };
        let app2 = app.clone();
        if attempt > BACKOFF_SECS.len() {
            let _ = app.run_on_main_thread(move || fallback_menu(&app2, "Roamies stopped"));
            return;
        }
        thread::sleep(Duration::from_secs(BACKOFF_SECS[attempt - 1]));
        if QUITTING.load(Ordering::SeqCst) || GENERATION.load(Ordering::SeqCst) != generation {
            return;
        }
        let _ = app.run_on_main_thread(move || spawn_sidecar(&app2));
    });
}
