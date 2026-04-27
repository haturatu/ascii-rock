# ASCII Rock

A command-line tool to play video files as ASCII art directly in your terminal.
  
This is the CLI implementation of the ascii-rock series by contemporary artist Yoshi Sodeoka.  

[Yoshi sodeoka](https://sodeoka.com/)  

## Features

- Plays video files in ASCII.
- Supports audio playback.
- Scales video frames to the terminal width while streaming them from ffmpeg.
- Interactive playback controls (pause/resume with Space, quit with 'q').

## Installation

1. Clone this repository.
2. Install PortAudio from your system package manager or from source:

```bash
# Debian/Ubuntu
sudo apt install ffmpeg libportaudio2

# From source
git clone https://github.com/PortAudio/portaudio
cd portaudio
mkdir build
cd build
cmake ..
make
sudo make install
```

3. Run the `install` command using the Makefile:

```bash
make install
```

This will install the `ascii-rock` command on your system. Default video
playback uses `ffmpeg` and `ffprobe` to stream scaled frames. Audio playback
with `-m` requires the PortAudio shared library to be available at runtime.

If PortAudio was installed under `/usr/local/lib`, refresh the dynamic loader
cache:

```bash
sudo ldconfig
```

If PortAudio is installed in a non-standard location, point ascii-rock at the
shared library:

```bash
ASCII_ROCK_PORTAUDIO=/path/to/libportaudio.so ascii-rock -m /path/to/video.mp4
```

## Usage

```bash
ascii-rock /path/to/your/video.mp4
```

### Options

The output of `ascii-rock -h`:
```
$ ascii-rock -h
usage: ascii-rock [-h] [-w WIDTH] [-m] [--no-downconvert] video_path

Play video files as ASCII art in the terminal.

Controls:
  Space: Pause/Resume
  q: Quit

positional arguments:
  video_path            Path to the video file.

options:
  -h, --help            show this help message and exit
  -w WIDTH, --width WIDTH
                        Width of the ASCII output in characters. Defaults to terminal width.
  -m, --music           Play audio from the video file.
  --no-downconvert      Disable ffmpeg frame scaling and decode full frames with OpenCV.
```
