import torch
import torchaudio


def load_wav(wav_path: str, sample_rate: int):
    """Load wav file and resample to target sample rate.

    Args:
        wav_path (str): Path to wav file.
        sample_rate (int): Target sample rate.

    Returns:
        torch.Tensor: Waveform tensor with shape (1, T).
    """
    # local change: read with soundfile (newer torchaudio needs torchcodec + ffmpeg just to open a file)
    import soundfile as sf
    data, sr = sf.read(wav_path, dtype="float32", always_2d=True)
    waveform = torch.from_numpy(data.T.copy())
        
    if sr != sample_rate:
        waveform = torchaudio.functional.resample(waveform, sr, sample_rate)

    if len(waveform.shape) > 1 and waveform.shape[0] > 1:
        waveform = torch.mean(waveform, dim=0, keepdim=True)
    
    return waveform

        
