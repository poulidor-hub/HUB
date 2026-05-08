/**
 * Hub Application - export import environnement
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// ===== Cloner / Migrer — Export & Import d'environnement =====

let backupFileData = null;
let backupFileRef = null;  // Référence au fichier original pour upload ZIP
let backupIsZip = false;   // Le fichier sélectionné est-il un ZIP ?

/**
 * Charge le résumé de l'environnement courant (appelé à l'ouverture de l'onglet)
 */
async function loadBackupInfo() {
    // Reset du formulaire d'import
    document.getElementById('importBackupFile').value = '';
    document.getElementById('backupPreview').classList.add('d-none');
    document.getElementById('importBackupBtn').disabled = true;
    document.getElementById('importResultCard').classList.add('d-none');
    backupFileData = null;
    backupFileRef = null;
    backupIsZip = false;

    // Charger les compteurs de l'environnement courant
    try {
        const usersResp = await api.getUsers({ page: 0 });
        const users = Array.isArray(usersResp) ? usersResp : (usersResp.users || usersResp.items || []);
        const appsCount = allApps.length || (await api.request('/apps')).length;
        const saCount = allServiceAccounts.length || 0;
        const rolesCount = users.reduce((sum, user) => {
            const appRoles = user.app_roles || user.apps || [];
            return sum + appRoles.length;
        }, 0);

        document.getElementById('envSummaryUsers').textContent = users.length;
        document.getElementById('envSummaryApps').textContent = appsCount;
        document.getElementById('envSummaryRoles').textContent = rolesCount;
        document.getElementById('envSummarySA').textContent = saCount;
    } catch (e) {
        console.warn('Impossible de charger le résumé environnement', e);
        document.getElementById('envSummaryUsers').textContent = '–';
        document.getElementById('envSummaryApps').textContent = allApps.length || '–';
        document.getElementById('envSummaryRoles').textContent = '–';
        document.getElementById('envSummarySA').textContent = allServiceAccounts.length || '–';
    }
}

/**
 * Télécharge l'export du backup
 */
