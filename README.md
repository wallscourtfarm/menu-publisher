# WFA Menu Publisher

A small Streamlit app that takes the term's lunch menu PDF, extracts the data with Claude, and publishes it to the staff-learning-tools repo so all six year-group morning tools update automatically.

## What it does

1. You upload the menu PDF (the standard 3-week rotation from Aspens)
2. Claude reads the PDF and extracts the meal data into structured JSON
3. The app shows you an editable preview so you can fix anything that looks wrong
4. One click publishes `menu.json` to <https://github.com/wallscourtfarm/staff-learning-tools>
5. All six morning tools update within a minute

## Setup

This app is deployed on Streamlit Community Cloud. The app code lives in this repo. Three secrets are configured in the Streamlit Cloud dashboard under Settings → Secrets:

```toml
GITHUB_TOKEN = "github_pat_..."          # Fine-grained PAT, contents:write on staff-learning-tools only
ANTHROPIC_API_KEY = "sk-ant-..."         # Anthropic console API key
APP_PASSWORD = "..."                     # Shared password for the app's login screen
```

## Token rotation

The GitHub PAT expires after 1 year. To rotate:

1. Generate a new fine-grained token at <https://github.com/settings/personal-access-tokens/new> with the same scope (Repository: `staff-learning-tools` only, Contents: read and write)
2. Open the Streamlit Cloud dashboard, go to this app's settings, and replace `GITHUB_TOKEN` in the secrets
3. Reboot the app

## Files

- `app.py` — the Streamlit app
- `requirements.txt` — Python dependencies
- `README.md` — this file

## Local development

```bash
pip install -r requirements.txt
streamlit run app.py
```

You'll need a `.streamlit/secrets.toml` file in this folder with the same three keys as above. **Do not commit secrets.toml** — it's gitignored.
