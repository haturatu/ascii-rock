
https://github.com/user-attachments/assets/b59b097a-acb3-49b1-b238-f3550cf609a9


# ASCII Rock

A command-line tool to play video files as ASCII art directly in your terminal.
  
This is the CLI implementation of the ascii-rock series by contemporary artist Yoshi Sodeoka.  

[Yoshi sodeoka](https://sodeoka.com/)  

## Features

- Plays video files in ASCII.
- Supports audio playback.
- Automatically down-converts high-resolution videos to 360p for better performance (can be disabled).
- Interactive playback controls (pause/resume with Space, quit with 'q').

## Installation

1. Clone this repository.
2. Run the `install` command using the Makefile:

```bash
make install
```

This will install the `ascii-rock` command on your system.

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
  --no-downconvert      Disable automatic 360p down-conversion for high-res videos.
```
