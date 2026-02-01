# -*- coding: utf-8 -*-
"""
바이낸스 속도 테스트 서버
독립 실행: python speed_test.py
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI()

HTML_CONTENT = '''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Speed Test</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a1a2e;color:#eee;font:14px monospace;padding:10px}
input,select,button{background:#16213e;color:#eee;border:1px solid #0f3460;padding:6px 10px;border-radius:4px}
button{cursor:pointer;background:#0f3460}
button:hover{background:#1a4a8a}
.row{display:flex;gap:8px;margin-bottom:10px;align-items:center}
#price{font-size:28px;margin:10px 0}
#change{font-size:16px;margin-bottom:10px}
#volume{font-size:14px;color:#888;margin-bottom:10px}
#stats{color:#888;font-size:12px}
.up{color:#0f0}
.down{color:#f00}
.neutral{color:#888}
#error{color:#f55;margin:5px 0}
#canvas{background:#0d1117;border:1px solid #333;margin-top:10px}
#symbols{position:absolute;background:#16213e;border:1px solid #0f3460;max-height:200px;overflow-y:auto;display:none;z-index:10}
#symbols div{padding:6px 10px;cursor:pointer}
#symbols div:hover{background:#0f3460}
.latency{color:#ff0}
.status{color:#0af}
</style>
</head>
<body>
<div class="row">
<input id="sym" placeholder="BTCUSDT" autocomplete="off" value="BTCUSDT">
<div id="symbols"></div>
<select id="tf">
<option value="aggTrade">Tick</option><option>1m</option><option>3m</option><option>5m</option><option>15m</option><option>30m</option>
<option>1h</option><option>4h</option><option>1d</option>
</select>
<select id="endpoint">
<option value="fstream.binance.com">Global</option>
</select>
<button id="startBtn">Start</button>
<button id="stopBtn">Stop</button>
<button id="pingBtn">Ping All</button>
<span id="status"></span>
</div>
<div id="error"></div>
<div id="price" class="neutral">-</div>
<div id="change">-</div>
<div id="volume">Vol: - | Sum: -</div>
<div id="stats">REST: -ms | WS: -ms | Updates: 0</div>
<canvas id="canvas" width="600" height="200"></canvas>

<script>
var ws = null;
var symbol = 'BTCUSDT';
var tf = '1m';
var updates = 0;
var lastWsTime = 0;
var candles = [];
var restLatency = '-';

var lastPrice = 0;
var totalVolume = 0;

var symInput = document.getElementById('sym');
var symList = document.getElementById('symbols');
var priceEl = document.getElementById('price');
var changeEl = document.getElementById('change');
var volumeEl = document.getElementById('volume');
var statsEl = document.getElementById('stats');
var errorEl = document.getElementById('error');
var statusEl = document.getElementById('status');
var canvas = document.getElementById('canvas');
var ctx = canvas.getContext('2d');

document.getElementById('startBtn').onclick = start;
document.getElementById('stopBtn').onclick = stop;
document.getElementById('pingBtn').onclick = pingAll;

var endpoints = [
    {name: 'Global', ws: 'fstream.binance.com', rest: 'fapi.binance.com'}
];

function pingAll() {
    setStatus('Pinging all servers...');
    setError('');
    var results = [];
    var completed = 0;

    endpoints.forEach(function(ep, idx) {
        var t0 = performance.now();
        fetch('https://' + ep.rest + '/fapi/v1/time')
            .then(function(res) {
                var latency = (performance.now() - t0).toFixed(0);
                results[idx] = ep.name + ': ' + latency + 'ms';
                completed++;
                if (completed === endpoints.length) {
                    setStatus(results.join(' | '));
                }
            })
            .catch(function(e) {
                results[idx] = ep.name + ': Error';
                completed++;
                if (completed === endpoints.length) {
                    setStatus(results.join(' | '));
                }
            });
    });
}

function setStatus(msg) {
    statusEl.innerHTML = '<span class="status">' + msg + '</span>';
}

function setError(msg) {
    errorEl.textContent = msg;
}

// 심볼 자동완성
var allSymbols = [];
setStatus('Loading symbols...');

fetch('https://fapi.binance.com/fapi/v1/exchangeInfo')
    .then(function(r) { return r.json(); })
    .then(function(d) {
        allSymbols = d.symbols.filter(function(s) { return s.status === 'TRADING'; }).map(function(s) { return s.symbol; });
        setStatus('Ready (' + allSymbols.length + ' symbols)');
    })
    .catch(function(e) {
        setError('Failed to load symbols: ' + e.message);
        setStatus('');
    });

symInput.oninput = function() {
    var v = symInput.value.toUpperCase();
    if (v.length < 1) {
        symList.style.display = 'none';
        return;
    }
    var m = allSymbols.filter(function(s) { return s.indexOf(v) !== -1; }).slice(0, 10);
    if (m.length) {
        symList.innerHTML = m.map(function(s) { return '<div onclick="selectSym(\\x27'+s+'\\x27)">'+s+'</div>'; }).join('');
        symList.style.display = 'block';
        symList.style.left = symInput.offsetLeft + 'px';
        symList.style.top = (symInput.offsetTop + symInput.offsetHeight) + 'px';
        symList.style.width = symInput.offsetWidth + 'px';
    } else {
        symList.style.display = 'none';
    }
};

function selectSym(s) {
    symbol = s;
    symInput.value = s;
    symList.style.display = 'none';
}

document.onclick = function(e) {
    if (e.target !== symInput) symList.style.display = 'none';
};

function start() {
    stop();
    setError('');
    symbol = symInput.value.toUpperCase();
    tf = document.getElementById('tf').value;
    updates = 0;
    candles = [];
    setStatus('Connecting...');

    if (tf === 'aggTrade') {
        // aggTrade 모드: 실시간 체결 데이터
        restLatency = '-';
        statsEl.innerHTML = 'REST: - | WS: connecting... | Updates: 0';

        var wsEndpoint = document.getElementById('endpoint').value;
        ws = new WebSocket('wss://' + wsEndpoint + '/ws/' + symbol.toLowerCase() + '@aggTrade');
        ws.onopen = function() {
            setStatus('Connected (Tick)');
            console.log('WS aggTrade connected');
        };
        ws.onmessage = function(e) {
            var now = performance.now();
            var wsLatency = lastWsTime ? (now - lastWsTime).toFixed(1) : '-';
            lastWsTime = now;
            updates++;

            var d = JSON.parse(e.data);
            var price = parseFloat(d.p);
            var qty = parseFloat(d.q);
            var quoteVol = price * qty;
            var isBuyerMaker = d.m;

            // 가격 색상 (매수/매도 기준)
            if (!isBuyerMaker) {
                priceEl.className = 'up';
            } else {
                priceEl.className = 'down';
            }
            priceEl.textContent = price.toFixed(8);

            // 변동폭
            if (lastPrice > 0) {
                var diff = price - lastPrice;
                var diffPct = (diff / lastPrice * 100);
                var diffClass = diff >= 0 ? 'up' : 'down';
                var diffSign = diff >= 0 ? '+' : '';
                changeEl.innerHTML = '<span class="' + diffClass + '">' + diffSign + diff.toFixed(8) + ' (' + diffSign + diffPct.toFixed(4) + '%)</span>';
            }

            lastPrice = price;
            totalVolume += qty;

            // 볼륨 표시
            volumeEl.textContent = 'Qty: ' + qty.toFixed(8) + ' | Sum: ' + totalVolume.toFixed(8) + ' | Trade: $' + quoteVol.toFixed(8);

            statsEl.innerHTML = 'Mode: <span class="latency">Tick</span> | WS interval: <span class="latency">' + wsLatency + 'ms</span> | Trades: ' + updates;
        };
        ws.onerror = function(e) {
            console.error('WS error', e);
            setError('WebSocket error');
        };
        ws.onclose = function() {
            setStatus('Disconnected');
        };
    } else {
        // 캔들 모드
        var wsEndpoint = document.getElementById('endpoint').value;
        var restEndpoint = wsEndpoint.replace('fstream', 'fapi').replace('binancefuture', 'binancefuture');
        var t0 = performance.now();
        fetch('https://' + restEndpoint + '/fapi/v1/klines?symbol=' + symbol + '&interval=' + tf + '&limit=50')
            .then(function(res) {
                restLatency = (performance.now() - t0).toFixed(1);
                if (!res.ok) {
                    return res.json().then(function(err) { throw new Error(err.msg || 'HTTP ' + res.status); });
                }
                return res.json();
            })
            .then(function(data) {
                candles = data.map(function(k) { return {t:k[0], o:+k[1], h:+k[2], l:+k[3], c:+k[4], v:+k[5]}; });
                drawCandles();
                if (candles.length > 0) {
                    lastPrice = candles[candles.length-1].c;
                    priceEl.textContent = lastPrice.toFixed(8);
                    priceEl.className = 'neutral';
                }
                statsEl.innerHTML = 'REST: <span class="latency">' + restLatency + 'ms</span> | WS: connecting... | Updates: 0';

                // WebSocket 연결
                ws = new WebSocket('wss://' + wsEndpoint + '/ws/' + symbol.toLowerCase() + '@kline_' + tf);
                ws.onopen = function() {
                    setStatus('Connected');
                    console.log('WS connected');
                };
                ws.onmessage = function(e) {
                    var now = performance.now();
                    var wsLatency = lastWsTime ? (now - lastWsTime).toFixed(1) : '-';
                    lastWsTime = now;
                    updates++;

                    var d = JSON.parse(e.data);
                    var k = d.k;
                    var price = parseFloat(k.c);
                    var vol = parseFloat(k.v);
                    var quoteVol = parseFloat(k.q);

                    // 가격 색상
                    if (price > lastPrice) {
                        priceEl.className = 'up';
                    } else if (price < lastPrice) {
                        priceEl.className = 'down';
                    }
                    priceEl.textContent = price.toFixed(8);

                    // 변동폭
                    var diff = price - lastPrice;
                    var diffPct = lastPrice > 0 ? (diff / lastPrice * 100) : 0;
                    var diffClass = diff >= 0 ? 'up' : 'down';
                    var diffSign = diff >= 0 ? '+' : '';
                    changeEl.innerHTML = '<span class="' + diffClass + '">' + diffSign + diff.toFixed(8) + ' (' + diffSign + diffPct.toFixed(4) + '%)</span>';

                    lastPrice = price;
                    totalVolume += vol;

                    // 볼륨 표시
                    volumeEl.textContent = 'Vol: ' + vol.toFixed(8) + ' | Sum: ' + totalVolume.toFixed(8) + ' | Quote: $' + quoteVol.toFixed(8);

                    var candle = {t:k.t, o:+k.o, h:+k.h, l:+k.l, c:+k.c, v:vol};
                    if (candles.length && candles[candles.length-1].t === candle.t) {
                        candles[candles.length-1] = candle;
                    } else {
                        candles.push(candle);
                        if (candles.length > 50) candles.shift();
                    }
                    drawCandles();

                    statsEl.innerHTML = 'REST: <span class="latency">' + restLatency + 'ms</span> | WS interval: <span class="latency">' + wsLatency + 'ms</span> | Updates: ' + updates;
                };
                ws.onerror = function(e) {
                    console.error('WS error', e);
                    setError('WebSocket error');
                };
                ws.onclose = function() {
                    setStatus('Disconnected');
                };
            })
            .catch(function(e) {
                setError('Error: ' + e.message);
                setStatus('');
                console.error(e);
            });
    }
}

function stop() {
    if (ws) {
        ws.close();
        ws = null;
    }
    priceEl.textContent = '-';
    priceEl.className = 'neutral';
    changeEl.textContent = '-';
    volumeEl.textContent = 'Vol: - | Sum: -';
    updates = 0;
    lastWsTime = 0;
    lastPrice = 0;
    totalVolume = 0;
}

function drawCandles() {
    if (!candles.length) return;
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, 600, 200);

    var w = 600 / candles.length;
    var prices = [];
    candles.forEach(function(c) { prices.push(c.h, c.l); });
    var min = Math.min.apply(null, prices);
    var max = Math.max.apply(null, prices);
    var range = max - min || 1;

    function scale(v) {
        return 190 - (v - min) / range * 180;
    }

    candles.forEach(function(c, i) {
        var x = i * w + w / 2;
        var color = c.c >= c.o ? '#0f0' : '#f00';
        ctx.strokeStyle = color;
        ctx.beginPath();
        ctx.moveTo(x, scale(c.h));
        ctx.lineTo(x, scale(c.l));
        ctx.stroke();
        ctx.fillStyle = color;
        var top = scale(Math.max(c.o, c.c));
        var bot = scale(Math.min(c.o, c.c));
        ctx.fillRect(x - w * 0.3, top, w * 0.6, Math.max(1, bot - top));
    });
}
</script>
</body>
</html>'''

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_CONTENT

if __name__ == "__main__":
    print("Speed Test Server: http://localhost:8899")
    uvicorn.run(app, host="0.0.0.0", port=8899)
