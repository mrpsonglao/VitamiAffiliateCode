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

# # Step 1. Monitor Sample Application Status
print("\n>>> Extracting latest sample application statuses.")
statuses = [
    "AWAITING_SHIPMENT", "SHIPPED", "CONTENT_PENDING", 
    "OPS_COMPLETED", "COMPLETED"
]
all_sample_applications = []
for status in statuses:
    # print(f"Fetching status: {status}")
    results = search_all_sample_applications(status=status)
    all_sample_applications.extend(results)

print(f"\nTotal combined results: {len(all_sample_applications)}")

df_all_sample_applications = pd.json_normalize(all_sample_applications)
df_all_sample_applications.to_csv(SAMPLE_APPLICATIONS_CSV, index=False)
print(df_all_sample_applications['status'].value_counts())

# # Step 2. Create Conversations
# ## Track conversation IDs of all creators
# load all creators
df_creators_list = pd.read_excel("all_creators_sorted.xlsx", sheet_name="LIST_CREATOR", usecols=[1, 2, 25])
df_creators = pd.read_csv(CONSOLIDATED_CSV)
df_all_conversations = pd.read_csv(ALL_CONVERSATIONS_CSV)
df_target_collab = pd.read_csv(TARGET_COLLAB_CREATORS_CSV)

# ensure IDs are strings
df_target_collab['target_collaboration_id'] = df_target_collab['target_collaboration_id'].astype(str)
df_all_conversations['conversation_id'] = df_all_conversations['conversation_id'].astype(str)

# merge all extracted data
df_creators_messaging = df_creators_list \
    .merge(df_creators[['username', 'creator_open_id']], how='left', on="username") \
    .merge(df_target_collab.rename(columns={'name':'target_collaboration_name'}).drop(['batch_name'], axis=1), how='left', on="creator_open_id") \
    .merge(df_all_sample_applications[['creator.creator_open_id', 'status']].rename(columns={'creator.creator_open_id':'creator_open_id', 'status':'sample_status'}), how='left', on="creator_open_id") \
    .merge(df_all_conversations, how='left', on="username")

# ## Track Message Status based on manifest
df_collab_invite = pd.read_csv(COLLAB_INVITE_MANIFEST_CSV)
df_viber_invite = pd.read_csv(VIBER_INVITE_MANIFEST_CSV)
df_creators_messaging['invited_to_join_collab'] =  df_creators_messaging['conversation_id'].isin(df_collab_invite['conversation_id'].astype(str))
df_creators_messaging['invited_to_viber_grp'] =  df_creators_messaging['conversation_id'].isin(df_viber_invite['conversation_id'].astype(str))

print("\n>>> Reviewing new conversations and messages to send.")
list_create_conversation = df_creators_messaging.loc[
    (df_creators_messaging['target_collaboration_id'].notnull() | df_creators_messaging['sample_status'].notnull())
    & df_creators_messaging['conversation_id'].isnull(),
    'creator_open_id'
].tolist()
print(f"- To create: {len(list_create_conversation)} new conversations.")
count_viber_invite = df_creators_messaging.loc[df_creators_messaging['sample_status'].notnull() & ~df_creators_messaging['invited_to_viber_grp']].shape[0]
print(f"- To send: Viber invites for {count_viber_invite} new creators.")
count_collab_invite = df_creators_messaging.loc[~df_creators_messaging['invited_to_join_collab'] & df_creators_messaging['target_collaboration_id'].notnull()].shape[0]
print(f"- To send: collab invites for {count_collab_invite} new creators.")

proceed = input("Proceed with sending messages to creators? (y/n): ").strip().lower()
if proceed != "y":
    raise SystemExit("Stopped by user.")

# ## [Optional] Create Conversation if no conversation_id but has target_collaboration_id or has sample application
if list_create_conversation:
    print(f"\n>>> Generating conversation IDs for {len(list_create_conversation)} new creators.")
    conversation_rows = []
    failed_conversations = create_conversations_with_retry(list_create_conversation, conversation_rows, max_passes=3, delay=0.5)
    
    print(f"\nFinal: {len(conversation_rows)} succeeded, {len(failed_conversations)} still failed after all retries.")
    
    if conversation_rows:
        df_conversations_new = pd.DataFrame(conversation_rows)
        file_exists = Path(ALL_CONVERSATIONS_NEW_CSV).exists()
        df_conversations_new.to_csv(ALL_CONVERSATIONS_NEW_CSV, mode="a", header=not file_exists, index=False)
    
        print(f"\nAppended {len(df_conversations_new)} row(s) to {TARGET_COLLAB_CREATORS_CSV}")
