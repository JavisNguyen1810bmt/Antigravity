import { useEffect, useState } from 'react';
import PropTypes from 'prop-types';
import './ExportTools.css';

const API_BASE = 'http://localhost:8000';

async function responseError(response) {
  const payload = await response.json().catch(() => ({}));
  const error = new Error(payload.detail || `Yêu cầu thất bại (${response.status}).`);
  error.status = response.status;
  return error;
}

function SlidePreview({ deck }) {
  const slides = [
    { title: deck.title, bullets: ['Bài giảng được tạo từ nội dung bản dịch.'] },
    { title: 'Mục tiêu bài học', bullets: deck.objectives },
    ...deck.slides,
    { title: 'Tổng kết', bullets: deck.summary },
  ];

  return (
    <div className="export-slide-grid">
      {slides.map((slide, index) => (
        <article className="export-slide" key={`${index}-${slide.title}`}>
          <span className="export-slide-number">SLIDE {String(index + 1).padStart(2, '0')}</span>
          <h4>{slide.title}</h4>
          <ul>
            {slide.bullets.map((bullet, bulletIndex) => (
              <li key={`${bulletIndex}-${bullet}`}>{bullet}</li>
            ))}
          </ul>
        </article>
      ))}
    </div>
  );
}

SlidePreview.propTypes = {
  deck: PropTypes.shape({
    title: PropTypes.string.isRequired,
    objectives: PropTypes.arrayOf(PropTypes.string).isRequired,
    slides: PropTypes.arrayOf(PropTypes.shape({
      title: PropTypes.string.isRequired,
      bullets: PropTypes.arrayOf(PropTypes.string).isRequired,
    })).isRequired,
    summary: PropTypes.arrayOf(PropTypes.string).isRequired,
  }).isRequired,
};

