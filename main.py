from flask import Flask, request, redirect, session, url_for, render_template, redirect, send_file
import hashlib
import psycopg
import os
import io
import secrets
import string
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse
from datetime import date, timedelta
from PIL import Image, ImageOps, UnidentifiedImageError
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

PRIZE_POOL_DEFAULT = 5000
LEGACY_PRIZE_POOL = 5_000_000


def format_fake_money(value):
    if value is None:
        return "0"
    amount = int(round(float(value)))
    text = str(amount)
    if len(text) <= 3:
        return text
    parts = []
    while len(text) > 3:
        parts.insert(0, text[-3:])
        text = text[:-3]
    if text:
        parts.insert(0, text)
    return ".".join(parts)


@app.template_filter("fake_money")
def fake_money_filter(value):
    return format_fake_money(value)


def ensure_player_profile_columns():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                alter table players
                add column if not exists avatar_data bytea;
                """
            )
            cur.execute(
                """
                alter table players
                add column if not exists avatar_mime_type text;
                """
            )
            cur.execute(
                """
                alter table players
                add column if not exists last_known_handicap numeric(6, 2);
                """
            )
        conn.commit()


ensure_player_profile_columns()


def ensure_upcoming_events_table():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                create table if not exists upcoming_events (
                    id serial primary key,
                    title text not null,
                    event_date date not null,
                    emphasis boolean not null default false,
                    course_image_data bytea,
                    course_image_mime_type text,
                    created_at timestamptz not null default now()
                );
                """
            )
        conn.commit()


ensure_upcoming_events_table()


def ensure_event_rsvps_table():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                create table if not exists event_rsvps (
                    event_id integer not null
                        references upcoming_events(id) on delete cascade,
                    player_id integer not null
                        references players(id) on delete cascade,
                    status text not null
                        check (status in ('yes', 'no')),
                    updated_at timestamptz not null default now(),
                    primary key (event_id, player_id)
                );
                """
            )
        conn.commit()


ensure_event_rsvps_table()


def ensure_sidebar_updates_table():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                create table if not exists sidebar_updates (
                    id serial primary key,
                    body text not null,
                    author_username text not null,
                    created_at timestamptz not null default now()
                );
                """
            )
        conn.commit()


ensure_sidebar_updates_table()


def ensure_user_notifications_table():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                create table if not exists user_notifications (
                    user_id integer primary key references users(id) on delete cascade,
                    forum_seen_at timestamptz not null default now(),
                    rounds_seen_round_id integer not null default 0,
                    leaderboard_seen_round_id integer not null default 0,
                    events_seen_event_id integer not null default 0,
                    updated_at timestamptz not null default now()
                );
                """
            )
        conn.commit()


ensure_user_notifications_table()


def ensure_forum_enhancements():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                alter table forum_threads
                add column if not exists is_sticky boolean not null default false;
                """
            )
            cur.execute(
                """
                alter table forum_posts
                add column if not exists parent_post_id integer references forum_posts(id) on delete set null;
                """
            )
            cur.execute(
                """
                create table if not exists forum_post_likes (
                    post_id integer not null references forum_posts(id) on delete cascade,
                    user_id integer not null references users(id) on delete cascade,
                    created_at timestamptz not null default now(),
                    primary key (post_id, user_id)
                );
                """
            )
            cur.execute(
                """
                create table if not exists forum_thread_reads (
                    user_id integer not null references users(id) on delete cascade,
                    thread_id integer not null references forum_threads(id) on delete cascade,
                    last_seen_post_at timestamptz not null default now(),
                    primary key (user_id, thread_id)
                );
                """
            )
        conn.commit()


ensure_forum_enhancements()


def ensure_messaging_tables():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                create table if not exists conversations (
                    id serial primary key,
                    kind text not null check (kind in ('direct', 'group')),
                    title text,
                    created_by_user_id integer references users(id) on delete set null,
                    created_at timestamptz not null default now()
                );
                """
            )
            cur.execute(
                """
                create table if not exists conversation_members (
                    conversation_id integer not null references conversations(id) on delete cascade,
                    user_id integer not null references users(id) on delete cascade,
                    joined_at timestamptz not null default now(),
                    last_read_at timestamptz not null default now(),
                    primary key (conversation_id, user_id)
                );
                """
            )
            cur.execute(
                """
                create table if not exists messages (
                    id serial primary key,
                    conversation_id integer not null references conversations(id) on delete cascade,
                    sender_user_id integer not null references users(id) on delete cascade,
                    body text not null,
                    created_at timestamptz not null default now()
                );
                """
            )
            cur.execute(
                """
                create index if not exists messages_conversation_created_idx
                on messages (conversation_id, created_at desc);
                """
            )
        conn.commit()


ensure_messaging_tables()


MESSAGING_USER_SQL = """
    select u.id, p.id, p.full_name, (p.avatar_data is not null) as has_avatar
    from users u
    join players p on p.id = u.player_id
    where u.is_approved = true
      and p.is_active = true
"""


def fetch_messaging_user_ids(cur):
    cur.execute(
        f"""
        select u.id
        from users u
        join players p on p.id = u.player_id
        where u.is_approved = true
          and p.is_active = true;
        """
    )
    return {int(row[0]) for row in cur.fetchall()}


def is_messaging_user(cur, user_id):
    if not user_id:
        return False
    cur.execute(
        """
        select 1
        from users u
        join players p on p.id = u.player_id
        where u.id = %s
          and u.is_approved = true
          and p.is_active = true;
        """,
        (user_id,),
    )
    return cur.fetchone() is not None


def find_direct_conversation_id(cur, user_a, user_b):
    cur.execute(
        """
        select c.id
        from conversations c
        where c.kind = 'direct'
          and (
            select count(*) from conversation_members cm
            where cm.conversation_id = c.id
          ) = 2
          and exists (
            select 1 from conversation_members cm
            where cm.conversation_id = c.id and cm.user_id = %s
          )
          and exists (
            select 1 from conversation_members cm
            where cm.conversation_id = c.id and cm.user_id = %s
          )
        limit 1;
        """,
        (user_a, user_b),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def get_or_create_direct_conversation(cur, user_id, other_user_id):
    existing = find_direct_conversation_id(cur, user_id, other_user_id)
    if existing:
        return existing
    cur.execute(
        """
        insert into conversations (kind, created_by_user_id)
        values ('direct', %s)
        returning id;
        """,
        (user_id,),
    )
    conversation_id = int(cur.fetchone()[0])
    for member_id in (user_id, other_user_id):
        cur.execute(
            """
            insert into conversation_members (conversation_id, user_id)
            values (%s, %s);
            """,
            (conversation_id, member_id),
        )
    return conversation_id


def user_in_conversation(cur, user_id, conversation_id):
    cur.execute(
        """
        select 1
        from conversation_members
        where conversation_id = %s and user_id = %s;
        """,
        (conversation_id, user_id),
    )
    return cur.fetchone() is not None


def mark_conversation_read(cur, user_id, conversation_id):
    cur.execute(
        """
        update conversation_members
        set last_read_at = now()
        where conversation_id = %s and user_id = %s;
        """,
        (conversation_id, user_id),
    )


def count_unread_messages(cur, user_id):
    cur.execute(
        """
        select coalesce(sum(sub.unread), 0)
        from (
            select count(*)::int as unread
            from conversation_members cm
            join messages m on m.conversation_id = cm.conversation_id
            where cm.user_id = %s
              and m.created_at > cm.last_read_at
              and m.sender_user_id <> %s
            group by cm.conversation_id
        ) sub;
        """,
        (user_id, user_id),
    )
    return int(cur.fetchone()[0] or 0)


def fetch_inbox_rows(cur, user_id):
    cur.execute(
        """
        select
            c.id,
            c.kind,
            c.title,
            (
                select max(m.created_at)
                from messages m
                where m.conversation_id = c.id
            ) as last_at,
            (
                select m.body
                from messages m
                where m.conversation_id = c.id
                order by m.created_at desc, m.id desc
                limit 1
            ) as last_body,
            (
                select count(*)::int
                from messages m
                where m.conversation_id = c.id
                  and m.created_at > cm.last_read_at
                  and m.sender_user_id <> %s
            ) as unread_count,
            case
                when c.kind = 'direct' then (
                    select p.full_name
                    from conversation_members cm2
                    join users u on u.id = cm2.user_id
                    join players p on p.id = u.player_id
                    where cm2.conversation_id = c.id
                      and cm2.user_id <> %s
                    limit 1
                )
                else coalesce(c.title, 'Gruppe')
            end as display_name
        from conversations c
        join conversation_members cm on cm.conversation_id = c.id and cm.user_id = %s
        order by
            coalesce(
                (
                    select max(m.created_at)
                    from messages m
                    where m.conversation_id = c.id
                ),
                c.created_at
            ) desc,
            c.id desc;
        """,
        (user_id, user_id, user_id),
    )
    return cur.fetchall()


def fetch_sidebar_updates(cur, limit=8):
    cur.execute(
        """
        select id, body, author_username, created_at
        from sidebar_updates
        order by created_at desc, id desc
        limit %s;
        """,
        (limit,),
    )
    return cur.fetchall()


def ensure_notification_row(cur, user_id):
    cur.execute(
        """
        insert into user_notifications (user_id)
        values (%s)
        on conflict (user_id) do nothing;
        """,
        (user_id,),
    )


def fetch_latest_markers(cur):
    cur.execute("select coalesce(max(id), 0) from rounds;")
    latest_round_id = int(cur.fetchone()[0] or 0)

    cur.execute("select coalesce(max(id), 0) from upcoming_events;")
    latest_event_id = int(cur.fetchone()[0] or 0)

    cur.execute(
        """
        select coalesce(max(created_at), to_timestamp(0))
        from forum_posts;
        """
    )
    latest_forum_post_at = cur.fetchone()[0]

    return {
        "latest_round_id": latest_round_id,
        "latest_event_id": latest_event_id,
        "latest_forum_post_at": latest_forum_post_at,
    }


def mark_notification_seen(user_id, section):
    if not user_id:
        return
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            ensure_notification_row(cur, user_id)
            latest = fetch_latest_markers(cur)
            if section == "forum":
                cur.execute(
                    """
                    update user_notifications
                    set forum_seen_at = now(),
                        updated_at = now()
                    where user_id = %s;
                    """,
                    (user_id,),
                )
            elif section == "rounds":
                cur.execute(
                    """
                    update user_notifications
                    set rounds_seen_round_id = %s,
                        updated_at = now()
                    where user_id = %s;
                    """,
                    (latest["latest_round_id"], user_id),
                )
            elif section == "leaderboard":
                cur.execute(
                    """
                    update user_notifications
                    set leaderboard_seen_round_id = %s,
                        updated_at = now()
                    where user_id = %s;
                    """,
                    (latest["latest_round_id"], user_id),
                )
            elif section == "events":
                cur.execute(
                    """
                    update user_notifications
                    set events_seen_event_id = %s,
                        updated_at = now()
                    where user_id = %s;
                    """,
                    (latest["latest_event_id"], user_id),
                )
        conn.commit()


def mark_forum_thread_seen(cur, user_id, thread_id):
    cur.execute(
        """
        select coalesce(max(created_at), now())
        from forum_posts
        where thread_id = %s;
        """,
        (thread_id,),
    )
    latest_post_at = cur.fetchone()[0]
    cur.execute(
        """
        insert into forum_thread_reads (user_id, thread_id, last_seen_post_at)
        values (%s, %s, %s)
        on conflict (user_id, thread_id)
        do update set last_seen_post_at = excluded.last_seen_post_at;
        """,
        (user_id, thread_id, latest_post_at),
    )


def ensure_course_hero_assets():
    """Delte hero-billeder (én blob pr. unikt indhold) — mindre DB og ingen duplikat-upload."""
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                create table if not exists course_hero_assets (
                    id serial primary key,
                    content_sha256 char(64) not null unique,
                    image_data bytea not null,
                    image_mime_type text not null,
                    byte_size int not null,
                    created_at timestamptz not null default now()
                );
                """
            )
            cur.execute(
                """
                alter table upcoming_events
                add column if not exists hero_asset_id integer references course_hero_assets(id) on delete set null;
                """
            )
        conn.commit()


