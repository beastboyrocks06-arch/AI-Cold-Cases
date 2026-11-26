import asyncio
import json
import edge_tts

VOICE = "en-US-AndrewNeural"  # Natural storytelling voice (also try en-US-BrianNeural or en-US-AvaNeural)
OUTPUT_AUDIO = "voiceover.mp3"
OUTPUT_TIMING = "timing.json"

async def main():
    text = (
        "In 1872, a merchant ship was found drifting in the Atlantic. "
        "Her sails were set, the cargo was untouched, and six months of food remained. "
        "Yet not a single crew member was ever found."
    )

    communicate = edge_tts.Communicate(text, VOICE, rate="+5%")
    word_timings = []

    with open(OUTPUT_AUDIO, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # Convert 100-nanosecond ticks to seconds
                start_sec = round(chunk["offset"] / 10_000_000, 3)
                duration_sec = round(chunk["duration"] / 10_000_000, 3)
                word_timings.append({
                    "word": chunk["text"],
                    "start": start_sec,
                    "end": round(start_sec + duration_sec, 3)
                })

    with open(OUTPUT_TIMING, "w", encoding="utf-8") as f:
        json.dump(word_timings, f, indent=2)

    print(f"Saved {OUTPUT_AUDIO} and {len(word_timings)} word timestamps.")

if __name__ == "__main__":
    asyncio.run(main())