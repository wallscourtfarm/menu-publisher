"""
WFA Menu Publisher
Upload a menu PDF, extract data via Claude, edit if needed, publish to GitHub.

Author: built for Innes McLean, Wallscourt Farm Academy
"""

import streamlit as st
import anthropic
import base64
import json
import re
from datetime import date, timedelta, datetime
import requests
import pandas as pd

from wfa_shared.api import get_anthropic_client, DEFAULT_MODEL
from wfa_shared.streamlit_css import inject_wfa_css

# ────────────────────────────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────────────────────────────
GITHUB_OWNER = "wallscourtfarm"
GITHUB_REPO = "staff-learning-tools"
GITHUB_PATH = "menu.json"
GITHUB_BRANCH = "main"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]

st.set_page_config(
    page_title="WFA Menu Publisher",
    page_icon="🍽️",
    layout="wide",
)

inject_wfa_css(buttons=True, download=True)

# ────────────────────────────────────────────────────────────────────────
# AUTH
# ────────────────────────────────────────────────────────────────────────
def check_password():
    """Simple shared-password gate."""
    if st.session_state.get("authenticated"):
        return True
    st.title("🔒 WFA Menu Publisher")
    pw = st.text_input("Password", type="password")
    if st.button("Sign in"):
        if pw == st.secrets.get("APP_PASSWORD"):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    return False

if not check_password():
    st.stop()

# ────────────────────────────────────────────────────────────────────────
# HELPERS
# ────────────────────────────────────────────────────────────────────────
def extract_menu_from_pdf(pdf_bytes: bytes) -> dict:
    """Send PDF to Claude and ask for structured JSON of the menu."""
    client = get_anthropic_client()
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    prompt = """You are extracting a UK primary school lunch menu from a PDF.

The menu has 3 weeks (Week 1, Week 2, Week 3) that rotate. Each page lists the dates the week applies to (typically 8-10 Monday dates per page in DD/MM/YY format) and the meal options for that week.

Each day has TWO main hot options:
- A "red column" option (typically meat-based, in the upper row of the meal table)
- A "green column" option (vegetarian, marked with B or labelled as Meat-Free / Veggie / Magic, in the lower row)

Days run Monday to Friday. Ignore the rotating "Filled Jackets" and "Pasta Twirler" sections — those are fixed and not part of what we extract.

Keep meal names SHORT (under 40 chars where possible). Drop "HALAL/NON HALAL" labels. Drop "Skin on" and shorten "Roasties" naming. Convert "Wholegrain" to "Wholegrain" (keep the word). Use "and" not "&". Examples of good shortened names:
- "Roast Chicken, Stuffing, Roasties"
- "Cottage Pie"
- "Fish Fingers and Chips"
- "Cauliflower & Broccoli Cheese Bake"
- "Mixed Bean Fajitas with Wedges"

Return ONLY valid JSON in this exact structure (no markdown fences, no commentary):

{
  "term_label": "Spring/Summer 2026",
  "weeks": [
    {
      "week_number": 1,
      "monday_dates": ["2026-04-13", "2026-05-04"],
      "days": {
        "Mon": ["Red column option", "Green column option"],
        "Tue": ["...", "..."],
        "Wed": ["...", "..."],
        "Thu": ["...", "..."],
        "Fri": ["...", "..."]
      }
    },
    { "week_number": 2, ... },
    { "week_number": 3, ... }
  ]
}

Convert all dates to YYYY-MM-DD format. If the PDF shows "13/04/26" assume 2026.
Verify all monday_dates fall on a Monday — if a date doesn't, flag it but include it anyway."""

    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=4000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )

    text = response.content[0].text.strip()
    # Strip markdown fences if model wrapped the JSON despite instructions
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    return json.loads(text)


def expand_to_weekly_menu(extracted: dict) -> dict:
    """Convert the 3-weeks-with-dates structure into the flat menu.json format."""
    weeks_out = {}
    for week in extracted["weeks"]:
        for monday in week["monday_dates"]:
            weeks_out[monday] = week["days"]
    sorted_weeks = {k: weeks_out[k] for k in sorted(weeks_out.keys())}
    return {
        "_comment": "WFA shared lunch menu. Each key is a Monday in YYYY-MM-DD format. Each day holds [red_option, green_option].",
        "_lastUpdated": date.today().isoformat(),
        "_termLabel": extracted.get("term_label", ""),
        "weeks": sorted_weeks,
    }


