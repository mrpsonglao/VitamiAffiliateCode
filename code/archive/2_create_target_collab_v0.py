#!/usr/bin/env python
# coding: utf-8

# # Setup
# Setup
import json
import os
import sys
from datetime import datetime, timedelta, timezone
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


# # Step 1: Load Creator Open IDs
# load all creators
df_creators_list = pd.read_excel(SORTED_EXCEL_FILE, sheet_name="LIST_CREATOR", usecols=[1, 2, 25])
list_all_creators = df_creators_list['username'].tolist()

# Load or initialize the manifest (tracks which handles are already found)
manifest = pd.read_csv(MANIFEST_CSV)
df_creators = pd.read_csv(CONSOLIDATED_CSV)

# recheck manifest in case of failed runs
manifest["found"] = manifest["handle"].isin(set(df_creators['username']))
manifest.to_csv(MANIFEST_CSV, index=False)
print(f"{manifest['found'].sum()} creator_open_id found")

# # Step 2: Extract List of existing target collaborations to avoid invitation conflicts

# load IDs to check
creator_open_id_list = df_creators.loc[df_creators['username'].isin(set(manifest['handle'])), 'creator_open_id'].tolist()
product_id_list = ['1734810555690551128']

# Load known conflicts from previous runs
conflicts_manifest_df = pd.read_csv(CONFLICTS_MANIFEST_CSV, dtype=str)
known_conflict_ids = set(conflicts_manifest_df["creator_open_id"])

# Skip creators already known to conflict
creators_to_check = [c for c in creator_open_id_list if c not in known_conflict_ids]
skipped_count = len(creator_open_id_list) - len(creators_to_check)
if skipped_count:
    print(f"Skipping {skipped_count} creator(s) already known to conflict. Checking {len(creators_to_check)} potentially new creators.")

# find all open IDs with conflicts
all_conflict_items = []

for i in range(0, len(creators_to_check), 50):
    batch = creators_to_check[i:i + 50]
    result = check_target_collaboration_conflicts(
        creator_open_id_list=batch,
        product_id_list=product_id_list,
    )

    if result.get("code") == 0:
        all_conflict_items.extend(result["data"]["conflict_items"])
    else:
        print(f"  ⚠️  Batch failed: {result}")
        print(batch)
        
df_all_collab_conflicts = pd.DataFrame(all_conflict_items)

# Merge any newly found conflicts into the manifest
if not df_all_collab_conflicts.empty:
    df_all_collab_conflicts = pd.concat([conflicts_manifest_df, df_all_collab_conflicts], ignore_index=True)
    df_all_collab_conflicts = df_all_collab_conflicts.drop_duplicates(subset=["creator_open_id"], keep="first")
    df_all_collab_conflicts.to_csv(CONFLICTS_MANIFEST_CSV, index=False)
    new_count = len(df_all_collab_conflicts) - len(conflicts_manifest_df)
    print(f"Manifest now has {len(df_all_collab_conflicts)} known conflict(s) ({new_count} new this run).")
else:
    df_all_collab_conflicts = conflicts_manifest_df

# ## Review current status
# merge all extracted data
df_creators_list_id_conflict = df_creators_list \
    .merge(df_creators[['username', 'creator_open_id']], how='left', on="username") \
    .merge(df_all_collab_conflicts, how='left', on="creator_open_id")

df_creator_summary = df_creators_list_id_conflict.groupby('batch_name').agg(
    all_creators=('creator_open_id', 'size'),
    creators_with_id=('creator_open_id', 'count'),
    invited=('existing_collaboration_id', 'count'),
)

df_creator_summary['to_invite'] = df_creator_summary['creators_with_id'] - df_creator_summary['invited']
print(df_creator_summary)

# ## Prepare shortlisted file without conflicts for batch processing
# set aside all new creators with open IDs but no conflicts
df_creators_list_id_new = df_creators_list_id_conflict[df_creators_list_id_conflict['creator_open_id'].notnull() & df_creators_list_id_conflict['existing_collaboration_id'].isnull()].reset_index(drop=True).copy()

# check counts
num_creators_with_conflict = df_creators_list_id_conflict['existing_collaboration_id'].notnull().sum()
num_creators_with_id = df_creators_list_id_conflict['creator_open_id'].notnull().sum()
print(f"{num_creators_with_conflict} out of {num_creators_with_id} with conflicts. {df_creators_list_id_new.shape[0]} new creator_open_id remaining for new target collaborations")

# # Step 3: Create new Target Collaboration in batches of 50 new creators
proceed = input("Create new target collaborations? (y/n): ").strip().lower()
if proceed != "y":
    raise SystemExit("Stopped by user.")

