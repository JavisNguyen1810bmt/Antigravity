import { useState, useRef } from 'react';
import DetailedProgress from './components/DetailedProgress';
import ExportTools from './components/ExportTools';
import TerminalLog from './components/TerminalLog';
import './App.css';

function App() {
  const [file, setFile] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [session, setSession] = useState(null); // Lưu thông tin session sau khi upload
  const [selectedParts, setSelectedParts] = useState([]);
  const [isStarted, setIsStarted] = useState(false);
  const [isDone, setIsDone] = useState(false);
  const [pdfFailed, setPdfFailed] = useState(false);
  const [downloadUrl, setDownloadUrl] = useState('');
  const [texUrl, setTexUrl] = useState('');
  const [isPdfReady, setIsPdfReady] = useState(false);
  const [logs, setLogs] = useState([]);
  const [activeTab, setActiveTab] = useState('progress');

  const fileInputRef = useRef(null);
  const ws = useRef(null);

  // Xử lý kéo thả File
  const handleDrop = (e) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileSelection(e.dataTransfer.files[0]);
    }
  };

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      handleFileSelection(e.target.files[0]);
    }
  };

  const handleFileSelection = (selectedFile) => {
    const validExtensions = ['.pdf', '.tex'];
    const fileName = selectedFile.name.toLowerCase();
    const isValid = validExtensions.some(ext => fileName.endsWith(ext));

    if (!isValid) {
      alert('Vui lòng chọn file PDF hoặc LaTeX (.pdf, .tex)!');
      return;
    }

    setFile(selectedFile);
    setSession(null);
    setIsDone(false);
    setPdfFailed(false);
    setDownloadUrl('');
    setIsPdfReady(false);
    setTexUrl('');
    setLogs([]);
  };

  // Upload file lên Backend để phân tích và chia Part
  const handleUpload = async () => {
    if (!file) return;

    setIsUploading(true);
    const formData = new FormData();
    formData.append('file', file);
    formData.append('chunk_size', 10); // Mỗi part 10 trang

    try {
      const res = await fetch('http://localhost:8000/api/upload-guest-pdf', {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || 'Lỗi khi tải file lên backend');
      }

      const data = await res.json();
      setSession(data);
      // Mặc định chọn tất cả các part
      setSelectedParts(data.parts.map(p => p.id));
    } catch (err) {
      alert(`Upload thất bại: ${err.message}`);
    } finally {
      setIsUploading(false);
    }
  };

  // Bắt đầu quá trình dịch qua WebSocket
  const handleStartTranslation = () => {
    if (!session) return;
    if (selectedParts.length === 0) {
      alert('Vui lòng chọn ít nhất 1 phần để dịch!');
      return;
    }

    setIsStarted(true);
    setIsDone(false);
    setPdfFailed(false);
    setIsPdfReady(false);
    setLogs([]);

    const wsUrl = new URL(`ws://localhost:8000/ws/translate-guest/${session.session_id}`);
    wsUrl.searchParams.append('parts', selectedParts.join(','));

    ws.current = new WebSocket(wsUrl.toString());

    ws.current.onmessage = (event) => {
      const message = JSON.parse(event.data);

      if (message.type === 'log') {
        setLogs(prev => [...prev, { time: new Date().toLocaleTimeString(), text: message.message }]);
      } else if (message.type === 'log_append') {
        setLogs(prev => {
          if (prev.length === 0) {
            return [{ time: new Date().toLocaleTimeString(), text: message.text || '' }];
          }
          const last = prev[prev.length - 1];
          return [
            ...prev.slice(0, -1),
            { ...last, text: last.text + (message.text || '') }
          ];
        });
      } else if (message.type === 'status') {
        setSession(prev => {
          if (!prev) return prev;
          const updatedParts = prev.parts.map(p =>
            p.id === message.part ? { ...p, status: message.status } : p
          );
          return { ...prev, parts: updatedParts };
        });
      } else if (message.type === 'done') {
        setIsDone(true);
        setIsStarted(false);
        setPdfFailed(Boolean(message.pdf_failed));
        setIsPdfReady(!message.pdf_failed);
        if (message.download_url) {
          setDownloadUrl(`http://localhost:8000${message.download_url}`);
        }
        if (message.tex_url) {
          setTexUrl(`http://localhost:8000${message.tex_url}`);
        }
      }
    };

    ws.current.onerror = () => {
      setLogs(prev => [...prev, { time: new Date().toLocaleTimeString(), text: '[Error] WebSocket Connection Error' }]);
      setIsStarted(false);
    };
  };

  return (
    <div className="app-container">
      <header style={{ textAlign: 'center', marginBottom: '2rem' }}>
        <h1>Antigravity Document Translator</h1>
        <p style={{ color: '#94a3b8' }}>Dịch tự động tài liệu PDF / LaTeX sang mã nguồn LaTeX & PDF hoàn chỉnh</p>
      </header>

      <main style={{ maxWidth: '800px', margin: '0 auto' }}>
        {/* Khung Kéo & Thả File */}
        <div
          className="glass-panel"
          style={{
            border: '2px dashed rgba(255, 255, 255, 0.2)',
            borderRadius: '1rem',
            padding: '2.5rem',
            textAlign: 'center',
            cursor: 'pointer',
            transition: 'border 0.3s ease',
            marginBottom: '1.5rem'
          }}
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            type="file"
            ref={fileInputRef}
            onChange={handleFileChange}
            accept=".pdf,.tex"
            style={{ display: 'none' }}
          />

          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2" style={{ marginBottom: '1rem' }}>
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="17 8 12 3 7 8"></polyline>
            <line x1="12" y1="3" x2="12" y2="15"></line>
          </svg>

          {file ? (
            <div>
              <h3 style={{ color: '#4ade80', margin: '0.5rem 0' }}>📄 {file.name}</h3>
              <p style={{ color: '#94a3b8', fontSize: '0.85rem' }}>{(file.size / (1024 * 1024)).toFixed(2)} MB</p>
            </div>
          ) : (
            <div>
              <h3 style={{ margin: '0.5rem 0' }}>Kéo & Thả file PDF hoặc LaTeX vào đây</h3>
              <p style={{ color: '#94a3b8', fontSize: '0.9rem' }}>Hoặc nhấp để chọn file từ máy tính của bạn (.pdf, .tex)</p>
            </div>
          )}
        </div>

        {/* Nút Phân Tích File */}
        {file && !session && (
          <button
            className="btn-start"
            onClick={handleUpload}
            disabled={isUploading}
            style={{ width: '100%', padding: '0.8rem', fontSize: '1rem', marginBottom: '1.5rem' }}
          >
            {isUploading ? 'Đang phân tích file...' : 'Tải lên & Phân tích cấu trúc'}
          </button>
        )}

        {/* Sau khi Phân tích xong - Hiển thị Danh sách Parts & Control */}
        {session && (
          <div className="glass-panel" style={{ padding: '1.5rem', borderRadius: '1rem', marginBottom: '1.5rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
              <div>
                <h3 style={{ margin: 0 }}>{session.filename}</h3>
                <span style={{ fontSize: '0.85rem', color: '#94a3b8' }}>Tổng số trang: {session.total_pages} trang</span>
              </div>

              {isDone && (
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                {/* Nút tải PDF */}
                {isPdfReady ? (
                  <a
                    href={downloadUrl}
                    download
                    className="btn-start"
                    style={{ background: '#10b981', textDecoration: 'none' }}
                  >
                    📄 Tải file PDF
                  </a>
                ) : (
                  <span
                    className="btn-start"
                    style={{ background: '#7f1d1d', cursor: 'not-allowed' }}
                    title="XeLaTeX chưa tạo được PDF"
                  >
                    ⚠️ PDF chưa sẵn sàng
                  </span>
                )}

                {/* Nút tải mã nguồn TeX */}
                {texUrl && (
                  <a
                    href={texUrl}
                    download
                    className="btn-start"
                    style={{ background: '#4b5563', textDecoration: 'none' }}
                  >
                    🛠️ Tải file LaTeX (.tex)
                  </a>
                )}
              </div>
            )}
              {isDone && pdfFailed && (
                <p style={{ color: '#fca5a5', margin: '0.5rem 0' }}>
                  XeLaTeX không tạo được PDF. Xem log bên dưới để biết lỗi chi tiết.
                </p>
              )}
              <button
                className="btn-start"
                onClick={handleStartTranslation}
                disabled={isStarted}
              >
                {isStarted ? 'Đang dịch...' : 'Bắt đầu Dịch'}
              </button>
            </div>

            {/* Bảng chọn Part để dịch */}
            {!isStarted && !isDone && (
              <div style={{ background: 'rgba(255,255,255,0.05)', padding: '1rem', borderRadius: '0.5rem', marginBottom: '1rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
                  <h4 style={{ margin: 0 }}>Chọn các phần cần dịch:</h4>
                  <label style={{ fontSize: '0.85rem', color: '#3b82f6', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={selectedParts.length === session.parts.length}
                      onChange={(e) => {
                        if (e.target.checked) setSelectedParts(session.parts.map(p => p.id));
                        else setSelectedParts([]);
                      }}
                    /> Chọn tất cả
                  </label>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                  {session.parts.map(p => (
                    <label key={p.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.9rem', cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        checked={selectedParts.includes(p.id)}
                        onChange={(e) => {
                          if (e.target.checked) setSelectedParts(prev => [...prev, p.id]);
                          else setSelectedParts(prev => prev.filter(id => id !== p.id));
                        }}
                      />
                      {p.name}
                    </label>
                  ))}
                </div>
              </div>
            )}

            {/* Tab Tiến độ và Log */}
            {(isStarted || isDone || logs.length > 0) && (
              <div style={{ marginTop: '1rem' }}>
                <div style={{ display: 'flex', gap: '1rem', borderBottom: '1px solid rgba(255,255,255,0.1)', marginBottom: '1rem' }}>
                  <button
                    onClick={() => setActiveTab('progress')}
                    style={{
                      background: 'none', border: 'none', padding: '0.5rem 1rem', cursor: 'pointer',
                      color: activeTab === 'progress' ? '#3b82f6' : '#94a3b8',
                      borderBottom: activeTab === 'progress' ? '2px solid #3b82f6' : '2px solid transparent'
                    }}
                  >
                    Tiến độ các Part
                  </button>
                  <button
                    onClick={() => setActiveTab('logs')}
                    style={{
                      background: 'none', border: 'none', padding: '0.5rem 1rem', cursor: 'pointer',
                      color: activeTab === 'logs' ? '#3b82f6' : '#94a3b8',
                      borderBottom: activeTab === 'logs' ? '2px solid #3b82f6' : '2px solid transparent'
                    }}
                  >
                    Log thời gian thực
                  </button>
                </div>

                {activeTab === 'progress' && <DetailedProgress parts={session.parts} />}
                {activeTab === 'logs' && <TerminalLog logs={logs} />}
              </div>
            )}
          </div>
        )}
        {session && isDone && (
          <ExportTools sessionId={session.session_id} />
        )}
      </main>
    </div>
  );
}

export default App;