"""Thin shell smoke test. All real logic is covered by the pure core tests."""

import re

import pytest
from flask import Flask

from tools.todo.shell.web import todo_bp


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update(TODO_DB=str(tmp_path / "todo.sqlite3"), TESTING=True)
    app.secret_key = "test"
    app.register_blueprint(todo_bp, url_prefix="/todo")
    return app


def csrf_from(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def signup(client, username):
    token = csrf_from(client.get("/todo/signup").get_data(as_text=True))
    r = client.post("/todo/signup", data={"csrf": token, "username": username, "password": "password1", "confirm": "password1"})
    assert r.status_code == 302, r.get_data(as_text=True)
    page = client.get("/todo/").get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)


def cmd(client, token, **payload):
    return client.post("/todo/api/command", json=payload, headers={"X-CSRF-Token": token})


def done(view, task_id):
    return next(t["done"] for b in view["bays"] for t in b["tasks"] if t["id"] == task_id)


def test_flow(app):
    alice = app.test_client()
    assert alice.get("/todo/").status_code == 302
    assert alice.get("/todo/api/view").status_code == 401

    token = signup(alice, "alice")
    v = alice.get("/todo/api/view").get_json()
    assert [b["name"] for b in v["bays"]] == ["Main"]
    assert [c["title"] for c in v["cooldowns"]] == ["Fountain", "Harbour Ship"]
    assert [c["title"] for c in v["bays"][0]["tasks"][4]["subtasks"]] == ["Mail Box", "Cake Hound", "Town Square Event"]
    assert not v["can_undo"]  # the starter template is the baseline, not an undoable step
    task_id = v["bays"][0]["tasks"][0]["id"]
    v = cmd(alice, token, type="complete", task_id=task_id).get_json()
    assert done(v, task_id) and v["can_undo"]

    v = alice.post("/todo/api/undo", headers={"X-CSRF-Token": token}).get_json()
    assert not done(v, task_id) and v["can_redo"]
    v = alice.post("/todo/api/redo", headers={"X-CSRF-Token": token}).get_json()
    assert done(v, task_id)

    assert cmd(alice, "wrong", type="add_bay", name="X").status_code == 403
    assert cmd(alice, token, type="bogus").status_code == 400

    # A second user sees nothing of alice's and cannot touch her tasks.
    bob = app.test_client()
    bob_token = signup(bob, "bob")
    assert task_id not in {t["id"] for b in bob.get("/todo/api/view").get_json()["bays"] for t in b["tasks"]}
    assert cmd(bob, bob_token, type="uncomplete", task_id=task_id).status_code == 400
    assert done(alice.get("/todo/api/view").get_json(), task_id)


def test_login_and_duplicate_signup(app):
    c = app.test_client()
    signup(c, "carol")
    c.post("/todo/logout", data={"csrf": csrf_from(c.get("/todo/signup").get_data(as_text=True))})
    assert c.get("/todo/").status_code == 302

    token = csrf_from(c.get("/todo/login").get_data(as_text=True))
    assert c.post("/todo/login", data={"csrf": token, "username": "carol", "password": "nope"}).status_code == 401
    assert c.post("/todo/login", data={"csrf": token, "username": "Carol", "password": "password1"}).status_code == 302

    other = app.test_client()
    token = csrf_from(other.get("/todo/signup").get_data(as_text=True))
    r = other.post("/todo/signup", data={"csrf": token, "username": "carol", "password": "password1", "confirm": "password1"})
    assert r.status_code == 400 and "taken" in r.get_data(as_text=True)


def test_robot_routes(app):
    c = app.test_client()
    page = c.get("/todo/robot/").get_data(as_text=True)
    assert 'name="todo-api" content="/todo/robot/api/"' in page and "robot · public" in page
    text = c.get("/todo/debug/robot").get_data(as_text=True)
    assert "Bay 1 · Main" in text and "Cooldowns" in text and "Fountain" in text

    v = c.get("/todo/robot/api/view").get_json()
    bay_id = v["bays"][0]["id"]
    v = c.post("/todo/robot/api/command", json={"type": "add_task", "bay_id": bay_id, "title": "Robot task"}).get_json()
    task = next(t for t in v["bays"][0]["tasks"] if t["title"] == "Robot task")
    v = c.post("/todo/robot/api/command", json={"type": "remove_task", "task_id": task["id"]}).get_json()
    assert all(t["title"] != "Robot task" for t in v["bays"][0]["tasks"])
    assert c.post("/todo/robot/api/undo").get_json()["can_redo"]
    assert c.post("/todo/robot/api/command", json={"type": "bogus"}).status_code == 400

    assert c.get("/todo/debug/robot.json?now=bad").status_code == 400
    assert c.get("/todo/debug/robot.json?now=2099-01-01T00:00:00Z").get_json()["now"].startswith("2099")

    # robot's routes never expose a real user, and nobody can register as robot
    alice = app.test_client()
    signup(alice, "alice")
    assert c.get("/todo/api/view").status_code == 401
    token = csrf_from(c.get("/todo/signup").get_data(as_text=True))
    r = c.post("/todo/signup", data={"csrf": token, "username": "robot", "password": "password1", "confirm": "password1"})
    assert r.status_code == 400 and "reserved" in r.get_data(as_text=True)