# Set Default Values
message = "Hi {{user_name}}! \n\nWe'd love to have you as a Vitami affiliate. We make PMS Relief Gummies to help women get through their period with less discomfort. You'll get 20% commission plus a free sample for 1 TikTok video. Kindly accept this invite and request your sample if you're interested so we can ship it right away. Hoping to spread the word on better period care together! \u200d\u200d"
end_time = "2101132799"
products = [{
    "id": '1734810555690551128',
    "target_commission_rate":2000, 
    "shop_ads_commission_rate": 300
}]
seller_contact_info = {
    "email": 'admin@vitamigummies.com'
}
free_sample_rule = {
    'has_free_sample': True,
    'is_sample_approval_exempt': True
}

# Load prior runs' manifest so chunk numbering continues from where each batch_name last left off, instead of restarting at 01 every run.
manifest_df = pd.read_csv(TARGET_COLLAB_MANIFEST_CSV)

# ## Batch process Target Collab
# create collab only if there are at N=optimize_cutoff in the group
optimize_collab_count = True
optimize_cutoff = 50

# Batch-create target collaborations: group by batch_name, then chunk each group's creators into groups of 50 (the API's max per invitation)
results = []
creator_rows = []  # long-format rows: one per (target_collaboration_id, creator_open_id)

for batch_name, group in df_creators_list_id_new.groupby('batch_name'):
    creator_ids = group['creator_open_id'].tolist()
    chunks = [creator_ids[i:i + 50] for i in range(0, len(creator_ids), 50)]
    # Find the highest chunk_count already used for this batch_name in the manifest (across any previous run), and continue numbering from there.
    existing_counts = manifest_df.loc[manifest_df["batch_name"] == batch_name, "chunk_count"]
    start_count = int(existing_counts.max()) + 1 if not existing_counts.empty else 1

    for offset, chunk in enumerate(chunks):
        if optimize_collab_count:
            if len(chunk) < optimize_cutoff:
                continue

        chunk_count = start_count + offset
        name = f"{batch_name}_{chunk_count:02d}"
        print(f"Creating '{name}' with {len(chunk)} creators...")

        result = create_target_collaboration(
            name=name,
            end_time=end_time,
            products=products,
            creator_user_open_ids=chunk,
            seller_contact_info=seller_contact_info,
            free_sample_rule=free_sample_rule,
            message=message,
        )
        # print(result)

        created_at = datetime.now(timezone.utc).isoformat()

        if result.get("code") == 0:
            try:
                collab_id = result["data"]["target_collaboration"]["id"]
            except (KeyError, TypeError) as e:
                print(f"  ⚠️  Unexpected response shape ({e}). Full result:")
                print(json.dumps(result, indent=2))
                collab_id = None
            else:
                print(f"  ✅ Created: {collab_id}")

            # One row per creator invited to this collaboration, only recorded
            # on success (a failed collaboration never actually invited anyone).
            if collab_id is not None:
                for creator_open_id in chunk:
                    creator_rows.append({
                        "target_collaboration_id": collab_id,
                        "name": name,
                        "batch_name": batch_name,
                        "creator_open_id": creator_open_id,
                        "end_time": end_time,
                    })
        else:
            collab_id = None
            print(f"  ⚠️  Failed: {result}")

        results.append({
            "name": name,
            "batch_name": batch_name,
            "chunk_count": chunk_count,
            "num_creators": len(chunk),
            "target_collaboration_id": str(collab_id),
            "code": result.get("code"),
            "message": result.get("message"),
            "created_at": created_at,
            "end_time": end_time,
        })

# Append a summary row per collaboration to the CSV manifest.
if results:
    df_results = pd.DataFrame(results)
    manifest_exists = Path(TARGET_COLLAB_MANIFEST_CSV).exists()
    df_results.to_csv(TARGET_COLLAB_MANIFEST_CSV, mode="a", header=not manifest_exists, index=False)
    print(f"\nAppended {len(df_results)} row(s) to: {TARGET_COLLAB_MANIFEST_CSV}")
else:
    print("\nNo creators to process this run.")

# Append the long-format target_collaboration_id -> creator_open_id rows.
# This file is append-only: every run just adds more rows, never rewrites
# or deduplicates existing ones (each row records a real invitation event).
if creator_rows:
    df_creator_rows = pd.DataFrame(creator_rows)
    creators_csv_exists = Path(TARGET_COLLAB_CREATORS_CSV).exists()
    df_creator_rows.to_csv(TARGET_COLLAB_CREATORS_CSV, mode="a", header=not creators_csv_exists, index=False)
    print(f"Appended {len(df_creator_rows)} row(s) to: {TARGET_COLLAB_CREATORS_CSV}")

manifest_df = pd.read_csv(TARGET_COLLAB_MANIFEST_CSV)
print(f"{manifest_df['num_creators'].sum()} creators invited to target collaborations so far.")