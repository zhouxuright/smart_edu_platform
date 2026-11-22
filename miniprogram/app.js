const { ensureAuth } = require('./utils/request');

App({
  globalData: {
    // 全局共享一份用户信息，页面之间不必反复读 storage
    user: null,
  },

  onLaunch() {
    // 冷启动时不在这里跳登录：tabBar 页面已注册，reLaunch 会造成闪屏。
    // 各页面 onShow 里调 ensureAuth()，无 token 才跳。
    this.globalData.user = wx.getStorageSync('qicheng_user') || null;
  },

  onShow() {
    if (!wx.getStorageSync('qicheng_token')) {
      // 什么都不做，交给页面处理
    }
  },
});