def backfill_inline_hero_images_to_assets():
    """Flyt gamle course_image_data til course_hero_assets og dedupliker på hash."""
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, course_image_data, course_image_mime_type
                from upcoming_events
                where course_image_data is not null
                  and hero_asset_id is null;
                """
            )
            rows = cur.fetchall()
            for event_id, data, mime in rows:
                if not data:
                    continue
                digest = hashlib.sha256(data).hexdigest()
                cur.execute(
                    "select id from course_hero_assets where content_sha256 = %s;",
                    (digest,),
                )
                found = cur.fetchone()
                if found:
                    asset_id = found[0]
                else:
                    cur.execute(
                        """
                        insert into course_hero_assets (
                            content_sha256, image_data, image_mime_type, byte_size
                        )
                        values (%s, %s, %s, %s)
                        returning id;
                        """,
                        (digest, data, mime or "image/webp", len(data)),
                    )
                    asset_id = cur.fetchone()[0]
                cur.execute(
                    """
                    update upcoming_events
                    set hero_asset_id = %s,
                        course_image_data = null,
                        course_image_mime_type = null
                    where id = %s;
                    """,
                    (asset_id, event_id),
                )
        conn.commit()


ensure_course_hero_assets()
backfill_inline_hero_images_to_assets()

HOME_PANEL_DEFINITIONS = [
    ("hero", "Hero (titel øverst)", 10),
    ("highlights", "Highlights · seneste runde", 20),
    ("top_points", "Top 3 – Point", 30),
    ("top_money", "Top 3 – Fake money", 40),
    ("forum", "Forum", 50),
    ("admin", "Admin-boks", 60),
]
HOME_PANEL_SLUGS = {slug for slug, _, _ in HOME_PANEL_DEFINITIONS}


def ensure_home_panels_table():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                create table if not exists home_panels (
                    slug text primary key,
                    label text not null,
                    sort_order int not null default 0,
                    hero_asset_id integer references course_hero_assets(id) on delete set null
                );
                """
            )
            for slug, label, sort_order in HOME_PANEL_DEFINITIONS:
                cur.execute(
                    """
                    insert into home_panels (slug, label, sort_order)
                    values (%s, %s, %s)
                    on conflict (slug) do nothing;
                    """,
                    (slug, label, sort_order),
                )
        conn.commit()


ensure_home_panels_table()


def get_or_create_course_hero_asset(cur, image_bytes: bytes, mime: str) -> int:
    digest = hashlib.sha256(image_bytes).hexdigest()
    cur.execute(
        "select id from course_hero_assets where content_sha256 = %s;",
        (digest,),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        """
        insert into course_hero_assets (
            content_sha256, image_data, image_mime_type, byte_size
        )
        values (%s, %s, %s, %s)
        returning id;
        """,
        (digest, image_bytes, mime, len(image_bytes)),
    )
    return cur.fetchone()[0]


def delete_orphan_course_hero_assets(cur):
    cur.execute(
        """
        delete from course_hero_assets a
        where not exists (
            select 1 from upcoming_events e where e.hero_asset_id = a.id
        )
        and not exists (
            select 1 from home_panels hp where hp.hero_asset_id = a.id
        );
        """
    )


