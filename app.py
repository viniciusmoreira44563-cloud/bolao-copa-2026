from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify, make_response, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3, json, secrets, os
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = "bolao-copa-2026-app-web-profissional"

DB_PATH = Path("bolao_copa_2026.db")
UPLOAD_FOLDER = Path("static/uploads")
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

ADMIN_PASSWORD = "admin123"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

FLAGS = {
    "México": "🇲🇽",
    "Africa do Sul": "🇿🇦",
    "África do Sul": "🇿🇦",
    "Coreia do Sul": "🇰🇷",
    "República Tcheca": "🇨🇿",
    "Republica Tcheca": "🇨🇿",
    "Canadá": "🇨🇦",
    "Canada": "🇨🇦",
    "Bósnia": "🇧🇦",
    "Bosnia": "🇧🇦",
    "Estados Unidos": "🇺🇸",
    "Paraguai": "🇵🇾",
    "Austrália": "🇦🇺",
    "Australia": "🇦🇺",
    "Turquia": "🇹🇷",
    "Brasil": "🇧🇷",
    "Argentina": "🇦🇷",
    "França": "🇫🇷",
    "Franca": "🇫🇷",
    "Alemanha": "🇩🇪",
    "Espanha": "🇪🇸",
    "Portugal": "🇵🇹",
    "Inglaterra": "🏴",
    "Japão": "🇯🇵",
    "Japao": "🇯🇵",
    "Itália": "🇮🇹",
    "Italia": "🇮🇹",
    "Uruguai": "🇺🇾",
    "Sérvia": "🇷🇸",
    "Servia": "🇷🇸",
}

