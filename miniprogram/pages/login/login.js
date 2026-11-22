const { request, setToken, setUser, getToken } = require('../../utils/request');

Page({
  data: {
    email: '',
    password: '',
    loading: false,
  },

  onShow() {
    // 已登录直接进首页，避免用户重复输入
    if (getToken()) {
      wx.switchTab({ url: '/pages/index/index' });
    }
  },

  onEmailInput(e) {
    this.setData({ email: e.detail.value });
  },

  onPasswordInput(e) {
    this.setData({ password: e.detail.value });
  },

  async submit() {
    const email = this.data.email.trim();
    const password = this.data.password;
    if (!email || !password) {
      wx.showToast({ title: '请填写邮箱和密码', icon: 'none' });
      return;
    }
    if (this.data.loading) return;
    this.setData({ loading: true });
    try {
      const res = await request({ url: '/login', method: 'POST', data: { email, password } });
      setToken(res.access_token);
      setUser({ role: res.role, userId: res.user_id, email });
      wx.switchTab({ url: '/pages/index/index' });
    } catch (error) {
      wx.showToast({ title: error.message || '登录失败', icon: 'none' });
    } finally {
      this.setData({ loading: false });
    }
  },
});
