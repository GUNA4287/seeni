from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
import sqlite3, os, pathlib, time
from werkzeug.utils import secure_filename

# ------------------ Config ------------------
APP_DIR = pathlib.Path(__file__).parent.resolve()
DB_FILE = APP_DIR / "database.sql"          # keep your filename
UPLOAD_DIR = APP_DIR / "uploads"            # where photos go
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_CONTENT_LENGTH = 6 * 1024 * 1024        # 6 MB per file

app = Flask(__name__, static_folder=None)   # serve files manually below
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
CORS(app)  # allow your frontend (fetch to 127.0.0.1:8000)

# ------------------ DB helpers ------------------
def get_db():
    return sqlite3.connect(DB_FILE)

def init_db():
    with get_db() as conn:
        c = conn.cursor()

        # Users table (for /login)
        c.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        """)

        # Admin table (for /api/check-key)
        c.execute("""
            CREATE TABLE IF NOT EXISTS admin(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                adminpass TEXT NOT NULL
            )
        """)

        # Employees table (for your register page)
        c.execute("""
            CREATE TABLE IF NOT EXISTS employees(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                department TEXT NOT NULL,
                role TEXT NOT NULL,
                roll_number TEXT NOT NULL UNIQUE,
                photo_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Seed demo data if empty
        user_count = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count == 0:
            c.execute("INSERT INTO users (username, password) VALUES (?,?)",
                      ("hidan", "killer"))

        admin_count = c.execute("SELECT COUNT(*) FROM admin").fetchone()[0]
        if admin_count == 0:
            c.execute("INSERT INTO admin (adminpass) VALUES (?)",
                      ("ceo@2025",))

        conn.commit()

def row_to_employee_dict(row):
    # row order must match SELECT columns below
    (id_, name, email, department, role, roll, photo, created) = row
    return {
        "id": id_,
        "name": name,
        "email": email,
        "department": department,
        "role": role,
        "roll_number": roll,
        "photo_url": f"/uploads/{os.path.basename(photo)}" if photo else None,
        "created_at": created
    }

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# ------------------ Static/Index ------------------
@app.route("/")
def index():
    # Keep your main index if you have one; otherwise open employee-delete.html in browser directly
    index_path = APP_DIR / "index.html"
    if index_path.exists():
        return send_file(index_path)
    return "<h3>Backend is running. Open /employee-delete.html to manage employees.</h3>"

@app.route("/<path:path>")
def static_files(path):
    # Serve any local file next to server.py (HTML/CSS/JS)
    target = APP_DIR / path
    if target.exists():
        return send_file(target)
    return "Not Found", 404

@app.route("/uploads/<path:filename>")
def get_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)

# ------------------ Auth / Admin ------------------
@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username") or ""
    password = request.form.get("password") or ""
    try: 
        with get_db() as conn:
            cur = conn.execute(
                "SELECT id FROM users WHERE username=? AND password=?",
                (username, password)
            )
            row = cur.fetchone()
        return jsonify({"status": "success" if row else "fail"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.post("/api/check-key")
def check_key():
    data = request.get_json(silent=True) or {}
    key = data.get("key", "")
    with get_db() as conn:
        cur = conn.execute("SELECT 1 FROM admin WHERE adminpass=? LIMIT 1", (key,))
        row = cur.fetchone()
    return jsonify(ok=bool(row))

# ------------------ Employees API ------------------
@app.post("/api/employees")
def create_employee():
    """
    Accepts multipart/form-data with fields:
      name, email, department, role, roll_number, photo (file, optional)
    Returns: { ok: true, employee_id, photo_url? } or error.
    """
    try:
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        department = (request.form.get("department") or "").strip()
        role = (request.form.get("role") or "").strip()
        roll_number = (request.form.get("roll_number") or "").strip()

        # Validate
        missing = [k for k, v in {
            "name": name, "email": email, "department": department,
            "role": role, "roll_number": roll_number
        }.items() if not v]
        if missing:
            return jsonify(ok=False, message=f"Missing fields: {', '.join(missing)}"), 400

        # Handle optional photo
        photo = request.files.get("photo")
        photo_path = None
        if photo and photo.filename:
            if not allowed_file(photo.filename):
                return jsonify(ok=False, message="Unsupported image type"), 415
            # unique filename: roll + timestamp + ext
            ext = photo.filename.rsplit(".", 1)[1].lower()
            fname = secure_filename(f"{roll_number}_{int(time.time())}.{ext}")
            save_path = UPLOAD_DIR / fname
            photo.save(save_path)
            photo_path = str(save_path)

        # Insert into DB
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO employees (name, email, department, role, roll_number, photo_path)
                VALUES (?,?,?,?,?,?)
            """, (name, email, department, role, roll_number, photo_path))
            emp_id = cur.lastrowid
            conn.commit()

        return jsonify(
            ok=True,
            employee_id=emp_id,
            photo_url=(f"/uploads/{os.path.basename(photo_path)}" if photo_path else None)
        ), 201

    except sqlite3.IntegrityError as ie:
        # likely duplicate roll_number
        msg = "Roll number already exists." if "UNIQUE" in str(ie).upper() else str(ie)
        return jsonify(ok=False, message=msg), 409
    except Exception as e:
        return jsonify(ok=False, message=str(e)), 500

@app.get("/api/employees")
def list_employees():
    """List all employees."""
    with get_db() as conn:
        cur = conn.execute("""
            SELECT id, name, email, department, role, roll_number, photo_path, created_at
            FROM employees
            ORDER BY id DESC
        """)
        rows = cur.fetchall()
    return jsonify(ok=True, employees=[row_to_employee_dict(r) for r in rows])

@app.get("/api/employees/<int:emp_id>")
def get_employee(emp_id: int):
    """Fetch single employee by id."""
    with get_db() as conn:
        cur = conn.execute("""
            SELECT id, name, email, department, role, roll_number, photo_path, created_at
            FROM employees WHERE id=?
        """, (emp_id,))
        row = cur.fetchone()
    if not row:
        return jsonify(ok=False, message="Not found"), 404
    return jsonify(ok=True, employee=row_to_employee_dict(row))

# ---- NEW: Delete employee ----
@app.delete("/api/employees/<int:emp_id>")
def delete_employee(emp_id: int):
    with get_db() as conn:
        cur = conn.execute("SELECT photo_path FROM employees WHERE id=?", (emp_id,))
        row = cur.fetchone()
        if not row:
            return jsonify(ok=False, message="Employee not found"), 404
        photo_path = row[0]
        conn.execute("DELETE FROM employees WHERE id=?", (emp_id,))
        conn.commit()

    # Delete photo file if present
    if photo_path and os.path.exists(photo_path):
        try:
            os.remove(photo_path)
        except Exception:
            pass  # ignore filesystem errors

    return jsonify(ok=True, message="Employee deleted")

# ------------------ Main ------------------
if __name__ == "__main__":
    init_db()
    print(f"DB: {DB_FILE}")
    print(f"Uploads: {UPLOAD_DIR}")
    app.run(host="0.0.0.0", port=8000, debug=True)
