const {
  request, ensureAuth, getToken, clearAuth, BASE_URL, toastError,
} = require('../../utils/request');

/** 单文件上限与后端保持一致：10MB */
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const ALLOWED_EXTS = ['pdf', 'docx', 'doc', 'txt', 'md'];

function formatSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function buildMeta(item) {
  const parts = [formatSize(item.size_bytes)];
  if (item.pages) parts.push(`${item.pages} 页`);
  if (item.chars) parts.push(`${item.chars} 字`);
  return parts.join(' · ');
}

Page({
  data: {
    loading: true,
    list: [],
    currentId: 0,
    title: '',
    targetRole: '',
    basics: { name: '', phone: '', email: '', city: '', github: '', summary: '' },
    skillsText: '',
    completeness: 0,
    counts: { education: 0, experience: 0, projects: 0 },
    saving: false,
    dirty: false,
    exporting: false,
    // 附件
    attachments: [],
    attachLimit: 20,
    uploading: false,
    uploadText: '',
    importId: 0,
    preview: { open: false, title: '', text: '', loading: false },
  },

  onShow() {
    if (!ensureAuth()) return;
    this.loadList();
  },

  async loadList() {
    try {
      const res = await request({ url: '/resumes' });
      const list = res.items || [];
      this.setData({ loading: false, list });
      if (!list.length) {
        await this.createResume();
        return;
      }
      const target = this.data.currentId && list.some((item) => item.id === this.data.currentId)
        ? this.data.currentId
        : list[0].id;
      await this.loadOne(target);
    } catch (error) {
      this.setData({ loading: false });
      toastError(error);
    }
  },

  async loadOne(id) {
    const res = await request({ url: `/resumes/${id}` });
    const data = res.data || {};
    this.setData({
      currentId: res.id,
      title: res.title || '',
      targetRole: res.target_role || '',
      basics: Object.assign({ name: '', phone: '', email: '', city: '', github: '', summary: '' }, data.basics || {}),
      skillsText: (data.skills || []).join('、'),
      completeness: res.completeness || 0,
      counts: {
        education: (data.education || []).length,
        experience: (data.experience || []).length,
        projects: (data.projects || []).length,
      },
      dirty: false,
    });
    // 附件与简历分开取：附件失败不该让整页报错，但要等它回来再算加载完成，
    // 否则刚进页面就点上传会拿到过期的条数
    await this.loadAttachments(id);
  },

  async loadAttachments(id) {
    const resumeId = id || this.data.currentId;
    if (!resumeId) return;
    try {
      const res = await request({ url: `/resumes/${resumeId}/attachments` });
      this.setData({
        attachments: (res.items || []).map((item) => Object.assign({}, item, { metaText: buildMeta(item) })),
        attachLimit: res.limit || 20,
      });
    } catch (error) {
      // 静默失败：没有附件列表不影响编辑基本信息
      console.warn('attachments load failed', error && error.message);
    }
  },

  async createResume() {
    try {
      const res = await request({ url: '/resumes', method: 'POST', data: { title: '我的简历' } });
      this.setData({ list: [res].concat(this.data.list) });
      await this.loadOne(res.id);
    } catch (error) {
      toastError(error);
    }
  },

  onSwitch(e) {
    const id = Number(e.currentTarget.dataset.id);
    if (id === this.data.currentId) return;
    if (this.data.dirty) {
      wx.showToast({ title: '请先保存当前修改', icon: 'none' });
      return;
    }
    this.loadOne(id).catch(toastError);
  },

  onField(e) {
    const field = e.currentTarget.dataset.field;
    this.setData({ [`basics.${field}`]: e.detail.value, dirty: true });
  },

  onTitle(e) {
    this.setData({ title: e.detail.value, dirty: true });
  },

  onRole(e) {
    this.setData({ targetRole: e.detail.value, dirty: true });
  },

  onSkills(e) {
    this.setData({ skillsText: e.detail.value, dirty: true });
  },

  async save() {
    if (this.data.saving || !this.data.currentId) return;
    this.setData({ saving: true });
    try {
      const skills = this.data.skillsText
        .split(/[、,，\s]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      // 只回传 basics + skills：其余板块（教育/实习/项目）在小程序里不编辑，
      // 但必须原样带回，否则 PATCH 会把它们清空。
      const detail = await request({ url: `/resumes/${this.data.currentId}` });
      const data = Object.assign({}, detail.data || {}, { basics: this.data.basics, skills });
      await request({
        url: `/resumes/${this.data.currentId}?snapshot=true`,
        method: 'PATCH',
        data: { title: this.data.title || '我的简历', target_role: this.data.targetRole || null, data, note: '小程序编辑' },
      });
      wx.showToast({ title: '已保存', icon: 'success' });
      await this.loadOne(this.data.currentId);
    } catch (error) {
      toastError(error);
    } finally {
      this.setData({ saving: false });
    }
  },

  exportPdf() {
    if (!this.data.currentId || this.data.exporting) return;
    this.setData({ exporting: true });
    wx.downloadFile({
      url: `${BASE_URL}/resumes/${this.data.currentId}/pdf`,
      header: { Authorization: `Bearer ${getToken()}` },
      success: (res) => {
        if (res.statusCode !== 200) {
          wx.showToast({ title: '导出失败，请先在网页端检查内容', icon: 'none' });
          return;
        }
        wx.openDocument({
          filePath: res.tempFilePath,
          fileType: 'pdf',
          showMenu: true,
          fail: () => wx.showToast({ title: '打开失败，可稍后重试', icon: 'none' }),
        });
      },
      fail: () => wx.showToast({ title: '下载失败，请检查网络', icon: 'none' }),
      complete: () => this.setData({ exporting: false }),
    });
  },

  // ---------------------------------------------------------------- 附件简历

  /** 一次从聊天记录里选多个文件；小程序只能逐个 uploadFile，这里顺序发 */
  chooseAndUpload() {
    if (this.data.uploading || !this.data.currentId) return;
    const room = this.data.attachLimit - this.data.attachments.length;
    if (room <= 0) {
      wx.showToast({ title: `一份简历最多 ${this.data.attachLimit} 个附件`, icon: 'none' });
      return;
    }
    wx.chooseMessageFile({
      count: Math.min(room, 9),
      type: 'file',
      extension: ALLOWED_EXTS,
      success: (res) => this.uploadFiles(res.tempFiles || []),
      fail: (err) => {
        // 用户取消不提示
        if (!/cancel/i.test((err && err.errMsg) || '')) {
          wx.showToast({ title: '选择文件失败', icon: 'none' });
        }
      },
    });
  },

  async uploadFiles(files) {
    const queue = (files || []).filter((file) => {
      const name = (file.name || file.path || '').toLowerCase();
      const ext = name.includes('.') ? name.split('.').pop() : '';
      if (!ALLOWED_EXTS.includes(ext)) {
        wx.showToast({ title: `${file.name || '文件'} 格式不支持`, icon: 'none' });
        return false;
      }
      if ((file.size || 0) > MAX_FILE_BYTES) {
        wx.showToast({ title: `${file.name || '文件'} 超过 10MB`, icon: 'none' });
        return false;
      }
      return true;
    });
    if (!queue.length) return;

    this.setData({ uploading: true });
    const okList = [];
    const dupList = [];
    const failList = [];
    for (let i = 0; i < queue.length; i += 1) {
      this.setData({ uploadText: `正在上传 ${i + 1}/${queue.length}` });
      try {
        const body = await this.uploadOne(queue[i]);
        const item = (body.items || [])[0] || {};
        if (item.status === 'ok') okList.push(item);
        else if (item.status === 'duplicated') dupList.push(item);
        else failList.push(Object.assign({ filename: queue[i].name }, item));
      } catch (error) {
        failList.push({ filename: queue[i].name, error: error.message });
      }
    }
    this.setData({ uploading: false, uploadText: '' });
    await this.loadAttachments();

    if (!dupList.length && !failList.length) {
      wx.showToast({ title: `已上传 ${okList.length} 个`, icon: 'success' });
      return;
    }
    const lines = [];
    if (okList.length) lines.push(`成功 ${okList.length} 个`);
    if (dupList.length) lines.push(`重复跳过 ${dupList.length} 个`);
    if (failList.length) lines.push(`失败 ${failList.length} 个`);
    const first = failList[0];
    wx.showModal({
      title: '上传结果',
      content: `${lines.join(' · ')}\n${first ? `${first.filename}：${first.error || '上传失败'}` : '重复的文件不会再占一份空间。'}`,
      showCancel: false,
      confirmText: '知道了',
    });
  },

  uploadOne(file) {
    return new Promise((resolve, reject) => {
      wx.uploadFile({
        url: `${BASE_URL}/resumes/${this.data.currentId}/attachments`,
        filePath: file.path,
        name: 'files',
        // wx.uploadFile 不能指定 multipart 文件名，不传的话列表里会显示临时文件名
        formData: { filenames: JSON.stringify([file.name || '']) },
        header: { Authorization: `Bearer ${getToken()}` },
        timeout: 120000,
        success: (res) => {
          let body = {};
          try {
            body = JSON.parse(res.data || '{}');
          } catch (error) {
            body = {};
          }
          if (res.statusCode >= 200 && res.statusCode < 300) {
            resolve(body);
            return;
          }
          if (res.statusCode === 401) {
            clearAuth();
            wx.reLaunch({ url: '/pages/login/login' });
            reject(new Error('登录已过期，请重新登录'));
            return;
          }
          const detail = body.detail;
          reject(new Error(typeof detail === 'string' ? detail : '上传失败'));
        },
        fail: () => reject(new Error('网络异常，上传失败')),
      });
    });
  },

  findAttachment(id) {
    return this.data.attachments.find((item) => item.id === id) || null;
  },

  async onPreview(e) {
    const id = Number(e.currentTarget.dataset.id);
    const item = this.findAttachment(id);
    this.setData({ preview: { open: true, title: (item && item.filename) || '', text: '读取中…', loading: true } });
    try {
      const res = await request({ url: `/resumes/${this.data.currentId}/attachments/${id}/text` });
      this.setData({
        preview: {
          open: true,
          title: res.filename || (item && item.filename) || '',
          text: res.text || res.parse_error || '这份文件里没有可提取的文字',
          loading: false,
        },
      });
    } catch (error) {
      this.setData({
        preview: { open: true, title: (item && item.filename) || '', text: error.message || '读取失败', loading: false },
      });
    }
  },

  closePreview() {
    this.setData({ preview: { open: false, title: '', text: '', loading: false } });
  },

  noop() {},

  /** PDF / Word 走系统预览；txt、md 系统打不开，直接看提取出的文字 */
  onDownload(e) {
    const id = Number(e.currentTarget.dataset.id);
    const item = this.findAttachment(id);
    if (!item) return;
    wx.showLoading({ title: '下载中', mask: true });
    wx.downloadFile({
      url: `${BASE_URL}/resumes/${this.data.currentId}/attachments/${id}/download`,
      header: { Authorization: `Bearer ${getToken()}` },
      success: (res) => {
        wx.hideLoading();
        if (res.statusCode !== 200) {
          wx.showToast({ title: '下载失败，请重试', icon: 'none' });
          return;
        }
        const ext = (item.ext || '').replace('.', '').toLowerCase();
        if (['pdf', 'doc', 'docx'].includes(ext)) {
          wx.openDocument({
            filePath: res.tempFilePath,
            fileType: ext,
            showMenu: true,
            fail: () => this.onPreview(e),
          });
        } else {
          this.onPreview(e);
        }
      },
      fail: () => {
        wx.hideLoading();
        wx.showToast({ title: '下载失败，请检查网络', icon: 'none' });
      },
    });
  },

  async onImport(e) {
    const id = Number(e.currentTarget.dataset.id);
    const item = this.findAttachment(id);
    if (!item || this.data.importId) return;
    if (!item.has_text) {
      wx.showToast({ title: '这份文件没有可解析的文字', icon: 'none' });
      return;
    }
    this.setData({ importId: id });
    wx.showLoading({ title: 'AI 解析中', mask: true });
    try {
      const res = await request({
        url: `/resumes/${this.data.currentId}/attachments/${id}/import`,
        method: 'POST',
        timeout: 120000,
      });
      wx.hideLoading();
      const data = res.data || {};
      const basics = data.basics || {};
      wx.showModal({
        title: '导入为结构化简历？',
        content: [
          `姓名：${basics.name || '未识别'}`,
          `教育 ${(data.education || []).length} 段 · 实习 ${(data.experience || []).length} 段 · 项目 ${(data.projects || []).length} 段`,
          `完整度 ${res.completeness || 0}%`,
          '',
          '确认后会覆盖当前结构化内容，导入前会自动留档，可在网页端「版本」里恢复。',
        ].join('\n'),
        confirmText: '确认导入',
        cancelText: '取消',
        success: (r) => {
          if (r.confirm) this.applyImport(id);
        },
      });
    } catch (error) {
      wx.hideLoading();
      const msg = error.status === 429 ? '今日 AI 额度已用完' : (error.message || '解析失败，请稍后再试');
      wx.showToast({ title: msg, icon: 'none' });
    } finally {
      this.setData({ importId: 0 });
    }
  },

  async applyImport(id) {
    wx.showLoading({ title: '导入中', mask: true });
    try {
      await request({
        url: `/resumes/${this.data.currentId}/attachments/${id}/import?apply=true`,
        method: 'POST',
        timeout: 120000,
      });
      wx.hideLoading();
      wx.showToast({ title: '已导入', icon: 'success' });
      await this.loadOne(this.data.currentId);
    } catch (error) {
      wx.hideLoading();
      toastError(error);
    }
  },

  onDelete(e) {
    const id = Number(e.currentTarget.dataset.id);
    const item = this.findAttachment(id);
    wx.showModal({
      title: '删除这个附件？',
      content: (item && item.filename) || '',
      confirmText: '删除',
      confirmColor: '#a3283f',
      success: async (r) => {
        if (!r.confirm) return;
        try {
          await request({ url: `/resumes/${this.data.currentId}/attachments/${id}`, method: 'DELETE' });
          wx.showToast({ title: '已删除', icon: 'success' });
          await this.loadAttachments();
        } catch (error) {
          toastError(error);
        }
      },
    });
  },
});
