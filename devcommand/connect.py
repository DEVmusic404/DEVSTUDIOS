import argparse
import getpass

import keyring

from .platforms import GRAPH, SERVICE, YT, request
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
        if not any(item["id"] == channel for item in items):
            parser.error("Set youtube_channel to the studio channel ID. Signed-in channels: "
                         + ", ".join(item["id"] for item in items))
        if not credentials.refresh_token:
            parser.error("Google did not return an offline refresh token. Revoke the grant and reconnect.")
        keyring.set_password(SERVICE, "youtube", credentials.to_json())
    else:
        token = getpass.getpass("Paste DEV Studios Page access token: ").strip()
        result = request("GET", f"{GRAPH}/me", token, params={"fields": "id,instagram_business_account"})
        instagram = (result.get("instagram_business_account") or {}).get("id")
        if result["id"] != C.get("facebook_page") or (C.get("instagram_account") and instagram != C["instagram_account"]):
            parser.error(f"Update the configured account IDs. Page: {result['id']}. Instagram: {instagram or 'not linked'}.")
        keyring.set_password(SERVICE, "meta", token)
    print("Account connected.")


if __name__ == "__main__":
    main()
