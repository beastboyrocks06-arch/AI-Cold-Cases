
import os
import json
import base64
import requests

from io import BytesIO
from pathlib import Path
from PIL import Image, ImageOps

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ACCOUNT_ID = "6c8155838548cbf420eb3c284f0efd5c"
API_TOKEN = "cfut_mDclnywXEl8f20KmnPHVdwHaZX7vErUF5yZUeori9dc3460e"

IMAGE_SIZE = (1080, 1920)
IMAGE_QUALITY = 90

API_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"
    "/ai/run/@cf/black-forest-labs/flux-1-schnell"
)

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    temp_path.replace(path)

def generate_image(prompt):
    headers = {
        "Authorization": f"Bearer {API_TOKEN}"
    }

    payload = {
        "prompt": prompt,
        "steps": 4
    }

    response = requests.post(
        API_URL,
        headers=headers,
        json=payload,
        timeout=120
    )

    if response.status_code != 200:
        raise Exception(f"Cloudflare API error: {response.text}")

    result = response.json()

    if not result.get("success"):
        raise Exception(f"Image generation failed: {result}")

    img_b64 = result["result"]["image"]
    img_bytes = base64.b64decode(img_b64)

    return Image.open(BytesIO(img_bytes)).convert("RGB")

def process_story(json_path):
    data = load_json(json_path)

    video_id = data["video_id"]
    video_output = OUTPUT_DIR / video_id
    video_output.mkdir(parents=True, exist_ok=True)

    print(f"\nProcessing: {data['title']}")

    for scene in data.get("scenes", []):
        scene_id = scene["scene_id"]
        image_path = video_output / f"scene_{scene_id:02d}.jpg"

        if scene.get("image_generated") and image_path.exists():
            print(f"[SKIP] Scene {scene_id}: Already generated")
            continue

        if not scene.get("image_prompt"):
            print(f"[SKIP] Scene {scene_id}: Missing image prompt")
            continue

        try:
            print(f"[GENERATING] Scene {scene_id}")

            img = generate_image(scene["image_prompt"])

            fitted = ImageOps.fit(
                img,
                IMAGE_SIZE,
                method=Image.Resampling.LANCZOS
            )

            fitted.save(
                image_path,
                format="JPEG",
                quality=IMAGE_QUALITY
            )

            scene["image_generated"] = True
            save_json(json_path, data)

            print(f"[SUCCESS] Saved {image_path.name}")

        except Exception as e:
            print(f"[ERROR] Scene {scene_id}: {e}")
            continue

    data["slides_generated"] = all(
        scene.get("image_generated", False)
        and (video_output / f"scene_{scene['scene_id']:02d}.jpg").exists()
        for scene in data.get("scenes", [])
    )

    save_json(json_path, data)


def main():
    if not ACCOUNT_ID or not API_TOKEN:
        raise RuntimeError("Missing Cloudflare credentials in environment variables.")

    MAX_STORIES_PER_RUN = 3

    json_files = sorted(DATA_DIR.glob("*.json"))

    if not json_files:
        print("[INFO] No JSON files found.")
        return

    pending_files = []

    for json_path in json_files:
        try:
            data = load_json(json_path)

            if data.get("slides_generated"):
                continue

            pending_scenes = [
                scene for scene in data.get("scenes", [])
                if not scene.get("image_generated")
                or not (OUTPUT_DIR / data["video_id"] / f"scene_{scene['scene_id']:02d}.jpg").exists()
            ]

            if pending_scenes:
                pending_files.append(json_path)

        except Exception as e:
            print(f"[ERROR] {json_path.name}: {e}")

    selected_files = pending_files[:MAX_STORIES_PER_RUN]

    if not selected_files:
        print("[INFO] No pending stories found.")
        return

    print(f"[INFO] Processing {len(selected_files)} stories (maximum {MAX_STORIES_PER_RUN} per run).")

    for json_path in selected_files:
        try:
            process_story(json_path)
        except Exception as e:
            print(f"[ERROR] {json_path.name}: {e}")

    print("\nImage generation completed.")


if __name__ == "__main__":
    main()

