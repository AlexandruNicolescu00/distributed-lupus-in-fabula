# ─────────────────────────────────────────────────────────────────────────────
# Deploy completo su minikube - Windows PowerShell
#
# Uso:
#   .\k8s\deploy.ps1                  # deploy completo
#   .\k8s\deploy.ps1 -Delete          # rimuove tutte le risorse
#   .\k8s\deploy.ps1 -Cpus 6 -Memory 6144   # cluster con più risorse
#   .\k8s\deploy.ps1 -SkipBuild       # salta la build delle immagini
#   .\k8s\deploy.ps1 -SkipMinikube    # cluster già avviato, solo deploy
# ─────────────────────────────────────────────────────────────────────────────

param(
    [switch]$Delete,
    [switch]$SkipBuild,
    [switch]$SkipMinikube,
    [int]$Cpus = 4,
    [int]$Memory = 4096
)

$ErrorActionPreference = "Stop"
$Namespace = "game"
$K8sDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $K8sDir

# ── Colori ────────────────────────────────────────────────────────────────────
# Sostituisci il blocco delle funzioni (righe 25-30 circa) con questo:
function Write-Step { param($msg) Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Write-Ok { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Info { param($msg) Write-Host "  [..] $msg" -ForegroundColor Blue }
function Write-Warn { param($msg) Write-Host "  [!]  $msg" -ForegroundColor Yellow }
function Write-Fail { param($msg) Write-Host "  [X]  $msg" -ForegroundColor Red; exit 1 }

function Invoke-Kubectl {
    param([string[]]$CommandArgs)
    & kubectl @CommandArgs
    if ($LASTEXITCODE -ne 0) { Write-Fail "kubectl $($CommandArgs -join ' ') failed (exit $LASTEXITCODE)" }
}

# ══════════════════════════════════════════════════════════════════════════════
# MODALITA' DELETE
# ══════════════════════════════════════════════════════════════════════════════
if ($Delete) {
    Write-Host "`nRimozione namespace '$Namespace'..." -ForegroundColor Red
    kubectl delete namespace $Namespace --ignore-not-found
    Write-Host "Namespace rimosso.`n" -ForegroundColor Green
    exit 0
}

# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════
$Header = @"

============================================================
  Game Platform - Kubernetes Deploy (Windows)
  CPUs:    $Cpus
  Memory:  ${Memory}MB
  Root:    $RootDir
============================================================
"@
Write-Host $Header -ForegroundColor Cyan

# ══════════════════════════════════════════════════════════════════════════════
# 1. PREREQUISITI
# ══════════════════════════════════════════════════════════════════════════════
Write-Step "Verifica prerequisiti"

foreach ($tool in @("kubectl", "minikube", "docker")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        # Correzione operatore ternario per compatibilità PS 5.1
        $pkgName = if ($tool -eq 'docker') { "Docker" } else { $tool.Substring(0, 1).ToUpper() + $tool.Substring(1) }
        Write-Fail "$tool non trovato nel PATH. Installarlo con: winget install Kubernetes.$pkgName"
    }
    Write-Ok "$tool disponibile"
}

# ══════════════════════════════════════════════════════════════════════════════
# 2. MINIKUBE
# ══════════════════════════════════════════════════════════════════════════════
if (-not $SkipMinikube) {
    Write-Step "Minikube"

    $oldEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $status = minikube status --format='{{.Host}}' 2>$null
    $ErrorActionPreference = $oldEAP

    if ($status -eq "Running") {
        Write-Info "Cluster già in esecuzione"

        # Forza la continuazione anche se minikube restituisce errore sulla config
        $oldEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"

        $cpusRaw = minikube config get cpus 2>$null
        $memRaw = minikube config get memory 2>$null
        
        # Se non trova il valore in config, assume 0 per forzare il check
        [int]$currentCpus = if ($cpusRaw -match '\d+') { [int]$cpusRaw } else { 0 }
        [int]$currentMemory = if ($memRaw -match '\d+') { [int]$memRaw } else { 0 }

        $ErrorActionPreference = $oldEAP # Ripristina lo Stop per il resto dello script

        if ($currentCpus -lt $Cpus -or $currentMemory -lt $Memory) {
            Write-Warn "Risorse attuali non configurate o insufficienti (CPU=$currentCpus, RAM=${currentMemory}MB)"
            Write-Info "Tentativo di riavvio con i nuovi parametri..."
            minikube delete
            minikube start --cpus=$Cpus --memory=$Memory --driver=docker
        }
        else {
            Write-Ok "Risorse OK (CPU=$currentCpus, RAM=${currentMemory}MB)"
        }
    }
    else {
        Write-Info "Avvio cluster..."
        minikube start --cpus=$Cpus --memory=$Memory --driver=docker
    }

    minikube addons enable ingress | Out-Null
    minikube addons enable metrics-server | Out-Null
    
    Write-Info "Attesa ingress controller..."
    kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=120s | Out-Null
    Write-Ok "Ingress pronto"
}

# ══════════════════════════════════════════════════════════════════════════════
# 3. DOCKER REGISTRY & 4. BUILD
# ══════════════════════════════════════════════════════════════════════════════
Write-Step "Configurazione Docker & Build"
& minikube -p minikube docker-env --shell powershell | Invoke-Expression

if (-not $SkipBuild) {
    docker build -t game_backend:latest "$RootDir\backend"
    docker build -t game_frontend:latest "$RootDir\frontend"
    Write-Ok "Immagini create"
}

# ══════════════════════════════════════════════════════════════════════════════
# 5-10. DEPLOY RISORSE (Sintesi per brevità, applica lo stesso fix ai blocchi successivi)
# ══════════════════════════════════════════════════════════════════════════════
Write-Step "Deploy Risorse K8s"

Invoke-Kubectl @("apply", "-f", "$K8sDir\namespace.yml")
Invoke-Kubectl @("apply", "-f", "$K8sDir\configmap.yml")


# Regole Prometheus (dai file sorgente - unica fonte di verità)
$rulesDir = "$RootDir\infrastructure\monitoring\prometheus\rules"
if (Test-Path $rulesDir) {
    kubectl create configmap prometheus-rules `
        --from-file="$rulesDir\" `
        -n $Namespace `
        --dry-run=client -o yaml | kubectl apply -f -
    Write-Ok "ConfigMap prometheus-rules (da $rulesDir)"
}
else {
    Write-Warn "Cartella rules non trovata: $rulesDir - skip"
}

# Dashboard Grafana
$dashboardFile = "$RootDir\infrastructure\monitoring\grafana\dashboards\game-platform.json"
if (Test-Path $dashboardFile) {
    kubectl create configmap grafana-dashboards `
        "--from-file=game-platform.json=$dashboardFile" `
        -n $Namespace `
        --dry-run=client -o yaml | kubectl apply -f -
    Write-Ok "ConfigMap grafana-dashboards"
}
else {
    Write-Warn "Dashboard JSON non trovata: $dashboardFile - skip"
}

# Secret (opzionale)
$secretFile = "$K8sDir\secret.yml"
if (Test-Path $secretFile) {
    Invoke-Kubectl apply, -f, $secretFile
    Write-Ok "Secret applicato"
}
else { Write-Info "secret.yml non trovato -- ok se Redis non ha password" }
 
Invoke-Kubectl @("apply", "-f", "$K8sDir\redis\")

Write-Info "Attesa Redis ready..."
Invoke-Kubectl rollout, status, deployment/redis, -n, $Namespace, --timeout=90s
Write-Ok "Redis pronto"

Invoke-Kubectl @("apply", "-f", "$K8sDir\backend\")

Write-Info "Attesa Backend ready..."
Invoke-Kubectl rollout, status, deployment/backend, -n, $Namespace, --timeout=120s
Write-Ok "Backend pronto"

Invoke-Kubectl @("apply", "-f", "$K8sDir\frontend\")

Write-Info "Attesa Frontend ready..."
Invoke-Kubectl rollout, status, deployment/frontend, -n, $Namespace, --timeout=90s
Write-Ok "Frontend pronto"

Write-Step "Monitoring (Redis Exporter + Prometheus + Grafana)"

Invoke-Kubectl apply, -f, "$K8sDir\monitoring\redis-exporter.yml"
Write-Ok "Redis Exporter applicato"

Write-Info "Verifica ConfigMap prometheus-rules..."
$cm = kubectl get configmap prometheus-rules -n $Namespace -o name 2>$null
if (-not $cm) {
    Write-Warn "ConfigMap prometheus-rules non trovata - ricreo..."
    $rulesDir = "$RootDir\infrastructure\monitoring\prometheus\rules"
    if (-not (Test-Path $rulesDir)) {
        Write-Fail "Cartella rules non trovata: $rulesDir"
    }
    $rulesYaml = kubectl create configmap prometheus-rules --from-file="$rulesDir\" -n $Namespace --dry-run=client -o yaml
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Impossibile generare ConfigMap prometheus-rules"
    }
    $rulesYaml | kubectl apply -f -
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Impossibile applicare ConfigMap prometheus-rules"
    }
}
Write-Ok "ConfigMap prometheus-rules presente"
# ─────────────────────────────────────────────────────────────────────────────

Invoke-Kubectl apply, -f, "$K8sDir\monitoring\prometheus.yml"
Write-Ok "Prometheus applicato"
 
Invoke-Kubectl apply, -f, "$K8sDir\monitoring\grafana.yml"
Write-Ok "Grafana applicata"
 
Write-Info "Attesa Prometheus ready..."
Invoke-Kubectl rollout, status, deployment/prometheus, -n, $Namespace, --timeout=120s
Write-Ok "Prometheus pronto"
 
Write-Info "Attesa Grafana ready..."
Invoke-Kubectl rollout, status, deployment/grafana, -n, $Namespace, --timeout=120s
Write-Ok "Grafana pronta"

Invoke-Kubectl @("apply", "-f", "$K8sDir\ingress.yml")

# ══════════════════════════════════════════════════════════════════════════════
# 11. HOSTS FILE
# ══════════════════════════════════════════════════════════════════════════════
Write-Step "Configurazione hosts"
$hostsPath = "C:\Windows\System32\drivers\etc\hosts"
if ((Get-Content $hostsPath) -notmatch "game\.local") {
    try {
        Add-Content -Path $hostsPath -Value "`n127.0.0.1  game.local" -ErrorAction Stop
        Write-Ok "game.local aggiunto"
    }
    catch {
        Write-Warn "Esegui come Amministratore per aggiornare il file hosts!"
    }
}
# ══════════════════════════════════════════════════════════════════════════════
# 12. RIEPILOGO
# ══════════════════════════════════════════════════════════════════════════════
Write-Host @"

============================================================
  Deploy completato
============================================================
"@ -ForegroundColor Green

Write-Host "`nRisorse nel namespace '$Namespace':"
kubectl get all -n $Namespace

Write-Host "`nIngress:"
kubectl get ingress -n $Namespace

Write-Host @"

============================================================
  Prossimi passi
============================================================

  1. Avvia il tunnel in un terminale separato (tienilo aperto):
       minikube tunnel

  2. Accedi ai servizi:
       Frontend:   http://game.local
       WebSocket:  ws://game.local/ws/<room_id>
       Grafana:    http://game.local/grafana    (admin/admin)
       Prometheus: http://game.local/prometheus

  3. Per cancellare tutto:
       .\k8s\deploy.ps1 -Delete

  4. Per ricreare con risorse modificate (es. 6 CPU, 8GB):
       .\k8s\deploy.ps1 -Cpus 6 -Memory 8192

============================================================
"@ -ForegroundColor Cyan