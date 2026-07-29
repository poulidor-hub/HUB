/**
 * Hub Application - État global partagé
 *
 * Toutes les déclarations top-level `let`/`const` ici sont visibles dans les
 * autres scripts classiques (même realm). Ce module est chargé avant `app.js`
 * et avant tout module métier qui consomme ces variables.
 *
 * Ne pas dupliquer ces déclarations dans app.js ou tout autre module.
 */

// ===== Auth & utilisateur courant =====
let currentUser = null;
let hubHost = null;       // IP/hostname du Hub (chargé depuis l'API)
let beaconToken = null;   // Token HMAC éphémère pour sécuriser beacon-logout

// ===== Catalogue d'apps & comptes de service =====
let allApps = [];
let allServiceAccounts = [];
let allOidcClients = [];

// ===== Filtres admin apps =====
let adminAppsSearchQuery = '';
let adminAppsStatusFilter = 'all';
let adminAppsCategoryFilter = 'all';
let adminAppsWorkshopFilter = 'false';  // 'false' (prod), 'true' (ateliers), 'all' (tout)
let adminAppsUnmanagedFilter = 'all';  // 'all' (défaut, rien de caché), 'managed', 'unmanaged'

// ===== Dashboard utilisateur =====
let dashboardFilter = 'all';   // 'all' | 'favorites' | catégorie
let dashboardApps = [];        // apps affichées sur le dashboard (après loadApps)

// Tracking des fenêtres d'applications ouvertes (pour fermeture auto au logout)
const openedAppWindows = new Map();

// Timestamp du dernier acquittement des erreurs (pour le badge de notifications)
let errorsLastAcknowledgedAt = localStorage.getItem('errorsLastAcknowledgedAt') || null;

// ===== Bootstrap modals & toast (instanciés au DOMContentLoaded) =====
let appFormModal, userFormModal, serviceAccountFormModal, saCredentialsModal, toast;
let oidcClientFormModal, oidcClientCredentialsModal;

// ===== SSE: état de connexion et debounces =====
let eventSource = null;
let sseWasConnected = false;     // Indique si on a déjà été connecté (détecte les reconnexions)
let _sseInitializing = false;    // Guard contre les initialisations SSE concurrentes
let _sseAppsUpdateTimer = null;
let _ssePermissionsUpdateTimer = null;
const SSE_DEBOUNCE_DELAY = 800;  // ms

// Tracking des tooltips Bootstrap (évite les fuites mémoire)
let _activeTooltips = [];

// ===== Navigation =====
let currentPage = 'dashboard';