# -----------------------------
# Banco
# -----------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def column_exists(cur, table, column):
    cur.execute(f"PRAGMA table_info({table})")
    return any(row["name"] == column for row in cur.fetchall())

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        password_hash TEXT,
        avatar TEXT,
        api_token TEXT UNIQUE,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stage TEXT,
        group_name TEXT,
        team_home TEXT NOT NULL,
        team_away TEXT NOT NULL,
        match_date TEXT,
        location TEXT,
        score_home INTEGER,
        score_away INTEGER,
        finished INTEGER DEFAULT 0,
        locked INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS guesses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        match_id INTEGER NOT NULL,
        guess_home INTEGER NOT NULL,
        guess_away INTEGER NOT NULL,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, match_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ranking_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        points INTEGER NOT NULL,
        position INTEGER NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Migração segura para versões antigas
    for col, coltype in [
        ("password_hash", "TEXT"),
        ("avatar", "TEXT"),
        ("avatar_data", "BLOB"),
        ("avatar_mime", "TEXT"),
        ("api_token", "TEXT UNIQUE")
    ]:
        if not column_exists(cur, "users", col):
            cur.execute(f"ALTER TABLE users ADD COLUMN {col} {coltype}")

    # Carga inicial dos jogos
    cur.execute("SELECT COUNT(*) as total FROM matches")
    if cur.fetchone()["total"] == 0:
        data = json.loads(Path("schedule_data.json").read_text(encoding="utf-8"))
        for m in data:
            cur.execute("""
                INSERT INTO matches (stage, group_name, team_home, team_away, match_date, location)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                m["stage"],
                m["group_name"],
                m["team_home"],
                m["team_away"],
                m["match_date"],
                m["location"],
            ))


    # Usuários iniciais do grupo
    seed_users = [
        "Vinicius",
        "Diogo",
        "Pai",
        "Paulo",
        "Rafael",
        "Anderson",
        "Gabriel",
        "Geison",
        "Luisxx",
        "Luiz",
        "Cleber",
        "Alfredo",
    ]

    for user_name in seed_users:
        cur.execute("SELECT id FROM users WHERE lower(name) = lower(?)", (user_name,))
        existing_user = cur.fetchone()

        if not existing_user:
            cur.execute("""
                INSERT INTO users (name, password_hash, api_token)
                VALUES (?, ?, ?)
            """, (
                user_name,
                generate_password_hash("123"),
                create_token()
            ))


    conn.commit()
    conn.close()

# -----------------------------
# Helpers
# -----------------------------

def calc_points_sql():
    return """
    CASE
        WHEN m.finished = 1 THEN
            CASE
                WHEN g.guess_home = m.score_home AND g.guess_away = m.score_away THEN 10
                WHEN 
                    (g.guess_home = g.guess_away AND m.score_home = m.score_away)
                    OR (g.guess_home > g.guess_away AND m.score_home > m.score_away)
                    OR (g.guess_home < g.guess_away AND m.score_home < m.score_away)
                THEN 5
                ELSE 0
            END
        ELSE 0
    END
    """

def get_ranking_rows():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT 
            u.id,
            u.name,
            u.avatar,
            COALESCE(SUM({calc_points_sql()}), 0) as points,
            COUNT(g.id) as guesses_count,
            SUM(CASE WHEN m.finished = 1 AND g.guess_home = m.score_home AND g.guess_away = m.score_away THEN 1 ELSE 0 END) as exacts,
            SUM(CASE 
                WHEN m.finished = 1 AND (
                    (g.guess_home = g.guess_away AND m.score_home = m.score_away)
                    OR (g.guess_home > g.guess_away AND m.score_home > m.score_away)
                    OR (g.guess_home < g.guess_away AND m.score_home < m.score_away)
                ) THEN 1 ELSE 0 END
            ) as simple_hits
        FROM users u
        LEFT JOIN guesses g ON g.user_id = u.id
        LEFT JOIN matches m ON m.id = g.match_id
        GROUP BY u.id, u.name, u.avatar
        ORDER BY points DESC, exacts DESC, simple_hits DESC, guesses_count DESC, u.name ASC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    final = []
    for idx, row in enumerate(rows, start=1):
        exacts = row.get("exacts") or 0
        simple_hits = row.get("simple_hits") or 0
        guesses_count = row.get("guesses_count") or 0
        points = row.get("points") or 0

        badges = []
        if idx == 1:
            badges.append({"icon": "👑", "label": "Líder"})
        if exacts >= 3:
            badges.append({"icon": "🎯", "label": "Cravador Nato"})
        elif exacts >= 1:
            badges.append({"icon": "🏹", "label": "Mira Boa"})
        if simple_hits >= 5:
            badges.append({"icon": "🔥", "label": "Em Boa Fase"})
        if guesses_count >= 20:
            badges.append({"icon": "⚡", "label": "Participativo"})
        if points == 0 and guesses_count > 0:
            badges.append({"icon": "🦓", "label": "Zebra"})
        if not badges:
            badges.append({"icon": "⚽", "label": "Na Disputa"})

        if idx <= 3:
            trend_icon, trend_text, trend_class = "↑", "+2", "up"
        elif points == 0:
            trend_icon, trend_text, trend_class = "−", "=", "stable"
        else:
            trend_icon, trend_text, trend_class = "↓", "-1", "down"

        final.append({
            **row,
            "position": idx,
            "avatar_letter": (row["name"][:1] or "?").upper(),
            "avatar_url": f"/avatar/{row['id']}" if row.get("avatar") else None,
            "badges": badges[:3],
            "trend_icon": trend_icon,
            "trend_text": trend_text,
            "trend_class": trend_class,
            "progress": min(100, points),
        })

    return final

def create_token():
    return secrets.token_urlsafe(32)

def current_user():
    if "user_id" not in session:
        return None

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],))
    user = cur.fetchone()
    conn.close()
    return user

def require_api_user():
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()

    if not token:
        return None

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE api_token = ?", (token,))
    user = cur.fetchone()
    conn.close()
    return user

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def parse_match_date(value):
    try:
        return datetime.strptime(value or "", "%Y-%m-%d %H:%M")
    except ValueError:
        return None

def is_match_closed(match_date):
    dt = parse_match_date(match_date)
    if not dt:
        return False
    return datetime.now() >= (dt - timedelta(minutes=5))

def display_team_name(team):
    if not team or team.startswith("Jogo") or team.lower().strip() == "a definir":
        return "A definir"
    return team

def display_team_flag(team):
    if not team or team.startswith("Jogo") or team.lower().strip() == "a definir":
        return "⚽"
    return FLAGS.get(team, "⚽")

def get_user_by_id(user_id):
    if not user_id:
        return None
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()
    conn.close()
    return user

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Faça login para acessar o bolão.")
            return redirect(url_for("login"))
        if not get_user_by_id(session.get("user_id")):
            session.clear()
            flash("Sua sessão expirou. Entre novamente.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

@app.context_processor
def inject_globals():
    header_user = get_user_by_id(session.get("user_id")) if session.get("user_id") else None
    return dict(
        FLAGS=FLAGS,
        display_team_name=display_team_name,
        display_team_flag=display_team_flag,
        header_user=header_user,
        is_match_closed=is_match_closed,
    )


@app.route("/avatar/<int:user_id>")
def avatar_image(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT avatar, avatar_data, avatar_mime FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()
    conn.close()

    if not user:
        return "", 404

    if user["avatar_data"]:
        return Response(
            bytes(user["avatar_data"]),
            mimetype=user["avatar_mime"] or "image/jpeg"
        )

    if user["avatar"]:
        avatar_path = UPLOAD_FOLDER / user["avatar"]
        if avatar_path.exists():
            return Response(
                avatar_path.read_bytes(),
                mimetype="image/jpeg"
            )

    return "", 404


# -----------------------------
# Web
# -----------------------------

@app.route("/")
@login_required
def home():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as total FROM matches")
    total_matches = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(DISTINCT group_name) as total FROM matches WHERE group_name LIKE 'Grupo%'")
    total_groups = cur.fetchone()["total"]

    cur.execute("SELECT * FROM matches WHERE finished = 0 ORDER BY match_date, id LIMIT 4")
    upcoming = cur.fetchall()

    leaders = get_ranking_rows()[:5]

    user_position = None
    user_points = 0
    for row in get_ranking_rows():
        if row["id"] == session["user_id"]:
            user_position = row["position"]
            user_points = row["points"]
            break

    conn.close()

    return render_template(
        "home.html",
        upcoming=upcoming,
        leaders=leaders,
        total_matches=total_matches,
        total_groups=total_groups,
        user_position=user_position,
        user_points=user_points,
        user_name=session.get("user_name"),
    )

@app.route("/entrar", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "").strip()

        if not name or not password:
            flash("Digite nome e senha.")
            return redirect(url_for("login"))

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE name = ?", (name,))
        user = cur.fetchone()

        if not user:
            token = create_token()
            cur.execute("""
                INSERT INTO users (name, password_hash, api_token)
                VALUES (?, ?, ?)
            """, (name, generate_password_hash(password), token))
            conn.commit()
            cur.execute("SELECT * FROM users WHERE name = ?", (name,))
            user = cur.fetchone()
        else:
            if not user["password_hash"]:
                cur.execute(
                    "UPDATE users SET password_hash=?, api_token=? WHERE id=?",
                    (generate_password_hash(password), user["api_token"] or create_token(), user["id"]),
                )
                conn.commit()
                cur.execute("SELECT * FROM users WHERE name = ?", (name,))
                user = cur.fetchone()
            elif not check_password_hash(user["password_hash"], password):
                conn.close()
                flash("Senha incorreta para esse nome.")
                return redirect(url_for("login"))

        conn.close()

        session["user_id"] = user["id"]
        session["user_name"] = user["name"]

        return redirect(url_for("home"))

    return render_template("login.html")

@app.route("/recuperar-senha", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not name or not new_password or not confirm_password:
            flash("Preencha todos os campos.")
            return redirect(url_for("forgot_password"))

        if new_password != confirm_password:
            flash("As senhas não conferem.")
            return redirect(url_for("forgot_password"))

        if len(new_password) < 4:
            flash("A senha precisa ter pelo menos 4 caracteres.")
            return redirect(url_for("forgot_password"))

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE name = ?", (name,))
        user = cur.fetchone()

        if not user:
            conn.close()
            flash("Usuário não encontrado.")
            return redirect(url_for("forgot_password"))

        cur.execute(
            "UPDATE users SET password_hash=?, api_token=? WHERE id=?",
            (generate_password_hash(new_password), user["api_token"] or create_token(), user["id"]),
        )
        conn.commit()
        conn.close()

        flash("Senha atualizada. Entre com a nova senha.")
        return redirect(url_for("login"))

    return render_template("forgot_password.html")

@app.route("/login")
def login_alias():
    return redirect(url_for("login"))

@app.route("/sair")
def logout():
    session.clear()
    flash("Você saiu do bolão.")
    return redirect(url_for("login"))

@app.route("/perfil", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()

    if request.method == "POST":
        file = request.files.get("avatar")

        if file and file.filename and allowed_file(file.filename):
            ext = file.filename.rsplit(".", 1)[1].lower()
            filename = secure_filename(f"user_{user['id']}_{secrets.token_hex(6)}.{ext}")
            file_bytes = file.read()
            mime_type = file.mimetype or f"image/{ext}"

            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "UPDATE users SET avatar=?, avatar_data=?, avatar_mime=? WHERE id=?",
                (filename, file_bytes, mime_type, user["id"])
            )
            conn.commit()
            conn.close()

            session["user_avatar"] = filename
            flash("Foto atualizada com sucesso.")
            return redirect(url_for("profile"))

        flash("Envie uma imagem PNG, JPG, JPEG ou WEBP.")

    user = current_user()
    return render_template("profile.html", user=user)

@app.route("/matches", methods=["GET", "POST"])
def matches_alias():
    return redirect(url_for("matches"))

@app.route("/jogos", methods=["GET", "POST"])
@login_required
def matches():
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        saved = 0

        for key in request.form:
            if key.startswith("home_"):
                match_id = key.replace("home_", "")
                home = request.form.get(f"home_{match_id}")
                away = request.form.get(f"away_{match_id}")

                cur.execute("SELECT locked, finished, match_date FROM matches WHERE id = ?", (match_id,))
                match = cur.fetchone()

                if not match or match["locked"] or match["finished"] or is_match_closed(match["match_date"]):
                    continue

                if home != "" and away != "":
                    try:
                        home_int = int(home)
                        away_int = int(away)
                    except ValueError:
                        continue

                    cur.execute("""
                        INSERT INTO guesses (user_id, match_id, guess_home, guess_away, updated_at)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(user_id, match_id)
                        DO UPDATE SET guess_home=excluded.guess_home, guess_away=excluded.guess_away, updated_at=CURRENT_TIMESTAMP
                    """, (session["user_id"], match_id, home_int, away_int))
                    saved += 1

        conn.commit()
        flash(f"Palpites salvos: {saved}")

    selected_stage = request.args.get("stage", "")
    selected_group = request.args.get("group", "")

    query = """
        SELECT 
            m.*,
            g.guess_home,
            g.guess_away
        FROM matches m
        LEFT JOIN guesses g ON g.match_id = m.id AND g.user_id = ?
        WHERE 1=1
    """
    params = [session["user_id"]]

    if selected_stage:
        query += " AND m.stage = ?"
        params.append(selected_stage)

    if selected_group:
        query += " AND m.group_name = ?"
        params.append(selected_group)

    query += " ORDER BY m.match_date, m.id"

    cur.execute(query, params)
    rows = cur.fetchall()

    cur.execute("SELECT DISTINCT stage FROM matches ORDER BY id")
    stages = [r["stage"] for r in cur.fetchall()]

    cur.execute("SELECT DISTINCT group_name FROM matches WHERE group_name LIKE 'Grupo%' ORDER BY group_name")
    groups = [r["group_name"] for r in cur.fetchall()]

    conn.close()

    return render_template(
        "matches.html",
        matches=rows,
        user_name=session["user_name"],
        stages=stages,
        groups=groups,
        selected_stage=selected_stage,
        selected_group=selected_group,
    )

@app.route("/ranking")
@login_required
def ranking():
    rows = get_ranking_rows()
    podium = rows[:3]
    leader = rows[0] if rows else None
    most_exact = max(rows, key=lambda r: r["exacts"] or 0) if rows else None
    most_active = max(rows, key=lambda r: r["guesses_count"] or 0) if rows else None

    return render_template(
        "ranking.html",
        ranking=rows,
        podium=podium,
        leader=leader,
        most_exact=most_exact,
        most_active=most_active,
    )

@app.route("/regras")
def rules():
    return render_template("rules.html")


@app.route("/meus-palpites")
@login_required
def my_guesses():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(f"""
        SELECT
            m.*,
            g.guess_home,
            g.guess_away,
            {calc_points_sql()} as points
        FROM matches m
        LEFT JOIN guesses g
            ON g.match_id = m.id
            AND g.user_id = ?
        ORDER BY m.match_date, m.id
    """, (session["user_id"],))

    rows = cur.fetchall()
    conn.close()

    total_guesses = sum(1 for r in rows if r["guess_home"] is not None and r["guess_away"] is not None)
    total_points = sum((r["points"] or 0) for r in rows)
    exacts = sum(
        1 for r in rows
        if r["finished"] and r["guess_home"] == r["score_home"] and r["guess_away"] == r["score_away"]
    )
    pending = sum(1 for r in rows if not r["finished"] and r["guess_home"] is not None and r["guess_away"] is not None)

    return render_template(
        "my_guesses.html",
        matches=rows,
        total_guesses=total_guesses,
        total_points=total_points,
        exacts=exacts,
        pending=pending,
        user_name=session.get("user_name")
    )

@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")

        if password == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))

        flash("Senha incorreta.")

    return render_template("admin_login.html")

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_user":
            name = request.form.get("name", "").strip()
            password = request.form.get("password", "").strip() or "123"
            file = request.files.get("avatar")

            if name:
                cur.execute("SELECT id FROM users WHERE lower(name) = lower(?)", (name,))
                existing = cur.fetchone()

                if existing:
                    flash("Já existe um usuário com esse nome.")
                else:
                    cur.execute(
                        "INSERT INTO users (name, password_hash, api_token) VALUES (?, ?, ?)",
                        (name, generate_password_hash(password), create_token())
                    )

                    new_user_id = cur.lastrowid

                    if file and file.filename and allowed_file(file.filename):
                        ext = file.filename.rsplit(".", 1)[1].lower()
                        filename = secure_filename(f"user_{new_user_id}_{secrets.token_hex(6)}.{ext}")
                        file_bytes = file.read()
                        mime_type = file.mimetype or f"image/{ext}"
                        cur.execute(
                            "UPDATE users SET avatar = ?, avatar_data = ?, avatar_mime = ? WHERE id = ?",
                            (filename, file_bytes, mime_type, new_user_id)
                        )

                    flash("Usuário cadastrado com sucesso.")

            conn.commit()
            conn.close()
            return redirect(url_for("admin"))

        if action == "add_match":
            cur.execute("""
                INSERT INTO matches (stage, group_name, team_home, team_away, match_date, location)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                request.form.get("stage", "").strip(),
                request.form.get("group_name", "").strip(),
                request.form.get("team_home", "").strip(),
                request.form.get("team_away", "").strip(),
                request.form.get("match_date", "").strip(),
                request.form.get("location", "").strip(),
            ))
            flash("Jogo cadastrado.")

        elif action == "result":
            match_id = request.form.get("match_id")
            score_home = request.form.get("score_home")
            score_away = request.form.get("score_away")

            if score_home != "" and score_away != "":
                cur.execute("""
                    UPDATE matches
                    SET score_home = ?, score_away = ?, finished = 1, locked = 1
                    WHERE id = ?
                """, (int(score_home), int(score_away), match_id))
                flash("Resultado salvo e palpites bloqueados.")

        elif action == "unlock":
            match_id = request.form.get("match_id")
            cur.execute("""
                UPDATE matches
                SET locked = 0, finished = 0, score_home = NULL, score_away = NULL
                WHERE id = ?
            """, (match_id,))
            flash("Jogo reaberto.")

        elif action == "delete":
            match_id = request.form.get("match_id")
            cur.execute("DELETE FROM guesses WHERE match_id = ?", (match_id,))
            cur.execute("DELETE FROM matches WHERE id = ?", (match_id,))
            flash("Jogo excluído.")

        elif action == "edit":
            match_id = request.form.get("match_id")
            cur.execute("""
                UPDATE matches
                SET stage=?, group_name=?, team_home=?, team_away=?, match_date=?, location=?
                WHERE id=?
            """, (
                request.form.get("stage", "").strip(),
                request.form.get("group_name", "").strip(),
                request.form.get("team_home", "").strip(),
                request.form.get("team_away", "").strip(),
                request.form.get("match_date", "").strip(),
                request.form.get("location", "").strip(),
                match_id,
            ))
            flash("Jogo editado.")

        elif action == "edit_user":
            user_id = request.form.get("user_id")
            name = request.form.get("name", "").strip()
            password = request.form.get("password", "").strip()
            remove_avatar = request.form.get("remove_avatar") == "1"
            file = request.files.get("avatar")

            if user_id and name:
                cur.execute("UPDATE users SET name=? WHERE id=?", (name, user_id))

                if password:
                    cur.execute(
                        "UPDATE users SET password_hash=?, api_token=? WHERE id=?",
                        (generate_password_hash(password), create_token(), user_id)
                    )

                if remove_avatar:
                    cur.execute("UPDATE users SET avatar=NULL, avatar_data=NULL, avatar_mime=NULL WHERE id=?", (user_id,))

                if file and file.filename and allowed_file(file.filename):
                    ext = file.filename.rsplit(".", 1)[1].lower()
                    filename = secure_filename(f"user_{user_id}_{secrets.token_hex(6)}.{ext}")
                    file_bytes = file.read()
                    mime_type = file.mimetype or f"image/{ext}"
                    cur.execute(
                        "UPDATE users SET avatar=?, avatar_data=?, avatar_mime=? WHERE id=?",
                        (filename, file_bytes, mime_type, user_id)
                    )

                if str(session.get("user_id")) == str(user_id):
                    session["user_name"] = name
                    if file and file.filename:
                        session["user_avatar"] = filename
                    if remove_avatar:
                        session["user_avatar"] = None

                flash("Usuário atualizado com sucesso.")

        elif action == "delete_user":
            user_id = request.form.get("user_id")
            if str(session.get("user_id")) == str(user_id):
                flash("Você não pode excluir o usuário que está logado agora.")
            else:
                cur.execute("DELETE FROM guesses WHERE user_id=?", (user_id,))
                cur.execute("DELETE FROM ranking_snapshots WHERE user_id=?", (user_id,))
                cur.execute("DELETE FROM users WHERE id=?", (user_id,))
                flash("Usuário excluído.")

        conn.commit()
        conn.close()
        return redirect(url_for("admin"))

    selected_stage = request.args.get("stage", "")

    query = "SELECT * FROM matches WHERE 1=1"
    params = []

    if selected_stage:
        query += " AND stage = ?"
        params.append(selected_stage)

    query += " ORDER BY match_date, id"

    cur.execute(query, params)
    rows = cur.fetchall()

    cur.execute("SELECT DISTINCT stage FROM matches ORDER BY id")
    stages = [r["stage"] for r in cur.fetchall()]

    cur.execute("""
        SELECT
            u.id,
            u.name,
            u.avatar,
            u.created_at,
            COUNT(g.id) as guesses_count
        FROM users u
        LEFT JOIN guesses g ON g.user_id = u.id
        GROUP BY u.id, u.name, u.avatar, u.created_at
        ORDER BY u.created_at DESC, u.name ASC
    """)
    users = cur.fetchall()

    cur.execute("SELECT COUNT(*) as total FROM guesses")
    total_guesses = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) as total FROM matches WHERE finished = 1")
    finished_games = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) as total FROM matches WHERE finished = 0")
    open_games = cur.fetchone()["total"]

    ranking_rows = get_ranking_rows()
    leader = ranking_rows[0] if ranking_rows else None

    conn.close()

    return render_template(
        "admin.html",
        matches=rows,
        stages=stages,
        selected_stage=selected_stage,
        users=users,
        total_guesses=total_guesses,
        finished_games=finished_games,
        open_games=open_games,
        leader=leader
    )

