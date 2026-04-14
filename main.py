from flask import Flask, render_template_string, request, redirect
import psycopg
import os

app = Flask(__name__)

DB_URL = os.getenv("DATABASE_URL")


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
                border: 1px solid #d0d0d0;
                border-radius: 10px;
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
                border: none;
                border-radius: 12px;
                padding: 14px;
                font-size: 17px;
                font-weight: 700;
                background: #111;
                color: white;
                cursor: pointer;
            }

            @media (min-width: 700px) {
                .player-grid {
                    grid-template-columns: 1fr 1fr;
                }
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

            for key in request.form:
                if not key.startswith("score_"):
                    continue

                player_id = int(key.split("_")[1])
                score = request.form.get(key)
                ctp = request.form.get(f"ctp_{player_id}")

                if score:
                    cur.execute(
                        """
                        update round_players
                        set status = 'played',
                            stableford_points = %s,
                            closest_to_pin_cm = %s
                        where round_id = %s
                          and player_id = %s;
                        """,
                        (
                            int(score),
                            int(ctp) if ctp else None,
                            round_id,
                            player_id
                        )
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
                update round_players
                set season_points = case position
                    when 1 then 12
                    when 2 then 9
                    when 3 then 8
                    when 4 then 7
                    when 5 then 6
                    when 6 then 5
                    when 7 then 4
                    when 8 then 3
                    when 9 then 2
                    when 10 then 1
                    else 0
                end
                where round_id = %s
                  and status = 'played';
                """,
                (round_id,)
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

        conn.commit()

    return redirect(f"/round/{round_id}")

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
                    coalesce(sum(rp.season_points), 0) as total_points,
                    coalesce(sum(rp.money_rank), 0) as total_money,
                    count(*) filter (where rp.status = 'played') as rounds_played
                from players p
                left join round_players rp
                    on rp.player_id = p.id
                left join rounds r
                    on r.id = rp.round_id
                   and r.season_year = %s
                where p.is_active = true
                group by p.id, p.full_name
                order by total_points desc, total_money desc, rounds_played desc, p.full_name;
                """,
                (season_year,)
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

            <a class="button-link" href="/">Ny runde</a>
        </div>
    </body>
    </html>
    """

    return render_template_string(
        html,
        round_date=round_date,
        season_year=season_year,
        prize_pool=prize_pool,
        course_name=course_name,
        daily_rows=daily_rows,
        season_rows=season_rows,
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)