// Live Price Update & Portfolio Summary Polling Script
window.userHoldings = window.userHoldings || {};

// Cross-Tab Wallet and Portfolio Auto-Sync
const walletSyncChannel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('bharatinvest_wallet_sync') : null;

function applyWalletUpdate(balanceVal, holdings = null) {
  const numBal = parseFloat(balanceVal);
  if (isNaN(numBal)) return;

  const formattedBal = '₹' + numBal.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});

  // 1. Navbar wallet balance
  const navBalEl = document.getElementById('navWalletBalance');
  if (navBalEl) navBalEl.textContent = formattedBal;

  // 2. Funds page wallet balance
  const fundsPageBalEl = document.getElementById('fundsPageBalance');
  if (fundsPageBalEl) fundsPageBalEl.textContent = formattedBal;

  // 3. Modal balance display
  const modalBalEl = document.getElementById('modalBalanceDisplay');
  if (modalBalEl) modalBalEl.textContent = formattedBal;

  if (typeof modalWalletBalance !== 'undefined') {
    modalWalletBalance = numBal;
    if (typeof recalcOrderEstimate === 'function') {
      recalcOrderEstimate();
    }
  }

  // 4. Update holdings if provided
  if (holdings && typeof window.userHoldings !== 'undefined') {
    window.userHoldings = holdings;
    document.querySelectorAll('.holding-badge').forEach(el => {
      const sym = el.getAttribute('data-symbol');
      if (sym) {
        const item = holdings[sym];
        const qty = item ? (typeof item === 'object' ? item.quantity : item) : 0;
        if (qty > 0) {
          el.style.display = 'inline-block';
          el.innerText = `💼 ${qty} Owned`;
        } else {
          el.style.display = 'none';
          el.innerText = '';
        }
      }
    });
    document.querySelectorAll('.btn-sell-action').forEach(btn => {
      const sym = btn.getAttribute('data-symbol');
      if (sym) {
        const item = holdings[sym];
        const qty = item ? (typeof item === 'object' ? item.quantity : item) : 0;
        btn.innerText = qty > 0 ? `SELL (${qty})` : 'SELL';
      }
    });
  }
}
window.applyWalletUpdate = applyWalletUpdate;

function broadcastWalletUpdate(newBalance, holdings = null) {
  if (walletSyncChannel) {
    try {
      walletSyncChannel.postMessage({ type: 'WALLET_UPDATE', balance: newBalance, holdings: holdings });
    } catch(e) {}
  }
  try {
    localStorage.setItem('bharatinvest_wallet_sync_event', JSON.stringify({
      balance: newBalance,
      holdings: holdings,
      timestamp: Date.now()
    }));
  } catch(e) {}
}
window.broadcastWalletUpdate = broadcastWalletUpdate;

if (walletSyncChannel) {
  walletSyncChannel.onmessage = (event) => {
    if (event.data && event.data.type === 'WALLET_UPDATE') {
      applyWalletUpdate(event.data.balance, event.data.holdings);
    }
  };
}

// Fallback for cross-tab via localStorage storage event
window.addEventListener('storage', (e) => {
  if (e.key === 'bharatinvest_wallet_sync_event' && e.newValue) {
    try {
      const data = JSON.parse(e.newValue);
      if (data && data.balance !== undefined) {
        applyWalletUpdate(data.balance, data.holdings);
      }
    } catch(err) {}
  }
});

