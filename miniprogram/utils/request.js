/**
 * 后端地址。
 *
 * 默认写电脑的局域网 IP，而不是 127.0.0.1：
 * 真机调试时 127.0.0.1 指的是手机自己，连不到电脑；局域网 IP 在
 * 开发者工具和真机上都能用。换 WiFi / IP 变了不用改代码，在开发者工具
 * Console 里执行下面这句再重新编译即可：
 *   wx.setStorageSync('qicheng_base_url', 'http://新IP:8000/api')
 *
 * 本地调试还要在「详情 → 本地设置」勾选「不校验合法域名」
 * （project.config.json 里 urlCheck 已置 false）。
 * 上线前换成已在小程序后台备案的 https 域名。
 */
const DEFAULT_BASE_URL = 'http://192.168.1.3:8000/api';

const OVERRIDE_BASE_URL = (typeof wx !== 'undefined' && wx.getStorageSync('qicheng_base_url')) || '';
const BASE_URL = OVERRIDE_BASE_URL || DEFAULT_BASE_URL;

const TOKEN_KEY = 'qicheng_token';
const USER_KEY = 'qicheng_user';

function getToken() {
  return wx.getStorageSync(TOKEN_KEY) || '';
}

function setToken(token) {
  wx.setStorageSync(TOKEN_KEY, token);
}

function getUser() {
  return wx.getStorageSync(USER_KEY) || null;
}

function setUser(user) {
  wx.setStorageSync(USER_KEY, user || null);
}

function clearAuth() {
  wx.removeStorageSync(TOKEN_KEY);
  wx.removeStorageSync(USER_KEY);
}

/** 每个页面的 onShow 都调一次：没登录就直接回登录页，避免白屏 */
function ensureAuth() {
  if (getToken()) return true;
  wx.reLaunch({ url: '/pages/login/login' });
  return false;
}

function request(options) {
  const { url, method = 'GET', data, timeout = 60000 } = options;
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE_URL + url,
      method,
      data,
      timeout,
      header: Object.assign(
        { 'Content-Type': 'application/json' },
        getToken() ? { Authorization: `Bearer ${getToken()}` } : {},
      ),
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
          return;
        }
        if (res.statusCode === 401) {
          clearAuth();
          wx.reLaunch({ url: '/pages/login/login' });
          reject(new Error('登录已过期，请重新登录'));
          return;
        }
        const detail = (res.data && res.data.detail) || '请求失败，请稍后再试';
        const error = new Error(typeof detail === 'string' ? detail : '请求失败');
        error.status = res.statusCode;
        reject(error);
      },
      fail() {
        reject(new Error('网络异常，请确认后端已启动'));
      },
    });
  });
}

function toastError(error) {
  wx.showToast({ title: (error && error.message) || '操作失败', icon: 'none', duration: 2200 });
}

module.exports = {
  BASE_URL,
  request,
  getToken,
  setToken,
  getUser,
  setUser,
  clearAuth,
  ensureAuth,
  toastError,
};
