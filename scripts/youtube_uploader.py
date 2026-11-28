
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
CONFIG_DIR = BASE_DIR / "config"

CLIENT_SECRET_FILE = CONFIG_DIR / "client_secret.json"
TOKEN_FILE = CONFIG_DIR / "coldcase_token.json"

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

PRIVACY_STATUS = os.getenv("YOUTUBE_PRIVACY_STATUS", "public")
CHUNK_SIZE = 8 * 1024 * 1024
MAX_UPLOADS_PER_RUN = 1

CATEGORY_IDS = {
    "film": "1",
    "autos": "2",
    "music": "10",
    "sports": "17",
    "travel": "19",
    "gaming": "20",
    "people": "22",
    "comedy": "23",
    "entertainment": "24",
    "mystery": "24",
    "news": "25",
    "politics": "25",
    "education": "27",
    "science": "28",
    "technology": "28",
    "howto": "26"
}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    temp_path = path.with_suffix(".tmp")

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    temp_path.replace(path)


def get_credentials():
    token_json = os.getenv("YOUTUBE_TOKEN_JSON")

    if token_json:
        try:
            credentials = Credentials.from_authorized_user_info(
                json.loads(token_json),
                SCOPES
            )
        except Exception as e:
            raise RuntimeError(f"Invalid YOUTUBE_TOKEN_JSON: {e}")

    elif TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(
            str(TOKEN_FILE),
            SCOPES
        )

    else:
        raise FileNotFoundError(
            "YouTube token not found. Provide YOUTUBE_TOKEN_JSON "
            "or config/coldcase_token.json."
        )

    if credentials.expired:
        if not credentials.refresh_token:
            raise RuntimeError(
                "YouTube token expired and has no refresh token. "
                "Generate a valid token manually."
            )

        credentials.refresh(Request())

        if not token_json:
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(credentials.to_json())

        print("[AUTH] YouTube token refreshed.")

    if not credentials.valid:
        raise RuntimeError("YouTube credentials are invalid.")

    return credentials


def get_youtube_service():
    credentials = get_credentials()

    return build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )


def get_category_id(data):
    custom_category = data.get("youtube_category_id")

    if custom_category:
        return str(custom_category)

    category = str(data.get("category", "entertainment")).lower().strip()

    return CATEGORY_IDS.get(category, "24")


def prepare_tags(tags):
    if not isinstance(tags, list):
        return []

    result = []
    remaining = 500

    for tag in tags:
        tag = str(tag).strip()

        if not tag:
            continue

        separator_length = 1 if result else 0
        available = remaining - separator_length

        if available <= 0:
            break

        tag = tag[:available]
        result.append(tag)
        remaining -= len(tag) + separator_length

    return result


def upload_video(youtube, video_path, data):
    title = str(data.get("title", "Untitled Video")).strip()
    description = str(data.get("description", "")).strip()

    if not title:
        raise ValueError("Video title is empty.")

    title = title[:100]
    description = description[:5000]

    tags = prepare_tags(data.get("tags", []))

    snippet = {
        "title": title,
        "description": description,
        "categoryId": get_category_id(data)
    }

    if tags:
        snippet["tags"] = tags

    body = {
        "snippet": snippet,
        "status": {
            "privacyStatus": PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=CHUNK_SIZE,
        resumable=True
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    retry_count = 0

    while response is None:
        try:
            status, response = request.next_chunk()

            if status:
                print(
                    f"[UPLOAD] Progress: "
                    f"{int(status.progress() * 100)}%"
                )

            retry_count = 0

        except HttpError:
            raise

        except Exception as e:
            retry_count += 1

            if retry_count > 5:
                raise RuntimeError(
                    f"Upload failed after retries: {e}"
                )

            wait_time = min(2 ** retry_count, 60)

            print(
                f"[WARNING] Upload interrupted: {e}. "
                f"Retrying in {wait_time} seconds..."
            )

            time.sleep(wait_time)

    uploaded_id = response.get("id")

    if not uploaded_id:
        raise RuntimeError("YouTube did not return a video ID.")

    return uploaded_id


def process_video(json_path, youtube):
    data = load_json(json_path)
    video_id = data.get("video_id", json_path.stem)

    if data.get("uploaded"):
        print(f"[SKIP] {video_id}: Already uploaded.")
        return

    if not data.get("video_generated"):
        print(f"[SKIP] {video_id}: Video is not generated.")
        return

    final_path = OUTPUT_DIR / video_id / "final_video.mp4"

    if not final_path.exists() or final_path.stat().st_size == 0:
        print(f"[ERROR] {video_id}: final_video.mp4 is missing.")
        return

    if data.get("youtube_id"):
        print(
            f"[WARNING] {video_id}: YouTube ID already exists "
            f"({data['youtube_id']}), but uploaded is false."
        )
        print("[WARNING] Check YouTube Studio before retrying.")
        return

    print(f"\n[UPLOAD] Starting: {video_id}")
    print(f"[UPLOAD] File: {final_path}")

    try:
        uploaded_id = upload_video(
            youtube,
            final_path,
            data
        )

        data["youtube_id"] = uploaded_id
        data["video_url"] = (
            f"https://www.youtube.com/watch?v={uploaded_id}"
        )
        data["uploaded_at"] = datetime.now(timezone.utc).isoformat()
        data["uploaded"] = True

        save_json(json_path, data)

        print(f"[SUCCESS] Uploaded: {video_id}")
        print(f"[YOUTUBE] {data['video_url']}")

    except HttpError as e:
        print(f"[YOUTUBE ERROR] {video_id}: {e}")

    except Exception as e:
        print(f"[ERROR] {video_id}: {e}")


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

            if data.get("uploaded"):
                continue

            if not data.get("video_generated"):
                continue

            video_id = data.get("video_id", json_path.stem)
            final_path = OUTPUT_DIR / video_id / "final_video.mp4"

            if not final_path.exists() or final_path.stat().st_size == 0:
                print(f"[SKIP] {video_id}: Final video is missing.")
                continue

            if data.get("youtube_id"):
                print(
                    f"[WARNING] {video_id}: Existing YouTube ID "
                    f"{data['youtube_id']}. Check YouTube Studio."
                )
                continue

            pending_files.append(json_path)

        except Exception as e:
            print(f"[ERROR] {json_path.name}: {e}")

    selected_files = pending_files[:MAX_UPLOADS_PER_RUN]

    if not selected_files:
        print("[INFO] No pending videos ready for upload.")
        return

    print(
        f"[INFO] Uploading {len(selected_files)} videos "
        f"(maximum {MAX_UPLOADS_PER_RUN} per run)."
    )

    try:
        youtube = get_youtube_service()
    except Exception as e:
        print(f"[AUTH ERROR] Could not initialize YouTube API: {e}")
        return

    for json_path in selected_files:
        try:
            process_video(json_path, youtube)
        except Exception as e:
            print(f"[ERROR] {json_path.name}: {e}")

    print("\nYouTube upload process completed.")


if __name__ == "__main__":
    filename = sys.argv[1] if len(sys.argv) > 1 else None
    main(filename)
