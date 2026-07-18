"""Smallest check that fails if the GPU pipeline breaks."""
import os
import pathlib

import truststore
truststore.inject_into_ssl()
import nvidia.cublas, nvidia.cudnn
for pkg in (nvidia.cublas, nvidia.cudnn):
    d = str(pathlib.Path(pkg.__path__[0]) / "bin")
    os.add_dll_directory(d)
    os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]

import numpy as np
from faster_whisper import WhisperModel

m = WhisperModel("small", device="cuda", compute_type="int8_float16")
segs, info = m.transcribe(np.zeros(16000, dtype=np.float32), task="translate")
list(segs)
print("GPU OK")
