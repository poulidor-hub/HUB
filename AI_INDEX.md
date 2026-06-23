# Index du Projet pour Assistant IA

> **Note pour l'IA** : Ceci est la carte du projet. Les numéros de ligne sont indiqués entre crochets. Demande un extrait de code spécifique (ex: Lignes 20 à 50) si tu as besoin de plus de contexte.

### 📄 `test_remote_app_m2m.py`
  - [Ligne 42] Fonction : def print_section()
  - [Ligne 49] Fonction : def print_result()

### 📄 `index_Test.py`
  - [Ligne 9] Fonction : def get_python_signatures()
  - [Ligne 25] Fonction : def get_react_components()
  - [Ligne 43] Fonction : def generate_index()

### 📄 `sync_version.py`
  - [Ligne 46] Fonction : def get_french_date()
  - [Ligne 52] Fonction : def read_version()
  - [Ligne 60] Fonction : def write_version()
  - [Ligne 66] Fonction : def parse_version()
  - [Ligne 75] Fonction : def bump_version()
  - [Ligne 96] Fonction : def get_version_from_integration()
  - [Ligne 105] Fonction : def get_version_from_changelog()
  - [Ligne 114] Fonction : def sync_integration()
  - [Ligne 146] Fonction : def sync_changelog()
  - [Ligne 205] Fonction : def check_versions()
  - [Ligne 233] Fonction : def sync_all()
  - [Ligne 252] Fonction : def main()

### 📄 `hub/app_tester.py`
  - [Ligne 41] Classe : class TestStatus
  - [Ligne 51] Classe : class TestResult
  - [Ligne 70] Classe : class TestSuite
  - [Ligne 98] Classe : class AppTester

### 📄 `hub/tests/conftest.py`
  - [Ligne 25] Fonction : def reset_rate_limiter()
  - [Ligne 39] Fonction : def event_loop()
  - [Ligne 190] Fonction : def user_token()
  - [Ligne 200] Fonction : def admin_token()
  - [Ligne 241] Fonction : def get_auth_header()

### 📄 `hub/tests/test_models.py`
  - [Ligne 14] Classe : class TestUserModel
  - [Ligne 91] Classe : class TestApplicationModel
  - [Ligne 134] Classe : class TestServiceAccountModel
  - [Ligne 170] Classe : class TestRefreshTokenModel
  - [Ligne 218] Classe : class TestUserAppRoleModel
  - [Ligne 252] Classe : class TestDefaultRoles

### 📄 `hub/tests/test_api_apps.py`
  - [Ligne 12] Classe : class TestAppsEndpoints
  - [Ligne 232] Classe : class TestAppUserAssignment
  - [Ligne 307] Classe : class TestIsSharedService

### 📄 `hub/tests/test_api_auth.py`
  - [Ligne 13] Classe : class TestAuthEndpoints
  - [Ligne 228] Classe : class TestClientCredentials
  - [Ligne 276] Classe : class TestSSOFlow

### 📄 `hub/tests/test_discovery.py`
  - [Ligne 12] Fonction : def _mock_discovery_token()
  - [Ligne 21] Fonction : def _mock_httpx_client()
  - [Ligne 31] Fonction : def _make_mock_response()
  - [Ligne 50] Classe : class TestDiscoveryApps
  - [Ligne 102] Classe : class TestDiscoveryOpenApi
  - [Ligne 147] Classe : class TestDiscoveryProxy
  - [Ligne 313] Classe : class TestFilterOpenApiSpec
  - [Ligne 410] Classe : class TestDiscoveryStoredSpec
  - [Ligne 467] Classe : class TestM2MSetup
  - [Ligne 594] Classe : class TestMarkdownGeneration

### 📄 `hub/tests/test_app_replace.py`
  - [Ligne 24] Classe : class TestAppAutoReplace
  - [Ligne 212] Classe : class TestAppDeleteCascade
  - [Ligne 387] Classe : class TestCredentialsFileManagement
  - [Ligne 439] Classe : class TestAutoRegistrationFromNonDockerIP

### 📄 `hub/tests/test_sse_permissions.py`
  - [Ligne 11] Classe : class TestUserPermissionsSSE
  - [Ligne 127] Classe : class TestRefreshTokenPermissions

### 📄 `hub/tests/test_compatibility_routes.py`
  - [Ligne 11] Classe : class TestCompatibilityRoutes

