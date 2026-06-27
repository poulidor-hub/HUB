INSERT INTO app_settings (key, value) VALUES ('vpn_enabled', 'false')
ON CONFLICT (key) DO NOTHING;

INSERT INTO app_settings (key, value) VALUES ('vpn_config_hash', '')
ON CONFLICT (key) DO NOTHING;
