import hashlib
import hmac
import json
import os
import random
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv, set_key

load_dotenv()  # reads .env in the current directory into environment variables

app_key = os.environ.get("TIKTOK_APP_KEY")
app_secret = os.environ.get("TIKTOK_APP_SECRET")
access_token = os.environ.get("TIKTOK_ACCESS_TOKEN")
shop_cipher = os.environ.get("SHOP_CIPHER")

base_url = "https://open-api.tiktokglobalshop.com"
AUTHORIZED_SHOPS_PATH = "/authorization/202309/shops"
MARKETPLACE_SEARCH_PATH = "/affiliate_seller/202508/marketplace_creators/search"

CREATORS_LIST_CSV = "all_creators_handleonly.csv"
CONSOLIDATED_CSV = "creators_found.csv"
MANIFEST_CSV = "creators_manifest.csv"

RATE_LIMIT_CODE = 36009002
DELAY_BETWEEN_CALLS = 5.0  # seconds between successful chunk calls — was 1.0, too fast to stay under the limit


def generate_sign(path: str, params: dict, app_secret: str, body: str = "") -> str:
    """
    Generate the TikTok Shop 'sign' value for a request.

    Args:
        path: The request path only, e.g. "/authorization/202309/shops"
              (no domain, no query string).
        params: Dict of query parameters that will be sent with the request.
                Include app_key and timestamp here. Do NOT include 'sign' itself
                (it's excluded automatically even if present).
        app_secret: Your TikTok Shop App Secret.
        body: Raw JSON request body string, only for requests that have one
              (e.g. POST with a JSON payload). Leave as "" for GET requests
              or requests with no body.

    Returns:
        Lowercase hex-encoded HMAC-SHA256 signature string.
    """
    filtered = {k: v for k, v in params.items() if k not in ("sign", "access_token")}
    sorted_params = sorted(filtered.items())
    param_string = "".join(f"{k}{v}" for k, v in sorted_params)
    base_string = f"{path}{param_string}"
    if body:
        base_string += body
    signed_string = f"{app_secret}{base_string}{app_secret}"

    return hmac.new(
        app_secret.encode("utf-8"),
        signed_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def build_signed_params(path: str, params: dict, app_secret: str, body: str = "") -> dict:
    """
    Convenience wrapper: returns params with 'sign' added, ready to send.
    Does not mutate the input dict.
    """
    params_with_sign = dict(params)
    params_with_sign["sign"] = generate_sign(path, params, app_secret, body)
    return params_with_sign


def build_keyword(handles: list[str]) -> str:
    """Builds the '@handle1|@handle2|...' keyword string for a multi-handle search."""
    return "@" + "|@".join(handles)


def get_and_save_shop_cipher() -> str:
    """
    Calls Get Authorized Shops and saves the first shop's cipher to .env as
    SHOP_CIPHER. Returns the cipher too, in case you want to use it directly.

    Note: `shop_cipher` at module level was loaded from .env at import time.
    Calling this afterward updates the .env FILE, but the already-imported
    `shop_cipher` name in your notebook won't update on its own. After
    calling this, either:
        shop_cipher = get_and_save_shop_cipher()
    or re-run load_dotenv(override=True) and reassign it, so the rest of
    your notebook picks up the new value.
    """
    params = {
        "app_key": app_key,
        "timestamp": int(time.time()),
    }
    signed_params = build_signed_params(AUTHORIZED_SHOPS_PATH, params, app_secret)

    headers = {
        "x-tts-access-token": access_token,
        "content-type": "application/json",
    }

    response = requests.get(
        f"{base_url}{AUTHORIZED_SHOPS_PATH}", params=signed_params, headers=headers, timeout=15
    )
    result = response.json()

    if result.get("code") != 0:
        raise RuntimeError(f"Get Authorized Shops failed: {result}")

    cipher = result["data"]["shops"][0]["cipher"]
    set_key(".env", "SHOP_CIPHER", cipher)
    print(f"Saved SHOP_CIPHER to .env: {cipher}")
    return cipher


def search_creators_with_retry(keyword: str, page_size: int = 20, max_retries: int = 8, base_delay: float = 5.0, max_delay: float = 60.0) -> dict:
    """
    Calls Seller Search Creator on Marketplace for the given keyword, with
    exponential backoff + jitter on TikTok's rate-limit error (code 36009002).
    Any other error code is returned immediately without retrying.

    Delay grows as base_delay * 2^attempt (capped at max_delay), plus jitter.
    With defaults: ~5s, 10s, 20s, 40s, 60s, 60s, 60s, 60s — over 5 minutes
    of total patience before giving up, since TikTok's rate-limit window
    may take longer to clear than a few quick retries account for.
    """
    params = {
        "app_key": app_key,
        "timestamp": int(time.time()),
        "shop_cipher": shop_cipher,
        "page_size": page_size,
    }
    body_dict = {"keyword": keyword, "search_key": ""}
    body = json.dumps(body_dict)
    signed_params = build_signed_params(MARKETPLACE_SEARCH_PATH, params, app_secret, body)

    for attempt in range(max_retries):
        response = requests.post(
            f"{base_url}{MARKETPLACE_SEARCH_PATH}",
            params=signed_params,
            data=body,
            headers={"x-tts-access-token": access_token, "content-type": "application/json"},
            timeout=15,
        )
        result = response.json()

        if result.get("code") != RATE_LIMIT_CODE:
            return result

        if attempt == max_retries - 1:
            raise RuntimeError(f"Still rate-limited after {max_retries} attempts: {result}")

        delay = min(base_delay * (2 ** attempt), max_delay) + random.uniform(0, 1)
        print(f"Rate limited (attempt {attempt + 1}/{max_retries}). Waiting {delay:.1f}s...")
        time.sleep(delay)


def run_pass(handles_to_find: list[str], chunk_size: int, df_creators: pd.DataFrame) -> tuple[list[str], set, pd.DataFrame]:
    """
    Runs a single pass over handles_to_find, chunked into groups of chunk_size.
    Each chunk is searched exactly once (linear pass, no re-searching within
    this call). Saves progress to CSV after every chunk.

    Returns (handles still not found after this pass, found_usernames, updated df_creators).
    """
    chunks = [handles_to_find[i:i + chunk_size] for i in range(0, len(handles_to_find), chunk_size)]

    for i, chunk in enumerate(chunks, start=1):
        keyword = build_keyword(chunk)
        print(f"[chunk_size={chunk_size}] {i}/{len(chunks)}: searching {chunk}")

        result = search_creators_with_retry(keyword=keyword)

        if result.get("code") != 0:
            print(f"  ⚠️  Search failed: {result}")
        else:
            creators = result.get("data", {}).get("creators", [])
            if creators:
                new_df = pd.DataFrame(creators)
                df_creators = pd.concat([df_creators, new_df], ignore_index=True)
                df_creators = df_creators.drop_duplicates(subset="creator_open_id", keep="first")

        df_creators.to_csv(CONSOLIDATED_CSV, index=False)  # save progress every chunk
        time.sleep(DELAY_BETWEEN_CALLS)

    found_usernames = set(df_creators["username"]) if not df_creators.empty else set()
    still_not_found = [h for h in handles_to_find if h not in found_usernames]

    return still_not_found, found_usernames, df_creators
