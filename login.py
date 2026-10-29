"""One-time Garmin login. Saves the session token to ./.garmintokens

Run this locally, from your own machine: Garmin sees a familiar IP and will not
trigger repeated email verification. The password is never stored.

garminconnect handles authentication itself now - garth is not involved, and
old garth-format tokens are no longer accepted.
"""
import os
import sys
from getpass import getpass

from garminconnect import Garmin

TOKEN_DIR = os.environ.get("GARMIN_TOKEN_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".garmintokens"
)


def main() -> int:
    email = input("Garmin email: ").strip()
    password = getpass("Garmin password: ")

    client = Garmin(email, password, return_on_mfa=True)

    try:
        status, state = client.login()
        if status == "needs_mfa":
            code = input("MFA code (authenticator app or SMS): ").strip()
            client.resume_login(state, code)
    except Exception as exc:
        text = str(exc)
        print(f"\nLogin failed: {text}")
        if "429" in text or "Too Many Requests" in text:
            print(
                "Garmin is rate limiting logins for this account or IP.\n"
                "Wait 30-60 minutes and try ONCE more - repeated attempts extend\n"
                "the block. If it persists, try from a different network."
            )
        elif "401" in text or "403" in text:
            print("Credentials rejected. Check the email and password.")
        return 1

    os.makedirs(TOKEN_DIR, exist_ok=True)
    client.client.dump(TOKEN_DIR)
    print(f"\nDone. Token saved to {TOKEN_DIR}")
    print("Start the server with: uv run garmin_mcp.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
