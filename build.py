#!/usr/bin/env python3
import os
import sys
import subprocess
import platform
import shutil
import logging
import importlib.util

APP_NAME = "ZebraFET"
ENTRY_POINT = "main.py"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)

def check_dependency(name, import_name=None):
    """Check if a dependency can be imported and log version."""
    if import_name is None:
        import_name = name
    try:
        spec = importlib.util.find_spec(import_name)
        if spec is None:
            raise ImportError
        module = importlib.import_module(import_name)
        version = getattr(module, "__version__", "unknown")
        logging.info(f"✅ {name} found: {version}")
        return True
    except ImportError:
        logging.warning(f"⚠️ {name} not found.")
        return False

def clean_previous_builds():
    """Remove old dist/build folders before new build."""
    for folder in ("build", "dist", f"{APP_NAME}.egg-info", "__pycache__"):
        if os.path.exists(folder):
            shutil.rmtree(folder, ignore_errors=True)
            logging.info(f"🧹 Removed {folder}/")

def run_pyinstaller():
    """Build and run the PyInstaller command dynamically."""
    
    # Define data folders to include in the bundle
    datas = [
        ("resources", "resources"),
        ("src", "src"),
    ]
    data_args = []
    for src, dst in datas:
        if os.path.exists(src):
            data_args += ["--add-data", f"{src}{os.pathsep}{dst}"]

    # Define icons based on platform
    icon_win = "resources/icons/fishapp_icon.png" # Update to .ico later if compiling on Windows
    icon_mac = "resources/icons/fishapp_icon.icns"
    icon_path = icon_mac if sys.platform == "darwin" else icon_win
    icon_arg  = ["--icon", icon_path] if os.path.exists(icon_path) else []

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onedir",    # Creates a directory / .app bundle instead of a slow single file
        "--windowed",  # Hides the console and creates standard OS app bundle
        "--noconfirm",
        "--clean",
        # Hidden imports for scientific packages that might load dynamically
        "--hidden-import=pandas",
        "--hidden-import=numpy",
        "--hidden-import=matplotlib",
        "--hidden-import=markdown",
        # Force copy metadata to prevent version-check crashes in PyInstaller hooks
        "--copy-metadata=numpy",
        "--copy-metadata=pandas",
        *data_args,
        *icon_arg,
        ENTRY_POINT,
    ]

    logging.info(f"🚀 Running PyInstaller for '{APP_NAME}'...")
    logging.info("Command: " + " ".join(cmd))
    
    env = os.environ.copy()
    env["PYTHONWARNINGS"] = "ignore" 
    
    try:
        subprocess.run(cmd, check=True, env=env)
        logging.info("✅ Build completed successfully.")
    except subprocess.CalledProcessError as e:
        logging.error(f"❌ Build failed with exit code {e.returncode}")
        logging.error("="*60)
        logging.error("If the error traceback mentions 'numpy_version', 'packaging', or 'TypeError',")
        logging.error("your virtual environment's package metadata is corrupted.")
        logging.error("To fix this issue, please run the following command in your terminal:")
        logging.error("    pip install --force-reinstall numpy pyinstaller")
        logging.error("="*60)
        sys.exit(e.returncode)

def main():
    # Enforce absolute paths: Change working directory to where this build.py is located.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    logging.info(f"--- Starting build for {APP_NAME} on {platform.system()} ---")
    logging.info(f"📂 Working directory set to: {script_dir}")

    # Clean previous artifacts
    clean_previous_builds()

    # Check scientific dependencies
    deps = ["numpy", "pandas", "matplotlib", "PySide6", "markdown"]
    for dep in deps:
        check_dependency(dep)

    # Run the robust build process
    run_pyinstaller()

if __name__ == "__main__":
    main()