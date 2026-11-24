/**
 * 追问按钮验收：点一下是否真的进入下一轮。
 *
 * 后端已有 `backend/scripts/agent_followup_check.py` 验证「追问文案 → 当输入发出去
 * → 拿到回答」这条路，但那证明不了前端：按钮有没有渲染、点击有没有绑上、
 * 点了之后是不是真的发出去了，只有真点一次才算数。
 *
 * 和 `agent_dock_check.cjs` 的分工：那边管入口、提案卡片、应用/放弃、切页上下文，
 * 这里只管追问按钮这一条链路，所以能独立跑、自建账号，不依赖外部测试号。
 *
 * 用法（在 frontend 目录，先起 preview 和后端）：
 *   node scripts/agent_followup_ui.cjs
 *   UI_BASE=http://127.0.0.1:4173 API_BASE=http://127.0.0.1:8000 node scripts/agent_followup_ui.cjs
 */
const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer');

const CHROME = process.env.UI_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const UI_BASE = (process.env.UI_BASE || 'http://127.0.0.1:4173').replace(/\/$/, '');
const API_BASE = (process.env.API_BASE || 'http://127.0.0.1:8000').replace(/\/$/, '');
const TIMEOUT = Number(process.env.UI_TIMEOUT || 180000);

const OUT = path.join(__dirname, 'out');
const passed = [];
const failed = [];

function check(name, ok, detail = '') {
  (ok ? passed : failed).push(name);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  -> ${detail}` : ''}`);
  return ok;
}

const rand = (n = 8) =>
  Array.from({ length: n }, () => 'abcdefghijklmnopqrstuvwxyz0123456789'[Math.floor(Math.random() * 36)]).join('');