document.addEventListener('DOMContentLoaded', () => {
  function fetchLivePrices() {
    fetch('/action/prices')
      .then(res => res.json())
      .then(data => {
        if (data.success) {
          if (data.market_open !== undefined) {
            window.isMarketOpen = data.market_open;
          }
          if (data.prices) updateDOMPrices(data.prices);
          if (data.user_summary) {
            updateUserSummary(data.user_summary);
            if (data.user_summary.holdings) {
              window.userHoldings = data.user_summary.holdings;
            }
          }
        }
      })
      .catch(err => console.error("Price update error:", err));
  }

  // Fetch immediately on page load
  fetchLivePrices();

  function updateDOMPrices(prices) {
    window.livePricesMap = prices;

    // If Buy/Sell modal is currently open, sync LTP in real-time
    const modalEl = document.getElementById('buySellModal');
    if (modalEl && modalEl.classList.contains('active')) {
      const activeSym = document.getElementById('orderSymbol') ? document.getElementById('orderSymbol').value : null;
      if (activeSym && prices[activeSym]) {
        const liveVal = parseFloat(prices[activeSym].price);
        if (!isNaN(liveVal) && liveVal > 0) {
          modalCurrentLtp = liveVal;
          const ltpDisp = document.getElementById('modalLtpDisplay');
          if (ltpDisp) ltpDisp.innerText = '₹' + liveVal.toFixed(2);
          const oType = document.getElementById('orderType') ? document.getElementById('orderType').value : 'MARKET';
          if (oType === 'MARKET') {
            const pInput = document.getElementById('orderPrice');
            if (pInput) pInput.value = liveVal.toFixed(2);
          }
          if (typeof recalcOrderEstimate === 'function') {
            recalcOrderEstimate();
          }
        }
      }
    }

    // Update elements with data-symbol attribute
    document.querySelectorAll('[data-symbol]').forEach(el => {
      const sym = el.getAttribute('data-symbol');
      if (prices[sym]) {
        const item = prices[sym];
        
        // Update Price Text
        const priceEl = el.querySelector('.live-price');
        if (priceEl) {
          priceEl.textContent = '₹' + parseFloat(item.price).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        }

        // Update Change & Change %
        const changeEl = el.querySelector('.live-change');
        if (changeEl) {
          changeEl.textContent = item.change + ' (' + item.change_pct + ')';
          changeEl.className = 'live-change ' + (item.is_positive ? 'val-gain' : 'val-loss');
        }

        // Update High & Low
        const highEl = el.querySelector('.live-high');
        if (highEl) highEl.textContent = '₹' + item.high;
        const lowEl = el.querySelector('.live-low');
        if (lowEl) lowEl.textContent = '₹' + item.low;

        // Recalculate holding row values if data-qty and data-avg present
        const qty = parseFloat(el.getAttribute('data-qty'));
        const avg = parseFloat(el.getAttribute('data-avg'));
        const unitPrice = parseFloat(item.price);
        if (!isNaN(qty) && !isNaN(avg) && !isNaN(unitPrice)) {
          const curr = qty * unitPrice;
          const inv = qty * avg;
          const pnl = curr - inv;
          const pnlPct = inv > 0 ? (pnl / inv * 100) : 0;

          const currEl = el.querySelector('.holding-curr');
          if (currEl) {
            currEl.textContent = '₹' + curr.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
          }

          const pnlAbsEl = el.querySelector('.pnl-abs');
          if (pnlAbsEl) {
            const sign = pnl >= 0 ? '+' : '-';
            pnlAbsEl.textContent = sign + '₹' + Math.abs(pnl).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
            pnlAbsEl.className = 'pnl-abs ' + (pnl >= 0 ? 'val-gain' : 'val-loss');
          }

          const pnlPctEl = el.querySelector('.pnl-pct');
          if (pnlPctEl) {
            const sign = pnlPct >= 0 ? '+' : '';
            pnlPctEl.textContent = sign + pnlPct.toFixed(2) + '%';
            pnlPctEl.className = 'pnl-pct ' + (pnlPct >= 0 ? 'badge-gain' : 'badge-loss');
          }
        }
      }
    });
  }

  function updateUserSummary(summary) {
    // 1. Total Current Value
    const totCurrEl = document.getElementById('summaryTotalCurrent');
    if (totCurrEl) {
      totCurrEl.textContent = '₹' + parseFloat(summary.total_current).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    }

    // 2. Total Invested
    const totInvEl = document.getElementById('summaryTotalInvested');
    if (totInvEl) {
      totInvEl.textContent = 'Invested: ₹' + parseFloat(summary.total_invested).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    }

    // 3. 1-Day Return
    const dayGainEl = document.getElementById('summaryDayGain');
    if (dayGainEl) {
      const val = parseFloat(summary.day_gain_abs);
      dayGainEl.textContent = (val >= 0 ? '+' : '') + '₹' + Math.abs(val).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
      dayGainEl.className = val >= 0 ? 'val-gain' : 'val-loss';
    }

    const dayPctEl = document.getElementById('summaryDayReturnPct');
    if (dayPctEl) {
      const isPos = summary.is_day_positive;
      dayPctEl.textContent = (isPos ? '+' : '') + summary.day_return_pct;
      dayPctEl.className = isPos ? 'badge-gain' : 'badge-loss';
    }

    // 4. Total Return
    const totRetEl = document.getElementById('summaryTotalReturn');
    if (totRetEl) {
      const val = parseFloat(summary.total_return_abs);
      totRetEl.textContent = (val >= 0 ? '+' : '') + '₹' + Math.abs(val).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' (' + summary.total_return_pct + ')';
      totRetEl.className = val >= 0 ? 'val-gain' : 'val-loss';
    }

    const totRetPctEl = document.getElementById('summaryTotalReturnPct');
    if (totRetPctEl) {
      const isPos = summary.is_total_positive;
      totRetPctEl.textContent = (isPos ? '+' : '') + summary.total_return_pct;
      totRetPctEl.className = isPos ? 'badge-gain' : 'badge-loss';
    }

    // 5. Navbar Wallet Balance
    const navBalEl = document.getElementById('navWalletBalance');
    if (navBalEl) {
      navBalEl.textContent = '₹' + parseFloat(summary.wallet_balance).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    }

    // 6. Funds Page Balance
    const fundsPageBalEl = document.getElementById('fundsPageBalance');
    if (fundsPageBalEl) {
      fundsPageBalEl.textContent = '₹' + parseFloat(summary.wallet_balance).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    }

    // 7. Buy/Sell Modal Balance (if modal is currently open)
    const modalBalEl = document.getElementById('modalBalanceDisplay');
    if (modalBalEl && typeof modalWalletBalance !== 'undefined') {
      modalWalletBalance = parseFloat(summary.wallet_balance);
      modalBalEl.textContent = '₹' + modalWalletBalance.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
      if (typeof recalcOrderEstimate === 'function') {
        recalcOrderEstimate();
      }
    }
  }

  // Poll every 5 seconds
  setInterval(fetchLivePrices, 5000);
});
