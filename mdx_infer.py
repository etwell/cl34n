"""
mdx_infer.py - Direct MDX-NET ONNX inference.

Implements the full MDX-NET pipeline:
  STFT -> chunked GPU inference -> overlap-add ISTFT -> write vocals + instrumental
"""

from pathlib import Path

import numpy as np
import soundfile as sf
import librosa
import onnxruntime as ort

# ---------------------------------------------------------------------------
# MDX-NET parameters (apply to all MDX-NET ONNX models)
# ---------------------------------------------------------------------------
HOP_LENGTH  = 1024
TARGET_SR   = 44100
DIM_T       = 256    # time frames per inference chunk
OVERLAP     = 0.75   # chunk overlap ratio  -> step = DIM_T * 0.25 = 64


# ---------------------------------------------------------------------------
# Model helper
# ---------------------------------------------------------------------------

def ensure_model(model_path):
    """Verify the model file exists and return its Path."""
    path = Path(model_path)
    if not path.exists():
        raise FileNotFoundError(
            f'Model not found: {path}\n'
            f'Copy the .onnx file into: {path.parent}'
        )
    return path


# ---------------------------------------------------------------------------
# STFT / ISTFT helpers (pure numpy + librosa)
# ---------------------------------------------------------------------------

def _stft(audio, n_fft, hop_length):
    """Return [channels, n_bins, T] complex spectrogram."""
    return np.stack([
        librosa.stft(audio[c], n_fft=n_fft, hop_length=hop_length,
                     window='hann', center=True)
        for c in range(audio.shape[0])
    ], axis=0)


def _istft(spec, hop_length):
    """Inverse STFT.  spec is [channels, n_bins, T] complex -> [channels, samples]."""
    return np.stack([
        librosa.istft(spec[c], hop_length=hop_length,
                      window='hann', center=True)
        for c in range(spec.shape[0])
    ], axis=0)


# ---------------------------------------------------------------------------
# Main separation function
# ---------------------------------------------------------------------------

