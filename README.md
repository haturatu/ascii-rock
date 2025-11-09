# ASCII Video Player

This project converts a video file into ASCII art and plays it directly in your terminal.

## Setup

1.  **Navigate to the project directory.**

    ```bash
    cd ascii_video_player
    ```

2.  **Install the dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

## Usage

Run the script from your terminal, providing the path to your video file.

```bash
python main.py /path/to/your/video.mp4
```

### Options

*   `-w` or `--width`: Set the width of the ASCII art output. It defaults to the current width of your terminal.

    ```bash
    python main.py /path/to/your/video.mp4 --width 120
    ```

## How it Works

The script uses OpenCV to read the video file frame by frame. Each frame is then converted into an ASCII representation using the Pillow library for image manipulation. The terminal screen is cleared and redrawn for each frame to create the animation effect. The video will loop automatically. Press `Ctrl+C` to stop.