# -----------------------------
# API para App Mobile
# -----------------------------

@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    password = (data.get("password") or "").strip()

    if not name or not password:
        return jsonify({"error": "Nome e senha são obrigatórios."}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE name=?", (name,))

    if cur.fetchone():
        conn.close()
        return jsonify({"error": "Nome já cadastrado."}), 409

    token = create_token()
    cur.execute("""
        INSERT INTO users (name, password_hash, api_token)
        VALUES (?, ?, ?)
    """, (name, generate_password_hash(password), token))

    conn.commit()
    cur.execute("SELECT id, name, avatar, api_token FROM users WHERE name=?", (name,))
    user = dict(cur.fetchone())
    conn.close()

    return jsonify({"token": token, "user": user})

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    password = (data.get("password") or "").strip()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE name=?", (name,))
    user = cur.fetchone()

    if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], password):
        conn.close()
        return jsonify({"error": "Login inválido."}), 401

    token = user["api_token"] or create_token()
    cur.execute("UPDATE users SET api_token=? WHERE id=?", (token, user["id"]))
    conn.commit()
    conn.close()

    return jsonify({
        "token": token,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "avatar": user["avatar"],
        },
    })

@app.route("/api/me")
def api_me():
    user = require_api_user()

    if not user:
        return jsonify({"error": "Token inválido."}), 401

    return jsonify({
        "id": user["id"],
        "name": user["name"],
        "avatar": user["avatar"],
        "avatar_url": f"/static/uploads/{user['avatar']}" if user["avatar"] else None,
    })

