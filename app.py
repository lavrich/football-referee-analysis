from datetime import datetime, timedelta
import io
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
import numpy as np

if not hasattr(np, "bool"):
    np.bool = bool
if not hasattr(np, "object"):
    np.object = object

import streamlit as st
import pandas as pd

try:
    st.set_page_config(
        page_title="Football Referee Analysis",
        layout="wide",
    )
except Exception:
    pass

st.title("⚽ English Football Referee & Team Analysis")
st.markdown(
    "Analyse historical referee trends, match results, and upcoming fixture"
    " bias."
)


def standardize_referee_name(name):
    """Standardises messy referee names into 'I Surname' format (e.g., A Madley)."""
    if pd.isna(name):
        return "Unknown"
    name = re.sub(r"\s+", " ", str(name).strip())
    if not name:
        return "Unknown"
    if "," in name:
        parts = name.split(",", 1)
        surname = parts[0].strip()
        first_part = parts[1].strip().replace(".", "").split()
        initial = first_part[0][0].upper() if first_part else ""
        return f"{initial} {surname}".strip()
    parts = name.replace(".", "").split()
    if len(parts) == 1:
        return parts[0].title()
    if len(parts) >= 2:
        return f"{parts[0][0].upper()} {parts[-1].title()}"
    return name.title()


def lookup_referee_wikipedia(standardized_name, ctx):
    """Queries Wikipedia's API via urllib to find the full name of a new referee."""
    if not isinstance(standardized_name, str) or standardized_name in [
        "",
        "Unknown",
    ]:
        return "Unknown"

    parts = standardized_name.split()
    if len(parts) < 2:
        return "Manual Check Needed"

    initial = parts[0].upper()
    surname = " ".join(parts[1:])

    query = urllib.parse.quote(f'"{surname}" "football referee" England')
    url = f"https://wikipedia.org{query}&format=json&srlimit=5"
    headers = {"User-Agent": "FootballRefereeLookupBot/1.0 (Local Streamlit App)"}

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            results = data.get("query", {}).get("search", [])

            for item in results:
                title = item["title"]
                clean_title = re.sub(r"\s*\(.*?\)", "", title).strip()
                name_parts = clean_title.split()

                if len(name_parts) >= 2:
                    if (
                        name_parts[0].upper().startswith(initial)
                        and name_parts[-1].lower() == surname.lower()
                    ):
                        return clean_title

            return "Not Found"
    except Exception:
        return "Lookup Error"


def sync_referee_master(df_matches, ctx):
    """Checks for any new referees, looks them up on Wikipedia, and updates referee_master.csv."""
    ref_file = "referee_master.csv"
    unique_refs = sorted([
        r
        for r in df_matches["Referee_Initial"].dropna().unique()
        if r not in ["", "Unknown", "nan"]
    ])

    if os.path.exists(ref_file):
        df_ref_master = pd.read_csv(ref_file)
        existing_map = dict(
            zip(df_ref_master["Initial_Name"], df_ref_master["Full_Name"])
        )
    else:
        existing_map = {}

    updated_records = []
    clean_lookup_map = {}
    new_lookups = 0
    for ref in unique_refs:
        current_full = existing_map.get(ref, "")

        if pd.isna(current_full) or current_full in [
            "",
            "Not Found",
            "Lookup Error",
        ]:
            full_name = lookup_referee_wikipedia(ref, ctx)
            new_lookups += 1
            time.sleep(0.3)
        else:
            full_name = current_full

        updated_records.append({"Initial_Name": ref, "Full_Name": full_name})

        if full_name not in [
            "",
            "Not Found",
            "Lookup Error",
            "Manual Check Needed",
            "Unknown",
        ]:
            clean_lookup_map[ref] = full_name
        else:
            clean_lookup_map[ref] = ref

    for old_init, old_full in existing_map.items():
        if old_init not in clean_lookup_map:
            updated_records.append(
                {"Initial_Name": old_init, "Full_Name": old_full}
            )
            if old_full not in [
                "",
                "Not Found",
                "Lookup Error",
                "Manual Check Needed",
                "Unknown",
            ]:
                clean_lookup_map[old_init] = old_full
            else:
                clean_lookup_map[old_init] = old_init

    pd.DataFrame(updated_records).sort_values("Initial_Name").to_csv(
        ref_file, index=False
    )
    return clean_lookup_map, new_lookups


