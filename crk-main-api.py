from flask import Flask, send_from_directory
from flask_cors import CORS
import os

from tools.todo.shell.web import todo_bp

app = Flask(__name__)
CORS(app)
app.register_blueprint(todo_bp, url_prefix="/tools/todo")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GUIDES_DIR = os.path.join(BASE_DIR, "guides")


@app.route("/")
def index():
    return "Hello, World!"

@app.route("/guides/")
def guides_index():
    files = sorted(f for f in os.listdir(GUIDES_DIR) if f.endswith(".html"))
    links = "".join(f'<li><a href="/guides/{f}">{f}</a></li>' for f in files)
    return f"<ul>{links}</ul>"

@app.route("/guides/<path:filename>")
def guides(filename):
    return send_from_directory(GUIDES_DIR, filename)


@app.route("/icons/<path:filename>")
def icons(filename):
    return send_from_directory(os.path.join(BASE_DIR, "icons"), filename)


if __name__ == "__main__":
    app.run(debug=True, port=5183)
