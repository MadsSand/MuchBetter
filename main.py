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
                with latest_round_cte as (
                    select id, round_date, season_year
                    from rounds
                    order by round_date desc, id desc
                    limit 1
                ),
                current_totals as (
                    select
                        p.id as player_id,
                        p.full_name,
                        coalesce(sum(rp.season_points), 0) as total_points
                    from players p
                    left join round_players rp
                        on rp.player_id = p.id
                    left join rounds r
                        on r.id = rp.round_id
                    cross join latest_round_cte lr
                    where p.is_active = true
                    and (r.season_year = lr.season_year or r.id is null)
                    group by p.id, p.full_name
                ),
                previous_totals as (
                    select
                        p.id as player_id,
                        p.full_name,
                        coalesce(sum(rp.season_points), 0) as total_points
                    from players p
                    left join round_players rp
                        on rp.player_id = p.id
                    left join rounds r
                        on r.id = rp.round_id
                    cross join latest_round_cte lr
                    where p.is_active = true
                    and (
                            (
                                r.season_year = lr.season_year
                                and (
                                    r.round_date < lr.round_date
                                    or (r.round_date = lr.round_date and r.id < lr.id)
                                )
                            )
                            or r.id is null
                    )
                    group by p.id, p.full_name
                ),
                ranked_current as (
                    select
                        player_id,
                        full_name,
                        total_points,
                        rank() over (order by total_points desc, full_name) as current_rank
                    from current_totals
                ),
                ranked_previous as (
                    select
                        player_id,
                        total_points,
                        rank() over (order by total_points desc, full_name) as previous_rank
                    from previous_totals
                )
                select
                    rc.player_id,
                    rc.full_name,
                    rc.total_points,
                    rc.current_rank,
                    rp.previous_rank,
                    case
                        when rp.previous_rank is null then 'new'
                        when rp.previous_rank > rc.current_rank then 'up'
                        when rp.previous_rank < rc.current_rank then 'down'
                        else 'same'
                    end as movement,
                    case
                        when rp.previous_rank is null then null
                        else abs(rp.previous_rank - rc.current_rank)
                    end as movement_by
                from ranked_current rc
                left join ranked_previous rp
                    on rp.player_id = rc.player_id
                order by rc.current_rank
                limit 3;
            """)
            top_points = cur.fetchall()

            cur.execute("""
                with latest_round_cte as (
                    select id, round_date, season_year
                    from rounds
                    order by round_date desc, id desc
                    limit 1
                ),
                current_totals as (
                    select
                        p.id as player_id,
                        p.full_name,
                        coalesce(sum(rp.money_rank), 0) as total_money
                    from players p
                    left join round_players rp
                        on rp.player_id = p.id
                    left join rounds r
                        on r.id = rp.round_id
                    cross join latest_round_cte lr
                    where p.is_active = true
                    and (r.season_year = lr.season_year or r.id is null)
                    group by p.id, p.full_name
                ),
                previous_totals as (
                    select
                        p.id as player_id,
                        p.full_name,
                        coalesce(sum(rp.money_rank), 0) as total_money
                    from players p
                    left join round_players rp
                        on rp.player_id = p.id
                    left join rounds r
                        on r.id = rp.round_id
                    cross join latest_round_cte lr
                    where p.is_active = true
                    and (
                            (
                                r.season_year = lr.season_year
                                and (
                                    r.round_date < lr.round_date
                                    or (r.round_date = lr.round_date and r.id < lr.id)
                                )
                            )
                            or r.id is null
                    )
                    group by p.id, p.full_name
                ),
                ranked_current as (
                    select
                        player_id,
                        full_name,
                        total_money,
                        rank() over (order by total_money desc, full_name) as current_rank
                    from current_totals
                ),
                ranked_previous as (
                    select
                        player_id,
                        total_money,
                        rank() over (order by total_money desc, full_name) as previous_rank
                    from previous_totals
                )
                select
                    rc.player_id,
                    rc.full_name,
                    rc.total_money,
                    rc.current_rank,
                    rp.previous_rank,
                    case
                        when rp.previous_rank is null then 'new'
                        when rp.previous_rank > rc.current_rank then 'up'
                        when rp.previous_rank < rc.current_rank then 'down'
                        else 'same'
                    end as movement,
                    case
                        when rp.previous_rank is null then null
                        else abs(rp.previous_rank - rc.current_rank)
                    end as movement_by
                from ranked_current rc
                left join ranked_previous rp
                    on rp.player_id = rc.player_id
                order by rc.current_rank
                limit 3;
            """)
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
        <title>MuchBetter Golf Liga</title>
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

            .movement {
                font-size: 13px;
                font-weight: 700;
                padding: 4px 8px;
                border-radius: 999px;
                display: inline-block;
            }

            .movement.up {
                background: #e8f7ed;
                color: #1f7a3e;
            }

            .movement.down {
                background: #fdecec;
                color: #b42318;
            }

            .movement.same {
                background: #f2f4f7;
                color: #667085;
            }

            .movement.new {
                background: #eef4ff;
                color: #175cd3;
            }

            .value-wrap {
                display: flex;
                align-items: center;
                gap: 8px;
                flex-wrap: wrap;
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
                <h1>MuchBetter Golf Liga</h1>
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
                                    <div>{{ p[3] }}. <a class="player-link" href="/player/{{ p[0] }}">{{ p[1] }}</a></div>
                                    <div class="value-wrap">
                                        <div>{{ p[2] }} point</div>

                                        {% if p[5] == "up" %}
                                            <span class="movement up">▲ {{ p[6] }}</span>
                                        {% elif p[5] == "down" %}
                                            <span class="movement down">▼ {{ p[6] }}</span>
                                        {% elif p[5] == "new" %}
                                            <span class="movement new">Ny</span>
                                        {% else %}
                                            <span class="movement same">–</span>
                                        {% endif %}
                                    </div>
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
                                    <div>{{ p[3] }}. <a class="player-link" href="/player/{{ p[0] }}">{{ p[1] }}</a></div>
                                    <div class="value-wrap">
                                        <div>{{ p[2] }} kr</div>

                                        {% if p[5] == "up" %}
                                            <span class="movement up">▲ {{ p[6] }}</span>
                                        {% elif p[5] == "down" %}
                                            <span class="movement down">▼ {{ p[6] }}</span>
                                        {% elif p[5] == "new" %}
                                            <span class="movement new">Ny</span>
                                        {% else %}
                                            <span class="movement same">–</span>
                                        {% endif %}
                                    </div>
                                </div>
                            {% endfor %}
                        </div>
                    {% else %}
                        <div class="muted">Ingen data endnu.</div>
                    {% endif %}
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
                    p.id,
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
                    p.id,
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
            .player-link {
                color: #111;
                text-decoration: none;
                font-weight: 600;
            }

            .player-link:hover {
                text-decoration: underline;
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
                            <td>{{ r[4] if r[4] is not none else "" }}</td>
                            <td><a class="player-link" href="/player/{{ r[0] }}">{{ r[1] }}</a></td>
                            <td>{{ r[2] }}</td>
                            <td>{{ r[3] if r[3] is not none else "" }}</td>
                            <td>{{ r[5] if r[5] is not none else "" }}</td>
                            <td>{{ r[6] if r[6] is not none else "" }}</td>
                            <td>{{ r[7] if r[7] is not none else "" }}</td>
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
                            <td><a class="player-link" href="/player/{{ r[0] }}">{{ r[1] }}</a></td>
                            <td>{{ r[2] }}</td>
                            <td>{{ r[3] }}</td>
                            <td>{{ r[4] }}</td>
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
        update round_players
        set position = null
        where round_id = %s;
        """,
        (round_id,)
    )

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
        with played_count as (
            select count(*)::numeric as n
            from round_players
            where round_id = %s
              and status = 'played'
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
        expanded_positions as (
            select
                tg.position,
                tg.tie_count,
                gs.pos as occupied_pos
            from tie_groups tg
            cross join lateral generate_series(
                tg.position,
                tg.position + tg.tie_count - 1
            ) as gs(pos)
        ),
        point_values as (
            select
                ep.position,
                round(
                    avg(
                        (2 * (pc.n - ep.occupied_pos)) +
                        case
                            when ep.occupied_pos = 1 then 4
                            when ep.occupied_pos = 2 then 2
                            when ep.occupied_pos = 3 then 1
                            else 0
                        end
                    ),
                    2
                ) as avg_points
            from expanded_positions ep
            cross join played_count pc
            group by ep.position
        )
        update round_players rp
        set season_points = pv.avg_points
        from point_values pv
        where rp.round_id = %s
          and rp.status = 'played'
          and rp.position = pv.position;
        """,
        (round_id, round_id, round_id)
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
        "name": "full_name",
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
        with latest_round_cte as (
            select id, round_date, season_year
            from rounds
            order by round_date desc, id desc
            limit 1
        ),
        current_totals as (
            select
                p.id as player_id,
                coalesce(sum(case when r.season_year = lr.season_year then rp.season_points else 0 end), 0) as total_points,
                coalesce(sum(case when r.season_year = lr.season_year then rp.money_rank else 0 end), 0) as total_money
            from players p
            left join round_players rp
                on rp.player_id = p.id
            left join rounds r
                on r.id = rp.round_id
            cross join latest_round_cte lr
            where p.is_active = true
            group by p.id
        ),
        previous_totals as (
            select
                p.id as player_id,
                coalesce(sum(
                    case
                        when r.season_year = lr.season_year
                        and (
                            r.round_date < lr.round_date
                            or (r.round_date = lr.round_date and r.id < lr.id)
                        )
                        then rp.season_points
                        else 0
                    end
                ), 0) as prev_points,
                coalesce(sum(
                    case
                        when r.season_year = lr.season_year
                        and (
                            r.round_date < lr.round_date
                            or (r.round_date = lr.round_date and r.id < lr.id)
                        )
                        then rp.money_rank
                        else 0
                    end
                ), 0) as prev_money
            from players p
            left join round_players rp
                on rp.player_id = p.id
            left join rounds r
                on r.id = rp.round_id
            cross join latest_round_cte lr
            where p.is_active = true
            group by p.id
        )
        select
            p.id,
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
            coalesce(sum(rp.money_rank), 0) as total_money,
            coalesce(ct.total_points, 0) - coalesce(pt.prev_points, 0) as points_change,
            coalesce(ct.total_money, 0) - coalesce(pt.prev_money, 0) as money_change
        from players p
        left join round_players rp
            on rp.player_id = p.id
        left join current_totals ct
            on ct.player_id = p.id
        left join previous_totals pt
            on pt.player_id = p.id
        where p.is_active = true
        group by
            p.id,
            p.full_name,
            ct.total_points,
            ct.total_money,
            pt.prev_points,
            pt.prev_money
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
                max-width: 1100px;
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

            h1, h2 {
                margin-top: 0;
            }

            .table-wrap {
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
            }

            table {
                width: 100%;
                min-width: 1100px;
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

            .change {
                font-weight: 700;
                padding: 4px 8px;
                border-radius: 999px;
                display: inline-block;
                min-width: 54px;
                text-align: center;
            }

            .change.up {
                background: #e8f7ed;
                color: #1f7a3e;
            }

            .change.down {
                background: #fdecec;
                color: #b42318;
            }

            .change.same {
                background: #f2f4f7;
                color: #667085;
            }

            .player-link {
                color: #111;
                text-decoration: none;
                font-weight: 600;
            }

            .player-link:hover {
                text-decoration: underline;
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
                            <th>Point siden sidst</th>
                            <th>Total money</th>
                            <th>Money siden sidst</th>
                        </tr>

                        {% for r in player_stats %}
                            <tr>
                                <td>{{ loop.index }}</td>
                                <td><a class="player-link" href="/player/{{ r[0] }}">{{ r[1] }}</a></td>
                                <td>{{ r[2] }}</td>
                                <td>{{ r[3] if r[3] is not none else "" }}</td>
                                <td>{{ r[4] if r[4] is not none else "" }}</td>
                                <td>{{ r[5] }}</td>
                                <td>{{ r[6] }}</td>
                                <td>{{ r[7] if r[7] is not none else 0 }}</td>
                                <td>
                                    {% if r[9] > 0 %}
                                        <span class="change up">▲ {{ r[9] }}</span>
                                    {% elif r[9] < 0 %}
                                        <span class="change down">▼ {{ -r[9] }}</span>
                                    {% else %}
                                        <span class="change same">–</span>
                                    {% endif %}
                                </td>
                                <td>{{ r[8] if r[8] is not none else 0 }}</td>
                                <td>
                                    {% if r[10] > 0 %}
                                        <span class="change up">▲ {{ r[10] }}</span>
                                    {% elif r[10] < 0 %}
                                        <span class="change down">▼ {{ -r[10] }}</span>
                                    {% else %}
                                        <span class="change same">–</span>
                                    {% endif %}
                                </td>
                            </tr>
                            {% endfor %}
                    </table>
                </div>
            </div>

            <div class="card">
                <h2>Pointsystem</h2>

                <div class="muted" style="line-height: 1.6;">
                    <b>Sådan beregnes point:</b><br><br>

                    • Du får <b>2 point pr. spiller du vinder over på dagen</b><br>
                    • <b>Bonus til top 3:</b><br>
                    &nbsp;&nbsp;&nbsp;1. plads: +4 point<br>
                    &nbsp;&nbsp;&nbsp;2. plads: +2 point<br>
                    &nbsp;&nbsp;&nbsp;3. plads: +1 point<br><br>

                    • Sidsteplads giver 0 point<br>
                    • Ved uafgjort deles point for de placeringer man optager<br><br>

                    <span style="font-size: 13px;">
                        Eksempel: 8 spillere → vinder får 2×7 pr. spiller man har vundet over + 4 bonus = 18 point
                    </span>
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

@app.get("/player/<int:player_id>")
def player_page(player_id):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    p.id,
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
                where p.id = %s
                  and p.is_active = true
                group by p.id, p.full_name;
                """,
                (player_id,)
            )
            player = cur.fetchone()

            if not player:
                return "Spilleren blev ikke fundet", 404

            cur.execute(
                """
                select
                    r.id,
                    r.round_date,
                    c.name,
                    rp.stableford_points,
                    rp.position,
                    rp.season_points,
                    rp.money_rank
                from round_players rp
                join rounds r on r.id = rp.round_id
                join courses c on c.id = r.course_id
                where rp.player_id = %s
                  and rp.status = 'played'
                order by r.round_date desc, r.id desc
                limit 5;
                """,
                (player_id,)
            )
            last_5_rounds = cur.fetchall()

            cur.execute(
                """
                with player_seasons as (
                    select distinct r.season_year
                    from round_players rp
                    join rounds r on r.id = rp.round_id
                    where rp.player_id = %s
                ),
                relevant_rounds as (
                    select
                        r.id as round_id,
                        r.round_date,
                        r.season_year,
                        c.name as course_name
                    from rounds r
                    join courses c on c.id = r.course_id
                    where r.season_year in (select season_year from player_seasons)
                ),
                player_round_data as (
                    select
                        rr.round_id,
                        rr.round_date,
                        rr.season_year,
                        rr.course_name,
                        coalesce(rp.position, null) as round_position,
                        coalesce(rp.stableford_points, 0) as stableford_points,
                        coalesce(rp.season_points, 0) as season_points,
                        coalesce(rp.money_rank, 0) as money_rank,
                        coalesce(rp.status, 'dnp') as status
                    from relevant_rounds rr
                    left join round_players rp
                        on rp.round_id = rr.round_id
                    and rp.player_id = %s
                ),
                leaderboard_after_each_round as (
                    select
                        rr.round_id,
                        p.id as player_id,
                        rank() over (
                            partition by rr.round_id
                            order by
                                coalesce(sum(
                                    case
                                        when r2.season_year = rr.season_year
                                        and (
                                            r2.round_date < rr.round_date
                                            or (r2.round_date = rr.round_date and r2.id <= rr.round_id)
                                        )
                                        then rp2.season_points
                                        else 0
                                    end
                                ), 0) desc,
                                coalesce(sum(
                                    case
                                        when r2.season_year = rr.season_year
                                        and (
                                            r2.round_date < rr.round_date
                                            or (r2.round_date = rr.round_date and r2.id <= rr.round_id)
                                        )
                                        then rp2.money_rank
                                        else 0
                                    end
                                ), 0) desc,
                                p.full_name
                        ) as leaderboard_position
                    from relevant_rounds rr
                    cross join players p
                    left join round_players rp2
                        on rp2.player_id = p.id
                    left join rounds r2
                        on r2.id = rp2.round_id
                    where p.is_active = true
                    group by rr.round_id, rr.season_year, rr.round_date, p.id, p.full_name
                ),
                final_rows as (
                    select
                        prd.round_id,
                        prd.round_date,
                        prd.course_name,
                        prd.round_position,
                        prd.stableford_points,
                        prd.season_points,
                        prd.money_rank,
                        prd.status,
                        sum(prd.season_points) over (
                            partition by prd.season_year
                            order by prd.round_date, prd.round_id
                            rows between unbounded preceding and current row
                        ) as running_points,
                        sum(prd.money_rank) over (
                            partition by prd.season_year
                            order by prd.round_date, prd.round_id
                            rows between unbounded preceding and current row
                        ) as running_money,
                        laer.leaderboard_position
                    from player_round_data prd
                    left join leaderboard_after_each_round laer
                        on laer.round_id = prd.round_id
                    and laer.player_id = %s
                )
                select
                    round_id,
                    round_date,
                    course_name,
                    round_position,
                    stableford_points,
                    season_points,
                    money_rank,
                    status,
                    running_points,
                    running_money,
                    leaderboard_position
                from final_rows
                order by round_date, round_id;
                """,
                (player_id, player_id, player_id)
            )
            progress_rows = cur.fetchall()

    chart_labels = [str(row[1]) for row in progress_rows]
    chart_positions = [int(row[10]) if row[10] is not None else None for row in progress_rows]
    chart_stableford = [int(row[4]) if row[7] == "played" and row[4] is not None else None for row in progress_rows]
    chart_round_points = [float(row[5]) if row[5] is not None else 0 for row in progress_rows]
    chart_round_money = [float(row[6]) if row[6] is not None else 0 for row in progress_rows]
    chart_running_points = [float(row[8]) if row[8] is not None else 0 for row in progress_rows]
    chart_running_money = [float(row[9]) if row[9] is not None else 0 for row in progress_rows]

    html = """
    <!doctype html>
    <html lang="da">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{{ player[1] }}</title>
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
                max-width: 1100px;
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

            .card {
                background: white;
                border-radius: 16px;
                padding: 18px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
                margin-bottom: 16px;
            }

            .profile {
                display: grid;
                grid-template-columns: 120px 1fr;
                gap: 18px;
                align-items: center;
            }

            .avatar {
                width: 120px;
                height: 120px;
                border-radius: 50%;
                background: #111;
                color: white;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 42px;
                font-weight: 700;
            }

            .profile h1 {
                margin: 0 0 8px 0;
            }

            .muted {
                color: #666;
                font-size: 14px;
            }

            .stats-grid {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 16px;
                margin-bottom: 16px;
            }

            .stat-box {
                background: white;
                border-radius: 16px;
                padding: 18px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
            }

            .stat-label {
                color: #666;
                font-size: 14px;
                margin-bottom: 8px;
            }

            .stat-value {
                font-size: 28px;
                font-weight: 700;
            }

            .charts-grid {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 16px;
                margin-bottom: 16px;
            }

            .chart-card {
                background: white;
                border-radius: 16px;
                padding: 22px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
                margin-top: 10px;
            }

            .chart-card h2 {
                margin-top: 0;
                margin-bottom: 8px;
            }

            .table-wrap {
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
            }

            table {
                width: 100%;
                min-width: 700px;
                border-collapse: collapse;
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

            .round-link, .player-link {
                color: #111;
                text-decoration: none;
                font-weight: 600;
            }

            .round-link:hover, .player-link:hover {
                text-decoration: underline;
            }

            canvas {
                width: 100%;
                max-width: 100%;
            }

            @media (max-width: 900px) {
                .stats-grid {
                    grid-template-columns: 1fr 1fr;
                }

                .charts-grid {
                    grid-template-columns: 1fr;
                }
            }

            @media (max-width: 700px) {
                .profile {
                    grid-template-columns: 1fr;
                    text-align: center;
                }

                .avatar {
                    margin: 0 auto;
                }
            }

            @media (max-width: 520px) {
                .stats-grid {
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
                <a href="/stats">Statistik</a>
                <a href="/logout">Log ud</a>
            </div>

            <div class="card profile">
                <div class="avatar">{{ player[1][0] }}</div>
                <div>
                    <h1>{{ player[1] }}</h1>
                    <div class="muted">Spillerprofil og udvikling</div>
                </div>
            </div>

            <div class="stats-grid">
                <div class="stat-box">
                    <div class="stat-label">Snit stableford</div>
                    <div class="stat-value">{{ player[3] if player[3] is not none else 0 }}</div>
                </div>

                <div class="stat-box">
                    <div class="stat-label">Bedste score</div>
                    <div class="stat-value">{{ player[4] if player[4] is not none else 0 }}</div>
                </div>

                <div class="stat-box">
                    <div class="stat-label">Sejre</div>
                    <div class="stat-value">{{ player[5] }}</div>
                </div>

                <div class="stat-box">
                    <div class="stat-label">Top 3</div>
                    <div class="stat-value">{{ player[6] }}</div>
                </div>

                <div class="stat-box">
                    <div class="stat-label">Spillede runder</div>
                    <div class="stat-value">{{ player[2] }}</div>
                </div>

                <div class="stat-box">
                    <div class="stat-label">Total point</div>
                    <div class="stat-value">{{ player[7] }}</div>
                </div>

                <div class="stat-box">
                    <div class="stat-label">Total fake money</div>
                    <div class="stat-value">{{ player[8] }}</div>
                </div>
            </div>

            <div class="chart-card">
                <h2>Placering pr. runde</h2>
                {% if chart_labels %}
                    <canvas id="placementChart" height="160"></canvas>
                    <div class="muted" style="margin-top: 10px;">Graf over spillerens placering i hver runde.</div>
                {% else %}
                    <div class="muted">Ingen runder endnu.</div>
                {% endif %}
            </div>

            <div class="charts-grid">
                <div class="chart-card">
                    <h2>Point</h2>
                    {% if chart_labels %}
                        <canvas id="pointsChart" height="200"></canvas>
                        <div class="muted" style="margin-top: 10px;">Klik på farverne for at vise eller skjule serier.</div>
                    {% else %}
                        <div class="muted">Ingen runder endnu.</div>
                    {% endif %}
                </div>

                <div class="chart-card">
                    <h2>Fake money</h2>
                    {% if chart_labels %}
                        <canvas id="moneyChart" height="200"></canvas>
                        <div class="muted" style="margin-top: 10px;">Klik på farverne for at vise eller skjule serier.</div>
                    {% else %}
                        <div class="muted">Ingen runder endnu.</div>
                    {% endif %}
                </div>
            </div>

            <div class="card">
                <h2>Sidste 5 runder</h2>
                <div class="table-wrap">
                    <table>
                        <tr>
                            <th>Dato</th>
                            <th>Bane</th>
                            <th>Stableford</th>
                            <th>Pos</th>
                            <th>Point</th>
                            <th>Money</th>
                        </tr>
                        {% for r in last_5_rounds %}
                        <tr>
                            <td><a class="round-link" href="/round/{{ r[0] }}">{{ r[1] }}</a></td>
                            <td>{{ r[2] }}</td>
                            <td>{{ r[3] }}</td>
                            <td>{{ r[4] }}</td>
                            <td>{{ r[5] }}</td>
                            <td>{{ r[6] }}</td>
                        </tr>
                        {% endfor %}
                    </table>
                </div>
            </div>
        </div>

        {% if chart_labels %}
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script>
            const labels = {{ chart_labels | tojson }};
            const positions = {{ chart_positions | tojson }};
            const stableford = {{ chart_stableford | tojson }};
            const roundPoints = {{ chart_round_points | tojson }};
            const roundMoney = {{ chart_round_money | tojson }};
            const runningPoints = {{ chart_running_points | tojson }};
            const runningMoney = {{ chart_running_money | tojson }};

            new Chart(document.getElementById("placementChart"), {
                type: "line",
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: "Placering",
                            data: positions,
                            borderColor: "#dc2626",
                            backgroundColor: "#dc2626",
                            tension: 0.25,
                            pointRadius: 4
                        }
                    ]
                },
                options: {
                    responsive: true,
                    interaction: {
                        mode: "index",
                        intersect: false
                    },
                    plugins: {
                        legend: {
                            display: false
                        }
                    },
                    scales: {
                        y: {
                            reverse: true,
                            beginAtZero: false,
                            ticks: {
                                precision: 0,
                                stepSize: 1
                            },
                            title: {
                                display: true,
                                text: "Placering"
                            }
                        }
                    }
                }
            });

            new Chart(document.getElementById("pointsChart"), {
                type: "line",
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: "Point pr. runde",
                            data: roundPoints,
                            borderColor: "#2563eb",
                            backgroundColor: "#2563eb",
                            tension: 0.25,
                            pointRadius: 4,
                            hidden: false
                        },
                        {
                            label: "Samlet point pr. runde",
                            data: runningPoints,
                            borderColor: "#111111",
                            backgroundColor: "#111111",
                            tension: 0.25,
                            pointRadius: 4,
                            hidden: false
                        },
                        {
                            label: "Stableford",
                            data: stableford,
                            borderColor: "#7c3aed",
                            backgroundColor: "#7c3aed",
                            tension: 0.25,
                            pointRadius: 4,
                            hidden: true
                        }
                    ]
                },
                options: {
                    responsive: true,
                    interaction: {
                        mode: "index",
                        intersect: false
                    },
                    plugins: {
                        legend: {
                            display: true,
                            position: "top"
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            title: {
                                display: true,
                                text: "Point"
                            }
                        }
                    }
                }
            });

            new Chart(document.getElementById("moneyChart"), {
                type: "line",
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: "Fake money pr. runde",
                            data: roundMoney,
                            borderColor: "#f59e0b",
                            backgroundColor: "#f59e0b",
                            tension: 0.25,
                            pointRadius: 4,
                            hidden: false
                        },
                        {
                            label: "Samlet fake money pr. runde",
                            data: runningMoney,
                            borderColor: "#16a34a",
                            backgroundColor: "#16a34a",
                            tension: 0.25,
                            pointRadius: 4,
                            hidden: false
                        }
                    ]
                },
                options: {
                    responsive: true,
                    interaction: {
                        mode: "index",
                        intersect: false
                    },
                    plugins: {
                        legend: {
                            display: true,
                            position: "top"
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            title: {
                                display: true,
                                text: "Fake money"
                            }
                        }
                    }
                }
            });
        </script>
        {% endif %}
    </body>
    </html>
    """

    return render_template_string(
        html,
        player=player,
        last_5_rounds=last_5_rounds,
        chart_labels=chart_labels,
        chart_positions=chart_positions,
        chart_stableford=chart_stableford,
        chart_round_points=chart_round_points,
        chart_round_money=chart_round_money,
        chart_running_points=chart_running_points,
        chart_running_money=chart_running_money,
    )


app.config["SECRET_KEY"] = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)