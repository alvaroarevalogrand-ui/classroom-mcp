"""One-time authorization: opens the browser and writes token.json."""

import os


def main() -> None:
    # Google returns some scopes under aliases (e.g. student-submissions.me.readonly)
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

    from google_auth_oauthlib.flow import InstalledAppFlow

    from classroom_mcp.server import SCOPES, credentials_path, token_path

    creds_path = credentials_path()
    if not creds_path.exists():
        raise SystemExit(
            f"credentials.json not found in {creds_path.parent} "
            "(download it from Google Cloud; set CLASSROOM_MCP_HOME to change the folder)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    token_path().write_text(creds.to_json(), encoding="utf-8")
    print(f"OK: token saved to {token_path()}")


if __name__ == "__main__":
    main()