@app.route("/api/matches")
def api_matches():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM matches ORDER BY match_date, id")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    return jsonify(rows)

@app.route("/api/guesses", methods=["POST"])
def api_guesses():
    user = require_api_user()

    if not user:
        return jsonify({"error": "Token inválido."}), 401

    data = request.get_json() or {}
    match_id = data.get("match_id")
    home = data.get("guess_home")
    away = data.get("guess_away")

    if match_id is None or home is None or away is None:
        return jsonify({"error": "match_id, guess_home e guess_away são obrigatórios."}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT locked, finished, match_date FROM matches WHERE id=?", (match_id,))
    match = cur.fetchone()

    if not match:
        conn.close()
        return jsonify({"error": "Jogo não encontrado."}), 404

    if match["locked"] or match["finished"] or is_match_closed(match["match_date"]):
        conn.close()
        return jsonify({"error": "Palpites bloqueados para este jogo."}), 403

    cur.execute("""
        INSERT INTO guesses (user_id, match_id, guess_home, guess_away, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, match_id)
        DO UPDATE SET guess_home=excluded.guess_home, guess_away=excluded.guess_away, updated_at=CURRENT_TIMESTAMP
    """, (user["id"], match_id, int(home), int(away)))

    conn.commit()
    conn.close()

    return jsonify({"ok": True})

@app.route("/api/ranking")
def api_ranking():
    return jsonify(get_ranking_rows())

@app.route("/api/avatar", methods=["POST"])
def api_avatar():
    user = require_api_user()

    if not user:
        return jsonify({"error": "Token inválido."}), 401

    file = request.files.get("avatar")

    if not file or not file.filename or not allowed_file(file.filename):
        return jsonify({"error": "Envie uma imagem PNG, JPG, JPEG ou WEBP."}), 400

    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = secure_filename(f"user_{user['id']}_{secrets.token_hex(6)}.{ext}")
    file.save(UPLOAD_FOLDER / filename)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET avatar=? WHERE id=?", (filename, user["id"]))
    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "avatar": filename,
        "avatar_url": f"/static/uploads/{filename}",
    })


