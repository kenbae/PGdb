// ============================================================
// Crypto Trading Dashboard - JavaScript
// ============================================================

let resultChart = null;
let profitChart = null;
let currentSignalsData = [];
let currentSortColumn = 'time';
let currentSortOrder = 'desc';
let allSymbols = [];
let filteredSymbols = [];
let symbolSortOrder = 'asc';
let selectedSymbol = '';

// ============================================================
// 유틸리티 함수
// ============================================================

function formatDateTime(dateString) {
    const date = new Date(dateString);
    return date.toLocaleString('ko-KR', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function formatNumber(num, decimals = 2) {
    if (num === null || num === undefined) return '-';
    return Number(num).toFixed(decimals);
}

function updateLastUpdateTime() {
    const now = new Date();
    document.getElementById('last-update').textContent = now.toLocaleTimeString('ko-KR');
}

// ============================================================
// API 호출 함수
// ============================================================

async function fetchStats() {
    try {
        const response = await fetch('/api/stats');
        const data = await response.json();
        
        document.getElementById('total-signals').textContent = data.total_signals || 0;
        document.getElementById('win-rate').textContent = data.win_rate ? data.win_rate.toFixed(1) + '%' : '0%';
        document.getElementById('avg-r').textContent = data.avg_r ? data.avg_r.toFixed(2) + 'R' : '0R';
        document.getElementById('total-profit').textContent = data.total_profit ? data.total_profit.toFixed(2) + 'R' : '0R';
        
        updateLastUpdateTime();
    } catch (error) {
        console.error('통계 로드 실패:', error);
    }
}

async function fetchResultDistribution() {
    try {
        const response = await fetch('/api/result-distribution');
        const data = await response.json();
        
        const ctx = document.getElementById('resultChart').getContext('2d');
        
        if (resultChart) {
            resultChart.destroy();
        }
        
        resultChart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: data.labels,
                datasets: [{
                    data: data.values,
                    backgroundColor: ['#10B981', '#3B82F6', '#EF4444', '#9CA3AF'],
                    borderWidth: 2,
                    borderColor: '#fff'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { 
                        position: 'bottom',
                        labels: { padding: 15, font: { size: 12 } }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const label = context.label || '';
                                const value = context.parsed || 0;
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = ((value / total) * 100).toFixed(1);
                                return `${label}: ${value}개 (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
    } catch (error) {
        console.error('결과 분포 로드 실패:', error);
    }
}

async function fetchDailyProfit() {
    try {
        const response = await fetch('/api/daily-profit?days=30');
        const data = await response.json();
        
        const ctx = document.getElementById('profitChart').getContext('2d');
        
        if (profitChart) {
            profitChart.destroy();
        }
        
        profitChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.dates,
                datasets: [{
                    label: '누적 R-Multiple',
                    data: data.cumulative_profit,
                    borderColor: '#8B5CF6',
                    backgroundColor: 'rgba(139, 92, 246, 0.1)',
                    fill: true,
                    tension: 0.4,
                    pointRadius: 3,
                    pointHoverRadius: 5,
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                return `누적 수익: ${context.parsed.y.toFixed(2)}R`;
                            }
                        }
                    }
                },
                scales: {
                    y: { 
                        beginAtZero: true,
                        grid: { color: 'rgba(0, 0, 0, 0.05)' }
                    },
                    x: {
                        grid: { display: false }
                    }
                }
            }
        });
    } catch (error) {
        console.error('일별 수익 로드 실패:', error);
    }
}

async function fetchRecentSignals() {
    try {
        const limit = document.getElementById('limit-filter').value;
        
        let url = `/api/recent-signals?limit=${limit}`;
        if (selectedSymbol) {
            url += `&symbol=${selectedSymbol}`;
        }
        
        const response = await fetch(url);
        const data = await response.json();
        
        currentSignalsData = data;
        sortAndDisplaySignals();
        
    } catch (error) {
        console.error('신호 목록 로드 실패:', error);
        document.getElementById('signals-body').innerHTML = 
            '<tr><td colspan="8" class="px-6 py-8 text-center text-red-500">데이터 로드 실패</td></tr>';
    }
}

async function loadSymbols() {
    try {
        const response = await fetch('/api/symbols');
        const symbols = await response.json();
        
        allSymbols = symbols;
        filteredSymbols = [...symbols];
        displaySymbolList();
        
    } catch (error) {
        console.error('심볼 목록 로드 실패:', error);
    }
}

// ============================================================
// 정렬 함수
// ============================================================

function sortAndDisplaySignals() {
    if (currentSignalsData.length === 0) {
        document.getElementById('signals-body').innerHTML = 
            '<tr><td colspan="8" class="px-6 py-8 text-center text-gray-500">신호가 없습니다.</td></tr>';
        return;
    }

    const sortedData = [...currentSignalsData].sort((a, b) => {
        let valA, valB;

        switch(currentSortColumn) {
            case 'time':
                valA = new Date(a.open_time).getTime();
                valB = new Date(b.open_time).getTime();
                break;
            case 'r_multiple':
                valA = a.r_multiple !== null ? a.r_multiple : -999;
                valB = b.r_multiple !== null ? b.r_multiple : -999;
                break;
            case 'score':
                valA = a.score !== null ? a.score : -999;
                valB = b.score !== null ? b.score : -999;
                break;
            default:
                return 0;
        }

        if (currentSortOrder === 'asc') {
            return valA - valB;
        } else {
            return valB - valA;
        }
    });

    displaySignalsTable(sortedData);
    updateSortIcons();
}

function sortTable(column) {
    if (currentSortColumn === column) {
        currentSortOrder = currentSortOrder === 'asc' ? 'desc' : 'asc';
    } else {
        currentSortColumn = column;
        currentSortOrder = 'desc';
    }
    
    sortAndDisplaySignals();
}

function handleSortChange() {
    const sortSelect = document.getElementById('sort-select');
    const [column, order] = sortSelect.value.split('-');
    currentSortColumn = column;
    currentSortOrder = order;
    sortAndDisplaySignals();
}

function updateSortIcons() {
    ['time', 'r_multiple', 'score'].forEach(col => {
        const icon = document.getElementById(`sort-icon-${col}`);
        if (icon) {
            icon.className = 'sort-icon';
            icon.textContent = '⇅';
        }
    });

    const activeIcon = document.getElementById(`sort-icon-${currentSortColumn}`);
    if (activeIcon) {
        activeIcon.className = 'sort-icon active';
        activeIcon.textContent = currentSortOrder === 'asc' ? '↑' : '↓';
    }

    const sortSelect = document.getElementById('sort-select');
    if (sortSelect) {
        sortSelect.value = `${currentSortColumn}-${currentSortOrder}`;
    }
}

// ============================================================
// 테이블 표시
// ============================================================

function displaySignalsTable(data) {
    const tbody = document.getElementById('signals-body');
    tbody.innerHTML = '';
    
    data.forEach(signal => {
        const row = document.createElement('tr');
        row.className = 'hover:bg-gray-50 transition-colors';
        
        const resultColors = {
            'tp2': 'text-green-600 font-semibold',
            'tp1': 'text-blue-600 font-medium',
            'sl': 'text-red-600 font-medium',
            'none': 'text-gray-500'
        };
        
        const resultEmojis = {
            'tp2': '✓✓',
            'tp1': '✓',
            'sl': '✗',
            'none': '○'
        };
        
        const rMultipleColor = signal.r_multiple > 0 ? 'text-green-600 font-semibold' : 
                              signal.r_multiple < 0 ? 'text-red-600 font-semibold' : 
                              'text-gray-500';
        
        row.innerHTML = `
            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-700">
                ${formatDateTime(signal.open_time)}
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-sm font-semibold text-blue-600 hover:text-blue-800 cursor-pointer" onclick="selectSymbolFromTable('${signal.symbol}')">
                ${signal.symbol}
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-600">
                ${signal.tf}
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-sm">
                <span class="badge ${signal.direction === 'long' ? 'badge-long' : 'badge-short'}">
                    ${signal.direction.toUpperCase()}
                </span>
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-700 font-mono">
                ${formatNumber(signal.entry, 2)}
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-sm ${resultColors[signal.result] || ''}">
                ${resultEmojis[signal.result] || ''} ${signal.result ? signal.result.toUpperCase() : '-'}
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-sm ${rMultipleColor} font-mono">
                ${signal.r_multiple !== null ? formatNumber(signal.r_multiple, 2) + 'R' : '-'}
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-700">
                ${signal.score || '-'}
            </td>
        `;
        tbody.appendChild(row);
    });
}

// ============================================================
// 심볼 필터 함수
// ============================================================

function displaySymbolList() {
    const symbolList = document.getElementById('symbol-list');
    symbolList.innerHTML = '';
    
    const sorted = [...filteredSymbols].sort((a, b) => {
        if (symbolSortOrder === 'asc') {
            return a.localeCompare(b);
        } else {
            return b.localeCompare(a);
        }
    });
    
    sorted.forEach(symbol => {
        const item = document.createElement('div');
        item.className = 'symbol-item';
        if (symbol === selectedSymbol) {
            item.classList.add('selected');
        }
        item.textContent = symbol;
        item.onclick = () => selectSymbol(symbol);
        symbolList.appendChild(item);
    });
    
    if (sorted.length === 0) {
        symbolList.innerHTML = '<div class="px-4 py-3 text-sm text-gray-500 text-center">검색 결과 없음</div>';
    }
}

function toggleSymbolDropdown() {
    const dropdown = document.getElementById('symbol-dropdown');
    dropdown.classList.toggle('hidden');
    
    if (!dropdown.classList.contains('hidden')) {
        document.getElementById('symbol-search').focus();
    }
}

function selectSymbol(symbol) {
    selectedSymbol = symbol;
    document.getElementById('symbol-search').value = symbol;
    
    const badge = document.getElementById('selected-symbol-badge');
    const badgeText = document.getElementById('selected-symbol-text');
    badgeText.textContent = symbol;
    badge.classList.remove('hidden');
    
    document.getElementById('symbol-dropdown').classList.add('hidden');
    fetchRecentSignals();
    displaySymbolList();
}

function selectSymbolFromTable(symbol) {
    selectSymbol(symbol);
}

function clearSymbolFilter() {
    selectedSymbol = '';
    document.getElementById('symbol-search').value = '';
    document.getElementById('selected-symbol-badge').classList.add('hidden');
    
    filteredSymbols = [...allSymbols];
    displaySymbolList();
    fetchRecentSignals();
}

function selectAllSymbols() {
    clearSymbolFilter();
}

function toggleSymbolSort() {
    symbolSortOrder = symbolSortOrder === 'asc' ? 'desc' : 'asc';
    const btn = document.getElementById('symbol-sort-btn');
    btn.textContent = symbolSortOrder === 'asc' ? 'A→Z' : 'Z→A';
    displaySymbolList();
}

function searchSymbols(query) {
    if (!query) {
        filteredSymbols = [...allSymbols];
    } else {
        const lowerQuery = query.toLowerCase();
        filteredSymbols = allSymbols.filter(symbol => 
            symbol.toLowerCase().includes(lowerQuery)
        );
    }
    displaySymbolList();
}

// ============================================================
// 전체 새로고침
// ============================================================

function refreshData() {
    fetchStats();
    fetchResultDistribution();
    fetchDailyProfit();
    fetchRecentSignals();
}

// ============================================================
// 이벤트 리스너
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('symbol-search');
    const limitFilter = document.getElementById('limit-filter');
    
    // 검색 입력 이벤트
    searchInput.addEventListener('input', (e) => {
        searchSymbols(e.target.value);
    });
    
    // 검색창 포커스 시 드롭다운 열기
    searchInput.addEventListener('focus', () => {
        document.getElementById('symbol-dropdown').classList.remove('hidden');
    });
    
    // 검색창 클릭 시 전체 텍스트 선택
    searchInput.addEventListener('click', () => {
        searchInput.select();
    });
    
    // 표시 개수 변경 이벤트
    limitFilter.addEventListener('change', fetchRecentSignals);
    
    // 초기 데이터 로드
    loadSymbols();
    refreshData();
    
    // 1분마다 자동 새로고침
    setInterval(() => {
        fetchStats();
        fetchRecentSignals();
    }, 60000);
});

// 외부 클릭 시 드롭다운 닫기
document.addEventListener('click', (event) => {
    const dropdown = document.getElementById('symbol-dropdown');
    const searchInput = document.getElementById('symbol-search');
    const dropdownBtn = document.getElementById('symbol-dropdown-btn');
    
    if (!dropdown.contains(event.target) && 
        event.target !== searchInput && 
        event.target !== dropdownBtn) {
        dropdown.classList.add('hidden');
    }
});
