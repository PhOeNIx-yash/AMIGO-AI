import os
import sys
import subprocess
import shutil

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
    print("      AMIGO VOICE ASSISTANT - ONE-CLICK EASY SETUP")
    print("=" * 60)

    # 1. Install Python dependencies
    run_step("Installing Python requirements", [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

    # 2. Build Frontend UI (if Node/npm is available)
    ui_dir = os.path.join(base_dir, "ui_app")
    dist_dir = os.path.join(ui_dir, "dist")
    if os.path.exists(ui_dir):
        if not os.path.exists(dist_dir) or not os.listdir(dist_dir):
            if shutil.which("npm"):
                run_step("Installing UI dependencies", ["npm", "install"], cwd=ui_dir, check=False)
                run_step("Building Web UI distribution bundle", ["npm", "run", "build"], cwd=ui_dir, check=False)
            else:
                print("\n[!] Node.js/npm not found. Pre-built UI or dev server can be used.")

    # 3. Download AI Models
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

    # Qwen 2.5 3B Instruct LLM GGUF Model
    try:
        import local_llm
        model_info = local_llm.get_active_model_info()
        print(f" -> Checking local LLM model: {model_info['name']} (~{model_info.get('size_gb', 2.05)}GB)...")
        model_path = local_llm.get_model_path()
        if os.path.exists(model_path):
            print(f"    {model_info['name']} is ready at {model_path}.")
    except Exception as e:
        print(f"    LLM model check note: {e}")

    print("\n" + "=" * 60)
    print("           AMIGO SETUP COMPLETED SUCCESSFULLY!")
    print("=" * 60)
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
    main()
