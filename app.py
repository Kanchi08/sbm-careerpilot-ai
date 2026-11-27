"""SBM CareerPilot AI - Streamlit front end.

Reads and writes the CareerPilot Google Sheet. Only input columns are written;
the sheet's formulas calculate everything else.
"""
import hmac
import re
from datetime import datetime, timedelta, timezone

import gspread
import pandas as pd
import streamlit as st

st.set_page_config(page_title="SBM CareerPilot AI", layout="wide")


def require_sign_in():
    """Access codes from the app's secrets decide the role: student or admin."""
    if st.session_state.get("role"):
        return st.session_state["role"]
    st.title("SBM CareerPilot AI")
    missing = [k for k in ("student_code", "admin_code", "sheet_id", "gcp_service_account") if k not in st.secrets]
    if missing:
        st.error(f"Missing in Secrets: {', '.join(missing)}")
        st.stop()
    with st.form("sign_in"):
        code = st.text_input("Access code", type="password")
        sent = st.form_submit_button("Sign in")
    if sent:
        for role, key in (("Admin", "admin_code"), ("Student", "student_code")):
            if hmac.compare_digest(code.encode(), str(st.secrets[key]).encode()):
                st.session_state["role"] = role
                st.rerun()
        st.error("Incorrect access code.")
    st.stop()