function ExportTools({ sessionId }) {
  const [deck, setDeck] = useState(null);
  const [isGeneratingDeck, setIsGeneratingDeck] = useState(false);
  const [deckError, setDeckError] = useState('');
  const [deckRetryable, setDeckRetryable] = useState(false);
  const [videoJob, setVideoJob] = useState(null);
  const [videoError, setVideoError] = useState('');
  const [videoRetryable, setVideoRetryable] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE}/api/exports/${sessionId}/slides`, { signal: controller.signal })
      .then((response) => {
        if (response.status === 404) return null;
        if (!response.ok) throw new Error(`Không tải được bài giảng (${response.status}).`);
        return response.json();
      })
      .then((data) => {
        if (data) setDeck(data);
      })
      .catch((error) => {
        if (error.name !== 'AbortError') setDeckError(error.message);
      });
    return () => controller.abort();
  }, [sessionId]);

  useEffect(() => {
    if (!videoJob?.job_id || ['completed', 'failed'].includes(videoJob.status)) return undefined;
    let active = true;
    let timer;

    const poll = async () => {
      try {
        const response = await fetch(
          `${API_BASE}/api/exports/${sessionId}/video/${videoJob.job_id}`,
        );
        if (!response.ok) throw await responseError(response);
        const data = await response.json();
        if (!active) return;
        setVideoJob(data);
        if (data.status === 'failed') {
          setVideoRetryable(Boolean(data.retryable));
        }
        if (!['completed', 'failed'].includes(data.status)) {
          timer = window.setTimeout(poll, 2000);
        }
      } catch (error) {
        if (active) {
          setVideoError(error.message);
          setVideoJob((current) => ({ ...current, status: 'failed', retryable: false }));
        }
      }
    };

    timer = window.setTimeout(poll, 1000);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [sessionId, videoJob?.job_id, videoJob?.status]);

  const createDeck = async () => {
    setIsGeneratingDeck(true);
    setDeckError('');
    setDeckRetryable(false);
    try {
      const response = await fetch(`${API_BASE}/api/exports/${sessionId}/slides`, {
        method: 'POST',
      });
      if (!response.ok) throw await responseError(response);
      setDeck(await response.json());
    } catch (error) {
      setDeckError(error.message);
      setDeckRetryable(error.status === 503);
    } finally {
      setIsGeneratingDeck(false);
    }
  };

  const createVideo = async () => {
    setVideoError('');
    setVideoRetryable(false);
    setVideoJob(null);
    try {
      const response = await fetch(`${API_BASE}/api/exports/${sessionId}/video`, {
        method: 'POST',
      });
      if (!response.ok) throw await responseError(response);
      setVideoJob(await response.json());
    } catch (error) {
      setVideoError(error.message);
      setVideoRetryable(error.status === 503);
    }
  };

  return (
    <section className="export-tools">
      <div className="export-tools-header">
        <div>
          <h3>Học liệu từ bản dịch</h3>
          <p>Tạo bài giảng và video tóm tắt độc lập với kết quả dịch.</p>
        </div>
      </div>

      <div className="export-action-row">
        <button
          className="btn-start export-button"
          type="button"
          onClick={createDeck}
          disabled={isGeneratingDeck}
        >
          {isGeneratingDeck ? 'Đang tạo slide...' : deckRetryable ? 'Thử lại tạo slide' : 'Tạo Slide Bài Giảng'}
        </button>
        <button
          className="btn-start export-button export-video-button"
          type="button"
          onClick={createVideo}
          disabled={Boolean(videoJob && !['completed', 'failed'].includes(videoJob.status))}
        >
          {videoJob && !['completed', 'failed'].includes(videoJob.status)
            ? 'Đang tạo video...'
            : videoRetryable || videoJob?.retryable
              ? 'Thử lại tạo video'
              : 'Tạo Video Tóm Tắt'}
        </button>
      </div>

      {deckError && (
        <p className="export-error" role="alert">
          {deckError}
          {deckRetryable && ' Bạn có thể nhấn “Thử lại tạo slide” sau ít phút.'}
        </p>
      )}
      {deck && (
        <div className="export-preview">
          <div className="export-preview-heading">
            <h4>Xem trước bài giảng</h4>
            <div className="export-downloads">
              <a href={`${API_BASE}/api/exports/${sessionId}/slides.pptx`}>Tải .pptx</a>
              <a href={`${API_BASE}/api/exports/${sessionId}/slides.pdf`}>Tải .pdf</a>
            </div>
          </div>
          <SlidePreview deck={deck} />
        </div>
      )}

      {videoJob && !['completed', 'failed'].includes(videoJob.status) && (
        <div className="export-video-progress" aria-live="polite">
          <div className="export-progress-label">
            <span>{videoJob.message || 'Đang xử lý video...'}</span>
            <span>{videoJob.progress || 0}%</span>
          </div>
          <progress max="100" value={videoJob.progress || 0} />
        </div>
      )}
      {videoError && (
        <p className="export-error" role="alert">
          {videoError}
          {videoRetryable && ' Bạn có thể nhấn “Thử lại tạo video” sau ít phút.'}
        </p>
      )}
      {videoJob?.status === 'failed' && (
        <p className="export-error" role="alert">
          {videoJob.message}
          {videoJob.retryable && ' Bạn có thể nhấn “Thử lại tạo video” sau ít phút.'}
        </p>
      )}
      {videoJob?.status === 'completed' && (
        <div className="export-video-ready">
          <p>
            Video sẵn sàng
            {videoJob.duration_seconds ? ` (${Math.round(videoJob.duration_seconds)} giây)` : ''}.
          </p>
          <div className="export-downloads">
            <a href={`${API_BASE}${videoJob.download_url}`} download="video-tom-tat.mp4">
              Tải video .mp4
            </a>
            <a href={`${API_BASE}${videoJob.subtitles_url}`}>Tải phụ đề .srt</a>
          </div>
        </div>
      )}
    </section>
  );
}

ExportTools.propTypes = {
  sessionId: PropTypes.string.isRequired,
};

export default ExportTools;