def fetch_home_backgrounds(cur):
    cur.execute(
        """
        select slug, hero_asset_id
        from home_panels
        where hero_asset_id is not null;
        """
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def list_hero_assets_for_admin(cur):
    cur.execute(
        """
        select
            a.id,
            a.byte_size,
            a.content_sha256,
            (select count(*)::int from upcoming_events e where e.hero_asset_id = a.id)
            + (select count(*)::int from home_panels hp where hp.hero_asset_id = a.id) as ref_count
        from course_hero_assets a
        order by a.id desc;
        """
    )
    return cur.fetchall()


MONTH_NAMES_DA = (
    "",
    "januar",
    "februar",
    "marts",
    "april",
    "maj",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "december",
)

WEEKDAY_SHORT_DA = ("ma", "ti", "on", "to", "fr", "lø", "sø")


def parse_last_known_handicap_form(raw: str) -> tuple[float | None, str | None]:
    """Return (db_value or None to clear, error_message)."""
    s = (raw or "").strip()
    if not s:
        return None, None
    normalized = s.replace(",", ".").replace(" ", "")
    try:
        v = float(normalized)
    except ValueError:
        return None, "Indtast et tal (fx 18,4)."
    if v < -15 or v > 60:
        return None, "Handicap skal ligge i et fornuftigt interval."
    return round(v, 1), None


def format_handicap_dk(value) -> str | None:
    if value is None:
        return None
    return f"{float(value):.1f}".replace(".", ",")


def generate_temporary_password(length=10):
    alphabet = string.ascii_letters + string.digits
    alphabet = alphabet.replace("0", "").replace("O", "").replace("l", "").replace("I", "")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def pop_password_reset_notice():
    notice = session.pop("admin_password_reset", None)
    if not notice:
        return None
    if not isinstance(notice, dict):
        return None
    username = notice.get("username")
    password = notice.get("password")
    if not username or not password:
        return None
    return {"username": username, "password": password}


def redirect_after_admin_action():
    """Redirect to referrer only when it matches this host (avoid open redirects)."""
    fallback = url_for("admin_users")
    ref = request.referrer
    if not ref:
        return redirect(fallback)
    parts = urlparse(ref)
    if parts.scheme in ("http", "https") and parts.netloc and parts.netloc != request.host:
        return redirect(fallback)
    return redirect(ref)


def compress_avatar_image(raw_bytes: bytes) -> tuple[bytes, str]:
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((512, 512))
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")

            compressed = io.BytesIO()
            quality = 82
            while quality >= 55:
                compressed.seek(0)
                compressed.truncate(0)
                img.save(
                    compressed,
                    format="WEBP",
                    quality=quality,
                    method=6,
                )
                if compressed.tell() <= 700 * 1024:
                    break
                quality -= 7

            return compressed.getvalue(), "image/webp"
    except UnidentifiedImageError:
        raise ValueError("Filen er ikke et gyldigt billede")


def compress_course_hero_image(raw_bytes: bytes) -> tuple[bytes, str]:
    """Hero-baggrund: WebP, begrænset opløsning og filstørrelse (passer til CSS-cover)."""
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((1280, 720))
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")

            compressed = io.BytesIO()
            quality = 78
            max_bytes = 420 * 1024
            while quality >= 45:
                compressed.seek(0)
                compressed.truncate(0)
                img.save(
                    compressed,
                    format="WEBP",
                    quality=quality,
                    method=6,
                )
                if compressed.tell() <= max_bytes:
                    break
                quality -= 6

            return compressed.getvalue(), "image/webp"
    except UnidentifiedImageError:
        raise ValueError("Filen er ikke et gyldigt billede")


@app.context_processor
def inject_layout_context():
    online_users = []
    if session.get("logged_in") and session.get("username"):
        online_users.append(session["username"])
    ctx = {
        "online_users": online_users,
        "admin_pending_users": [],
        "admin_players": [],
        "sidebar_updates": [],
        "notif_forum_count": 0,
        "notif_rounds_count": 0,
        "notif_leaderboard_count": 0,
        "notif_events_count": 0,
        "notif_messages_count": 0,
    }
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            ctx["sidebar_updates"] = fetch_sidebar_updates(cur)
            user_id = session.get("user_id")
            if user_id:
                ensure_notification_row(cur, user_id)
                latest = fetch_latest_markers(cur)
                cur.execute(
                    """
                    select
                        forum_seen_at,
                        rounds_seen_round_id,
                        leaderboard_seen_round_id,
                        events_seen_event_id
                    from user_notifications
                    where user_id = %s;
                    """,
                    (user_id,),
                )
                seen = cur.fetchone()
                if seen:
                    forum_seen_at, rounds_seen_id, leaderboard_seen_id, events_seen_id = seen

                    cur.execute(
                        """
                        select count(*)
                        from forum_posts fp
                        where fp.created_at > %s
                          and (%s is null or fp.user_id <> %s);
                        """,
                        (forum_seen_at, user_id, user_id),
                    )
                    ctx["notif_forum_count"] = int(cur.fetchone()[0] or 0)

                    cur.execute(
                        """
                        select count(*)
                        from rounds
                        where id > %s;
                        """,
                        (int(rounds_seen_id or 0),),
                    )
                    ctx["notif_rounds_count"] = int(cur.fetchone()[0] or 0)

                    ctx["notif_leaderboard_count"] = (
                        1 if latest["latest_round_id"] > int(leaderboard_seen_id or 0) else 0
                    )

                    cur.execute(
                        """
                        select count(*)
                        from upcoming_events
                        where id > %s;
                        """,
                        (int(events_seen_id or 0),),
                    )
                    ctx["notif_events_count"] = int(cur.fetchone()[0] or 0)
                    ctx["notif_messages_count"] = count_unread_messages(cur, user_id)
            if session.get("is_admin"):
                cur.execute("""
                    select id, username, created_at
                    from users
                    where is_approved = false
                    order by created_at;
                """)
                ctx["admin_pending_users"] = cur.fetchall()
                cur.execute("""
                    select id, full_name
                    from players
                    where is_active = true
                    order by full_name;
                """)
                ctx["admin_players"] = cur.fetchall()
    return ctx


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped_view


def messaging_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapped_view(*args, **kwargs):
        if not session.get("player_id"):
            return (
                "Din bruger er ikke koblet til en spiller. Kontakt en admin for at bruge beskeder.",
                403,
            )
        return view_func(*args, **kwargs)

    return wrapped_view


def can_edit_player(player_id: int) -> bool:
    if session.get("is_admin"):
        return True
    return session.get("player_id") == player_id


def can_change_own_password(player_id: int) -> bool:
    return (
        session.get("logged_in")
        and session.get("user_id")
        and session.get("player_id") == player_id
    )


def comparable_dt(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _highlight_names_with_score(rows, score_label="stableford"):
    names = [r[0] for r in rows]
    scores = [r[1] for r in rows if len(r) > 1 and r[1] is not None]
    value = ", ".join(names)
    if scores and len(set(scores)) == 1:
        return f"{value} ({scores[0]} {score_label})"
    if len(names) == 1 and scores:
        return f"{value} ({scores[0]} {score_label})"
    return value


def fetch_round_highlights(cur, round_id):
    """Varierede highlights — samme spiller vises kun én gang (vinder undtaget)."""
    items = []
    used_names = set()

    def add(label, name, detail=None):
        if not name or name in used_names:
            return
        value = f"{name} ({detail})" if detail is not None else name
        items.append({"label": label, "value": value})
        used_names.add(name)

    def add_group(label, rows, score_label="stableford"):
        eligible = [r for r in rows if r[0] not in used_names]
        if not eligible:
            return
        value = _highlight_names_with_score(eligible, score_label)
        items.append({"label": label, "value": value})
        used_names.update(r[0] for r in eligible)

    cur.execute(
        """
        select p.full_name, rp.stableford_points
        from round_players rp
        join players p on p.id = rp.player_id
        where rp.round_id = %s
          and rp.position = 1
          and rp.status = 'played'
        order by p.full_name;
        """,
        (round_id,),
    )
    winner_rows = cur.fetchall()
    if winner_rows:
        used_names.update(r[0] for r in winner_rows)
        items.append({
            "label": "Vinder",
            "value": _highlight_names_with_score(winner_rows),
        })

    cur.execute(
        """
        select p.full_name, rp.closest_to_pin_cm
        from round_players rp
        join players p on p.id = rp.player_id
        where rp.round_id = %s
          and rp.closest_to_pin_cm is not null
        order by rp.closest_to_pin_cm asc, p.full_name
        limit 1;
        """,
        (round_id,),
    )
    row = cur.fetchone()
    if row:
        add("Closest to pin", row[0], f"{row[1]} cm")

    cur.execute(
        """
        select p.full_name, rp.stableford_points
        from round_players rp
        join players p on p.id = rp.player_id
        where rp.round_id = %s
          and rp.status = 'played'
          and rp.stableford_points is not null
        order by rp.stableford_points asc, p.full_name
        limit 1;
        """,
        (round_id,),
    )
    row = cur.fetchone()
    if row:
        add("Today's bomb", row[0], f"{row[1]} stableford")

    cur.execute(
        """
        select p.full_name, rp.money_rank
        from round_players rp
        join players p on p.id = rp.player_id
        where rp.round_id = %s
          and rp.status = 'played'
          and rp.money_rank is not null
          and rp.money_rank > 0
        order by rp.money_rank desc, p.full_name
        limit 1;
        """,
        (round_id,),
    )
    row = cur.fetchone()
    if row:
        add("Flest fake money", row[0], f"{format_fake_money(row[1])} kr")

    cur.execute(
        """
        with non_winners as (
            select
                p.full_name,
                rp.stableford_points
            from round_players rp
            join players p on p.id = rp.player_id
            where rp.round_id = %s
              and rp.status = 'played'
              and rp.stableford_points is not null
              and (rp.position is null or rp.position > 1)
        ),
        best as (
            select max(stableford_points) as best_sf
            from non_winners
        )
        select nw.full_name, nw.stableford_points
        from non_winners nw
        cross join best b
        where nw.stableford_points = b.best_sf
        order by nw.full_name;
        """,
        (round_id,),
    )
    add_group("Bedste score uden sejr", cur.fetchall())

    if not items:
        return None

    return {"entries": items}


def fetch_course_stats(cur, season_year):
    cur.execute(
        """
        select
            c.name,
            count(distinct r.id)::int as rounds_played,
            round(avg(rp.stableford_points), 2) as avg_stableford,
            round(avg(rp.season_points), 2) as avg_points,
            max(rp.stableford_points) as best_stableford,
            min(rp.stableford_points) as worst_stableford
        from courses c
        inner join rounds r
            on r.course_id = c.id
            and r.season_year = %s
        inner join round_players rp
            on rp.round_id = r.id
            and rp.status = 'played'
        group by c.id, c.name
        order by c.name;
        """,
        (season_year,),
    )
    return cur.fetchall()


CHART_PLAYER_PALETTE = [
    "#22c55e",
    "#3b82f6",
    "#f59e0b",
    "#ef4444",
    "#a855f7",
    "#14b8a6",
    "#eab308",
    "#f97316",
    "#06b6d4",
    "#84cc16",
]


def _fetch_season_running_points_rows(cur, season_year):
    cur.execute(
        """
        with season_rounds as (
            select id as round_id, round_date
            from rounds
            where season_year = %s
            order by round_date, id
        ),
        active_players as (
            select
                p.id as player_id,
                p.full_name,
                (p.avatar_data is not null) as has_avatar
            from players p
            inner join round_players rp
                on rp.player_id = p.id
                and rp.status = 'played'
            inner join rounds r
                on r.id = rp.round_id
                and r.season_year = %s
            where p.is_active = true
            group by p.id, p.full_name, p.avatar_data
            having count(rp.id) > 0
        ),
        round_points as (
            select
                sr.round_id,
                sr.round_date,
                ap.player_id,
                ap.full_name,
                ap.has_avatar,
                coalesce(rp.season_points, 0) as season_points
            from season_rounds sr
            cross join active_players ap
            left join round_players rp
                on rp.round_id = sr.round_id
                and rp.player_id = ap.player_id
                and rp.status = 'played'
        ),
        running as (
            select
                round_id,
                round_date,
                player_id,
                full_name,
                has_avatar,
                sum(season_points) over (
                    partition by player_id
                    order by round_date, round_id
                    rows between unbounded preceding and current row
                ) as running_points
            from round_points
        )
        select
            round_id,
            round_date,
            player_id,
            full_name,
            has_avatar,
            running_points
        from running
        order by round_date, round_id, full_name;
        """,
        (season_year, season_year),
    )
    return cur.fetchall()


def _build_season_line_chart(rows, *, value_from_row):
    if not rows:
        return {"labels": [], "datasets": [], "y_max": 1}

    round_ids = []
    labels = []
    for row in rows:
        round_id = row[0]
        if round_id not in round_ids:
            round_ids.append(round_id)
            labels.append(str(row[1]))

    index_by_round_id = {round_id: idx for idx, round_id in enumerate(round_ids)}
    series_by_player = {}

    for row in rows:
        round_id = row[0]
        player_id = row[2]
        full_name = row[3]
        has_avatar = row[4]
        if player_id not in series_by_player:
            series_by_player[player_id] = {
                "player_id": player_id,
                "label": full_name,
                "has_avatar": bool(has_avatar),
                "initial": (full_name or "?")[0].upper(),
                "data": [None] * len(round_ids),
            }
        series_by_player[player_id]["data"][index_by_round_id[round_id]] = value_from_row(
            row
        )

    datasets = []
    y_max = 0
    for idx, player in enumerate(
        sorted(series_by_player.values(), key=lambda x: x["label"].lower())
    ):
        if not any(value is not None for value in player["data"]):
            continue
        color = CHART_PLAYER_PALETTE[idx % len(CHART_PLAYER_PALETTE)]
        for value in player["data"]:
            if value is not None:
                y_max = max(y_max, value)
        datasets.append(
            {
                "player_id": player["player_id"],
                "label": player["label"],
                "has_avatar": player["has_avatar"],
                "initial": player["initial"],
                "data": player["data"],
                "borderColor": color,
                "backgroundColor": color,
            }
        )

    if not datasets:
        return {"labels": labels, "datasets": [], "y_max": 1}

    return {"labels": labels, "datasets": datasets, "y_max": y_max}


def fetch_leaderboard_progress(cur, season_year):
    rows = _fetch_season_running_points_rows(cur, season_year)
    if not rows:
        return {"labels": [], "datasets": [], "max_rank": 1}

    round_ids = []
    labels = []
    for row in rows:
        round_id = row[0]
        if round_id not in round_ids:
            round_ids.append(round_id)
            labels.append(str(row[1]))

    index_by_round_id = {round_id: idx for idx, round_id in enumerate(round_ids)}
    position_by_round_player = {}

    for row in rows:
        round_id = row[0]
        player_id = row[2]
        running_points = float(row[5])
        position_by_round_player.setdefault(round_id, []).append(
            (player_id, row[3], row[4], running_points)
        )

    ranked_rows = []
    for round_id in round_ids:
        round_date = next(row[1] for row in rows if row[0] == round_id)
        entries = sorted(
            position_by_round_player[round_id],
            key=lambda item: (-item[3], item[1]),
        )
        for rank, (player_id, full_name, has_avatar, _) in enumerate(entries, start=1):
            ranked_rows.append(
                (round_id, round_date, player_id, full_name, has_avatar, rank)
            )

    chart = _build_season_line_chart(
        ranked_rows,
        value_from_row=lambda row: int(row[5]),
    )
    chart["max_rank"] = len(chart["datasets"]) or 1
    return chart


def fetch_leaderboard_points_progress(cur, season_year):
    rows = _fetch_season_running_points_rows(cur, season_year)
    chart = _build_season_line_chart(
        rows,
        value_from_row=lambda row: float(row[5]),
    )
    chart["max_points"] = chart.pop("y_max")
    if chart["max_points"] < 1:
        chart["max_points"] = 1
    return chart


def build_chart_series(progress_rows):
    return {
        "chart_labels": [str(row[1]) for row in progress_rows],
        "chart_positions": [
            int(row[10]) if row[10] is not None else None for row in progress_rows
        ],
        "chart_stableford": [
            int(row[4]) if row[7] == "played" and row[4] is not None else None
            for row in progress_rows
        ],
        "chart_round_points": [
            float(row[5]) if row[5] is not None else 0 for row in progress_rows
        ],
        "chart_round_money": [
            int(round(float(row[6]))) if row[6] is not None else 0
            for row in progress_rows
        ],
        "chart_running_points": [
            float(row[8]) if row[8] is not None else 0 for row in progress_rows
        ],
        "chart_running_money": [
            int(round(float(row[9]))) if row[9] is not None else 0
            for row in progress_rows
        ],
    }


def load_player_stats_bundle(cur, player_id):
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
            coalesce(sum(rp.money_rank), 0) as total_money,
            p.avatar_data is not null as has_avatar,
            p.last_known_handicap
        from players p
        left join round_players rp
            on rp.player_id = p.id
        where p.id = %s
          and p.is_active = true
        group by p.id, p.full_name, p.avatar_data, p.last_known_handicap;
        """,
        (player_id,),
    )
    player = cur.fetchone()
    if not player:
        return None

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
        (player_id,),
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
        (player_id, player_id, player_id),
    )
    progress_rows = cur.fetchall()

    return {
        "player": player,
        "last_5_rounds": last_5_rounds,
        **build_chart_series(progress_rows),
    }


def fetch_head_to_head_years(cur, player_id):
    cur.execute(
        """
        select distinct r.season_year
        from round_players me
        join round_players opp
            on opp.round_id = me.round_id
           and opp.player_id != me.player_id
           and opp.status = 'played'
        join rounds r on r.id = me.round_id
        where me.player_id = %s
          and me.status = 'played'
        order by r.season_year desc;
        """,
        (player_id,),
    )
    return [row[0] for row in cur.fetchall()]


def resolve_head_to_head_year(year_raw, available_years):
    if year_raw == "all":
        return None
    if year_raw.isdigit():
        year = int(year_raw)
        if year in available_years:
            return year
    if available_years:
        return available_years[0]
    return None


def fetch_head_to_head_summary(cur, player_id, season_year=None):
    params = [player_id]
    season_clause = ""
    if season_year is not None:
        season_clause = "and r.season_year = %s"
        params.append(season_year)

    cur.execute(
        f"""
        select
            opp_p.id,
            opp_p.full_name,
            opp_p.avatar_data is not null as has_avatar,
            count(*) filter (
                where me.stableford_points > opp.stableford_points
            ) as wins,
            count(*) filter (
                where me.stableford_points < opp.stableford_points
            ) as losses,
            count(*) filter (
                where me.stableford_points = opp.stableford_points
            ) as draws,
            count(*) as shared_rounds
        from round_players me
        join round_players opp
            on opp.round_id = me.round_id
           and opp.player_id != me.player_id
           and opp.status = 'played'
        join players opp_p
            on opp_p.id = opp.player_id
           and opp_p.is_active = true
        join rounds r on r.id = me.round_id
        where me.player_id = %s
          and me.status = 'played'
          {season_clause}
        group by opp_p.id, opp_p.full_name, opp_p.avatar_data
        having count(*) > 0
        order by
            count(*) filter (where me.stableford_points > opp.stableford_points)
            - count(*) filter (where me.stableford_points < opp.stableford_points) desc,
            opp_p.full_name;
        """,
        tuple(params),
    )
    return cur.fetchall()


def fetch_head_to_head_matchups(cur, player_id, opponent_id, season_year=None):
    # Placeholder order in SQL is: opponent first (join), then player (where).
    params = [opponent_id, player_id]
    season_clause = ""
    if season_year is not None:
        season_clause = "and r.season_year = %s"
        params.append(season_year)

    cur.execute(
        f"""
        select
            r.id,
            r.round_date,
            c.name,
            me.stableford_points,
            opp.stableford_points,
            case
                when me.stableford_points > opp.stableford_points then 'win'
                when me.stableford_points < opp.stableford_points then 'loss'
                else 'draw'
            end as result
        from round_players me
        join round_players opp
            on opp.round_id = me.round_id
           and opp.player_id = %s
           and opp.status = 'played'
        join rounds r on r.id = me.round_id
        join courses c on c.id = r.course_id
        where me.player_id = %s
          and me.status = 'played'
          {season_clause}
        order by r.round_date desc, r.id desc;
        """,
        tuple(params),
    )
    return cur.fetchall()


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
            session["username"] = "Admin"
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
                        p.avatar_data is not null as has_avatar,
                        coalesce(sum(rp.season_points), 0) as total_points
                    from players p
                    left join round_players rp
                        on rp.player_id = p.id
                    left join rounds r
                        on r.id = rp.round_id
                    cross join latest_round_cte lr
                    where p.is_active = true
                    and (r.season_year = lr.season_year or r.id is null)
                    group by p.id, p.full_name, p.avatar_data
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
                        has_avatar,
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
                    rc.has_avatar,
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
                        p.avatar_data is not null as has_avatar,
                        coalesce(sum(rp.money_rank), 0) as total_money
                    from players p
                    left join round_players rp
                        on rp.player_id = p.id
                    left join rounds r
                        on r.id = rp.round_id
                    cross join latest_round_cte lr
                    where p.is_active = true
                    and (r.season_year = lr.season_year or r.id is null)
                    group by p.id, p.full_name, p.avatar_data
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
                        has_avatar,
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
                    rc.has_avatar,
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

            cur.execute(
                """
                select id, title, event_date, emphasis,
                       course_image_data is not null as has_course_image
                from upcoming_events
                where event_date >= %s
                order by event_date asc, id asc;
                """,
                (date.today() - timedelta(days=400),),
            )
            upcoming_for_calendar = cur.fetchall()

            cur.execute(
                """
                select id, title, event_date, emphasis
                from upcoming_events
                where event_date >= current_date
                order by event_date asc, id asc
                limit 3;
                """
            )
            upcoming_next_three = cur.fetchall()

            cur.execute(
                """
                select ue.id
                from upcoming_events ue
                left join course_hero_assets cha on cha.id = ue.hero_asset_id
                where ue.event_date >= current_date
                  and (
                      cha.image_data is not null
                      or ue.course_image_data is not null
                  )
                order by ue.event_date asc, ue.id asc
                limit 1;
                """
            )
            bg_row = cur.fetchone()
            upcoming_bg_event_id = bg_row[0] if bg_row else None

            round_highlights = None
            if latest_round:
                round_highlights = fetch_round_highlights(cur, latest_round[0])

            home_backgrounds = fetch_home_backgrounds(cur)

            cur.execute(
                """
                select season_year
                from rounds
                order by round_date desc, id desc
                limit 1;
                """
            )
            season_row = cur.fetchone()
            home_season_year = (
                season_row[0] if season_row else date.today().year
            )
            points_progress = fetch_leaderboard_points_progress(
                cur, home_season_year
            )

    cal_events = [
        {"y": r[2].year, "m": r[2].month, "d": r[2].day, "t": r[1]}
        for r in upcoming_for_calendar
        if r[2] is not None
    ]
    next_event_key = None
    today_d = date.today()
    for r in upcoming_for_calendar:
        if r[2] is not None and r[2] >= today_d:
            next_event_key = f"{r[2].year}-{r[2].month}-{r[2].day}"
            break

    upcoming_cal_json = {
        "events": cal_events,
        "nextEventKey": next_event_key,
        "months": list(MONTH_NAMES_DA[1:]),
        "weekdays": list(WEEKDAY_SHORT_DA),
    }

    return render_template(
        "home.html",
        latest_round=latest_round,
        top_points=top_points,
        top_money=top_money,
        total_rounds=total_rounds,
        upcoming_next_three=upcoming_next_three,
        upcoming_bg_event_id=upcoming_bg_event_id,
        upcoming_cal_json=upcoming_cal_json,
        round_highlights=round_highlights,
        home_backgrounds=home_backgrounds,
        points_progress=points_progress,
        home_season_year=home_season_year,
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

    if session.get("user_id"):
        mark_notification_seen(session.get("user_id"), "rounds")
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


    if session.get("user_id"):
        mark_notification_seen(session.get("user_id"), "rounds")
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
            0
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


def ensure_fake_money_scale():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                alter table rounds
                alter column prize_pool set default {PRIZE_POOL_DEFAULT};
                """
            )
            cur.execute(
                """
                select count(*)
                from rounds
                where prize_pool >= %s;
                """,
                (LEGACY_PRIZE_POOL,),
            )
            if cur.fetchone()[0] == 0:
                conn.commit()
                return

            cur.execute(
                """
                update rounds
                set prize_pool = %s
                where prize_pool >= %s;
                """,
                (PRIZE_POOL_DEFAULT, LEGACY_PRIZE_POOL),
            )
            cur.execute("select id from rounds order by id;")
            for (round_id,) in cur.fetchall():
                recalculate_round(cur, round_id)
        conn.commit()


ensure_fake_money_scale()

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
    year_raw = (request.args.get("year") or "").strip()

    allowed_sorts = {
        "name": "full_name",
        "rounds_played": "rounds_played",
        "avg_stableford": "avg_stableford",
        "best_stableford": "best_stableford",
        "wins": "wins",
        "top3": "top3",
        "total_points": "total_points",
        "points_change": "(coalesce(ct.total_points, 0) - coalesce(pt.prev_points, 0))",
    }

    allowed_directions = {"asc", "desc"}

    order_by = allowed_sorts.get(sort, "wins")
    order_direction = direction if direction in allowed_directions else "desc"

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select distinct season_year
                from rounds
                order by season_year desc;
                """
            )
            available_years = [r[0] for r in cur.fetchall()]

            if available_years:
                if year_raw.isdigit() and int(year_raw) in available_years:
                    selected_year = int(year_raw)
                else:
                    selected_year = available_years[0]
            else:
                selected_year = date.today().year

            query = f"""
                with latest_round_cte as (
                    select id, round_date, season_year
                    from rounds
                    where season_year = %s
                    order by round_date desc, id desc
                    limit 1
                ),
                current_totals as (
                    select
                        p.id as player_id,
                        coalesce(sum(rp.season_points), 0) as total_points
                    from players p
                    left join round_players rp
                        on rp.player_id = p.id
                    left join rounds r
                        on r.id = rp.round_id
                        and r.season_year = %s
                    cross join latest_round_cte lr
                    where p.is_active = true
                    group by p.id
                ),
                previous_totals as (
                    select
                        p.id as player_id,
                        coalesce(sum(
                            case
                                when (
                                    r.round_date < lr.round_date
                                    or (r.round_date = lr.round_date and r.id < lr.id)
                                )
                                then rp.season_points
                                else 0
                            end
                        ), 0) as prev_points
                    from players p
                    left join round_players rp
                        on rp.player_id = p.id
                    left join rounds r
                        on r.id = rp.round_id
                        and r.season_year = %s
                    cross join latest_round_cte lr
                    where p.is_active = true
                    group by p.id
                )
                select
                    p.id,
                    p.full_name,
                    p.avatar_data is not null as has_avatar,
                    count(*) filter (where rp.status = 'played') as rounds_played,
                    round(avg(rp.stableford_points) filter (where rp.status = 'played'), 2) as avg_stableford,
                    max(rp.stableford_points) as best_stableford,
                    count(*) filter (where rp.position = 1) as wins,
                    count(*) filter (
                        where rp.position <= 3
                        and rp.position is not null
                    ) as top3,
                    coalesce(sum(rp.season_points), 0) as total_points,
                    coalesce(ct.total_points, 0) - coalesce(pt.prev_points, 0) as points_change
                from players p
                left join round_players rp
                    on rp.player_id = p.id
                left join rounds r
                    on r.id = rp.round_id
                    and r.season_year = %s
                left join current_totals ct
                    on ct.player_id = p.id
                left join previous_totals pt
                    on pt.player_id = p.id
                where p.is_active = true
                group by
                    p.id,
                    p.full_name,
                    p.avatar_data,
                    ct.total_points,
                    pt.prev_points
                having count(*) filter (where rp.status = 'played') > 0
                order by {order_by} {order_direction} nulls last, p.full_name;
            """
            cur.execute(
                query,
                (selected_year, selected_year, selected_year, selected_year),
            )
            player_stats = cur.fetchall()
            course_stats = fetch_course_stats(cur, selected_year)
            leaderboard_progress = fetch_leaderboard_progress(cur, selected_year)

    if session.get("user_id"):
        mark_notification_seen(session.get("user_id"), "leaderboard")
    return render_template(
        "stats.html",
        player_stats=player_stats,
        course_stats=course_stats,
        leaderboard_progress=leaderboard_progress,
        sort=sort,
        direction=direction,
        selected_year=selected_year,
        available_years=available_years,
    )


@app.get("/qa")
def qa():
    return render_template("qa.html")


@app.get("/players")
def players():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    p.id,
                    p.full_name,
                    p.avatar_data is not null as has_avatar,
                    u.id as linked_user_id,
                    count(*) filter (where rp.status = 'played') as rounds_played
                from players p
                left join users u on u.player_id = p.id and u.is_approved = true
                left join round_players rp on rp.player_id = p.id
                where p.is_active = true
                group by p.id, p.full_name, p.avatar_data, u.id
                order by p.full_name;
                """
            )
            player_rows = cur.fetchall()

    active_players = [row for row in player_rows if int(row[4] or 0) > 0]
    inactive_players = [row for row in player_rows if int(row[4] or 0) == 0]

    return render_template(
        "players.html",
        players=active_players,
        inactive_players=inactive_players,
    )

