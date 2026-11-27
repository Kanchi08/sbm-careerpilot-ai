import datetime as dt
import pandas as pd
import streamlit as st
import gspread

SHEET_ID = "1_jqflsEBUuIM8RXXshMpo7ys6UDA4VVCW1ltTi4Rh0k"

st.set_page_config(page_title="SBM CareerPilot AI", page_icon="🧭", layout="wide")

st.markdown("""
<style>
[data-testid="stToolbar"], footer, [data-testid="stDecoration"] {display:none !important;}
.block-container {padding-top: 1.2rem; max-width: 1100px;}
.hero {background: linear-gradient(120deg,#1F3864 0%,#0F766E 100%); color:#fff; padding:22px 26px; border-radius:14px; margin-bottom:14px;}
.hero h1 {margin:0; font-size:28px; color:#fff;}
.hero p {margin:4px 0 0 0; opacity:.9; font-size:14px;}
.badge {display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; font-weight:600; margin-right:6px;}
.b-top {background:#DCFCE7; color:#166534;} .b-good {background:#DBEAFE; color:#1E40AF;} .b-stretch {background:#F1F5F9; color:#475569;}
.b-crit {background:#FEE2E2; color:#991B1B;} .b-soon {background:#FEF3C7; color:#92400E;} .b-type {background:#EDE9FE; color:#5B21B6;}
.score {font-size:30px; font-weight:700; line-height:1; text-align:right;}
.score small {font-size:12px; font-weight:500; color:#64748B;}
.meta {color:#475569; font-size:13px;}
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def get_sheet():
    try:
        gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    except Exception:
        gc = gspread.service_account(filename="secrets/service_account.json")
    return gc.open_by_key(SHEET_ID)


def to_df(rows):
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows[1:], columns=rows[0])
    return df


@st.cache_data(ttl=60, show_spinner="Loading live data...")
def load_all():
    sh = get_sheet()
    students = to_df(sh.worksheet("Students").get_all_values())
    opps = to_df(sh.worksheet("Opportunities").get_all_values())
    match = to_df(sh.worksheet("Opportunity Match").get("A1:M12001"))
    apps = to_df(sh.worksheet("Applications").get("A1:E2000"))
    match = match[match["StudentID"].astype(str).str.strip() != ""].copy()
    for c in ["SkillMatch", "InterestMatch", "EligibilityScore", "RelevanceScore", "DaysToDeadline"]:
        match[c] = pd.to_numeric(match[c], errors="coerce")
    apps = apps[apps["ApplicationID"].astype(str).str.strip() != ""].copy()
    return students, opps, match, apps


def submit_application(student_id, opp_id):
    ws = get_sheet().worksheet("Applications")
    col = ws.col_values(1)
    nums = [int(x[1:]) for x in col[1:] if x[1:].isdigit()]
    new_id = f"A{(max(nums) if nums else 0) + 1:04d}"
    row = len(col) + 1
    ws.update(values=[[new_id, student_id, opp_id, dt.date.today().isoformat(), "Applied"]],
              range_name=f"A{row}:E{row}", value_input_option="USER_ENTERED")
    return new_id


students, opps, match, apps = load_all()

# ---------------- Sidebar ----------------
with st.sidebar:
    st.markdown("### 🧭 SBM CareerPilot AI")
    st.caption("Career readiness and opportunity management for SBM students")
    labels = {f"{r.Name} ({r.StudentID})": r.StudentID for r in students.itertuples()}
    who = st.selectbox("Demo sign-in: choose a student", list(labels.keys()))
    sid = labels[who]
    st.caption("Production version: institutional single sign-on. Demo mode lets you switch student.")
    if st.button("Refresh live data"):
        st.cache_data.clear()
        st.rerun()

me = students[students["StudentID"] == sid].iloc[0]
my_apps = set(apps.loc[apps["StudentID"] == sid, "OpportunityID"])

st.markdown(f"""<div class="hero"><h1>Opportunity Feed</h1>
<p>Welcome, {me['Name'].split()[0]}. Every live opportunity is shown to you with your personal relevance score.</p></div>""",
            unsafe_allow_html=True)

mine = match[match["StudentID"] == sid].copy()
cols_needed = ["OpportunityID", "OpportunityType", "Domain", "Deadline", "SourceName", "JD_Summary", "JD_Text", "JD_Link", "ApplyLink"]
mine = mine.merge(opps[cols_needed], on="OpportunityID", how="left")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Live opportunities", len(mine))
k2.metric("Top Matches for you", int((mine["MatchLabel"] == "Top Match").sum()))
k3.metric("Closing within 7 days", int((mine["DaysToDeadline"] <= 7).sum()))
k4.metric("Your applications", len(my_apps))

# ---------------- Filters ----------------
f1, f2, f3, f4 = st.columns([2, 2, 2, 2])
q = f1.text_input("Search", placeholder="Company, role or skill")
types = f2.multiselect("Type", sorted(mine["OpportunityType"].dropna().unique()))
labs = f3.multiselect("Relevance", ["Top Match", "Good Match", "Stretch"])
sort = f4.selectbox("Sort by", ["Best match", "Closing soonest"])
hide = st.checkbox("Hide opportunities I already applied to")

view = mine
if q:
    ql = q.lower()
    view = view[view["OpportunityName"].str.lower().str.contains(ql) | view["Company"].str.lower().str.contains(ql)
                | view["Domain"].str.lower().str.contains(ql)]
if types:
    view = view[view["OpportunityType"].isin(types)]
if labs:
    view = view[view["MatchLabel"].isin(labs)]
if hide:
    view = view[~view["OpportunityID"].isin(my_apps)]
if sort == "Best match":
    view = view.sort_values(["RelevanceScore", "DaysToDeadline"], ascending=[False, True])
else:
    view = view.sort_values(["DaysToDeadline", "RelevanceScore"], ascending=[True, False])

st.caption(f"Showing {len(view)} of {len(mine)} live opportunities")
if "limit" not in st.session_state:
    st.session_state.limit = 12

LAB = {"Top Match": "b-top", "Good Match": "b-good", "Stretch": "b-stretch"}
for r in view.head(st.session_state.limit).itertuples():
    with st.container(border=True):
        left, right = st.columns([5, 1.4])
        d = r.DaysToDeadline
        urg = ""
        if pd.notna(d) and d <= 2:
            urg = '<span class="badge b-crit">Closes in %d day%s</span>' % (d, "" if d == 1 else "s")
        elif pd.notna(d) and d <= 7:
            urg = '<span class="badge b-soon">Closes in %d days</span>' % d
        left.markdown(f"**{r.OpportunityName}**")
        left.markdown(
            f'<span class="badge b-type">{r.OpportunityType}</span>{urg}'
            f'<div class="meta">{r.Company} · {r.Domain} · Deadline {r.Deadline} · via {r.SourceName}</div>',
            unsafe_allow_html=True)
        right.markdown(
            f'<div class="score">{r.RelevanceScore:.0f}<small> / 100</small></div>'
            f'<div style="text-align:right"><span class="badge {LAB.get(r.MatchLabel, "b-stretch")}">{r.MatchLabel}</span></div>',
            unsafe_allow_html=True)
        st.caption(f"Why this score: {r.MatchReason}")
        if r.JD_Summary:
            st.write(r.JD_Summary)
        with st.expander("Read full job description"):
            st.text(r.JD_Text if r.JD_Text else "No JD attached yet.")
            if r.JD_Link:
                st.link_button("Open JD file", r.JD_Link)
        b1, b2, _ = st.columns([1.3, 1.3, 4])
        if r.OpportunityID in my_apps:
            b1.success("Applied")
        elif b1.button("Apply in CareerPilot", key=f"apply_{r.OpportunityID}", type="primary"):
            new_id = submit_application(sid, r.OpportunityID)
            st.cache_data.clear()
            st.toast(f"Application {new_id} recorded")
            st.rerun()
        if r.ApplyLink:
            b2.link_button("Company page", r.ApplyLink)

if len(view) > st.session_state.limit:
    if st.button("Show more"):
        st.session_state.limit += 12
        st.rerun()