def test_admin_preset_and_suggested(app):
    from tools.todo.shell import db

    admin = app.test_client()
    admin_token = signup(admin, "boss")
    user = app.test_client()
    user_token = signup(user, "pleb")

    # only admins may open or edit the preset
    assert admin.get("/todo/admin/").status_code == 403
    assert user.post("/todo/admin/api/command", json={"type": "add_bay", "name": "X"},
                     headers={"X-CSRF-Token": user_token}).status_code == 403
    with app.app_context():
        conn = db.connect(app.config["TODO_DB"])
        db.set_admin(conn, db.get_user_by_username(conn, "boss")["id"], True)
        conn.commit()
    assert admin.get("/todo/admin/").status_code == 200
    assert "Edit the preset" in admin.get("/todo/").get_data(as_text=True)

    # admin adds an item to the preset
    h = {"X-CSRF-Token": admin_token}
    v = admin.post("/todo/admin/api/command", json={"type": "add_bay", "name": "Events"}, headers=h).get_json()
    bay_id = v["bays"][-1]["id"]
    v = admin.post("/todo/admin/api/command", headers=h, json={
        "type": "add_task", "bay_id": bay_id, "title": "Arena Shop",
        "recurrence": {"kind": "interval", "every": 3, "anchor": "2026-09-24"}}).get_json()
    assert v["suggested"] == []  # the preset itself never has suggestions

    # existing users see it under Suggested, and can import it
    uh = {"X-CSRF-Token": user_token}
    [s] = user.get("/todo/api/view").get_json()["suggested"]
    assert (s["title"], s["bay_name"]) == ("Arena Shop", "Events")
    v = user.post("/todo/api/suggested", json={"id": s["id"], "action": "import"}, headers=uh).get_json()
    assert v["suggested"] == []
    shop = next(t for b in v["bays"] for t in b["tasks"] if t["title"] == "Arena Shop")
    assert shop["recurrence_label"] == "Every 3 days"
    assert user.post("/todo/api/suggested", json={"id": s["id"], "action": "import"}, headers=uh).status_code == 400

    # new users start with everything in the preset, so nothing is suggested
    newbie = app.test_client()
    signup(newbie, "newbie")
    v = newbie.get("/todo/api/view").get_json()
    assert "Arena Shop" in [t["title"] for b in v["bays"] for t in b["tasks"]]
    assert v["suggested"] == []

    # hiding a suggestion
    admin.post("/todo/admin/api/command", headers=h, json={"type": "add_task", "bay_id": bay_id, "title": "Guild Shop"})
    [s] = user.get("/todo/api/view").get_json()["suggested"]
    v = user.post("/todo/api/suggested", json={"id": s["id"], "action": "hide"}, headers=uh).get_json()
    assert v["suggested"] == []
    assert "Guild Shop" not in [t["title"] for b in v["bays"] for t in b["tasks"]]

    # "preset" is reserved
    token = csrf_from(newbie.get("/todo/signup").get_data(as_text=True))
    r = newbie.post("/todo/signup", data={"csrf": token, "username": "preset", "password": "password1", "confirm": "password1"})
    assert r.status_code == 400


def test_repeat_preview(app):
    c = app.test_client()
    r = c.post("/todo/robot/api/preview", json={"recurrence": {"kind": "interval", "every": 3, "anchor": "2026-09-24"}})
    assert r.status_code == 200 and r.get_json()["next_reset_at"].endswith("+00:00")
    assert c.post("/todo/robot/api/preview", json={"recurrence": {"kind": "once"}}).get_json() == {"next_reset_at": None}
    assert c.post("/todo/robot/api/preview", json={"recurrence": {"kind": "interval", "every": 1}}).status_code == 400
    assert c.post("/todo/api/preview", json={"recurrence": {"kind": "daily"}}).status_code == 401


def test_browse_and_import_preset_items(app):
    from tools.todo.shell import db

    admin = app.test_client()
    ah = {"X-CSRF-Token": signup(admin, "boss")}
    user = app.test_client()
    uh = {"X-CSRF-Token": signup(user, "pleb")}
    assert user.get("/todo/api/missing").get_json() == {"items": []}
    with app.app_context():
        conn = db.connect(app.config["TODO_DB"])
        db.set_admin(conn, db.get_user_by_username(conn, "boss")["id"], True)
        conn.commit()

    bay_id = admin.get("/todo/admin/api/view").get_json()["bays"][0]["id"]
    for title in ("Arcade", "Post Office"):
        admin.post("/todo/admin/api/command", headers=ah, json={"type": "add_task", "bay_id": bay_id, "title": title})
    admin.post("/todo/admin/api/command", headers=ah, json={"type": "add_cooldown", "title": "Wishes", "minutes": 60})

    items = user.get("/todo/api/missing").get_json()["items"]
    assert [(i["title"], i["kind"]) for i in items] == [("Arcade", "task"), ("Post Office", "task"), ("Wishes", "cooldown")]
    picked = [i["id"] for i in items if i["title"] != "Post Office"]
    v = user.post("/todo/api/import", json={"ids": picked}, headers=uh).get_json()
    titles = [t["title"] for b in v["bays"] for t in b["tasks"]]
    assert "Arcade" in titles and "Post Office" not in titles
    assert "Wishes" in [c["title"] for c in v["cooldowns"]]
    assert [i["title"] for i in user.get("/todo/api/missing").get_json()["items"]] == ["Post Office"]
    assert user.post("/todo/api/undo", headers=uh).get_json()["can_redo"]  # one undo step for the lot
    assert len(user.get("/todo/api/missing").get_json()["items"]) == 3

    assert user.post("/todo/api/import", json={"ids": picked}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert user.post("/todo/api/import", json={"ids": "all"}, headers=uh).status_code == 400
    assert user.post("/todo/api/import", json={"ids": ["nope"]}, headers=uh).status_code == 400
    assert admin.get("/todo/admin/api/missing").get_json() == {"items": []}
