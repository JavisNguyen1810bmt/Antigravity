import React, { useState, useEffect, useRef } from 'react';
import DetailedProgress from './DetailedProgress';
import TerminalLog from './TerminalLog';

function ChapterCard({ chapter }) {
  const [isStarted, setIsStarted] = useState(false);
  const [isRunComplete, setIsRunComplete] = useState(false);
  const [plan, setPlan] = useState(null);
  const [logs, setLogs] = useState([]);
  const [selectedParts, setSelectedParts] = useState([]);
  const [isExpanded, setIsExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState('progress');
  const ws = useRef(null);

  // Lấy cấu trúc các phần (parts) khi component mount hoặc expand
  const fetchPlan = async () => {
    try {
      const res = await fetch(`http://localhost:8000/api/chapters/${chapter.id}/plan`);
      const data = await res.json();
      setPlan(data);
    } catch (err) {
      console.error("Lỗi khi tải danh sách parts:", err);
    }
  };

  useEffect(() => {
    fetchPlan();
  }, [chapter.id]);

  const toggleExpand = () => {
    if (!isExpanded && !plan) {
      fetchPlan();
    }
    setIsExpanded(!isExpanded);
  };

  // Chọn hoặc bỏ chọn tất cả các Part
  const handleSelectAll = (e) => {
    if (e.target.checked && plan?.parts) {
      setSelectedParts(plan.parts.map(p => p.id));
    } else {
      setSelectedParts([]);
    }
  };

  const handleStart = async () => {
    if (selectedParts.length === 0) {
      alert("Vui lòng chọn ít nhất 1 phần cần dịch!");
      return;
    }

    setIsStarted(true);
    setIsRunComplete(false);
    setLogs([]);

    // Khởi tạo kết nối WebSocket (Đã loại bỏ tham số api_key)
    const url = new URL(`ws://localhost:8000/ws/translate/${chapter.id}`);
    url.searchParams.append('parts', selectedParts.join(','));

    ws.current = new WebSocket(url.toString());

    ws.current.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === 'log') {
        setLogs(prev => [...prev, { time: new Date().toLocaleTimeString(), text: message.message }]);
      } else if (message.type === 'log_append') {
        setLogs(prev => {
          if (prev.length === 0) return prev;
          const last = prev[prev.length - 1];
          return [
            ...prev.slice(0, -1),
            { ...last, text: last.text + (message.text || '') }
          ];
        });
      } else if (message.type === 'status') {
        setPlan(prevPlan => {
          if (!prevPlan) return prevPlan;
          const newParts = prevPlan.parts.map(p =>
            p.id === message.part ? { ...p, status: message.status } : p
          );
          return { ...prevPlan, parts: newParts };
        });
      } else if (message.type === 'done') {
        setIsRunComplete(true);
        setIsStarted(false);
      }
    };

    ws.current.onerror = () => {
      setLogs(prev => [...prev, { time: new Date().toLocaleTimeString(), text: `[Error] WebSocket Connection Error` }]);
      setIsStarted(false);
    };
  };

  const isFinished = plan && plan.parts && plan.parts.length > 0 && plan.parts.every(p => p.status === 'completed');

  return (
    <div className="chapter-card glass-panel" style={{ transition: 'all 0.3s ease' }}>
      <div 
        className="chapter-header" 
        onClick={toggleExpand}
        style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <svg 
            width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" 
            style={{ transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.3s ease' }}
          >
            <polyline points="6 9 12 15 18 9"></polyline>
          </svg>
          <h2 className="chapter-title" style={{ margin: 0 }}>{chapter.name}</h2>
        </div>
        
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
          <a 
            href={`http://localhost:8000/original/Chapter_${chapter.id.split('_')[1]}.pdf`}
            target="_blank" 
            rel="noreferrer" 
            className="btn-start" 
            style={{ textDecoration: 'none', background: 'rgba(255, 255, 255, 0.1)', color: 'var(--text-color)', display: 'flex', alignItems: 'center', gap: '0.5rem', border: '1px solid var(--border-color)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
            Original PDF
          </a>

          {plan?.parts?.find(p => p.id === 17)?.status === 'completed' && (
            <a 
              href={`http://localhost:8000/output/${chapter.id === 'chapter_05' ? 'chapter_05_standalone.pdf' : 'main.pdf'}`}
              target="_blank" 
              rel="noreferrer" 
              className="btn-start" 
              style={{ textDecoration: 'none', background: 'var(--success)', display: 'flex', alignItems: 'center', gap: '0.5rem' }}
              onClick={(e) => e.stopPropagation()}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
              Translated PDF
            </a>
          )}

          {chapter.status === 'completed' && isFinished && (
            <span style={{color: 'var(--success)', fontWeight: '600', padding: '0.5rem 1rem', background: 'rgba(16, 185, 129, 0.1)', borderRadius: '0.5rem', display: 'flex', alignItems: 'center'}} onClick={(e) => e.stopPropagation()}>
              ✓ All Done
            </span>
          )}
          <button 
            className="btn-start" 
            onClick={(e) => { e.stopPropagation(); handleStart(); }} 
            disabled={isStarted}
          >
            {isStarted ? "In Progress..." : "Start Translation"}
          </button>
        </div>
      </div>
      
      {isExpanded && plan && plan.parts && plan.parts.length > 0 && (
        <div style={{ marginTop: '1rem', animation: 'fadeIn 0.3s ease' }}>
          
          {/* Checkboxes chọn Part dịch */}
          {!isStarted && (
            <div style={{ background: 'rgba(255,255,255,0.05)', padding: '1rem', borderRadius: '0.5rem', marginBottom: '1rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.8rem' }}>
                <h4 style={{ margin: 0, color: 'var(--text-color)' }}>Select Parts to Translate:</h4>
                <label style={{ fontSize: '0.85rem', color: '#3b82f6', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <input
                    type="checkbox"
                    onChange={handleSelectAll}
                    checked={plan.parts.length > 0 && selectedParts.length === plan.parts.length}
                  />
                  Select All
                </label>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                {plan.parts.map(p => (
                  <label key={p.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: p.status === 'completed' ? '#4ade80' : 'var(--text-color)', fontSize: '0.9rem', cursor: 'pointer' }}>
                    <input 
                      type="checkbox" 
                      checked={selectedParts.includes(p.id)}
                      style={{ cursor: 'pointer' }}
                      onChange={(e) => {
                        if (e.target.checked) {
                          setSelectedParts(prev => [...prev, p.id]);
                        } else {
                          setSelectedParts(prev => prev.filter(id => id !== p.id));
                        }
                      }}
                    />
                    {p.name} {p.status === 'completed' && '(Đã hoàn thành)'}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Tab Progress và Logs */}
          <div style={{ marginTop: '1.5rem' }}>
            <div style={{ display: 'flex', gap: '1rem', borderBottom: '1px solid var(--border-color)', marginBottom: '1rem' }}>
              <button 
                onClick={() => setActiveTab('progress')}
                style={{ 
                  background: 'none', border: 'none', padding: '0.5rem 1rem', cursor: 'pointer',
                  color: activeTab === 'progress' ? '#3b82f6' : '#94a3b8',
                  borderBottom: activeTab === 'progress' ? '2px solid #3b82f6' : '2px solid transparent',
                  fontWeight: activeTab === 'progress' ? '600' : '400'
                }}
              >
                Pipeline Progress
              </button>
              <button 
                onClick={() => setActiveTab('logs')}
                style={{ 
                  background: 'none', border: 'none', padding: '0.5rem 1rem', cursor: 'pointer',
                  color: activeTab === 'logs' ? '#3b82f6' : '#94a3b8',
                  borderBottom: activeTab === 'logs' ? '2px solid #3b82f6' : '2px solid transparent',
                  fontWeight: activeTab === 'logs' ? '600' : '400'
                }}
              >
                Translation Logs
              </button>
            </div>

            {activeTab === 'progress' && (
              <div className="progress-section">
                <DetailedProgress parts={plan.parts} />
              </div>
            )}

            {activeTab === 'logs' && (
              <div className="terminal-section">
                <TerminalLog logs={logs} />
                {logs.length === 0 && <div style={{color: '#64748b', fontSize: '0.9rem', fontStyle: 'italic', padding: '1rem'}}>No logs available yet. Click Start Translation to see live logs.</div>}
              </div>
            )}
          </div>
        </div>
      )}
      
      {isExpanded && plan && !isStarted && (!plan.parts || plan.parts.length === 0) && (
        <div style={{ marginTop: '1rem', padding: '1rem', color: 'var(--text-muted)', textAlign: 'center' }}>
          No parts available for this chapter yet.
        </div>
      )}
    </div>
  );
}

export default ChapterCard;