### 📄 `hub/tests/test_api_users.py`
  - [Ligne 13] Classe : class TestUsersEndpoints
  - [Ligne 260] Classe : class TestM2MBulkUserRoles
  - [Ligne 415] Classe : class TestCsvImport

### 📄 `hub/tests/test_auth.py`
  - [Ligne 26] Classe : class TestPasswordHashing
  - [Ligne 68] Classe : class TestJWTCreation
  - [Ligne 132] Classe : class TestJWTVerification
  - [Ligne 192] Classe : class TestRefreshToken
  - [Ligne 243] Classe : class TestTokenRevocation
  - [Ligne 266] Classe : class TestTokenCleanup

### 📄 `hub/app/docker_scanner.py`
  - [Ligne 64] Classe : class ContainerPort
  - [Ligne 72] Classe : class DiscoveredContainer
  - [Ligne 91] Fonction : def generate_slug()
  - [Ligne 131] Fonction : def format_container_name()
  - [Ligne 140] Fonction : def generate_base_slug()
  - [Ligne 156] Fonction : def detect_container_type()
  - [Ligne 197] Fonction : def should_exclude_container()
  - [Ligne 212] Classe : class DockerScanner

### 📄 `hub/app/models.py`
  - [Ligne 10] Fonction : def utcnow()
  - [Ligne 17] Classe : class ApprovalStatus
  - [Ligne 31] Fonction : def highest_role()
  - [Ligne 60] Classe : class ProfileAppRole
  - [Ligne 77] Classe : class Profile
  - [Ligne 98] Classe : class UserAppRole
  - [Ligne 117] Classe : class AppRole
  - [Ligne 133] Classe : class User
  - [Ligne 174] Classe : class Application
  - [Ligne 235] Classe : class UserFavorite
  - [Ligne 252] Classe : class AuditLog
  - [Ligne 283] Classe : class ErrorLog
  - [Ligne 311] Classe : class ServiceAccount
  - [Ligne 342] Classe : class RefreshToken
  - [Ligne 368] Classe : class RevokedToken
  - [Ligne 382] Classe : class UserSession
  - [Ligne 427] Classe : class BackupMode
  - [Ligne 434] Classe : class BackupStatus
  - [Ligne 441] Classe : class AppBackup
  - [Ligne 485] Classe : class UserMessage
  - [Ligne 529] Classe : class MessageReadReceipt

### 📄 `hub/app/config.py`
  - [Ligne 11] Fonction : def get_server_ip()
  - [Ligne 22] Fonction : def generate_rsa_keys()
  - [Ligne 57] Fonction : def _validate_pem_keys()
  - [Ligne 75] Fonction : def ensure_keys_exist()
  - [Ligne 154] Classe : class Settings
  - [Ligne 243] Fonction : def get_settings()

### 📄 `hub/app/database.py`
  - [Ligne 23] Fonction : def enable_sqlite_fk()
  - [Ligne 37] Classe : class Base
  - [Ligne 54] Fonction : def _strip_sql_comments()

### 📄 `hub/app/main.py`
  - [Ligne 444] Fonction : def _sanitize_error_for_json()
  - [Ligne 519] Fonction : def _get_error_type()

### 📄 `hub/app/health_checker.py`
  - [Ligne 27] Fonction : def utcnow()
  - [Ligne 55] Fonction : def _get_http_client()
  - [Ligne 76] Fonction : def get_app_health_status()
  - [Ligne 91] Fonction : def get_all_health_statuses()
  - [Ligne 251] Fonction : def start_health_checker()
  - [Ligne 263] Fonction : def stop_health_checker()
  - [Ligne 272] Fonction : def is_health_checker_running()
  - [Ligne 277] Fonction : def get_combined_app_status()

