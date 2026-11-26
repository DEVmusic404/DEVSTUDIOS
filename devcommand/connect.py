import argparse
import getpass

import keyring

from .platforms import GRAPH, SERVICE, YT, request
from .setup import update_config
from .store import C


def main():
    parser = argparse.ArgumentParser(description="Connect a studio account to Windows Credential Manager.")
    parser.add_argument("platform", choices=["youtube", "meta"])
    parser.add_argument("client_json", nargs="?")
    args = parser.parse_args()
    if args.platform == "youtube":
        if not args.client_json:
            parser.error("Provide the Google Desktop OAuth client JSON path.")
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(args.client_json, [
            "https://www.googleapis.com/auth/youtube.force-ssl",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ])
        credentials = flow.run_local_server(port=0, prompt="consent", access_type="offline")
        items = request("GET", f"{YT}/channels", credentials.token, params={"part": "id,snippet", "mine": "true"})["items"]
        channel = C.get("youtube_channel")
        if not channel:
            if len(items) != 1:
                parser.error("Set youtube_channel to the intended studio channel ID. Signed-in channels: "
                             + ", ".join(item["id"] for item in items))
            item = items[0]
            name = item.get("snippet", {}).get("title", item["id"])
            if input(f"Connect YouTube channel {name} ({item['id']})? [y/N] ").lower().strip() != "y":
                parser.exit(message="YouTube connection canceled.\n")
            channel = item["id"]
        if not any(item["id"] == channel for item in items):
            parser.error("Set youtube_channel to the studio channel ID. Signed-in channels: "
                         + ", ".join(item["id"] for item in items))
        if not credentials.refresh_token:
            parser.error("Google did not return an offline refresh token. Revoke the grant and reconnect.")
        keyring.set_password(SERVICE, "youtube", credentials.to_json())
        update_config(youtube_channel=channel)
    else:
        token = getpass.getpass("Paste DEV Studios Page access token: ").strip()
        result = request("GET", f"{GRAPH}/me", token, params={"fields": "id,name,instagram_business_account{id,username}"})
        instagram = (result.get("instagram_business_account") or {}).get("id")
        if (C.get("facebook_page") and result["id"] != C["facebook_page"]) or (
            C.get("instagram_account") and instagram != C["instagram_account"]
        ):
            parser.error(f"Update the configured account IDs. Page: {result['id']}. Instagram: {instagram or 'not linked'}.")
        if not C.get("facebook_page") or (instagram and not C.get("instagram_account")):
            print(f"Facebook Page: {result.get('name') or result['id']}")
            print("Instagram: " + str((result.get("instagram_business_account") or {}).get("username") or instagram or "unlinked"))
            if input("Connect these accounts? [y/N] ").lower().strip() != "y":
                parser.exit(message="Meta connection canceled.\n")
        keyring.set_password(SERVICE, "meta", token)
        update_config(facebook_page=result["id"], instagram_account=instagram or "")
    print("Account connected.")


if __name__ == "__main__":
    main()
