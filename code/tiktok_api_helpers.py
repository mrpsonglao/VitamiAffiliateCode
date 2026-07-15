import hashlib
import hmac
import json
import logging
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
CREATE_TARGET_COLLABORATION_PATH = "/affiliate_seller/202508/target_collaborations"
QUERY_TARGET_COLLABORATION_PATH_TEMPLATE = "/affiliate_seller/202508/target_collaborations/{}"
CHECK_TARGET_COLLABORATION_CONFLICTS_PATH = "/affiliate_seller/202605/target_collaborations/conflicts/check"
CREATE_CONVERSATION_PATH = "/affiliate_seller/202508/conversations"
GET_CONVERSATION_LIST_PATH = "/affiliate_seller/202412/conversations"
SEND_IM_MESSAGE_PATH_TEMPLATE = "/affiliate_seller/202412/conversations/{}/messages"

CONSOLIDATED_CSV = "creators_found.csv"
MANIFEST_CSV = "creators_manifest.csv"
TARGET_COLLAB_MANIFEST_CSV = "target_collaboration_manifest.csv"
CONFLICTS_MANIFEST_CSV = "collab_conflicts_manifest.csv"

RATE_LIMIT_CODE = 36009002
DELAY_BETWEEN_CALLS = 30.0  # seconds between successful chunk calls — raised again: even 15s still needed a retry on nearly every chunk

LOG_FILE = "run_pass.log"

logger = logging.getLogger("tiktok_api_helpers.run_pass")
logger.setLevel(logging.INFO)
if not logger.handlers:  # avoid duplicate handlers if this module is re-imported (e.g. notebook re-run)
    _file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")  # append, never overwrite
    _file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(_file_handler)


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


def call_api(
    method: str,
    path: str,
    query_params: dict | None = None,
    body_dict: dict | None = None,
    max_retries: int = 3,
    base_delay: float = 15.0,
    max_delay: float = 45.0,
) -> dict:
    """
    Generic signed call to a TikTok Shop Open API endpoint. Handles:
      - building + signing query params (app_key/timestamp added automatically)
      - GET vs POST dispatch
      - exponential backoff + jitter on the rate-limit error (code 36009002)

    IMPORTANT: the signature (and timestamp) are rebuilt fresh on EVERY retry
    attempt, not once before the loop. TikTok's timestamp has a freshness
    window, so reusing a signature built before a 60s backoff wait would risk
    the retry failing for a different reason (stale timestamp) than the
    original rate limit.

    query_params: endpoint-specific query params (e.g. shop_cipher, page_size).
                  app_key and timestamp are added automatically each attempt.
    body_dict: the JSON request body as a dict, or None for GET/no-body calls.

    Does not raise on persistent rate-limiting — returns the last response
    as-is so the caller can log it and move on rather than crash.
    """
    query_params = dict(query_params or {})
    body = json.dumps(body_dict) if body_dict is not None else ""
    headers = {"x-tts-access-token": access_token, "content-type": "application/json"}

    result = None
    for attempt in range(max_retries):
        query_params["app_key"] = app_key
        query_params["timestamp"] = int(time.time())  # fresh every attempt
        if shop_cipher:
            query_params.setdefault("shop_cipher", shop_cipher)
        signed_params = build_signed_params(path, query_params, app_secret, body)

        if method.upper() == "GET":
            response = requests.get(f"{base_url}{path}", params=signed_params, headers=headers, timeout=15)
        else:
            response = requests.post(f"{base_url}{path}", params=signed_params, data=body, headers=headers, timeout=15)

        result = response.json()

        if result.get("code") != RATE_LIMIT_CODE:
            return result

        if attempt == max_retries - 1:
            msg = f"  ⚠️  Still rate-limited after {max_retries} attempts — giving up for now."
            logger.info(msg)
            print(msg)
            return result

        delay = min(base_delay * (2 ** attempt), max_delay) + random.uniform(0, 1)
        logger.info(f"Rate limited (attempt {attempt + 1}/{max_retries}). Waiting {delay:.1f}s...")
        time.sleep(delay)

    return result


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
    result = call_api("GET", AUTHORIZED_SHOPS_PATH)

    if result.get("code") != 0:
        raise RuntimeError(f"Get Authorized Shops failed: {result}")

    cipher = result["data"]["shops"][0]["cipher"]
    set_key(".env", "SHOP_CIPHER", cipher)
    print(f"Saved SHOP_CIPHER to .env: {cipher}")
    return cipher


