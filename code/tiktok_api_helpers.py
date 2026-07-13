import json
import os
import random
import time
from pathlib import Path
import pandas as pd
import requests


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
    # Step 1: sort params alphabetically, excluding sign/access_token
    filtered = {k: v for k, v in params.items() if k not in ("sign", "access_token")}
    sorted_params = sorted(filtered.items())

    # Step 2: concatenate sorted param names + values
    param_string = "".join(f"{k}{v}" for k, v in sorted_params)

    # Step 3: prepend the path
    base_string = f"{path}{param_string}"

    # Step 4: append the raw body, if any
    if body:
        base_string += body

    # Step 5: wrap with app_secret on both ends, then HMAC-SHA256
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
    return "@" + "|@".join(h.lstrip("@") for h in handles)


def search_creators_with_retry(keyword: str, page_size: int = 20, max_retries: int = 5, base_delay: float = 2.0) -> dict:
    """
    Calls Seller Search Creator on Marketplace for the given keyword, with
    exponential backoff + jitter on TikTok's rate-limit error (code 36009002).
    Any other error code is returned immediately without retrying.
    """
    params = {
        "app_key": app_key,
        "timestamp": int(time.time()),
        "shop_cipher": shop_cipher,
        "page_size": page_size,
    }
    body_dict = {"keyword": keyword, "search_key": ""}
    body = json.dumps(body_dict)
    signed_params = build_signed_params(path, params, app_secret, body)

    for attempt in range(max_retries):
        response = requests.post(
            f"{base_url}{path}",
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

        delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
        print(f"Rate limited (attempt {attempt + 1}/{max_retries}). Waiting {delay:.1f}s...")
        time.sleep(delay)


def run_pass(handles_to_find: list[str], chunk_size: int, df_creators: pd.DataFrame) -> tuple[list[str], pd.DataFrame]:
    """
    Runs a single pass over handles_to_find, chunked into groups of chunk_size.
    Each chunk is searched exactly once (linear pass, no re-searching within
    this call). Saves progress to CSV after every chunk.

    Returns (handles still not found after this pass, updated df_creators).
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

    found_usernames = set(df_creators["username"].str.lower()) if not df_creators.empty else set()
    still_not_found = [h for h in handles_to_find if h.lstrip("@").lower() not in found_usernames]

    return still_not_found, df_creators
