import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest

import db

BACKENDS = ["sqlite"] + (["postgres"] if os.environ.get("TEST_DATABASE_URL") else [])


@pytest.fixture(params=BACKENDS)
def url(request, tmp_path):
    if request.param == "sqlite":
        u = f"sqlite:///{tmp_path / 'test.db'}"
    else:
        u = os.environ["TEST_DATABASE_URL"]
        db.metadata.drop_all(db.get_engine(u))
    db.init_db(u)
    db.add_member("A-101", "Meera", url=u)
    return u


def test_new_database_is_empty(tmp_path):
    u = f"sqlite:///{tmp_path / 'fresh.db'}"
    db.init_db(u)
    db.init_db(u)
    assert db.list_books(url=u) == [] and db.list_members(url=u) == []
    assert db.stats(url=u) == {"total": 0, "borrowed": 0, "available": 0, "members": 0, "overdue": 0}


def test_issue_marks_book_borrowed_and_logs_due_date(url):
    book = db.add_book("Sapiens", "Harari", url=url)
    today = date(2026, 11, 3)
    db.issue_book(book, "A-101", loan_days=14, today=today, url=url)

    assert db.list_books(url=url)[0]["status"] == "Borrowed"
    loan = db.active_loans(today=today, url=url)[0]
    assert loan["due_date"] == date(2026, 11, 17) and loan["days_overdue"] == 0


def test_cannot_issue_borrowed_book_or_to_unknown_flat(url):
    book = db.add_book("Sapiens", url=url)
    db.issue_book(book, "A-101", url=url)
    with pytest.raises(db.LibraryError, match="not available"):
        db.issue_book(book, "A-101", url=url)

    other = db.add_book("Ikigai", url=url)
    with pytest.raises(db.LibraryError, match="not a registered member"):
        db.issue_book(other, "Z-999", url=url)
    assert db.list_books(status="Available", url=url)[0]["title"] == "Ikigai"
    assert len(db.active_loans(url=url)) == 1


def test_concurrent_issue_of_same_copy_succeeds_once(url):
    book = db.add_book("Sapiens", url=url)

    def attempt(_):
        try:
            db.issue_book(book, "A-101", url=url)
            return True
        except db.LibraryError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(8))) == 1
    assert len(db.active_loans(url=url)) == 1


def test_return_frees_book_and_cannot_return_twice(url):
    book = db.add_book("Sapiens", url=url)
    log_id = db.issue_book(book, "A-101", url=url)
    db.return_book(log_id, url=url)

    assert db.list_books(url=url)[0]["status"] == "Available"
    assert db.active_loans(url=url) == []
    assert db.loan_history(url=url)[0]["return_date"] == date.today()
    with pytest.raises(db.LibraryError, match="already been returned"):
        db.return_book(log_id, url=url)


def test_overdue_and_stats(url):
    late = db.add_book("Late", url=url)
    fine = db.add_book("Fine", url=url)
    db.add_book("Shelf", url=url)
    today = date(2026, 11, 3)
    db.issue_book(late, "A-101", loan_days=14, today=today - timedelta(days=20), url=url)
    db.issue_book(fine, "A-101", loan_days=14, today=today - timedelta(days=3), url=url)

    overdue = db.overdue_loans(today=today, url=url)
    assert [l["title"] for l in overdue] == ["Late"] and overdue[0]["days_overdue"] == 6
    assert db.stats(today=today, url=url) == {
        "total": 3, "borrowed": 2, "available": 1, "members": 1, "overdue": 1}


def test_search_is_case_insensitive_and_validates_input(url):
    db.add_book("Malgudi Days", "R. K. Narayan", "Short Stories", url=url)
    db.add_book("Sapiens", "Harari", "History", url=url)
    db.add_book("100% Pure", url=url)
    assert [b["title"] for b in db.list_books("NARAYAN", url=url)] == ["Malgudi Days"]
    assert [b["title"] for b in db.list_books("history", url=url)] == ["Sapiens"]
    assert [b["title"] for b in db.list_books("%", url=url)] == ["100% Pure"]

    with pytest.raises(db.LibraryError):
        db.add_book("   ", url=url)
    with pytest.raises(db.LibraryError, match="already registered"):
        db.add_member("a-101", "Someone", url=url)
