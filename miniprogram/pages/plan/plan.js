const { request, ensureAuth, toastError } = require('../../utils/request');

const STAGES = ['大三在读', '大四在读', '研一在读', '研二在读', '应届毕业', '已毕业'];

Page({
  data: {
    loading: true,
    plan: null,
    percent: 0,
    stages: STAGES,
    stageIndex: 0,
    targetRole: '',
    weeklyHours: 7,
    generating: false,
    showForm: false,
    savingTaskId: 0,
  },

  onShow() {
    if (!ensureAuth()) return;
    this.loadPlan();
  },

  onPullDownRefresh() {
    this.loadPlan().then(() => wx.stopPullDownRefresh());
  },

  async loadPlan() {
    try {
      const res = await request({ url: '/internship-plans/current' });
      const plan = res.plan;
      const total = plan ? plan.tasks.length : 0;
      const done = plan ? plan.completed_count : 0;
      this.setData({
        loading: false,
        plan,
        percent: total ? Math.round((done / total) * 100) : 0,
        showForm: !plan,
        targetRole: plan ? plan.target_role : this.data.targetRole,
      });
      if (plan) {
        // 打开计划页记一次行为，用于漏斗；后端按天去重，重复调用不会刷量
        request({ url: '/internship-plans/events', method: 'POST', data: { event: 'plan_opened', plan_id: plan.id } }).catch(() => {});
      }
    } catch (error) {
      this.setData({ loading: false });
      toastError(error);
    }
  },

  onRoleInput(e) {
    this.setData({ targetRole: e.detail.value });
  },

  onStageChange(e) {
    this.setData({ stageIndex: Number(e.detail.value) });
  },

  onHoursChange(e) {
    this.setData({ weeklyHours: Number(e.detail.value) });
  },

  async generate() {
    const targetRole = this.data.targetRole.trim();
    if (targetRole.length < 2) {
      wx.showToast({ title: '请填写目标岗位，至少 2 个字', icon: 'none' });
      return;
    }
    if (this.data.generating) return;
    this.setData({ generating: true });
    try {
      await request({
        url: '/internship-plans/generate',
        method: 'POST',
        data: {
          target_role: targetRole,
          stage: STAGES[this.data.stageIndex],
          weekly_hours: this.data.weeklyHours,
          replace: Boolean(this.data.plan),
        },
        timeout: 90000,
      });
      wx.showToast({ title: '计划已生成', icon: 'success' });
      await this.loadPlan();
    } catch (error) {
      wx.showToast({ title: error.message || '生成失败', icon: 'none', duration: 2400 });
    } finally {
      this.setData({ generating: false });
    }
  },

  async toggleTask(e) {
    const { id, completed } = e.currentTarget.dataset;
    if (this.data.savingTaskId) return;
    this.setData({ savingTaskId: id });
    try {
      await request({
        url: `/internship-plans/tasks/${id}`,
        method: 'PATCH',
        data: { completed: !completed },
      });
      await this.loadPlan();
      if (!completed) {
        wx.showToast({ title: '完成一步，继续保持', icon: 'none' });
      }
    } catch (error) {
      toastError(error);
    } finally {
      this.setData({ savingTaskId: 0 });
    }
  },

  toggleForm() {
    this.setData({ showForm: !this.data.showForm });
  },
});
