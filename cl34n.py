#!/usr/bin/env python3
"""
FRAME-PERFECT Single-Pass Music Removal (Keep Non-Music Sounds)
Ensures perfect frame alignment for DaVinci Resolve compatibility
Uses direct MDX-NET ONNX inference -- no PyTorch, no audio-separator.

KEY DIFFERENCE: This removes ONLY music, keeping:
✓ Vocals
✓ Sound effects (baseball hitting, gunshots, etc)
✓ Ambient noise
✓ Dialogue
✓ All non-musical audio

OUTPUTS:
✓ input_music_removed.mp4  -- original video with music-removed audio
✓ input_vocals.wav          -- vocals / speech / SFX as standalone WAV
✓ input_instrumental.wav    -- isolated music track as standalone WAV

REQUIREMENTS:
✓ NVIDIA GPU with CUDA 12.x drivers
✓ onnxruntime-gpu, numpy, soundfile, librosa  (~300 MB total, no PyTorch)
✓ FFmpeg on PATH (or installed by install.ps1)
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

def _fmt_eta(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"

def get_video_info(video_file):
    cmd = [
        "ffprobe", "-v", "quiet", "-select_streams", "v:0", 
        "-show_entries", "stream=r_frame_rate,duration,nb_frames",
        "-show_entries", "format=duration",
        "-show_entries", "stream=sample_rate:stream_tags=language",
        "-of", "json", str(video_file)
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        
        frame_rate_str = info['streams'][0]['r_frame_rate']
        fps_num, fps_den = map(int, frame_rate_str.split('/'))
        fps = fps_num / fps_den
        
        duration = float(info['format']['duration'])
        
        try:
            nb_frames = int(info['streams'][0]['nb_frames'])
        except (KeyError, ValueError):
            nb_frames = int(duration * fps)
        
        audio_sample_rate = 48000
        for stream in info.get('streams', []):
            if stream.get('codec_type') == 'audio' and 'sample_rate' in stream:
                audio_sample_rate = int(stream['sample_rate'])
                break
        
        return {
            'fps': fps,
            'duration': duration,
            'frame_count': nb_frames,
            'frame_rate_str': frame_rate_str,
            'audio_sample_rate': audio_sample_rate
        }
        
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
        print(f"  Warning: Could not get complete video info: {e}")
        cmd_simple = ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_file)]
        duration = float(subprocess.check_output(cmd_simple, text=True).strip())
        return {
            'fps': 30.0,
            'duration': duration,
            'frame_count': int(duration * 30),
            'frame_rate_str': '30/1',
            'audio_sample_rate': 48000
        }

def _models_dir(base_dir):
    """Return the models/ folder next to the script, then AppData fallback."""
    local = (base_dir or Path.cwd()) / 'models'
    if local.exists():
        return local
    return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'CL34N' / 'models'


def run_mdx_separation(audio_file, temp_dir, base_dir=None, model_path=None):
    """
    Separate audio into vocals and instrumental using direct MDX-NET ONNX inference.
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

def _draw_bar(label, pct, width=24):
    filled = int(width * pct / 100)
    bar    = '#' * filled + '-' * (width - filled)
    print(f'  {label}  [{bar}]  {pct:3}%', end='\r', flush=True)

def run_ffmpeg_with_progress(cmd, total_duration_seconds, step_name, overall_start_time=None):
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
        print(f'\n  Error: ffmpeg failed.')
        return False
    except Exception as e:
        print(f'\n  Error: {e}')
        return False

def single_pass_music_removal(video_file, video_info, base_dir, model_path=None,
                              primary_label='output', secondary_label='leftovers'):
    temp_audio = None
    temp_dir   = None

    try:
        temp_audio = base_dir / 'temp_full_audio.flac'
        cmd_extract = [
            'ffmpeg', '-i', str(video_file),
            '-vn', '-acodec', 'flac',
            '-ar', str(video_info['audio_sample_rate']),
            '-ac', '2',
            '-avoid_negative_ts', 'make_zero',
            '-y', str(temp_audio)
        ]

        print('  Extracting audio...', end='\r', flush=True)
        if not run_ffmpeg_with_progress(cmd_extract, video_info['duration'], '[1/2] Extracting audio'):
            return []

        temp_dir = base_dir / 'temp_separation_output'
        temp_dir.mkdir(exist_ok=True)

        vocals_file, instr_file = run_mdx_separation(
            temp_audio, temp_dir, base_dir=base_dir, model_path=model_path
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

    base_dir = Path(Path(__file__).resolve().parent)
    mdir     = _models_dir(base_dir)

    # ── Task / model selection ────────────────────────────────────────────────
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
    video_info = get_video_info(video_file)
    start = time.time()

    stems = single_pass_music_removal(video_file, video_info, base_dir=base_dir,
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