async function downloadBackupExport() {
    try {
        const includeJwt = document.getElementById('exportIncludeJwt').checked;
        const includeSaSecrets = document.getElementById('exportIncludeSaSecrets').checked;
        const includeAppBackups = document.getElementById('exportIncludeAppBackups').checked;
        
        showToast('Export', 'Génération de l\'export en cours...', 'info');
        
        const data = await api.exportBackup(includeJwt, includeSaSecrets, includeAppBackups);
        
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        
        if (includeAppBackups && data instanceof Blob) {
            // Télécharger le ZIP
            const url = URL.createObjectURL(data);
            const a = document.createElement('a');
            a.href = url;
            a.download = `hub_export_${timestamp}.zip`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            showToast('Export terminé', 'Archive ZIP avec backups d\'applications téléchargée', 'success');
        } else {
            // Télécharger le JSON
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `hub_export_${timestamp}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            showToast('Export terminé', `${data.users.length} utilisateurs, ${data.applications.length} apps, ${(data.service_accounts || []).length} SA`, 'success');
        }
        
    } catch (error) {
        console.error('Erreur export backup:', error);
        showToast('Erreur', error.message, 'danger');
    }
}

/**
 * Prévisualise le fichier de backup sélectionné
 */
async function previewBackupFile() {
    const fileInput = document.getElementById('importBackupFile');
    const file = fileInput.files[0];
    
    if (!file) {
        document.getElementById('backupPreview').classList.add('d-none');
        document.getElementById('importBackupBtn').disabled = true;
        backupFileData = null;
        backupFileRef = null;
        backupIsZip = false;
        return;
    }
    
    backupFileRef = file;
    
    // Déterminer si c'est un ZIP en regardant l'extension ou les magic bytes
    const isZipByName = file.name.toLowerCase().endsWith('.zip');
    
    if (isZipByName) {
        // Fichier ZIP : utiliser l'endpoint serveur pour le preview
        try {
            const preview = await api.previewBackupUpload(file);
            backupIsZip = true;
            
            // Stocker les données de preview (pas les données complètes)
            backupFileData = {
                _isZip: true,
                users: { length: preview.users_count },
                applications: { length: preview.apps_count },
                service_accounts: { length: preview.service_accounts_count },
                app_backups: { length: preview.app_backups_count },
                jwt_private_key_b64: preview.has_jwt_keys,
                version: preview.version,
                exported_at: preview.exported_at,
                exported_by: preview.exported_by,
                app_backups_by_app: preview.app_backups_by_app
            };
            
            document.getElementById('previewUsers').textContent = preview.users_count;
            document.getElementById('previewApps').textContent = preview.apps_count;
            document.getElementById('previewSA').textContent = preview.service_accounts_count;
            document.getElementById('previewJwt').textContent = preview.has_jwt_keys ? 'Oui' : 'Non';
            document.getElementById('previewBackups').textContent = preview.app_backups_count;
            document.getElementById('previewVersion').textContent = preview.version || '–';
            
            // Mettre à jour les compteurs des checkboxes de sélection
            document.getElementById('importCountUsers').textContent = preview.users_count;
            document.getElementById('importCountApps').textContent = preview.apps_count;
            document.getElementById('importCountRoles').textContent = preview.user_app_roles_count || 0;
            document.getElementById('importCountSA').textContent = preview.service_accounts_count;
            document.getElementById('importCountBackups').textContent = preview.app_backups_count;
            
            if (preview.exported_at) {
                const date = parseUTCDate(preview.exported_at);
                document.getElementById('previewDate').textContent = date.toLocaleString('fr-FR');
            } else {
                document.getElementById('previewDate').textContent = '–';
            }
            document.getElementById('previewBy').textContent = preview.exported_by || '-';
            
            document.getElementById('backupPreview').classList.remove('d-none');
            document.getElementById('importBackupBtn').disabled = false;
            
        } catch (error) {
            console.error('Erreur preview ZIP:', error);
            showToast('Erreur', 'Impossible de lire l\'archive ZIP: ' + error.message, 'danger');
            document.getElementById('backupPreview').classList.add('d-none');
            document.getElementById('importBackupBtn').disabled = true;
            backupFileData = null;
            backupFileRef = null;
            backupIsZip = false;
        }
    } else {
        // Fichier JSON classique
        backupIsZip = false;
        const reader = new FileReader();
        reader.onload = function(e) {
            try {
                backupFileData = JSON.parse(e.target.result);
                
                document.getElementById('previewUsers').textContent = (backupFileData.users || []).length;
                document.getElementById('previewApps').textContent = (backupFileData.applications || []).length;
                document.getElementById('previewSA').textContent = (backupFileData.service_accounts || []).length;
                document.getElementById('previewJwt').textContent = backupFileData.jwt_private_key_b64 ? 'Oui' : 'Non';
                document.getElementById('previewBackups').textContent = (backupFileData.app_backups || []).length;
                document.getElementById('previewVersion').textContent = backupFileData.version || '–';
                
                // Mettre à jour les compteurs des checkboxes de sélection
                document.getElementById('importCountUsers').textContent = (backupFileData.users || []).length;
                document.getElementById('importCountApps').textContent = (backupFileData.applications || []).length;
                document.getElementById('importCountRoles').textContent = (backupFileData.user_app_roles || []).length;
                document.getElementById('importCountSA').textContent = (backupFileData.service_accounts || []).length;
                document.getElementById('importCountBackups').textContent = (backupFileData.app_backups || []).length;
                
                if (backupFileData.exported_at) {
                    const date = parseUTCDate(backupFileData.exported_at);
                    document.getElementById('previewDate').textContent = date.toLocaleString('fr-FR');
                } else {
                    document.getElementById('previewDate').textContent = '–';
                }
                document.getElementById('previewBy').textContent = backupFileData.exported_by || '-';
                
                document.getElementById('backupPreview').classList.remove('d-none');
                document.getElementById('importBackupBtn').disabled = false;
                
            } catch (error) {
                console.error('Erreur parsing backup:', error);
                showToast('Erreur', 'Fichier JSON invalide', 'danger');
                document.getElementById('backupPreview').classList.add('d-none');
                document.getElementById('importBackupBtn').disabled = true;
                backupFileData = null;
            }
        };
        reader.readAsText(file);
    }
}

/**
 * Exécute l'import du backup
 */
async function executeBackupImport() {
    if (!backupFileData) {
        showToast('Erreur', 'Aucun fichier de backup sélectionné', 'warning');
        return;
    }
    
    const importMode = document.getElementById('importMode').value;
    const restoreJwt = document.getElementById('importRestoreJwt').checked;
    
    // Catégories sélectionnées
    const includeOptions = {
        include_users: document.getElementById('importIncludeUsers').checked,
        include_applications: document.getElementById('importIncludeApps').checked,
        include_user_app_roles: document.getElementById('importIncludeRoles').checked,
        include_service_accounts: document.getElementById('importIncludeSA').checked,
        include_app_backups: document.getElementById('importIncludeBackups').checked
    };
    
    // Compteurs pour la confirmation
    const usersCount = backupIsZip ? backupFileData.users.length : (backupFileData.users || []).length;
    const appsCount = backupIsZip ? backupFileData.applications.length : (backupFileData.applications || []).length;
    const saCount = backupIsZip ? backupFileData.service_accounts.length : (backupFileData.service_accounts || []).length;
    const backupsCount = backupIsZip ? backupFileData.app_backups.length : (backupFileData.app_backups || []).length;
    
    let confirmMsg = `Vous allez importer:`;
    if (includeOptions.include_users) confirmMsg += `\n- ${usersCount} utilisateurs`;
    if (includeOptions.include_applications) confirmMsg += `\n- ${appsCount} applications`;
    if (includeOptions.include_service_accounts) confirmMsg += `\n- ${saCount} service accounts`;
    
    if (includeOptions.include_app_backups && backupsCount > 0) {
        confirmMsg += `\n- ${backupsCount} backups d'applications`;
    }
    
    // Catégories exclues
    const excluded = [];
    if (!includeOptions.include_users) excluded.push('utilisateurs');
    if (!includeOptions.include_applications) excluded.push('applications');
    if (!includeOptions.include_user_app_roles) excluded.push('associations user-app');
    if (!includeOptions.include_service_accounts) excluded.push('service accounts');
    if (!includeOptions.include_app_backups) excluded.push('backups apps');
    if (excluded.length > 0) {
        confirmMsg += `\n\n🚫 Exclus: ${excluded.join(', ')}`;
    }
    
    confirmMsg += `\n\nMode: ${importMode.toUpperCase()}`;
    
    if (restoreJwt) {
        confirmMsg += '\n⚠️ Les clés JWT seront restaurées (redémarrage nécessaire)';
    }
    
    confirmMsg += '\n\nContinuer ?';
    
    if (!confirm(confirmMsg)) {
        return;
    }
    
    try {
        const btn = document.getElementById('importBackupBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Import en cours...';
        
        let result;
        
        if (backupIsZip) {
            // Upload du fichier ZIP via le nouvel endpoint
            result = await api.importBackupFile(backupFileRef, importMode, restoreJwt, includeOptions);
        } else {
            // Import JSON classique
            result = await api.importBackup(backupFileData, importMode, restoreJwt, includeOptions);
        }
        
        displayImportResult(result);
        
        if (result.success) {
            showToast('Import réussi', 'Les données ont été importées avec succès', 'success');
            loadAdminData();
            loadUsersPageData();
        } else {
            showToast('Import partiel', 'L\'import a terminé avec des erreurs', 'warning');
        }
        
    } catch (error) {
        console.error('Erreur import backup:', error);
        showToast('Erreur', error.message, 'danger');
        
        displayImportResult({
            success: false,
            errors: [error.message],
            warnings: []
        });
    } finally {
        const btn = document.getElementById('importBackupBtn');
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-upload me-2"></i>Lancer l\'import';
    }
}

/**
 * Affiche le résultat de l'import
 */
function displayImportResult(result) {
    const card = document.getElementById('importResultCard');
    const header = document.getElementById('importResultHeader');
    const statsDiv = document.getElementById('importResultStats');
    const warningsDiv = document.getElementById('importResultWarnings');
    const errorsDiv = document.getElementById('importResultErrors');
    
    // Header styling
    if (result.success) {
        header.className = 'card-header bg-success text-white';
        header.innerHTML = '<i class="bi bi-check-circle me-2"></i>Import réussi';
    } else {
        header.className = 'card-header bg-danger text-white';
        header.innerHTML = '<i class="bi bi-x-circle me-2"></i>Import échoué';
    }
    
    // Stats
    statsDiv.innerHTML = `
        <div class="col-md-3">
            <div class="text-center p-2 border rounded">
                <div class="small text-muted">Utilisateurs</div>
                <span class="badge bg-success">${result.users_created || 0} créés</span>
                <span class="badge bg-info">${result.users_updated || 0} maj</span>
                <span class="badge bg-secondary">${result.users_skipped || 0} skip</span>
            </div>
        </div>
        <div class="col-md-3">
            <div class="text-center p-2 border rounded">
                <div class="small text-muted">Applications</div>
                <span class="badge bg-success">${result.apps_created || 0} créées</span>
                <span class="badge bg-info">${result.apps_updated || 0} maj</span>
                <span class="badge bg-secondary">${result.apps_skipped || 0} skip</span>
            </div>
        </div>
        <div class="col-md-3">
            <div class="text-center p-2 border rounded">
                <div class="small text-muted">Service Accounts</div>
                <span class="badge bg-success">${result.service_accounts_created || 0} créés</span>
                <span class="badge bg-info">${result.service_accounts_updated || 0} maj</span>
                <span class="badge bg-secondary">${result.service_accounts_skipped || 0} skip</span>
            </div>
        </div>
        <div class="col-md-3">
            <div class="text-center p-2 border rounded">
                <div class="small text-muted">Backups Apps</div>
                <span class="badge bg-success">${result.app_backups_created || 0} créés</span>
                <span class="badge bg-secondary">${result.app_backups_skipped || 0} skip</span>
                ${(result.app_backups_errors || 0) > 0 ? `<span class="badge bg-danger">${result.app_backups_errors} err</span>` : ''}
            </div>
        </div>
    `;
    
    // Warnings
    if (result.warnings && result.warnings.length > 0) {
        document.getElementById('importWarningsList').innerHTML = result.warnings.map(w => `<li>${w}</li>`).join('');
        warningsDiv.classList.remove('d-none');
    } else {
        warningsDiv.classList.add('d-none');
    }
    
    // Errors
    if (result.errors && result.errors.length > 0) {
        document.getElementById('importErrorsList').innerHTML = result.errors.map(e => `<li>${e}</li>`).join('');
        errorsDiv.classList.remove('d-none');
    } else {
        errorsDiv.classList.add('d-none');
    }
    
    card.classList.remove('d-none');
}
