import requests

dorg_homebase_channel_id = "804689936388325376"


def send_discord_message(msg, channel_id):
    global discord_bot_token
    url = "https://discordapp.com/api/channels/"+channel_id+"/messages"
    headers = {
        "Authorization": "Bot " + discord_bot_token
    }
    body = {
        "content": msg
    }
    print("before sending "+msg)
    response = requests.request("POST", url, headers=headers, data=body)
    return {"data": str("Status code "+str(response.status_code))}