def publish_to_github(menu_dict: dict, commit_message: str):
    """Commit menu.json to the staff-learning-tools repo via GitHub API."""
    token = st.secrets["GITHUB_TOKEN"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Get current file SHA (required for updates)
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_PATH}"
    r = requests.get(url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=10)
    if r.status_code == 200:
        sha = r.json()["sha"]
    elif r.status_code == 404:
        sha = None  # File doesn't exist yet, this will be a create
    else:
        raise RuntimeError(f"GitHub GET failed: {r.status_code} {r.text}")

    # Encode the new content
    content_str = json.dumps(menu_dict, indent=2)
    content_b64 = base64.standard_b64encode(content_str.encode("utf-8")).decode("utf-8")

    body = {
        "message": commit_message,
        "content": content_b64,
        "branch": GITHUB_BRANCH,
    }
    if sha:
        body["sha"] = sha

    r = requests.put(url, headers=headers, json=body, timeout=10)
    if r.status_code in (200, 201):
        return r.json()["commit"]["html_url"]
    else:
        raise RuntimeError(f"GitHub PUT failed: {r.status_code} {r.text}")


def fetch_current_menu():
    """Fetch the live menu.json from GitHub Pages."""
    try:
        r = requests.get(
            f"https://{GITHUB_OWNER}.github.io/{GITHUB_REPO}/menu.json",
            timeout=5,
            params={"t": datetime.now().isoformat()},
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def menu_to_dataframe(menu_dict: dict) -> pd.DataFrame:
    """Flatten the menu into a DataFrame for st.data_editor."""
    rows = []
    for monday, days in menu_dict.get("weeks", {}).items():
        for day in DAYS:
            opts = days.get(day, ["", ""])
            rows.append({
                "Monday": monday,
                "Day": day,
                "Red column": opts[0] if len(opts) > 0 else "",
                "Green column": opts[1] if len(opts) > 1 else "",
            })
    return pd.DataFrame(rows)


def dataframe_to_menu(df: pd.DataFrame, term_label: str) -> dict:
    """Convert the edited DataFrame back into menu.json structure."""
    weeks = {}
    for _, row in df.iterrows():
        monday = row["Monday"]
        if not monday:
            continue
        if monday not in weeks:
            weeks[monday] = {}
        weeks[monday][row["Day"]] = [row["Red column"] or "", row["Green column"] or ""]

    sorted_weeks = {k: weeks[k] for k in sorted(weeks.keys())}
    return {
        "_comment": "WFA shared lunch menu. Each key is a Monday in YYYY-MM-DD format. Each day holds [red_option, green_option].",
        "_lastUpdated": date.today().isoformat(),
        "_termLabel": term_label,
        "weeks": sorted_weeks,
    }


def validate_mondays(df: pd.DataFrame) -> list:
    """Return list of dates that aren't actually Mondays."""
    bad = []
    for monday in df["Monday"].unique():
        try:
            d = datetime.strptime(monday, "%Y-%m-%d").date()
            if d.weekday() != 0:
                bad.append(monday)
        except ValueError:
            bad.append(monday)
    return bad


# ────────────────────────────────────────────────────────────────────────
# UI
# ────────────────────────────────────────────────────────────────────────
st.title("🍽️ WFA Menu Publisher")
st.caption("Upload a new menu PDF, review, publish. All 6 morning tools update within a minute.")

# Show what's currently live
with st.expander("📍 What's currently published", expanded=False):
    current = fetch_current_menu()
    if current:
        wk_count = len(current.get("weeks", {}))
        st.markdown(
            f"**Term label:** {current.get('_termLabel', '(none)')}  \n"
            f"**Last updated:** {current.get('_lastUpdated', '(unknown)')}  \n"
            f"**Weeks covered:** {wk_count}"
        )
        if wk_count > 0:
            keys = sorted(current["weeks"].keys())
            st.caption(f"First Monday: **{keys[0]}** · Last Monday: **{keys[-1]}**")
    else:
        st.info("No menu published yet, or unreachable.")

st.divider()

# ── Step 1: Upload ──────────────────────────────────────────────────────
st.header("1. Upload menu PDF")
uploaded = st.file_uploader(
    "Drop the menu PDF here",
    type=["pdf"],
    help="The standard 3-week rotation PDF from Aspens.",
)

if uploaded is not None and uploaded.name not in st.session_state.get("processed_files", []):
    if st.button("🤖 Extract menu data with Claude", type="primary"):
        with st.spinner("Reading PDF and extracting menu (about 20 seconds)…"):
            try:
                pdf_bytes = uploaded.read()
                extracted = extract_menu_from_pdf(pdf_bytes)
                menu = expand_to_weekly_menu(extracted)
                st.session_state["menu"] = menu
                st.session_state["term_label"] = menu.get("_termLabel", "")
                st.session_state["df"] = menu_to_dataframe(menu)
                st.session_state.setdefault("processed_files", []).append(uploaded.name)
                st.success(f"Extracted {len(menu['weeks'])} weeks from {len(extracted['weeks'])} rotation patterns.")
                st.rerun()
            except json.JSONDecodeError as e:
                st.error(f"Claude returned invalid JSON. {e}")
                st.code(str(e))
            except Exception as e:
                st.error(f"Extraction failed: {e}")

# ── Step 2: Review and edit ─────────────────────────────────────────────
if "df" in st.session_state:
    st.divider()
    st.header("2. Review and edit")
    st.caption("Tweak any cell that looks wrong. Add or delete rows if needed. Use YYYY-MM-DD format for dates.")

    term = st.text_input("Term label", value=st.session_state.get("term_label", ""))
    st.session_state["term_label"] = term

    edited = st.data_editor(
        st.session_state["df"],
        num_rows="dynamic",
        use_container_width=True,
        height=420,
        column_config={
            "Monday": st.column_config.TextColumn("Monday (YYYY-MM-DD)", required=True, width="medium"),
            "Day": st.column_config.SelectboxColumn("Day", options=DAYS, required=True, width="small"),
            "Red column": st.column_config.TextColumn("Red column option", width="large"),
            "Green column": st.column_config.TextColumn("Green column option", width="large"),
        },
        key="editor",
    )
    st.session_state["df"] = edited

    bad_mondays = validate_mondays(edited)
    if bad_mondays:
        st.warning(
            f"These dates are not Mondays: {', '.join(bad_mondays)}. "
            "The morning tools key on Monday dates only — fix before publishing."
        )

    n_weeks = len(edited["Monday"].unique())
    n_rows = len(edited)
    st.caption(f"📊 {n_weeks} unique Mondays · {n_rows} rows total (5 expected per week)")

    # ── Step 3: Publish ─────────────────────────────────────────────────
    st.divider()
    st.header("3. Publish to GitHub")

    final_menu = dataframe_to_menu(edited, term)

    with st.expander("👀 Preview JSON that will be published"):
        st.code(json.dumps(final_menu, indent=2), language="json")

    commit_msg = st.text_input(
        "Commit message",
        value=f"Update menu — {term or 'new term'} (via Streamlit)",
    )

    can_publish = not bad_mondays and len(final_menu["weeks"]) > 0
    if not can_publish:
        st.info("Fix the issues above before publishing.")

    if st.button("🚀 Publish to GitHub", disabled=not can_publish, type="primary"):
        with st.spinner("Committing to GitHub…"):
            try:
                commit_url = publish_to_github(final_menu, commit_msg)
                st.success(
                    f"✅ Published. The morning tools will pick up the new menu within about a minute."
                )
                st.markdown(f"[View commit on GitHub]({commit_url})")
                st.markdown(
                    "**Year-group tools:**  \n"
                    "🔗 [Y1 Beech](https://wallscourtfarm.github.io/staff-learning-tools/year1-beech/) · "
                    "[Y2 Willow](https://wallscourtfarm.github.io/staff-learning-tools/year2-willow/) · "
                    "[Y3 Acer](https://wallscourtfarm.github.io/staff-learning-tools/year3-acer/) · "
                    "[Y4 Maple](https://wallscourtfarm.github.io/staff-learning-tools/year4-maple/) · "
                    "[Y5 Hazel](https://wallscourtfarm.github.io/staff-learning-tools/year5-hazel/) · "
                    "[Y6 Elm](https://wallscourtfarm.github.io/staff-learning-tools/year6-elm/)"
                )
            except Exception as e:
                st.error(f"Publish failed: {e}")

    # ── Reset ───────────────────────────────────────────────────────────
    st.divider()
    if st.button("🔄 Start over (clear loaded data)"):
        for k in ["df", "menu", "term_label", "processed_files"]:
            st.session_state.pop(k, None)
        st.rerun()
