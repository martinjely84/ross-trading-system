from flask import Flask, jsonify, request, render_template_string
import json, os, csv
from datetime import datetime
import pytz

app = Flask(__name__)
ET = pytz.timezone("America/New_York")

SESSION_FILE  = "session_state.json"
WEEKLY_FILE   = "weekly_log.json"
INSIGHTS_FILE = "insights.json"
TRADE_LOG     = "trade_log.csv"
BOT_CONTROL   = "bot_control.json"
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")

def load_json(path):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except:
            pass
    return {}

def load_trades():
    trades = []
    if os.path.exists(TRADE_LOG):
        with open(TRADE_LOG) as f:
            reader = csv.DictReader(f)
            for row in reader:
                row["pnl"] = float(row["pnl"])
                trades.append(row)
    return trades

def api_data():
    session  = load_json(SESSION_FILE)
    weekly   = load_json(WEEKLY_FILE)
    insights = load_json(INSIGHTS_FILE)
    all_trades = load_trades()

    total_pnl   = sum(t["pnl"] for t in all_trades)
    total_wins  = sum(1 for t in all_trades if t["pnl"] > 0)
    win_rate    = round(total_wins / len(all_trades) * 100, 1) if all_trades else 0
    today_trades = session.get("trades_today", [])
    today_pnl   = sum(t["pnl"] for t in today_trades)

    # Build equity curve from weekly log
    equity_dates  = []
    equity_values = []
    running = 0
    for d in sorted(weekly.keys()):
        running += weekly[d].get("pnl", 0)
        equity_dates.append(d)
        equity_values.append(round(running, 2))

    return {
        "now": datetime.now(ET).strftime("%A %b %d, %I:%M %p CT"),
        "armed": session.get("armed", False),
        "suspended": session.get("trading_suspended", False),
        "account_value": session.get("account_value", 0),
        "daily_loss_limit": session.get("daily_loss_limit", 0),
        "daily_loss_used": session.get("daily_loss_used", 0),
        "today_pnl": round(today_pnl, 2),
        "today_trades": len(today_trades),
        "total_pnl": round(total_pnl, 2),
        "total_trades": len(all_trades),
        "win_rate": win_rate,
        "open_positions": session.get("open_positions", {}),
        "watchlist": session.get("watchlist", [])[:8],
        "trades_today": today_trades,
        "equity_dates": equity_dates,
        "equity_values": equity_values,
        "latest_review": insights.get("latest_claude_review", ""),
        "latest_suggestions": insights.get("latest_suggestions", []),
        "weekly": weekly,
    }

TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ross Trading Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0e0e0e; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 16px; }
  h1 { font-size: 1.1rem; color: #fff; font-weight: 600; }
  h2 { font-size: 0.85rem; color: #888; font-weight: 500; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 10px; }
  .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
  .status-pill { padding: 4px 12px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }
  .armed   { background: #1a3a1a; color: #4caf50; }
  .unarmed { background: #2a2a2a; color: #888; }
  .suspended { background: #3a1a1a; color: #f44336; }
  .time { font-size: 0.75rem; color: #666; }
  .grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-bottom: 16px; }
  .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
  @media(max-width:600px) { .grid-4 { grid-template-columns: repeat(2,1fr); } }
  .card { background: #1a1a1a; border-radius: 12px; padding: 16px; }
  .card-full { background: #1a1a1a; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
  .big-number { font-size: 1.8rem; font-weight: 700; margin: 4px 0; }
  .positive { color: #4caf50; }
  .negative { color: #f44336; }
  .neutral  { color: #e0e0e0; }
  .label { font-size: 0.72rem; color: #666; }
  .sub { font-size: 0.8rem; color: #888; margin-top: 2px; }
  .progress-bar-bg { background: #2a2a2a; border-radius: 4px; height: 6px; margin-top: 8px; }
  .progress-bar { background: #f44336; border-radius: 4px; height: 6px; transition: width 0.3s; }
  table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
  th { color: #666; font-weight: 500; text-align: left; padding: 6px 4px; border-bottom: 1px solid #2a2a2a; }
  td { padding: 8px 4px; border-bottom: 1px solid #1e1e1e; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 600; }
  .tag-a-plus { background: #1a3a1a; color: #4caf50; }
  .tag-a      { background: #1a2a3a; color: #2196f3; }
  .tag-b      { background: #2a2a1a; color: #ff9800; }
  .review-box { background: #141414; border-left: 3px solid #4caf50; padding: 12px; border-radius: 0 8px 8px 0; font-size: 0.82rem; line-height: 1.6; color: #ccc; white-space: pre-wrap; }
  .refresh-note { text-align: center; color: #444; font-size: 0.7rem; margin-top: 20px; }
  canvas { max-height: 180px; }
</style>
</head>
<body>

<div class="header">
  <h1>Ross Trading Bot</h1>
  <span id="status-pill" class="status-pill unarmed">Loading...</span>
</div>
<div class="time" id="now-time"></div>

<br>

<!-- P&L Cards -->
<div class="grid-4" id="pnl-cards">
  <div class="card">
    <div class="label">All-Time P&L</div>
    <div class="big-number" id="total-pnl">--</div>
    <div class="sub" id="total-trades">-- trades</div>
  </div>
  <div class="card">
    <div class="label">Today's P&L</div>
    <div class="big-number" id="today-pnl">--</div>
    <div class="sub" id="today-trades">-- trades</div>
  </div>
  <div class="card">
    <div class="label">Win Rate</div>
    <div class="big-number neutral" id="win-rate">--%</div>
    <div class="sub" id="account-val">Account: --</div>
  </div>
  <div class="card">
    <div class="label">Daily Risk Used</div>
    <div class="big-number" id="risk-used">--</div>
    <div class="progress-bar-bg"><div class="progress-bar" id="risk-bar" style="width:0%"></div></div>
    <div class="sub" id="risk-remaining">-- remaining</div>
  </div>
</div>

<!-- Equity Curve -->
<div class="card-full">
  <h2>Equity Curve</h2>
  <canvas id="equityChart"></canvas>
</div>

<!-- Open Positions -->
<div class="card-full">
  <h2>Open Positions</h2>
  <table>
    <thead><tr><th>Ticker</th><th>Shares</th><th>Entry</th><th>Stop</th><th>T1</th><th>T2</th></tr></thead>
    <tbody id="positions-body"><tr><td colspan="6" style="color:#555">No open positions</td></tr></tbody>
  </table>
</div>

<!-- Today's Trades -->
<div class="card-full">
  <h2>Today's Trades</h2>
  <table>
    <thead><tr><th>Ticker</th><th>Setup</th><th>Entry</th><th>Exit</th><th>P&L</th><th>R</th><th>Reason</th></tr></thead>
    <tbody id="trades-body"><tr><td colspan="7" style="color:#555">No trades today</td></tr></tbody>
  </table>
</div>

<!-- Watchlist -->
<div class="card-full">
  <h2>Today's Watchlist</h2>
  <table>
    <thead><tr><th>Ticker</th><th>Grade</th><th>Gap</th><th>Float</th><th>RelVol</th><th>Catalyst</th></tr></thead>
    <tbody id="watchlist-body"><tr><td colspan="6" style="color:#555">No watchlist yet</td></tr></tbody>
  </table>
</div>

<!-- Claude Review -->
<div class="card-full">
  <h2>Claude AI Review</h2>
  <div class="review-box" id="claude-review">No review yet — reviews appear at 2:30pm CT after trading.</div>
</div>

<div class="refresh-note">Auto-refreshes every 30 seconds</div>

<script>
let equityChart = null;

function pnlClass(val) {
  return val > 0 ? 'positive' : val < 0 ? 'negative' : 'neutral';
}
function fmt(val) {
  const sign = val >= 0 ? '+' : '';
  return sign + '$' + Math.abs(val).toFixed(2);
}
function tagClass(c) {
  if (c === 'A+') return 'tag-a-plus';
  if (c === 'A')  return 'tag-a';
  return 'tag-b';
}

async function refresh() {
  const d = await fetch('/api/data').then(r => r.json());

  document.getElementById('now-time').textContent = d.now;

  const pill = document.getElementById('status-pill');
  if (d.suspended) { pill.textContent = 'SUSPENDED'; pill.className = 'status-pill suspended'; }
  else if (d.armed) { pill.textContent = 'ARMED'; pill.className = 'status-pill armed'; }
  else { pill.textContent = 'NOT ARMED'; pill.className = 'status-pill unarmed'; }

  const tp = document.getElementById('total-pnl');
  tp.textContent = fmt(d.total_pnl);
  tp.className = 'big-number ' + pnlClass(d.total_pnl);
  document.getElementById('total-trades').textContent = d.total_trades + ' total trades';

  const tdp = document.getElementById('today-pnl');
  tdp.textContent = fmt(d.today_pnl);
  tdp.className = 'big-number ' + pnlClass(d.today_pnl);
  document.getElementById('today-trades').textContent = d.today_trades + ' trades today';

  document.getElementById('win-rate').textContent = d.win_rate + '%';
  document.getElementById('account-val').textContent = 'Account: $' + d.account_value.toLocaleString();

  const riskPct = d.daily_loss_limit > 0 ? (d.daily_loss_used / d.daily_loss_limit * 100) : 0;
  document.getElementById('risk-used').textContent = '$' + d.daily_loss_used.toFixed(2);
  document.getElementById('risk-bar').style.width = Math.min(riskPct, 100) + '%';
  const remaining = d.daily_loss_limit - d.daily_loss_used;
  document.getElementById('risk-remaining').textContent = '$' + remaining.toFixed(2) + ' remaining';

  // Equity chart
  if (d.equity_dates.length > 0) {
    const ctx = document.getElementById('equityChart').getContext('2d');
    if (equityChart) equityChart.destroy();
    equityChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: d.equity_dates,
        datasets: [{
          data: d.equity_values,
          borderColor: d.equity_values[d.equity_values.length-1] >= 0 ? '#4caf50' : '#f44336',
          backgroundColor: 'transparent',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
        }]
      },
      options: {
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#555', maxTicksLimit: 6 }, grid: { color: '#1e1e1e' } },
          y: { ticks: { color: '#555', callback: v => '$'+v }, grid: { color: '#1e1e1e' } }
        }
      }
    });
  }

  // Positions
  const pb = document.getElementById('positions-body');
  const pos = Object.values(d.open_positions);
  if (pos.length === 0) {
    pb.innerHTML = '<tr><td colspan="6" style="color:#555">No open positions</td></tr>';
  } else {
    pb.innerHTML = pos.map(p => `
      <tr>
        <td><b>${p.ticker}</b></td>
        <td>${p.remaining_shares}</td>
        <td>$${p.entry_price.toFixed(2)}</td>
        <td>$${p.current_stop.toFixed(2)}</td>
        <td>$${p.target1.toFixed(2)}</td>
        <td>$${p.target2.toFixed(2)}</td>
      </tr>`).join('');
  }

  // Today's trades
  const tb = document.getElementById('trades-body');
  if (d.trades_today.length === 0) {
    tb.innerHTML = '<tr><td colspan="7" style="color:#555">No trades today</td></tr>';
  } else {
    tb.innerHTML = d.trades_today.map(t => `
      <tr>
        <td><b>${t.ticker}</b></td>
        <td>${t.setup_type}</td>
        <td>$${parseFloat(t.entry_price).toFixed(2)}</td>
        <td>$${parseFloat(t.exit_price).toFixed(2)}</td>
        <td class="${pnlClass(t.pnl)}">${fmt(t.pnl)}</td>
        <td>${t.r_multiple}R</td>
        <td>${t.exit_reason}</td>
      </tr>`).join('');
  }

  // Watchlist
  const wb = document.getElementById('watchlist-body');
  if (d.watchlist.length === 0) {
    wb.innerHTML = '<tr><td colspan="6" style="color:#555">No watchlist yet</td></tr>';
  } else {
    wb.innerHTML = d.watchlist.map(s => `
      <tr>
        <td><b>${s.ticker}</b></td>
        <td><span class="tag ${tagClass(s.conviction)}">${s.conviction}</span></td>
        <td class="positive">+${s.gap_pct}%</td>
        <td>${s.float ? (s.float/1e6).toFixed(1)+'M' : '--'}</td>
        <td>${s.rel_vol}x</td>
        <td style="color:#888;font-size:0.72rem">${(s.catalyst||'').substring(0,50)}${s.catalyst && s.catalyst.length>50?'...':''}</td>
      </tr>`).join('');
  }

  // Claude review
  if (d.latest_review) {
    document.getElementById('claude-review').textContent = d.latest_review;
  }
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(TEMPLATE)

@app.route("/api/data")
def data():
    return jsonify(api_data())

@app.route("/api/control", methods=["POST"])
def control():
    if not DASHBOARD_TOKEN:
        return jsonify({"ok": False, "error": "dashboard control disabled"}), 403
    supplied = request.headers.get("X-Dashboard-Token") or request.args.get("token")
    if supplied != DASHBOARD_TOKEN:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    action = payload.get("action")
    allowed = {"arm", "disarm", "suspend", "resume", "close_all"}
    if action not in allowed:
        return jsonify({"ok": False, "error": "invalid action"}), 400
    tmp_file = f"{BOT_CONTROL}.tmp"
    with open(tmp_file, "w") as f:
        json.dump({"action": action, "created_at": datetime.now(ET).isoformat()}, f)
    os.replace(tmp_file, BOT_CONTROL)
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
