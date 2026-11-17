-- Grocery list schema (PostgreSQL), for use with Keycloak
CREATE TABLE IF NOT EXISTS users (
    id         UUID PRIMARY KEY,               -- = Keycloak "sub" claim
    username   VARCHAR(100) NOT NULL,          -- copied from the token for display
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS grocery_lists (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id    UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       VARCHAR(100) NOT NULL,
    recipe_link TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS grocery_lists_owner_idx ON grocery_lists (owner_id);

CREATE TABLE IF NOT EXISTS list_members (
    list_id   INTEGER     NOT NULL REFERENCES grocery_lists(id) ON DELETE CASCADE,
    user_id   UUID        NOT NULL REFERENCES users(id)         ON DELETE CASCADE,
    role      VARCHAR(10) NOT NULL DEFAULT 'editor' CHECK (role IN ('editor', 'viewer')),
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (list_id, user_id)
);
CREATE INDEX IF NOT EXISTS list_members_user_idx ON list_members (user_id);

CREATE TABLE IF NOT EXISTS list_items (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    list_id    INTEGER       NOT NULL REFERENCES grocery_lists(id) ON DELETE CASCADE,
    added_by   UUID          REFERENCES users(id) ON DELETE SET NULL,
    name       VARCHAR(100)  NOT NULL CHECK (length(btrim(name)) > 0),
    quantity   NUMERIC(10,2) CHECK (quantity > 0),
    unit       VARCHAR(10),
    is_checked BOOLEAN       NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS list_items_list_idx ON list_items (list_id);