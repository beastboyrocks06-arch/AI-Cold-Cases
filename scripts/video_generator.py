
import json
import math
import random
import re
import subprocess
import sys

from pathlib import Path
from caption_generator import generate_caption_data

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
MUSIC_DIR = BASE_DIR / "assets" / "music"
FONTS_DIR = BASE_DIR / "fonts"

WIDTH = 1080
HEIGHT = 1920
FPS = 30

TRANSITION = 0.6
MUSIC_VOLUME = 0.12

VIDEO_CRF = 23
VIDEO_PRESET = "medium"

MAX_VIDEOS_PER_RUN = 3

MUSIC_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"
}


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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-5000:])

    return result


def get_audio_duration(audio_path):
    result = run_command([
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ])

    try:
        duration = float(result.stdout.strip())
    except (ValueError, TypeError):
        result = run_command([
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ])
        duration = float(result.stdout.strip())

    if duration <= 0:
        raise RuntimeError("Voiceover has an invalid duration.")

    return duration


def get_video_duration(video_path):
    result = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ])

    return float(result.stdout.strip())


def has_audio_stream(video_path):
    result = run_command([
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        str(video_path)
    ])

    return "audio" in result.stdout.lower()


def get_music_files():
    if not MUSIC_DIR.exists():
        return []

    return [
        path for path in MUSIC_DIR.rglob("*")
        if path.is_file()
        and path.suffix.lower() in MUSIC_EXTENSIONS
    ]


def get_scene_images(scenes, story_output):
    images = []

    for scene in scenes:
        scene_id = scene.get("scene_id")

        if scene_id is None:
            raise ValueError("A scene is missing its scene_id.")

        image_path = story_output / f"scene_{int(scene_id):02d}.jpg"

        if not scene.get("image_generated"):
            raise RuntimeError(
                f"Scene {scene_id} is not marked as image_generated."
            )

        if not image_path.exists() or image_path.stat().st_size == 0:
            raise FileNotFoundError(
                f"Scene image is missing: {image_path}"
            )

        images.append(image_path)

    if not images:
        raise RuntimeError("No scene images found.")

    return images


def get_scene_durations(scenes, timing_path, audio_duration):
    timings = []

    if timing_path.exists():
        try:
            timings = load_json(timing_path)
        except Exception as e:
            print(f"[WARNING] Could not read timing.json: {e}")

    word_counts = [
        len(re.findall(
            r"\b[\w’'-]+\b",
            str(scene.get("narration", ""))
        ))
        for scene in scenes
    ]

    total_words = sum(word_counts)

    if not timings or not total_words:
        print("[WARNING] Using narration word-count estimates.")

        return [
            audio_duration * count / max(total_words, 1)
            for count in word_counts
        ]

    boundaries = [0.0]
    cumulative_words = 0

    for count in word_counts[:-1]:
        cumulative_words += count

        index = round(
            cumulative_words / total_words * len(timings)
        )

        index = min(max(index, 0), len(timings) - 1)

        try:
            boundary = float(timings[index]["start"])
        except (KeyError, TypeError, ValueError):
            boundary = (
                audio_duration * cumulative_words / total_words
            )

        boundary = min(max(boundary, boundaries[-1] + 0.1), audio_duration)

        boundaries.append(boundary)

    boundaries.append(audio_duration)

    durations = [
        boundaries[i + 1] - boundaries[i]
        for i in range(len(scenes))
    ]

    if any(duration <= 0 for duration in durations):
        raise RuntimeError("Invalid scene durations calculated.")

    # Make the complete sequence exactly match the narration duration.
    duration_sum = sum(durations)

    return [
        duration * audio_duration / duration_sum
        for duration in durations
    ]


def ass_time(seconds):
    seconds = max(0, float(seconds))

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remaining = seconds % 60

    return (
        f"{hours}:"
        f"{minutes:02d}:"
        f"{remaining:05.2f}"
    )


def ass_color(hex_color, opacity=1.0):
    color = str(hex_color).strip().lstrip("#")

    if len(color) != 6:
        color = "FFFFFF"

    red = int(color[0:2], 16)
    green = int(color[2:4], 16)
    blue = int(color[4:6], 16)

    alpha = round((1 - max(0, min(1, opacity))) * 255)

    return f"&H{alpha:02X}{blue:02X}{green:02X}{red:02X}"


def escape_ass_text(text):
    text = str(text)

    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", " ")
    )


