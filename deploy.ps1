$VPS = "root@143.244.160.5"
$REPO = "C:\Users\marti\OneDrive\Documents\GitHub\ross-trading-system"
$REMOTE = "/root/ross-trading-system"

Write-Host "Deploying to VPS..." -ForegroundColor Cyan

scp "$REPO\main.py"      "${VPS}:${REMOTE}/main.py"
scp "$REPO\brain.py"     "${VPS}:${REMOTE}/brain.py"
scp "$REPO\scanner.py"   "${VPS}:${REMOTE}/scanner.py"
scp "$REPO\signals.py"   "${VPS}:${REMOTE}/signals.py"
scp "$REPO\monitor.py"   "${VPS}:${REMOTE}/monitor.py"
scp "$REPO\executor.py"  "${VPS}:${REMOTE}/executor.py"
scp "$REPO\config.py"    "${VPS}:${REMOTE}/config.py"
scp "$REPO\reports.py"   "${VPS}:${REMOTE}/reports.py"
scp "$REPO\session.py"    "${VPS}:${REMOTE}/session.py"
scp "$REPO\dashboard.py" "${VPS}:${REMOTE}/dashboard.py"

Write-Host "Restarting bot..." -ForegroundColor Cyan
ssh $VPS "pm2 restart trading-bot && pm2 restart dashboard 2>/dev/null || pm2 start 'python3 /root/ross-trading-system/dashboard.py' --name dashboard"

Write-Host "Done. Bot restarted." -ForegroundColor Green
