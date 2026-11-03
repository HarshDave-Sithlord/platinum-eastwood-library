"""Data layer for the building library: books, members and loan transactions.

Uses a local SQLite file by default, or any SQLAlchemy URL (e.g. Supabase
Postgres) when DATABASE_URL is set.
"""
import os
from datetime import date, timedelta

import sqlalchemy as sa

DEFAULT_LOAN_DAYS = 14
_SQLITE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library.db")

metadata = sa.MetaData()

books = sa.Table(
    "books", metadata,
    sa.Column("book_id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("author", sa.Text, nullable=False, server_default=""),
    sa.Column("genre", sa.Text, nullable=False, server_default=""),
    sa.Column("status", sa.Text, nullable=False, server_default="Available"),
    sa.CheckConstraint("status IN ('Available', 'Borrowed')", name="books_status_check"),
)

members = sa.Table(
    "members", metadata,
    sa.Column("flat_number", sa.Text, primary_key=True),
    sa.Column("resident_name", sa.Text, nullable=False),
    sa.Column("phone_number", sa.Text, nullable=False, server_default=""),
)

transactions = sa.Table(
    "transactions", metadata,
    sa.Column("log_id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("book_id", sa.Integer, sa.ForeignKey("books.book_id"), nullable=False),
    sa.Column("flat_number", sa.Text, sa.ForeignKey("members.flat_number"), nullable=False),
    sa.Column("issue_date", sa.Date, nullable=False),
    sa.Column("due_date", sa.Date, nullable=False),
    sa.Column("return_date", sa.Date),
    sa.Index("idx_transactions_open", "return_date"),
)


class LibraryError(Exception):
    """Raised for user-facing errors such as issuing a book that is already out."""


_engines = {}


def get_engine(url=None):
    url = url or os.environ.get("DATABASE_URL") or f"sqlite:///{_SQLITE_PATH}"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url not in _engines:
        if url.startswith("sqlite"):
            engine = sa.create_engine(url, connect_args={"timeout": 10})

            @sa.event.listens_for(engine, "connect")
            def _enable_foreign_keys(dbapi_conn, _):
                dbapi_conn.execute("PRAGMA foreign_keys = ON")
        else:
            # Free hosted Postgres tiers cap client connections.
            engine = sa.create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=3)
        _engines[url] = engine
    return _engines[url]


def _dicts(result):
    return [dict(row._mapping) for row in result]


def init_db(url=None):
    metadata.create_all(get_engine(url))


def add_book(title, author="", genre="", url=None):
    title = title.strip()
    if not title:
        raise LibraryError("Title is required.")
    with get_engine(url).begin() as conn:
        result = conn.execute(
            books.insert().values(title=title, author=author.strip(), genre=genre.strip())
        )
        return result.inserted_primary_key[0]


def add_member(flat_number, resident_name, phone_number="", url=None):
    flat_number, resident_name = flat_number.strip().upper(), resident_name.strip()
    if not flat_number or not resident_name:
        raise LibraryError("Flat number and resident name are required.")
    try:
        with get_engine(url).begin() as conn:
            conn.execute(members.insert().values(
                flat_number=flat_number, resident_name=resident_name,
                phone_number=phone_number.strip()))
    except sa.exc.IntegrityError:
        raise LibraryError(f"Flat {flat_number} is already registered.") from None


def list_books(search="", status=None, url=None):
    query = sa.select(books)
    search = search.strip().lower()
    if search:
        query = query.where(sa.or_(
            *(sa.func.lower(col).contains(search, autoescape=True)
              for col in (books.c.title, books.c.author, books.c.genre))))
    if status:
        query = query.where(books.c.status == status)
    query = query.order_by(sa.func.lower(books.c.title), books.c.book_id)
    with get_engine(url).connect() as conn:
        return _dicts(conn.execute(query))


def list_members(url=None):
    with get_engine(url).connect() as conn:
        return _dicts(conn.execute(sa.select(members).order_by(members.c.flat_number)))


def issue_book(book_id, flat_number, loan_days=DEFAULT_LOAN_DAYS, today=None, url=None):
    today = today or date.today()
    with get_engine(url).begin() as conn:
        if conn.execute(sa.select(members.c.flat_number)
                        .where(members.c.flat_number == flat_number)).first() is None:
            raise LibraryError(f"Flat {flat_number} is not a registered member.")
        # Conditional update makes concurrent issues of the same copy impossible.
        claimed = conn.execute(
            books.update()
            .where(books.c.book_id == book_id, books.c.status == "Available")
            .values(status="Borrowed"))
        if claimed.rowcount == 0:
            raise LibraryError("That book is not available right now.")
        result = conn.execute(transactions.insert().values(
            book_id=book_id, flat_number=flat_number, issue_date=today,
            due_date=today + timedelta(days=int(loan_days))))
        return result.inserted_primary_key[0]


def return_book(log_id, today=None, url=None):
    today = today or date.today()
    with get_engine(url).begin() as conn:
        loan = conn.execute(
            sa.select(transactions.c.book_id)
            .where(transactions.c.log_id == log_id, transactions.c.return_date.is_(None))).first()
        closed = conn.execute(
            transactions.update()
            .where(transactions.c.log_id == log_id, transactions.c.return_date.is_(None))
            .values(return_date=today))
        if loan is None or closed.rowcount == 0:
            raise LibraryError("This loan has already been returned.")
        conn.execute(books.update().where(books.c.book_id == loan.book_id)
                     .values(status="Available"))


_loans = (
    sa.select(transactions.c.log_id, transactions.c.book_id, books.c.title, books.c.author,
              transactions.c.flat_number, members.c.resident_name, members.c.phone_number,
              transactions.c.issue_date, transactions.c.due_date, transactions.c.return_date)
    .join(books, books.c.book_id == transactions.c.book_id)
    .join(members, members.c.flat_number == transactions.c.flat_number)
)


def active_loans(today=None, url=None):
    today = today or date.today()
    query = (_loans.where(transactions.c.return_date.is_(None))
             .order_by(transactions.c.due_date, transactions.c.log_id))
    with get_engine(url).connect() as conn:
        loans = _dicts(conn.execute(query))
    for loan in loans:
        loan["days_overdue"] = max((today - loan["due_date"]).days, 0)
    return loans


def overdue_loans(today=None, url=None):
    return [loan for loan in active_loans(today, url) if loan["days_overdue"] > 0]


def loan_history(limit=500, url=None):
    with get_engine(url).connect() as conn:
        return _dicts(conn.execute(_loans.order_by(transactions.c.log_id.desc()).limit(limit)))


def stats(today=None, url=None):
    today = today or date.today()
    borrowed = sa.func.coalesce(sa.func.sum(
        sa.case((books.c.status == "Borrowed", 1), else_=0)), 0)
    with get_engine(url).connect() as conn:
        total, out = conn.execute(sa.select(sa.func.count(), borrowed).select_from(books)).one()
        flats = conn.execute(sa.select(sa.func.count()).select_from(members)).scalar_one()
        overdue = conn.execute(
            sa.select(sa.func.count()).select_from(transactions)
            .where(transactions.c.return_date.is_(None), transactions.c.due_date < today)
        ).scalar_one()
    return {"total": total, "borrowed": out, "available": total - out,
            "members": flats, "overdue": overdue}