def get_caption_font(caption_data):
    font_info = caption_data.get("font", {})
    filename = font_info.get("filename", "")

    font_path = FONTS_DIR / filename

    if font_path.exists():
        return font_path.stem

    available = list(FONTS_DIR.glob("*.ttf"))

    if available:
        selected = random.choice(available)
        print(f"[WARNING] Caption font missing. Using {selected.name}")
        return selected.stem

    print("[WARNING] No custom font found. Using Arial.")
    return "Arial"


def get_caption_position(style):
    position = style.get("position", "lower")

    if position == "center":
        return 5, 0

    if position == "upper":
        return 8, 260

    return 2, 320


def build_karaoke_text(words):
    result = []

    for index, word in enumerate(words):
        text = escape_ass_text(word.get("text", ""))

        if not text:
            continue

        try:
            start = float(word["start"])
            end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue

        duration = max(0.01, end - start)
        duration_cs = max(1, round(duration * 100))

        result.append(f"{{\\kf{duration_cs}}}{text}")

        if index < len(words) - 1:
            result.append(" ")

    return "".join(result)


def generate_ass_file(caption_data, ass_path):
    style = caption_data.get("style", {})
    captions = caption_data.get("captions", [])

    if not captions:
        raise ValueError("No captions found.")

    font_info = caption_data.get("font", {})
    font_filename = font_info.get("filename", "")
    font_path = FONTS_DIR / font_filename

    if not font_path.exists():
        raise FileNotFoundError(f"Caption font missing: {font_path}")

    font_name = font_path.stem
    font_size = int(style.get("font_size", 86))
    outline_width = int(style.get("outline_width", 6))
    shadow = int(style.get("shadow", 3))

    white = ass_color("#FFFFFF")
    yellow = ass_color("#FFD21F")
    outline = ass_color("#000000")

    script = [
        "[Script Info]",
        "Title: AI Philosopher Captions",
        "; AIPhilosopherCaptionFormat: per-word-highlight-v1",
        "ScriptType: v4.00+",
        f"PlayResX: {WIDTH}",
        f"PlayResY: {HEIGHT}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, "
        "SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        (
            f"Style: Caption,{font_name},{font_size},"
            f"{white},{white},{outline},&H00000000,"
            f"-1,-1,0,0,100,100,0,0,"
            f"1,{outline_width},{shadow},5,"
            f"70,70,0,1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, "
        "MarginR, MarginV, Effect, Text"
    ]

    for caption in captions:
        words = caption.get("words", [])

        if not words:
            continue

        try:
            caption_end = float(caption["end"])
        except (KeyError, TypeError, ValueError):
            continue

        clean_words = []

        for word in words:
            try:
                start = float(word["start"])
                end = float(word["end"])
                text = str(word.get("text", "")).strip().upper()
            except (KeyError, TypeError, ValueError):
                continue

            if text and end > start:
                clean_words.append({
                    "text": text,
                    "start": start,
                    "end": end
                })

        if not clean_words:
            continue

        # Divide long phrases into two balanced lines.
        midpoint = (len(clean_words) + 1) // 2

        line_groups = [
            clean_words[:midpoint],
            clean_words[midpoint:]
        ]

        # Build the phrase with ASS line breaks.
        # Important: insert \N AFTER escaping individual words.
        phrase_parts = []

        for line_index, line_words in enumerate(line_groups):
            if not line_words:
                continue

            if phrase_parts:
                phrase_parts.append(r"\N")

            phrase_parts.append(" ".join(
                escape_ass_text(word["text"])
                for word in line_words
            ))

        plain_phrase = "".join(phrase_parts)

        # Keep the complete phrase visible and change only
        # the currently spoken word to yellow.
        for active_index, active_word in enumerate(clean_words):
            start = active_word["start"]

            if active_index + 1 < len(clean_words):
                end = clean_words[active_index + 1]["start"]
            else:
                end = caption_end

            end = min(end, caption_end)

            if end <= start:
                continue

            highlighted_parts = []
            word_index = 0

            for line_index, line_words in enumerate(line_groups):
                if not line_words:
                    continue

                if highlighted_parts:
                    highlighted_parts.append(r"\N")

                line_parts = []

                for word in line_words:
                    color = (
                        yellow
                        if word_index == active_index
                        else white
                    )

                    line_parts.append(
                        f"{{\\c{color}}}"
                        f"{escape_ass_text(word['text'])}"
                        f"{{\\c{white}}}"
                    )

                    word_index += 1

                highlighted_parts.append(" ".join(line_parts))

            highlighted_text = "".join(highlighted_parts)

            script.append(
                f"Dialogue: 0,{ass_time(start)},{ass_time(end)},"
                f"Caption,,0,0,0,,{highlighted_text}"
            )

    ass_path.parent.mkdir(parents=True, exist_ok=True)

    with open(ass_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(script) + "\n")

    return ass_path


