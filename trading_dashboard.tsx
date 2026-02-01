import React, { useState, useEffect } from 'react';
import { RefreshCw, TrendingUp, TrendingDown, AlertCircle, BarChart3, Filter } from 'lucide-react';

const API_BASE = 'http://localhost:8000';

const Dashboard = () => {
  const [signals, setSignals] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState({
    symbol: '',
    tf: '',
    strategy: '',
    direction: '',
    hours: 24
  });
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchData = async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      Object.entries(filters).forEach(([key, value]) => {
        if (value) params.append(key, value);
      });
      
      const [signalsRes, statsRes] = await Promise.all([
        fetch(`${API_BASE}/api/signals?${params}`),
        fetch(`${API_BASE}/api/stats?hours=${filters.hours}`)
      ]);
      
      const signalsData = await signalsRes.json();
      const statsData = await statsRes.json();
      
      setSignals(signalsData);
      setStats(statsData);
    } catch (error) {
      console.error('Failed to fetch data:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, [filters]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(fetchData, 30000); // 30초마다 갱신
    return () => clearInterval(interval);
  }, [autoRefresh, filters]);

  const getBiasColor = (bias) => {
    if (bias === 'bull') return 'text-green-600 bg-green-50';
    if (bias === 'bear') return 'text-red-600 bg-red-50';
    return 'text-gray-600 bg-gray-50';
  };

  const getOutcomeColor = (result) => {
    if (result === 'profit') return 'text-green-700 bg-green-100';
    if (result === 'loss') return 'text-red-700 bg-red-100';
    return 'text-yellow-700 bg-yellow-100';
  };

  const formatTime = (dateStr) => {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    const kst = new Date(date.getTime() + (9 * 60 * 60 * 1000));
    return kst.toLocaleString('ko-KR', { 
      month: '2-digit', 
      day: '2-digit', 
      hour: '2-digit', 
      minute: '2-digit' 
    });
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <div className="flex items-center justify-between mb-4">
            <h1 className="text-4xl font-bold text-slate-800 flex items-center gap-3">
              <BarChart3 className="text-blue-600" size={40} />
              실시간 트레이딩 시그널
            </h1>
            <div className="flex gap-2">
              <button
                onClick={() => setAutoRefresh(!autoRefresh)}
                className={`px-4 py-2 rounded-lg font-medium transition ${
                  autoRefresh 
                    ? 'bg-green-500 text-white' 
                    : 'bg-gray-300 text-gray-700'
                }`}
              >
                {autoRefresh ? '자동갱신 ON' : '자동갱신 OFF'}
              </button>
              <button
                onClick={fetchData}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition flex items-center gap-2"
              >
                <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
                새로고침
              </button>
            </div>
          </div>

          {/* Stats */}
          {stats && (
            <div className="grid grid-cols-2 md:grid-cols-6 gap-4">
              <div className="bg-white p-4 rounded-xl shadow-sm border border-slate-200">
                <div className="text-sm text-slate-600 mb-1">총 시그널</div>
                <div className="text-2xl font-bold text-slate-800">{stats.total_signals}</div>
              </div>
              <div className="bg-white p-4 rounded-xl shadow-sm border border-slate-200">
                <div className="text-sm text-slate-600 mb-1">승률</div>
                <div className="text-2xl font-bold text-blue-600">
                  {stats.win_rate ? `${(stats.win_rate * 100).toFixed(1)}%` : '-'}
                </div>
              </div>
              <div className="bg-white p-4 rounded-xl shadow-sm border border-slate-200">
                <div className="text-sm text-slate-600 mb-1">평균 R</div>
                <div className="text-2xl font-bold text-purple-600">
                  {stats.avg_r_multiple ? stats.avg_r_multiple.toFixed(2) : '-'}
                </div>
              </div>
              <div className="bg-white p-4 rounded-xl shadow-sm border border-slate-200">
                <div className="text-sm text-green-600 mb-1">수익</div>
                <div className="text-2xl font-bold text-green-700">{stats.total_profit_signals}</div>
              </div>
              <div className="bg-white p-4 rounded-xl shadow-sm border border-slate-200">
                <div className="text-sm text-red-600 mb-1">손실</div>
                <div className="text-2xl font-bold text-red-700">{stats.total_loss_signals}</div>
              </div>
              <div className="bg-white p-4 rounded-xl shadow-sm border border-slate-200">
                <div className="text-sm text-slate-600 mb-1">최고 전략</div>
                <div className="text-lg font-bold text-amber-600 truncate">
                  {stats.best_strategy || '-'}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Filters */}
        <div className="bg-white p-4 rounded-xl shadow-sm border border-slate-200 mb-6">
          <div className="flex items-center gap-2 mb-3">
            <Filter size={20} className="text-slate-600" />
            <h2 className="font-semibold text-slate-700">필터</h2>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <input
              type="text"
              placeholder="심볼 (예: BTCUSDT)"
              value={filters.symbol}
              onChange={(e) => setFilters({...filters, symbol: e.target.value.toUpperCase()})}
              className="px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
            <select
              value={filters.tf}
              onChange={(e) => setFilters({...filters, tf: e.target.value})}
              className="px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500"
            >
              <option value="">모든 타임프레임</option>
              <option value="30m">30m</option>
              <option value="4h">4h</option>
              <option value="1d">1d</option>
            </select>
            <input
              type="text"
              placeholder="전략"
              value={filters.strategy}
              onChange={(e) => setFilters({...filters, strategy: e.target.value})}
              className="px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500"
            />
            <select
              value={filters.direction}
              onChange={(e) => setFilters({...filters, direction: e.target.value})}
              className="px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500"
            >
              <option value="">모든 방향</option>
              <option value="long">Long</option>
              <option value="short">Short</option>
            </select>
            <select
              value={filters.hours}
              onChange={(e) => setFilters({...filters, hours: parseInt(e.target.value)})}
              className="px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500"
            >
              <option value="6">최근 6시간</option>
              <option value="24">최근 24시간</option>
              <option value="72">최근 3일</option>
              <option value="168">최근 7일</option>
            </select>
          </div>
        </div>

        {/* Signals List */}
        <div className="space-y-4">
          {loading && signals.length === 0 ? (
            <div className="text-center py-12 text-slate-500">
              <RefreshCw className="animate-spin mx-auto mb-2" size={32} />
              데이터 로딩 중...
            </div>
          ) : signals.length === 0 ? (
            <div className="text-center py-12 text-slate-500">
              <AlertCircle className="mx-auto mb-2" size={32} />
              시그널이 없습니다
            </div>
          ) : (
            signals.map((signal) => (
              <div
                key={signal.signal_id}
                className="bg-white rounded-xl shadow-sm border border-slate-200 p-5 hover:shadow-md transition"
              >
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className={`px-3 py-1 rounded-full text-sm font-semibold ${
                      signal.direction === 'long' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                    }`}>
                      {signal.direction === 'long' ? <TrendingUp size={16} className="inline mr-1" /> : <TrendingDown size={16} className="inline mr-1" />}
                      {signal.direction?.toUpperCase()}
                    </div>
                    <h3 className="text-xl font-bold text-slate-800">{signal.symbol}</h3>
                    <span className="px-2 py-1 bg-slate-100 text-slate-600 text-xs font-medium rounded">
                      {signal.tf}
                    </span>
                    <span className="px-2 py-1 bg-blue-100 text-blue-700 text-xs font-medium rounded">
                      {signal.strategy}
                    </span>
                  </div>
                  <div className="text-right">
                    <div className="text-xs text-slate-500">발생시간</div>
                    <div className="text-sm font-medium text-slate-700">{formatTime(signal.open_time)}</div>
                  </div>
                </div>

                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4 text-sm">
                  <div>
                    <div className="text-slate-500 mb-1">진입가</div>
                    <div className="font-semibold text-slate-800">{signal.entry?.toFixed(4) || '-'}</div>
                  </div>
                  <div>
                    <div className="text-slate-500 mb-1">손절가</div>
                    <div className="font-semibold text-red-600">{signal.stop_loss?.toFixed(4) || '-'}</div>
                  </div>
                  <div>
                    <div className="text-slate-500 mb-1">목표가 1</div>
                    <div className="font-semibold text-green-600">{signal.take_profit_1?.toFixed(4) || '-'}</div>
                  </div>
                  <div>
                    <div className="text-slate-500 mb-1">점수</div>
                    <div className="font-semibold text-purple-600">{signal.score?.toFixed(2) || '-'}</div>
                  </div>
                </div>

                {/* LLM Analysis */}
                {signal.llm_headline && (
                  <div className="border-t border-slate-200 pt-4 mt-4">
                    <div className="flex items-center gap-2 mb-2">
                      <div className={`px-2 py-1 rounded text-xs font-semibold ${getBiasColor(signal.llm_bias)}`}>
                        {signal.llm_bias?.toUpperCase() || 'NEUTRAL'}
                      </div>
                      <div className="text-xs text-slate-500">
                        신뢰도: {signal.llm_confidence ? `${(signal.llm_confidence * 100).toFixed(0)}%` : '-'}
                      </div>
                    </div>
                    <div className="font-medium text-slate-800 mb-2">{signal.llm_headline}</div>
                    
                    {signal.llm_evidence && signal.llm_evidence.length > 0 && (
                      <div className="mb-2">
                        <div className="text-xs font-semibold text-slate-600 mb-1">근거:</div>
                        <ul className="text-xs text-slate-700 space-y-1">
                          {signal.llm_evidence.slice(0, 3).map((ev, i) => (
                            <li key={i} className="pl-3 border-l-2 border-blue-300">• {ev}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    
                    {signal.llm_risks && signal.llm_risks.length > 0 && (
                      <div>
                        <div className="text-xs font-semibold text-slate-600 mb-1">리스크:</div>
                        <ul className="text-xs text-red-700 space-y-1">
                          {signal.llm_risks.slice(0, 2).map((risk, i) => (
                            <li key={i} className="pl-3 border-l-2 border-red-300">⚠ {risk}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}

                {/* Outcome */}
                {signal.outcome_result && (
                  <div className="border-t border-slate-200 pt-3 mt-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <span className={`px-3 py-1 rounded-full text-sm font-semibold ${getOutcomeColor(signal.outcome_result)}`}>
                          {signal.outcome_result === 'profit' ? '✓ 수익' : signal.outcome_result === 'loss' ? '✗ 손실' : '진행중'}
                        </span>
                        {signal.outcome_r_multiple && (
                          <span className={`text-sm font-bold ${signal.outcome_r_multiple > 0 ? 'text-green-600' : 'text-red-600'}`}>
                            R: {signal.outcome_r_multiple.toFixed(2)}
                          </span>
                        )}
                      </div>
                      {signal.outcome_exit_time && (
                        <div className="text-xs text-slate-500">
                          종료: {formatTime(signal.outcome_exit_time)}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};

export default Dashboard;