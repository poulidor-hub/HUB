-- 047_user_tags.sql
-- User tags: admin-defined categories, user-selectable values

CREATE TABLE tag_categories (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(80) UNIQUE NOT NULL,
    label VARCHAR(150) NOT NULL,
    is_multiple BOOLEAN NOT NULL DEFAULT FALSE,
    source VARCHAR(20) NOT NULL,
    source_app_id INTEGER REFERENCES applications(id) ON DELETE SET NULL,
    values JSONB NOT NULL DEFAULT '[]',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE user_tags (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES tag_categories(id) ON DELETE CASCADE,
    value VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, category_id, value)
);

CREATE INDEX idx_user_tags_user ON user_tags(user_id);
CREATE INDEX idx_user_tags_category ON user_tags(category_id);