@app.get("/health")
def health():
    return {"ok": True}, 200

@app.get("/player/<int:player_id>")
def player_page(player_id):
    h2h_year_raw = (request.args.get("h2h_year") or "").strip()

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            bundle = load_player_stats_bundle(cur, player_id)
            if not bundle:
                return "Spilleren blev ikke fundet", 404
            player = bundle["player"]
            h2h_available_years = fetch_head_to_head_years(cur, player_id)
            h2h_season_year = resolve_head_to_head_year(
                h2h_year_raw, h2h_available_years
            )
            if h2h_year_raw == "all":
                h2h_selected = "all"
            elif h2h_season_year is not None:
                h2h_selected = str(h2h_season_year)
            else:
                h2h_selected = "all"
            head_to_head = fetch_head_to_head_summary(
                cur, player_id, h2h_season_year
            )
            cur.execute(
                """
                select id
                from users
                where player_id = %s and is_approved = true
                limit 1;
                """,
                (player_id,),
            )
            linked_user = cur.fetchone()

    is_own_profile = session.get("player_id") == player_id
    message_user_id = None
    if (
        linked_user
        and session.get("player_id")
        and session.get("user_id")
        and int(linked_user[0]) != int(session["user_id"])
    ):
        message_user_id = int(linked_user[0])

    return render_template(
        "player.html",
        player=player,
        is_own_profile=is_own_profile,
        message_user_id=message_user_id,
        can_edit_player_profile=can_edit_player(player_id),
        handicap_display=format_handicap_dk(player[10]),
        last_5_rounds=bundle["last_5_rounds"],
        chart_labels=bundle["chart_labels"],
        chart_positions=bundle["chart_positions"],
        chart_stableford=bundle["chart_stableford"],
        chart_round_points=bundle["chart_round_points"],
        chart_round_money=bundle["chart_round_money"],
        chart_running_points=bundle["chart_running_points"],
        chart_running_money=bundle["chart_running_money"],
        head_to_head=head_to_head,
        h2h_available_years=h2h_available_years,
        h2h_selected=h2h_selected,
    )


