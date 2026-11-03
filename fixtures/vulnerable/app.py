# INTENTIONALLY VULNERABLE — test fixture for SecGenie. Do not copy.
import hashlib
import pickle
import sqlite3
import subprocess

import yaml
from flask import Flask, request

app = Flask(__name__)


@app.route("/ping")
def ping():
    host = request.args.get("host")
    # Command injection via shell=True with user input.
    return subprocess.check_output(f"ping -c 1 {host}", shell=True)


@app.route("/user")
def get_user():
    user_id = request.args.get("id")
    conn = sqlite3.connect("app.db")
    # SQL injection via string formatting.
    rows = conn.execute(f"SELECT * FROM users WHERE id = '{user_id}'").fetchall()
    return str(rows)


@app.route("/load", methods=["POST"])
def load():
    # Insecure deserialization of untrusted data.
    return str(pickle.loads(request.data))


def parse_config(raw):
    # Unsafe YAML loading can construct arbitrary objects.
    return yaml.load(raw)


def hash_password(password):
    # Weak hash for passwords.
    return hashlib.md5(password.encode()).hexdigest()


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)
