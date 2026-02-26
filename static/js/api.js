/**
 * API 调用封装模块
 */

const API_BASE = "/api";

const api = {
  /**
   * 创建新的处理任务
   * @param {Object} params - 任务参数
   * @returns {Promise<Object>} 任务对象
   */
  async createJob(params) {
    const res = await fetch(`${API_BASE}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "创建任务失败");
    }
    return res.json();
  },

  /**
   * 获取单个任务详情
   * @param {number} jobId - 任务 ID
   * @returns {Promise<Object>} 任务对象
   */
  async getJob(jobId) {
    const res = await fetch(`${API_BASE}/jobs/${jobId}`);
    if (!res.ok) throw new Error(`任务 ${jobId} 不存在`);
    return res.json();
  },

  /**
   * 获取任务列表
   * @param {number} limit - 每页数量
   * @param {number} offset - 偏移量
   * @returns {Promise<Array>} 任务列表
   */
  async listJobs(limit = 20, offset = 0) {
    const res = await fetch(`${API_BASE}/jobs?limit=${limit}&offset=${offset}`);
    if (!res.ok) throw new Error("获取任务列表失败");
    return res.json();
  },

  /**
   * 删除任务
   * @param {number} jobId - 任务 ID
   */
  async deleteJob(jobId) {
    const res = await fetch(`${API_BASE}/jobs/${jobId}`, { method: "DELETE" });
    if (!res.ok && res.status !== 204) throw new Error("删除任务失败");
  },

  /**
   * 获取投影片下载 URL
   * @param {number} jobId - 任务 ID
   * @returns {string} 下载 URL
   */
  getDownloadUrl(jobId) {
    return `${API_BASE}/jobs/${jobId}/download`;
  },
};