@app.get("/player/<int:player_id>/mod/<int:opponent_id>")
def player_head_to_head_detail(player_id, opponent_id):
    h2h_year_raw = (request.args.get("h2h_year") or "").strip()

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, full_name, avatar_data is not null as has_avatar
                from players
                where id = %s and is_active = true;
                """,
                (player_id,),
            )
            player = cur.fetchone()
            if not player:
                return "Spilleren blev ikke fundet", 404

            cur.execute(
                """
                select id, full_name, avatar_data is not null as has_avatar
                from players
                where id = %s and is_active = true;
                """,
                (opponent_id,),
            )
            opponent = cur.fetchone()
            if not opponent:
                return "Modstanderen blev ikke fundet", 404

            h2h_available_years = fetch_head_to_head_years(cur, player_id)
            h2h_season_year = resolve_head_to_head_year(
                h2h_year_raw, h2h_available_years
            )
            if h2h_year_raw == "all":
                h2h_selected = "all"
            elif h2h_season_year is not None:
                h2h_selected = str(h2h_season_year)
            else:
                h2h_selected = "all"

            matchups = fetch_head_to_head_matchups(
                cur, player_id, opponent_id, h2h_season_year
            )
            if not matchups:
                return "Ingen fælles runder fundet for det valgte filter", 404

            summary = fetch_head_to_head_summary(
                cur, player_id, h2h_season_year
            )
            opponent_summary = next(
                (row for row in summary if row[0] == opponent_id), None
            )

    is_own_profile = session.get("player_id") == player_id

    return render_template(
        "player_head_to_head.html",
        player=player,
        opponent=opponent,
        opponent_summary=opponent_summary,
        matchups=matchups,
        is_own_profile=is_own_profile,
        h2h_available_years=h2h_available_years,
        h2h_selected=h2h_selected,
    )


@app.get("/player/<int:player_id>/avatar")
def player_avatar(player_id):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select avatar_data, avatar_mime_type
                from players
                where id = %s
                and is_active = true;
                """,
                (player_id,)
            )
            row = cur.fetchone()

    if not row or not row[0]:
        return "Avatar ikke fundet", 404

    avatar_data, mime_type = row
    safe_mime = mime_type if mime_type else "image/png"
    return send_file(
        io.BytesIO(avatar_data),
        mimetype=safe_mime,
        as_attachment=False,
        download_name=f"player-{player_id}-avatar"
    )


@app.post("/player/<int:player_id>/avatar")
@login_required
def upload_player_avatar(player_id):
    settings_url = url_for("player_settings", player_id=player_id) + "#billede"

    if not can_edit_player(player_id):
        return "Du har ikke adgang til at redigere dette profilbillede", 403

    avatar = request.files.get("avatar")
    if not avatar or not avatar.filename:
        return redirect(settings_url)

    avatar_bytes = avatar.read()
    if not avatar_bytes:
        return redirect(settings_url)

    max_size_bytes = 2 * 1024 * 1024
    if len(avatar_bytes) > max_size_bytes:
        return "Billedet er for stort (maks 2 MB)", 400

    allowed_mimes = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    mime_type = avatar.mimetype or "application/octet-stream"
    if mime_type not in allowed_mimes:
        return "Kun JPG, PNG, WEBP eller GIF er tilladt", 400

    try:
        compressed_avatar_bytes, compressed_mime_type = compress_avatar_image(avatar_bytes)
    except ValueError as err:
        return str(err), 400

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            # Clear old avatar explicitly before storing the new one.
            cur.execute(
                """
                update players
                set avatar_data = null,
                    avatar_mime_type = null
                where id = %s
                and is_active = true;
                """,
                (player_id,)
            )
            cur.execute(
                """
                update players
                set avatar_data = %s,
                    avatar_mime_type = %s
                where id = %s
                and is_active = true;
                """,
                (compressed_avatar_bytes, compressed_mime_type, player_id)
            )
        conn.commit()

    return redirect(url_for("player_page", player_id=player_id))


