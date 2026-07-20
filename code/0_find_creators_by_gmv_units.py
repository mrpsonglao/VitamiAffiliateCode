#!/usr/bin/env python
# coding: utf-8

# # Setup
# Setup
import json
import os
import sys
from datetime import datetime, timedelta
import time
from dotenv import load_dotenv, set_key
import random
from pathlib import Path
import logging

# hashing for signing
import hashlib
import hmac

# requests
import requests

# data munging
import pandas as pd
import numpy as np

# helper functions
from tiktok_api_helpers import *


# ## API Setup
new_access_token = False
use_refresh_token = False

if (new_access_token | use_refresh_token):

    # get new acces_token
    token_url = "https://auth.tiktok-shops.com/api/v2/token/get"

    if use_refresh_token:
        auth_code = os.environ.get("TIKTOK_REFRESH_TOKEN")
        
    params = {
        "app_key": app_key,
        "app_secret": app_secret,
        "auth_code": auth_code,
        "grant_type": "authorized_code",
    }
    
    response = requests.get(token_url, params=params, timeout=15)
    data = response.json()['data']
    access_token = data['access_token']
    print(data)

    # save tokens in .env file
    set_key(".env", "TIKTOK_ACCESS_TOKEN", data['access_token'])
    set_key(".env", "TIKTOK_REFRESH_TOKEN", data['refresh_token'])

else:
    access_token = os.environ.get("TIKTOK_ACCESS_TOKEN")
    refresh_token = os.environ.get("TIKTOK_REFRESH_TOKEN")

# # Extract Top Creators by GMV and Units Sold
# logger and log_print are already set up in tiktok_api_helpers.py and
# imported via `from tiktok_api_helpers import *` above — no need to redefine
# them here.

# Count what's already saved from previous runs, so "total so far" reflects
# the full cumulative manifest, not just what's been collected this session.
if RESULTS_CSV.exists():
    previously_saved_count = len(pd.read_csv(RESULTS_CSV, usecols=[0]))
else:
    previously_saved_count = 0

# Resume from a previous run if a checkpoint exists, instead of starting
# over from page 1.
if CHECKPOINT_FILE.exists():
    checkpoint = json.loads(CHECKPOINT_FILE.read_text())
    search_key = checkpoint.get("search_key", "")
    page_token = checkpoint.get("page_token", "")
    page_num = checkpoint.get("page_num", 1)
    gmv_units_log_print(f"Resuming from checkpoint: page {page_num}, page_token={page_token!r}")
else:
    search_key = ""
    page_token = ""
    page_num = 1

proceed = input("Proceed with pulling creator data? (y/n): ").strip().lower()
if proceed != "y":
    raise SystemExit("Stopped by user.")

all_creators = []

while True:
    result = search_creators_with_retry(
        gmv_ranges=["GMV_RANGE_10000_AND_ABOVE"],
        units_sold_ranges=[
            # "UNITS_SOLD_RANGE_100_1000", 
            "UNITS_SOLD_RANGE_1000_AND_ABOVE"
                           ],
        not_invited_l90_days=True,
        search_key=search_key,
        page_token=page_token,
        max_retries=100,
        retry_logger=gmv_units_logger,
    )

    if result.get("code") != 0:
        gmv_units_log_print(f"  ⚠️  Page {page_num} failed after retries, stopping here. page_token={page_token!r}. Result: {result}")
        break

    data = result.get("data", {}) or {}
    creators = data.get("creators", [])
    all_creators.extend(creators)

    # Save this page's results immediately — so if anything crashes on a
    # LATER page, everything collected so far is already safely on disk.
    if creators:
        df_page = pd.DataFrame(creators).reindex(columns=CREATOR_SEARCH_COLUMNS)
        file_exists = RESULTS_CSV.exists()
        df_page.to_csv(RESULTS_CSV, mode="a", header=not file_exists, index=False)

    search_key = data.get("search_key", search_key)  # carry forward, per the doc's caching note
    page_token = data.get("next_page_token", "")

    gmv_units_log_print(f"Page {page_num}: {len(creators)} creator(s) (total so far: {previously_saved_count + len(all_creators)}). page_token={page_token!r}")

    # Save progress AFTER handling this page's data, so the checkpoint
    # always points to the next page still needing to be fetched.
    CHECKPOINT_FILE.write_text(json.dumps({
        "search_key": search_key,
        "page_token": page_token,
        "page_num": page_num + 1,
    }))

    if not page_token:
        break

    page_num += 1
    time.sleep(DELAY_BETWEEN_QUERIES)

gmv_units_log_print(f"\nDone. {len(all_creators)} creator(s) collected this run ({previously_saved_count + len(all_creators)} total in manifest).")

# Pagination finished cleanly (no more pages, or a page failed and we
# stopped) — clear the checkpoint so a future run starts a fresh search
# instead of "resuming" a search that's actually already done.
if not page_token:
    CHECKPOINT_FILE.unlink(missing_ok=True)
    gmv_units_log_print("Checkpoint cleared — search complete.")
else:
    gmv_units_log_print(f"Checkpoint saved (page_token={page_token!r}) — re-run this script to resume from page {page_num + 1}.")
