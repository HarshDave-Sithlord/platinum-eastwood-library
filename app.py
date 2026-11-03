"""Streamlit UI for the building library: dashboard, issue, return, catalogue, members, history."""
import hmac
import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import db

st.set_page_config(page_title="Platinum Eastwoods Library", page_icon="📚", layout="wide")


def secret(name):
    try:
        value = st.secrets.get(name)
    except FileNotFoundError:  # no secrets.toml when running locally
        value = None
    return value or os.environ.get(name)


def require_passcode():
    expected = secret("APP_PASSCODE")
    if not expected or st.session_state.get("unlocked"):
        return
    st.title("📚 Platinum Eastwoods Library")
    with st.form("unlock"):
        code = st.text_input("Library passcode", type="password")
        if st.form_submit_button("Enter", type="primary"):
            if hmac.compare_digest(code.encode(), str(expected).encode()):
                st.session_state["unlocked"] = True
                st.rerun()
            st.error("Wrong passcode.")
    st.stop()


@st.cache_resource
def _init_database(url):
    db.init_db(url)
    return True


DATABASE_URL = secret("DATABASE_URL")
if DATABASE_URL:
    os.environ["DATABASE_URL"] = DATABASE_URL
require_passcode()
_init_database(DATABASE_URL)


def flash(message, kind="success"):
    # Survives the st.rerun() that refreshes lists after a write.
    st.session_state["flash"] = (kind, message)


def show_flash():
    if "flash" in st.session_state:
        kind, message = st.session_state.pop("flash")
        getattr(st, kind)(message)


def fmt(day):
    return day.strftime("%d %b %Y") if day else "—"


def table(rows, columns):
    """Render rows (list of dicts) with the given {key: header} columns."""
    frame = pd.DataFrame(rows, columns=list(columns)).rename(columns=columns)
    st.dataframe(frame, hide_index=True)


def page_dashboard():
    st.title("📚 Platinum Eastwoods Library")
    s = db.stats()
    cols = st.columns(5)
    cols[0].metric("Total books", s["total"], border=True)
    cols[1].metric("Available", s["available"], border=True)
    cols[2].metric("Checked out", s["borrowed"], border=True)
    cols[3].metric("Overdue", s["overdue"], border=True)
    cols[4].metric("Member flats", s["members"], border=True)

    st.subheader("🔎 Find a book")
    query = st.text_input("Search by title, author or genre", placeholder="e.g. Narayan")
    books = db.list_books(search=query)
    if books:
        table(books, {"title": "Title", "author": "Author", "genre": "Genre", "status": "Status"})
    elif query:
        st.info("No books match that search.")
    else:
        st.info("The catalogue is empty. Add books on the **Catalogue** page.")

    st.subheader("⏰ Overdue books")
    overdue = db.overdue_loans()
    if not overdue:
        st.success("No overdue books. 🎉")
        return
    for loan in overdue:
        loan["due"] = fmt(loan["due_date"])
    table(overdue, {"flat_number": "Flat", "resident_name": "Resident", "phone_number": "Phone",
                    "title": "Book", "due": "Due", "days_overdue": "Days overdue"})


def page_issue():
    st.title("📤 Issue a book")
    show_flash()
    available = db.list_books(status="Available")
    members = db.list_members()
    if not members:
        st.warning("Register a flat on the **Members** page first.")
        return
    if not available:
        if db.stats()["total"] == 0:
            st.warning("Add books on the **Catalogue** page first.")
        else:
            st.info("Every book is currently checked out.")
        return

    with st.form("issue_form"):
        book = st.selectbox("Book", available,
                            format_func=lambda b: f"{b['title']} — {b['author']}")
        member = st.selectbox("Flat", members,
                              format_func=lambda m: f"{m['flat_number']} · {m['resident_name']}")
        loan_days = st.number_input("Loan period (days)", min_value=1, max_value=60,
                                    value=db.DEFAULT_LOAN_DAYS)
        if st.form_submit_button("Issue Book", type="primary"):
            try:
                db.issue_book(book["book_id"], member["flat_number"], loan_days)
            except db.LibraryError as err:
                st.error(str(err))
            else:
                due = date.today() + timedelta(days=int(loan_days))
                flash(f"Issued **{book['title']}** to flat **{member['flat_number']}**. "
                      f"Due back on **{fmt(due)}**.")
                st.rerun()