@app.get("/player/<int:player_id>/settings")
@login_required
def player_settings(player_id):
    if not can_edit_player(player_id):
        return "Du har ikke adgang til at redigere denne profil", 403

    hcp_error = request.args.get("hcp_error", "").strip() or None

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select full_name, last_known_handicap
                from players
                where id = %s and is_active = true;
                """,
                (player_id,),
            )
            row = cur.fetchone()

    if not row:
        return "Spilleren blev ikke fundet", 404

    full_name, last_hcp = row[0], row[1]
    hcp_display = format_handicap_dk(last_hcp) or ""

    return render_template(
        "player_settings.html",
        player_id=player_id,
        player_name=full_name,
        hcp_display=hcp_display,
        hcp_error=hcp_error,
        can_change_password=can_change_own_password(player_id),
        password_error=session.pop("password_change_error", None),
        password_success=session.pop("password_change_success", None),
    )


@app.post("/player/<int:player_id>/password")
@login_required
def change_player_password(player_id):
    if not can_change_own_password(player_id):
        return "Du kan kun ændre kodeord for din egen konto", 403

    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "").strip()
    confirm_password = request.form.get("confirm_password", "").strip()
    settings_url = url_for("player_settings", player_id=player_id) + "#kodeord"

    if len(new_password) < 6:
        session["password_change_error"] = "Nyt kodeord skal være mindst 6 tegn."
        return redirect(settings_url)

    if len(new_password) > 128:
        session["password_change_error"] = "Nyt kodeord må højst være 128 tegn."
        return redirect(settings_url)

    if new_password != confirm_password:
        session["password_change_error"] = "Det nye kodeord og bekræftelsen matcher ikke."
        return redirect(settings_url)

    user_id = session.get("user_id")
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select password_hash
                from users
                where id = %s and player_id = %s;
                """,
                (user_id, player_id),
            )
            row = cur.fetchone()

            if not row:
                return "Brugerkontoen blev ikke fundet", 404

            if not check_password_hash(row[0], current_password):
                session["password_change_error"] = "Nuværende kodeord er forkert."
                return redirect(settings_url)

            cur.execute(
                """
                update users
                set password_hash = %s
                where id = %s and player_id = %s;
                """,
                (generate_password_hash(new_password), user_id, player_id),
            )
            if cur.rowcount == 0:
                return "Brugerkontoen blev ikke fundet", 404
        conn.commit()

    session["password_change_success"] = True
    return redirect(settings_url)


@app.post("/player/<int:player_id>/handicap")
@login_required
def update_player_handicap(player_id):
    if not can_edit_player(player_id):
        return "Du har ikke adgang til at redigere denne profil", 403

    raw = request.form.get("last_known_handicap", "")
    value, err = parse_last_known_handicap_form(raw)
    if err:
        return redirect(
            url_for("player_settings", player_id=player_id, hcp_error=err) + "#hcp"
        )

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update players
                set last_known_handicap = %s
                where id = %s and is_active = true;
                """,
                (value, player_id),
            )
            if cur.rowcount == 0:
                return "Spilleren blev ikke fundet", 404
        conn.commit()

    return redirect(url_for("player_page", player_id=player_id) + "#profil")


@app.get("/forum")
@login_required
def forum():
    user_id = session.get("user_id")
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select
                    t.id,
                    t.title,
                    t.is_sticky,
                    t.created_at,
                    count(p.id) as post_count,
                    max(p.created_at) as latest_post,
                    coalesce(ftr.last_seen_post_at, to_timestamp(0)) as last_seen_post_at
                from forum_threads t
                left join forum_posts p on p.thread_id = t.id
                left join forum_thread_reads ftr
                    on ftr.thread_id = t.id
                    and ftr.user_id = %s
                group by t.id, t.title, t.created_at
                       , t.is_sticky, ftr.last_seen_post_at
                order by t.is_sticky desc, coalesce(max(p.created_at), t.created_at) desc;
            """, (user_id,))
            threads = cur.fetchall()

    mark_notification_seen(session.get("user_id"), "forum")
    unread_threads_count = 0
    prepared_threads = []
    for t in threads:
        thread_id, title, is_sticky, created_at, post_count, latest_post, last_seen_post_at = t
        last_activity = comparable_dt(latest_post or created_at)
        seen_at = comparable_dt(last_seen_post_at)
        has_unread = bool(last_activity and seen_at and last_activity > seen_at)
        if has_unread:
            unread_threads_count += 1
        prepared_threads.append(
            {
                "id": thread_id,
                "title": title,
                "is_sticky": bool(is_sticky),
                "created_at": created_at,
                "post_count": int(post_count or 0),
                "latest_post": latest_post,
                "has_unread": has_unread,
            }
        )

    return render_template(
        "forum.html",
        threads=prepared_threads,
        unread_threads_count=unread_threads_count,
    )


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
                insert into forum_threads (user_id, title)
                values (%s, %s)
                returning id;
            """, (user_id, title))
            thread_id = cur.fetchone()[0]

            
            cur.execute("""
                insert into forum_posts (thread_id, user_id, author_name, body)
                values (%s, %s, %s, %s);
            """, (thread_id, user_id, author_name, body))
            mark_forum_thread_seen(cur, user_id, thread_id)

        conn.commit()

    return redirect(f"/forum/{thread_id}")


@app.get("/forum/<int:thread_id>")
@login_required
def forum_thread(thread_id):
    user_id = session.get("user_id")
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select id, title, created_at, is_sticky
                from forum_threads
                where id = %s;
            """, (thread_id,))
            thread = cur.fetchone()

            if not thread:
                return "Tråden blev ikke fundet", 404

            cur.execute("""
                select
                    fp.id,
                    fp.author_name,
                    fp.body,
                    fp.created_at,
                    u.player_id,
                    coalesce(p.avatar_data is not null, false) as has_avatar,
                    fp.parent_post_id,
                    pp.author_name as parent_author_name,
                    pp.body as parent_body,
                    count(fpl.user_id)::int as like_count,
                    bool_or(fpl.user_id = %s) as liked_by_me
                from forum_posts fp
                left join users u on u.id = fp.user_id
                left join players p on p.id = u.player_id
                left join forum_posts pp on pp.id = fp.parent_post_id
                left join forum_post_likes fpl on fpl.post_id = fp.id
                where fp.thread_id = %s
                group by
                    fp.id,
                    fp.author_name,
                    fp.body,
                    fp.created_at,
                    u.player_id,
                    p.avatar_data,
                    fp.parent_post_id,
                    pp.author_name,
                    pp.body
                order by fp.created_at asc;
            """, (user_id, thread_id))
            posts = cur.fetchall()
            mark_forum_thread_seen(cur, user_id, thread_id)
        conn.commit()

    mark_notification_seen(session.get("user_id"), "forum")
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
    reply_to_raw = (request.form.get("reply_to_post_id") or "").strip()
    parent_post_id = None
    if reply_to_raw:
        try:
            parent_post_id = int(reply_to_raw)
        except ValueError:
            parent_post_id = None

    if not author_name or not body:
        return redirect(f"/forum/{thread_id}")

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            if parent_post_id is not None:
                cur.execute(
                    """
                    select 1
                    from forum_posts
                    where id = %s
                      and thread_id = %s;
                    """,
                    (parent_post_id, thread_id),
                )
                if not cur.fetchone():
                    parent_post_id = None
            cur.execute("""
                insert into forum_posts (thread_id, user_id, author_name, body, parent_post_id)
                values (%s, %s, %s, %s, %s);
            """, (thread_id, user_id, author_name, body, parent_post_id))
            mark_forum_thread_seen(cur, user_id, thread_id)

        conn.commit()

    return redirect(f"/forum/{thread_id}")


@app.post("/forum/<int:thread_id>/sticky")
@admin_required
def toggle_forum_thread_sticky(thread_id):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update forum_threads
                set is_sticky = not is_sticky
                where id = %s
                returning is_sticky;
                """,
                (thread_id,),
            )
            row = cur.fetchone()
            if not row:
                return "Tråden blev ikke fundet", 404
        conn.commit()
    return redirect(request.referrer or url_for("forum_thread", thread_id=thread_id))


@app.post("/forum/post/<int:post_id>/like")
@login_required
def toggle_forum_post_like(post_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select thread_id
                from forum_posts
                where id = %s;
                """,
                (post_id,),
            )
            post = cur.fetchone()
            if not post:
                return "Indlæg blev ikke fundet", 404
            thread_id = post[0]

            cur.execute(
                """
                select 1
                from forum_post_likes
                where post_id = %s and user_id = %s;
                """,
                (post_id, user_id),
            )
            already_liked = cur.fetchone() is not None

            if already_liked:
                cur.execute(
                    """
                    delete from forum_post_likes
                    where post_id = %s and user_id = %s;
                    """,
                    (post_id, user_id),
                )
            else:
                cur.execute(
                    """
                    insert into forum_post_likes (post_id, user_id)
                    values (%s, %s)
                    on conflict (post_id, user_id) do nothing;
                    """,
                    (post_id, user_id),
                )
        conn.commit()

    return redirect(url_for("forum_thread", thread_id=thread_id))


@app.get("/beskeder")
@messaging_required
def messages_inbox():
    user_id = session["user_id"]
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            inbox = fetch_inbox_rows(cur, user_id)
    return render_template("messages_inbox.html", inbox=inbox)


@app.get("/beskeder/ny")
@messaging_required
def messages_new():
    user_id = session["user_id"]
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                MESSAGING_USER_SQL
                + """
                and u.id <> %s
                order by p.full_name;
                """,
                (user_id,),
            )
            messaging_users = cur.fetchall()
    return render_template(
        "messages_new.html",
        messaging_users=messaging_users,
        form_error=request.args.get("error"),
    )


@app.post("/beskeder/direct")
@messaging_required
def messages_create_direct():
    user_id = session["user_id"]
    other_user_id = request.form.get("other_user_id", type=int)
    if not other_user_id or other_user_id == user_id:
        return redirect(url_for("messages_new"))

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            if not is_messaging_user(cur, other_user_id):
                return "Du kan kun skrive til brugere med koblet spillerprofil", 400
            conversation_id = get_or_create_direct_conversation(cur, user_id, other_user_id)
        conn.commit()

    return redirect(url_for("messages_thread", conversation_id=conversation_id))


@app.get("/beskeder/med/<int:other_user_id>")
@messaging_required
def messages_start_direct(other_user_id):
    user_id = session["user_id"]
    if other_user_id == user_id:
        return redirect(url_for("messages_inbox"))

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            if not is_messaging_user(cur, other_user_id):
                return "Du kan kun skrive til brugere med koblet spillerprofil", 400
            conversation_id = get_or_create_direct_conversation(cur, user_id, other_user_id)
        conn.commit()

    return redirect(url_for("messages_thread", conversation_id=conversation_id))


