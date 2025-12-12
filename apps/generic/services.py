# generic/services.py
import os
import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Securely fetch secrets from the environment
discord_bot_token = os.getenv("discord_bot_token")
indexer_channel_id = os.getenv("INDEXER_DISCORD_CHANNEL_ID")

# Claude Code Engineer webhook
engineer_webhook = os.getenv("ENGINEER_WEBHOOK", "http://10.241.68.122:8765")
engineer_secret = os.getenv("ENGINEER_SECRET", "eng_7Kx9mPqR3vNcYhW2jL5sT8bF4aD6eG1i")

def send_discord_message(msg, channel_id):
    """Generic function to send a message to any Discord channel."""
    if not discord_bot_token:
        print("ERROR: discord_bot_token is not configured. Cannot send message.")
        return

    url = f"https://discordapp.com/api/channels/{channel_id}/messages"
    headers = { "Authorization": "Bot " + discord_bot_token }
    body = { "content": msg }

    try:
        response = requests.post(url, headers=headers, data=body)
        response.raise_for_status() # Raises an exception for bad status codes (4xx or 5xx)
        print(f"Successfully sent Discord message to channel {channel_id}.")
        return {"data": f"Status code {response.status_code}"}
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Failed to send Discord message: {e}")
        return {"error": str(e)}

def send_engineer_alert(msg: str, project: str = "etherlink-indexer"):
    """Send alert to Claude Code Engineer for automated investigation."""
    if not engineer_webhook:
        print("WARNING: ENGINEER_WEBHOOK not configured. Skipping Engineer alert.")
        return

    try:
        headers = {
            "Content-Type": "application/json",
            "X-Engineer-Secret": engineer_secret
        }
        payload = {
            "alert": msg,
            "project": project
        }
        response = requests.post(engineer_webhook, json=payload, headers=headers, timeout=5)
        if response.status_code == 200:
            print(f"Successfully sent alert to Engineer: {response.json().get('status', 'ok')}")
        else:
            print(f"Engineer webhook returned {response.status_code}: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"WARNING: Failed to send Engineer alert (non-fatal): {e}")

def send_indexer_alert(msg: str, network: str = "N/A", app: str = "N/A"):
    """
    Sends a pre-formatted, critical alert to the dedicated indexer channel.
    Also triggers Claude Code Engineer for automated investigation.
    """
    if not indexer_channel_id:
        print("WARNING: INDEXER_DISCORD_CHANNEL_ID not set. Cannot send alert.")
        return

    # Build the string line-by-line, now including network and app context.
    lines = [
        "🚨 **CRITICAL INDEXER ERROR** 🚨",
        "---",
        f"**Network:** `{network.upper()}`",
        f"**App:** `{app.upper()}`",
        "---",
        "**Disruption Detected:**",
        "```",
        msg,
        "```",
        "---",
        "Please investigate the indexer service immediately."
    ]
    formatted_msg = "\n".join(lines)

    # Send to Discord
    result = send_discord_message(formatted_msg, indexer_channel_id)

    # Also send to Engineer for automated investigation
    engineer_msg = f"[{network.upper()}] [{app.upper()}] {msg}"
    send_engineer_alert(engineer_msg, project="etherlink-indexer")

    return result
