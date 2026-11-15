# Заливає локальні зміни з цієї папки на GitHub.
#
# Одноразове налаштування (після встановлення Git):
#   .\deploy.ps1 -Setup -RepoUrl https://github.com/<логін>/padel-monitor.git
# Далі щоразу після змін:
#   .\deploy.ps1 "що змінилось"
param(
    [string]$Message = "update code",
    [switch]$Setup,
    [string]$RepoUrl
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Run([string]$cmd) {
    Write-Host "> git $cmd" -ForegroundColor DarkGray
    $args_ = $cmd -split ' '
    & git @args_
    if ($LASTEXITCODE -ne 0) { throw "git $cmd завершився з помилкою" }
}

if ($Setup) {
    if (-not $RepoUrl) { throw "Вкажіть -RepoUrl https://github.com/<логін>/padel-monitor.git" }
    if (-not (Test-Path .git)) { Run "init -q -b main" }
    if (-not (git remote)) { Run "remote add origin $RepoUrl" }
    Run "fetch -q origin"
    # прив'язуємось до історії на GitHub, локальні файли лишаються як є
    Run "reset -q origin/main"
    Run "branch -q --set-upstream-to=origin/main main"
    Run "config user.name padel-monitor-local"
    Run "config user.email padel-monitor@users.noreply.github.com"
    # журнали і звіт беремо з GitHub (їх пише тільки Actions)
    try { & git checkout origin/main -- data reports 2>$null } catch { }  # до першого запуску їх ще немає
}

if (-not (Test-Path .git)) { throw "Спершу: .\deploy.ps1 -Setup -RepoUrl <адреса репозиторію>" }

# GitHub Actions постійно комітить дані, тож спершу підтягуємо їх
Run "pull -q --rebase --autostash"
# заливаємо лише код і налаштування; data/ і reports/ веде GitHub Actions
Run "add -A -- . :!data :!reports"
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    & git commit -q -m $Message
    if ($LASTEXITCODE -ne 0) { throw "commit не вдався" }
    Run "push -q"
    Write-Host "Готово: зміни на GitHub." -ForegroundColor Green
} else {
    Write-Host "Нових змін немає." -ForegroundColor Yellow
}
