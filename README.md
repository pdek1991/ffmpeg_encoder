##VigilSiddhi Encoder Application

VigilSiddhi Encoder is a robust desktop application built with Python's Tkinter and ttkbootstrap that provides a user-friendly interface for managing and streaming video and audio channels using FFmpeg. It allows users to configure various input and output types (UDP, SRT, RTMP, HLS, YouTube), monitor system resources, and get real-time status updates for each stream.
---

## Table of Contents

- [Overview](#overview)
- [Supported Input & Output Types](#supported-input--output-types)
  - [Local Files](#local-files)
  - [Network Streams (HTTP, RTMP, RTP)](#network-streams-http-rtmp-rtp)
  - [SRT (Secure Reliable Transport)](#srt-secure-reliable-transport)
  - [YouTube & Online Sources](#youtube--online-sources)
  - [Dummy Data](#dummy-data)
- [FFmpeg & ffprobe Basics](#ffmpeg--ffprobe-basics)
- [Advanced FFmpeg Command Reference](#advanced-ffmpeg-command-reference)
- [Parameter Details: Defaults, Effects, Fine-Tuning](#parameter-details-defaults-effects-fine-tuning)
- [Config File Formats](#config-file-formats)
- [Working Steps](#working-steps)
- [Color Indicators](#color-indicators)
- [Future Improvements](#future-improvements)
- [Advanced FFmpeg Parameters (For Future Use)](#advanced-ffmpeg-parameters-for-future-use)
- [License](#license)

---

## Overview

FFmpeg is a powerful multimedia framework for encoding, decoding, transcoding, muxing, demuxing, streaming, filtering, and playing almost anything. This project provides scripts, config templates, and examples for advanced encoding workflows.

---

## Supported Input & Output Types

FFmpeg supports a wide range of inputs and outputs:

### Local Files

**Input:** Any audio/video file format (e.g., `.mp4`, `.mov`, `.avi`, `.wav`, `.mp3`).
**Output:** Any supported format—customizable via codecs and containers.

**Example:**
```sh
ffmpeg -i input.mp4 -c:v libx264 -c:a aac output.mkv
```
- `-i input.mp4`: Local file input.
- `output.mkv`: Output file.

---

### Network Streams (HTTP, RTMP, RTP)

**Input:** HTTP, RTMP, RTP, UDP, etc.
**Output:** Can stream out over network protocols or save locally.

**Example:**
```sh
ffmpeg -i http://example.com/video.mp4 -c copy output.mp4
ffmpeg -i rtmp://server/app/stream -c copy output.flv
```
- `http://...`, `rtmp://...`: Network stream input.

---

### SRT (Secure Reliable Transport)

SRT is a protocol for secure, low-latency video streaming over unreliable networks.

#### SRT Listener

Waits for incoming SRT streams (acts as a server).

**Example:**
```sh
ffplay srt://0.0.0.0:8888?mode=listener
```

- `mode=listener`: Listen for incoming streams.
- `0.0.0.0:8888`: Bind to all interfaces, port 8888.

#### SRT Caller

Connects to a remote SRT endpoint (acts as a client).

**Example:**
```sh
ffplay srt://remote-host:8888?mode=caller
```

- `mode=caller`: Initiate connection.
- `remote-host:8888`: Remote server and port.

#### SRT in FFmpeg

**Input (receive):**
```sh
ffmpeg -i srt://0.0.0.0:8888?mode=listener -c copy received.mp4
```

**Output (send):**
```sh
ffmpeg -re -i input.mp4 -c:v libx264 -c:a aac -f mpegts srt://destination-ip:8888?pkt_size=1316
```
- `-re`: Read at native rate (for streaming).
- `-f mpegts`: Output format suitable for streaming.
- `pkt_size`: Adjust packet size for network performance.

**How SRT works:**
- SRT provides error recovery, encryption, and adaptive retransmission.
- Use `listener` mode for receiving, `caller` for sending.
- Can transmit over unreliable networks with minimal drops.

---

### YouTube & Online Sources

**Input:** You can ingest YouTube streams using `youtube-dl` or `yt-dlp` as an input pipe to FFmpeg.

**Example:**
```sh
youtube-dl -f best -o - "https://www.youtube.com/watch?v=ID" | ffmpeg -i - -c:v copy -c:a copy output.mp4
```
- `youtube-dl`/`yt-dlp`: Retrieves video and pipes it to FFmpeg.
- `-i -`: FFmpeg reads from stdin.

**Direct Streaming (Output):**  
You can stream output to YouTube Live using RTMP.

**Example:**
```sh
ffmpeg -re -i input.mp4 -c:v libx264 -c:a aac -f flv rtmp://a.rtmp.youtube.com/live2/STREAM_KEY
```
- Replace `STREAM_KEY` with your YouTube Live key.

---

### Dummy Data

Useful for testing pipelines.

**Dummy Video:**
```sh
ffmpeg -f lavfi -i testsrc=size=1280x720:rate=30 -t 10 dummy.mp4
```
**Dummy Audio:**
```sh
ffmpeg -f lavfi -i sine=frequency=440:duration=5 dummy.wav
```
**Combine Dummy Audio & Video:**
```sh
ffmpeg -f lavfi -i testsrc=size=1920x1080:rate=25 -f lavfi -i sine=frequency=1000:duration=10 \
-c:v libx264 -c:a aac -t 10 dummy_av.mp4
```

---

## FFmpeg & ffprobe Basics

### FFmpeg

FFmpeg is a command-line tool to process audio/video files.

**Basic syntax:**
```sh
ffmpeg [global_options] -i <input> [output_options] <output>
```

### ffprobe

ffprobe analyzes media files and outputs info about streams, codecs, format, etc.

**Basic syntax:**
```sh
ffprobe [options] <input>
```
**Example:**
```sh
ffprobe -v error -show_format -show_streams input.mp4
```
- `-v error`: Show only errors
- `-show_format`: Output container format info
- `-show_streams`: Output stream info (audio/video)

---

## Advanced FFmpeg Command Reference

### Basic Transcoding
```sh
ffmpeg -i input.mp4 -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 128k output.mp4
```
- `-c:v libx264`: Video codec (H.264)
- `-preset fast`: x264 encoding speed/quality
- `-crf 23`: Constant Rate Factor (quality 0–51, lower = better)
- `-c:a aac`: Audio codec
- `-b:a 128k`: Audio bitrate

### Stream Copy (No Re-encoding)
```sh
ffmpeg -i input.mkv -c copy output.mp4
```
- `-c copy`: Copy streams directly (fast, lossless)

### Resize and Change Frame Rate
```sh
ffmpeg -i input.mp4 -vf scale=640:360,fps=15 -c:v libx264 -crf 28 output_resized.mp4
```
- `-vf scale=640:360`: Resize video
- `fps=15`: Change frame rate

### Extract Audio
```sh
ffmpeg -i input.mp4 -vn -acodec copy output_audio.aac
```
- `-vn`: “No video”
- `-acodec copy`: Copy audio codec

### Two-Pass Encoding
```sh
ffmpeg -y -i input.mp4 -c:v libx264 -b:v 2M -pass 1 -an -f null /dev/null
ffmpeg -i input.mp4 -c:v libx264 -b:v 2M -pass 2 -c:a aac -b:a 128k output_2pass.mp4
```
- `-pass 1/2`: First and second pass

### HLS Streaming
```sh
ffmpeg -i input.mp4 -c:v libx264 -c:a aac -f hls -hls_time 4 -hls_playlist_type vod playlist.m3u8
```
- `-f hls`: Output HLS format
- `-hls_time 4`: Segment length (seconds)
- `-hls_playlist_type vod`: Video on demand

---

## Parameter Details: Defaults, Effects, Fine-Tuning

### Video Codec (`-c:v`)
- **Default:** FFmpeg auto-selects based on format (e.g., `mpeg4` for `.mp4`)
- **Effect:** Compression, compatibility, quality

### Audio Codec (`-c:a`)
- **Default:** Auto-selects `aac` for `.mp4`
- **Effect:** Audio quality, compatibility

### Bitrate (`-b:v`, `-b:a`)
- **Default:** None; codec defaults
- **Effect:** Higher = better quality, larger file

### CRF (Constant Rate Factor)
- **Default for x264:** 23
- **Effect:** Lower = better quality

### Preset
- **Default for x264:** `medium`
- **Effect:** Faster = less compression

### Frame Rate (`-r`)
- **Default:** Input’s native
- **Effect:** Lower = smaller file, less smooth

### Resolution (`-vf scale=WxH`)
- **Default:** Input’s native
- **Effect:** Lower = smaller file

### Audio Channels (`-ac`)
- **Default:** Input’s count
- **Effect:** `-ac 2` stereo, `-ac 1` mono

### SRT Parameters
- **mode=listener**: Listens for incoming streams; acts as a server.
- **mode=caller**: Initiates connection; acts as a client.
- **pkt_size**: Packet size for network performance.
- **latency**: Buffer time for network jitter.
- **encryption**: Enable SRT encryption.

### YouTube/RTMP Parameters
- **-f flv**: Required for RTMP streaming.
- **-rtmp_live live**: Sets YouTube live mode.

---

## Config File Formats

### YAML Example
```yaml
input: "input.mp4"
output: "output.mp4"
video:
  codec: "libx264"
  crf: 22
  preset: "medium"
  filters:
    - scale=1280:720
    - fps=30
audio:
  codec: "aac"
  bitrate: "128k"
  channels: 2
extra_options: ["-movflags", "+faststart"]
```

### JSON Example
```json
{
  "input": "input.mp4",
  "output": "output.mp4",
  "video": {
    "codec": "libx264",
    "crf": 22,
    "preset": "medium",
    "filters": ["scale=1280:720", "fps=30"]
  },
  "audio": {
    "codec": "aac",
    "bitrate": "128k",
    "channels": 2
  },
  "extra_options": ["-movflags", "+faststart"]
}
```

---

## Working Steps

1. **Install FFmpeg:**  
   Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to your PATH.

2. **Prepare Config:**  
   Write your YAML/JSON config or use command-line directly.

3. **Run ffmpeg:**  
   Execute via CLI or script:
   ```sh
   ffmpeg -i input.mp4 -c:v libx264 -crf 22 -preset medium -vf scale=1280:720 -c:a aac -b:a 128k output.mp4
   ```

4. **Analyze Output:**  
   Use ffprobe:
   ```sh
   ffprobe -show_streams output.mp4
   ```

5. **Interpret Color Indicators:**  
   - **Green:** Success/OK
   - **Yellow:** Warning (non-fatal error, fallback)
   - **Red:** Error/Failure
   - **Blue:** Information/Processing

---

## Color Indicators

If your workflow includes a dashboard or logs:
- **Green:** Encoded successfully / healthy stream
- **Yellow:** Encoding warning (e.g., dropped frames)
- **Red:** Critical error (e.g., failed encode)
- **Blue:** Informational (e.g., process started)

---

## Future Improvements

- Configurable presets (YAML/JSON)
- Advanced inputs: RTP/UDP, HTTP live sources, device capture
- PWA/Web UI: Live notifications, encode status
- Stream monitoring: Real-time health, dropped frames, sync errors
- Batch processing: Multiple files/configs
- GPU acceleration: NVIDIA/AMD hardware
- Logging & visualization: Color-coded logs, dashboards
- Transcoding pipelines: Chained filter/encode jobs

---

## Advanced FFmpeg Parameters (For Future Use)

Not yet implemented, but planned:

- `-filter_complex`: Build complex filter graphs (overlay, concat, split)
- `-map`: Explicit stream selection (audio, video, subtitles)
- `-x264-params`: Advanced x264 encoder settings
- `-profile:v`, `-level:v`: Set H.264/H.265 profile/level
- `-maxrate`, `-bufsize`: Streaming bitrate and VBV buffer
- `-ss`, `-t`: Trim input (`-ss` start, `-t` duration)
- `-threads`: Number of encode threads
- `-hwaccel`: Hardware acceleration
- `-f segment`: Segmented files for archival/streaming
- `-metadata`: Custom metadata (title, author)
- `-an`, `-vn`: Drop audio or video stream

---

## References

- [FFmpeg Documentation](https://ffmpeg.org/documentation.html)
- [ffprobe Documentation](https://ffmpeg.org/ffprobe.html)
- [FFmpeg Filters](https://ffmpeg.org/ffmpeg-filters.html)
- [SRT Alliance](https://www.srtalliance.org/)
- [YouTube Live Streaming](https://support.google.com/youtube/answer/2853702?hl=en)
- [youtube-dl](https://github.com/ytdl-org/youtube-dl)

