import collections
import concurrent.futures
import subprocess

from ascii_rock.constants import EXPORT_DEFAULT_WORKERS, EXPORT_PENDING_MULTIPLIER, EXPORT_PROGRESS_INTERVAL
from ascii_rock.renderers import AsciiFrameImageRenderer
from ascii_rock.video import FFmpegGrayFrameReader, OpenCvGrayFrameReader


def export_ascii_mp4(video_path, output_path, width, no_downconvert, remove_background, workers=EXPORT_DEFAULT_WORKERS):
    """Export ASCII-rendered video to an MP4 file."""
    if no_downconvert:
        reader = OpenCvGrayFrameReader(video_path, width)
    else:
        reader = FFmpegGrayFrameReader(video_path, width)

    renderer = AsciiFrameImageRenderer(width, reader.height)
    fps = reader.fps if reader.fps > 0 else 30.0
    ffmpeg_command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{renderer.width}x{renderer.height}",
        "-r",
        f"{fps:.6f}",
        "-i",
        "-",
        "-i",
        video_path,
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        "-movflags",
        "+faststart",
        output_path,
    ]

    workers = max(1, workers)
    max_pending = max(1, workers * EXPORT_PENDING_MULTIPLIER)
    process = subprocess.Popen(ffmpeg_command, stdin=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
    frame_count = 0
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            pending = collections.deque()
            reader_done = False

            while pending or not reader_done:
                while not reader_done and len(pending) < max_pending:
                    ret, gray_frame = reader.read_gray()
                    if not ret:
                        reader_done = True
                        break
                    pending.append(executor.submit(renderer.render_gray, gray_frame, remove_background, True))

                if not pending:
                    continue

                process.stdin.write(pending.popleft().result())
                frame_count += 1
                if frame_count % EXPORT_PROGRESS_INTERVAL == 0:
                    print(f"Exported {frame_count} frames...", flush=True)
    finally:
        reader.release()
        if process.stdin:
            process.stdin.close()

    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"ffmpeg MP4 export failed: {stderr.strip()}")
    print(f"Exported {frame_count} ASCII frames to {output_path}")