@st.cache_resource
def spreadsheet():
    client = gspread.service_account_from_dict(
        dict(st.secrets["gcp_service_account"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return client.open_by_key(st.secrets["sheet_id"])


@st.cache_data(ttl=60, show_spinner="Loading data...")
def load(tab, required):
    """Return a tab as text columns, keeping rows where the first required column is filled."""
    values = spreadsheet().worksheet(tab).get_all_values()
    header = [h.strip() for h in values[0]] if values else []
    missing = [c for c in required if c not in header]
    if missing:
        st.error(f"Tab '{tab}' is missing column(s): {', '.join(missing)}")
        st.stop()
    df = pd.DataFrame(values[1:], columns=header)
    df = df.loc[:, ~df.columns.duplicated()]
    return df[df[required[0]].str.strip() != ""]


IST = timezone(timedelta(hours=5, minutes=30))


def now():
    return datetime.now(IST)


def safe(value):
    """Stop typed or AI-extracted text from being run as a spreadsheet formula."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def fresh(tab):
    """Uncached read used before writing: header, and rows as dicts with row numbers."""
    values = spreadsheet().worksheet(tab).get_all_values()
    header = [h.strip() for h in values[0]]
    rows = []
    for number_, cells in enumerate(values[1:], start=2):
        if cells and cells[0].strip():
            row = dict(zip(header, cells))
            row["_row"] = number_
            rows.append(row)
    return header, rows


def write(tab, header, row_number, data):
    """Write only the named input cells of one row."""
    missing = [c for c in data if c not in header]
    if missing:
        st.error(f"Tab '{tab}' is missing column(s): {', '.join(missing)}")
        st.stop()
    updates = [
        {
            "range": gspread.utils.rowcol_to_a1(row_number, header.index(c) + 1),
            "values": [[safe(v)]],
        }
        for c, v in data.items()
    ]
    try:
        spreadsheet().worksheet(tab).batch_update(updates, value_input_option="USER_ENTERED")
    except gspread.exceptions.APIError as error:
        st.error(
            "The sheet refused the change. Check that the service account in "
            f"secrets.toml is shared on the sheet as Editor. Details: {error}"
        )
        st.stop()
    st.cache_data.clear()


def next_row(rows):
    return max([r["_row"] for r in rows], default=1) + 1


def next_id(rows, column, prefix, width):
    used = [int(re.sub(r"\D", "", r[column]) or 0) for r in rows]
    return f"{prefix}{max(used, default=0) + 1:0{width}d}"


def done(message):
    st.session_state["flash"] = message
    st.rerun()


def pick_student():
    students = load("Students", ("StudentID", "Name"))
    names = dict(zip(students["StudentID"], students["Name"]))
    return st.sidebar.selectbox(
        "Student", list(names), format_func=lambda s: f"{names[s]} ({s})", key="student"
    )


def record_application(student, opportunity):
    header, rows = fresh("Applications")
    if any(r["StudentID"] == student and r["OpportunityID"] == opportunity for r in rows):
        done("This application is already recorded.")
    new_id = next_id(rows, "ApplicationID", "A", 4)
    write("Applications", header, next_row(rows), {
        "ApplicationID": new_id,
        "StudentID": student,
        "OpportunityID": opportunity,
        "AppliedDate": now().strftime("%Y-%m-%d"),
        "Status": "Applied",
    })
    done(f"Application {new_id} recorded.")


def number(series):
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce")


def opportunity_card(row, extra="", student=None, applied=False):
    with st.container(border=True):
        st.subheader(row.get("OpportunityName", ""))
        facts = [row.get(c, "") for c in ("Company", "OpportunityType", "Domain", "Status")]
        st.caption(" | ".join(f for f in facts if f))
        if extra:
            st.write(extra)
        skills = [row.get(c, "") for c in ("RequiredSkill1", "RequiredSkill2")]
        st.write(
            f"Deadline: {row.get('Deadline') or 'Not stated'}  \n"
            f"Skills: {', '.join(s for s in skills if s) or 'Not stated'}  \n"
            f"Minimum CGPA: {row.get('MinCGPA') or 'Not stated'}"
        )
        if row.get("VerificationStatus") == "Auto-Published":
            st.success(
                f"Captured automatically from {row.get('SourceName') or 'the inbox'} "
                f"(AI confidence {row.get('AIConfidence') or 'n/a'})"
            )
        if row.get("JD_Summary"):
            with st.expander("Summary"):
                st.text(row["JD_Summary"])
        link = row.get("ApplyLink", "").strip()
        if link.startswith(("https://", "http://")):
            st.link_button("Open application page", link)
        if row.get("JD_Text"):
            with st.expander("Full description"):
                st.text(row["JD_Text"])
        if applied:
            st.caption("Applied. See My Applications.")
        elif student and row.get("Status") != "Closed":
            if st.button("Mark as applied", key="apply" + row["OpportunityID"]):
                record_application(student, row["OpportunityID"])


def feed_page(opps, student):
    st.header("Opportunity Feed")
    order = st.radio("Sort by", ["Newest", "Best match for me"], horizontal=True)
    c1, c2, c3 = st.columns(3)
    search = c1.text_input("Search name or company")
    types = c2.multiselect("Type", sorted(set(opps["OpportunityType"]) - {""}))
    domains = c3.multiselect("Domain", sorted(set(opps["Domain"]) - {""}))
    show_closed = st.checkbox("Include closed opportunities")

    matches = load(
        "Opportunity Match",
        ("StudentID", "OpportunityID", "RelevanceScore", "MatchLabel", "MatchReason"),
    )
    mine = matches[matches["StudentID"] == student].drop_duplicates("OpportunityID")
    df = opps.merge(
        mine[["OpportunityID", "RelevanceScore", "MatchLabel", "MatchReason"]],
        on="OpportunityID", how="left",
    ).fillna("")

    if not show_closed:
        df = df[df["Status"] != "Closed"]
    if search:
        text = df["OpportunityName"] + " " + df["Company"]
        df = df[text.str.contains(search, case=False, regex=False)]
    if types:
        df = df[df["OpportunityType"].isin(types)]
    if domains:
        df = df[df["Domain"].isin(domains)]

    if order == "Newest":
        keys = [c for c in ("IngestedAt", "PostedDate") if c in df.columns]
        df = df.sort_values(
            keys, ascending=False, key=lambda s: pd.to_datetime(s, errors="coerce", format="mixed")
        )
    else:
        df = df.sort_values("RelevanceScore", ascending=False, key=number)

    apps = load("Applications", ("ApplicationID", "StudentID", "OpportunityID", "Status"))
    applied = set(apps[apps["StudentID"] == student]["OpportunityID"])
    st.write(f"{len(df)} opportunities")
    if len(df) > 15:
        st.caption("Showing the first 15. Use the filters to narrow the list.")
    for _, row in df.head(15).iterrows():
        r = row.to_dict()
        why = ""
        if r["RelevanceScore"]:
            why = f"**{r['MatchLabel']} ({r['RelevanceScore']}/100)**: {r['MatchReason']}"
        opportunity_card(r, extra=why, student=student, applied=r["OpportunityID"] in applied)


def tracker_page(opps, student):
    st.header("My Applications")
    apps = load("Applications", ("ApplicationID", "StudentID", "OpportunityID", "Status"))
    mine = apps[apps["StudentID"] == student]

    c1, c2, c3 = st.columns(3)
    c1.metric("Applications", len(mine))
    c2.metric("Active", int((mine.get("IsActive", pd.Series(dtype=str)) == "1").sum()))
    c3.metric("Shortlisted or better", int((mine.get("IsPositive", pd.Series(dtype=str)) == "1").sum()))

    columns = ["ApplicationID", "OpportunityName", "Company", "AppliedDate", "Deadline", "Status"]
    st.dataframe(mine[[c for c in columns if c in mine.columns]], hide_index=True)

    st.caption("To add an application, use Mark as applied in the Opportunity Feed.")

    if not mine.empty:
        st.subheader("Update a status")
        names = dict(zip(mine["ApplicationID"], mine["OpportunityName"]))
        app_id = st.selectbox("Application", list(names), format_func=lambda a: f"{a}: {names[a]}")
        status = st.selectbox(
            "New status",
            ["Applied", "Under Review", "Shortlisted", "Interview", "Selected", "Rejected", "Withdrawn"],
        )
        if st.button("Update status"):
            header, rows = fresh("Applications")
            row = next(r for r in rows if r["ApplicationID"] == app_id)
            write("Applications", header, row["_row"], {"Status": status})
            done(f"{app_id} is now {status}.")


def one_row(tab, student):
    df = load(tab, ("StudentID",))
    found = df[df["StudentID"] == student]
    return found.iloc[0].to_dict() if len(found) else {}


def readiness_page(opps, student):
    st.header("My Readiness")
    ready = one_row("Career Readiness", student)
    resume = one_row("Resume Analysis", student)
    gap = one_row("Skill Gap Analysis", student)

    if ready:
        c1, c2, c3 = st.columns(3)
        c1.metric("Readiness score", ready.get("ReadinessScore", ""))
        c2.metric("Band", ready.get("ReadinessBand", ""))
        c3.metric("Cohort rank", ready.get("CohortRank", ""))
        st.write(f"Main development area: **{ready.get('PrimaryDevelopmentArea', '')}**")
        parts = ["ResumeScore", "SkillReadiness", "AcademicScore", "ExperienceScore", "EngagementScore"]
        for part in parts:
            value = pd.to_numeric(ready.get(part), errors="coerce")
            if pd.notna(value):
                st.progress(min(max(value / 100, 0.0), 1.0), text=f"{part}: {value:g}")
    else:
        st.info("No readiness record for this student.")

    st.subheader("Resume")
    if resume:
        st.write(
            f"Overall **{resume.get('OverallScore', '')}** (grade {resume.get('Grade', '')}). "
            f"Weakest area: **{resume.get('WeakestArea', '')}**."
        )
        st.info(resume.get("FeedbackSummary") or "No feedback yet.")
    else:
        st.info("No resume analysis for this student.")

    st.subheader("Skill gap")
    if gap:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Skill": gap.get(f"Skill{i}"), "Your level": gap.get(f"Level{i}"),
                        "Required": gap.get(f"Req{i}"), "Gap": gap.get(f"Gap{i}"),
                    }
                    for i in (1, 2, 3)
                ]
            ),
            hide_index=True,
        )
        st.write(f"Severity: **{gap.get('GapSeverity', '')}**")
        st.success(gap.get("SuggestedAction") or "")
    st.divider()
    coach_section(student)


def coach_section(student):
    st.subheader("AI Career Coach")
    st.caption("AI answers can be wrong. Check important advice with the placement cell.")
    with st.form("ask", clear_on_submit=True):
        kind = st.selectbox("Topic", ["Coach", "Resume", "Skill Gap"])
        question = st.text_area("Your question", max_chars=500)
        sent = st.form_submit_button("Ask")
    if sent and question.strip():
        header, rows = fresh("Requests")
        if any(r["StudentID"] == student and r["Status"] == "Pending" for r in rows):
            st.warning("Please wait for your pending question to be answered.")
        else:
            write("Requests", header, next_row(rows), {
                "RequestID": next_id(rows, "RequestID", "REQ", 4),
                "StudentID": student,
                "RequestType": kind,
                "Question": question.strip(),
                "Status": "Pending",
                "CreatedAt": now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            done("Question sent. Click Refresh data in about a minute.")

    asked = load("Requests", ("RequestID", "StudentID", "Question", "Status", "Response"))
    for _, row in asked[asked["StudentID"] == student].iloc[::-1].iterrows():
        with st.container(border=True):
            st.write(f"**{row['Question']}**")
            if row["Status"] == "Done":
                st.write(row["Response"])
            else:
                st.caption(f"Status: {row['Status']}")


def add_section(opps):
    st.caption("New entries are checked for duplicates, then wait in the review queue.")
    with st.form("add", clear_on_submit=True):
        name = st.text_input("Opportunity name (start with the company)")
        company = st.text_input("Company")
        kind = st.selectbox("Type", sorted(set(opps["OpportunityType"]) - {""}))
        domain = st.selectbox("Domain", sorted(set(opps["Domain"]) - {""}))
        deadline = st.date_input("Deadline", value=now().date() + timedelta(days=14), min_value=now().date())
        skills = ["Excel", "SQL", "Python", "Data Visualization", "Financial Modelling",
                  "Strategy Frameworks", "Marketing Analytics", "Supply Chain Analytics",
                  "Communication", "Stakeholder Management", "Statistics", "Product Thinking"]
        skill1 = st.selectbox("Required skill 1", skills)
        skill2 = st.selectbox("Required skill 2", skills, index=1)
        cgpa = st.number_input("Minimum CGPA (0 if none)", 0.0, 4.0, 0.0, 0.1)
        link = st.text_input("Apply link")
        summary = st.text_area("Short description", max_chars=1000)
        sent = st.form_submit_button("Submit for review")
    if not sent:
        return
    if not name.strip() or not company.strip():
        st.error("Enter the opportunity name and the company.")
        return
    header, rows = fresh("Ingestion Staging")
    stamp = now()
    staging_id = "ST-" + stamp.strftime("%y%m%d%H%M%S")
    text = summary.strip()
    write("Ingestion Staging", header, next_row(rows), {
        "StagingID": staging_id,
        "ReceivedAt": stamp.strftime("%Y-%m-%d %H:%M:%S"),
        "SourceID": "SRC06",
        "SourceRef": "app-" + staging_id,
        "RawSubject": name.strip(),
        "RawText": text,
        "ExtractedName": name.strip(),
        "ExtractedCompany": company.strip(),
        "ExtractedType": kind,
        "ExtractedDomain": domain,
        "ExtractedDeadline": deadline.isoformat(),
        "ExtractedSkill1": skill1,
        "ExtractedSkill2": skill2,
        "ExtractedMinCGPA": cgpa,
        "ExtractedApplyLink": link.strip(),
        "ExtractedJDSummary": text,
        "ExtractedJDText": text,
        "ProcessedAt": stamp.strftime("%Y-%m-%d %H:%M:%S"),
    })
    _, rows = fresh("Ingestion Staging")
    route = next((r["RouteDecision"] for r in rows if r["StagingID"] == staging_id), "")
    done(f"{staging_id} saved. Route decision: {route or 'being calculated'}.")


def company_key(text):
    text = re.sub(r"[^a-z0-9 ]", " ", str(text).lower())
    text = re.sub(r"\b(and|ltd|limited|pvt|private|india|inc|company|co)\b", " ", text)
    return " ".join(text.split())


def publish(item):
    """Copy an approved staging row into Opportunities and link the two."""
    header, rows = fresh("Opportunities")
    existing = next((r for r in rows if r["SourceRef"] == item["SourceRef"] and item["SourceRef"]), None)
    if existing:
        return existing["OpportunityID"]
    _, recruiters = fresh("Recruiters")
    target = company_key(item["ExtractedCompany"])
    recruiter = next((r["RecruiterID"] for r in recruiters if company_key(r["Company"]) == target), "")
    new_id = next_id(rows, "OpportunityID", "O", 3)
    stamp = now()
    write("Opportunities", header, next_row(rows), {
        "OpportunityID": new_id,
        "OpportunityName": item["ExtractedName"],
        "RecruiterID": recruiter,
        "OpportunityType": item["ExtractedType"],
        "Domain": item["ExtractedDomain"],
        "RequiredSkill1": item["ExtractedSkill1"],
        "RequiredSkill2": item["ExtractedSkill2"],
        "MinCGPA": item["ExtractedMinCGPA"] or 0,
        "PostedDate": stamp.strftime("%Y-%m-%d"),
        "Deadline": item["ExtractedDeadline"],
        "SourceID": item["SourceID"],
        "SourceRef": item["SourceRef"],
        "SourcePostedAt": item["ReceivedAt"],
        "IngestedAt": stamp.strftime("%Y-%m-%d %H:%M:%S"),
        "VerificationStatus": "Verified",
        "AIConfidence": item["AIConfidence"],
        "CompanyRaw": item["ExtractedCompany"],
        "ApplyLink": item["ExtractedApplyLink"],
        "JD_Summary": item["ExtractedJDSummary"],
        "JD_Text": item["ExtractedJDText"],
    })
    return new_id


def review_section():
    header, rows = fresh("Ingestion Staging")
    waiting = [r for r in rows if r["RouteDecision"] in ("Admin Review", "Publish - Admin Approved")]
    if not waiting:
        st.info("Nothing is waiting for review.")
    for item in waiting:
        key = item["StagingID"]
        with st.container(border=True):
            st.subheader(item["ExtractedName"])
            st.write(
                f"{item['ExtractedCompany']} | {item['ExtractedType']} | {item['ExtractedDomain']}  \n"
                f"Deadline: {item['ExtractedDeadline'] or 'Not stated'}  \n"
                f"Source: {item.get('SourceName', '')} | AI confidence: {item['AIConfidence'] or 'n/a'}"
            )
            with st.expander("Original text"):
                st.text(item["RawText"])
            if not item["ExtractedDeadline"]:
                st.warning("Add the deadline in the sheet before approving.")
            c1, c2 = st.columns(2)
            approve = c1.button("Approve and publish", key="a" + key, disabled=not item["ExtractedDeadline"])
            reject = c2.button("Reject", key="r" + key)
            if approve or reject:
                header, latest = fresh("Ingestion Staging")
                current = next((r for r in latest if r["StagingID"] == key), None)
                if not current or current["PublishedOpportunityID"]:
                    done("This item was already handled.")
                if reject:
                    write("Ingestion Staging", header, current["_row"], {"AdminDecision": "Reject"})
                    done(f"{key} rejected.")
                write("Ingestion Staging", header, current["_row"], {"AdminDecision": "Approve"})
                new_id = publish(current)
                write("Ingestion Staging", header, current["_row"], {"PublishedOpportunityID": new_id})
                done(f"Published as {new_id}.")


def admin_page(opps, student):
    st.header("Admin: Review and Add")
    queue, duplicates, add, sources = st.tabs(
        ["Review queue", "Duplicates blocked", "Add opportunity", "Source status"]
    )
    with queue:
        review_section()
    with duplicates:
        _, rows = fresh("Ingestion Staging")
        blocked = [r for r in rows if r["RouteDecision"] == "Discard - Duplicate"]
        st.metric("Duplicates blocked", len(blocked))
        if blocked:
            columns = ["StagingID", "ExtractedName", "ExtractedCompany", "SourceName", "ReceivedAt"]
            st.dataframe(pd.DataFrame(blocked)[columns], hide_index=True)
    with add:
        add_section(opps)
    with sources:
        df = load("Sources", ("SourceID", "SourceName"))
        columns = ["SourceName", "Mode", "Active", "OpportunitiesFromSource", "LiveNow", "AvgAIConfidence"]
        st.dataframe(df[[c for c in columns if c in df.columns]], hide_index=True)
        st.caption("Active shows the setting in the Sources tab, not a live connection test.")


def impact_page(opps, student):
    st.header("Impact Dashboard")
    st.subheader("Measured from the live pipeline")
    _, staging = fresh("Ingestion Staging")
    published = [r for r in staging if r["PublishedOpportunityID"]]
    automatic = [r for r in published if not r["AdminDecision"]]
    blocked = sum(r["RouteDecision"] == "Discard - Duplicate" for r in staging)
    latency = number(opps[opps["VerificationStatus"] == "Auto-Published"]["IngestLatencyMin"]).dropna()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Items received", len(staging))
    c2.metric("Published without admin", f"{len(automatic) / len(staging):.0%}" if staging else "n/a")
    c3.metric("Duplicates blocked", blocked)
    c4.metric("Median minutes to live", f"{latency.median():.1f}" if len(latency) else "n/a")
    st.caption(
        f"Based on {len(staging)} item(s) now in staging and {len(latency)} auto-published "
        "opportunity(ies). Replayed test emails inflate the minutes."
    )

    baseline = pd.to_numeric(spreadsheet().worksheet("Config").acell("B44").value, errors="coerce")
    if len(latency) and pd.notna(baseline):
        st.write(
            f"Manual circulation baseline (assumption, Config B44): **{baseline:g} hours**. "
            f"Automated median: **{latency.median() / 60:.2f} hours**."
        )

    logs = load("Notification Logs", ("NotificationID", "DeliveryStatus"))
    if len(logs):
        sent = logs["DeliveryStatus"].isin(["Sent", "Retried - Sent"]).mean()
        st.write(f"Logged alert delivery: **{sent:.1%}** of {len(logs)} records (includes synthetic history).")

    st.subheader("Opportunities by source")
    st.bar_chart(opps["SourceName"].replace("", "Unknown").value_counts())

    st.subheader("Efficiency model (estimates, not measurements)")
    c1, c2, c3 = st.columns(3)
    volume = c1.number_input("Opportunities per month", 0, 5000, 100)
    minutes = c2.number_input("Manual minutes per opportunity", 0, 240, 15)
    review = c3.number_input("Share needing review (%)", 0, 100, 20)
    cost = c1.number_input("Staff cost per hour (Rs)", 0, 10000, 300)
    review_minutes = c2.number_input("Review minutes per item", 0, 240, 3)
    hours = volume * (minutes - review / 100 * review_minutes) / 60
    c1, c2 = st.columns(2)
    c1.metric("Hours saved per month", f"{hours:,.1f}")
    c2.metric("Staff cost saved per month", f"Rs {hours * cost:,.0f}")
    st.caption("Excludes software, AI usage and maintenance costs. Replace the inputs with your own evidence.")


def main():
    role = require_sign_in()
    st.sidebar.title("CareerPilot AI")
    st.sidebar.caption(f"Signed in as {role}")
    pages = {
        "Opportunity Feed": feed_page,
        "My Applications": tracker_page,
        "My Readiness": readiness_page,
    }
    if role == "Admin":
        pages["Admin: Review and Add"] = admin_page
        pages["Impact Dashboard"] = impact_page
    page = st.sidebar.radio("Go to", list(pages))
    student = pick_student()
    if st.sidebar.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()
    if st.sidebar.button("Sign out"):
        st.session_state.clear()
        st.rerun()
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))

    opps = load(
        "Opportunities",
        (
            "OpportunityID", "OpportunityName", "Company", "OpportunityType",
            "Domain", "Status", "Deadline", "VerificationStatus", "IngestLatencyMin", "SourceName",
        ),
    )
    pages[page](opps, student)


main()
