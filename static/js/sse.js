/**
 * SSE 客户端封装模块
 */

class SSEClient {
  /**
   * 创建 SSE 客户端
   * @param {number} jobId - 任务 ID
   * @param {Object} callbacks - 回调函数集合
   * @param {Function} callbacks.onProgress - 进度更新回调
   * @param {Function} callbacks.onComplete - 完成回调
   * @param {Function} callbacks.onError - 错误回调
   */
  constructor(jobId, { onProgress, onComplete, onError } = {}) {
    this.jobId = jobId;
    this.onProgress = onProgress || (() => {});
    this.onComplete = onComplete || (() => {});
    this.onError = onError || (() => {});
    this.es = null;
    this.retries = 0;
    this.maxRetries = 5;
  }

  /** 连接到 SSE 端点 */
  connect() {
    if (this.es) this.disconnect();

    const url = `/api/events/${this.jobId}`;
    this.es = new EventSource(url);

    this.es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        this._handleEvent(data);
      } catch (err) {
        console.warn("SSE 解析错误:", err);
      }
    };

    this.es.onerror = () => {
      this.retries++;
      if (this.retries >= this.maxRetries) {
        this.disconnect();
        this.onError("SSE 连接失败，请刷新页面重试");
      }
    };

    this.es.onopen = () => {
      this.retries = 0;
    };
  }

  /** 断开 SSE 连接 */
  disconnect() {
    if (this.es) {
      this.es.close();
      this.es = null;
    }
  }

  /** 处理 SSE 事件 */
  _handleEvent(data) {
    if (data.type === "ping" || data.type === "connected") return;

    this.onProgress(data);

    if (data.status === "completed") {
      this.disconnect();
      this.onComplete(data);
    } else if (data.status === "failed") {
      this.disconnect();
      this.onError(data.error || "处理失败");
    }
  }
}