else:
    print("No conversations to generate.")

# merge new conversations with all conversations and save to file
df_conversations_new = pd.read_csv(ALL_CONVERSATIONS_NEW_CSV)
df_all_conversations = pd.concat([df_all_conversations, df_conversations_new.merge(df_creators[['username', 'creator_open_id']], how='left', on="creator_open_id")[['creator_im_id', 'conversation_id', 'username']]]).drop_duplicates(subset=['username']).reset_index(drop=True)
df_all_conversations.to_csv(ALL_CONVERSATIONS_CSV, index=False)

# change all conversation_id to str
df_all_conversations['conversation_id'] = df_all_conversations['conversation_id'].astype(str)

# # Step 3. Bulk Send Message
# merge all extracted data
df_creators_messaging = df_creators_list \
    .merge(df_creators[['username', 'creator_open_id']], how='left', on="username") \
    .merge(df_target_collab.rename(columns={'name':'target_collaboration_name'}).drop(['batch_name'], axis=1), how='left', on="creator_open_id") \
    .merge(df_all_sample_applications[['creator.creator_open_id', 'status']].rename(columns={'creator.creator_open_id':'creator_open_id', 'status':'sample_status'}), how='left', on="creator_open_id") \
    .merge(df_all_conversations, how='left', on="username")

dict_conv_to_username = df_creators_messaging.dropna(subset=['conversation_id']).set_index('conversation_id')['username'].to_dict()
dict_conv_to_collab =  df_creators_messaging.dropna(subset=['conversation_id']).set_index('conversation_id')['target_collaboration_id'].to_dict()

# ## Track Message Status based on manifest
df_collab_invite = pd.read_csv(COLLAB_INVITE_MANIFEST_CSV)
df_viber_invite = pd.read_csv(VIBER_INVITE_MANIFEST_CSV)
df_creators_messaging['invited_to_join_collab'] =  df_creators_messaging['conversation_id'].isin(df_collab_invite['conversation_id'].astype(str))
df_creators_messaging['invited_to_viber_grp'] =  df_creators_messaging['conversation_id'].isin(df_viber_invite['conversation_id'].astype(str))

list_viber_invite = df_creators_messaging.loc[df_creators_messaging['sample_status'].notnull() & ~df_creators_messaging['invited_to_viber_grp'], 'conversation_id'].astype(str).tolist()
list_collab_invite = df_creators_messaging.loc[~df_creators_messaging['invited_to_join_collab'] & df_creators_messaging['target_collaboration_id'].notnull(), 'conversation_id'].astype(str).tolist()

# ## Send Viber Group invite
message_thank_you = "Hi {}! Thank you so much for accepting our invite, super excited to work with you! Sending over a photo with QR codes para sa content brief and Viber community namin. Just scan para ma access mo!\n\n Feel free to message me here anytime kung may mga tanong ka. Looking forward to creating with you!"
message_viber_qrcode = {
    'url': 'https://p16-oec-sg.ibyteimg.com/tos-alisg-i-aphluv4xwc-sg/de85540122b94ac9bf6c801faf19d01c~tplv-aphluv4xwc-origin-image.image?dr=15570&t=555f072d&ps=933b5bde&shp=5566cfe3&shcp=3c3d9ffb&idc=my&from=1432801251',
    'width': 1280,
    'height': 905
}

# Skip conversations already FULLY sent (both TEXT and IMAGE succeeded) in a previous run — a partial failure (e.g. text sent but image failed) should
# still be retried, not silently treated as done.
if Path(VIBER_INVITE_MANIFEST_CSV).exists():
    prior = pd.read_csv(VIBER_INVITE_MANIFEST_CSV, dtype={"conversation_id": str})
    already_sent = set(prior.loc[prior["text_sent"] & prior["image_sent"], "conversation_id"])
else:
    already_sent = set()

to_send = [c for c in list_viber_invite if c not in already_sent]
skipped = len(list_viber_invite) - len(to_send)
if skipped:
    print(f"Skipping {skipped} conversation(s) already sent in a previous run.")

