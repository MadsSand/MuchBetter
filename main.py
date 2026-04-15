from flask import Flask, render_template_string, request, redirect
import psycopg
import os

app = Flask(__name__)

from dotenv import load_dotenv
load_dotenv()

from pathlib import Path
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

DB_URL = os.getenv("DATABASE_URL")

if not DB_URL:
    raise ValueError("DATABASE_URL is not set")


@app.get("/")
def home():
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
                <a href="/">Ny runde</a>
                <a href="/rounds">Runder</a>
                <a href="/stats">Statistik</a>
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
                        <input type="checkbox" id="closest_to_pin_active" name="closest_to_pin_active">
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

                            <div class="field">
                                <label for="ctp_{{ p[0] }}">Closest (cm)</label>
                                <input type="number" id="ctp_{{ p[0] }}" name="ctp_{{ p[0] }}" min="0">
                            </div>
                        </div>
                    </div>
                {% endfor %}

                <button type="submit">Gem runde</button>
            </form>
        </div>
    </body>
    </html>
    """

    return render_template_string(html, courses=courses, players=players)

@app.post("/save")
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
                <a href="/">Ny runde</a>
                <a href="/rounds">Runder</a>
                <a href="/stats">Statistik</a>
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
                <a href="/">Ny runde</a>
                <a href="/rounds">Runder</a>
                <a href="/stats">Statistik</a>
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
                <a href="/">Ny runde</a>
                <a href="/rounds">Runder</a>
                <a href="/stats">Statistik</a>
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
                               {% if round_row[3] %}checked{% endif %}>
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

                            <div class="field">
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
def delete_round(round_id):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("delete from round_players where round_id = %s;", (round_id,))
            cur.execute("delete from rounds where id = %s;", (round_id,))
        conn.commit()

    return redirect("/rounds")

@app.get("/stats")
def stats():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
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
                order by
                    wins desc,
                    total_points desc,
                    avg_stableford desc nulls last,
                    p.full_name;
                """
            )
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
        </style>
    </head>
    <body>
        <div class="container">

            <div class="navbar">
                <a href="/">Ny Runde</a>
                <a href="/rounds">Runder</a>
                <a href="/stats">Statistik</a>
            </div>

            <h1>Statistik</h1>

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

    return render_template_string(html, player_stats=player_stats)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)