### 📄 `hub/app/schemas.py`
  - [Ligne 7] Classe : class GrantType
  - [Ligne 14] Classe : class ApprovalStatusEnum
  - [Ligne 22] Classe : class UserBase
  - [Ligne 27] Classe : class UserCreate
  - [Ligne 34] Classe : class UserUpdate
  - [Ligne 44] Classe : class PasswordChangeRequest
  - [Ligne 51] Classe : class ProfileAppRoleRequest
  - [Ligne 57] Classe : class ProfileCreate
  - [Ligne 65] Classe : class ProfileUpdate
  - [Ligne 73] Classe : class ProfileAppRoleResponse
  - [Ligne 83] Classe : class ProfileBriefResponse
  - [Ligne 92] Classe : class ProfileResponse
  - [Ligne 106] Classe : class ProfileApplyRequest
  - [Ligne 112] Classe : class ProfileRemoveUsersRequest
  - [Ligne 119] Classe : class UserAppRoleResponse
  - [Ligne 140] Classe : class UserResponse
  - [Ligne 184] Classe : class UserBrief
  - [Ligne 192] Classe : class PaginatedUsersResponse
  - [Ligne 201] Classe : class UserBriefWithRole
  - [Ligne 212] Classe : class AppBase
  - [Ligne 226] Classe : class AppCreateSimplified
  - [Ligne 242] Classe : class AppCreate
  - [Ligne 246] Classe : class AppUpdate
  - [Ligne 262] Classe : class AppResponse
  - [Ligne 281] Classe : class AppResponseWithMyRole
  - [Ligne 326] Classe : class AppBrief
  - [Ligne 336] Classe : class AppWithUsers
  - [Ligne 375] Classe : class AppWithUsersAndDocker
  - [Ligne 416] Classe : class DockerInfoResponse
  - [Ligne 454] Classe : class DockerContainerStats
  - [Ligne 462] Classe : class DockerStatsResponse
  - [Ligne 470] Classe : class AppDiscoveryItem
  - [Ligne 481] Classe : class DiscoverResponse
  - [Ligne 491] Classe : class Token
  - [Ligne 500] Classe : class TokenPayload
  - [Ligne 508] Classe : class LoginRequest
  - [Ligne 514] Classe : class RefreshTokenRequest
  - [Ligne 518] Classe : class LogoutRequest
  - [Ligne 523] Classe : class ClientCredentialsRequest
  - [Ligne 530] Classe : class TokenIntrospection
  - [Ligne 543] Classe : class ServiceAccountBase
  - [Ligne 548] Classe : class ServiceAccountCreate
  - [Ligne 552] Classe : class ServiceAccountUpdate
  - [Ligne 558] Classe : class ServiceAccountResponse
  - [Ligne 569] Classe : class ServiceAccountCreated
  - [Ligne 576] Classe : class UserAppAccess
  - [Ligne 582] Classe : class ImportMode
  - [Ligne 589] Classe : class AppRoleExport
  - [Ligne 595] Classe : class UserExport
  - [Ligne 607] Classe : class AppExport
  - [Ligne 628] Classe : class UserAppRoleExport
  - [Ligne 636] Classe : class ServiceAccountExport
  - [Ligne 648] Classe : class AppBackupExport
  - [Ligne 665] Classe : class BackupExport
  - [Ligne 679] Classe : class BackupImport
  - [Ligne 699] Classe : class ImportResult
  - [Ligne 720] Classe : class DockerPortInfo
  - [Ligne 727] Classe : class DockerContainerInfo
  - [Ligne 747] Classe : class DockerAppGroup
  - [Ligne 761] Classe : class DockerScanResponse
  - [Ligne 773] Classe : class BatchAppItem
  - [Ligne 783] Classe : class BatchImportRequest
  - [Ligne 788] Classe : class BatchAppResult
  - [Ligne 798] Classe : class BatchImportResponse
  - [Ligne 808] Classe : class AppRegisterRequest
  - [Ligne 821] Classe : class AppRegisterResponse
  - [Ligne 830] Classe : class AppOpenApiPush
  - [Ligne 835] Classe : class AppOpenApiResponse
  - [Ligne 844] Classe : class AppApprovalRequest
  - [Ligne 850] Classe : class AppApprovalResponse
  - [Ligne 858] Classe : class PendingAppsResponse
  - [Ligne 864] Classe : class AppCredentialsResponse
  - [Ligne 876] Classe : class BackupModeEnum
  - [Ligne 883] Classe : class BackupStatusEnum
  - [Ligne 890] Classe : class AppBackupConfig
  - [Ligne 897] Classe : class AppBackupConfigUpdate
  - [Ligne 904] Classe : class AppBackupReceive
  - [Ligne 915] Classe : class AppBackupResponse
  - [Ligne 967] Classe : class AppBackupBrief
  - [Ligne 979] Classe : class AppBackupListResponse
  - [Ligne 988] Classe : class AppBackupStatusResponse
  - [Ligne 1004] Classe : class AllBackupsResponse
  - [Ligne 1013] Classe : class BackupTriggerResponse
  - [Ligne 1021] Classe : class BackupExportResponse
  - [Ligne 1031] Classe : class RestoreToAppRequest
  - [Ligne 1040] Classe : class RestoreToAppResponse
  - [Ligne 1057] Classe : class BulkUserAppRoleAssignment
  - [Ligne 1063] Classe : class BulkUserCreate
  - [Ligne 1076] Classe : class BulkUsersCreateRequest
  - [Ligne 1081] Classe : class BulkUserCreateResult
  - [Ligne 1089] Classe : class BulkUsersCreateResponse
  - [Ligne 1097] Classe : class BulkUserRoleUpdate
  - [Ligne 1103] Classe : class BulkUsersRolesUpdateRequest
  - [Ligne 1108] Classe : class BulkUserRoleUpdateResult
  - [Ligne 1117] Classe : class BulkUsersRolesUpdateResponse
  - [Ligne 1125] Classe : class BulkUsersDeleteRequest
  - [Ligne 1131] Classe : class BulkUserDeleteResult
  - [Ligne 1139] Classe : class BulkUsersDeleteResponse
  - [Ligne 1147] Classe : class BulkAppRoleAssignment
  - [Ligne 1154] Classe : class BulkAppRolesAssignRequest
  - [Ligne 1159] Classe : class BulkAppRoleAssignResult
  - [Ligne 1171] Classe : class BulkAppRolesAssignResponse
  - [Ligne 1179] Classe : class BulkRemoveAppAccess
  - [Ligne 1185] Classe : class BulkRemoveAppAccessRequest
  - [Ligne 1190] Classe : class BulkRemoveAppAccessResult
  - [Ligne 1200] Classe : class BulkRemoveAppAccessResponse
  - [Ligne 1208] Classe : class M2MObservedUsersRolesRequest
  - [Ligne 1224] Classe : class M2MObservedAppRole
  - [Ligne 1230] Classe : class M2MObservedUserData
  - [Ligne 1238] Classe : class CsvUserPreviewLine
  - [Ligne 1251] Classe : class CsvPreviewResponse
  - [Ligne 1271] Classe : class MessagePriority
  - [Ligne 1278] Classe : class MessageTargetType
  - [Ligne 1284] Classe : class MessageSenderType
  - [Ligne 1291] Classe : class MessageCreate
  - [Ligne 1303] Classe : class MessageM2MCreate
  - [Ligne 1317] Classe : class MessageBrief
  - [Ligne 1334] Classe : class MessageReadReceiptResponse
  - [Ligne 1341] Classe : class MessageResponse
  - [Ligne 1366] Classe : class PaginatedMessagesResponse
  - [Ligne 1377] Classe : class MessageSyncItem
  - [Ligne 1403] Classe : class MessageSyncResponse
  - [Ligne 1412] Classe : class ReceiptSyncItem
  - [Ligne 1420] Classe : class ReceiptSyncResponse
  - [Ligne 1429] Classe : class MessageDetailSyncResponse

