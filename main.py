from flask import Flask, render_template_string, request, redirect, session, url_for
import psycopg
import os
from functools import wraps
from pathlib import Path
from datetime import date

app = Flask(__name__)

from dotenv import load_dotenv
if Path(".env").exists():
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")

DB_URL = os.getenv("DATABASE_URL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY")

if not DB_URL:
    raise ValueError("DATABASE_URL is not set")

if not ADMIN_PASSWORD:
    raise ValueError("ADMIN_PASSWORD is not set")

if not SECRET_KEY:
    raise ValueError("SECRET_KEY is not set")

app.config["SECRET_KEY"] = SECRET_KEY

def admin_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped_view
@app.route("/login", methods=["GET", "POST"])

def login():
    error = None

    if request.method == "POST":
        password = request.form.get("password", "")

        if password == ADMIN_PASSWORD:
            session["is_admin"] = True
            next_url = request.args.get("next") or "/"
            return redirect(next_url)
        else:
            error = "Forkert kode"

    html = """
    <!doctype html>
    <html lang="da">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Login</title>
        <style>
            * {
                box-sizing: border-box;
            }

            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 16px;
                background: #f5f5f5;
                color: #111;
            }

            .container {
                max-width: 420px;
                margin: 60px auto;
            }

            .card {
                background: white;
                border-radius: 14px;
                padding: 20px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
            }

            h1 {
                margin-top: 0;
            }

            label {
                display: block;
                font-weight: 600;
                margin-bottom: 6px;
            }

            input[type="password"] {
                width: 100%;
                padding: 12px;
                border-radius: 10px;
                border: 1px solid #ccc;
                font-size: 16px;
                margin-bottom: 14px;
            }

            button {
                width: 100%;
                padding: 14px;
                background: #111;
                color: white;
                border: none;
                border-radius: 12px;
                font-weight: bold;
                font-size: 16px;
                cursor: pointer;
            }

            .error {
                color: #b00020;
                margin-bottom: 12px;
                font-size: 14px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <h1>Admin login</h1>

                {% if error %}
                    <div class="error">{{ error }}</div>
                {% endif %}

                <form method="post">
                    <label for="password">Kode</label>
                    <input type="password" id="password" name="password" required>
                    <button type="submit">Log ind</button>
                </form>
            </div>
        </div>
    </body>
    </html>
    """

    return render_template_string(html, error=error)