def has_current_caption_format(ass_path):
    if not ass_path.is_file():
        return False

    with open(ass_path, "r", encoding="utf-8-sig") as f:
        return "; AIPhilosopherCaptionFormat: per-word-highlight-v1" in f.read()


def escape_filter_path(path):
    path = str(Path(path).resolve()).replace("\\", "/")

    path = path.replace(":", r"\:")
    path = path.replace("'", r"\'")
    path = path.replace(",", r"\,")

    return path


def build_video(
    images,
    durations,
    audio_path,
    output_path,
    audio_duration,
    ass_path,
    music_path=None
):
    count = len(images)

    if count != len(durations):
        raise ValueError("Image and duration counts do not match.")

    if count == 0:
        raise ValueError("No images supplied to video generator.")

    command = ["ffmpeg", "-y", "-hide_banner"]

    # Add image inputs.
    for index, (image_path, duration) in enumerate(
        zip(images, durations)
    ):
        segment_duration = duration

        if index < count - 1:
            segment_duration += TRANSITION

        command.extend([
            "-loop", "1",
            "-framerate", str(FPS),
            "-t", f"{segment_duration + 0.5:.3f}",
            "-i", str(image_path)
        ])

    # Narration input.
    audio_index = count

    command.extend([
        "-i", str(audio_path)
    ])

    # Optional looping background music.
    music_index = None

    if music_path:
        music_index = count + 1

        command.extend([
            "-stream_loop", "-1",
            "-i", str(music_path)
        ])

    filters = []

    # Prepare each scene with slow zoom.
    for index, duration in enumerate(durations):
        segment_duration = duration

        if index < count - 1:
            segment_duration += TRANSITION

        frames = max(1, math.ceil(segment_duration * FPS))

        filters.append(
            f"[{index}:v]"
            f"scale={WIDTH}:{HEIGHT}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},"
            f"zoompan="
            f"z='min(zoom+0.0005,1.08)':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d={frames}:"
            f"s={WIDTH}x{HEIGHT}:"
            f"fps={FPS},"
            f"setsar=1,"
            f"settb=AVTB,"
            f"setpts=PTS-STARTPTS"
            f"[v{index}]"
        )

    # Crossfade the scenes.
    if count == 1:
        video_label = "[v0]"

    else:
        previous_label = "[v0]"
        elapsed = 0.0

        for index in range(1, count):
            output_label = f"[x{index}]"

            elapsed += durations[index - 1]

            filters.append(
                f"{previous_label}[v{index}]"
                f"xfade=transition=fade:"
                f"duration={TRANSITION}:"
                f"offset={elapsed:.3f}"
                f"{output_label}"
            )

            previous_label = output_label

        video_label = previous_label

    # Apply designed, timed captions after scene transitions.
    ass_filter_path = escape_filter_path(ass_path)
    font_directory = escape_filter_path(FONTS_DIR)

    filters.append(
        f"{video_label}"
        f"subtitles=filename='{ass_filter_path}':"
        f"fontsdir='{font_directory}',"
        f"trim=duration={audio_duration:.3f},"
        f"setpts=PTS-STARTPTS,"
        f"fps={FPS},"
        f"format=yuv420p"
        f"[vout]"
    )

    # Narration and background music.
    if music_path:
        filters.append(
            f"[{music_index}:a]"
            f"volume={MUSIC_VOLUME},"
            f"atrim=duration={audio_duration:.3f},"
            f"asetpts=PTS-STARTPTS[music]"
        )

        filters.append(
            f"[{audio_index}:a]"
            f"volume=1.0,"
            f"aresample=44100,"
            f"asetpts=PTS-STARTPTS[voice]"
        )

        filters.append(
            "[voice][music]"
            "amix=inputs=2:duration=first:"
            "dropout_transition=2,"
            f"atrim=duration={audio_duration:.3f},"
            "asetpts=PTS-STARTPTS[aout]"
        )

    else:
        filters.append(
            f"[{audio_index}:a]"
            f"aresample=44100,"
            f"atrim=duration={audio_duration:.3f},"
            f"asetpts=PTS-STARTPTS[aout]"
        )

    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", VIDEO_PRESET,
        "-crf", str(VIDEO_CRF),
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{audio_duration:.3f}",
        "-movflags", "+faststart",
        str(output_path)
    ])

    run_command(command)


