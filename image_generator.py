import base64
import os
from io import BytesIO
import requests
from PIL import Image, ImageOps

ACCOUNT_ID = "6c8155838548cbf420eb3c284f0efd5c"
API_TOKEN = "cfut_mDclnywXEl8f20KmnPHVdwHaZX7vErUF5yZUeori9dc3460e"

url = (
    f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"
    "/ai/run/@cf/black-forest-labs/flux-1-schnell"
)
headers = {"Authorization": f"Bearer {API_TOKEN}"}
payload = {
    "prompt": "antique brass marine compass resting on a weathered parchment nautical chart, soft candlelit shadows, close-up top-down vertical framing, realistic detail",
    "steps": 4,
}

resp = requests.post(url, headers=headers, json=payload, timeout=60)
if resp.status_code != 200:
    print("Cloudflare error response:", resp.text)
    resp.raise_for_status()

img_b64 = resp.json()["result"]["image"]
img_bytes = base64.b64decode(img_b64)
img = Image.open(BytesIO(img_bytes)).convert("RGB")
fitted = ImageOps.fit(img, (1080, 1920), method=Image.Resampling.LANCZOS)
fitted.save("cf_test_1.jpg", format="JPEG", quality=90)
print("Saved cf_test_1.jpg:", fitted.size)