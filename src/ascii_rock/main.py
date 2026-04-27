import argparse
import os

from ascii_rock.constants import EXPORT_DEFAULT_WORKERS
from ascii_rock.export import export_ascii_mp4
from ascii_rock.playback import play_video
from ascii_rock.renderers import get_terminal_size


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description="Play video files as ASCII art in the terminal.\n\nControls:\n  Space: Pause/Resume\n  q: Quit",
    )
    parser.add_argument("video_path", help="Path to the video file.")
    parser.add_argument("-w", "--width", type=int, help="Width of the ASCII output in characters. Defaults to terminal width.")
    parser.add_argument("-m", "--music", action="store_true", help="Play audio from the video file.")
    parser.add_argument("--no-downconvert", action="store_true", help="Disable ffmpeg frame scaling and decode full frames with OpenCV.")
    parser.add_argument("--remove-background", action="store_true", help="Treat border-colored background pixels as empty space.")
    parser.add_argument("-o", "--output-mp4", help="Export the ASCII-rendered video to an MP4 file instead of playing it.")
    parser.add_argument(
        "--export-workers",
        type=int,
        default=EXPORT_DEFAULT_WORKERS,
        help=f"Number of parallel frame render workers for --output-mp4. Defaults to {EXPORT_DEFAULT_WORKERS}.",
    )

    args = parser.parse_args()

    if not os.path.exists(args.video_path):
        print(f"Error: Video file not found at '{args.video_path}'")
        return

    terminal_width, _ = get_terminal_size()
    output_width = args.width if args.width else terminal_width

    if args.output_mp4:
        export_ascii_mp4(
            args.video_path,
            args.output_mp4,
            output_width,
            args.no_downconvert,
            args.remove_background,
            workers=args.export_workers,
        )
        return

    play_video(args.video_path, output_width, args.music, args.no_downconvert, args.remove_background)


if __name__ == "__main__":
    main()