### 📄 `hub/app/auth.py`
  - [Ligne 22] Fonction : def verify_password()
  - [Ligne 26] Fonction : def get_password_hash()
  - [Ligne 30] Fonction : def hash_token()
  - [Ligne 35] Fonction : def generate_jti()
  - [Ligne 40] Fonction : def create_access_token()
  - [Ligne 221] Fonction : def verify_token()

### 📄 `hub/app/routes/docs.py`
  - [Ligne 26] Fonction : def get_hub_version()
  - [Ligne 64] Fonction : def parse_doc_sections()
  - [Ligne 94] Fonction : def get_doc_metadata()
  - [Ligne 123] Fonction : def find_section_by_alias()
  - [Ligne 149] Fonction : def extract_section_content()
  - [Ligne 408] Fonction : def parse_changelog_versions()
  - [Ligne 456] Fonction : def parse_version_tuple()
  - [Ligne 462] Fonction : def extract_version_changes()

### 📄 `hub/app/routes/profiles.py`
  - [Ligne 117] Fonction : def _build_profile_response()

### 📄 `hub/app/hub_self_backup.py`
  - [Ligne 140] Fonction : def create_hub_backup_archive()

### 📄 `hub/app/routes/snapshots.py`
  - [Ligne 55] Fonction : async def list_snapshots()
  - [Ligne 73] Fonction : async def create_snapshot_endpoint()
  - [Ligne 107] Fonction : async def get_in_progress_snapshot()