def update_master_csv():
    seasons = ["2324", "2425", "2526", "2627"]
    divs = ["E0", "E1", "E2", "E3"]
    new_frames = []
    last_error = "Unknown error"

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/csv,text/plain,*/*",
    }

    for s in seasons:
        for d in divs:
            for protocol in ["https", "http"]:
                url = f"{protocol}://www.football-data.co.uk/mmz4281/{s}/{d}.csv"
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(
                        req, context=ctx, timeout=10
                    ) as response:
                        raw_csv = response.read().decode("latin1")
                        df_temp = pd.read_csv(io.StringIO(raw_csv))
                        if (
                            not df_temp.empty
                            and "Date" in df_temp.columns
                            and "Referee" in df_temp.columns
                        ):
                            df_temp["Div"] = d
                            new_frames.append(df_temp)
                            break
                except Exception as e:
                    last_error = f"{type(e).__name__}: {str(e)}"
                    continue

    if not new_frames:
        return False, f"Download failed. Exact reason: {last_error}"

    df_new = pd.concat(new_frames, ignore_index=True)
    df_new = df_new.dropna(subset=["Date", "HomeTeam", "AwayTeam"])

    df_new["Referee_Initial"] = df_new["Referee"].apply(
        standardize_referee_name
    )
    ref_map, new_lookups = sync_referee_master(df_new, ctx)
    df_new["Referee"] = (
        df_new["Referee_Initial"]
        .map(ref_map)
        .fillna(df_new["Referee_Initial"])
    )
    df_new["Date"] = pd.to_datetime(
        df_new["Date"], errors="coerce", dayfirst=True
    ).dt.strftime("%Y-%m-%d")
    df_new = df_new.dropna(subset=["Date"])

    if os.path.exists("english_football_master.csv"):
        df_existing = pd.read_csv("english_football_master.csv")
        df_existing["Date"] = pd.to_datetime(
            df_existing["Date"], errors="coerce", dayfirst=True
        ).dt.strftime("%Y-%m-%d")
        df_existing = df_existing.dropna(subset=["Date"])

        if "Referee_Initial" not in df_existing.columns:
            df_existing["Referee_Initial"] = df_existing["Referee"].apply(
                standardize_referee_name
            )
        df_combined = pd.concat([df_existing, df_new], ignore_index=True)
        df_combined = df_combined.drop_duplicates(
            subset=["Date", "HomeTeam", "AwayTeam"], keep="last"
        )
    else:
        df_combined = df_new.drop_duplicates(
            subset=["Date", "HomeTeam", "AwayTeam"], keep="last"
        )

    df_combined.to_csv("english_football_master.csv", index=False)

    with open("cache_version.txt", "w") as f:
        f.write(str(time.time()))

    lookup_note = (
        f" ({new_lookups} new referee lookups)" if new_lookups > 0 else ""
    )
    return (
        True,
        f"Database updated! Total records: {len(df_combined)} matches{lookup_note}.",
    )


@st.cache_data
def load_data(cache_buster):
    try:
        needed_cols = [
            "Div",
            "Date",
            "HomeTeam",
            "AwayTeam",
            "FTHG",
            "FTAG",
            "FTR",
            "Referee",
            "HF",
            "AF",
            "HY",
            "AY",
            "HR",
            "AR",
        ]
        df = pd.read_csv(
            "english_football_master.csv",
            usecols=lambda c: c in needed_cols,
        )
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True)
        df = df.dropna(subset=["Date"])
        df["Year"] = df["Date"].dt.year

        # Always standardize initial names and re-apply referee_master.csv
        df["Referee_Initial"] = df["Referee"].apply(standardize_referee_name)
        if os.path.exists("referee_master.csv"):
            df_ref = pd.read_csv("referee_master.csv")
            valid_refs = df_ref[
                ~df_ref["Full_Name"].isin([
                    "", "Not Found", "Lookup Error", "Manual Check Needed", "Unknown"
                ])
                & df_ref["Full_Name"].notna()
            ]
            ref_map = dict(
                zip(valid_refs["Initial_Name"], valid_refs["Full_Name"])
            )
            df["Referee"] = (
                df["Referee_Initial"].map(ref_map).fillna(df["Referee_Initial"])
            )
        else:
            df["Referee"] = df["Referee_Initial"]

        return df
    except Exception as e:
        st.error(f"Error loading master CSV file: {e}")
        return None


# --- SCHEDULED AUTO-SYNC (MONDAYS & THURSDAYS AT 00:00) ---
STATUS_FILE = "update_status.json"


def get_last_scheduled_slot(now):
    # Monday = 0, Thursday = 3
    candidates = []
    for days_back in range(8):
        day = now - timedelta(days=days_back)
        if day.weekday() in (0, 3):
            slot = day.replace(hour=0, minute=0, second=0, microsecond=0)
            if slot <= now:
                candidates.append(slot)
    return max(candidates)


def read_update_status():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_success": None, "last_error": None}


def write_update_status(last_success, last_error):
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(
                {"last_success": last_success, "last_error": last_error}, f
            )
    except Exception:
        pass


def run_data_sync():
    status = read_update_status()
    try:
        with st.spinner("Syncing latest match results & referee names..."):
            success, msg = update_master_csv()
        if success:
            now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            write_update_status(now_iso, None)
        else:
            write_update_status(status.get("last_success"), msg)
    except Exception as e:
        write_update_status(status.get("last_success"), str(e))


now_dt = datetime.now()
latest_slot = get_last_scheduled_slot(now_dt)
sync_status = read_update_status()

due_for_update = False
if not sync_status.get("last_success"):
    due_for_update = True
else:
    try:
        last_ok_dt = datetime.strptime(
            sync_status["last_success"], "%Y-%m-%d %H:%M:%S"
        )
        if last_ok_dt < latest_slot:
            due_for_update = True
    except Exception:
        due_for_update = True

if due_for_update:
    run_data_sync()
    sync_status = read_update_status()

last_ok_str = sync_status.get("last_success")
last_err_str = sync_status.get("last_error")

if last_ok_str:
    try:
        dt_fmt = datetime.strptime(last_ok_str, "%Y-%m-%d %H:%M:%S").strftime(
            "%a %d %b %Y at %H:%M"
        )
    except Exception:
        dt_fmt = last_ok_str
else:
    dt_fmt = "Never"

if last_err_str:
    st.markdown(
        f"**Data Status:** Last updated **{dt_fmt}** &nbsp;|&nbsp; "
        f"<span style='color:#d93025; font-weight:bold;'>❗ Update Problem:"
        f" {last_err_str}</span>",
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        f"**Data Status:** Last updated **{dt_fmt}** "
        f"*(Auto-updates Mondays & Thursdays at 00:00)*"
    )

cache_version = "1"
if os.path.exists("cache_version.txt"):
    try:
        with open("cache_version.txt", "r") as f:
            cache_version = f.read().strip()
    except Exception:
        pass

df_master = load_data(cache_version)
if df_master is None:
    st.warning(
        "Could not load data. Check if 'english_football_master.csv' exists or"
        " click Refresh Live Data."
    )
    st.stop()

for col in ["HF", "AF", "HY", "AY", "HR", "AR"]:
    if col in df_master.columns:
        df_master[col] = pd.to_numeric(df_master[col], errors="coerce").fillna(0)

for col in ["Referee", "HomeTeam", "AwayTeam", "FTR"]:
    if col in df_master.columns:
        df_master[col] = df_master[col].astype(str).str.strip()

st.sidebar.header("Filter Options")
st.sidebar.markdown("**Leagues:**")
selected_leagues = []
if st.sidebar.checkbox("Premier League (E0)", value=True):
    selected_leagues.append("E0")
if st.sidebar.checkbox("Championship (E1)", value=True):
    selected_leagues.append("E1")
if st.sidebar.checkbox("League 1 (E2)", value=True):
    selected_leagues.append("E2")
if st.sidebar.checkbox("League 2 (E3)", value=True):
    selected_leagues.append("E3")

if not selected_leagues:
    st.sidebar.warning("Please check at least one league in the sidebar.")
    st.stop()

min_year = int(df_master["Year"].min())
max_year = int(df_master["Year"].max())
selected_years = st.sidebar.slider(
    "Select Year Window", min_year, max_year, (min_year, max_year)
)
start_year, end_year = selected_years
venue_filter = st.sidebar.selectbox(
    "Venue Split (For Table View Only)",
    ["All Match Venues", "Home Only", "Away Only"],
)
df_filtered = df_master[df_master["Div"].isin(selected_leagues)]
df_filtered = df_filtered[
    (df_filtered["Year"] >= start_year) & (df_filtered["Year"] <= end_year)
]
all_teams = sorted([
    t
    for t in set(df_filtered["HomeTeam"].unique())
    | set(df_filtered["AwayTeam"].unique())
    if t != "nan" and t != ""
])
if not all_teams:
    st.warning("No teams found. Try widening filters.")
    st.stop()

STATE_FILE = "ui_state.json"
USE_SESSION = hasattr(st, "session_state")


def load_ui_state():
    if USE_SESSION:
        if "ui_state" not in st.session_state:
            st.session_state["ui_state"] = {}
        return st.session_state["ui_state"]
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_ui_state(updates):
    if USE_SESSION:
        if "ui_state" not in st.session_state:
            st.session_state["ui_state"] = {}
        st.session_state["ui_state"].update(updates)
        return
    state = load_ui_state()
    state.update(updates)
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass

def get_idx(options, saved_val, fallback_val=None):
    if saved_val in options:
        return options.index(saved_val)
    if fallback_val and fallback_val in options:
        return options.index(fallback_val)
    return 0


ui_state = load_ui_state()


# SECTION 1: INDIVIDUAL TEAM REPORT ENGINE
st.markdown("## Individual Team Report Engine")
selected_team = st.selectbox(
    "Choose a Team to Analyse:",
    all_teams,
    index=get_idx(all_teams, ui_state.get("s1_team"), "Arsenal"),
)

try:
    home_m = df_filtered[df_filtered["HomeTeam"] == selected_team].copy()
    home_m["Team_Fouls"] = home_m["HF"]
    home_m["Team_Yellows"] = home_m["HY"]
    home_m["Team_Reds"] = home_m["HR"]
    home_m["Outcome"] = (
        home_m["FTR"].map({"H": "W", "D": "D", "A": "L"}).fillna("-")
        if "FTR" in home_m.columns
        else "-"
    )
    away_m = df_filtered[df_filtered["AwayTeam"] == selected_team].copy()
    away_m["Team_Fouls"] = away_m["AF"]
    away_m["Team_Yellows"] = away_m["AY"]
    away_m["Team_Reds"] = away_m["AR"]
    away_m["Outcome"] = (
        away_m["FTR"].map({"A": "W", "D": "D", "H": "L"}).fillna("-")
        if "FTR" in away_m.columns
        else "-"
    )
    if venue_filter == "Home Only":
        team_matches = home_m
    elif venue_filter == "Away Only":
        team_matches = away_m
    else:
        team_matches = pd.concat([home_m, away_m], ignore_index=True)

    if team_matches.empty:
        st.warning(f"No match records found for {selected_team}.")
    else:
        s1_metric_opts = ["Yellow Cards", "Red Cards", "Fouls"]
        metric_choice = st.selectbox(
            "Select Offence Metric:",
            s1_metric_opts,
            index=get_idx(s1_metric_opts, ui_state.get("s1_metric")),
        )

        s1_base_opts = [
            "Double-Adjusted (Team + Ref)",
            "Team Average Only",
            "Referee Average Only (Original)",
        ]
        baseline_mode = st.selectbox(
            "Baseline Comparison Mode:",
            s1_base_opts,
            index=get_idx(s1_base_opts, ui_state.get("s1_baseline")),
        )

        s1_sort_opts = [
            "Most Unfavourable First (Variance)",
            "Most Favourable First (Variance)",
            "Highest Win % First",
            "Lowest Win % First",
        ]
        sort_order = st.selectbox(
            "Ordering Sequence:",
            s1_sort_opts,
            index=get_idx(s1_sort_opts, ui_state.get("s1_sort")),
        )

        save_ui_state({
            "s1_team": selected_team,
            "s1_metric": metric_choice,
            "s1_baseline": baseline_mode,
            "s1_sort": sort_order,
        })

        max_games_vs_team = (
            int(team_matches["Referee"].value_counts().max())
            if "Referee" in team_matches.columns
            else 1
        )
        saved_threshold = 1
        if os.path.exists("slider_memory.txt"):
            try:
                with open("slider_memory.txt", "r") as f:
                    saved_threshold = int(f.read().strip())
            except Exception:
                pass
        active_slider_default = (
            saved_threshold if saved_threshold <= max_games_vs_team else 1
        )
        team_games_limit = st.slider(
            f"Filter: Minimum matches refereed specifically for {selected_team}:",
            1,
            max_games_vs_team,
            active_slider_default,
        )
        try:
            with open("slider_memory.txt", "w") as f:
                f.write(str(team_games_limit))
        except Exception:
            pass

        team_global_fouls = team_matches["Team_Fouls"].mean()
        team_global_yellows = team_matches["Team_Yellows"].mean()
        team_global_reds = team_matches["Team_Reds"].mean()

        if venue_filter == "Home Only":
            lg_fouls = df_filtered["HF"].mean()
            lg_yellows = df_filtered["HY"].mean()
            lg_reds = df_filtered["HR"].mean()
        elif venue_filter == "Away Only":
            lg_fouls = df_filtered["AF"].mean()
            lg_yellows = df_filtered["AY"].mean()
            lg_reds = df_filtered["AR"].mean()
        else:
            lg_fouls = (df_filtered["HF"].mean() + df_filtered["AF"].mean()) / 2.0
            lg_yellows = (df_filtered["HY"].mean() + df_filtered["AY"].mean()) / 2.0
            lg_reds = (df_filtered["HR"].mean() + df_filtered["AR"].mean()) / 2.0

        ref_stats = []
        for ref, group in df_filtered.groupby("Referee"):
            if ref == "nan" or ref == "":
                continue
            games = len(group)
            if games == 0:
                continue
            if venue_filter == "Home Only":
                b_fouls, b_yellows, b_reds = (
                    group["HF"].sum() / games,
                    group["HY"].sum() / games,
                    group["HR"].sum() / games,
                )
            elif venue_filter == "Away Only":
                b_fouls, b_yellows, b_reds = (
                    group["AF"].sum() / games,
                    group["AY"].sum() / games,
                    group["AR"].sum() / games,
                )
            else:
                b_fouls = ((group["HF"].sum() + group["AF"].sum()) / games) / 2.0
                b_yellows = ((group["HY"].sum() + group["AY"].sum()) / games) / 2.0
                b_reds = ((group["HR"].sum() + group["AR"].sum()) / games) / 2.0
            ref_stats.append({
                "Referee": ref,
                "Global_Games": games,
                "Ref_Avg_Fouls": b_fouls,
                "Ref_Avg_Yellows": b_yellows,
                "Ref_Avg_Reds": b_reds,
            })
        df_ref_baselines = pd.DataFrame(ref_stats)

        team_ref_stats = []
        for ref, group in team_matches.groupby("Referee"):
            if ref == "nan" or ref == "":
                continue
            g_count = len(group)
            wins = int((group["Outcome"] == "W").sum())
            draws = int((group["Outcome"] == "D").sum())
            losses = int((group["Outcome"] == "L").sum())
            win_pct = (wins / g_count) * 100.0
            team_ref_stats.append({
                "Referee": ref,
                "Games_vs_Team": g_count,
                "WDL_Record": f"{wins}-{draws}-{losses}",
                "Win_Pct_Num": win_pct,
                "Win_Pct": f"{win_pct:.0f}%",
                "Team_Total_Fouls": group["Team_Fouls"].sum(),
                "Team_Total_Yellows": group["Team_Yellows"].sum(),
                "Team_Total_Reds": group["Team_Reds"].sum(),
                "Team_Avg_Fouls": group["Team_Fouls"].mean(),
                "Team_Avg_Yellows": group["Team_Yellows"].mean(),
                "Team_Avg_Reds": group["Team_Reds"].mean(),
            })
        df_team_ref = pd.DataFrame(team_ref_stats)

        if not df_team_ref.empty and not df_ref_baselines.empty:
            merged = pd.merge(
                df_team_ref, df_ref_baselines, on="Referee", how="inner"
            )
            if baseline_mode == "Double-Adjusted (Team + Ref)":
                merged["Baseline_Fouls"] = (
                    team_global_fouls + merged["Ref_Avg_Fouls"] - lg_fouls
                )
                merged["Baseline_Yellows"] = (
                    team_global_yellows + merged["Ref_Avg_Yellows"] - lg_yellows
                )
                merged["Baseline_Reds"] = (
                    team_global_reds + merged["Ref_Avg_Reds"] - lg_reds
                )
                baseline_col_label = "Expected Baseline"
            elif baseline_mode == "Team Average Only":
                merged["Baseline_Fouls"] = team_global_fouls
                merged["Baseline_Yellows"] = team_global_yellows
                merged["Baseline_Reds"] = team_global_reds
                baseline_col_label = f"{selected_team} Avg"
            else:
                merged["Baseline_Fouls"] = merged["Ref_Avg_Fouls"]
                merged["Baseline_Yellows"] = merged["Ref_Avg_Yellows"]
                merged["Baseline_Reds"] = merged["Ref_Avg_Reds"]
                baseline_col_label = "Ref Baseline"

            merged["Fouls Variance"] = (
                merged["Team_Avg_Fouls"] - merged["Baseline_Fouls"]
            )
            merged["Yellows Variance"] = (
                merged["Team_Avg_Yellows"] - merged["Baseline_Yellows"]
            )
            merged["Reds Variance"] = (
                merged["Team_Avg_Reds"] - merged["Baseline_Reds"]
            )

            final_df = merged[merged["Games_vs_Team"] >= team_games_limit]
            if final_df.empty:
                st.warning("No records match your minimum game slider threshold limit.")
            else:
                if metric_choice == "Yellow Cards":
                    var_col, t_high, t_mid = "Yellows Variance", 0.60, 0.30
                    cols_to_keep = [
                        "Referee", "Games_vs_Team", "WDL_Record", "Win_Pct",
                        "Win_Pct_Num", "Team_Total_Yellows", "Global_Games",
                        "Team_Avg_Yellows", "Baseline_Yellows", "Yellows Variance",
                    ]
                elif metric_choice == "Red Cards":
                    var_col, t_high, t_mid = "Reds Variance", 0.15, 0.07
                    cols_to_keep = [
                        "Referee", "Games_vs_Team", "WDL_Record", "Win_Pct",
                        "Win_Pct_Num", "Team_Total_Reds", "Global_Games",
                        "Team_Avg_Reds", "Baseline_Reds", "Reds Variance",
                    ]
                else:
                    var_col, t_high, t_mid = "Fouls Variance", 3.00, 1.50
                    cols_to_keep = [
                        "Referee", "Games_vs_Team", "WDL_Record", "Win_Pct",
                        "Win_Pct_Num", "Team_Total_Fouls", "Global_Games",
                        "Team_Avg_Fouls", "Baseline_Fouls", "Fouls Variance",
                    ]
                display_df = final_df[cols_to_keep].copy()

                if sort_order == "Highest Win % First":
                    display_df = display_df.sort_values(
                        by=["Win_Pct_Num", "Games_vs_Team"], ascending=[False, False]
                    )
                elif sort_order == "Lowest Win % First":
                    display_df = display_df.sort_values(
                        by=["Win_Pct_Num", "Games_vs_Team"], ascending=[True, False]
                    )
                elif sort_order == "Most Favourable First (Variance)":
                    display_df = display_df.sort_values(by=var_col, ascending=True)
                else:
                    display_df = display_df.sort_values(by=var_col, ascending=False)

                display_df = display_df.drop(columns=["Win_Pct_Num"])
                display_df.columns = [
                    "Referee Name", "Team Matches", "W-D-L", "Win %",
                    "Team Actions", "All Ref Matches", "Avg Team Actions",
                    baseline_col_label, "Variance",
                ]

                def assign_tier_dots(v):
                    if v >= t_high:
                        return "🔴🔴🔴"
                    elif t_mid <= v < t_high:
                        return "🔴🔴"
                    elif 0.0 < v < t_mid:
                        return "🔴"
                    elif -t_mid < v <= 0.0:
                        return "🟢"
                    elif -t_high < v <= -t_mid:
                        return "🟢🟢"
                    else:
                        return "🟢🟢🟢"

                display_df["Rating"] = display_df["Variance"].apply(assign_tier_dots)
                display_df["Avg Team Actions"] = display_df["Avg Team Actions"].map("{:.2f}".format)
                display_df[baseline_col_label] = display_df[baseline_col_label].map("{:.2f}".format)
                display_df["Variance"] = display_df["Variance"].map("{:+.2f}".format)
                display_df["Team Actions"] = display_df["Team Actions"].astype(int)
                ordered_cols = [
                    "Referee Name", "Rating", "Team Matches", "W-D-L", "Win %",
                    "Team Actions", "All Ref Matches", "Avg Team Actions",
                    baseline_col_label, "Variance",
                ]
                display_df = display_df[ordered_cols]

                table_style = """
<style>
.block-container {
    max-width: 1050px !important;
}
table.custom-ref-table {
    width: 100% !important;
    border-collapse: collapse !important;
    font-family: Arial, sans-serif !important;
    font-size: 14px !important;
}
table.custom-ref-table thead tr th {
    background-color: #1155cc !important;
    color: #ffffff !important;
    font-weight: bold !important;
    padding: 8px 10px !important;
    border: 1px solid #d0d7de !important;
    text-align: center !important;
    vertical-align: top !important;
}
table.custom-ref-table thead tr th:nth-child(1),
table.custom-ref-table thead tr th:nth-child(2) {
    text-align: left !important;
}
table.custom-ref-table tbody tr td {
    padding: 6px 10px !important;
    border: 1px solid #d0d7de !important;
    color: #000000 !important;
    background-color: #ffffff !important;
    white-space: nowrap !important;
}
table.custom-ref-table tbody tr td:nth-child(1) {
    text-align: left !important;
}
table.custom-ref-table tbody tr td:nth-child(2) {
    text-align: left !important;
}
table.custom-ref-table tbody tr td:nth-child(n+3) {
    text-align: center !important;
}
table.custom-ref-table tbody tr:hover td {
    background-color: #f1f5fb !important;
}
</style>
"""
                html_table = display_df.to_html(
                    index=False,
                    classes="custom-ref-table",
                    border=0,
                    escape=False,
                )
                st.markdown(table_style, unsafe_allow_html=True)
                st.markdown(html_table, unsafe_allow_html=True)

except Exception as e:
    st.error(f"Team Report Error: {e}")


# SECTION 2: COMBINED ALL-IN-ONE MATCH PREDICTOR ENGINE
st.markdown(
    """
<div style="background-color: #f0f4fc; border-left: 6px solid #1155cc; padding: 12px 16px; margin: 40px 0 15px 0; border-radius: 4px;">
    <h2 style="margin: 0; color: #1155cc; font-size: 22px;">2. Upcoming Match Predictor Engine</h2>
</div>
""",
    unsafe_allow_html=True,
)

st.markdown("## Upcoming Match Predictor Engine")
st.markdown(
    "Evaluate officiating tendencies across all three disciplinary metrics"
    " using the Double-Adjusted Expected model."
)
try:
    raw_refs = df_filtered["Referee"].unique().tolist()
    pred_referees = sorted(
        [r for r in raw_refs if r != "" and r.lower() != "nan"]
    )
    default_s2_ref = (
        "Andy Madley" if "Andy Madley" in pred_referees else "A Madley"
    )
    home_team_sel = st.selectbox(
        "Predictor Home Team:",
        all_teams,
        index=get_idx(all_teams, ui_state.get("s2_home"), "Arsenal"),
    )
    away_team_sel = st.selectbox(
        "Predictor Away Team:",
        all_teams,
        index=get_idx(all_teams, ui_state.get("s2_away"), "Aston Villa"),
    )
    ref_sel = st.selectbox(
        "Predictor Official:",
        pred_referees,
        index=get_idx(pred_referees, ui_state.get("s2_ref"), default_s2_ref),
    )

    save_ui_state({
        "s2_home": home_team_sel,
        "s2_away": away_team_sel,
        "s2_ref": ref_sel,
    })

    ref_games = df_filtered[df_filtered["Referee"] == ref_sel]
    home_team_all = df_filtered[df_filtered["HomeTeam"] == home_team_sel]
    away_team_all = df_filtered[df_filtered["AwayTeam"] == away_team_sel]

    if ref_games.empty:
        st.warning(
            "No historical match records found for this specific referee within"
            " the current year split."
        )
    else:
        h_matches = ref_games[ref_games["HomeTeam"] == home_team_sel]
        a_matches = ref_games[ref_games["AwayTeam"] == away_team_sel]
        st.write(
            f"**{ref_sel} Career Sample:** {len(ref_games)} Total Matches | "
            f"**{home_team_sel} at Home:** {len(h_matches)} matches | "
            f"**{away_team_sel} Away:** {len(a_matches)} matches"
        )
        metrics_config = [
            ("Fouls", "HF", "AF", 0.50),
            ("Yellow Cards", "HY", "AY", 0.25),
            ("Red Cards", "HR", "AR", 0.05),
        ]
        summary_rows = []
        verdict_notes = []

        for label, h_col, a_col, threshold in metrics_config:
            lg_h_avg = float(df_filtered[h_col].mean())
            lg_a_avg = float(df_filtered[a_col].mean())

            ref_h_avg = float(ref_games[h_col].mean())
            ref_a_avg = float(ref_games[a_col].mean())

            team_h_avg = (
                float(home_team_all[h_col].mean())
                if not home_team_all.empty
                else lg_h_avg
            )
            team_a_avg = (
                float(away_team_all[a_col].mean())
                if not away_team_all.empty
                else lg_a_avg
            )

            h_expected = team_h_avg + ref_h_avg - lg_h_avg
            a_expected = team_a_avg + ref_a_avg - lg_a_avg

            if not h_matches.empty:
                h_actual = float(h_matches[h_col].mean())
                h_var = h_actual - h_expected
                h_disp = f"{h_actual:.2f} ({h_expected:.2f})"
            else:
                h_var = 0.0
                h_disp = f"N/A ({h_expected:.2f})"

            if not a_matches.empty:
                a_actual = float(a_matches[a_col].mean())
                a_var = a_actual - a_expected
                a_disp = f"{a_actual:.2f} ({a_expected:.2f})"
            else:
                a_var = 0.0
                a_disp = f"N/A ({a_expected:.2f})"

            net_tilt = h_var - a_var
            if net_tilt > threshold:
                fav_str = f"Favours {away_team_sel} ({abs(net_tilt):.2f})"
                verdict_notes.append(
                    f"**{label}:** Penalises {home_team_sel} more heavily than"
                    f" expected (tilts towards {away_team_sel} by"
                    f" {abs(net_tilt):.2f} per game)."
                )
            elif net_tilt < -threshold:
                fav_str = f"Favours {home_team_sel} ({abs(net_tilt):.2f})"
                verdict_notes.append(
                    f"**{label}:** Penalises {away_team_sel} more heavily than"
                    f" expected (tilts towards {home_team_sel} by"
                    f" {abs(net_tilt):.2f} per game)."
                )
            else:
                fav_str = "Neutral / Balanced"
                verdict_notes.append(
                    f"**{label}:** Statistically balanced relative to"
                    f" expectations ({net_tilt:+.2f} net variance)."
                )

            summary_rows.append({
                "Metric": label,
                f"{home_team_sel} Actual (Exp)": h_disp,
                f"{home_team_sel} Var": f"{h_var:+.2f}",
                f"{away_team_sel} Actual (Exp)": a_disp,
                f"{away_team_sel} Var": f"{a_var:+.2f}",
                "Historical Tilt": fav_str,
            })

        pred_df = pd.DataFrame(summary_rows)
        pred_html = pred_df.to_html(
            index=False,
            classes="custom-ref-table",
            border=0,
            escape=False,
        )
        st.markdown(pred_html, unsafe_allow_html=True)

        st.markdown("### Combined Bias Breakdown")
        for note in verdict_notes:
            st.markdown(f"* {note}")
except Exception as e:
    st.error(f"Predictor Engine Error: {e}")

# SECTION 3: INDIVIDUAL REFEREE LEAGUE TABLE
# st.markdown("---")
# st.markdown("## Individual Referee League Table")
st.markdown(
    """
<div style="background-color: #f0f4fc; border-left: 6px solid #1155cc; padding: 12px 16px; margin: 40px 0 15px 0; border-radius: 4px;">
    <h2 style="margin: 0; color: #1155cc; font-size: 22px;">3. Individual Referee League Table</h2>
</div>
""",
    unsafe_allow_html=True,
)

st.markdown(
    "Rank every team officiated by a selected referee from most favoured to"
    " most penalised."
)
try:
    raw_refs_s3 = df_filtered["Referee"].unique().tolist()
    s3_referees = sorted(
        [r for r in raw_refs_s3 if r != "" and r.lower() != "nan"]
    )
    if not s3_referees:
        st.warning("No referees found for the current sidebar filters.")
    else:
        default_s3_ref = next(
            (
                c
                for c in ["Andy Madley", "A Madley", "Michael Oliver"]
                if c in s3_referees
            ),
            None,
        )
        selected_ref_s3 = st.selectbox(
            "Choose a Referee to Analyse:",
            s3_referees,
            index=get_idx(
                s3_referees, ui_state.get("s3_ref"), default_s3_ref
            ),
            key="s3_ref_select",
        )

        s3_metric_opts = ["Yellow Cards", "Red Cards", "Fouls"]
        metric_choice_s3 = st.selectbox(
            "Select Offence Metric:",
            s3_metric_opts,
            index=get_idx(s3_metric_opts, ui_state.get("s3_metric")),
            key="s3_metric_select",
        )

        s3_base_opts = [
            "Double-Adjusted (Team + Ref)",
            "Team Average Only",
            "Referee Average Only (Original)",
        ]
        baseline_mode_s3 = st.selectbox(
            "Baseline Comparison Mode:",
            s3_base_opts,
            index=get_idx(s3_base_opts, ui_state.get("s3_baseline")),
            key="s3_baseline_select",
        )

        s3_sort_opts = [
            "Most Favoured First (Lowest Variance)",
            "Most Unfavourable First (Highest Variance)",
            "Highest Win % First",
            "Lowest Win % First",
        ]
        sort_order_s3 = st.selectbox(
            "Ordering Sequence:",
            s3_sort_opts,
            index=get_idx(s3_sort_opts, ui_state.get("s3_sort")),
            key="s3_sort_select",
        )

        save_ui_state({
            "s3_ref": selected_ref_s3,
            "s3_metric": metric_choice_s3,
            "s3_baseline": baseline_mode_s3,
            "s3_sort": sort_order_s3,
        })

        # Build team-level match log respecting Venue Split
        h_all = df_filtered.copy()
        h_all["Team"] = h_all["HomeTeam"]
        h_all["T_Fouls"] = h_all["HF"]
        h_all["T_Yellows"] = h_all["HY"]
        h_all["T_Reds"] = h_all["HR"]
        h_all["Outcome"] = (
            h_all["FTR"].map({"H": "W", "D": "D", "A": "L"}).fillna("-")
            if "FTR" in h_all.columns
            else "-"
        )

        a_all = df_filtered.copy()
        a_all["Team"] = a_all["AwayTeam"]
        a_all["T_Fouls"] = a_all["AF"]
        a_all["T_Yellows"] = a_all["AY"]
        a_all["T_Reds"] = a_all["AR"]
        a_all["Outcome"] = (
            a_all["FTR"].map({"A": "W", "D": "D", "H": "L"}).fillna("-")
            if "FTR" in a_all.columns
            else "-"
        )

        if venue_filter == "Home Only":
            all_team_logs = h_all
        elif venue_filter == "Away Only":
            all_team_logs = a_all
        else:
            all_team_logs = pd.concat([h_all, a_all], ignore_index=True)

        ref_team_logs = all_team_logs[
            all_team_logs["Referee"] == selected_ref_s3
        ]
        if ref_team_logs.empty:
            st.warning(
                f"No match records found for {selected_ref_s3} under current"
                " filters."
            )
        else:
            max_ref_team_games = max(
                1, int(ref_team_logs["Team"].value_counts().max())
            )
            saved_s3_threshold = 1
            if os.path.exists("slider_memory_s3.txt"):
                try:
                    with open("slider_memory_s3.txt", "r") as f:
                        saved_s3_threshold = int(f.read().strip())
                except Exception:
                    pass

            active_s3_default = min(saved_s3_threshold, max_ref_team_games)

            min_games_s3 = st.slider(
                "Filter: Minimum matches referee has officiated for a team:",
                1,
                max_ref_team_games,
                active_s3_default,
            )
            try:
                with open("slider_memory_s3.txt", "w") as f:
                    f.write(str(min_games_s3))
            except Exception:
                pass


            # League & Referee overall averages
            lg_f = all_team_logs["T_Fouls"].mean()
            lg_y = all_team_logs["T_Yellows"].mean()
            lg_r = all_team_logs["T_Reds"].mean()

            ref_f = ref_team_logs["T_Fouls"].mean()
            ref_y = ref_team_logs["T_Yellows"].mean()
            ref_r = ref_team_logs["T_Reds"].mean()

            # Team overall averages across all referees
            team_baselines = (
                all_team_logs.groupby("Team")
                .agg(
                    Team_Global_Fouls=("T_Fouls", "mean"),
                    Team_Global_Yellows=("T_Yellows", "mean"),
                    Team_Global_Reds=("T_Reds", "mean"),
                    All_Team_Matches=("Team", "count"),
                )
                .reset_index()
            )

            # Team stats specifically under the selected referee
            ref_vs_teams = []
            for team_name, grp in ref_team_logs.groupby("Team"):
                if team_name in ["", "nan"]:
                    continue
                g_cnt = len(grp)
                w = int((grp["Outcome"] == "W").sum())
                d = int((grp["Outcome"] == "D").sum())
                l = int((grp["Outcome"] == "L").sum())
                wp = (w / g_cnt) * 100.0
                ref_vs_teams.append({
                    "Team": team_name,
                    "Ref_Matches": g_cnt,
                    "WDL": f"{w}-{d}-{l}",
                    "Win_Pct_Num": wp,
                    "Win_Pct": f"{wp:.0f}%",
                    "Tot_Fouls": grp["T_Fouls"].sum(),
                    "Tot_Yellows": grp["T_Yellows"].sum(),
                    "Tot_Reds": grp["T_Reds"].sum(),
                    "Avg_Fouls": grp["T_Fouls"].mean(),
                    "Avg_Yellows": grp["T_Yellows"].mean(),
                    "Avg_Reds": grp["T_Reds"].mean(),
                })

            df_s3 = pd.merge(
                pd.DataFrame(ref_vs_teams),
                team_baselines,
                on="Team",
                how="inner",
            )

            if baseline_mode_s3 == "Double-Adjusted (Team + Ref)":
                df_s3["Base_Fouls"] = (
                    df_s3["Team_Global_Fouls"] + ref_f - lg_f
                )
                df_s3["Base_Yellows"] = (
                    df_s3["Team_Global_Yellows"] + ref_y - lg_y
                )
                df_s3["Base_Reds"] = df_s3["Team_Global_Reds"] + ref_r - lg_r
                base_label_s3 = "Expected Baseline"
            elif baseline_mode_s3 == "Team Average Only":
                df_s3["Base_Fouls"] = df_s3["Team_Global_Fouls"]
                df_s3["Base_Yellows"] = df_s3["Team_Global_Yellows"]
                df_s3["Base_Reds"] = df_s3["Team_Global_Reds"]
                base_label_s3 = "Team Avg"
            else:
                df_s3["Base_Fouls"] = ref_f
                df_s3["Base_Yellows"] = ref_y
                df_s3["Base_Reds"] = ref_r
                base_label_s3 = f"{selected_ref_s3} Avg"

            df_s3["Fouls_Var"] = df_s3["Avg_Fouls"] - df_s3["Base_Fouls"]
            df_s3["Yellows_Var"] = df_s3["Avg_Yellows"] - df_s3["Base_Yellows"]
            df_s3["Reds_Var"] = df_s3["Avg_Reds"] - df_s3["Base_Reds"]

            df_s3 = df_s3[df_s3["Ref_Matches"] >= min_games_s3]

            if df_s3.empty:
                st.warning(
                    "No teams match your minimum game slider threshold."
                )
            else:
                if metric_choice_s3 == "Yellow Cards":
                    v_col, th_h, th_m = "Yellows_Var", 0.60, 0.30
                    keep_s3 = [
                        "Team", "Ref_Matches", "WDL", "Win_Pct", "Win_Pct_Num",
                        "Tot_Yellows", "All_Team_Matches", "Avg_Yellows",
                        "Base_Yellows", "Yellows_Var",
                    ]
                elif metric_choice_s3 == "Red Cards":
                    v_col, th_h, th_m = "Reds_Var", 0.15, 0.07
                    keep_s3 = [
                        "Team", "Ref_Matches", "WDL", "Win_Pct", "Win_Pct_Num",
                        "Tot_Reds", "All_Team_Matches", "Avg_Reds",
                        "Base_Reds", "Reds_Var",
                    ]
                else:
                    v_col, th_h, th_m = "Fouls_Var", 3.00, 1.50
                    keep_s3 = [
                        "Team", "Ref_Matches", "WDL", "Win_Pct", "Win_Pct_Num",
                        "Tot_Fouls", "All_Team_Matches", "Avg_Fouls",
                        "Base_Fouls", "Fouls_Var",
                    ]

                disp_s3 = df_s3[keep_s3].copy()

                if sort_order_s3 == "Most Favoured First (Lowest Variance)":
                    disp_s3 = disp_s3.sort_values(
                        by=[v_col, "Win_Pct_Num"], ascending=[True, False]
                    )
                elif sort_order_s3 == "Most Unfavourable First (Highest Variance)":
                    disp_s3 = disp_s3.sort_values(
                        by=[v_col, "Win_Pct_Num"], ascending=[False, True]
                    )
                elif sort_order_s3 == "Highest Win % First":
                    disp_s3 = disp_s3.sort_values(
                        by=["Win_Pct_Num", "Ref_Matches"],
                        ascending=[False, False],
                    )
                else:
                    disp_s3 = disp_s3.sort_values(
                        by=["Win_Pct_Num", "Ref_Matches"],
                        ascending=[True, False],
                    )

                disp_s3 = disp_s3.drop(columns=["Win_Pct_Num"])
                disp_s3.columns = [
                    "Team Name", "Ref Matches", "W-D-L", "Win %",
                    "Team Actions", "All Team Matches", "Avg Team Actions",
                    base_label_s3, "Variance",
                ]

                def dots_s3(v):
                    if v >= th_h:
                        return "🔴🔴🔴"
                    elif th_m <= v < th_h:
                        return "🔴🔴"
                    elif 0.0 < v < th_m:
                        return "🔴"
                    elif -th_m < v <= 0.0:
                        return "🟢"
                    elif -th_h < v <= -th_m:
                        return "🟢🟢"
                    else:
                        return "🟢🟢🟢"

                disp_s3.insert(0, "Pos", range(1, len(disp_s3) + 1))
                disp_s3["Rating"] = disp_s3["Variance"].apply(dots_s3)
                disp_s3["Avg Team Actions"] = disp_s3["Avg Team Actions"].map(
                    "{:.2f}".format
                )
                disp_s3[base_label_s3] = disp_s3[base_label_s3].map(
                    "{:.2f}".format
                )
                disp_s3["Variance"] = disp_s3["Variance"].map("{:+.2f}".format)
                disp_s3["Team Actions"] = disp_s3["Team Actions"].astype(int)

                final_cols_s3 = [
                    "Pos", "Team Name", "Rating", "Ref Matches", "W-D-L",
                    "Win %", "Team Actions", "All Team Matches",
                    "Avg Team Actions", base_label_s3, "Variance",
                ]
                disp_s3 = disp_s3[final_cols_s3]

                html_s3 = disp_s3.to_html(
                    index=False,
                    classes="custom-ref-table",
                    border=0,
                    escape=False,
                )
                st.markdown(html_s3, unsafe_allow_html=True)
except Exception as e:
    st.error(f"Referee League Table Error: {e}")

# SECTION 4: GLOBAL REFEREE-TEAM OUTLIER SCANNER
st.markdown(
    """
<div style="background-color: #f0f4fc; border-left: 6px solid #1155cc; padding: 12px 16px; margin: 40px 0 15px 0; border-radius: 4px;">
    <h2 style="margin: 0; color: #1155cc; font-size: 22px;">4. Global Referee-Team Outlier Scanner</h2>
</div>
""",
    unsafe_allow_html=True,
)
st.markdown(
    "Identify the biggest statistical officiating anomalies across every"
    " referee and club pairing within your active sidebar filters."
)
try:
    s4_metric_opts = ["Yellow Cards", "Red Cards", "Fouls"]
    metric_choice_s4 = st.selectbox(
        "Select Offence Metric:",
        s4_metric_opts,
        index=get_idx(s4_metric_opts, ui_state.get("s4_metric")),
        key="s4_metric_select",
    )

    s4_base_opts = [
        "Double-Adjusted (Team + Ref)",
        "Team Average Only",
        "Referee Average Only (Original)",
    ]
    baseline_mode_s4 = st.selectbox(
        "Baseline Comparison Mode:",
        s4_base_opts,
        index=get_idx(s4_base_opts, ui_state.get("s4_baseline")),
        key="s4_baseline_select",
    )

    save_ui_state({
        "s4_metric": metric_choice_s4,
        "s4_baseline": baseline_mode_s4,
    })

    # Build lightweight team-level match log respecting Venue Split
    h_s4 = pd.DataFrame({
        "Team": df_filtered["HomeTeam"],
        "Referee": df_filtered["Referee"],
        "T_Fouls": df_filtered["HF"],
        "T_Yellows": df_filtered["HY"],
        "T_Reds": df_filtered["HR"],
        "Outcome": (
            df_filtered["FTR"].map({"H": "W", "D": "D", "A": "L"}).fillna("-")
            if "FTR" in df_filtered.columns
            else "-"
        ),
    })

    a_s4 = pd.DataFrame({
        "Team": df_filtered["AwayTeam"],
        "Referee": df_filtered["Referee"],
        "T_Fouls": df_filtered["AF"],
        "T_Yellows": df_filtered["AY"],
        "T_Reds": df_filtered["AR"],
        "Outcome": (
            df_filtered["FTR"].map({"A": "W", "D": "D", "H": "L"}).fillna("-")
            if "FTR" in df_filtered.columns
            else "-"
        ),
    })


    if venue_filter == "Home Only":
        logs_s4 = h_s4
    elif venue_filter == "Away Only":
        logs_s4 = a_s4
    else:
        logs_s4 = pd.concat([h_s4, a_s4], ignore_index=True)

    logs_s4 = logs_s4[
        ~logs_s4["Referee"].isin(["", "nan"])
        & logs_s4["Referee"].notna()
        & ~logs_s4["Team"].isin(["", "nan"])
        & logs_s4["Team"].notna()
    ]

    if logs_s4.empty:
        st.warning("No match records found for the current sidebar filters.")
    else:
        pair_counts = logs_s4.groupby(["Referee", "Team"]).size()
        max_pair_games = int(pair_counts.max()) if not pair_counts.empty else 5

        if max_pair_games < 5:
            st.warning(
                f"Maximum matches between any referee and team in this filter"
                f" is {max_pair_games}. Expand your Year Window or Leagues to"
                " reach at least 5 matches."
            )
        else:
            saved_s4_min = 5
            if os.path.exists("slider_memory_s4_min.txt"):
                try:
                    with open("slider_memory_s4_min.txt", "r") as f:
                        saved_s4_min = max(5, int(f.read().strip()))
                except Exception:
                    pass
            active_s4_min = min(saved_s4_min, max_pair_games)

            if max_pair_games == 5:
                min_games_s4 = 5
                st.write("**Minimum matches per Referee-Team pairing:** 5 (maximum available in current filter)")
            else:
                min_games_s4 = st.slider(
                    "Minimum matches per Referee-Team pairing (5 upwards):",
                    5,
                    max_pair_games,
                    active_s4_min,
                    key="s4_min_games_slider",
                )
            try:
                with open("slider_memory_s4_min.txt", "w") as f:
                    f.write(str(min_games_s4))
            except Exception:
                pass

            saved_s4_top = 10
            if os.path.exists("slider_memory_s4_top.txt"):
                try:
                    with open("slider_memory_s4_top.txt", "r") as f:
                        saved_s4_top = min(20, max(1, int(f.read().strip())))
                except Exception:
                    pass

            top_n_s4 = st.slider(
                "Number of top outliers to display (Max 20):",
                1,
                20,
                saved_s4_top,
                key="s4_top_n_slider",
            )
            try:
                with open("slider_memory_s4_top.txt", "w") as f:
                    f.write(str(top_n_s4))
            except Exception:
                pass
            if metric_choice_s4 == "Yellow Cards":
                m_col, th_h4, th_m4 = "T_Yellows", 0.60, 0.30
            elif metric_choice_s4 == "Red Cards":
                m_col, th_h4, th_m4 = "T_Reds", 0.15, 0.07
            else:
                m_col, th_h4, th_m4 = "T_Fouls", 3.00, 1.50

            lg_avg_s4 = logs_s4[m_col].mean()

            team_base_s4 = (
                logs_s4.groupby("Team")[m_col]
                .mean()
                .reset_index()
                .rename(columns={m_col: "Team_Avg"})
            )
            ref_base_s4 = (
                logs_s4.groupby("Referee")[m_col]
                .mean()
                .reset_index()
                .rename(columns={m_col: "Ref_Avg"})
            )

            pair_rows = []
            for (ref_n, team_n), grp in logs_s4.groupby(["Referee", "Team"]):
                g_cnt = len(grp)
                if g_cnt < min_games_s4:
                    continue
                w = int((grp["Outcome"] == "W").sum())
                d = int((grp["Outcome"] == "D").sum())
                l = int((grp["Outcome"] == "L").sum())
                wp = (w / g_cnt) * 100.0
                pair_rows.append({
                    "Referee": ref_n,
                    "Team": team_n,
                    "Matches": g_cnt,
                    "WDL": f"{w}-{d}-{l}",
                    "Win_Pct": f"{wp:.0f}%",
                    "Total_Actions": int(grp[m_col].sum()),
                    "Actual_Avg": grp[m_col].mean(),
                })

            if not pair_rows:
                st.warning(
                    f"No Referee-Team pairings have at least {min_games_s4}"
                    " matches under the current filters."
                )
            else:
                df_s4 = (
                    pd.DataFrame(pair_rows)
                    .merge(team_base_s4, on="Team", how="inner")
                    .merge(ref_base_s4, on="Referee", how="inner")
                )

                if baseline_mode_s4 == "Double-Adjusted (Team + Ref)":
                    df_s4["Baseline"] = (
                        df_s4["Team_Avg"] + df_s4["Ref_Avg"] - lg_avg_s4
                    )
                    base_label_s4 = "Expected Baseline"
                elif baseline_mode_s4 == "Team Average Only":
                    df_s4["Baseline"] = df_s4["Team_Avg"]
                    base_label_s4 = "Team Avg"
                else:
                    df_s4["Baseline"] = df_s4["Ref_Avg"]
                    base_label_s4 = "Ref Avg"

                df_s4["Variance"] = df_s4["Actual_Avg"] - df_s4["Baseline"]

                def dots_s4(v):
                    if v >= th_h4:
                        return "🔴🔴🔴"
                    elif th_m4 <= v < th_h4:
                        return "🔴🔴"
                    elif 0.0 < v < th_m4:
                        return "🔴"
                    elif -th_m4 < v <= 0.0:
                        return "🟢"
                    elif -th_h4 < v <= -th_m4:
                        return "🟢🟢"
                    else:
                        return "🟢🟢🟢"

                def format_outlier_table(sub_df):
                    out = sub_df.copy()
                    out.insert(0, "Rank", range(1, len(out) + 1))
                    out["Rating"] = out["Variance"].apply(dots_s4)
                    out["Avg Actions"] = out["Actual_Avg"].map("{:.2f}".format)
                    out[base_label_s4] = out["Baseline"].map("{:.2f}".format)
                    out["Variance"] = out["Variance"].map("{:+.2f}".format)
                    out = out[[
                        "Rank",
                        "Referee",
                        "Team",
                        "Rating",
                        "Matches",
                        "WDL",
                        "Win_Pct",
                        "Total_Actions",
                        "Avg Actions",
                        base_label_s4,
                        "Variance",
                    ]]
                    out.columns = [
                        "Rank",
                        "Referee Name",
                        "Team Name",
                        "Rating",
                        "Matches",
                        "W-D-L",
                        "Win %",
                        "Total Actions",
                        "Avg Actions",
                        base_label_s4,
                        "Variance",
                    ]
                    return out.to_html(
                        index=False,
                        classes="custom-ref-table",
                        border=0,
                        escape=False,
                    )

                unfav_df = df_s4.sort_values(
                    by=["Variance", "Matches"], ascending=[False, False]
                ).head(top_n_s4)
                fav_df = df_s4.sort_values(
                    by=["Variance", "Matches"], ascending=[True, False]
                ).head(top_n_s4)

                st.markdown(
                    f"### Top {len(unfav_df)} Most Unfavourable Outliers"
                    f" (Highest Positive Variance)"
                )
                st.markdown(
                    format_outlier_table(unfav_df), unsafe_allow_html=True
                )

                st.markdown(
                    f"### Top {len(fav_df)} Most Favourable Outliers"
                    f" (Lowest Negative Variance)"
                )
                st.markdown(
                    format_outlier_table(fav_df), unsafe_allow_html=True
                )
except Exception as e:
    st.error(f"Outlier Scanner Error: {e}")

# SECTION 5: REFEREE TRENDS OVER TIME
st.markdown("---")
st.markdown("## Referee Trends Over Time")
st.markdown(
    "Track how a referee's disciplinary averages or club-specific variance"
    " evolve season by season."
)
try:
    raw_refs_s5 = df_filtered["Referee"].unique().tolist()
    s5_referees = sorted(
        [r for r in raw_refs_s5 if r != "" and r.lower() != "nan"]
    )
    if not s5_referees:
        st.warning("No referees found for the current sidebar filters.")
    else:
        default_s5_ref = next(
            (
                c
                for c in ["Andy Madley", "A Madley", "Michael Oliver"]
                if c in s5_referees
            ),
            None,
        )
        selected_ref_s5 = st.selectbox(
            "Choose a Referee to Track:",
            s5_referees,
            index=get_idx(
                s5_referees, ui_state.get("s5_ref"), default_s5_ref
            ),
            key="s5_ref_select",
        )

        s5_mode_opts = [
            "Referee Strictness vs League Average",
            "Referee Treatment of Specific Club (Variance)",
        ]
        trend_mode_s5 = st.selectbox(
            "Select Trend View:",
            s5_mode_opts,
            index=get_idx(s5_mode_opts, ui_state.get("s5_mode")),
            key="s5_mode_select",
        )

        s5_metric_opts = ["Yellow Cards", "Red Cards", "Fouls"]
        metric_choice_s5 = st.selectbox(
            "Select Offence Metric:",
            s5_metric_opts,
            index=get_idx(s5_metric_opts, ui_state.get("s5_metric")),
            key="s5_metric_select",
        )

        selected_team_s5 = None
        if trend_mode_s5 == "Referee Treatment of Specific Club (Variance)":
            selected_team_s5 = st.selectbox(
                "Choose a Club to Compare Against:",
                all_teams,
                index=get_idx(all_teams, ui_state.get("s5_team"), "Arsenal"),
                key="s5_team_select",
            )

        save_ui_state({
            "s5_ref": selected_ref_s5,
            "s5_mode": trend_mode_s5,
            "s5_metric": metric_choice_s5,
            "s5_team": selected_team_s5 or ui_state.get("s5_team", "Arsenal"),
        })
        # Build lightweight team-level match log with Season labels
        df_s5_base = pd.DataFrame({
            "Date": df_filtered["Date"],
            "HomeTeam": df_filtered["HomeTeam"],
            "AwayTeam": df_filtered["AwayTeam"],
            "Referee": df_filtered["Referee"],
            "HF": df_filtered["HF"],
            "AF": df_filtered["AF"],
            "HY": df_filtered["HY"],
            "AY": df_filtered["AY"],
            "HR": df_filtered["HR"],
            "AR": df_filtered["AR"],
        })
        # Derive football season (e.g. Aug 2023 - May 2024 -> 2023/24)
        start_yr = df_s5_base["Date"].dt.year.where(
            df_s5_base["Date"].dt.month >= 7,
            df_s5_base["Date"].dt.year - 1,
        )
        df_s5_base["Season"] = (
            start_yr.astype(str)
            + "/"
            + ((start_yr + 1) % 100).astype(str).str.zfill(2)
        )

        h_s5 = pd.DataFrame({
            "Season": df_s5_base["Season"],
            "Team": df_s5_base["HomeTeam"],
            "Referee": df_s5_base["Referee"],
            "T_Fouls": df_s5_base["HF"],
            "T_Yellows": df_s5_base["HY"],
            "T_Reds": df_s5_base["HR"],
        })
        a_s5 = pd.DataFrame({
            "Season": df_s5_base["Season"],
            "Team": df_s5_base["AwayTeam"],
            "Referee": df_s5_base["Referee"],
            "T_Fouls": df_s5_base["AF"],
            "T_Yellows": df_s5_base["AY"],
            "T_Reds": df_s5_base["AR"],
        })

        if venue_filter == "Home Only":
            logs_s5 = h_s5
        elif venue_filter == "Away Only":
            logs_s5 = a_s5
        else:
            logs_s5 = pd.concat([h_s5, a_s5], ignore_index=True)

        if metric_choice_s5 == "Yellow Cards":
            m_col5 = "T_Yellows"
        elif metric_choice_s5 == "Red Cards":
            m_col5 = "T_Reds"
        else:
            m_col5 = "T_Fouls"

        ref_logs_s5 = logs_s5[logs_s5["Referee"] == selected_ref_s5]

        if ref_logs_s5.empty:
            st.warning(
                f"No records found for {selected_ref_s5} in the selected filter."
            )
        elif trend_mode_s5 == "Referee Strictness vs League Average":
            lg_season = (
                logs_s5.groupby("Season")[m_col5]
                .mean()
                .rename("League Avg (Per Team)")
            )
            ref_season = (
                ref_logs_s5.groupby("Season")
                .agg(
                    Ref_Avg=(m_col5, "mean"),
                    Team_Appearances=(m_col5, "count"),
                )
            )
            ref_season["Matches"] = (
                ref_season["Team_Appearances"]
                if venue_filter != "All Match Venues"
                else (ref_season["Team_Appearances"] / 2).astype(int)
            )
            chart_df = pd.concat(
                [ref_season["Ref_Avg"], lg_season], axis=1, join="inner"
            ).sort_index()
            chart_df.columns = [
                f"{selected_ref_s5} Avg",
                "League Avg (Per Team)",
            ]

            st.line_chart(chart_df)

            summary_s5 = chart_df.copy()
            summary_s5["Matches"] = ref_season["Matches"]
            summary_s5["Diff vs League"] = (
                summary_s5[f"{selected_ref_s5} Avg"]
                - summary_s5["League Avg (Per Team)"]
            )
            summary_s5 = summary_s5.reset_index()
            summary_s5[f"{selected_ref_s5} Avg"] = summary_s5[
                f"{selected_ref_s5} Avg"
            ].map("{:.2f}".format)
            summary_s5["League Avg (Per Team)"] = summary_s5[
                "League Avg (Per Team)"
            ].map("{:.2f}".format)
            summary_s5["Diff vs League"] = summary_s5["Diff vs League"].map(
                "{:+.2f}".format
            )
            st.markdown(
                summary_s5.to_html(
                    index=False, classes="custom-ref-table", border=0
                ),
                unsafe_allow_html=True,
            )
        else:
            club_ref_logs = ref_logs_s5[
                ref_logs_s5["Team"] == selected_team_s5
            ]
            if club_ref_logs.empty:
                st.warning(
                    f"{selected_ref_s5} has not officiated {selected_team_s5}"
                    " in the selected filter."
                )
            else:
                lg_s = logs_s5.groupby("Season")[m_col5].mean()
                ref_s = ref_logs_s5.groupby("Season")[m_col5].mean()
                team_s = (
                    logs_s5[logs_s5["Team"] == selected_team_s5]
                    .groupby("Season")[m_col5]
                    .mean()
                )
                act_s = club_ref_logs.groupby("Season")[m_col5].mean()
                cnt_s = club_ref_logs.groupby("Season")[m_col5].count()

                t_df = pd.DataFrame({
                    "Actual": act_s,
                    "Expected": team_s + ref_s - lg_s,
                    "Matches": cnt_s,
                }).dropna().sort_index()
                t_df["Variance"] = t_df["Actual"] - t_df["Expected"]

                st.line_chart(t_df[["Actual", "Expected"]])

                disp_t = t_df.reset_index()
                disp_t["Actual"] = disp_t["Actual"].map("{:.2f}".format)
                disp_t["Expected"] = disp_t["Expected"].map("{:.2f}".format)
                disp_t["Variance"] = disp_t["Variance"].map("{:+.2f}".format)
                st.markdown(
                    disp_t.to_html(
                        index=False, classes="custom-ref-table", border=0
                    ),
                    unsafe_allow_html=True,
                )
except Exception as e:
    st.error(f"Trend Visualisation Error: {e}")
