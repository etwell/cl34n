"""
model_registry.py -- Task picker and model manager for CL34N.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path


_CDN = 'https://cdn.etwellstudio.com/models/'

_TASKS = [
    {
        'label':     'Isolate Vocals',
        'desc':      'Extract speech, dialogue, and vocals. Music is removed.',
        'model':     'Kim_Vocal_2.onnx',
        'primary':   'isolated_vocals',
        'secondary': 'leftovers',
        'url':       _CDN + 'Kim_Vocal_2.onnx',
        'sha256':    'ce74ef3b6a6024ce44211a07be9cf8bc6d87728cc852a68ab34eb8e58cde9c8b',
    },
    {
        'label':     'Isolate Instrumental',
        'desc':      'Extract the music track. Everything else is removed.',
        'model':     'UVR-MDX-NET-Inst_HQ_5.onnx',
        'primary':   'isolated_instrumental',
        'secondary': 'leftovers',
        'url':       _CDN + 'UVR-MDX-NET-Inst_HQ_5.onnx',
        'sha256':    '811cb24095d865763752310848b7ec86aeede0626cb05749ab35350e46897000',
    },
]

_REGISTERED = {t['model'] for t in _TASKS}
_SHA256_MAP  = {t['model']: t['sha256'] for t in _TASKS}


# ── Public helpers ─────────────────────────────────────────────────────────────

def is_registered(model_path: Path) -> bool:
    return model_path.name in _REGISTERED


def verify_model(model_path: Path) -> bool:
    """Return True if the file exists and its SHA256 matches the registry."""
    expected = _SHA256_MAP.get(model_path.name)
    if not expected or not model_path.exists():
        return False
    h = hashlib.sha256()
    with open(model_path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest() == expected


# ── Download ───────────────────────────────────────────────────────────────────

def _download(url: str, dest: Path, sha256: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.part')

    def _progress(received, total):
        if total > 0:
            pct = min(100, received * 100 // total)
            mb  = received / 1_048_576
            print(f'  Downloading...  {pct}%  ({mb:.0f} MB)', end='\r', flush=True)

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as resp:
            total    = int(resp.headers.get('Content-Length', 0))
            received = 0
            with open(tmp, 'wb') as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    _progress(received, total)
        print()  # end progress line

        # Verify integrity
        print('  Verifying...', end='\r', flush=True)
        h = hashlib.sha256()
        with open(tmp, 'rb') as f:
            for block in iter(lambda: f.read(1 << 20), b''):
                h.update(block)
        if h.hexdigest() != sha256:
            tmp.unlink()
            raise RuntimeError('File is corrupted (SHA256 mismatch). Please try again.')

        tmp.rename(dest)
        print('  Ready.       ')
    except RuntimeError:
        raise
    except Exception as e:
        if tmp.exists():
            tmp.unlink()
        raise RuntimeError(f'Download failed: {e}') from e


# ── Task picker ────────────────────────────────────────────────────────────────

def _draw_menu(sel: int, models_dir: Path, first: bool = False) -> None:
    """Redraw the task list in-place, highlighting the selected row with >."""
    n = len(_TASKS)
    if not first:
        sys.stdout.write(f'\033[{n}A')  # move cursor up n lines
    for i, task in enumerate(_TASKS):
        marker     = '>' if i == sel else ' '
        model_file = models_dir / task['model']
        status     = '  [download required]' if not model_file.exists() else ''
        line       = f'  {marker} {task["label"]:<26} {task["desc"]}{status}'
        sys.stdout.write('\r' + line.ljust(80) + '\n')
    sys.stdout.flush()


def pick_task(models_dir: Path):
    """
    Show an arrow-key task menu and return (model_path, primary_label, secondary_label),
    or None if the user quits. Downloads and verifies the model if needed.
    """
    import msvcrt

    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    sel = 0
    n   = len(_TASKS)

    print('\n  CL34N\n')
    print('  Use arrow keys to select, Enter to confirm, q to quit.\n')
    _draw_menu(sel, models_dir, first=True)

    while True:
        ch = msvcrt.getwch()

        if ch in ('\r', '\n'):      # Enter — confirm selection
            break
        elif ch in ('q', '\x1b'):   # q or Escape — quit
            print()
            return None
        elif ch in ('\x00', '\xe0'):  # extended key prefix (arrows, F-keys, etc.)
            ch2 = msvcrt.getwch()
            if ch2 == 'H':    # Up arrow
                sel = (sel - 1) % n
            elif ch2 == 'P':  # Down arrow
                sel = (sel + 1) % n
            else:
                continue

        _draw_menu(sel, models_dir)

    print()  # move past the menu

    task       = _TASKS[sel]
    model_path = models_dir / task['model']

    if not model_path.exists():
        try:
            confirm = input(f'\n  Model not downloaded yet (~200 MB). Download now? [y/N]: ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if confirm.lower() != 'y':
            return None
        try:
            _download(task['url'], model_path, task['sha256'])
        except RuntimeError as e:
            print(f'\n  Error: {e}')
            return None

    elif not verify_model(model_path):
        print(f'\n  Warning: {task["model"]} failed integrity check -- re-downloading.')
        try:
            model_path.unlink()
            _download(task['url'], model_path, task['sha256'])
        except RuntimeError as e:
            print(f'\n  Error: {e}')
            return None

    return model_path, task['primary'], task['secondary']
