import os
import time
from functools import wraps

import jwt
import psycopg
from jwt import PyJWKClient
from psycopg.rows import dict_row
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from flasgger import Swagger  # http://localhost:8000/api-docs

app = Flask(__name__)

CORS(app, resources={
    r"/api/v1/*": {
        "origins": "*",  # Change to only allowed domain in production, now all
        "methods": ["GET", "POST", "DELETE", "OPTIONS"],
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
# JWKS_URL, download Keycloak's public keys to check the signature
KEYCLOAK_ISSUER = os.environ.get(
    "KEYCLOAK_ISSUER", "http://localhost:8081/realms/grocery"
)
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
        row = conn.execute(
            """
            INSERT INTO users (id, username)
            VALUES (%s::uuid, %s)
            ON CONFLICT (id) DO UPDATE SET username = EXCLUDED.username
            RETURNING id, username, created_at
            """,
            (g.claims["sub"], g.claims.get("preferred_username", "unknown")),
        ).fetchone()
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


if __name__ == "__main__":
    print("Starting Flask server...")
    print("API will be available at: http://localhost:8000")
    app.run(host="0.0.0.0", port=8000, debug=True)