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

# # Extract Creator Open IDs
# Load or initialize the manifest (tracks which handles are already found)
manifest = pd.read_csv(MANIFEST_CSV)
df_creators = pd.read_csv(CONSOLIDATED_CSV)

# recheck manifest in case of failed runs
manifest["found"] = manifest["handle"].isin(set(df_creators['username']))
manifest.to_csv(MANIFEST_CSV, index=False)

# check handles to find
handles_to_find = manifest.loc[~manifest["found"], "handle"].tolist()
print(f">>> {manifest['found'].sum()} handles found out of {len(manifest)}. {len(handles_to_find)} handles left to find.\n")

# ## Shortlist search to top ranks first
# load all creators
df_creators_list = pd.read_excel(SORTED_EXCEL_FILE, sheet_name="LIST_CREATOR", usecols=[1, 2, 25])
df_creators_list = df_creators_list.merge(manifest, how='left', left_on='username', right_on='handle')

target_batches = [
    'Health_202606_00k-02k', 'All_202606_00k-02k',
       'Health_202606_02k-04k', 'All_202606_02k-04k',
       'Health_202606_04k-06k', 'All_202606_04k-06k',
       'Health_202606_06k-08k', 'All_202606_06k-08k',
       'Health_202606_08k-10k', 'All_202606_08k-10k',
       'Health_202606_10k-12k', 'All_202606_10k-12k'
       ]

still_not_found = df_creators_list.loc[(df_creators_list['batch_name'].isin(target_batches) & ~df_creators_list['found']), 'username'].tolist()
df_tofind = df_creators_list[df_creators_list['batch_name'].isin(target_batches)].groupby('batch_name').agg(
    all_creators=('handle', 'size'),
    creators_with_id=('found', 'sum')
)
df_tofind['creators_to_find']  = df_tofind['all_creators'] - df_tofind['creators_with_id']

print(f"\nFocusing search on batches:  {target_batches}")
print(f"{len(still_not_found)} handles to find for these batches.\n")
print(f"Sample IDs: {still_not_found[:10]}")
print(df_tofind)

proceed = input("Proceed with finding creator open IDs? (y/n): ").strip().lower()
if proceed != "y":
    raise SystemExit("Stopped by user.")

chunk_size_phase_list = [10, 5, 1]
for phase_num, chunk_size in enumerate(chunk_size_phase_list, start=1):
    print(f"\n\n\n>>> Starting Phase {phase_num} (chunksize = {chunk_size})")

    still_not_found, found_usernames, df_creators = run_pass(still_not_found, chunk_size=chunk_size, df_creators=df_creators)

    found_usernames = set(df_creators["username"])
    manifest["found"] = manifest["handle"].isin(found_usernames)
    manifest.to_csv(MANIFEST_CSV, index=False)

    print(f"\nPhase {phase_num} (chunksize = {chunk_size}) done. {int(manifest['found'].sum())} out of {len(manifest)} handles found. {len(still_not_found)} handles still not found.\n")