@app.post("/beskeder/gruppe")
@messaging_required
def messages_create_group():
    user_id = session["user_id"]
    title = (request.form.get("title") or "").strip()
    member_ids = {
        int(mid)
        for mid in request.form.getlist("member_ids")
        if str(mid).isdigit()
    }
    member_ids.discard(user_id)

    if not title or len(title) > 120:
        return redirect(url_for("messages_new", error="title"))
    if not member_ids:
        return redirect(url_for("messages_new", error="members"))

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            allowed = fetch_messaging_user_ids(cur)
            if not member_ids.issubset(allowed):
                return "Alle medlemmer skal have en koblet spillerprofil", 400

            cur.execute(
                """
                insert into conversations (kind, title, created_by_user_id)
                values ('group', %s, %s)
                returning id;
                """,
                (title, user_id),
            )
            conversation_id = int(cur.fetchone()[0])
            all_members = {user_id} | member_ids
            for member_id in all_members:
                cur.execute(
                    """
                    insert into conversation_members (conversation_id, user_id)
                    values (%s, %s);
                    """,
                    (conversation_id, member_id),
                )
        conn.commit()

    return redirect(url_for("messages_thread", conversation_id=conversation_id))


@app.get("/beskeder/<int:conversation_id>")
@messaging_required
def messages_thread(conversation_id):
    user_id = session["user_id"]
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            if not user_in_conversation(cur, user_id, conversation_id):
                return "Samtalen blev ikke fundet", 404

            cur.execute(
                """
                select kind, title
                from conversations
                where id = %s;
                """,
                (conversation_id,),
            )
            conv = cur.fetchone()
            if not conv:
                return "Samtalen blev ikke fundet", 404
            kind, title = conv

            if kind == "direct":
                cur.execute(
                    """
                    select p.full_name
                    from conversation_members cm
                    join users u on u.id = cm.user_id
                    join players p on p.id = u.player_id
                    where cm.conversation_id = %s
                      and cm.user_id <> %s
                    limit 1;
                    """,
                    (conversation_id, user_id),
                )
                row = cur.fetchone()
                display_name = row[0] if row else "Samtale"
            else:
                display_name = title or "Gruppe"

            cur.execute(
                """
                select
                    m.id,
                    m.body,
                    m.created_at,
                    m.sender_user_id,
                    p.id,
                    p.full_name,
                    (p.avatar_data is not null) as has_avatar
                from messages m
                join users u on u.id = m.sender_user_id
                left join players p on p.id = u.player_id
                where m.conversation_id = %s
                order by m.created_at asc, m.id asc;
                """,
                (conversation_id,),
            )
            messages = cur.fetchall()

            cur.execute(
                """
                select
                    cm.user_id,
                    p.full_name,
                    (p.avatar_data is not null) as has_avatar,
                    p.id as player_id
                from conversation_members cm
                join users u on u.id = cm.user_id
                join players p on p.id = u.player_id
                where cm.conversation_id = %s
                order by p.full_name;
                """,
                (conversation_id,),
            )
            members = cur.fetchall()

            mark_conversation_read(cur, user_id, conversation_id)
        conn.commit()

    return render_template(
        "messages_thread.html",
        conversation_id=conversation_id,
        display_name=display_name,
        kind=kind,
        messages=messages,
        members=members,
    )


@app.post("/beskeder/<int:conversation_id>/send")
@messaging_required
def messages_send(conversation_id):
    user_id = session["user_id"]
    body = (request.form.get("body") or "").strip()
    if not body or len(body) > 4000:
        return redirect(url_for("messages_thread", conversation_id=conversation_id))

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            if not user_in_conversation(cur, user_id, conversation_id):
                return "Samtalen blev ikke fundet", 404
            cur.execute(
                """
                insert into messages (conversation_id, sender_user_id, body)
                values (%s, %s, %s);
                """,
                (conversation_id, user_id, body),
            )
            mark_conversation_read(cur, user_id, conversation_id)
        conn.commit()

    return redirect(url_for("messages_thread", conversation_id=conversation_id))


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
                        values (%s, %s, false);
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

        if not user[3]:
            return render_template(
                "login.html",
                error="Din konto afventer godkendelse fra en administrator. Skriv til Mads for hurtig godkendelse.",
            )

        session["logged_in"] = True
        session["user_id"] = user[0]
        session["is_admin"] = user[2]
        session["player_id"] = user[4]
        session["username"] = username

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
            pending_users = cur.fetchall()
            cur.execute(
                """
                select
                    u.id,
                    u.username,
                    u.created_at,
                    p.full_name
                from users u
                left join players p on p.id = u.player_id
                where u.is_approved = true
                order by u.username;
                """
            )
            approved_users = cur.fetchall()
            cur.execute("""
                select id, full_name
                from players
                where is_active = true
                order by full_name;
            """)
            admin_players = cur.fetchall()

    return render_template(
        "admin_users.html",
        pending_users=pending_users,
        approved_users=approved_users,
        admin_players=admin_players,
        password_reset_notice=pop_password_reset_notice(),
        password_reset_error=session.pop("admin_password_reset_error", None),
    )

@app.post("/admin/users/<int:user_id>/approve")
@admin_required
def approve_user(user_id):
    player_raw = request.form.get("player_id", "").strip()
    candidate = None
    if player_raw:
        try:
            candidate = int(player_raw)
        except ValueError:
            candidate = None

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            resolved_player_id = None
            if candidate is not None:
                cur.execute("select 1 from players where id = %s;", (candidate,))
                if cur.fetchone():
                    resolved_player_id = candidate

            if resolved_player_id is not None:
                cur.execute(
                    """
                    update users
                    set is_approved = true,
                        player_id = %s
                    where id = %s;
                    """,
                    (resolved_player_id, user_id),
                )
            else:
                cur.execute("""
                    update users
                    set is_approved = true
                    where id = %s;
                """, (user_id,))
        conn.commit()

    return redirect_after_admin_action()


@app.post("/admin/users/<int:user_id>/reset-password")
@admin_required
def admin_reset_user_password(user_id):
    generate_new = request.form.get("generate_password") == "on"
    manual_password = request.form.get("new_password", "").strip()

    if generate_new:
        new_password = generate_temporary_password()
    else:
        if len(manual_password) < 6:
            session["admin_password_reset_error"] = (
                "Kodeord skal være mindst 6 tegn, eller vælg «Generer midlertidigt kodeord»."
            )
            return redirect(url_for("admin_users"))
        if len(manual_password) > 128:
            session["admin_password_reset_error"] = "Kodeord må højst være 128 tegn."
            return redirect(url_for("admin_users"))
        new_password = manual_password

    password_hash = generate_password_hash(new_password)

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update users
                set password_hash = %s
                where id = %s
                returning username;
                """,
                (password_hash, user_id),
            )
            row = cur.fetchone()
            if not row:
                return "Brugeren blev ikke fundet", 404
        conn.commit()

    session.pop("admin_password_reset_error", None)
    session["admin_password_reset"] = {
        "username": row[0],
        "password": new_password,
    }
    return redirect(url_for("admin_users"))


def fetch_event_detail(cur, event_id):
    cur.execute(
        """
        select
            ue.id,
            ue.title,
            ue.event_date,
            ue.emphasis,
            (
                cha.image_data is not null
                or ue.course_image_data is not null
            ) as has_image
        from upcoming_events ue
        left join course_hero_assets cha on cha.id = ue.hero_asset_id
        where ue.id = %s;
        """,
        (event_id,),
    )
    event = cur.fetchone()
    if not event:
        return None

    cur.execute(
        """
        select
            p.id,
            p.full_name,
            p.avatar_data is not null as has_avatar
        from event_rsvps er
        join players p on p.id = er.player_id
        where er.event_id = %s
          and er.status = 'yes'
          and p.is_active = true
        order by p.full_name;
        """,
        (event_id,),
    )
    attending = cur.fetchall()

    cur.execute(
        """
        select
            p.id,
            p.full_name,
            p.avatar_data is not null as has_avatar
        from event_rsvps er
        join players p on p.id = er.player_id
        where er.event_id = %s
          and er.status = 'no'
          and p.is_active = true
        order by p.full_name;
        """,
        (event_id,),
    )
    not_attending = cur.fetchall()

    cur.execute(
        """
        select
            p.id,
            p.full_name,
            p.avatar_data is not null as has_avatar
        from players p
        where p.is_active = true
          and not exists (
              select 1
              from event_rsvps er
              where er.event_id = %s
                and er.player_id = p.id
          )
        order by p.full_name;
        """,
        (event_id,),
    )
    pending = cur.fetchall()

    return {
        "event": event,
        "attending": attending,
        "not_attending": not_attending,
        "pending": pending,
    }


@app.post("/admin/sidebar-update")
@admin_required
def admin_sidebar_update():
    body = request.form.get("body", "").strip()
    if not body:
        return redirect(request.referrer or url_for("home"))

    if len(body) > 500:
        body = body[:500]

    author = session.get("username") or "Admin"

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into sidebar_updates (body, author_username)
                values (%s, %s);
                """,
                (body, author),
            )
        conn.commit()

    return redirect(request.referrer or url_for("home"))


@app.get("/begivenheder")
def upcoming_events_page():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    ue.id,
                    ue.title,
                    ue.event_date,
                    ue.emphasis,
                    (
                        cha.image_data is not null
                        or ue.course_image_data is not null
                    ) as has_image
                from upcoming_events ue
                left join course_hero_assets cha on cha.id = ue.hero_asset_id
                where ue.event_date >= current_date
                order by ue.event_date asc, ue.id asc;
                """
            )
            events = cur.fetchall()

    if session.get("user_id"):
        mark_notification_seen(session.get("user_id"), "events")
    return render_template("upcoming_events.html", events=events)


@app.get("/begivenheder/<int:event_id>")
def event_detail_page(event_id):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            detail = fetch_event_detail(cur, event_id)

    if not detail:
        return "Begivenheden blev ikke fundet", 404

    event = detail["event"]
    my_rsvp = None
    can_rsvp = False
    rsvp_blocked_reason = None

    if session.get("logged_in"):
        player_id = session.get("player_id")
        if not player_id:
            rsvp_blocked_reason = "Din bruger er ikke koblet til en spiller endnu."
        elif event[2] < date.today():
            rsvp_blocked_reason = "Begivenheden er overstået — tilmelding er lukket."
        else:
            can_rsvp = True
            with psycopg.connect(DB_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        select status
                        from event_rsvps
                        where event_id = %s and player_id = %s;
                        """,
                        (event_id, player_id),
                    )
                    row = cur.fetchone()
                    if row:
                        my_rsvp = row[0]
    else:
        rsvp_blocked_reason = "Log ind for at melde deltagelse."

    if session.get("user_id"):
        mark_notification_seen(session.get("user_id"), "events")
    return render_template(
        "event_detail.html",
        event=event,
        attending=detail["attending"],
        not_attending=detail["not_attending"],
        pending=detail["pending"],
        my_rsvp=my_rsvp,
        can_rsvp=can_rsvp,
        rsvp_blocked_reason=rsvp_blocked_reason,
        is_past=event[2] < date.today(),
    )


