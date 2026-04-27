import json
import queue
import subprocess
import threading
import time

import cv2
import numpy as np

from ascii_rock.ascii import ascii_height_for, frame_to_ascii, gray_frame_to_ascii
from ascii_rock.constants import (
    FRAME_PREFETCH_SECONDS,
    INITIAL_PREBUFFER_SECONDS,
    INITIAL_PREBUFFER_TIMEOUT_SECONDS,
    MAX_SYNC_SKIP_FRAMES,
    SYNC_TOLERANCE_MS,
)


def _parse_fps(value):
    if not value or value == "0/0":
        return 0.0
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        denominator = float(denominator)
        if denominator == 0:
            return 0.0
        return float(numerator) / denominator
    return float(value)


def probe_video_metadata(video_path):
    """Reads basic video metadata with ffprobe."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate",
        "-of",
        "json",
        video_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")

    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError("No video stream found")

    stream = streams[0]
    fps = _parse_fps(stream.get("avg_frame_rate")) or _parse_fps(stream.get("r_frame_rate")) or 30.0
    return int(stream["width"]), int(stream["height"]), fps


class FFmpegAsciiFrameReader:
    """Reads already-scaled grayscale frames from ffmpeg stdout."""

    def __init__(self, video_path, width, remove_background=False):
        source_width, source_height, fps = probe_video_metadata(video_path)
        self.width = width
        self.height = ascii_height_for(source_width, source_height, width)
        self.remove_background = remove_background
        self.fps = fps
        self.frame_size = self.width * self.height
        self.frame_index = 0
        self._stopped = threading.Event()
        self._frames = queue.Queue(maxsize=max(8, int(self.fps * FRAME_PREFETCH_SECONDS)))
        self._reader_error = None
        self.process = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                video_path,
                "-an",
                "-vf",
                f"scale={self.width}:{self.height}:flags=fast_bilinear,format=gray",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._thread = threading.Thread(target=self._prefetch_frames, name="ascii-rock-ffmpeg-reader", daemon=True)
        self._thread.start()

    def read_ascii(self):
        ret, ascii_frame = self._frames.get()
        if self._reader_error:
            raise self._reader_error
        if not ret:
            return False, ""
        self.frame_index += 1
        return True, ascii_frame

    def get_pos_msec(self):
        return self.frame_index * 1000.0 / self.fps if self.fps > 0 else 0.0

    def skip_frame(self):
        ret, _ascii_frame = self.read_ascii()
        return ret

    def prebuffer(self, seconds=INITIAL_PREBUFFER_SECONDS, timeout=INITIAL_PREBUFFER_TIMEOUT_SECONDS):
        target = min(self._frames.maxsize, max(1, int(self.fps * seconds)))
        deadline = time.monotonic() + timeout
        while self._frames.qsize() < target and time.monotonic() < deadline:
            if self._reader_error:
                raise self._reader_error
            if self.process.poll() is not None:
                break
            time.sleep(0.02)

    def release(self):
        self._stopped.set()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _prefetch_frames(self):
        try:
            while not self._stopped.is_set():
                frame = self.process.stdout.read(self.frame_size)
                if len(frame) != self.frame_size:
                    self._put_frame(False, "")
                    break
                gray = np.frombuffer(frame, dtype=np.uint8).reshape((self.height, self.width))
                self._put_frame(True, gray_frame_to_ascii(gray, remove_background=self.remove_background))
        except Exception as e:
            self._reader_error = e
            self._put_frame(False, "")

    def _put_frame(self, ret, ascii_frame):
        while not self._stopped.is_set():
            try:
                self._frames.put((ret, ascii_frame), timeout=0.1)
                return
            except queue.Full:
                continue


class FFmpegGrayFrameReader:
    """Reads scaled grayscale frames from ffmpeg for export workers."""

    def __init__(self, video_path, width):
        source_width, source_height, fps = probe_video_metadata(video_path)
        self.width = width
        self.height = ascii_height_for(source_width, source_height, width)
        self.fps = fps
        self.frame_size = self.width * self.height
        self.process = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                video_path,
                "-an",
                "-vf",
                f"scale={self.width}:{self.height}:flags=fast_bilinear,format=gray",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def read_gray(self):
        frame = self.process.stdout.read(self.frame_size)
        if len(frame) != self.frame_size:
            return False, None
        return True, bytes(frame)

    def release(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


class OpenCvAsciiFrameReader:
    """Fallback reader that decodes full frames with OpenCV."""

    def __init__(self, video_path, width, remove_background=False):
        self.width = width
        self.remove_background = remove_background
        self.cap = cv2.VideoCapture(video_path)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open video file at {video_path}")
        source_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        source_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.height = ascii_height_for(source_width, source_height, width)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0

    def read_ascii(self):
        ret, frame = self.cap.read()
        if not ret:
            return False, ""
        return True, frame_to_ascii(frame, self.width, remove_background=self.remove_background)

    def get_pos_msec(self):
        return self.cap.get(cv2.CAP_PROP_POS_MSEC)

    def skip_frame(self):
        ret, _frame = self.cap.read()
        return ret

    def release(self):
        if self.cap.isOpened():
            self.cap.release()


class OpenCvGrayFrameReader:
    """Reads scaled grayscale frames with OpenCV for export workers."""

    def __init__(self, video_path, width):
        self.width = width
        self.cap = cv2.VideoCapture(video_path)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open video file at {video_path}")
        source_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        source_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.height = ascii_height_for(source_width, source_height, width)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0

    def read_gray(self):
        ret, frame = self.cap.read()
        if not ret:
            return False, None
        resized = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        return True, gray.tobytes()

    def release(self):
        if self.cap.isOpened():
            self.cap.release()


def skip_video_frames_to_sync(reader, audio_ts_ms):
    skipped = 0
    while skipped < MAX_SYNC_SKIP_FRAMES:
        video_ts_ms = reader.get_pos_msec()
        if audio_ts_ms - video_ts_ms <= SYNC_TOLERANCE_MS:
            break
        if not reader.skip_frame():
            return False, skipped
        skipped += 1
    return True, skipped