def search_creators_with_retry(keyword: str, search_key: str = "", page_size: int = 20, **retry_kwargs) -> dict:
    """
    Seller Search Creator on Marketplace.
    page_size must be 12 or 20 per the doc's requirement.
    search_key: pass the value from a previous response's data.search_key
    to help TikTok cache/stabilize the search. Leave "" for a first call.
    """
    if page_size not in (12, 20):
        raise ValueError("page_size must be 12 or 20")

    query_params = {"page_size": page_size}
    body_dict = {"keyword": keyword, "search_key": search_key}
    return call_api("POST", MARKETPLACE_SEARCH_PATH, query_params, body_dict, **retry_kwargs)


def create_target_collaboration(
    name: str,
    end_time: int,
    products: list[dict],
    creator_user_open_ids: list[str],
    seller_contact_info: dict,
    free_sample_rule: dict,
    message: str | None = None,
    **retry_kwargs,
) -> dict:
    """
    Creates a Target Collaboration, inviting creators to promote specific products.

    products: list of {"id": ..., "target_commission_rate": ..., "shop_ads_commission_rate": ...}
              (max 100 entries)
    creator_user_open_ids: list of creator_open_id strings (max 50 entries)
    seller_contact_info: dict with "email" required, other contact fields optional
    free_sample_rule: {"has_free_sample": bool, "is_sample_approval_exempt": bool}
    end_time: Unix epoch timestamp (int) for when the collaboration ends
    """
    if len(products) > 100:
        raise ValueError("products can have at most 100 entries")
    if len(creator_user_open_ids) > 50:
        raise ValueError("creator_user_open_ids can have at most 50 entries")

    query_params = None
    body_dict = {
        "name": name,
        "end_time": str(end_time),
        "products": products,
        "creator_user_open_ids": creator_user_open_ids,
        "seller_contact_info": seller_contact_info,
        "free_sample_rule": free_sample_rule,
    }
    if message:
        body_dict["message"] = message

    return call_api("POST", CREATE_TARGET_COLLABORATION_PATH, query_params, body_dict, **retry_kwargs)


def query_target_collaboration_detail(target_collaboration_id: str, **retry_kwargs) -> dict:
    """Fetches full detail (products, creators, status) for a given target collaboration."""
    path = QUERY_TARGET_COLLABORATION_PATH_TEMPLATE.format(target_collaboration_id)
    return call_api("GET", path, query_params=None, body_dict=None, **retry_kwargs)


def check_target_collaboration_conflicts(creator_open_id_list: list[str], product_id_list: list[str], **retry_kwargs) -> dict:
    """
    Checks whether any (creator, product) pairs already have an existing
    target collaboration, before actually creating one. Useful to call
    ahead of create_target_collaboration to avoid conflict failures.

    Returns data.has_conflict (bool) and data.conflict_items (list of
    {creator_open_id, product_id, existing_collaboration_id}).
    """
    body_dict = {
        "creator_open_id_list": creator_open_id_list,
        "product_id_list": product_id_list,
    }
    return call_api("POST", CHECK_TARGET_COLLABORATION_CONFLICTS_PATH, query_params=None, body_dict=body_dict, **retry_kwargs)


def create_conversation_with_creator(creator_open_id: str, only_need_conversation_id: bool = True, **retry_kwargs) -> dict:
    """Opens (or fetches, if one already exists) a conversation with a creator. Returns conversation_id."""
    body_dict = {"creator_open_id": creator_open_id, "only_need_conversation_id": only_need_conversation_id}
    return call_api("POST", CREATE_CONVERSATION_PATH, query_params=None, body_dict=body_dict, **retry_kwargs)


def get_conversation_list(
    page_size: int = 50,
    page_token: str = "",
    conversation_status: str | None = None,
    only_need_conversation_id: bool = True,
    **retry_kwargs,
) -> dict:
    """
    Lists conversations. page_size max is 50.
    conversation_status: "ALL", "UNREAD", or "UNREPLIED" (optional filter).
    """
    query_params = {"page_size": page_size, "only_need_conversation_id": only_need_conversation_id}
    if page_token:
        query_params["page_token"] = page_token
    if conversation_status:
        query_params["conversation_status"] = conversation_status

    return call_api("GET", GET_CONVERSATION_LIST_PATH, query_params, body_dict=None, **retry_kwargs)


