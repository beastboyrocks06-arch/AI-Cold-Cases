
import os
import re
import json
import asyncio
import edge_tts

from pathlib import Path
from mutagen.mp3 import MP3
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"

load_dotenv(BASE_DIR / ".env")

VOICE = os.getenv("TTS_VOICE", "en-US-AndrewNeural")
RATE = os.getenv("TTS_RATE", "+5%")
MAX_STORIES_PER_RUN = 3
WORDS_PER_SECOND = 2.5


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    temp_path.replace(path)


def get_story_text(data):
    scenes = sorted(data.get("scenes", []), key=lambda x: x["scene_id"])
    narration = []

    for scene in scenes:
        text = scene.get("narration", "").strip()
        if text:
            narration.append(text)

    return " ".join(narration)


def get_audio_duration(audio_path):
    try:
        return float(MP3(str(audio_path)).info.length)
    except Exception as e:
        raise RuntimeError(f"Could not read MP3 duration: {e}")


def split_boundary_text(text, start, end):
    words = re.findall(r"\S+", text)

    if not words:
        return []

    if len(words) == 1:
        return [{
            "word": words[0],
            "start": round(start, 3),
            "end": round(max(start, end), 3)
        }]

    total_weight = sum(max(len(word), 1) for word in words)
    current = start
    result = []

    for word in words:
        word_duration = (end - start) * max(len(word), 1) / total_weight

        result.append({
            "word": word,
            "start": round(current, 3),
            "end": round(current + word_duration, 3)
        })

        current += word_duration

    return result


def generate_fallback_timing(text, duration):
    words = re.findall(r"\S+", text)

    if not words:
        return []

    # Allocate time according to word length and punctuation.
    weights = []

    for word in words:
        weight = max(len(re.sub(r"[^\w]", "", word)), 2)

        if word.endswith((".", "!", "?")):
            weight += 5
        elif word.endswith((",", ";", ":")):
            weight += 2

        weights.append(weight)

    total_weight = sum(weights)
    current = 0.0
    timings = []

    for word, weight in zip(words, weights):
        word_duration = duration * weight / total_weight

        timings.append({
            "word": word,
            "start": round(current, 3),
            "end": round(current + word_duration, 3)
        })

        current += word_duration

    if timings:
        timings[-1]["end"] = round(duration, 3)

    return timings


async def generate_voiceover(text, audio_path):
    communicate = edge_tts.Communicate(
        text,
        VOICE,
        rate=RATE
    )

    audio_chunks = []
    word_timings = []

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])

        elif chunk["type"] == "WordBoundary":
            data = chunk["data"]

            start = data.get("offset", 0) / 10_000_000
            boundary_duration = data.get("duration", 0) / 10_000_000
            end = start + boundary_duration

            word_timings.extend(
                split_boundary_text(
                    data.get("text", ""),
                    start,
                    end
                )
            )

    if not audio_chunks:
        raise RuntimeError("Edge TTS returned no audio data.")

    audio_path.write_bytes(b"".join(audio_chunks))

    duration = get_audio_duration(audio_path)

    # Edge TTS sometimes returns audio without WordBoundary events.
    if not word_timings:
        print("[WARNING] No WordBoundary events received.")
        print("[FALLBACK] Generating approximate word timestamps.")

        word_timings = generate_fallback_timing(text, duration)

    if not word_timings:
        raise RuntimeError("Could not generate word timestamps.")

    # Keep all timestamps inside the actual audio duration.
    for item in word_timings:
        item["start"] = max(0, min(item["start"], duration))
        item["end"] = max(item["start"], min(item["end"], duration))

    word_timings[-1]["end"] = round(duration, 3)

    return word_timings, duration


def images_are_ready(data):
    video_output = OUTPUT_DIR / data["video_id"]

    scenes = data.get("scenes", [])

    if not scenes:
        return False

    for scene in scenes:
        scene_id = scene["scene_id"]
        image_path = video_output / f"scene_{scene_id:02d}.jpg"

        if not scene.get("image_generated") or not image_path.exists():
            return False

    return True


async def process_story(json_path):
    data = load_json(json_path)

    video_id = data["video_id"]
    video_output = OUTPUT_DIR / video_id
    video_output.mkdir(parents=True, exist_ok=True)

    audio_path = video_output / "voiceover.mp3"
    timing_path = video_output / "timing.json"

    print(f"\nProcessing: {data['title']}")

    if not images_are_ready(data):
        print("[SKIP] Story images are incomplete.")
        return False

    if (
        data.get("voiceover_generated")
        and audio_path.exists()
        and timing_path.exists()
    ):
        print("[SKIP] Voiceover already generated.")
        return True

    text = get_story_text(data)

    if not text:
        print("[ERROR] No narration found in story JSON.")
        return False

    print(f"[VOICE] {VOICE}")
    print(f"[RATE] {RATE}")
    print(f"[TEXT] {len(text.split())} words")

    try:
        timings, duration = await generate_voiceover(
            text,
            audio_path
        )

        save_json(timing_path, timings)

        data["voiceover_generated"] = True
        save_json(json_path, data)

        print(f"[SUCCESS] Voiceover generated: {audio_path.name}")
        print(f"[SUCCESS] Timing generated: {timing_path.name}")
        print(f"[WORDS] {len(timings)}")
        print(f"[DURATION] {duration:.2f} seconds")

        return True

    except Exception as e:
        print(f"[ERROR] {json_path.name}: {e}")

        audio_path.unlink(missing_ok=True)
        timing_path.unlink(missing_ok=True)

        data["voiceover_generated"] = False
        save_json(json_path, data)

        return False


async def main(filename=None):
    if filename:
        json_path = Path(filename)

        if not json_path.is_absolute():
            json_path = DATA_DIR / json_path

        if not json_path.exists():
            raise FileNotFoundError(f"Story JSON not found: {json_path}")

        await process_story(json_path)
        return

    json_files = sorted(DATA_DIR.glob("*.json"))

    if not json_files:
        print("[INFO] No story JSON files found.")
        return

    pending_files = []

    for json_path in json_files:
        try:
            data = load_json(json_path)

            if not images_are_ready(data):
                continue

            video_output = OUTPUT_DIR / data["video_id"]
            audio_path = video_output / "voiceover.mp3"
            timing_path = video_output / "timing.json"

            if (
                data.get("voiceover_generated")
                and audio_path.exists()
                and timing_path.exists()
            ):
                continue

            pending_files.append(json_path)

        except Exception as e:
            print(f"[ERROR] {json_path.name}: {e}")

    selected_files = pending_files[:MAX_STORIES_PER_RUN]

    if not selected_files:
        print("[INFO] No pending stories with completed images.")
        return

    print(
        f"[INFO] Processing {len(selected_files)} stories "
        f"(maximum {MAX_STORIES_PER_RUN} per run)."
    )

    for json_path in selected_files:
        try:
            await process_story(json_path)
        except Exception as e:
            print(f"[ERROR] {json_path.name}: {e}")

    print("\nVoice generation process completed.")


if __name__ == "__main__":
    asyncio.run(main())