def page_return():
    st.title("📥 Return a book")
    show_flash()
    loans = db.active_loans()
    if not loans:
        st.info("No books are currently borrowed.")
        return

    query = st.text_input("Filter by flat or title", placeholder="e.g. A-101").strip().lower()
    if query:
        loans = [l for l in loans if query in l["flat_number"].lower() or query in l["title"].lower()]

    widths = [3, 2, 1.3, 1.3, 1.6, 1.6]
    header = st.columns(widths)
    for col, label in zip(header, ["Book", "Flat", "Issued", "Due", "Status", ""]):
        col.markdown(f"**{label}**")
    for loan in loans:
        c = st.columns(widths, vertical_alignment="center")
        c[0].write(loan["title"])
        c[1].write(f"{loan['flat_number']} · {loan['resident_name']}")
        c[2].write(fmt(loan["issue_date"]))
        c[3].write(fmt(loan["due_date"]))
        if loan["days_overdue"]:
            c[4].markdown(f":red[**Overdue {loan['days_overdue']} d**]")
        else:
            c[4].markdown(":green[On time]")
        if c[5].button("Mark as Returned", key=f"return_{loan['log_id']}"):
            try:
                db.return_book(loan["log_id"])
            except db.LibraryError as err:
                st.error(str(err))
            else:
                flash(f"**{loan['title']}** returned by flat **{loan['flat_number']}**.")
                st.rerun()


def page_catalogue():
    st.title("📖 Catalogue")
    show_flash()
    with st.form("add_book", clear_on_submit=True):
        st.markdown("**Add a book**")
        c = st.columns(3)
        title = c[0].text_input("Title")
        author = c[1].text_input("Author")
        genre = c[2].text_input("Genre")
        if st.form_submit_button("Add book", type="primary"):
            try:
                db.add_book(title, author, genre)
            except db.LibraryError as err:
                st.error(str(err))
            else:
                flash(f"Added **{title.strip()}** to the catalogue.")
                st.rerun()
    table(db.list_books(), {"book_id": "ID", "title": "Title", "author": "Author",
                            "genre": "Genre", "status": "Status"})


def page_members():
    st.title("🏠 Members")
    show_flash()
    with st.form("add_member", clear_on_submit=True):
        st.markdown("**Register a flat**")
        c = st.columns(3)
        flat = c[0].text_input("Flat number", placeholder="A-101")
        name = c[1].text_input("Resident name")
        phone = c[2].text_input("Phone number")
        if st.form_submit_button("Register", type="primary"):
            try:
                db.add_member(flat, name, phone)
            except db.LibraryError as err:
                st.error(str(err))
            else:
                flash(f"Registered flat **{flat.strip().upper()}**.")
                st.rerun()
    table(db.list_members(), {"flat_number": "Flat", "resident_name": "Resident",
                              "phone_number": "Phone"})


def page_history():
    st.title("🗂️ Transaction log")
    loans = db.loan_history()
    if not loans:
        st.info("No books have been issued yet.")
        return
    for loan in loans:
        loan["issued"], loan["due"], loan["returned"] = (
            fmt(loan["issue_date"]), fmt(loan["due_date"]), fmt(loan["return_date"]))
    table(loans, {"log_id": "Log ID", "title": "Book", "flat_number": "Flat",
                  "resident_name": "Resident", "issued": "Issued", "due": "Due",
                  "returned": "Returned"})


PAGES = {
    "Dashboard": page_dashboard,
    "Issue Book": page_issue,
    "Return Book": page_return,
    "Catalogue": page_catalogue,
    "Members": page_members,
    "History": page_history,
}

choice = st.sidebar.radio("Menu", list(PAGES), key="page")
st.sidebar.caption(f"Today: {date.today().strftime('%d %b %Y')}")
PAGES[choice]()