### 📄 `hub/app/routes/backup.py`

(obsolète — supprimé, remplacé par le système de snapshots)

### 📄 `hub/app/routes/app_backups.py`
  - [Ligne 33] Classe : class UserAppRoleAssignment
  - [Ligne 39] Classe : class UserAppsUpdate

### 📄 `hub/app/routes/apps.py`
  - [Ligne 40] Fonction : def normalize_frontend_url()
  - [Ligne 165] Fonction : def get_whitelist_networks()
  - [Ligne 188] Fonction : def is_docker_network_ip()
  - [Ligne 210] Fonction : def generate_slug()
  - [Ligne 2106] Fonction : def filter_openapi_spec()

### 📄 `hub/app/routes/service_accounts.py`
  - [Ligne 35] Fonction : def get_fernet_cipher()
  - [Ligne 51] Fonction : def encrypt_secret()
  - [Ligne 70] Fonction : def decrypt_secret()
  - [Ligne 92] Fonction : def delete_service_account_credentials()
  - [Ligne 119] Fonction : def save_service_account_credentials()
  - [Ligne 170] Fonction : def read_service_account_credentials()

### 📄 `hub/app/routes/app_backups.py`
  - [Ligne 103] Fonction : def ensure_storage_dir()
  - [Ligne 110] Fonction : def compute_checksum()
  - [Ligne 115] Fonction : def human_readable_size()
  - [Ligne 124] Fonction : def extract_metadata_from_zip()
  - [Ligne 219] Fonction : def encrypt_backup()
  - [Ligne 265] Fonction : def create_encrypted_package()
  - [Ligne 297] Fonction : def decrypt_encrypted_package()

### 📄 `hub/app/routes/messages.py`
  - [Ligne 43] Fonction : def _notify_message()
  - [Ligne 66] Fonction : def _message_to_brief()

### 📄 `hub/app/routes/events.py`
  - [Ligne 63] Fonction : def notify_event()
  - [Ligne 144] Fonction : def notify_stats_update()
  - [Ligne 184] Fonction : def start_cleanup_task()
  - [Ligne 200] Fonction : def stop_cleanup_task()
  - [Ligne 214] Fonction : def force_stop_cleanup_task()
  - [Ligne 240] Fonction : def reset_shutdown_event()
  - [Ligne 250] Fonction : def register_user_connection()
  - [Ligne 257] Fonction : def unregister_user_connection()

### 📄 `hub/app/routes/auth.py`
  - [Ligne 47] Fonction : def _check_rate_limit()
  - [Ligne 70] Fonction : def generate_beacon_token()
  - [Ligne 76] Fonction : def verify_beacon_token()

### 📄 `hub/app/routes/discovery.py`
  - [Ligne 60] Classe : class DiscoveryAppItem
  - [Ligne 76] Classe : class DiscoveryAppsResponse
  - [Ligne 81] Classe : class ProxyRequest
  - [Ligne 91] Classe : class ProxyResponse
  - [Ligne 102] Classe : class M2MEndpointParam
  - [Ligne 111] Classe : class M2MEndpointDetail
  - [Ligne 122] Classe : class M2MSetupResponse
  - [Ligne 252] Fonction : def _get_httpx_client()
  - [Ligne 377] Fonction : def _extract_endpoints_from_spec()
  - [Ligne 424] Fonction : def _resolve_param_type()
  - [Ligne 435] Fonction : def _resolve_schema()
  - [Ligne 475] Fonction : def _generate_body_example()
  - [Ligne 505] Fonction : def _generate_m2m_markdown()

### 📄 `hub/app/routes/audit.py`
  - [Ligne 37] Fonction : def _notify_audit()
  - [Ligne 675] Fonction : def format_duration()

### ⚛️ `frontend/public/js/api.js`
  - [Ligne 8] Composant : API_BASE

### ⚛️ `frontend/public/js/app.js`
  - [Ligne 24] Composant : SSE_DEBOUNCE_DELAY
  - [Ligne 1646] Composant : CONNECTIVITY_REFRESH_DELAY
  - [Ligne 2499] Composant : BOOTSTRAP_ICONS
  - [Ligne 2744] Composant : AVAILABLE_ROLES

