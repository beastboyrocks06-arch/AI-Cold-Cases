
import os
import re
import sys
import json
import random
import shutil
import subprocess

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
MUSIC_DIR = BASE_DIR / "assets" / "music"

MAX_VIDEOS_PER_RUN = 1

WIDTH = 1080
HEIGHT = 1920
FPS = 30

TRANSITION = 0.45
MUSIC_VOLUME = 0.12

VIDEO_BITRATE = "5M"
AUDIO_BITRATE = "192k"

FONT_NAME = "Arial"
FONT_SIZE = 62


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    temp_path = path.with_suffix(".tmp")

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    temp_path.replace(path)


def run_command(command):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr[-4000:] or "Command execution failed."
        )

    return result.stdout


def get_media_info(path):
    command = [
        "ffprobe",
        "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        str(path)
    ]

    result = json.loads(run_command(command))

    streams = result.get("streams", [])
    audio_stream = next(
        (s for s in streams if s.get("codec_type") == "audio"),
        None
    )

    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"),
        None
    )

    duration = float(
        result.get("format", {}).get("duration", 0)
    )

    return {
        "duration": duration,
        "audio": audio_stream,
        "video": video_stream
    }


def get_scene_image(story_dir, scene_id):
    for extension in (".jpg", ".jpeg", ".png", ".webp"):
        path = story_dir / f"scene_{int(scene_id):02d}{extension}"

        if path.exists() and path.stat().st_size > 0:
            return path

    return None


def get_words(data):
    timing_path = (
        OUTPUT_DIR /
        data.get("video_id", "") /
        "timing.json"
    )

    if not timing_path.exists():
        raise FileNotFoundError(
            f"Timing file not found: {timing_path}"
        )

    with open(timing_path, "r", encoding="utf-8") as f:
        timings = json.load(f)

    if isinstance(timings, dict):
        timings = timings.get("words", [])

    if not isinstance(timings, list):
        raise ValueError("Invalid timing.json format.")

    words = []

    for item in timings:
        word = str(item.get("word", "")).strip()

        if not word:
            continue

        start = float(item.get("start", 0))
        end = float(item.get("end", 0))

        if end <= start:
            continue

        words.append({
            "word": word,
            "start": max(0, start),
            "end": end
        })

    words.sort(key=lambda x: x["start"])

    if not words:
        raise ValueError("No valid word timings found.")

    return words


def get_scene_durations(scenes, words, audio_duration):
    counts = []

    for scene in scenes:
        narration = str(scene.get("narration", "")).strip()
        count = len(re.findall(r"\S+", narration))
        counts.append(max(count, 1))

    total_scene_words = sum(counts)

    if not total_scene_words:
        raise ValueError("Scene narration is empty.")

    durations = []
    word_index = 0

    for count in counts:
        start_index = word_index
        end_index = min(word_index + count, len(words))

        if start_index >= len(words):
            durations.append(0.1)
        else:
            start = words[start_index]["start"]
            end = words[end_index - 1]["end"]
            durations.append(max(end - start, 0.1))

        word_index += count

    total = sum(durations)

    if total <= 0:
        raise ValueError("Could not calculate scene durations.")

    # Match the scene sequence to the actual voiceover duration.
    durations = [
        duration * audio_duration / total
        for duration in durations
    ]

    # Crossfades reduce the final duration.
    # Add the lost time to the final scene.
    if len(durations) > 1:
        durations[-1] += TRANSITION * (len(durations) - 1)

    return durations


def get_music():
    extensions = {
        ".mp3",
        ".wav",
        ".m4a",
        ".aac",
        ".ogg",
        ".flac"
    }

    if not MUSIC_DIR.exists():
        return None

    tracks = [
        path
        for path in MUSIC_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in extensions
    ]

    if not tracks:
        return None

    return random.choice(tracks)


def format_ass_time(seconds):
    seconds = max(0, seconds)

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remaining = seconds % 60

    return (
        f"{hours}:"
        f"{minutes:02d}:"
        f"{remaining:05.2f}"
    )


def escape_ass_text(text):
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
    )


def create_caption_groups(words):
    groups = []
    current = []

    for word in words:
        if current:
            current_text = " ".join(
                item["word"] for item in current
            )

            next_length = len(current_text) + len(word["word"]) + 1

            should_break = (
                len(current) >= 5
                or next_length > 34
                or re.search(r"[.!?]$", current[-1]["word"])
            )

            if should_break:
                groups.append(current)
                current = []

        current.append(word)

    if current:
        groups.append(current)

    return groups


