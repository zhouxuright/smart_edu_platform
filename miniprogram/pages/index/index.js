const { request, ensureAuth, getUser, toastError } = require('../../utils/request');

Page({
  data: {
    name: '',
    loading: true,
    stats: { studyHours: 0, achievements: 0, courses: 0 },
    plan: null,
    planPercent: 0,
    funnel: { submitted: 0, interview: 0, offer: 0 },
    recommendations: [],
  },

  onShow() {
    if (!ensureAuth()) return;
    const user = getUser();
    this.setData({ name: user && user.email ? user.email.split('@')[0] : '同学' });
    this.loadAll();
  },

  onPullDownRefresh() {
    this.loadAll().then(() => wx.stopPullDownRefresh());
  },

  async loadAll() {
    this.setData({ loading: true });
    // 三个接口互不依赖，任一失败不该让整页空掉，所以分开 catch
    const [summary, plan, appStats] = await Promise.all([
      request({ url: '/dashboard/summary' }).catch(() => null),
      request({ url: '/internship-plans/current' }).catch(() => null),
      request({ url: '/applications/stats' }).catch(() => null),
    ]);

    const currentPlan = plan && plan.plan ? plan.plan : null;
    const total = currentPlan ? currentPlan.tasks.length : 0;
    const done = currentPlan ? currentPlan.completed_count : 0;

    this.setData({
      loading: false,
      name: (summary && summary.name) || this.data.name,
      stats: {
        studyHours: summary ? summary.study_hours : 0,
        achievements: summary ? summary.achievements : 0,
        courses: summary ? summary.active_courses : 0,
      },
      plan: currentPlan,
      planPercent: total ? Math.round((done / total) * 100) : 0,
      funnel: {
        submitted: appStats ? appStats.submitted : 0,
        interview: appStats ? appStats.interview_count : 0,
        offer: appStats ? appStats.offer_count : 0,
      },
      recommendations: (summary && summary.recommendations) || [],
    });
  },

  goPlan() {
    wx.switchTab({ url: '/pages/plan/plan' });
  },

  goApplications() {
    wx.switchTab({ url: '/pages/applications/applications' });
  },

  goMentor() {
    wx.switchTab({ url: '/pages/mentor/mentor' });
  },

  goResume() {
    wx.switchTab({ url: '/pages/resume/resume' });
  },
});
