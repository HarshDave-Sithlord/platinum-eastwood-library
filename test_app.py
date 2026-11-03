import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.delenv("APP_PASSCODE", raising=False)
    return AppTest.from_file("app.py", default_timeout=30)


def go(at, page):
    at.sidebar.radio(key="page").set_value(page).run()


def submit(at, label):
    next(b for b in at.button if b.label == label).click().run()


def test_passcode_gate(app):
    app.secrets["APP_PASSCODE"] = "4321"
    app.run()
    assert [t.label for t in app.text_input] == ["Library passcode"]
    assert not app.metric

    app.text_input[0].input("0000")
    submit(app, "Enter")
    assert app.error[0].value == "Wrong passcode."

    app.text_input[0].input("4321")
    submit(app, "Enter")
    assert [m.label for m in app.metric][:2] == ["Total books", "Available"]


def test_issue_and_return_flow(app):
    app.run()
    assert [m.value for m in app.metric] == ["0", "0", "0", "0", "0"]

    go(app, "Catalogue")
    title, author, genre = app.text_input[0], app.text_input[1], app.text_input[2]
    title.input("Malgudi Days"); author.input("R. K. Narayan"); genre.input("Short Stories")
    submit(app, "Add book")
    assert "Added **Malgudi Days**" in app.success[0].value

    go(app, "Members")
    app.text_input[0].input("a-101"); app.text_input[1].input("Meera Iyer")
    app.text_input[2].input("98450 11223")
    submit(app, "Register")
    assert "Registered flat **A-101**" in app.success[0].value

    go(app, "Issue Book")
    submit(app, "Issue Book")
    assert "Issued **Malgudi Days** to flat **A-101**" in app.success[0].value

    go(app, "Dashboard")
    assert [m.value for m in app.metric] == ["1", "0", "1", "0", "1"]

    go(app, "Return Book")
    submit(app, "Mark as Returned")
    assert "**Malgudi Days** returned by flat **A-101**" in app.success[0].value
    assert app.info[0].value == "No books are currently borrowed."

    go(app, "History")
    log = app.dataframe[0].value
    assert list(log["Book"]) == ["Malgudi Days"] and list(log["Returned"]) != ["—"]