async function register() {
  const email = `ui_${rand()}@example.com`;
  const password = `Passw0rd!${rand(6)}`;
  const res = await fetch(`${API_BASE}/api/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, full_name: 'UI追问', role: 'student' }),
  });
  const created = await res.json();
  if (res.status !== 200) throw new Error(`注册失败 HTTP ${res.status} ${JSON.stringify(created)}`);

  // /api/register 只回用户对象，token 得再登一次拿
  const login = await fetch(`${API_BASE}/api/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  const body = await login.json();
  if (login.status !== 200) throw new Error(`登录失败 HTTP ${login.status} ${JSON.stringify(body)}`);
  const token = body.access_token || body.token;
  if (!token) throw new Error(`没有 token：${JSON.stringify(body)}`);
  return { token, userId: created.id ?? body.user_id };
}

const countMsgs = (page) => page.$$eval('.agent-msg', (nodes) => nodes.length);
const readMsgs = (page) => page.$$eval('.agent-msg', (nodes) => nodes.map((n) => n.textContent.trim()));
const readLabels = (page) =>
  page.$$eval('.agent-followups button', (ns) => ns.map((n) => n.textContent.trim())).catch(() => []);

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  console.log(`UI：${UI_BASE}   API：${API_BASE}\n`);

  const { token, userId } = await register();
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 950 });

  try {
    // 先落到同源页面再写 localStorage，否则设置会被随后的导航冲掉
    await page.goto(`${UI_BASE}/login`, { waitUntil: 'domcontentloaded', timeout: TIMEOUT });
    await page.evaluate(
      (t, uid) => {
        localStorage.setItem('token', t);
        localStorage.setItem('role', 'student');
        if (uid) localStorage.setItem('user_id', String(uid));
        // 清掉上次的会话 id，保证从干净状态开始
        Object.keys(localStorage)
          .filter((k) => k.startsWith('qicheng:agent_session:'))
          .forEach((k) => localStorage.removeItem(k));
      },
      token,
      userId,
    );

    await page.goto(`${UI_BASE}/dashboard`, { waitUntil: 'networkidle2', timeout: TIMEOUT });
    check('仪表盘打开', true, page.url());

    await page.waitForSelector('.agent-fab', { timeout: 30000 });
    await page.click('.agent-fab');
    await page.waitForSelector('.agent-panel', { timeout: 15000 });
    check('助手面板打开', !!page);

    // ---- 首轮提问 ----
    await page.waitForSelector('.agent-input textarea', { timeout: 15000 });
    await page.type('.agent-input textarea', '我现在最该做什么？');
    await page.keyboard.press('Enter');

    let gotFollowups = true;
    try {
      await page.waitForSelector('.agent-followups button', { timeout: TIMEOUT });
    } catch {
      gotFollowups = false;
    }
    check('首轮回答后出现追问按钮', gotFollowups);
    if (!gotFollowups) {
      const msgs = await readMsgs(page);
      console.log('      当前消息：', JSON.stringify(msgs).slice(0, 400));
      await page.screenshot({ path: path.join(OUT, 'followup-fail.png') });
      throw new Error('追问按钮没渲染出来，后面不用测了');
    }

    const labels = await readLabels(page);
    check('追问按钮有 2~3 个', labels.length >= 2 && labels.length <= 3, JSON.stringify(labels));

    // 这一张才有按钮：点击之后按钮会被消耗掉，最后再截就只剩 loading
    await page.screenshot({ path: path.join(OUT, 'followup-buttons.png') });
    console.log(`      截图：scripts/out/followup-buttons.png`);

    // ---- 真点一下 ----
    const before = await countMsgs(page);
    const target = labels[0];
    console.log(`      点击前 ${before} 条消息，准备点「${target}」`);

    await page.click('.agent-followups button');

    await page.waitForFunction(
      (n) => document.querySelectorAll('.agent-msg').length >= n + 2,
      { timeout: 15000 },
      before,
    );
    check('点击后立刻进入下一轮（消息 +2）', (await countMsgs(page)) >= before + 2, `${before} → ${await countMsgs(page)}`);

    // 按钮应该被消耗掉，避免连点发出重复请求
    const stillThere = (await readLabels(page)).length;
    check('点击后旧按钮被清掉', stillThere === 0, `还剩 ${stillThere} 个`);

    // ---- 等新一轮回答 ----
    await page.waitForFunction(
      (n) => !document.querySelector('.agent-typing') && document.querySelectorAll('.agent-msg').length >= n,
      { timeout: TIMEOUT },
      before + 2,
    );
    const after = await readMsgs(page);
    check('新一轮拿到回答', after.length >= before + 2, `${after.length} 条`);

    // 用户气泡应该正好是按钮那句话 —— 这是「点按钮 = 发这句话」的直接证据
    const userBubble = after[after.length - 2] || '';
    check(
      '点按钮等于把这句话发出去',
      userBubble.includes(target.slice(0, Math.min(8, target.length))),
      `用户消息=「${userBubble.slice(0, 40)}」 按钮=「${target}」`,
    );

    // ---- 新一轮又给新按钮 ----
    let again = true;
    try {
      await page.waitForSelector('.agent-followups button', { timeout: 60000 });
    } catch {
      again = false;
    }
    check('新一轮又给出追问按钮', again);

    if (again) {
      const labels2 = await readLabels(page);
      check('第二轮追问与第一轮不同', JSON.stringify(labels2) !== JSON.stringify(labels), JSON.stringify(labels2));

      // ---- 连点第二次，确认不是只有第一下能用 ----
      const before2 = await countMsgs(page);
      await page.click('.agent-followups button');
      await page.waitForFunction(
        (n) => document.querySelectorAll('.agent-msg').length >= n + 2,
        { timeout: 15000 },
        before2,
      );
      check('连点第二次仍能进入下一轮', (await countMsgs(page)) >= before2 + 2, `${before2} → ${await countMsgs(page)}`);
    }

    await page.screenshot({ path: path.join(OUT, 'followup-ok.png') });
    console.log('      截图：scripts/out/followup-ok.png');
  } finally {
    await browser.close();
  }

  console.log(`\n通过 ${passed.length}，失败 ${failed.length}`);
  if (failed.length) {
    console.log('\n失败项：');
    failed.forEach((f) => console.log(`  - ${f}`));
    process.exitCode = 1;
  }
})().catch((e) => {
  console.error('\n跑挂了：', e.message);
  process.exitCode = 1;
});
