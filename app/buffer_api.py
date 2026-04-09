import requests
import logging

logger = logging.getLogger(__name__)

BUFFER_API = "https://api.bufferapp.com/1"


def test_connection(access_token: str) -> dict:
    try:
        res = requests.get(
            f"{BUFFER_API}/user.json",
            params={"access_token": access_token},
            timeout=10
        )
        res.raise_for_status()
        data = res.json()
        return {"success": True, "name": data.get("name", "Unknown")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_profiles(access_token: str) -> list:
    try:
        res = requests.get(
            f"{BUFFER_API}/profiles.json",
            params={"access_token": access_token},
            timeout=10
        )
        res.raise_for_status()
        return res.json()
    except Exception as e:
        logger.error(f"Buffer get profiles error: {e}")
        return []


def send_video_to_buffer(
    access_token: str,
    profile_id: str,
    video_path: str,
    caption: str,
    schedule: str = "queue"
) -> dict:
    try:
        with open(video_path, "rb") as f:
            upload_res = requests.post(
                f"{BUFFER_API}/media/upload.json",
                params={"access_token": access_token},
                files={"file": ("video.mp4", f, "video/mp4")},
                timeout=120
            )
        upload_res.raise_for_status()
        media = upload_res.json()
        media_id = media.get("id")

        now = schedule == "now"
        payload = {
            "access_token": access_token,
            "profile_ids[]": profile_id,
            "text": caption,
            "media[video]": media_id,
            "now": str(now).lower(),
            "shorten": "false"
        }
        post_res = requests.post(
            f"{BUFFER_API}/updates/create.json",
            data=payload,
            timeout=30
        )
        post_res.raise_for_status()
        return {"success": True, "data": post_res.json()}

    except Exception as e:
        logger.error("Buffer send error (token redacted)")
        return {"success": False, "error": str(e)}
