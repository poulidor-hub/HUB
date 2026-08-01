-- Migration: passage WireGuard client → serveur
-- Crée la table vpn_peers et les paramètres serveur en app_settings

-- Table des peers VPN (un peer = un device d'un utilisateur HUB)
CREATE TABLE IF NOT EXISTS vpn_peers (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          VARCHAR(100) NOT NULL,
    public_key    VARCHAR(44) NOT NULL UNIQUE,
    private_key   VARCHAR(64) NOT NULL,
    vpn_ip        VARCHAR(18) NOT NULL UNIQUE,
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vpn_peers_user_id ON vpn_peers(user_id);

-- Paramètres du serveur VPN en app_settings (pattern clé-valeur existant)
INSERT INTO app_settings (key, value) VALUES ('vpn_server_private_key', '')
ON CONFLICT (key) DO NOTHING;

INSERT INTO app_settings (key, value) VALUES ('vpn_server_public_key', '')
ON CONFLICT (key) DO NOTHING;

INSERT INTO app_settings (key, value) VALUES ('vpn_endpoint', '')
ON CONFLICT (key) DO NOTHING;

INSERT INTO app_settings (key, value) VALUES ('vpn_listen_port', '51820')
ON CONFLICT (key) DO NOTHING;

INSERT INTO app_settings (key, value) VALUES ('vpn_subnet', '10.8.0.0/24')
ON CONFLICT (key) DO NOTHING;

-- vpn_config_hash servait au mode client (détection changements de conf) — obsolète
DELETE FROM app_settings WHERE key = 'vpn_config_hash';
