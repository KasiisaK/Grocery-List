import os
import time
import uuid
from functools import wraps

import jwt
import psycopg
from jwt import PyJWKClient
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from psycopg.rows import dict_row
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from flasgger import Swagger  # http://localhost:8000/api-docs

app = Flask(__name__)

CORS(app, resources={
    r"/api/v1/*": {
        "origins": "*",  # Change to only allowed domain in production, now all
        "methods": ["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
    }
})

# Swagger api documentation including an "Authorize" button for the bearer token
app.config["SWAGGER"] = {"title": "Grocery List API", "uiversion": 3}
swagger = Swagger(
    app,
    config={
        "headers": [],
        "specs": [{
            "endpoint": "apispec",
            "route": "/apispec.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }],
        "static_url_path": "/flasgger_static",
        "swagger_ui": True,
        "specs_route": "/api-docs",
    },
    template={
        "securityDefinitions": {
            "Bearer": {
                "type": "apiKey",
                "name": "Authorization",
                "in": "header",
                "description": "Type: Bearer <access_token>",
            }
        }
    },
)


# ------------------------------------------
# Database setup
# ------------------------------------------
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://grocery:changeme@localhost:5432/grocery"
)
SCHEMA_FILE = os.path.join(os.path.dirname(__file__), "./../schema.sql")


def get_conn():
    """Open a connection. Rows come back as dicts. Using it in a `with`
    block commits on success, rolls back on error, and closes it."""
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db(retries=20, delay=1.5):
    """Wait for Postgres to accept connections, then apply the schema."""
    with open(SCHEMA_FILE) as f:
        schema = f.read()
    for attempt in range(1, retries + 1):
        try:
            with get_conn() as conn:
                conn.execute(schema)
            print("Database ready")
            return
        except psycopg.OperationalError as err:
            print(f"Postgres not ready (attempt {attempt}/{retries}): {err}")
            time.sleep(delay)
    raise RuntimeError("Could not connect to PostgreSQL")


init_db()


# ------------------------------------------
# Keycloak authentication
# ------------------------------------------
KEYCLOAK_ISSUER = os.environ.get(
    "KEYCLOAK_ISSUER", "http://localhost:8081/realms/grocery"
)
# JWKS_URL, download Keycloak's public keys to check the signature
KEYCLOAK_JWKS_URL = os.environ.get(
    "KEYCLOAK_JWKS_URL", f"{KEYCLOAK_ISSUER}/protocol/openid-connect/certs"
)
KEYCLOAK_CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "grocery-frontend")

jwks_client = PyJWKClient(KEYCLOAK_JWKS_URL)  # downloads and caches keys lazily


def require_auth(view):
    """Only let the request through if it carries a valid Keycloak token.
    On success the token's claims are available as g.claims."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({"error": "Missing bearer token"}), 401
        token = header[len("Bearer "):]
        try:
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],            # Keycloak's default signing algorithm
                issuer=KEYCLOAK_ISSUER,          # checks "iss"
                options={"require": ["exp", "sub"], "verify_aud": False},
            )
        except jwt.PyJWTError:
            return jsonify({"error": "Invalid or expired token"}), 401
        if claims.get("azp") != KEYCLOAK_CLIENT_ID:   # which client the token was issued to
            return jsonify({"error": "Token was issued for another client"}), 401
        g.claims = claims
        return view(*args, **kwargs)
    return wrapper


# ------------------------------------------
# Helpers: current user, permissions, validation
# ------------------------------------------
# Roles. The owner is grocery_lists.owner_id; invited people are in list_members.
OWNER, EDITOR, VIEWER = "owner", "editor", "viewer"
CAN_READ = {OWNER, EDITOR, VIEWER}
CAN_EDIT = {OWNER, EDITOR}
OWNER_ONLY = {OWNER}

# Item columns sent to the client. quantity is cast so JSON gets a number, not "2.50".
ITEM_COLUMNS = (
    "id, list_id, name, quantity::float8 AS quantity, unit, "
    "is_checked, added_by, created_at"
)


def current_user_id():
    """The caller's Keycloak id as a UUID (always taken from the verified token)."""
    return uuid.UUID(g.claims["sub"])


def ensure_user(conn):
    """Create or refresh the caller's profile row, and return it."""
    return conn.execute(
        """
        INSERT INTO users (id, username)
        VALUES (%s::uuid, %s)
        ON CONFLICT (id) DO UPDATE SET username = EXCLUDED.username
        RETURNING id, username, created_at
        """,
        (g.claims["sub"], g.claims.get("preferred_username", "unknown")),
    ).fetchone()


def get_role(conn, list_id):
    """The caller's role on a list: 'owner', 'editor', 'viewer', or None
    (None means the list does not exist OR the caller has no access to it)."""
    row = conn.execute(
        """
        SELECT CASE WHEN l.owner_id = %(me)s THEN 'owner' ELSE m.role END AS role
        FROM grocery_lists l
        LEFT JOIN list_members m ON m.list_id = l.id AND m.user_id = %(me)s
        WHERE l.id = %(list_id)s
        """,
        {"me": current_user_id(), "list_id": list_id},
    ).fetchone()
    return row["role"] if row else None


def check_access(conn, list_id, allowed):
    """Returns (role, None) if the caller may proceed, else (role, error_response).
    Strangers get 404, so the API does not reveal that a list exists."""
    role = get_role(conn, list_id)
    if role is None:
        return None, (jsonify({"error": "List not found"}), 404)
    if role not in allowed:
        return role, (jsonify({"error": "You do not have permission to do this"}), 403)
    return role, None


def json_body():
    """The request body as a dict, or None if it is not a JSON object."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


# Each validator returns (clean_value, error_message).
def valid_text(value, label, max_len):
    if not isinstance(value, str) or not value.strip():
        return None, f"{label} must be a non-empty string"
    value = value.strip()
    if len(value) > max_len:
        return None, f"{label} must be at most {max_len} characters"
    return value, None


def valid_link(value):
    if value is None:                       # null clears the link
        return None, None
    value, error = valid_text(value, "recipe_link", 2000)
    if error:
        return None, error
    # Only web links: a "javascript:" link would be dangerous when shown in a page
    if not value.lower().startswith(("http://", "https://")):
        return None, "recipe_link must start with http:// or https://"
    return value, None


def valid_quantity(value):
    if value is None:
        return None, None
    ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
          and 0 < value <= 99999999.99)
    if not ok:
        return None, "quantity must be a positive number"
    return value, None


def valid_unit(value):
    if value is None:
        return None, None
    return valid_text(value, "unit", 10)


def valid_bool(value):
    if not isinstance(value, bool):
        return None, "is_checked must be true or false"
    return value, None


# Which fields a client may set on each resource, and how each is validated.
# These dictionaries are the ONLY source of column names in the dynamic SQL below.
LIST_FIELDS = {
    "title": lambda v: valid_text(v, "title", 100),
    "recipe_link": valid_link,
}
ITEM_FIELDS = {
    "name": lambda v: valid_text(v, "name", 100),
    "quantity": valid_quantity,
    "unit": valid_unit,
    "is_checked": valid_bool,
}


def parse_fields(data, validators, required=()):
    """Validate the fields present in `data`. Returns (clean_dict, error_message)."""
    clean = {}
    for column, validate in validators.items():
        if column in data:
            value, error = validate(data[column])
            if error:
                return None, error
            clean[column] = value
        elif column in required:
            return None, f"{column} is required"
    return clean, None


def bad_request(message):
    return jsonify({"error": message}), 400


def fetch_members(conn, list_id):
    """Everyone on a list: the owner first, then invited people."""
    return conn.execute(
        """
        SELECT * FROM (
            SELECT l.owner_id AS user_id, u.username, 'owner' AS role,
                   l.created_at AS joined_at
            FROM grocery_lists l JOIN users u ON u.id = l.owner_id
            WHERE l.id = %(list_id)s
            UNION ALL
            SELECT m.user_id, u.username, m.role, m.joined_at
            FROM list_members m JOIN users u ON u.id = m.user_id
            WHERE m.list_id = %(list_id)s
        ) AS people
        ORDER BY (role = 'owner') DESC, username
        """,
        {"list_id": list_id},
    ).fetchall()


# JSON errors for unknown paths / wrong methods (Flask's default is an HTML page)
@app.errorhandler(404)
def not_found(_error):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(405)
def method_not_allowed(_error):
    return jsonify({"error": "Method not allowed"}), 405


# ------------------------------------------
# Health
# ------------------------------------------
@app.route("/api/v1/health", methods=["GET"])
def health():
    """Health check (no login needed)
    ---
    tags:
      - system
    responses:
      200:
        description: The API is running
    """
    return jsonify({"status": "ok"})


# ------------------------------------------
# User Paths
# ------------------------------------------
def escape_like(text):
    """Make %, _ and \\ in user input match literally inside a LIKE pattern.
    Otherwise typing '%' would match every user."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


MIN_SEARCH_LENGTH = 2
MAX_SEARCH_LENGTH = 50


@app.route("/api/v1/users", methods=["GET"])
@require_auth
def search_users():
    """Search users by the start of their username
    Returns at most 20 matches, e.g. GET /api/v1/users?search=bo
    ---
    tags:
      - users
    security:
      - Bearer: []
    parameters:
      - in: query
        name: search
        type: string
        required: true
        description: The first characters of the username (at least 2)
        example: bo
    responses:
      200:
        description: Up to 20 users (id and username), without the caller
      400:
        description: Search text too long
      401:
        description: Missing, invalid or expired token
    """
    term = request.args.get("search", "").strip()
    if len(term) > MAX_SEARCH_LENGTH:
        return jsonify({"error": "Search text is too long"}), 400
    if len(term) < MIN_SEARCH_LENGTH:
        return jsonify([])      # too short to search: empty list, not an error

    pattern = escape_like(term) + "%"     # "bo" -> "bo%" = starts with "bo"
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, username
            FROM users
            WHERE username ILIKE %s
              AND id <> %s::uuid
            ORDER BY username
            LIMIT 20
            """,
            (pattern, g.claims["sub"]),
        ).fetchall()
    return jsonify(rows)


@app.route("/api/v1/me", methods=["GET"])
@require_auth
def get_me():
    """Current user (created in database on first visit)
    ---
    tags:
      - users
    security:
      - Bearer: []
    responses:
      200:
        description: The signed-in user's profile
      401:
        description: Missing, invalid or expired token
    """
    with get_conn() as conn:
        row = ensure_user(conn)
    return jsonify(row)


@app.route("/api/v1/me", methods=["DELETE"])
@require_auth
def delete_me():
    """Delete my data (GDPR erasure of everything stored in this database)
    ---
    tags:
      - users
    security:
      - Bearer: []
    responses:
      204:
        description: Profile, owned lists and memberships deleted
      401:
        description: Missing, invalid or expired token
    """
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = %s::uuid", (g.claims["sub"],))
    return "", 204


# ------------------------------------------
# Grocery list Paths
# ------------------------------------------
@app.route("/api/v1/lists", methods=["GET"])
@require_auth
def get_lists():
    """Lists I own or have been invited to
    ---
    tags:
      - lists
    security:
      - Bearer: []
    responses:
      200:
        description: My lists with my role, the owner name and item counts
      401:
        description: Missing, invalid or expired token
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT l.id, l.title, l.recipe_link, l.created_at,
                   CASE WHEN l.owner_id = %(me)s THEN 'owner' ELSE m.role END AS role,
                   u.username AS owner_username,
                   (SELECT count(*) FROM list_items i
                     WHERE i.list_id = l.id) AS item_count,
                   (SELECT count(*) FROM list_items i
                     WHERE i.list_id = l.id AND i.is_checked) AS checked_count
            FROM grocery_lists l
            JOIN users u ON u.id = l.owner_id
            LEFT JOIN list_members m ON m.list_id = l.id AND m.user_id = %(me)s
            WHERE l.owner_id = %(me)s OR m.user_id IS NOT NULL
            ORDER BY l.created_at DESC
            """,
            {"me": current_user_id()},
        ).fetchall()
    return jsonify(rows)


@app.route("/api/v1/lists", methods=["POST"])
@require_auth
def create_list():
    """Create a grocery list (the caller becomes its owner)
    ---
    tags:
      - lists
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - title
          properties:
            title:
              type: string
              example: Pancakes
            recipe_link:
              type: string
              example: https://example.com/pancakes
    responses:
      201:
        description: The created list
      400:
        description: Missing or invalid field
      401:
        description: Missing, invalid or expired token
    """
    data = json_body()
    if data is None:
        return bad_request("Body must be a JSON object")
    clean, error = parse_fields(data, LIST_FIELDS, required=("title",))
    if error:
        return bad_request(error)

    with get_conn() as conn:
        ensure_user(conn)    # the owner must exist in users (foreign key)
        row = conn.execute(
            """
            INSERT INTO grocery_lists (owner_id, title, recipe_link)
            VALUES (%s, %s, %s)
            RETURNING id, title, recipe_link, created_at
            """,
            (current_user_id(), clean["title"], clean.get("recipe_link")),
        ).fetchone()
    row["role"] = OWNER
    return jsonify(row), 201


@app.route("/api/v1/lists/<int:list_id>", methods=["GET"])
@require_auth
def get_list(list_id):
    """One list with its people and items
    ---
    tags:
      - lists
    security:
      - Bearer: []
    parameters:
      - in: path
        name: list_id
        type: integer
        required: true
    responses:
      200:
        description: The list, the caller's role, all members and all items
      401:
        description: Missing, invalid or expired token
      404:
        description: List not found, or the caller has no access to it
    """
    with get_conn() as conn:
        role, denied = check_access(conn, list_id, CAN_READ)
        if denied:
            return denied
        row = conn.execute(
            """
            SELECT l.id, l.title, l.recipe_link, l.created_at,
                   l.owner_id, u.username AS owner_username
            FROM grocery_lists l JOIN users u ON u.id = l.owner_id
            WHERE l.id = %s
            """,
            (list_id,),
        ).fetchone()
        row["role"] = role
        row["members"] = fetch_members(conn, list_id)
        row["items"] = conn.execute(
            f"SELECT {ITEM_COLUMNS} FROM list_items WHERE list_id = %s "
            "ORDER BY is_checked, id",
            (list_id,),
        ).fetchall()
    return jsonify(row)


@app.route("/api/v1/lists/<int:list_id>", methods=["PATCH"])
@require_auth
def update_list(list_id):
    """Change a list's title or recipe link (owner only)
    ---
    tags:
      - lists
    security:
      - Bearer: []
    parameters:
      - in: path
        name: list_id
        type: integer
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            title:
              type: string
            recipe_link:
              type: string
    responses:
      200:
        description: The updated list
      400:
        description: Nothing to update, or an invalid field
      403:
        description: Only the owner can do this
      404:
        description: List not found, or the caller has no access to it
    """
    data = json_body()
    if data is None:
        return bad_request("Body must be a JSON object")
    clean, error = parse_fields(data, LIST_FIELDS)
    if error:
        return bad_request(error)
    if not clean:
        return bad_request("Nothing to update")

    # Column names come from LIST_FIELDS (our code), values are placeholders.
    assignments = ", ".join(f"{column} = %s" for column in clean)
    with get_conn() as conn:
        _, denied = check_access(conn, list_id, OWNER_ONLY)
        if denied:
            return denied
        row = conn.execute(
            f"UPDATE grocery_lists SET {assignments} WHERE id = %s "
            "RETURNING id, title, recipe_link, created_at",
            [*clean.values(), list_id],
        ).fetchone()
    return jsonify(row)


@app.route("/api/v1/lists/<int:list_id>", methods=["DELETE"])
@require_auth
def delete_list(list_id):
    """Delete a list with all its items and memberships (owner only)
    ---
    tags:
      - lists
    security:
      - Bearer: []
    parameters:
      - in: path
        name: list_id
        type: integer
        required: true
    responses:
      204:
        description: Deleted
      403:
        description: Only the owner can do this
      404:
        description: List not found, or the caller has no access to it
    """
    with get_conn() as conn:
        _, denied = check_access(conn, list_id, OWNER_ONLY)
        if denied:
            return denied
        conn.execute("DELETE FROM grocery_lists WHERE id = %s", (list_id,))
    return "", 204


# ------------------------------------------
# Members (who can use a list)
# ------------------------------------------
@app.route("/api/v1/lists/<int:list_id>/members", methods=["GET"])
@require_auth
def get_members(list_id):
    """Everyone on a list, the owner first
    ---
    tags:
      - members
    security:
      - Bearer: []
    parameters:
      - in: path
        name: list_id
        type: integer
        required: true
    responses:
      200:
        description: Owner and invited people with their roles
      404:
        description: List not found, or the caller has no access to it
    """
    with get_conn() as conn:
        _, denied = check_access(conn, list_id, CAN_READ)
        if denied:
            return denied
        rows = fetch_members(conn, list_id)
    return jsonify(rows)


@app.route("/api/v1/lists/<int:list_id>/members", methods=["POST"])
@require_auth
def add_member(list_id):
    """Invite a user to a list (owner only). Find user ids with GET /api/v1/users?search=
    ---
    tags:
      - members
    security:
      - Bearer: []
    parameters:
      - in: path
        name: list_id
        type: integer
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - user_id
          properties:
            user_id:
              type: string
              example: 3f2b8c1e-9a4d-4c6e-8b1a-2d7f5e9c0a11
            role:
              type: string
              enum:
                - editor
                - viewer
              example: editor
    responses:
      201:
        description: The new member
      400:
        description: Invalid user_id or role
      403:
        description: Only the owner can do this
      404:
        description: List or user not found
      409:
        description: Already a member
    """
    data = json_body()
    if data is None:
        return bad_request("Body must be a JSON object")
    try:
        target = uuid.UUID(str(data.get("user_id")))
    except ValueError:
        return bad_request("user_id must be a valid UUID")
    role_value = data.get("role", EDITOR)
    if role_value not in (EDITOR, VIEWER):
        return bad_request("role must be 'editor' or 'viewer'")
    if target == current_user_id():
        return bad_request("You already own this list")

    try:
        with get_conn() as conn:
            _, denied = check_access(conn, list_id, OWNER_ONLY)
            if denied:
                return denied
            conn.execute(
                "INSERT INTO list_members (list_id, user_id, role) VALUES (%s, %s, %s)",
                (list_id, target, role_value),
            )
            row = conn.execute(
                """
                SELECT m.user_id, u.username, m.role, m.joined_at
                FROM list_members m JOIN users u ON u.id = m.user_id
                WHERE m.list_id = %s AND m.user_id = %s
                """,
                (list_id, target),
            ).fetchone()
    except UniqueViolation:
        return jsonify({"error": "That user is already a member"}), 409
    except ForeignKeyViolation:
        return jsonify({"error": "User not found (they must have opened the app once)"}), 404
    return jsonify(row), 201


@app.route("/api/v1/lists/<int:list_id>/members/<uuid:user_id>", methods=["PATCH"])
@require_auth
def update_member(list_id, user_id):
    """Change a member's role (owner only)
    ---
    tags:
      - members
    security:
      - Bearer: []
    parameters:
      - in: path
        name: list_id
        type: integer
        required: true
      - in: path
        name: user_id
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - role
          properties:
            role:
              type: string
              enum:
                - editor
                - viewer
    responses:
      200:
        description: The updated member
      400:
        description: Invalid role
      403:
        description: Only the owner can do this
      404:
        description: List not found, or that user is not a member
    """
    data = json_body()
    if data is None:
        return bad_request("Body must be a JSON object")
    role_value = data.get("role")
    if role_value not in (EDITOR, VIEWER):
        return bad_request("role must be 'editor' or 'viewer'")

    with get_conn() as conn:
        _, denied = check_access(conn, list_id, OWNER_ONLY)
        if denied:
            return denied
        cur = conn.execute(
            "UPDATE list_members SET role = %s WHERE list_id = %s AND user_id = %s",
            (role_value, list_id, user_id),
        )
        if cur.rowcount == 0:
            return jsonify({"error": "That user is not a member"}), 404
    return jsonify({"user_id": user_id, "role": role_value})


@app.route("/api/v1/lists/<int:list_id>/members/<uuid:user_id>", methods=["DELETE"])
@require_auth
def remove_member(list_id, user_id):
    """Remove a member (owner), or leave a list yourself (any member)
    ---
    tags:
      - members
    security:
      - Bearer: []
    parameters:
      - in: path
        name: list_id
        type: integer
        required: true
      - in: path
        name: user_id
        type: string
        required: true
    responses:
      204:
        description: Removed
      403:
        description: Only the owner can remove other people
      404:
        description: List not found, or that user is not a member
    """
    with get_conn() as conn:
        role, denied = check_access(conn, list_id, CAN_READ)
        if denied:
            return denied
        if role != OWNER and user_id != current_user_id():
            return jsonify({"error": "Only the owner can remove other people"}), 403
        cur = conn.execute(
            "DELETE FROM list_members WHERE list_id = %s AND user_id = %s",
            (list_id, user_id),
        )
        if cur.rowcount == 0:
            return jsonify({"error": "That user is not a member"}), 404
    return "", 204


# ------------------------------------------
# Items on a list
# ------------------------------------------
@app.route("/api/v1/lists/<int:list_id>/items", methods=["GET"])
@require_auth
def get_items(list_id):
    """All items on a list (unchecked first)
    ---
    tags:
      - items
    security:
      - Bearer: []
    parameters:
      - in: path
        name: list_id
        type: integer
        required: true
    responses:
      200:
        description: The items
      404:
        description: List not found, or the caller has no access to it
    """
    with get_conn() as conn:
        _, denied = check_access(conn, list_id, CAN_READ)
        if denied:
            return denied
        rows = conn.execute(
            f"SELECT {ITEM_COLUMNS} FROM list_items WHERE list_id = %s "
            "ORDER BY is_checked, id",
            (list_id,),
        ).fetchall()
    return jsonify(rows)


@app.route("/api/v1/lists/<int:list_id>/items", methods=["POST"])
@require_auth
def add_item(list_id):
    """Add an item (owner or editor)
    ---
    tags:
      - items
    security:
      - Bearer: []
    parameters:
      - in: path
        name: list_id
        type: integer
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - name
          properties:
            name:
              type: string
              example: Sugar
            quantity:
              type: number
              example: 5
            unit:
              type: string
              example: dl
    responses:
      201:
        description: The created item
      400:
        description: Missing or invalid field
      403:
        description: Viewers cannot add items
      404:
        description: List not found, or the caller has no access to it
    """
    data = json_body()
    if data is None:
        return bad_request("Body must be a JSON object")
    clean, error = parse_fields(data, ITEM_FIELDS, required=("name",))
    if error:
        return bad_request(error)

    with get_conn() as conn:
        _, denied = check_access(conn, list_id, CAN_EDIT)
        if denied:
            return denied
        row = conn.execute(
            f"""
            INSERT INTO list_items (list_id, added_by, name, quantity, unit, is_checked)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING {ITEM_COLUMNS}
            """,
            (list_id, current_user_id(), clean["name"], clean.get("quantity"),
             clean.get("unit"), clean.get("is_checked", False)),
        ).fetchone()
    return jsonify(row), 201


@app.route("/api/v1/lists/<int:list_id>/items/<int:item_id>", methods=["PATCH"])
@require_auth
def update_item(list_id, item_id):
    """Change an item, or tick it off with is_checked (owner or editor)
    ---
    tags:
      - items
    security:
      - Bearer: []
    parameters:
      - in: path
        name: list_id
        type: integer
        required: true
      - in: path
        name: item_id
        type: integer
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            name:
              type: string
            quantity:
              type: number
            unit:
              type: string
            is_checked:
              type: boolean
    responses:
      200:
        description: The updated item
      400:
        description: Nothing to update, or an invalid field
      403:
        description: Viewers cannot change items
      404:
        description: List or item not found
    """
    data = json_body()
    if data is None:
        return bad_request("Body must be a JSON object")
    clean, error = parse_fields(data, ITEM_FIELDS)
    if error:
        return bad_request(error)
    if not clean:
        return bad_request("Nothing to update")

    # Column names come from ITEM_FIELDS (our code), values are placeholders.
    assignments = ", ".join(f"{column} = %s" for column in clean)
    with get_conn() as conn:
        _, denied = check_access(conn, list_id, CAN_EDIT)
        if denied:
            return denied
        # "AND list_id = %s" stops someone editing an item of a DIFFERENT list
        row = conn.execute(
            f"UPDATE list_items SET {assignments} WHERE id = %s AND list_id = %s "
            f"RETURNING {ITEM_COLUMNS}",
            [*clean.values(), item_id, list_id],
        ).fetchone()
    if row is None:
        return jsonify({"error": "Item not found"}), 404
    return jsonify(row)


@app.route("/api/v1/lists/<int:list_id>/items/<int:item_id>", methods=["DELETE"])
@require_auth
def delete_item(list_id, item_id):
    """Delete an item (owner or editor)
    ---
    tags:
      - items
    security:
      - Bearer: []
    parameters:
      - in: path
        name: list_id
        type: integer
        required: true
      - in: path
        name: item_id
        type: integer
        required: true
    responses:
      204:
        description: Deleted
      403:
        description: Viewers cannot delete items
      404:
        description: List or item not found
    """
    with get_conn() as conn:
        _, denied = check_access(conn, list_id, CAN_EDIT)
        if denied:
            return denied
        cur = conn.execute(
            "DELETE FROM list_items WHERE id = %s AND list_id = %s",
            (item_id, list_id),
        )
        if cur.rowcount == 0:
            return jsonify({"error": "Item not found"}), 404
    return "", 204


if __name__ == "__main__":
    print("Starting Flask server...")
    print("API will be available at: http://localhost:8000")
    app.run(host="0.0.0.0", port=8000, debug=True)