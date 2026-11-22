const { request, ensureAuth, toastError } = require('../../utils/request');

/** 与后端 applications.TRANSITIONS 保持一致，前端只展示合法的下一步 */
const NEXT = {
  draft: ['applied', 'withdrawn'],
  applied: ['screening', 'interview', 'rejected', 'withdrawn'],
  screening: ['interview', 'rejected', 'withdrawn'],
  interview: ['offer', 'rejected', 'withdrawn'],
  offer: ['withdrawn'],
  rejected: ['applied'],
  withdrawn: ['applied'],
};

const CHANNELS = [
  { value: 'campus', label: '校招' },
  { value: 'referral', label: '内推' },
  { value: 'job_board', label: '招聘网站' },
  { value: 'company_site', label: '公司官网' },
  { value: 'other', label: '其他' },
];

const STATUS_TAG = {
  draft: 'tag-gray',
  applied: 'tag-blue',
  screening: 'tag-amber',
  interview: 'tag-amber',
  offer: 'tag-green',
  rejected: 'tag-red',
  withdrawn: 'tag-gray',
};

Page({
  data: {
    loading: true,
    items: [],
    stats: null,
    statusLabel: {},
    channels: CHANNELS,
    channelIndex: 0,
    showForm: false,
    company: '',
    position: '',
    submitting: false,
  },

  onShow() {
    if (!ensureAuth()) return;
    this.loadAll();
  },

  onPullDownRefresh() {
    this.loadAll().then(() => wx.stopPullDownRefresh());
  },

  async loadAll() {
    try {
      const [list, stats] = await Promise.all([
        request({ url: '/applications' }),
        request({ url: '/applications/stats' }),
      ]);
      this.setData({
        loading: false,
        items: (list.items || []).map((item) => Object.assign({}, item, { tagClass: STATUS_TAG[item.status] || 'tag-gray' })),
        statusLabel: list.status_label || {},
        stats,
      });
    } catch (error) {
      this.setData({ loading: false });
      toastError(error);
    }
  },

  toggleForm() {
    this.setData({ showForm: !this.data.showForm });
  },

  onCompany(e) {
    this.setData({ company: e.detail.value });
  },

  onPosition(e) {
    this.setData({ position: e.detail.value });
  },

  onChannel(e) {
    this.setData({ channelIndex: Number(e.detail.value) });
  },

  async create() {
    const company = this.data.company.trim();
    const position = this.data.position.trim();
    if (!company || !position) {
      wx.showToast({ title: '公司和岗位都要填', icon: 'none' });
      return;
    }
    if (this.data.submitting) return;
    this.setData({ submitting: true });
    try {
      await request({
        url: '/applications',
        method: 'POST',
        data: { company, position, channel: CHANNELS[this.data.channelIndex].value, status: 'applied' },
      });
      this.setData({ company: '', position: '', showForm: false });
      wx.showToast({ title: '已记录', icon: 'success' });
      await this.loadAll();
    } catch (error) {
      toastError(error);
    } finally {
      this.setData({ submitting: false });
    }
  },

  changeStatus(e) {
    const { id, status } = e.currentTarget.dataset;
    const options = NEXT[status] || [];
    if (!options.length) return;
    const labels = options.map((key) => this.data.statusLabel[key] || key);
    wx.showActionSheet({
      itemList: labels,
      success: async (res) => {
        const next = options[res.tapIndex];
        try {
          await request({ url: `/applications/${id}`, method: 'PATCH', data: { status: next } });
          wx.showToast({ title: `已更新为${this.data.statusLabel[next] || next}`, icon: 'none' });
          await this.loadAll();
        } catch (error) {
          toastError(error);
        }
      },
    });
  },
});