if list_viber_invite:
    print(f"\n>>> Sending Viber invites for {len(list_viber_invite)} new creators.")

    results = []
    
    for i, conversation_id in enumerate(to_send, start=1):
        print(f"{i}/{len(to_send)}: messaging {conversation_id}...")
    
        username = dict_conv_to_username[conversation_id]
        text_result = send_im_message(conversation_id, "TEXT", {"content": message_thank_you.format(username)})
        text_ok = text_result.get("code") == 0
        if not text_ok:
            print(f"  ⚠️  TEXT failed: {text_result}")
        time.sleep(DELAY_BETWEEN_PAGES)
    
        image_result = send_im_message(conversation_id, "IMAGE", message_viber_qrcode)
        image_ok = image_result.get("code") == 0
        if not image_ok:
            print(f"  ⚠️  IMAGE failed: {image_result}")
        time.sleep(DELAY_BETWEEN_PAGES)
    
        if text_ok and image_ok:
            print("  ✅ Both messages sent")
    
        results.append({
            "conversation_id": conversation_id,
            "text_sent": text_ok,
            "image_sent": image_ok,
        })
    
        # Save progress after every creator, not just at the end — so a crash
        # partway through doesn't lose track of who's already been messaged.
        file_exists = Path(VIBER_INVITE_MANIFEST_CSV).exists()
        pd.DataFrame([results[-1]]).to_csv(VIBER_INVITE_MANIFEST_CSV, mode="a", header=not file_exists, index=False)
    
    fully_sent = sum(1 for r in results if r["text_sent"] and r["image_sent"])
    print(f"\nDone. {fully_sent}/{len(results)} creator(s) got both messages successfully this run.")
    
else:
    print("No Viber invites to send.")

# ## Send Target Collab Invite
message_invite = "Hi {}! 😊\n\nI'm Coleen, founder of Vitami. Familiar ka ba sa PMS? Yung cramps, intense mood swings, bloating, at acne na kasabay ng period? Whether ikaw mismo nakakaramdam nun or may kakilala kang nageexperience nito, we all know how rough that week can get. Kaya gumawa ako ng product specifically for that kasi hindi dapat normal ang magtiis every month. My goal is simple: make period week lighter for every woman.\n\nWould you be open to creating content with us and helping other girls out? You'll get 20% commission per sale plus a free sample to try!\n\n💚 Coleen"

# Skip conversations already FULLY sent (both TEXT and TARGET_COLLAB_CARD succeeded) in a previous run — a partial failure (e.g. text sent but image failed) should
# still be retried, not silently treated as done.
if Path(COLLAB_INVITE_MANIFEST_CSV).exists():
    prior = pd.read_csv(COLLAB_INVITE_MANIFEST_CSV, dtype={"conversation_id": str})
    already_sent = set(prior.loc[prior["text_sent"] & prior["collab_sent"], "conversation_id"])
else:
    already_sent = set()

to_send = [c for c in list_collab_invite if c not in already_sent]
skipped = len(list_collab_invite) - len(to_send)
if skipped:
    print(f"Skipping {skipped} conversation(s) already sent in a previous run.")

if list_collab_invite:
    print(f"\n>>> Sending collab invites for {len(list_collab_invite)} new creators.")

    results = []
    
    for i, conversation_id in enumerate(to_send, start=1):
        print(f"{i}/{len(to_send)}: messaging {conversation_id}...")
    
        username = dict_conv_to_username[conversation_id]
        target_collab_id = dict_conv_to_collab[conversation_id]
        
        text_result = send_im_message(conversation_id, "TEXT", {"content": message_invite.format(username)})
        text_ok = text_result.get("code") == 0
        if not text_ok:
            print(f"  ⚠️  TEXT failed: {text_result}")
        time.sleep(DELAY_BETWEEN_PAGES)
    
        collab_result = send_im_message(conversation_id, "TARGET_COLLABORATION_CARD", {"target_collaboration_id": target_collab_id})
        collab_ok = collab_result.get("code") == 0
        if not collab_ok:
            print(f"  ⚠️  TARGET_COLLAB failed: {collab_result}")
        time.sleep(DELAY_BETWEEN_PAGES)
    
        if text_ok and collab_ok:
            print("  ✅ Both messages sent")
    
        results.append({
            "conversation_id": conversation_id,
            "text_sent": text_ok,
            "collab_sent": collab_ok,
        })
    
        # Save progress after every creator, not just at the end — so a crash
        # partway through doesn't lose track of who's already been messaged.
        file_exists = Path(COLLAB_INVITE_MANIFEST_CSV).exists()
        pd.DataFrame([results[-1]]).to_csv(COLLAB_INVITE_MANIFEST_CSV, mode="a", header=not file_exists, index=False)
    
    fully_sent = sum(1 for r in results if r["text_sent"] and r["collab_sent"])
    print(f"\nDone. {fully_sent}/{len(results)} creator(s) got both messages successfully this run.")
    
else:
    print("No collab invites to send.")