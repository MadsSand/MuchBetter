from flask import Flask, request, redirect, session, url_for, render_template, redirect
import psycopg
import os
from functools import wraps
from pathlib import Path
from datetime import date
from werkzeug.security import generate_password_hash, check_password_hash

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
            return redirect(url_for("admin_login"))
        return view_func(*args, **kwargs)
    return wrapped_view

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None

    if request.method == "POST":
        password = request.form.get("password", "")

        if password == ADMIN_PASSWORD:
            session["is_admin"] = True
            session["logged_in"] = True
            next_url = request.args.get("next") or "/"
            return redirect(next_url)
        else:
            error = "Forkert kode"

    return render_template("admin_login.html", error=error)

@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.get("/")
def home():
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


    return render_template(
        "home.html",
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

    return render_template("new_round.html", courses=courses, players=players)

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

    return render_template("rounds.html", rounds=rounds)

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


    return render_template(
        "round_detail.html",
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


    return render_template(
        "round_detail.html",
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

    return render_template(
        "stats.html",
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


    return render_template(
        "player.html",
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

def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped_view

@app.get("/forum")
@login_required
def forum():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select
                    t.id,
                    t.title,
                    t.created_at,
                    count(p.id) as post_count,
                    max(p.created_at) as latest_post
                from forum_threads t
                left join forum_posts p on p.thread_id = t.id
                group by t.id, t.title, t.created_at
                order by coalesce(max(p.created_at), t.created_at) desc;
            """)
            threads = cur.fetchall()

    return render_template("forum.html", threads=threads)


@app.get("/forum/new")
@login_required
def new_forum_thread():
    return render_template("forum_new_thread.html")


@app.post("/forum/new")
@login_required
def create_forum_thread():
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    user_id = session.get("user_id")

    if not title or not body:
        return redirect("/forum/new")

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select username
                from users
                where id = %s;
            """, (user_id,))
            user = cur.fetchone()

            if not user:
                return redirect("/login")

            author_name = user[0]

            cur.execute("""
                insert into forum_threads (title)
                values (%s)
                returning id;
            """, (title,))
            thread_id = cur.fetchone()[0]

            cur.execute("""
                insert into forum_posts (thread_id, user_id, author_name, body)
                values (%s, %s, %s, %s);
            """, (thread_id, user_id, author_name, body))

        conn.commit()

    return redirect(f"/forum/{thread_id}")


@app.get("/forum/<int:thread_id>")
@login_required
def forum_thread(thread_id):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select id, title, created_at
                from forum_threads
                where id = %s;
            """, (thread_id,))
            thread = cur.fetchone()

            if not thread:
                return "Tråden blev ikke fundet", 404

            cur.execute("""
                select id, author_name, body, created_at
                from forum_posts
                where thread_id = %s
                order by created_at asc;
            """, (thread_id,))
            posts = cur.fetchall()

    return render_template("forum_thread.html", thread=thread, posts=posts)


@app.post("/forum/<int:thread_id>/reply")
@login_required
def reply_forum_thread(thread_id):
    user_id = session.get("user_id")

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select username
                from users
                where id = %s;
            """, (user_id,))
            user = cur.fetchone()

        if not user:
            return redirect("/login")

    author_name = user[0]

    body = request.form.get("body", "").strip()

    if not author_name or not body:
        return redirect(f"/forum/{thread_id}")

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                insert into forum_posts (thread_id, author_name, body)
                values (%s, %s, %s);
            """, (thread_id, author_name, body))

        conn.commit()

    return redirect(f"/forum/{thread_id}")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            return render_template("register.html", error="Udfyld alle felter")

        password_hash = generate_password_hash(password)

        try:
            with psycopg.connect(DB_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        insert into users (username, password_hash, is_approved)
                        values (%s, %s, true);
                    """, (username, password_hash))
                conn.commit()
        except Exception:
            return render_template("register.html", error="Brugernavn findes allerede")

        return redirect("/login")

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    select id, password_hash, is_admin, is_approved, player_id
                    from users
                    where username = %s;
                """, (username,))
                user = cur.fetchone()

        if not user or not check_password_hash(user[1], password):
            return render_template("login.html", error="Forkert login")


        session["logged_in"] = True
        session["user_id"] = user[0]
        session["is_admin"] = user[2]
        session["player_id"] = user[4]

        next_url = request.args.get("next") or "/"
        return redirect(next_url)

    return render_template("login.html")

@app.get("/admin/users")
@admin_required
def admin_users():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select id, username, created_at
                from users
                where is_approved = false
                order by created_at;
            """)
            users = cur.fetchall()

    return render_template("admin_users.html", users=users)

@app.post("/admin/users/<int:user_id>/approve")
@admin_required
def approve_user(user_id):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                update users
                set is_approved = true
                where id = %s;
            """, (user_id,))
        conn.commit()

    return redirect("/admin/users")

@app.get("/me")
@login_required
def my_page():
    player_id = session.get("player_id")

    if not player_id:
        return "Din bruger er ikke koblet til din side endnu", 403

    return redirect(f"/player/{player_id}")

app.config["SECRET_KEY"] = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)