@app.post("/begivenheder/<int:event_id>/rsvp")
@login_required
def event_rsvp(event_id):
    player_id = session.get("player_id")
    if not player_id:
        return "Din bruger er ikke koblet til en spiller endnu", 403

    status = request.form.get("status", "").strip()
    if status not in ("yes", "no"):
        return redirect(url_for("event_detail_page", event_id=event_id))

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select event_date
                from upcoming_events
                where id = %s;
                """,
                (event_id,),
            )
            row = cur.fetchone()
            if not row:
                return "Begivenheden blev ikke fundet", 404
            if row[0] < date.today():
                return redirect(url_for("event_detail_page", event_id=event_id))

            cur.execute(
                """
                insert into event_rsvps (event_id, player_id, status, updated_at)
                values (%s, %s, %s, now())
                on conflict (event_id, player_id)
                do update set
                    status = excluded.status,
                    updated_at = now();
                """,
                (event_id, player_id, status),
            )
        conn.commit()

    return redirect(url_for("event_detail_page", event_id=event_id))


@app.get("/upcoming-events/<int:event_id>/course-image")
def upcoming_event_course_image(event_id):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select a.image_data, a.image_mime_type,
                       ue.course_image_data, ue.course_image_mime_type
                from upcoming_events ue
                left join course_hero_assets a on a.id = ue.hero_asset_id
                where ue.id = %s;
                """,
                (event_id,),
            )
            row = cur.fetchone()

    if not row:
        return "Billede ikke fundet", 404
    asset_data, asset_mime, legacy_data, legacy_mime = row
    if asset_data:
        data, mime = asset_data, asset_mime
    elif legacy_data:
        data, mime = legacy_data, legacy_mime
    else:
        return "Billede ikke fundet", 404

    safe_mime = mime if mime else "image/webp"
    return send_file(
        io.BytesIO(data),
        mimetype=safe_mime,
        as_attachment=False,
        download_name=f"event-{event_id}-course",
    )


@app.get("/home-panel/<slug>/image")
def home_panel_image(slug):
    if slug not in HOME_PANEL_SLUGS:
        return "Billede ikke fundet", 404

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select a.image_data, a.image_mime_type
                from home_panels hp
                join course_hero_assets a on a.id = hp.hero_asset_id
                where hp.slug = %s;
                """,
                (slug,),
            )
            row = cur.fetchone()

    if not row or not row[0]:
        return "Billede ikke fundet", 404

    data, mime = row
    return send_file(
        io.BytesIO(data),
        mimetype=mime or "image/webp",
        as_attachment=False,
        download_name=f"home-{slug}",
    )


@app.get("/admin/forside")
@admin_required
def admin_home():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    hp.slug,
                    hp.label,
                    hp.hero_asset_id,
                    a.byte_size
                from home_panels hp
                left join course_hero_assets a on a.id = hp.hero_asset_id
                order by hp.sort_order, hp.slug;
                """
            )
            panels = cur.fetchall()
            hero_assets = list_hero_assets_for_admin(cur)

    return render_template(
        "admin_home.html",
        panels=panels,
        hero_assets=hero_assets,
    )


@app.post("/admin/forside/<slug>")
@admin_required
def admin_home_panel_update(slug):
    if slug not in HOME_PANEL_SLUGS:
        return "Ukendt boks", 404

    if request.form.get("clear_background") == "1":
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update home_panels
                    set hero_asset_id = null
                    where slug = %s;
                    """,
                    (slug,),
                )
                delete_orphan_course_hero_assets(cur)
            conn.commit()
        return redirect(url_for("admin_home"))

    hero_asset_id = None
    image_file = request.files.get("course_image")
    if image_file and image_file.filename:
        raw = image_file.read()
        if raw and len(raw) <= 8 * 1024 * 1024:
            try:
                image_bytes, mime_store = compress_course_hero_image(raw)
            except ValueError:
                return redirect(url_for("admin_home"))
            with psycopg.connect(DB_URL) as conn:
                with conn.cursor() as cur:
                    hero_asset_id = get_or_create_course_hero_asset(
                        cur, image_bytes, mime_store
                    )
                conn.commit()

    if hero_asset_id is None:
        reuse_raw = (request.form.get("reuse_hero_asset_id") or "").strip()
        if reuse_raw:
            try:
                candidate = int(reuse_raw)
            except ValueError:
                candidate = None
            if candidate is not None:
                with psycopg.connect(DB_URL) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "select id from course_hero_assets where id = %s;",
                            (candidate,),
                        )
                        if cur.fetchone():
                            hero_asset_id = candidate

    if hero_asset_id is not None:
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update home_panels
                    set hero_asset_id = %s
                    where slug = %s;
                    """,
                    (hero_asset_id, slug),
                )
            conn.commit()

    return redirect(url_for("admin_home"))


@app.get("/admin/events")
@admin_required
def admin_events():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    ue.id,
                    ue.title,
                    ue.event_date,
                    ue.emphasis,
                    (ue.hero_asset_id is not null or ue.course_image_data is not null) as has_image
                from upcoming_events ue
                order by ue.event_date asc, ue.id asc;
                """
            )
            events = cur.fetchall()
            cur.execute(
                """
                select
                    a.id,
                    a.byte_size,
                    a.content_sha256,
                    (select count(*)::int from upcoming_events e where e.hero_asset_id = a.id) as ref_count
                from course_hero_assets a
                order by a.id desc
                limit 50;
                """
            )
            hero_assets = cur.fetchall()

    return render_template("admin_events.html", events=events, hero_assets=hero_assets)


@app.post("/admin/events/add")
@admin_required
def admin_events_add():
    title = (request.form.get("title") or "").strip()
    date_raw = (request.form.get("event_date") or "").strip()
    emphasis = request.form.get("emphasis") == "on"
    reuse_raw = (request.form.get("reuse_hero_asset_id") or "").strip()

    if not title or not date_raw:
        return redirect(url_for("admin_events"))

    try:
        event_date = date.fromisoformat(date_raw)
    except ValueError:
        return redirect(url_for("admin_events"))

    hero_asset_id = None
    image_file = request.files.get("course_image")
    if image_file and image_file.filename:
        raw = image_file.read()
        if raw and len(raw) <= 8 * 1024 * 1024:
            try:
                image_bytes, mime_store = compress_course_hero_image(raw)
            except ValueError:
                return redirect(url_for("admin_events"))
            with psycopg.connect(DB_URL) as conn:
                with conn.cursor() as cur:
                    hero_asset_id = get_or_create_course_hero_asset(cur, image_bytes, mime_store)
                conn.commit()

    if hero_asset_id is None and reuse_raw:
        try:
            candidate = int(reuse_raw)
        except ValueError:
            candidate = None
        if candidate is not None:
            with psycopg.connect(DB_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "select id from course_hero_assets where id = %s;",
                        (candidate,),
                    )
                    if cur.fetchone():
                        hero_asset_id = candidate

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into upcoming_events (
                    title, event_date, emphasis,
                    course_image_data, course_image_mime_type, hero_asset_id
                )
                values (%s, %s, %s, null, null, %s);
                """,
                (title, event_date, emphasis, hero_asset_id),
            )
        conn.commit()

    return redirect(url_for("admin_events"))


@app.post("/admin/events/<int:event_id>/delete")
@admin_required
def admin_events_delete(event_id):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("delete from upcoming_events where id = %s;", (event_id,))
            delete_orphan_course_hero_assets(cur)
        conn.commit()

    return redirect(url_for("admin_events"))


@app.get("/me")
@login_required
def my_page():
    player_id = session.get("player_id")

    if not player_id:
        return "Din bruger er ikke koblet til en spiller endnu", 403

    season_year = date.today().year

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    p.id,
                    p.full_name,
                    p.avatar_data is not null as has_avatar,
                    p.last_known_handicap
                from players p
                where p.id = %s and p.is_active = true;
                """,
                (player_id,),
            )
            player = cur.fetchone()
            if not player:
                return "Spilleren blev ikke fundet", 404

            cur.execute(
                """
                with totals as (
                    select
                        p.id,
                        coalesce(sum(rp.season_points), 0) as total_points
                    from players p
                    left join round_players rp on rp.player_id = p.id
                    left join rounds r
                        on r.id = rp.round_id
                        and r.season_year = %s
                    where p.is_active = true
                    group by p.id
                ),
                ranked as (
                    select
                        id,
                        total_points,
                        rank() over (
                            order by total_points desc, id
                        ) as season_rank
                    from totals
                )
                select season_rank, total_points
                from ranked
                where id = %s;
                """,
                (season_year, player_id),
            )
            standing = cur.fetchone()

            cur.execute(
                """
                select id, title, event_date
                from upcoming_events
                where event_date >= current_date
                order by event_date asc, id asc
                limit 1;
                """
            )
            next_event = cur.fetchone()

            cur.execute(
                """
                select
                    r.id,
                    r.round_date,
                    c.name,
                    rp.stableford_points,
                    rp.position,
                    rp.season_points
                from round_players rp
                join rounds r on r.id = rp.round_id
                join courses c on c.id = r.course_id
                where rp.player_id = %s
                  and rp.status = 'played'
                order by r.round_date desc, r.id desc
                limit 1;
                """,
                (player_id,),
            )
            last_round = cur.fetchone()

    return render_template(
        "me.html",
        player=player,
        season_year=season_year,
        season_rank=standing[0] if standing else None,
        season_points=standing[1] if standing else 0,
        next_event=next_event,
        last_round=last_round,
        handicap_display=format_handicap_dk(player[3]),
        can_edit_player_profile=True,
    )


@app.get("/me/statistik")
@login_required
def my_stats():
    player_id = session.get("player_id")
    if not player_id:
        return "Din bruger er ikke koblet til en spiller endnu", 403
    return redirect(url_for("player_page", player_id=player_id))

app.config["SECRET_KEY"] = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)