def prepare_captions(data, video_id, story_output):
    caption_path = story_output / "captions.json"

    if not caption_path.exists():
        print("[CAPTIONS] Generating caption data...")

        result = generate_caption_data(video_id)

        if not result:
            raise RuntimeError("Caption generator returned no data.")

        data["captions_generated"] = True
        save_json(DATA_DIR / f"{video_id}.json", data)

    caption_data = load_json(caption_path)

    if not caption_data.get("captions"):
        raise RuntimeError("captions.json contains no captions.")

    data["captions_generated"] = True

    return caption_data


def process_story(json_path):
    data = load_json(json_path)

    video_id = data.get("video_id", json_path.stem)
    title = data.get("title", video_id)

    story_output = OUTPUT_DIR / video_id
    story_output.mkdir(parents=True, exist_ok=True)

    final_path = story_output / "final_video.mp4"
    temp_path = story_output / "final_video.tmp.mp4"

    audio_path = story_output / "voiceover.mp3"
    timing_path = story_output / "timing.json"
    ass_path = story_output / "captions.ass"

    if data.get("video_generated") and final_path.exists():
        if has_current_caption_format(ass_path):
            print(f"[SKIP] {video_id}: Video already generated.")
            return

        print(
            f"[WARNING] {video_id}: Re-rendering video to update captions."
        )

    if data.get("video_generated") and not final_path.exists():
        print(f"[WARNING] {video_id}: Video flag is true but file is missing.")
        data["video_generated"] = False
        save_json(json_path, data)

    if not data.get("slides_generated"):
        print(f"[SKIP] {video_id}: Slides are not generated.")
        return

    if not data.get("voiceover_generated"):
        print(f"[SKIP] {video_id}: Voiceover is not generated.")
        return

    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise FileNotFoundError(
            f"Voiceover is missing or empty: {audio_path}"
        )

    scenes = data.get("scenes", [])

    if not scenes:
        raise RuntimeError("No scenes found in story JSON.")

    images = get_scene_images(scenes, story_output)

    print(f"\n[VIDEO] Processing: {title}")
    print(f"[ID] {video_id}")

    audio_duration = get_audio_duration(audio_path)

    print(f"[VOICE] Duration: {audio_duration:.2f}s")

    durations = get_scene_durations(
        scenes,
        timing_path,
        audio_duration
    )

    caption_data = prepare_captions(
        data,
        video_id,
        story_output
    )

    generate_ass_file(caption_data, ass_path)

    print(f"[CAPTIONS] Style: {caption_data.get('style_name', 'custom')}")
    print(f"[CAPTIONS] Font: {caption_data.get('font', {}).get('filename', 'Arial')}")
    print(f"[CAPTIONS] Count: {len(caption_data['captions'])}")

    music_files = get_music_files()
    music_path = random.choice(music_files) if music_files else None

    if music_path:
        print(f"[MUSIC] Selected: {music_path.relative_to(BASE_DIR)}")
    else:
        print("[MUSIC] No music found. Using narration only.")

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
            ass_path=ass_path,
            music_path=music_path
        )

        if not temp_path.exists() or temp_path.stat().st_size == 0:
            raise RuntimeError("FFmpeg did not create the video.")

        video_duration = get_video_duration(temp_path)

        if video_duration <= 0:
            raise RuntimeError("Final video has invalid duration.")

        if not has_audio_stream(temp_path):
            raise RuntimeError("Final video has no audio stream.")

        if abs(video_duration - audio_duration) > 1.0:
            raise RuntimeError(
                f"Video duration ({video_duration:.2f}s) does not "
                f"match voiceover ({audio_duration:.2f}s)."
            )

        temp_path.replace(final_path)

        data["captions_generated"] = True
        data["video_generated"] = True

        save_json(json_path, data)

        print(f"[SUCCESS] Video generated: {final_path}")
        print(f"[INFO] Video duration: {video_duration:.2f}s")
        print("[INFO] Narration audio verified.")
        print("[INFO] Caption overlay rendered.")

    except Exception:
        data["video_generated"] = False
        save_json(json_path, data)

        if temp_path.exists():
            temp_path.unlink()

        raise


def main(filename=None):
    if filename:
        json_files = [DATA_DIR / filename]
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

            if data.get("video_generated"):
                final_path = OUTPUT_DIR / video_id / "final_video.mp4"

                if final_path.exists() and final_path.stat().st_size > 0:
                    continue

            if not data.get("slides_generated"):
                continue

            if not data.get("voiceover_generated"):
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
