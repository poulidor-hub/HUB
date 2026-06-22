-- Migration 028: Email optionnel sur les demandes de compte
-- Description: Permet de collecter (optionnellement) l'email du demandeur sur
--              la modale de demande, ou de le renseigner par l'admin lors de
--              l'approbation, afin de pré-remplir l'email d'information envoyé
--              à l'utilisateur (mailto).

ALTER TABLE account_requests ADD COLUMN IF NOT EXISTS email VARCHAR(100);

-- Configuration du formulaire de demande de compte (message + champs visibles)
INSERT INTO app_settings (key, value)
VALUES ('account_request_form_config', '{"important_message": "Utilisez le même identifiant que votre session Windows/réseau, sinon votre demande ne sera pas acceptée.", "show_first_name": true, "show_last_name": true, "show_email": false}')
ON CONFLICT (key) DO NOTHING;
