"""
Garde-fou migrations — reproduit le découpage du runner `apply_migrations()`
(app/database.py) sur TOUS les fichiers hub/migrations/*.sql.

Le runner exécute `sql.split(';')` **avant** de retirer les commentaires
(`_strip_sql_comments`) : un `;` à l'intérieur d'un commentaire SQL produit
donc un fragment orphelin envoyé au SGBD tel quel → `MigrationError` fail-fast
au boot (incident 3.97.1, `syntax error at or near "le"`, hub-api crash-loop).

Ce test attrape la classe de bug AVANT le push :
1. `test_incident_3_97_1_est_detecte` — épingle le contenu fautif historique :
   le détecteur DOIT signaler le fragment orphelin (sinon il a été affaibli) ;
2. `test_toutes_les_migrations_produisent_des_fragments_sql` — balaye le
   répertoire réel : chaque fragment post-strip doit commencer par un mot-clé
   SQL (allowlist calibrée empiriquement sur les 69 fichiers du 18/11/2026 :
   CREATE, ALTER, COMMENT, INSERT, UPDATE, DELETE, DROP — superset tolérant
   pour les fichiers futurs).
"""

from pathlib import Path

from app.database import _strip_sql_comments

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Mots-clés SQL légitimes en tête de statement (calibré sur le parc + marge
# pour les fichiers futurs ; tout autre token = suspect, à justifier).
SQL_KEYWORDS = frozenset(
    {
        "CREATE",
        "ALTER",
        "DROP",
        "INSERT",
        "UPDATE",
        "DELETE",
        "SELECT",
        "WITH",
        "COMMENT",
        "GRANT",
        "REVOKE",
        "TRUNCATE",
        "VACUUM",
        "ANALYZE",
        "REINDEX",
        "LOCK",
        "REFRESH",
        "SET",
        "RESET",
        "BEGIN",
        "COMMIT",
        "PRAGMA",  # SQLite
    }
)


def runner_statements(sql: str) -> list:
    """Réplique EXACTE du découpage du runner (app.database.apply_migrations).

    Si le runner change, mettre à jour ICI et vérifier l'écart avec
    `hub/app/database.py` (les deux doivent rester synchronisés).
    """
    return [s for s in (_strip_sql_comments(f) for f in sql.split(";")) if s]


def first_token(fragment: str) -> str:
    stripped = fragment.strip()
    return stripped.split(None, 1)[0].upper() if stripped else ""


# Contenu fautif historique (3.97.1) : un ';' DANS le commentaire coupait le
# fragment « (même btree) ; le index=True correspondant… » → orphelin exécuté.
INCIDENT_3_97_1_SQL = """\
-- Index de performance (extrait fautif de la 3.97.1)
CREATE INDEX IF NOT EXISTS ix_service_account_app_access_service_account_id
    ON service_account_app_access (service_account_id);

-- ix_applications_id dupliquait exactement la clé primaire applications_pkey
-- (héritage d'un Column(index=True) sur une PK). Le planner bascule sur la PK
-- (même btree) ; le index=True correspondant est retiré du modèle en 3.97.1
DROP INDEX IF EXISTS ix_applications_id;
"""


def test_incident_3_97_1_est_detecte():
    """Le détecteur signale le fragment orphelin du contenu fautif 3.97.1.

    Si ce test échoue en « aucun offender », le détecteur a été affaibli
    (allowlist trop large ou découpage divergent du runner) — ne pas passer
    l'allowlist en force : relire app/database.py.
    """
    frags = runner_statements(INCIDENT_3_97_1_SQL)
    offenders = [f for f in frags if first_token(f) not in SQL_KEYWORDS]
    assert offenders, (
        "le contenu fautif 3.97.1 n'est plus détecté — détecteur affaibli "
        f"(fragments produits : {[first_token(f) for f in frags]})"
    )
    assert first_token(offenders[0]) == "LE", (
        f"fragment inattendu : {offenders[0][:80]!r} — le bug historique "
        "commençait par le mot orphelin « le » (syntax error at \"le\")"
    )


def test_incident_3_97_1_le_fix_passe():
    """La version corrigée (sans ';' en commentaire) produit 3 statements SQL."""
    fixed_sql = INCIDENT_3_97_1_SQL.replace(
        "-- (même btree) ; le index=True correspondant est retiré du modèle en 3.97.1",
        "-- (même btree). Le index=True correspondant est retiré du modèle en 3.97.1",
    )
    frags = runner_statements(fixed_sql)
    assert len(frags) == 2, f"2 statements attendus (1 CREATE + 1 DROP), obtenu {len(frags)}"
    assert all(first_token(f) in SQL_KEYWORDS for f in frags)


def test_toutes_les_migrations_produisent_des_fragments_sql():
    """Balayage réel : aucun fragment non-SQL dans hub/migrations/*.sql."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert files, f"aucune migration trouvée dans {MIGRATIONS_DIR} — chemin faux ?"

    problems = []
    for mf in files:
        for frag in runner_statements(mf.read_text()):
            tok = first_token(frag)
            if tok not in SQL_KEYWORDS:
                problems.append(
                    f"  {mf.name}: fragment non-SQL (token {tok!r}) : "
                    f"{frag[:70]!r}"
                )
    assert not problems, (
        f"{len(problems)} fragment(s) non-SQL — un ';' traîne dans un "
        f"commentaire (cf. incident 3.97.1) :\n" + "\n".join(problems)
    )
