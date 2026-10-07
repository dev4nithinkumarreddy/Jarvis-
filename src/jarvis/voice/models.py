"""Piper neural voice model downloader and manager for J.A.R.V.I.S."""

from __future__ import annotations

import logging
from pathlib import Path
import sys
import urllib.request
import yaml

logger = logging.getLogger(__name__)

# Official Piper repository links for authentic British Butler voices
VOICE_MODELS = {
    "alan": {
        "name": "en_GB-alan-medium (British Butler / Bettany Cadence)",
        "onnx_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alan/medium/en_GB-alan-medium.onnx",
        "json_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json",
        "onnx_filename": "en_GB-alan-medium.onnx",
        "json_filename": "en_GB-alan-medium.onnx.json",
    },
    "aru": {
        "name": "en_GB-aru-medium (Calm British English)",
        "onnx_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/aru/medium/en_GB-aru-medium.onnx",
        "json_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/aru/medium/en_GB-aru-medium.onnx.json",
        "onnx_filename": "en_GB-aru-medium.onnx",
        "json_filename": "en_GB-aru-medium.onnx.json",
    },
}

DEFAULT_MODEL_KEY = "alan"


def download_file_with_progress(url: str, dest_path: Path) -> None:
    """Download a file with console progress reporting."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")

    print(f"Downloading {dest_path.name} from Hugging Face...")

    def reporthook(block_num: int, block_size: int, total_size: int) -> None:
        if total_size > 0:
            downloaded = block_num * block_size
            percent = min(100.0, (downloaded / total_size) * 100)
            mb_down = downloaded / (1024 * 1024)
            mb_tot = total_size / (1024 * 1024)
            sys.stdout.write(f"\r  Progress: {percent:5.1f}% [{mb_down:.1f} MB / {mb_tot:.1f} MB]")
            sys.stdout.flush()

    try:
        urllib.request.urlretrieve(url, str(tmp_path), reporthook=reporthook)
        print("\n  Download complete.")
        if tmp_path.exists():
            if dest_path.exists():
                dest_path.unlink()
            tmp_path.rename(dest_path)
    except Exception as exc:
        if tmp_path.exists():
            tmp_path.unlink()
        raise RuntimeError(f"Failed to download '{url}': {exc}") from exc


def setup_piper_voice(
    voice_key: str = DEFAULT_MODEL_KEY,
    models_dir: Path | str = "models/piper",
    config_path: Path | str = "config.yaml",
) -> tuple[Path, Path]:
    """Download Piper neural voice model and update config.yaml.

    Returns:
        tuple[Path, Path]: (onnx_path, json_path)
    """
    voice_info = VOICE_MODELS.get(voice_key.lower())
    if not voice_info:
        raise ValueError(f"Unknown voice key '{voice_key}'. Available: {list(VOICE_MODELS.keys())}")

    base_dir = Path(models_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    onnx_path = base_dir / voice_info["onnx_filename"]
    json_path = base_dir / voice_info["json_filename"]

    # 1. Download ONNX model file if not present
    if not onnx_path.exists() or onnx_path.stat().st_size < 1024 * 1024:
        download_file_with_progress(voice_info["onnx_url"], onnx_path)
    else:
        print(f"  Model already present: {onnx_path}")

    # 2. Download JSON phoneme config if not present
    if not json_path.exists() or json_path.stat().st_size < 100:
        download_file_with_progress(voice_info["json_url"], json_path)
    else:
        print(f"  Phoneme map already present: {json_path}")

    # 3. Update config.yaml to activate Piper
    cfg_file = Path(config_path)
    if cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                cfg_data = yaml.safe_load(f) or {}

            if "voice" not in cfg_data:
                cfg_data["voice"] = {}

            cfg_data["voice"]["tts_engine"] = "piper"
            cfg_data["voice"]["piper_model_path"] = str(onnx_path).replace("\\", "/")
            cfg_data["voice"]["piper_config_path"] = str(json_path).replace("\\", "/")

            with open(cfg_file, "w", encoding="utf-8") as f:
                yaml.dump(cfg_data, f, sort_keys=False, default_flow_style=False)

            print(f"  Configuration updated in {cfg_file}: tts_engine='piper'")
        except Exception as exc:
            logger.warning(f"Could not automatically update config.yaml: {exc}")

    return onnx_path, json_path