def create_ass_file(words, output_path):
    groups = create_caption_groups(words)

    header = """[Script Info]
Title: AI Philosopher Captions
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,62,&H0000D7FF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,12,2,2,70,70,270,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [header]

    for group in groups:
        if not group:
            continue

        start = group[0]["start"]
        end = group[-1]["end"] + 0.30

        caption_parts = []

        for index, item in enumerate(group):
            if index + 1 < len(group):
                next_start = group[index + 1]["start"]
                duration = max(
                    next_start - item["start"],
                    item["end"] - item["start"]
                )
            else:
                duration = item["end"] - item["start"]

            centiseconds = max(1, round(duration * 100))

            caption_parts.append(
                f"{{\\kf{centiseconds}}}"
                f"{escape_ass_text(item['word'])}"
            )

        caption = " ".join(caption_parts)

        lines.append(
            f"Dialogue: 0,"
            f"{format_ass_time(start)},"
            f"{format_ass_time(end)},"
            f"Default,,0,0,0,,{caption}\n"
        )

    with open(output_path, "w", encoding="utf-8-sig") as f:
        f.writelines(lines)


def escape_filter_path(path):
    value = str(path.resolve()).replace("\\", "/")
    value = value.replace(":", r"\:")
    value = value.replace("'", r"\'")
    value = value.replace(",", r"\,")
    return value


def build_video(
    images,
    durations,
    audio_path,
    output_path,
    audio_duration,
    caption_path,
    music_path=None
):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg is not installed or not in PATH.")

    if not shutil.which("ffprobe"):
        raise RuntimeError("FFprobe is not installed or not in PATH.")

    if len(images) != len(durations):
        raise ValueError("Image and duration counts do not match.")

    if not images:
        raise ValueError("No scene images supplied.")

    command = ["ffmpeg", "-y"]

    for image_path, duration in zip(images, durations):
        command.extend([
            "-loop", "1",
            "-framerate", str(FPS),
            "-t", f"{duration:.3f}",
            "-i", str(image_path)
        ])

    audio_index = len(images)

    command.extend([
        "-i", str(audio_path)
    ])

    music_index = None

    if music_path:
        music_index = audio_index + 1

        command.extend([
            "-stream_loop", "-1",
            "-i", str(music_path)
        ])

    filters = []

    for index, duration in enumerate(durations):
        filters.append(
            f"[{index}:v]"
            f"scale={WIDTH}:{HEIGHT}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},"
            f"zoompan="
            f"z='min(zoom+0.00035,1.08)':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d=1:"
            f"s={WIDTH}x{HEIGHT}:"
            f"fps={FPS},"
            f"trim=duration={duration:.3f},"
            f"setpts=PTS-STARTPTS,"
            f"setsar=1"
            f"[v{index}]"
        )

    if len(images) == 1:
        video_label = "[v0]"
    else:
        previous_label = "[v0]"
        cumulative_duration = durations[0]

        for index in range(1, len(images)):
            output_label = f"[xf{index}]"
            offset = cumulative_duration - TRANSITION

            filters.append(
                f"{previous_label}[v{index}]"
                f"xfade=transition=fade:"
                f"duration={TRANSITION}:"
                f"offset={offset:.3f}"
                f"{output_label}"
            )

            cumulative_duration += durations[index] - TRANSITION
            previous_label = output_label

        video_label = previous_label

    caption_filter = escape_filter_path(caption_path)

    filters.append(
        f"{video_label}"
        f"ass=filename='{caption_filter}',"
        f"format=yuv420p"
        f"[vout]"
    )

    if music_path:
        filters.append(
            f"[{audio_index}:a]"
            f"aresample=48000,"
            f"volume=1.0"
            f"[voice];"

            f"[{music_index}:a]"
            f"aresample=48000,"
            f"volume={MUSIC_VOLUME}"
            f"[music];"

            f"[voice][music]"
            f"amix=inputs=2:"
            f"duration=first:"
            f"dropout_transition=2:"
            f"normalize=0,"
            f"alimiter=limit=0.95"
            f"[aout]"
        )
    else:
        filters.append(
            f"[{audio_index}:a]"
            f"aresample=48000,"
            f"volume=1.0,"
            f"alimiter=limit=0.95"
            f"[aout]"
        )

    filter_complex = ";".join(filters)

    command.extend([
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-t", f"{audio_duration:.3f}",
        "-r", str(FPS),
        "-c:v", "libx264",
        "-preset", "medium",
        "-b:v", VIDEO_BITRATE,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-ar", "48000",
        "-movflags", "+faststart",
        str(output_path)
    ])

    print("[VIDEO] Rendering scenes...")
    print("[CAPTIONS] Burning animated word captions...")

    if music_path:
        print(f"[MUSIC] {music_path.name}")
    else:
        print("[MUSIC] No background music found. Using voiceover only.")

    run_command(command)


def process_story(json_path):
    data = load_json(json_path)

    video_id = data.get("video_id", json_path.stem)
    title = data.get("title", video_id)

    story_dir = OUTPUT_DIR / video_id

    audio_path = story_dir / "voiceover.mp3"
    timing_path = story_dir / "timing.json"
    final_path = story_dir / "final_video.mp4"
    temp_path = story_dir / "final_video.tmp.mp4"
    caption_path = story_dir / "captions.ass"

    print(f"\n[GENERATING] {title}")

    if not data.get("slides_generated"):
        print(f"[SKIP] {video_id}: Images are not marked complete.")
        return

    scenes = data.get("scenes", [])

    if not scenes:
        print(f"[ERROR] {video_id}: No scenes found.")
        return

    images = []

    for scene in scenes:
        scene_id = scene.get("scene_id")

        if scene_id is None or not scene.get("image_generated"):
            raise RuntimeError(
                f"Scene {scene_id} is not marked as generated."
            )

        image_path = get_scene_image(story_dir, scene_id)

        if not image_path:
            raise FileNotFoundError(
                f"Image missing for scene {scene_id}."
            )

        images.append(image_path)

    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise FileNotFoundError(
            f"Voiceover is missing or empty: {audio_path}"
        )

    if not timing_path.exists() or timing_path.stat().st_size == 0:
        raise FileNotFoundError(
            f"Word timing file is missing: {timing_path}"
        )

    audio_info = get_media_info(audio_path)

    if not audio_info["audio"]:
        raise RuntimeError(
            "voiceover.mp3 does not contain an audio stream."
        )

    audio_duration = audio_info["duration"]

    if audio_duration <= 0:
        raise RuntimeError("Voiceover duration is invalid.")

    words = get_words(data)

    if words[-1]["end"] > audio_duration + 2:
        raise RuntimeError(
            "Word timings exceed the voiceover duration. "
            "Regenerate the voiceover and timing.json."
        )

    durations = get_scene_durations(
        scenes,
        words,
        audio_duration
    )

    story_dir.mkdir(parents=True, exist_ok=True)

    create_ass_file(words, caption_path)

    music_path = get_music()

    data["video_generated"] = False
    save_json(json_path, data)

    if temp_path.exists():
        temp_path.unlink()

    try:
        build_video(
            images=images,
            durations=durations,
            audio_path=audio_path,
            output_path=temp_path,
            audio_duration=audio_duration,
            caption_path=caption_path,
            music_path=music_path
        )

        if not temp_path.exists() or temp_path.stat().st_size == 0:
            raise RuntimeError("FFmpeg did not create the final video.")

        final_info = get_media_info(temp_path)

        if not final_info["video"]:
            raise RuntimeError("Final video has no video stream.")

        if not final_info["audio"]:
            raise RuntimeError("Final video has no audio stream.")

        if abs(final_info["duration"] - audio_duration) > 1.0:
            raise RuntimeError(
                "Final video duration does not match voiceover duration."
            )

        temp_path.replace(final_path)

        data["video_generated"] = True
        save_json(json_path, data)

        print(f"[SUCCESS] {video_id}")
        print(f"[DURATION] {final_info['duration']:.2f} seconds")
        print(f"[VOICE] Included")
        print(f"[CAPTIONS] Included")
        print(f"[VIDEO] {final_path}")

    except Exception:
        data["video_generated"] = False
        save_json(json_path, data)

        if temp_path.exists():
            temp_path.unlink()

        raise


def main(filename=None):
    if filename:
        supplied_path = Path(filename)

        json_files = [
            supplied_path if supplied_path.is_absolute()
            else DATA_DIR / supplied_path
        ]
    else:
        json_files = sorted(DATA_DIR.glob("*.json"))

    if not json_files:
        print("[INFO] No JSON files found.")
        return

    pending_files = []

    for json_path in json_files:
        if not json_path.exists():
            print(f"[ERROR] JSON file not found: {json_path}")
            continue

        try:
            data = load_json(json_path)

            video_id = data.get("video_id", json_path.stem)
            story_dir = OUTPUT_DIR / video_id

            if data.get("video_generated"):
                final_path = story_dir / "final_video.mp4"

                if final_path.exists() and final_path.stat().st_size > 0:
                    continue

            if not data.get("slides_generated"):
                continue

            scenes = data.get("scenes", [])

            if not scenes:
                continue

            images_ready = all(
                scene.get("image_generated")
                and scene.get("scene_id") is not None
                and get_scene_image(story_dir, scene["scene_id"])
                for scene in scenes
            )

            if not images_ready:
                continue

            audio_path = story_dir / "voiceover.mp3"
            timing_path = story_dir / "timing.json"

            if (
                not audio_path.exists()
                or audio_path.stat().st_size == 0
                or not timing_path.exists()
                or timing_path.stat().st_size == 0
            ):
                print(
                    f"[SKIP] {video_id}: "
                    "Voiceover or timing file is missing."
                )
                continue

            pending_files.append(json_path)

        except Exception as e:
            print(f"[ERROR] {json_path.name}: {e}")

    selected_files = pending_files[:MAX_VIDEOS_PER_RUN]

    if not selected_files:
        print("[INFO] No pending videos ready for generation.")
        return

    print(
        f"Processing {len(selected_files)} videos "
        f"(maximum {MAX_VIDEOS_PER_RUN} per run)."
    )

    for json_path in selected_files:
        try:
            process_story(json_path)
        except Exception as e:
            print(f"[ERROR] {json_path.name}: {e}")

    print("\nVideo generation process completed.")


if __name__ == "__main__":
    filename = sys.argv[1] if len(sys.argv) > 1 else None
    main(filename)
