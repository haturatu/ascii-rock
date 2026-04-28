"""Small ctypes wrapper for playing PCM WAV audio through PortAudio."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import threading
import time
import wave


PA_NO_ERROR = 0
PA_INT16 = 0x00000008
PA_NO_FLAG = 0
FRAMES_PER_BUFFER = 1024


class PortAudioError(RuntimeError):
    """Raised when PortAudio cannot complete an operation."""


class PaDeviceInfo(ctypes.Structure):
    _fields_ = [
        ("structVersion", ctypes.c_int),
        ("name", ctypes.c_char_p),
        ("hostApi", ctypes.c_int),
        ("maxInputChannels", ctypes.c_int),
        ("maxOutputChannels", ctypes.c_int),
        ("defaultLowInputLatency", ctypes.c_double),
        ("defaultLowOutputLatency", ctypes.c_double),
        ("defaultHighInputLatency", ctypes.c_double),
        ("defaultHighOutputLatency", ctypes.c_double),
        ("defaultSampleRate", ctypes.c_double),
    ]


class PaStreamParameters(ctypes.Structure):
    _fields_ = [
        ("device", ctypes.c_int),
        ("channelCount", ctypes.c_int),
        ("sampleFormat", ctypes.c_ulong),
        ("suggestedLatency", ctypes.c_double),
        ("hostApiSpecificStreamInfo", ctypes.c_void_p),
    ]


def _load_portaudio():
    candidates = []
    env_path = os.environ.get("ASCII_ROCK_PORTAUDIO")
    if env_path:
        candidates.append(env_path)

    lib_path = ctypes.util.find_library("portaudio")
    if lib_path:
        candidates.append(lib_path)

    candidates.extend(
        [
            "/usr/local/lib/libportaudio.so",
            "/usr/local/lib/libportaudio.so.2",
            "/usr/lib/libportaudio.so",
            "/usr/lib/libportaudio.so.2",
            "/usr/lib/x86_64-linux-gnu/libportaudio.so",
            "/usr/lib/x86_64-linux-gnu/libportaudio.so.2",
            "/opt/homebrew/lib/libportaudio.dylib",
            "/usr/local/lib/libportaudio.dylib",
        ]
    )

    load_errors = []
    for candidate in dict.fromkeys(candidates):
        try:
            lib = ctypes.CDLL(candidate)
            break
        except OSError as e:
            load_errors.append(f"{candidate}: {e}")
    else:
        details = "; ".join(load_errors) if load_errors else "no candidate library names found"
        raise PortAudioError(
            "PortAudio shared library could not be loaded. Install PortAudio "
            "(https://github.com/PortAudio/portaudio), run `sudo ldconfig` if "
            f"installed under /usr/local/lib, or set ASCII_ROCK_PORTAUDIO. Details: {details}"
        )

    lib.Pa_Initialize.restype = ctypes.c_int
    lib.Pa_Terminate.restype = ctypes.c_int
    lib.Pa_GetErrorText.argtypes = [ctypes.c_int]
    lib.Pa_GetErrorText.restype = ctypes.c_char_p
    lib.Pa_GetDefaultOutputDevice.restype = ctypes.c_int
    lib.Pa_GetDeviceInfo.argtypes = [ctypes.c_int]
    lib.Pa_GetDeviceInfo.restype = ctypes.POINTER(PaDeviceInfo)
    lib.Pa_IsFormatSupported.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(PaStreamParameters),
        ctypes.c_double,
    ]
    lib.Pa_IsFormatSupported.restype = ctypes.c_int
    lib.Pa_OpenStream.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(PaStreamParameters),
        ctypes.c_double,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    lib.Pa_OpenStream.restype = ctypes.c_int
    lib.Pa_StartStream.argtypes = [ctypes.c_void_p]
    lib.Pa_StartStream.restype = ctypes.c_int
    lib.Pa_StopStream.argtypes = [ctypes.c_void_p]
    lib.Pa_StopStream.restype = ctypes.c_int
    lib.Pa_AbortStream.argtypes = [ctypes.c_void_p]
    lib.Pa_AbortStream.restype = ctypes.c_int
    lib.Pa_CloseStream.argtypes = [ctypes.c_void_p]
    lib.Pa_CloseStream.restype = ctypes.c_int
    lib.Pa_WriteStream.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    lib.Pa_WriteStream.restype = ctypes.c_int
    return lib


class PortAudioWavPlayer:
    """Play a 16-bit PCM WAV file through the default PortAudio output device."""

    def __init__(self, wav_path: str):
        self.wav_path = wav_path
        self.lib = _load_portaudio()
        self.stream = ctypes.c_void_p()
        self._thread = None
        self._stopped = threading.Event()
        self._finished_writing = threading.Event()
        self._pause_condition = threading.Condition()
        self._stream_lock = threading.Lock()
        self._paused = False
        self._pause_started_at = None
        self._pause_seconds = 0.0
        self._started_at = None
        self._written_frames = 0
        self._position_lock = threading.Lock()
        self._initialized = False
        self._opened = False
        self._playback_error = None
        self._load_wav()

    def _load_wav(self):
        with wave.open(self.wav_path, "rb") as wav:
            self.channels = wav.getnchannels()
            self.sample_width = wav.getsampwidth()
            self.sample_rate = wav.getframerate()
            frame_count = wav.getnframes()
            self.pcm_data = wav.readframes(frame_count)

        if self.channels < 1:
            raise PortAudioError(f"Unsupported channel count: {self.channels}")
        if self.sample_width != 2:
            raise PortAudioError("Only 16-bit PCM WAV audio is supported")

        self.bytes_per_frame = self.channels * self.sample_width
        self.total_frames = len(self.pcm_data) // self.bytes_per_frame
        self.duration_seconds = self.total_frames / float(self.sample_rate)

    def _check(self, err: int, action: str):
        if err != PA_NO_ERROR:
            message = self.lib.Pa_GetErrorText(err)
            decoded = message.decode("utf-8", errors="replace") if message else f"error {err}"
            raise PortAudioError(f"{action}: {decoded}")

    def play(self):
        self._check(self.lib.Pa_Initialize(), "Could not initialize PortAudio")
        self._initialized = True

        device_index = self.lib.Pa_GetDefaultOutputDevice()
        if device_index < 0:
            raise PortAudioError("No default PortAudio output device found")

        device_info = self.lib.Pa_GetDeviceInfo(device_index)
        if not device_info:
            raise PortAudioError("Could not read default PortAudio output device info")
        if device_info.contents.maxOutputChannels < self.channels:
            name = device_info.contents.name.decode("utf-8", errors="replace")
            raise PortAudioError(
                f"Default PortAudio output device '{name}' supports "
                f"{device_info.contents.maxOutputChannels} output channel(s), "
                f"but audio needs {self.channels}"
            )

        output = PaStreamParameters(
            device=device_index,
            channelCount=self.channels,
            sampleFormat=PA_INT16,
            suggestedLatency=device_info.contents.defaultLowOutputLatency,
            hostApiSpecificStreamInfo=None,
        )

        supported = self.lib.Pa_IsFormatSupported(None, ctypes.byref(output), float(self.sample_rate))
        self._check(supported, f"Default PortAudio output device does not support {self.sample_rate} Hz 16-bit PCM")

        self._check(
            self.lib.Pa_OpenStream(
                ctypes.byref(self.stream),
                None,
                ctypes.byref(output),
                float(self.sample_rate),
                FRAMES_PER_BUFFER,
                PA_NO_FLAG,
                None,
                None,
            ),
            "Could not open PortAudio output stream",
        )
        self._opened = True

        self._check(self.lib.Pa_StartStream(self.stream), "Could not start PortAudio output stream")
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._write_loop, name="ascii-rock-portaudio", daemon=True)
        self._thread.start()

    def pause(self):
        with self._pause_condition:
            if not self._paused:
                self._paused = True
                self._pause_started_at = time.monotonic()
                with self._stream_lock:
                    if self._opened and self.stream:
                        self._check(self.lib.Pa_StopStream(self.stream), "Could not pause PortAudio stream")

    def unpause(self):
        with self._pause_condition:
            if self._paused:
                with self._stream_lock:
                    if self._opened and self.stream:
                        self._check(self.lib.Pa_StartStream(self.stream), "Could not resume PortAudio stream")
                self._paused = False
                if self._pause_started_at is not None:
                    self._pause_seconds += time.monotonic() - self._pause_started_at
                    self._pause_started_at = None
                self._pause_condition.notify_all()

    def get_pos(self) -> int:
        if self._started_at is None:
            return 0
        # Sync should follow playback time, not Pa_WriteStream completion time.
        # PipeWire/PortAudio buffering can make blocking writes advance unevenly.
        with self._pause_condition:
            paused_seconds = self._pause_seconds
            if self._paused and self._pause_started_at is not None:
                paused_seconds += time.monotonic() - self._pause_started_at
        elapsed = time.monotonic() - self._started_at - paused_seconds
        return max(0, min(int(elapsed * 1000), int(self.duration_seconds * 1000)))

    def is_busy(self) -> bool:
        if self._playback_error:
            raise self._playback_error
        with self._position_lock:
            return not self._stopped.is_set() and self._written_frames < self.total_frames

    def stop(self):
        self._stopped.set()
        with self._pause_condition:
            self._pause_condition.notify_all()

        if self._opened and self.stream and not self._finished_writing.is_set():
            with self._stream_lock:
                if self._opened and self.stream:
                    self.lib.Pa_AbortStream(self.stream)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._thread and self._thread.is_alive():
            return
        with self._stream_lock:
            if self._opened and self.stream:
                if self._finished_writing.is_set():
                    self.lib.Pa_StopStream(self.stream)
                self.lib.Pa_CloseStream(self.stream)
                self._opened = False
                self.stream = ctypes.c_void_p()
        if self._initialized:
            self.lib.Pa_Terminate()
            self._initialized = False

    def _write_loop(self):
        offset = 0
        try:
            while not self._stopped.is_set() and offset < len(self.pcm_data):
                with self._pause_condition:
                    while self._paused and not self._stopped.is_set():
                        self._pause_condition.wait(timeout=0.1)
                if self._stopped.is_set():
                    break

                bytes_to_write = min(FRAMES_PER_BUFFER * self.bytes_per_frame, len(self.pcm_data) - offset)
                frames_to_write = bytes_to_write // self.bytes_per_frame
                chunk = self.pcm_data[offset : offset + frames_to_write * self.bytes_per_frame]
                buffer = ctypes.create_string_buffer(chunk)
                with self._stream_lock:
                    if self._stopped.is_set() or self._paused:
                        continue
                    err = self.lib.Pa_WriteStream(self.stream, buffer, frames_to_write)
                if err != PA_NO_ERROR:
                    message = self.lib.Pa_GetErrorText(err)
                    decoded = message.decode("utf-8", errors="replace") if message else f"error {err}"
                    self._playback_error = PortAudioError(f"Could not write audio to PortAudio stream: {decoded}")
                    break
                offset += frames_to_write * self.bytes_per_frame
                with self._position_lock:
                    self._written_frames += frames_to_write
            if offset >= len(self.pcm_data):
                self._finished_writing.set()
        finally:
            self._stopped.set()
