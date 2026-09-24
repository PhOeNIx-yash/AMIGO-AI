import os
import sys
import subprocess
import shutil
import json

def run_step(desc: str, cmd: list, cwd: str = None, check: bool = True):
    print(f"\n[+] {desc}...")
    try:
        subprocess.run(cmd, cwd=cwd, check=check)
    except Exception as e:
        print(f"    Warning: {desc} encountered an issue: {e}")

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

    print("=" * 60)
    print("   AMIGO VOICE ASSISTANT v1.3 — ONE-CLICK EASY SETUP")
    print("   System 1 (Laya Neural Router) + System 2 (MiniCPM 5 2B)")
    print("=" * 60)

    # 1. Check Python & Git Environment
    print(f"[+] Python runtime: {sys.version.split()[0]} ({sys.executable})")
    git_bin = shutil.which("git")
    if git_bin:
        print(f"    Git version control is ready: {git_bin}")
    else:
        print("    Notice: Git is not detected in PATH. To install Git via Winget, run:")
        print("    winget install --id Git.Git -e --source winget")

    # 2. Install Python dependencies
    run_step("Installing Python requirements", [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

    # 3. Build Frontend UI (if Node/npm is available)
    ui_dir = os.path.join(base_dir, "ui_app")
    dist_dir = os.path.join(ui_dir, "dist")
    if os.path.exists(ui_dir):
        if not os.path.exists(dist_dir) or not os.listdir(dist_dir):
            if shutil.which("npm"):
                run_step("Installing UI dependencies", ["npm", "install"], cwd=ui_dir, check=False)
                run_step("Building Web UI distribution bundle", ["npm", "run", "build"], cwd=ui_dir, check=False)
            else:
                print("\n[!] Node.js/npm not found. Pre-built UI or dev server can be used.")
        else:
            print(" -> Pre-built Web UI distribution is present and ready.")

    # 4. Create RAG Data Directories & Default Profile
    rag_dir = os.path.join(base_dir, "rag_data", "chroma")
    os.makedirs(rag_dir, exist_ok=True)

    profile_path = os.path.join(base_dir, "amigo_profile.json")
    if not os.path.exists(profile_path):
        default_profile = {
            "user_identity": {"name": "User", "preferred_title": ""},
            "system": {"installed_at": None, "last_active": None},
            "ui_settings": {
                "thinkingEnabled": False,
                "textAnimationStyle": "silk_blur",
                "colorTheme": "violet",
                "visualizerMode": "ribbon"
            },
            "active_state": {}
        }
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(default_profile, f, indent=2)
        print(" -> Initialized default amigo_profile.json.")

    # 5. Download AI Models
    print("\n[+] Downloading AI Models...")
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("    huggingface_hub is required. Installing...")
        subprocess.run([sys.executable, "-m", "pip", "install", "huggingface-hub"])
        from huggingface_hub import hf_hub_download

    # Kokoro ONNX TTS Models
    kokoro_dir = os.path.join(base_dir, "models", "kokoro-onnx")
    os.makedirs(kokoro_dir, exist_ok=True)
    kokoro_model = os.path.join(kokoro_dir, "kokoro-v1.0.onnx")
    kokoro_voices = os.path.join(kokoro_dir, "voices-v1.0.bin")

    if not os.path.exists(kokoro_model) or not os.path.exists(kokoro_voices):
        print(" -> Downloading Kokoro Neural TTS model & voices...")
        try:
            hf_hub_download(repo_id="hexgrad/Kokoro-82M", filename="kokoro-v1.0.onnx", local_dir=kokoro_dir)
            hf_hub_download(repo_id="hexgrad/Kokoro-82M", filename="voices-v1.0.bin", local_dir=kokoro_dir)
            print("    Kokoro TTS models downloaded successfully.")
        except Exception as e:
            print(f"    Kokoro model download note: {e}")
    else:
        print(" -> Kokoro Neural TTS models already present.")

    # Sherpa-ONNX Streaming STT Model Check
    try:
        from tts import get_stt_recognizer, is_stt_available
        print(" -> Checking Sherpa-ONNX Neural STT engine...")
        rec = get_stt_recognizer()
        if is_stt_available():
            print("    Sherpa-ONNX Streaming STT engine is ready.")
        else:
            print("    Sherpa-ONNX model will be automatically fetched on first voice input.")
    except Exception as e:
        print(f"    Sherpa-ONNX model check note: {e}")

    # Laya System 1 Neural Router Model Check
    laya_dir = os.path.join(base_dir, "models", "laya")
    laya_weights = os.path.join(laya_dir, "model.safetensors")
    laya_config = os.path.join(laya_dir, "rl_agent_config.json")
    os.makedirs(laya_dir, exist_ok=True)
    if os.path.exists(laya_weights) and os.path.getsize(laya_weights) > 500_000_000 and os.path.exists(laya_config):
        print(" -> Laya System 1 Neural Router: ACTIVE (model.safetensors + rl_agent_config.json found).")
    else:
        print(" -> Laya System 1 Neural Router: NOT FOUND (optional).")
        print("    To enable sub-second action routing, place Laya model files into:")
        print(f"    {laya_dir}")
        print("    Required files: model.safetensors  rl_agent_config.json")
        print("    Without Laya, Amigo falls back gracefully to MiniCPM 5 2B for all queries.")

    # Local LLM GGUF Model Check (MiniCPM 5 2B — System 2)
    try:
        import local_llm
        model_info = local_llm.get_active_model_info()
        print(f" -> Checking MiniCPM 5 2B (System 2): {model_info['name']} (~{model_info.get('size_gb', 1.45)}GB)...")
        model_path = local_llm.get_model_path()
        if os.path.exists(model_path):
            print(f"    {model_info['name']} is ready at {model_path}.")
        else:
            print(f" -> Downloading {model_info['name']} GGUF model...")
            local_llm.ensure_model_downloaded()
        # Vision Configuration Check
        print(" -> Screen Vision: Configured with native Windows Media OCR engine (winocr).")
    except Exception as e:
        print(f"    LLM model check note: {e}")

    print("\n" + "=" * 60)
    print("           AMIGO SETUP COMPLETED SUCCESSFULLY!")
    print("=" * 60)
    print("\nActive Engine Stack:")
    print("  System 1 — Laya Neural Router  : sub-second action classification")
    print("  System 2 — MiniCPM 5 2B        : conversational reasoning & Q&A")
    print("\nHow to launch Amigo:")
    print("  1. Web Dashboard (Recommended):")
    print(f'     {sys.executable} ui_server.py')
    print("     (Then open http://localhost:5000 in your browser)")
    print("\n  2. Terminal Voice & Type Mode:")
    print(f'     {sys.executable} "amigo main.py"')
    print("\n  3. Windows Batch Launcher:")
    print("     Double-click 'start_amigo.bat' in the project directory.")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    # If invoked via pip install -e ., run standard setuptools setup
    if len(sys.argv) > 1 and sys.argv[1] in ("egg_info", "dist_info", "bdist_wheel", "sdist", "develop"):
        try:
            from setuptools import setup, find_packages
            setup(
                name="amigo-assistant",
                version="1.3.0",
                description="Local Offline Agentic AI Voice Assistant for Windows",
                packages=find_packages(),
            )
        except Exception:
            main()
    else:
        main()