@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.get("/")
def home():
    current_year = date.today().year

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select
                    r.id,
                    r.round_date,
                    c.name
                from rounds r
                join courses c on c.id = r.course_id
                order by r.round_date desc
                limit 1;
            """)
            latest_round = cur.fetchone()

            cur.execute("""
                select
                    p.full_name,
                    coalesce(sum(rp.season_points), 0) as total_points
                from players p
                left join round_players rp
                    on rp.player_id = p.id
                left join rounds r
                    on r.id = rp.round_id
                    and r.season_year = %s
                where p.is_active = true
                  and (r.id is not null or rp.id is null)
                group by p.id, p.full_name
                order by total_points desc, p.full_name
                limit 3;
            """, (current_year,))
            top_points = cur.fetchall()

            cur.execute("""
                select
                    p.full_name,
                    coalesce(sum(rp.money_rank), 0) as total_money
                from players p
                left join round_players rp
                    on rp.player_id = p.id
                left join rounds r
                    on r.id = rp.round_id
                    and r.season_year = %s
                where p.is_active = true
                  and (r.id is not null or rp.id is null)
                group by p.id, p.full_name
                order by total_money desc, p.full_name
                limit 3;
            """, (current_year,))
            top_money = cur.fetchall()

            cur.execute("""
                select count(*)
                from rounds;
            """)
            total_rounds = cur.fetchone()[0]

    html = """
    <!doctype html>
    <html lang="da">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Golf Liga</title>
        <style>
            * {
                box-sizing: border-box;
            }

            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 16px;
                background: #f5f5f5;
                color: #111;
            }

            .container {
                max-width: 900px;
                margin: 0 auto;
            }

            .navbar {
                display: flex;
                justify-content: space-around;
                align-items: center;
                background: #111;
                padding: 12px;
                border-radius: 12px;
                margin-bottom: 20px;
                gap: 8px;
                flex-wrap: wrap;
            }

            .navbar a {
                color: white;
                text-decoration: none;
                font-weight: 600;
                font-size: 14px;
                padding: 8px 10px;
                border-radius: 8px;
            }

            .navbar a.active {
                background: white;
                color: #111;
            }

            .hero {
                background: white;
                border-radius: 18px;
                padding: 24px;
                margin-bottom: 16px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
            }

            .hero h1 {
                margin: 0 0 8px 0;
                font-size: 32px;
            }

            .hero p {
                margin: 0;
                color: #555;
                font-size: 16px;
            }

            .hero-actions {
                display: flex;
                gap: 12px;
                flex-wrap: wrap;
                margin-top: 20px;
            }

            .primary-btn,
            .secondary-btn {
                display: inline-block;
                text-decoration: none;
                padding: 14px 18px;
                border-radius: 12px;
                font-weight: 700;
            }

            .primary-btn {
                background: #111;
                color: white;
            }

            .secondary-btn {
                background: #ececec;
                color: #111;
            }

            .grid {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 16px;
                margin-bottom: 16px;
            }

            .card {
                background: white;
                border-radius: 16px;
                padding: 18px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
            }

            .card h2 {
                margin-top: 0;
                margin-bottom: 12px;
                font-size: 20px;
            }

            .meta-number {
                font-size: 34px;
                font-weight: 700;
                margin: 4px 0 0 0;
            }

            .muted {
                color: #666;
                font-size: 14px;
            }

            .list {
                display: flex;
                flex-direction: column;
                gap: 10px;
            }

            .list-row {
                display: flex;
                justify-content: space-between;
                gap: 12px;
                padding: 10px 0;
                border-bottom: 1px solid #eee;
            }

            .list-row:last-child {
                border-bottom: none;
            }

            .list-row div:last-child {
                font-weight: 700;
            }

            .forum-box {
                background: white;
                border-radius: 16px;
                padding: 18px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
            }

            .forum-tag {
                display: inline-block;
                margin-top: 10px;
                padding: 8px 10px;
                border-radius: 999px;
                background: #f0f0f0;
                font-size: 13px;
                font-weight: 600;
                color: #444;
            }

            @media (max-width: 700px) {
                .grid {
                    grid-template-columns: 1fr;
                }

                .hero h1 {
                    font-size: 26px;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">

            <div class="navbar">
                <a href="/" class="active">Forside</a>
                <a href="/new">Ny runde</a>
                <a href="/rounds">Runder</a>
                <a href="/stats">Statistik</a>
                <a href="/logout">Log ud</a>
            </div>

            <div class="hero">
                <h1>Golf Liga</h1>
                <p>Overblik over runder, statistik og stilling.</p>

                <div class="hero-actions">
                    <a href="/new" class="primary-btn">Opret ny runde</a>
                    <a href="/rounds" class="secondary-btn">Se alle runder</a>
                    <a href="/stats" class="secondary-btn">Se statistik</a>
                </div>
            </div>

            <div class="grid">
                <div class="card">
                    <div class="muted">Antal registrerede runder</div>
                    <div class="meta-number">{{ total_rounds }}</div>
                </div>

                <div class="card">
                    <h2>Seneste runde</h2>
                    {% if latest_round %}
                        <div class="list">
                            <div><b>Dato:</b> {{ latest_round[1] }}</div>
                            <div><b>Bane:</b> {{ latest_round[2] }}</div>
                            <div style="margin-top: 12px;">
                                <a href="/round/{{ latest_round[0] }}" class="secondary-btn">Se resultat</a>
                            </div>
                        </div>
                    {% else %}
                        <div class="muted">Ingen runder endnu.</div>
                    {% endif %}
                </div>
            </div>

            <div class="grid">
                <div class="card">
                    <h2>Top 3 – Point</h2>

                    {% if top_points %}
                        <div class="list">
                            {% for p in top_points %}
                                <div class="list-row">
                                    <div>{{ loop.index }}. {{ p[0] }}</div>
                                    <div>{{ p[1] }} point</div>
                                </div>
                            {% endfor %}
                        </div>
                    {% else %}
                        <div class="muted">Ingen data endnu.</div>
                    {% endif %}
                </div>

                <div class="card">
                    <h2>Top 3 – Fake money</h2>

                    {% if top_money %}
                        <div class="list">
                            {% for p in top_money %}
                                <div class="list-row">
                                    <div>{{ loop.index }}. {{ p[0] }}</div>
                                    <div>{{ p[1] }} kr</div>
                                </div>
                            {% endfor %}
                        </div>
                    {% else %}
                        <div class="muted">Ingen data endnu.</div>
                    {% endif %}
                </div>
            </div>

            <div class="forum-box">
                <h2>Forum</h2>
                <div class="muted">
                    Her kommer der snart et lille forum
                </div>
                <div class="forum-tag">Kommer senere</div>
            </div>

        </div>
    </body>
    </html>
    """

    return render_template_string(
        html,
        latest_round=latest_round,
        top_points=top_points,
        top_money=top_money,
        total_rounds=total_rounds,
    )

@app.get("/new")
def new_round():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("select id, name from courses order by name;")
            courses = cur.fetchall()

            cur.execute("select id, full_name from players where is_active = true order by full_name;")
            players = cur.fetchall()

    html = """
    <!doctype html>
    <html lang="da">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Ny runde</title>
        <style>
            * {
                box-sizing: border-box;
            }

            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 16px;
                background: #f5f5f5;
                color: #111;
            }

            .container {
                max-width: 760px;
                margin: 0 auto;
            }

            .navbar {
                display: flex;
                justify-content: space-around;
                background: #111;
                padding: 12px;
                border-radius: 12px;
                margin-bottom: 16px;
                gap: 8px;
                flex-wrap: wrap;
            }

            .navbar a {
                color: white;
                text-decoration: none;
                font-weight: 600;
                font-size: 14px;
                padding: 8px 10px;
                border-radius: 8px;
            }

            .navbar a.active {
                background: white;
                color: #111;
            }

            .card {
                background: white;
                border-radius: 14px;
                padding: 16px;
                margin-bottom: 16px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
            }

            .field {
                margin-bottom: 14px;
            }

            label {
                display: block;
                font-weight: 600;
                margin-bottom: 6px;
            }

            input[type="date"],
            input[type="number"],
            select {
                width: 100%;
                padding: 12px;
                border-radius: 10px;
                border: 1px solid #ccc;
                font-size: 16px;
                background: white;
            }

            .checkbox-row {
                display: flex;
                align-items: center;
                gap: 10px;
                margin-top: 8px;
            }

            .checkbox-row input {
                width: 18px;
                height: 18px;
            }

            .player-card {
                background: white;
                border-radius: 14px;
                padding: 14px;
                margin-bottom: 12px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
            }

            .player-name {
                font-weight: 700;
                margin-bottom: 12px;
                font-size: 17px;
            }

            .player-grid {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 12px;
            }

            .hint {
                color: #555;
                font-size: 14px;
                margin-top: 10px;
            }

            button {
                width: 100%;
                padding: 14px;
                background: #111;
                color: white;
                border: none;
                border-radius: 12px;
                font-weight: bold;
                font-size: 16px;
                cursor: pointer;
            }

            @media (max-width: 480px) {
                .player-grid {
                    grid-template-columns: 1fr;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">

            <div class="navbar">
                <a href="/">Forside</a>
                <a href="/new" class="active">Ny runde</a>
                <a href="/rounds">Runder</a>
                <a href="/stats">Statistik</a>
                <a href="/logout">Log ud</a>
            </div>

            <h1>Ny runde</h1>

            <form method="post" action="/save">
                <div class="card">
                    <div class="field">
                        <label for="round_date">Dato</label>
                        <input type="date" id="round_date" name="round_date" required>
                    </div>

                    <div class="field">
                        <label for="course_id">Bane</label>
                        <select id="course_id" name="course_id" required>
                            {% for c in courses %}
                                <option value="{{ c[0] }}">{{ c[1] }}</option>
                            {% endfor %}
                        </select>
                    </div>

                    <div class="checkbox-row">
                        <input type="checkbox" id="closest_to_pin_active" name="closest_to_pin_active" onchange="toggleCtpFields()">
                        <label for="closest_to_pin_active" style="margin: 0;">Nærmest pinden</label>
                    </div>

                    <div class="hint">Tom stableford betyder DNP.</div>
                </div>

                <h2>Spillere</h2>

                {% for p in players %}
                    <div class="player-card">
                        <div class="player-name">{{ p[1] }}</div>

                        <div class="player-grid">
                            <div class="field">
                                <label for="score_{{ p[0] }}">Stableford</label>
                                <input type="number" id="score_{{ p[0] }}" name="score_{{ p[0] }}" min="0" max="60">
                            </div>

                            <div class="field ctp-field" style="display: none;">
                                <label for="ctp_{{ p[0] }}">Closest (cm)</label>
                                <input type="number" id="ctp_{{ p[0] }}" name="ctp_{{ p[0] }}" min="0">
                            </div>
                        </div>
                    </div>
                {% endfor %}

                <button type="submit">Gem runde</button>
            </form>
        </div>

        <script>
            function toggleCtpFields() {
                const isActive = document.getElementById("closest_to_pin_active").checked;
                const ctpFields = document.querySelectorAll(".ctp-field");

                ctpFields.forEach(field => {
                    field.style.display = isActive ? "block" : "none";
                });
            }

            document.addEventListener("DOMContentLoaded", function () {
                toggleCtpFields();
            });
        </script>
    </body>
    </html>
    """

    return render_template_string(html, courses=courses, players=players)

@app.post("/save")
@admin_required
def save_round():
    round_date = request.form.get("round_date")
    course_id = int(request.form.get("course_id"))
    closest_to_pin_active = request.form.get("closest_to_pin_active") == "on"

    season_year = int(round_date[:4])

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into rounds (round_date, season_year, course_id, closest_to_pin_active)
                values (%s, %s, %s, %s)
                returning id;
                """,
                (round_date, season_year, course_id, closest_to_pin_active)
            )
            round_id = cur.fetchone()[0]

            cur.execute(
                """
                insert into round_players (round_id, player_id)
                select %s, id from players where is_active = true;
                """,
                (round_id,)
            )

            upsert_round_players(cur, round_id, request.form)
            recalculate_round(cur, round_id)

        conn.commit()

    return redirect(f"/round/{round_id}")

@app.get("/rounds")
def list_rounds():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select
                    r.id,
                    r.round_date,
                    c.name
                from rounds r
                join courses c on c.id = r.course_id
                order by r.round_date desc
            """)
            rounds = cur.fetchall()

        html = """
    <!doctype html>
    <html lang="da">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Runder</title>
        <style>
            * {
                box-sizing: border-box;
            }

            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 16px;
                background: #f5f5f5;
                color: #111;
            }

            .container {
                max-width: 760px;
                margin: 0 auto;
            }

            .navbar {
                display: flex;
                justify-content: space-around;
                background: #111;
                padding: 12px;
                border-radius: 12px;
                margin-bottom: 16px;
            }

            .navbar a {
                color: white;
                text-decoration: none;
                font-weight: 600;
                font-size: 14px;
            }

            .card {
                background: white;
                border-radius: 14px;
                padding: 16px;
                margin-bottom: 12px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
            }

            .round-link {
                text-decoration: none;
                color: #111;
                font-weight: 600;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="navbar">
                <a href="/">Forside</a>
                <a href="/new" class="active">Ny runde</a>
                <a href="/rounds">Runder</a>
                <a href="/stats">Statistik</a>
                <a href="/logout">Log ud</a>
            </div>

            <h1>Runder</h1>

            {% for r in rounds %}
                <div class="card">
                    <a class="round-link" href="/round/{{ r[0] }}">
                        {{ r[1] }} - {{ r[2] }}
                    </a>
                </div>
            {% endfor %}
        </div>
    </body>
    </html>
    """

    return render_template_string(html, rounds=rounds)
    
@app.get("/round/<int:round_id>")
def show_round(round_id):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                select
                    r.round_date,
                    r.season_year,
                    r.prize_pool,
                    c.name
                from rounds r
                join courses c on c.id = r.course_id
                where r.id = %s;
                """,
                (round_id,)
            )
            round_info = cur.fetchone()

            if not round_info:
                return "Runden blev ikke fundet", 404

            round_date, season_year, prize_pool, course_name = round_info

            cur.execute(
                """
                select
                    p.full_name,
                    rp.status,
                    rp.stableford_points,
                    rp.position,
                    rp.season_points,
                    rp.money_rank,
                    rp.closest_to_pin_cm
                from round_players rp
                join players p on p.id = rp.player_id
                where rp.round_id = %s
                order by
                    rp.position nulls last,
                    rp.stableford_points desc nulls last,
                    p.full_name;
                """,
                (round_id,)
            )
            daily_rows = cur.fetchall()

            cur.execute(
                """
                select
                    p.full_name,
                    coalesce(sum(case when r.season_year = %s then rp.season_points else 0 end), 0) as total_points,
                    coalesce(sum(case when r.season_year = %s then rp.money_rank else 0 end), 0) as total_money,
                    count(*) filter (
                        where r.season_year = %s
                        and rp.status = 'played'
                    ) as rounds_played
                from players p
                left join round_players rp
                    on rp.player_id = p.id
                left join rounds r
                    on r.id = rp.round_id
                where p.is_active = true
                group by p.id, p.full_name
                order by total_points desc, total_money desc, rounds_played desc, p.full_name;
                """,
                (season_year, season_year, season_year)
            )
            season_rows = cur.fetchall()

        html = """
    <!doctype html>
    <html lang="da">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Resultater</title>
        <style>
            * {
                box-sizing: border-box;
            }

            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 16px;
                background: #f5f5f5;
                color: #111;
            }

            .container {
                max-width: 900px;
                margin: 0 auto;
            }

            .navbar {
                display: flex;
                justify-content: space-around;
                background: #111;
                padding: 12px;
                border-radius: 12px;
                margin-bottom: 16px;
            }

            .navbar a {
                color: white;
                text-decoration: none;
                font-weight: 600;
                font-size: 14px;
            }

            .card {
                background: white;
                border-radius: 14px;
                padding: 16px;
                margin-bottom: 16px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
            }

            h1, h2 {
                margin-top: 0;
            }

            .table-wrap {
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
            }

            table {
                width: 100%;
                min-width: 640px;
                border-collapse: collapse;
                background: white;
            }

            th, td {
                padding: 10px 12px;
                border-bottom: 1px solid #e5e5e5;
                text-align: left;
                white-space: nowrap;
            }

            th {
                background: #fafafa;
            }

            a.button-link {
                display: inline-block;
                text-decoration: none;
                background: #111;
                color: white;
                padding: 12px 16px;
                border-radius: 12px;
                font-weight: 700;
            }

            .meta {
                line-height: 1.7;
            }
        </style>
    </head>
    <body>
        <div class="container">

            <div class="navbar">
                <a href="/">Forside</a>
                <a href="/new" class="active">Ny runde</a>
                <a href="/rounds">Runder</a>
                <a href="/stats">Statistik</a>
                <a href="/logout">Log ud</a>
            </div>

            <h1>Resultater</h1>

            <div class="card meta">
                <b>Dato:</b> {{ round_date }}<br>
                <b>Bane:</b> {{ course_name }}<br>
                <b>Sæson:</b> {{ season_year }}<br>
                <b>Dagens præmiepulje:</b> {{ prize_pool }}
            </div>

            <div class="card">
                <h2>Dagens resultat</h2>
                <div class="table-wrap">
                    <table>
                        <tr>
                            <th>Pos</th>
                            <th>Spiller</th>
                            <th>Status</th>
                            <th>Stableford</th>
                            <th>Point</th>
                            <th>Money</th>
                            <th>Closest</th>
                        </tr>

                        {% for r in daily_rows %}
                        <tr>
                            <td>{{ r[3] if r[3] is not none else "" }}</td>
                            <td>{{ r[0] }}</td>
                            <td>{{ r[1] }}</td>
                            <td>{{ r[2] if r[2] is not none else "" }}</td>
                            <td>{{ r[4] if r[4] is not none else "" }}</td>
                            <td>{{ r[5] if r[5] is not none else "" }}</td>
                            <td>{{ r[6] if r[6] is not none else "" }}</td>
                        </tr>
                        {% endfor %}
                    </table>
                </div>
            </div>

            <div class="card">
                <h2>Sæson leaderboard</h2>
                <div class="table-wrap">
                    <table>
                        <tr>
                            <th>Pos</th>
                            <th>Spiller</th>
                            <th>Total point</th>
                            <th>Total money</th>
                            <th>Spillede runder</th>
                        </tr>

                        {% for r in season_rows %}
                        <tr>
                            <td>{{ loop.index }}</td>
                            <td>{{ r[0] }}</td>
                            <td>{{ r[1] }}</td>
                            <td>{{ r[2] }}</td>
                            <td>{{ r[3] }}</td>
                        </tr>
                        {% endfor %}
                    </table>
                </div>
            </div>

            <div style="display: flex; gap: 12px; flex-wrap: wrap;">
                <a class="button-link" href="/">Ny runde</a>
                <a class="button-link" href="/round/{{ round_id }}/edit">Ret runde</a>

                <form method="post" action="/round/{{ round_id }}/delete" onsubmit="return confirm('Slet runden?');" style="margin: 0;">
                    <button type="submit" style="width: auto;">Slet runde</button>
                </form>
            </div>
        </div>
    </body>
    </html>
    """

    return render_template_string(
        html,
        round_id=round_id,
        round_date=round_date,
        season_year=season_year,
        prize_pool=prize_pool,
        course_name=course_name,
        daily_rows=daily_rows,
        season_rows=season_rows,
    )

@app.get("/round/<int:round_id>/edit")
@admin_required
def edit_round(round_id):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("select id, name from courses order by name;")
            courses = cur.fetchall()

            cur.execute(
                """
                select id, round_date, course_id, closest_to_pin_active
                from rounds
                where id = %s;
                """,
                (round_id,)
            )
            round_row = cur.fetchone()

            if not round_row:
                return "Runden blev ikke fundet", 404

            cur.execute(
                """
                select
                    p.id,
                    p.full_name,
                    rp.stableford_points,
                    rp.closest_to_pin_cm
                from round_players rp
                join players p on p.id = rp.player_id
                where rp.round_id = %s
                order by p.full_name;
                """,
                (round_id,)   # FIX: kun én parameter
            )
            player_rows = cur.fetchall()

    html = """
    <!doctype html>
    <html lang="da">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Ret runde</title>
        <style>
            * { box-sizing: border-box; }

            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 16px;
                background: #f5f5f5;
                color: #111;
            }

            .container {
                max-width: 760px;
                margin: 0 auto;
            }

            .navbar {
                display: flex;
                justify-content: space-around;
                background: #111;
                padding: 12px;
                border-radius: 12px;
                margin-bottom: 16px;
            }

            .navbar a {
                color: white;
                text-decoration: none;
                font-weight: 600;
                font-size: 14px;
            }

            .card {
                background: white;
                border-radius: 14px;
                padding: 16px;
                margin-bottom: 16px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.06);
            }

            .field {
                margin-bottom: 14px;
            }

            label {
                display: block;
                font-weight: 600;
                margin-bottom: 6px;
            }

            input[type="date"],
            input[type="number"],
            select {
                width: 100%;
                padding: 12px;
                border-radius: 10px;
                border: 1px solid #ccc;
                font-size: 16px;
                background: white;
            }

            .checkbox-row {
                display: flex;
                align-items: center;
                gap: 10px;
                margin-top: 8px;
            }

            .player-card {
                background: white;
                border-radius: 14px;
                padding: 14px;
                margin-bottom: 12px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.06);
            }

            .player-name {
                font-weight: 700;
                margin-bottom: 12px;
                font-size: 17px;
            }

            .player-grid {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 12px;
            }

            button {
                width: 100%;
                padding: 14px;
                background: #111;
                color: white;
                border: none;
                border-radius: 12px;
                font-weight: bold;
                font-size: 16px;
                cursor: pointer;
            }

            @media (max-width: 480px) {
                .player-grid {
                    grid-template-columns: 1fr;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">

            <div class="navbar">
                <a href="/">Forside</a>
                <a href="/new">Ny runde</a>
                <a href="/rounds">Runder</a>
                <a href="/stats" class="active">Statistik</a>
                <a href="/logout">Log ud</a>
            </div>

            <h1>Ret runde</h1>

            <form method="post" action="/round/{{ round_row[0] }}/edit">
                <div class="card">
                    <div class="field">
                        <label for="round_date">Dato</label>
                        <input type="date" id="round_date" name="round_date" value="{{ round_row[1] }}" required>
                    </div>

                    <div class="field">
                        <label for="course_id">Bane</label>
                        <select id="course_id" name="course_id" required>
                            {% for c in courses %}
                                <option value="{{ c[0] }}" {% if c[0] == round_row[2] %}selected{% endif %}>
                                    {{ c[1] }}
                                </option>
                            {% endfor %}
                        </select>
                    </div>

                    <div class="checkbox-row">
                        <input type="checkbox" id="closest_to_pin_active" name="closest_to_pin_active"
                            {% if round_row[3] %}checked{% endif %}
                            onchange="toggleCtpFields()">
                        <label for="closest_to_pin_active" style="margin: 0;">Nærmest pinden</label>
                    </div>
                </div>

                <h2>Spillere</h2>

                {% for p in player_rows %}
                    <div class="player-card">
                        <div class="player-name">{{ p[1] }}</div>

                        <div class="player-grid">
                            <div class="field">
                                <label for="score_{{ p[0] }}">Stableford</label>
                                <input type="number"
                                       id="score_{{ p[0] }}"
                                       name="score_{{ p[0] }}"
                                       min="0"
                                       max="60"
                                       value="{{ p[2] if p[2] is not none else '' }}">
                            </div>

                            <div class="field ctp-field" style="display: none;">
                                <label for="ctp_{{ p[0] }}">Closest (cm)</label>
                                <input type="number"
                                    id="ctp_{{ p[0] }}"
                                    name="ctp_{{ p[0] }}"
                                    min="0"
                                    value="{{ p[3] if p[3] is not none else '' }}">
                            </div>
                        </div>
                    </div>
                {% endfor %}

                <button type="submit">Gem ændringer</button>
            </form>
        </div>
        <script>
            function toggleCtpFields() {
                const isActive = document.getElementById("closest_to_pin_active").checked;
                const ctpFields = document.querySelectorAll(".ctp-field");

                ctpFields.forEach(field => {
                    field.style.display = isActive ? "block" : "none";
                });
            }

            document.addEventListener("DOMContentLoaded", function () {
                toggleCtpFields();
            });
        </script>
    </body>
    </html>
    """

    return render_template_string(
        html,
        round_row=round_row,
        courses=courses,
        player_rows=player_rows,
    )

def recalculate_round(cur, round_id):
    cur.execute(
        """
        with ranked as (
            select
                id,
                rank() over (
                    order by stableford_points desc
                ) as pos
            from round_players
            where round_id = %s
              and status = 'played'
        )
        update round_players rp
        set position = ranked.pos
        from ranked
        where rp.id = ranked.id;
        """,
        (round_id,)
    )

    cur.execute(
        """
        with point_table(pos, pts) as (
            values
                (1, 12.0::numeric),
                (2, 9.0::numeric),
                (3, 8.0::numeric),
                (4, 7.0::numeric),
                (5, 6.0::numeric),
                (6, 5.0::numeric),
                (7, 4.0::numeric),
                (8, 3.0::numeric),
                (9, 2.0::numeric),
                (10, 1.0::numeric)
        ),
        tie_groups as (
            select
                position,
                count(*) as tie_count
            from round_players
            where round_id = %s
              and status = 'played'
              and position is not null
            group by position
        ),
        tie_points as (
            select
                tg.position,
                tg.tie_count,
                coalesce(avg(pt.pts), 0) as avg_points
            from tie_groups tg
            left join point_table pt
                on pt.pos between tg.position and tg.position + tg.tie_count - 1
            group by tg.position, tg.tie_count
        )
        update round_players rp
        set season_points = tp.avg_points
        from tie_points tp
        where rp.round_id = %s
          and rp.status = 'played'
          and rp.position = tp.position;
        """,
        (round_id, round_id)
    )

    cur.execute(
        """
        with prize_table(pos, pct) as (
            values
                (1, 23.0::numeric),
                (2, 19.0::numeric),
                (3, 16.0::numeric),
                (4, 13.0::numeric),
                (5, 10.0::numeric),
                (6, 7.0::numeric),
                (7, 5.0::numeric),
                (8, 4.0::numeric),
                (9, 2.0::numeric),
                (10, 1.0::numeric)
        ),
        round_pool as (
            select prize_pool
            from rounds
            where id = %s
        ),
        tie_groups as (
            select
                position,
                count(*) as tie_count
            from round_players
            where round_id = %s
              and status = 'played'
              and position is not null
            group by position
        ),
        tie_prizes as (
            select
                tg.position,
                tg.tie_count,
                coalesce(sum(pt.pct), 0) as total_pct
            from tie_groups tg
            left join prize_table pt
                on pt.pos between tg.position and tg.position + tg.tie_count - 1
            group by tg.position, tg.tie_count
        )
        update round_players rp
        set money_rank = round(
            (rp_pool.prize_pool * (tp.total_pct / 100.0)) / tp.tie_count,
            2
        )
        from tie_prizes tp
        cross join round_pool rp_pool
        where rp.round_id = %s
          and rp.status = 'played'
          and rp.position = tp.position;
        """,
        (round_id, round_id, round_id)
    )

    cur.execute(
        """
        update round_players
        set
            position = null,
            season_points = null,
            money_rank = null
        where round_id = %s
          and status = 'dnp';
        """,
        (round_id,)
    )

def upsert_round_players(cur, round_id, form_data):
    cur.execute(
        """
        update round_players
        set
            status = 'dnp',
            stableford_points = null,
            closest_to_pin_cm = null,
            position = null,
            season_points = null,
            money_rank = null
        where round_id = %s;
        """,
        (round_id,)
    )

    for key in form_data:
        if not key.startswith("score_"):
            continue

        player_id = int(key.split("_")[1])
        score = form_data.get(key)
        ctp = form_data.get(f"ctp_{player_id}")

        if score:
            cur.execute(
                """
                update round_players
                set
                    status = 'played',
                    stableford_points = %s,
                    closest_to_pin_cm = %s
                where round_id = %s
                  and player_id = %s;
                """,
                (
                    int(score),
                    int(ctp) if ctp else None,
                    round_id,
                    player_id,
                )
            )

@app.post("/round/<int:round_id>/edit")
@admin_required
def update_round(round_id):
    round_date = request.form.get("round_date")
    course_id = int(request.form.get("course_id"))
    closest_to_pin_active = request.form.get("closest_to_pin_active") == "on"
    season_year = int(round_date[:4])

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update rounds
                set round_date = %s,
                    season_year = %s,
                    course_id = %s,
                    closest_to_pin_active = %s
                where id = %s;
                """,
                (round_date, season_year, course_id, closest_to_pin_active, round_id)
            )

            upsert_round_players(cur, round_id, request.form)
            recalculate_round(cur, round_id)

        conn.commit()

    return redirect(f"/round/{round_id}")

@app.post("/round/<int:round_id>/delete")
@admin_required
def delete_round(round_id):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("delete from round_players where round_id = %s;", (round_id,))
            cur.execute("delete from rounds where id = %s;", (round_id,))
        conn.commit()

    return redirect("/rounds")

@app.get("/stats")
def stats():
    sort = request.args.get("sort", "wins")
    direction = request.args.get("direction", "desc")

    allowed_sorts = {
        "name": "p.full_name",
        "rounds_played": "rounds_played",
        "avg_stableford": "avg_stableford",
        "best_stableford": "best_stableford",
        "wins": "wins",
        "top3": "top3",
        "total_points": "total_points",
        "total_money": "total_money",
    }

    allowed_directions = {"asc", "desc"}

    order_by = allowed_sorts.get(sort, "wins")
    order_direction = direction if direction in allowed_directions else "desc"

    query = f"""
        select
            p.full_name,
            count(*) filter (where rp.status = 'played') as rounds_played,
            round(avg(rp.stableford_points) filter (where rp.status = 'played'), 2) as avg_stableford,
            max(rp.stableford_points) as best_stableford,
            count(*) filter (where rp.position = 1) as wins,
            count(*) filter (
                where rp.position <= 3
                and rp.position is not null
            ) as top3,
            coalesce(sum(rp.season_points), 0) as total_points,
            coalesce(sum(rp.money_rank), 0) as total_money
        from players p
        left join round_players rp
            on rp.player_id = p.id
        where p.is_active = true
        group by p.id, p.full_name
        order by {order_by} {order_direction} nulls last, p.full_name;
    """

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            player_stats = cur.fetchall()

    html = """
    <!doctype html>
    <html lang="da">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Statistik</title>
        <style>
            * {
                box-sizing: border-box;
            }

            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 16px;
                background: #f5f5f5;
                color: #111;
            }

            .container {
                max-width: 1000px;
                margin: 0 auto;
            }

            .navbar {
                display: flex;
                justify-content: space-around;
                background: #111;
                padding: 12px;
                border-radius: 12px;
                margin-bottom: 16px;
            }

            .navbar a {
                color: white;
                text-decoration: none;
                font-weight: 600;
                font-size: 14px;
            }

            .card {
                background: white;
                border-radius: 14px;
                padding: 16px;
                margin-bottom: 16px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
            }

            h1, h2 {
                margin-top: 0;
            }

            .table-wrap {
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
            }

            table {
                width: 100%;
                min-width: 900px;
                border-collapse: collapse;
                background: white;
            }

            th, td {
                padding: 10px 12px;
                border-bottom: 1px solid #e5e5e5;
                text-align: left;
                white-space: nowrap;
            }

            th {
                background: #fafafa;
            }

            .muted {
                color: #666;
                font-size: 14px;
            }

            .sort-bar {
                display: flex;
                gap: 12px;
                flex-wrap: wrap;
                align-items: end;
            }

            .field {
                min-width: 220px;
            }

            label {
                display: block;
                font-weight: 600;
                margin-bottom: 6px;
            }

            select {
                width: 100%;
                padding: 12px;
                border-radius: 10px;
                border: 1px solid #ccc;
                font-size: 16px;
                background: white;
            }

            button {
                padding: 12px 18px;
                background: #111;
                color: white;
                border: none;
                border-radius: 12px;
                font-weight: bold;
                font-size: 16px;
                cursor: pointer;
            }
        </style>
    </head>
    <body>
        <div class="container">

            <div class="navbar">
                <a href="/">Forside</a>
                <a href="/new">Ny runde</a>
                <a href="/rounds">Runder</a>
                <a href="/stats">Statistik</a>
                <a href="/logout">Log ud</a>
            </div>

            <h1>Statistik</h1>

            <div class="card">
                <h2>Sortering</h2>

                <form method="get" action="/stats" class="sort-bar">
                    <div class="field">
                        <label for="sort">Sorter efter</label>
                        <select id="sort" name="sort">
                            <option value="wins" {% if sort == "wins" %}selected{% endif %}>Sejre</option>
                            <option value="total_points" {% if sort == "total_points" %}selected{% endif %}>Total point</option>
                            <option value="total_money" {% if sort == "total_money" %}selected{% endif %}>Total money</option>
                            <option value="avg_stableford" {% if sort == "avg_stableford" %}selected{% endif %}>Snit stableford</option>
                            <option value="best_stableford" {% if sort == "best_stableford" %}selected{% endif %}>Bedste score</option>
                            <option value="rounds_played" {% if sort == "rounds_played" %}selected{% endif %}>Spillede runder</option>
                            <option value="top3" {% if sort == "top3" %}selected{% endif %}>Top 3</option>
                            <option value="name" {% if sort == "name" %}selected{% endif %}>Navn</option>
                        </select>
                    </div>

                    <div class="field">
                        <label for="direction">Retning</label>
                        <select id="direction" name="direction">
                            <option value="desc" {% if direction == "desc" %}selected{% endif %}>Høj til lav</option>
                            <option value="asc" {% if direction == "asc" %}selected{% endif %}>Lav til høj</option>
                        </select>
                    </div>

                    <div>
                        <button type="submit">Opdater</button>
                    </div>
                </form>
            </div>

            <div class="card">
                <h2>Spillerstatistik</h2>
                <div class="muted">Oversigt over aktive spillere på tværs af alle runder.</div>

                <div class="table-wrap" style="margin-top: 12px;">
                    <table>
                        <tr>
                            <th>Pos</th>
                            <th>Spiller</th>
                            <th>Runder</th>
                            <th>Snit stableford</th>
                            <th>Bedste score</th>
                            <th>Sejre</th>
                            <th>Top 3</th>
                            <th>Total point</th>
                            <th>Total money</th>
                        </tr>

                        {% for r in player_stats %}
                        <tr>
                            <td>{{ loop.index }}</td>
                            <td>{{ r[0] }}</td>
                            <td>{{ r[1] }}</td>
                            <td>{{ r[2] if r[2] is not none else "" }}</td>
                            <td>{{ r[3] if r[3] is not none else "" }}</td>
                            <td>{{ r[4] }}</td>
                            <td>{{ r[5] }}</td>
                            <td>{{ r[6] if r[6] is not none else 0 }}</td>
                            <td>{{ r[7] if r[7] is not none else 0 }}</td>
                        </tr>
                        {% endfor %}
                    </table>
                </div>
            </div>

        </div>
    </body>
    </html>
    """

    return render_template_string(
        html,
        player_stats=player_stats,
        sort=sort,
        direction=direction,
    )

@app.get("/health")
def health():
    return {"ok": True}, 200

app.config["SECRET_KEY"] = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)