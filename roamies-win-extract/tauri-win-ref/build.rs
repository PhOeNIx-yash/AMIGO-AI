// On macOS, compiles the Swift helpers into the sidecar paths Tauri bundles (externalBin):
// roamies-voice (talk chord + speech) and roamies-hands (window capture, reading and controlling apps).
fn main() {
    #[cfg(target_os = "macos")]
    {
        build_swift_helper("voice");
        build_swift_helper("hands");
    }
    tauri_build::build()
}

#[cfg(target_os = "macos")]
fn build_swift_helper(name: &str) {
    use std::path::PathBuf;
    use std::process::Command;
    println!("cargo:rerun-if-changed={name}");
    println!("cargo:rerun-if-changed=shared");
    let target = std::env::var("TARGET").expect("cargo sets TARGET");
    let arch = if target.starts_with("aarch64") { "arm64" } else { "x86_64" };
    let dir = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("cargo sets this")).join("binaries");
    std::fs::create_dir_all(&dir).expect("create binaries dir");
    let out = dir.join(format!("roamies-{name}-{target}"));
    let status = Command::new("swiftc")
        .args(["-O", "-swift-version", "5", "-target", &format!("{arch}-apple-macos11.0")])
        // Newer than macOS 11: loaded only where it exists (used behind #available).
        .args(["-Xlinker", "-weak_framework", "-Xlinker", "ScreenCaptureKit", "-o"])
        .arg(&out)
        .args(sources(name))
        .status()
        .expect("swiftc not found (install the Xcode Command Line Tools)");
    assert!(status.success(), "compiling the {name} helper failed");
}

/// Every .swift file in the helper's folder, main.swift first (it holds the top-level code), then the shared ones.
#[cfg(target_os = "macos")]
fn sources(name: &str) -> Vec<std::path::PathBuf> {
    let mut files: Vec<_> = std::fs::read_dir(name)
        .expect("helper folder")
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "swift"))
        .collect();
    files.sort_by_key(|p| (p.file_name() != Some(std::ffi::OsStr::new("main.swift")), p.clone()));
    let mut shared: Vec<_> = std::fs::read_dir("shared")
        .expect("shared helper folder")
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "swift"))
        .collect();
    shared.sort();
    files.extend(shared);
    files
}