def run_mdx_separation(
    audio_path,
    vocals_path,
    instrumental_path,
    model_path=None,
    progress_callback=None,
):
    """
    Separate vocals from music using MDX-NET (ONNX, GPU).

    Parameters
    ----------
    audio_path          : path to input audio (any format soundfile can read)
    vocals_path         : output path for the vocals / non-music stem  (WAV)
    instrumental_path   : output path for the instrumental / music stem (WAV)
    model_path          : path to the .onnx model file
    progress_callback   : optional callable(int 0-100) for progress updates

    Returns
    -------
    (Path(vocals_path), Path(instrumental_path))
    """

    # ------------------------------------------------------------------
    # 1. Model
    # ------------------------------------------------------------------
    resolved_model = ensure_model(model_path)

    available = ort.get_available_providers()
    if 'CUDAExecutionProvider' not in available:
        raise RuntimeError(
            'CUDAExecutionProvider not available.\n'
            'Install CUDA 13.x from nvidia.com/drivers, then retry.'
        )
    providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']

    sess_opts = ort.SessionOptions()
    sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session    = ort.InferenceSession(str(resolved_model), sess_options=sess_opts, providers=providers)
    input_name = session.get_inputs()[0].name

    # Read model dimensions from the ONNX input shape.
    # N_FFT is always dim_f * 2 for MDX-NET models (handles both 6144 and 7680 variants).
    raw_shape   = session.get_inputs()[0].shape
    dim_f_model = int(raw_shape[2]) if isinstance(raw_shape[2], int) and raw_shape[2] > 0 else 3072
    dim_t_model = int(raw_shape[3]) if isinstance(raw_shape[3], int) and raw_shape[3] > 0 else DIM_T
    n_fft_model = dim_f_model * 2

    # ------------------------------------------------------------------
    # 2. Load audio
    # ------------------------------------------------------------------
    audio, sr = sf.read(str(audio_path), always_2d=True)
    audio = audio.T.astype(np.float32)          # [C, samples]

    if audio.shape[0] == 1:                      # mono -> stereo
        audio = np.tile(audio, (2, 1))

    if sr != TARGET_SR:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=TARGET_SR,
                                 res_type='soxr_hq')

    orig_len = audio.shape[1]

    # ------------------------------------------------------------------
    # 3. STFT  ->  [4, dim_f, T]  (real + imag of both channels)
    # ------------------------------------------------------------------
    spec   = _stft(audio, n_fft_model, HOP_LENGTH)                    # [2, n_bins, T] complex
    n_bins = spec.shape[1]
    tensor = np.concatenate([spec.real, spec.imag], axis=0).astype(np.float32)  # [4, n_bins, T]
    tensor = tensor[:, :dim_f_model, :]                                # [4, dim_f, T]
    T      = tensor.shape[2]

    # ------------------------------------------------------------------
    # 4. Chunked overlap-add inference
    # ------------------------------------------------------------------
    pad        = dim_t_model // 2
    tensor_pad = np.pad(tensor, ((0, 0), (0, 0), (pad, pad + dim_t_model)),
                        mode='reflect')

    out_pad = np.zeros_like(tensor_pad)
    weight  = np.zeros(tensor_pad.shape[2], dtype=np.float32)
    win     = np.hanning(dim_t_model).astype(np.float32)

    step      = max(1, int(dim_t_model * (1.0 - OVERLAP)))
    positions = list(range(0, tensor_pad.shape[2] - dim_t_model + 1, step))
    n_chunks  = len(positions)

    report_every = max(1, n_chunks // 100)
    for i, s in enumerate(positions):
        chunk = tensor_pad[:, :, s:s + dim_t_model][np.newaxis]        # [1, 4, dim_f, dim_t]
        out   = session.run(None, {input_name: chunk})[0][0]           # [4, dim_f, dim_t]
        out  *= win                                                     # hann window
        out_pad[:, :, s:s + dim_t_model] += out
        weight[s:s + dim_t_model]         += win
        if progress_callback and i % report_every == 0:
            progress_callback(int(100 * i / n_chunks))

    # Normalize by overlap weights
    weight  = np.maximum(weight, 1e-8)
    out_pad /= weight

    # Trim padding back to original length
    out = out_pad[:, :, pad:pad + T]                                   # [4, dim_f, T]

    # ------------------------------------------------------------------
    # 5. Reconstruct complex spectrogram  ->  ISTFT
    # ------------------------------------------------------------------
    vocals_spec = out[:2] + 1j * out[2:]                               # [2, dim_f, T]

    # Pad frequency axis back to full n_bins (zeros for high-freq bins)
    if n_bins > dim_f_model:
        pad_bins    = n_bins - dim_f_model
        vocals_spec = np.concatenate(
            [vocals_spec, np.zeros((2, pad_bins, T), dtype=complex)], axis=1
        )

    vocals_audio = _istft(vocals_spec, HOP_LENGTH)
    vocals_audio = vocals_audio[:, :orig_len]

    # Instrumental = original mix - vocals
    min_len            = min(audio.shape[1], vocals_audio.shape[1])
    instrumental_audio = audio[:, :min_len] - vocals_audio[:, :min_len]
    vocals_audio       = vocals_audio[:, :min_len]

    # Hard-clip to prevent clipping artifacts on save
    vocals_audio       = np.clip(vocals_audio,       -1.0, 1.0)
    instrumental_audio = np.clip(instrumental_audio, -1.0, 1.0)

    # ------------------------------------------------------------------
    # 6. Save both stems
    # ------------------------------------------------------------------
    sf.write(str(vocals_path), vocals_audio.T, TARGET_SR, subtype='FLOAT')
    sf.write(str(instrumental_path), instrumental_audio.T, TARGET_SR, subtype='FLOAT')

    if progress_callback:
        progress_callback(100)

    return Path(vocals_path), Path(instrumental_path)
