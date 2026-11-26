
import json
import re
import sys
import random

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
FONTS_DIR = BASE_DIR / "fonts"

MAX_STORIES_PER_RUN = 3
MAX_WORDS_PER_CAPTION = 4
MAX_CAPTION_DURATION = 2.5

FONT_PREFERENCES = [
    "dejavu-sans-bold.ttf",
    "Blankit-8MW2B.ttf",
    "Cintaly-Eazdl.ttf",
    "PoemLettersSerifRegular-lgggX.ttf"
]

CAPTION_STYLES = {
    "yellow_bold": {
        "font_size": 86,
        "font_color": "#FFD21F",
        "outline_color": "#000000",
        "outline_width": 6,
        "shadow": 3,
        "bold": True,
        "italic": True,
        "position": "center"
    },
    "yellow_bold_large": {
        "font_size": 94,
        "font_color": "#FFD21F",
        "outline_color": "#000000",
        "outline_width": 7,
        "shadow": 3,
        "bold": True,
        "italic": True,
        "position": "center"
    },
    "yellow_bold_compact": {
        "font_size": 78,
        "font_color": "#FFD21F",
        "outline_color": "#000000",
        "outline_width": 6,
        "shadow": 2,
        "bold": True,
        "italic": True,
        "position": "center"
    }
}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    temp_path.replace(path)


def select_font():
    for filename in FONT_PREFERENCES:
        path = FONTS_DIR / filename

        if path.exists():
            return path

    available = list(FONTS_DIR.glob("*.ttf"))

    if available:
        return random.choice(available)

    raise FileNotFoundError(f"No TTF fonts found in {FONTS_DIR}")


def select_style():
    name = random.choice(list(CAPTION_STYLES))
    return name, CAPTION_STYLES[name].copy()


def load_word_timings(path):
    timings = load_json(path)

    if not isinstance(timings, list):
        raise ValueError("timing.json must contain a list.")

    words = []

    for item in timings:
        word = str(item.get("word", "")).strip()

        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue

        if word and start >= 0 and end > start:
            words.append({
                "text": word,
                "start": start,
                "end": end
            })

    if not words:
        raise ValueError("No valid word timings found.")

    return words


def clean_word(word):
    return re.sub(r"\s+", " ", str(word)).strip()


def create_caption(words):
    phrase = " ".join(item["text"] for item in words)

    return {
        "start": round(words[0]["start"], 3),
        "end": round(words[-1]["end"], 3),
        "text": phrase.upper(),
        "words": [
            {
                "text": item["text"],
                "start": round(item["start"], 3),
                "end": round(item["end"], 3)
            }
            for item in words
        ]
    }


def build_captions(words):
    captions = []
    current = []

    for index, item in enumerate(words):
        current.append(item)

        next_word = words[index + 1] if index + 1 < len(words) else None

        current_duration = current[-1]["end"] - current[0]["start"]
        current_text = current[-1]["text"]

        sentence_end = current_text.endswith((".", "?", "!", ";", ":"))
        word_limit = len(current) >= MAX_WORDS_PER_CAPTION
        duration_limit = (
            current_duration >= MAX_CAPTION_DURATION
            or (
                next_word
                and next_word["end"] - current[0]["start"] > MAX_CAPTION_DURATION
            )
        )

        if sentence_end or word_limit or duration_limit or next_word is None:
            captions.append(create_caption(current))
            current = []

    return captions


def generate_caption_data(video_id):
    story_dir = OUTPUT_DIR / video_id
    timing_path = story_dir / "timing.json"
    caption_path = story_dir / "captions.json"

    if not timing_path.exists():
        raise FileNotFoundError(f"Missing timing file: {timing_path}")

    words = load_word_timings(timing_path)

    font_path = select_font()
    style_name, style = select_style()

    captions = build_captions(words)

    if not captions:
        raise ValueError("No captions generated.")

    result = {
        "video_id": video_id,
        "width": 1080,
        "height": 1920,
        "style_name": style_name,
        "font": {
            "filename": font_path.name,
            "path": str(font_path.resolve())
        },
        "style": style,
        "caption_count": len(captions),
        "captions": captions
    }

    save_json(caption_path, result)

    return result


def process_story(json_path):
    data = load_json(json_path)
    video_id = data.get("video_id", json_path.stem)

    if not data.get("voiceover_generated"):
        print(f"[SKIP] {video_id}: Voiceover not generated.")
        return

    try:
        result = generate_caption_data(video_id)

        data["captions_generated"] = True
        save_json(json_path, data)

        print(f"[SUCCESS] {video_id}")
        print(f"[STYLE] {result['style_name']}")
        print(f"[FONT] {result['font']['filename']}")
        print(f"[CAPTIONS] {result['caption_count']}")

    except Exception as e:
        data["captions_generated"] = False
        save_json(json_path, data)
        print(f"[ERROR] {json_path.name}: {e}")


def main(filename=None):
    json_files = (
        [DATA_DIR / filename]
        if filename
        else sorted(DATA_DIR.glob("*.json"))
    )

    pending = []

    for path in json_files:
        if not path.exists():
            print(f"[ERROR] Missing JSON: {path}")
            continue

        try:
            data = load_json(path)
            video_id = data.get("video_id", path.stem)

            if not data.get("voiceover_generated"):
                continue

            caption_path = OUTPUT_DIR / video_id / "captions.json"

            if data.get("captions_generated") and caption_path.exists():
                continue

            pending.append(path)

        except Exception as e:
            print(f"[ERROR] {path.name}: {e}")

    selected = pending[:MAX_STORIES_PER_RUN]

    if not selected:
        print("[INFO] No stories waiting for captions.")
        return

    print(f"Processing {len(selected)} stories (maximum {MAX_STORIES_PER_RUN}).")

    for path in selected:
        process_story(path)

    print("\nCaption generation completed.")


if __name__ == "__main__":
    filename = sys.argv[1] if len(sys.argv) > 1 else None
    main(filename)