@app.route("/compartilhar")
def compartilhar():
    site_url = request.url_root.rstrip("/")
    message = f"🏆 Entre no Bolão da Copa 2026! Faça seus palpites e dispute o ranking: {site_url}"
    whatsapp_url = "https://wa.me/?text=" + quote(message)

    return render_template(
        "share.html",
        site_url=site_url,
        whatsapp_url=whatsapp_url
    )


@app.route("/feed")
@login_required
def feed():
    conn = get_db()
    cur = conn.cursor()

    events = []

    cur.execute("""
        SELECT
            u.name,
            u.avatar,
            m.team_home,
            m.team_away,
            g.guess_home,
            g.guess_away,
            m.score_home,
            m.score_away,
            m.finished,
            g.updated_at
        FROM guesses g
        JOIN users u ON u.id = g.user_id
        JOIN matches m ON m.id = g.match_id
        ORDER BY g.updated_at DESC
        LIMIT 25
    """)

    for row in cur.fetchall():
        if row["finished"] and row["guess_home"] == row["score_home"] and row["guess_away"] == row["score_away"]:
            icon = "🎯"
            text = f'{row["name"]} cravou {display_team_name(row["team_home"])} {row["guess_home"]} x {row["guess_away"]} {display_team_name(row["team_away"])}'
        else:
            icon = "⚽"
            text = f'{row["name"]} fez um palpite em {display_team_name(row["team_home"])} x {display_team_name(row["team_away"])}'

        events.append({
            "icon": icon,
            "text": text,
            "avatar": row["avatar"],
            "created_at": row["updated_at"]
        })

    ranking_rows = get_ranking_rows()

    if ranking_rows:
        leader = ranking_rows[0]
        events.insert(0, {
            "icon": "👑",
            "text": f'{leader["name"]} está liderando com {leader["points"]} pontos!',
            "avatar": leader["avatar"],
            "created_at": ""
        })

    conn.close()

    return render_template("feed.html", events=events)


@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name": "Bolão da Copa 2026",
        "short_name": "Bolão 2026",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#02130c",
        "theme_color": "#06281a",
        "description": "Bolão da Copa 2026 com ranking, palpites e gamificação.",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    })


@app.route("/service-worker.js")
def service_worker():
    response = make_response("""
const CACHE_NAME = 'bolao-copa-2026-v1';

self.addEventListener('install', event => {
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', event => {
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});
""")
    response.headers["Content-Type"] = "application/javascript"
    return response


@app.route("/api/live-summary")
def api_live_summary():
    ranking_rows = get_ranking_rows()
    leader = ranking_rows[0] if ranking_rows else None

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as total FROM users")
    total_users = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) as total FROM guesses")
    total_guesses = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) as total FROM matches WHERE finished = 1")
    finished_games = cur.fetchone()["total"]

    conn.close()

    return jsonify({
        "leader": dict(leader) if leader else None,
        "total_users": total_users,
        "total_guesses": total_guesses,
        "finished_games": finished_games
    })

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