def send_im_message(conversation_id: str, msg_type: str, content: dict, **retry_kwargs) -> dict:
    """
    Sends a message in an existing conversation.

    msg_type: "TEXT", "PRODUCT_CARD", "TARGET_COLLABORATION_CARD", "FREE_SAMPLE_CARD", or "IMAGE"
    content: a dict matching the msg_type, e.g.:
        TEXT:                    {"content": "simple text"}
        PRODUCT_CARD:             {"product_id": "12345"}
        TARGET_COLLABORATION_CARD: {"target_collaboration_id": "1234"}
        FREE_SAMPLE_CARD:         {"apply_id": "1234"}
        IMAGE:                    {"url": "...", "width": 1280, "height": 720}
    This function handles JSON-serializing `content` into the string the API expects.
    """
    path = SEND_IM_MESSAGE_PATH_TEMPLATE.format(conversation_id)
    body_dict = {"msg_type": msg_type, "content": json.dumps(content)}
    return call_api("POST", path, query_params=None, body_dict=body_dict, **retry_kwargs)


def run_pass(handles_to_find: list[str], chunk_size: int, df_creators: pd.DataFrame) -> tuple[list[str], set, pd.DataFrame]:
    """
    Runs a single pass over handles_to_find, chunked into groups of chunk_size.
    Each chunk is searched exactly once (linear pass, no re-searching within
    this call).

    IMPORTANT: each chunk's new rows are APPENDED to CONSOLIDATED_CSV, never
    rewritten wholesale. The old approach re-saved the entire accumulated
    df_creators after every chunk — if two processes ever wrote around the
    same time (or a single write got interrupted, e.g. by a sync client on a
    Google-Drive-backed folder), whichever write landed last would replace
    the ENTIRE file with just its own in-memory state, silently wiping out
    everything found before. Appending means a bad write can only affect
    that one chunk's rows, not the whole file's history.

    Returns (handles still not found after this pass, found_usernames, updated df_creators).
    """
    consolidated_path = Path(CONSOLIDATED_CSV)

    # Read-only pass at the start to find creator_open_ids already saved
    # (from this run or any previous one), so appends don't create duplicate
    # rows on disk. This is a read, not a write, so it's safe even if another
    # process is writing to the same file concurrently.
    if consolidated_path.exists():
        already_saved_ids = set(pd.read_csv(consolidated_path, usecols=["creator_open_id"])["creator_open_id"])
    else:
        already_saved_ids = set()

    chunks = [handles_to_find[i:i + chunk_size] for i in range(0, len(handles_to_find), chunk_size)]

    search_key = ""  # empty on first call; carried forward from each response after that
    usernames_found_this_run = set()  # every username matched this run, whether newly appended or already on disk

    for i, chunk in enumerate(chunks, start=1):
        keyword = build_keyword(chunk)
        msg = f"[chunk_size={chunk_size}] {i}/{len(chunks)}: searching {chunk}"
        logger.info(msg)
        print(msg)

        result = search_creators_with_retry(keyword=keyword, search_key=search_key)

        if result.get("code") != 0:
            logger.info(f"  ⚠️  Search failed: {result}")
            if result.get("code") == RATE_LIMIT_CODE:
                cooldown = 60.0
                logger.info(f"  Cooling down an extra {cooldown:.0f}s before the next chunk...")
                time.sleep(cooldown)
        else:
            data = result.get("data", {}) or {}
            search_key = data.get("search_key", search_key)  # carry forward for next call

            creators = data.get("creators", [])
            if creators:
                new_df = pd.DataFrame(creators)
                usernames_found_this_run.update(new_df["username"])

                # Only append rows for creators not already on disk, so
                # re-running or overlapping chunks don't duplicate rows.
                to_append = new_df[~new_df["creator_open_id"].isin(already_saved_ids)]
                if not to_append.empty:
                    file_exists = consolidated_path.exists()
                    to_append.to_csv(consolidated_path, mode="a", header=not file_exists, index=False)
                    already_saved_ids.update(to_append["creator_open_id"])

                # Keep df_creators consistent with CONSOLIDATED_CSV: only add
                # rows that were actually newly written to the file this run.
                # (Handles matching an already-saved creator are still counted
                # as "found" via usernames_found_this_run below — they just
                # don't need to be re-added here since df_creators, as passed
                # in, is expected to already reflect anything previously saved.)
                df_creators = pd.concat([df_creators, to_append], ignore_index=True)
                df_creators = df_creators.drop_duplicates(subset="creator_open_id", keep="first")

        time.sleep(DELAY_BETWEEN_CALLS)

    existing_usernames = set(df_creators["username"]) if not df_creators.empty else set()
    found_usernames = existing_usernames | usernames_found_this_run
    still_not_found = [h for h in handles_to_find if h not in found_usernames]

    return still_not_found, found_usernames, df_creators
