#!/usr/bin/env python3
"""
cl34n.py - Audio stem separator.

Extracts audio from a video or audio file, runs MDX-NET ONNX inference,
and writes two 16-bit WAV stems next to the original file.
"""

import os
import subprocess
from pathlib import Path
import time
import json
import sys
import shutil

# Embedded Python (._pth) does not add the script directory to sys.path.
# Insert it explicitly so mdx_infer.py / model_registry.py are importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_registry import pick_task, is_registered

# ── Auto-updater ──────────────────────────────────────────────────────────────

_GITHUB_API = 'https://api.github.com/repos/etwell/cl34n/commits/main'
_GITHUB_RAW = 'https://raw.githubusercontent.com/etwell/cl34n/main'


def _check_update():
    """
    Fetch the latest commit SHA from GitHub. If newer:
      1. Download manifest.json to get the full file + package list.
      2. Download all listed files, install any new packages.
      3. Restart with the updated code.
    Silent on any network or install failure.
    """
    import urllib.request
    import json
    import subprocess

    app_dir   = Path(__file__).resolve().parent
    ver_file  = app_dir / 'version.txt'
    local_sha = ver_file.read_text().strip() if ver_file.exists() else ''

    try:
        req = urllib.request.Request(
            _GITHUB_API,
            headers={'Accept': 'application/vnd.github.sha', 'User-Agent': 'cl34n-updater'},
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            remote_sha = resp.read().decode().strip()
    except Exception:
        return  # no internet or GitHub down — run normally

    if remote_sha == local_sha:
        return

    print('  Updating CL34N...', end='\r', flush=True)

    files     = []
    tmp_files = []
    try:
        req = urllib.request.Request(
            f'{_GITHUB_RAW}/manifest.json',
            headers={'User-Agent': 'cl34n-updater'},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            manifest = json.loads(resp.read())

        files    = manifest.get('files', [])
        packages = manifest.get('packages', [])

        for fname in files:
            tmp = app_dir / (fname + '.new')
            req = urllib.request.Request(
                f'{_GITHUB_RAW}/{fname}',
                headers={'User-Agent': 'cl34n-updater'},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                tmp.write_bytes(resp.read())
            tmp_files.append((tmp, app_dir / fname))

        if packages:
            subprocess.check_call(
                [sys.executable, '-m', 'pip', 'install', '--quiet',
                 '--no-warn-script-location', *packages],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        for tmp, dest in tmp_files:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp.replace(dest)

        ver_file.write_text(remote_sha)
        print('  Updated.          ')

        subprocess.Popen([sys.executable] + sys.argv)
        sys.exit(0)

    except Exception:
        for tmp, _ in tmp_files:
            if tmp.exists():
                tmp.unlink()


# ─────────────────────────────────────────────────────────────────────────────

def get_file_info(path):
    """Return duration (seconds) and audio sample rate for any media file."""
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-show_entries', 'format=duration',
        '-show_entries', 'stream=sample_rate,codec_type',
        '-of', 'json', str(path)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info   = json.loads(result.stdout)
        duration = float(info['format']['duration'])
        sample_rate = 48000
        for stream in info.get('streams', []):
            if stream.get('codec_type') == 'audio' and 'sample_rate' in stream:
                sample_rate = int(stream['sample_rate'])
                break
        return {'duration': duration, 'audio_sample_rate': sample_rate}
    except Exception:
        cmd_simple = ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                      '-of', 'csv=p=0', str(path)]
        duration = float(subprocess.check_output(cmd_simple, text=True).strip())
        return {'duration': duration, 'audio_sample_rate': 48000}


def _models_dir(base_dir):
    """Return the models/ folder next to the script, falling back to AppData."""
    local = (base_dir or Path.cwd()) / 'models'
    if local.exists():
        return local
    return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'CL34N' / 'models'


def _draw_bar(label, pct, width=24):
    filled = int(width * pct / 100)
    bar    = '#' * filled + '-' * (width - filled)
    print(f'  {label}  [{bar}]  {pct:3}%', end='\r', flush=True)


def run_ffmpeg_with_progress(cmd, total_duration_seconds, step_name):
    progress_cmd = cmd[:1] + ['-progress', '-', '-nostats'] + cmd[1:]
    last_pct = -1
    try:
        process = subprocess.Popen(
            progress_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, universal_newlines=True
        )
        for line in process.stdout:
            if line.startswith('out_time_ms') and total_duration_seconds:
                try:
                    ms  = int(line.split('=')[1])
                    pct = min(100, int((ms / 1_000_000) / total_duration_seconds * 100))
                    if pct != last_pct:
                        _draw_bar(step_name, pct)
                        last_pct = pct
                except ValueError:
                    pass
        process.wait()
        if last_pct >= 0:
            print()
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, progress_cmd)
        return True
    except subprocess.CalledProcessError:
        print('\n  Error: ffmpeg failed.')
        return False
    except Exception as e:
        print(f'\n  Error: {e}')
        return False


def run_mdx_separation(audio_file, temp_dir, model_path=None):
    """
    Run MDX-NET inference on audio_file and write vocals + instrumental to temp_dir.
    Returns (vocals_path, instrumental_path) or (None, None) on failure.
    """
    if audio_file.stat().st_size < 1024:
        print('  Error: audio file is too small.')
        return None, None

    try:
        import onnxruntime as ort
        if 'CUDAExecutionProvider' not in ort.get_available_providers():
            print('  Error: NVIDIA GPU with CUDA not found.')
            print('  Install drivers from nvidia.com/drivers and retry.')
            return None, None
    except ImportError:
        print('  Error: onnxruntime-gpu is not installed.')
        return None, None

    try:
        from mdx_infer import run_mdx_separation as _infer
    except ImportError:
        print('  Error: mdx_infer.py not found.')
        return None, None

    vocals_out = temp_dir / 'vocals.wav'
    instr_out  = temp_dir / 'instrumental.wav'

    print('  Loading model...', end='\r', flush=True)

    def _progress(pct):
        _draw_bar('[2/2] Processing', pct)

    try:
        _infer(
            audio_path=audio_file,
            vocals_path=vocals_out,
            instrumental_path=instr_out,
            model_path=model_path,
            progress_callback=_progress,
        )
        print()
        return vocals_out, instr_out
    except Exception as e:
        print(f'\n  Error: {e}')
        return None, None


def single_pass_music_removal(video_file, file_info, base_dir, model_path=None,
                              primary_label='output', secondary_label='leftovers'):
    temp_audio = None
    temp_dir   = None

    try:
        temp_audio = base_dir / 'temp_full_audio.flac'
        cmd_extract = [
            'ffmpeg', '-i', str(video_file),
            '-vn', '-acodec', 'flac',
            '-ar', str(file_info['audio_sample_rate']),
            '-ac', '2',
            '-avoid_negative_ts', 'make_zero',
            '-y', str(temp_audio)
        ]

        if not run_ffmpeg_with_progress(cmd_extract, file_info['duration'], '[1/2] Extracting audio'):
            return []

        temp_dir = base_dir / 'temp_separation_output'
        temp_dir.mkdir(exist_ok=True)

        vocals_file, instr_file = run_mdx_separation(
            temp_audio, temp_dir, model_path=model_path
        )

        if not vocals_file or not vocals_file.exists():
            print('  Error: separation produced no output.')
            return []

        stem1_dest = video_file.with_name(f'{video_file.stem}_{primary_label}.wav')
        stem2_dest = video_file.with_name(f'{video_file.stem}_{secondary_label}.wav')
        shutil.copy2(str(vocals_file), str(stem1_dest))
        if instr_file and instr_file.exists():
            shutil.copy2(str(instr_file), str(stem2_dest))

        if temp_audio and temp_audio.exists():
            temp_audio.unlink(missing_ok=True)
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        return [stem1_dest, stem2_dest] if (instr_file and instr_file.exists()) else [stem1_dest]

    except Exception as e:
        print(f'  Error: {e}')
        if temp_audio and temp_audio.exists():
            temp_audio.unlink(missing_ok=True)
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        return []


def main():
    import argparse
    parser = argparse.ArgumentParser(description='CL34N -- audio stem separator')
    parser.add_argument('input', help='Input video or audio file')
    parser.add_argument('--model', metavar='FILENAME',
                        help='Skip the picker and use this model file directly.')
    args = parser.parse_args()

    _check_update()

    base_dir = Path(__file__).resolve().parent
    mdir     = _models_dir(base_dir)

    if args.model:
        model_path      = mdir / args.model
        primary_label   = 'isolated_vocals' if 'Vocal' in args.model or 'Kim' in args.model else 'isolated_instrumental'
        secondary_label = 'leftovers'
        if not is_registered(model_path):
            print(f'  Unknown model: {args.model}')
            print(f'  Available: Kim_Vocal_2.onnx, UVR-MDX-NET-Inst_HQ_5.onnx')
            return
        if not model_path.exists():
            print(f'  Model not found: {model_path}')
            return
    else:
        result = pick_task(mdir)
        if result is None:
            return
        model_path, primary_label, secondary_label = result

    video_file = Path(args.input).expanduser().resolve()

    if not video_file.exists():
        print(f'  File not found: {video_file}')
        return
    if video_file.is_dir():
        print('  Input is a folder, not a file.')
        return
    if video_file.suffix.lower() not in {'.mp4', '.mp3', '.mkv', '.mov', '.wav', '.flac', '.m4a'}:
        print('  Unsupported file type. Supported: mp4 mkv mov avi mp3 wav m4a flac')
        return

    print(f'\n  File: {video_file.name}')
    file_info = get_file_info(video_file)
    start     = time.time()

    stems = single_pass_music_removal(video_file, file_info, base_dir=base_dir,
                                      model_path=model_path,
                                      primary_label=primary_label,
                                      secondary_label=secondary_label)
    if not stems:
        print('\n  Something went wrong. Check the output above.')
        return

    elapsed = int(time.time() - start)
    mins, secs = divmod(elapsed, 60)
    print(f'\n  Done in {mins}m {secs:02d}s\n')
    for s in stems:
        if s and s.exists():
            print(f'  {s.name}')


if __name__ == "__main__":
    main()
