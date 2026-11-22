/**
 * 新增工具的浏览器验收：新增项目（item 卡片）、整段润色（text 卡片）、勾任务（status 卡片）。
 * 每种都会真的应用一次，跑完把演示数据还原。
 *
 * 用法（在 frontend 目录）：
 *   UI_EMAIL=xx@example.com UI_PASSWORD=xxx node scripts/agent_tools_check.cjs
 */
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const CHROME = process.env.UI_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const BASE = process.env.UI_BASE || 'http://127.0.0.1:5173';
const EMAIL = process.env.UI_EMAIL;
const PASSWORD = process.env.UI_PASSWORD;

if (!EMAIL || !PASSWORD) {
  console.error('请通过 UI_EMAIL / UI_PASSWORD 提供测试账号');
  process.exit(1);
}

const OUT = path.join(__dirname, 'out');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const passed = [];
const failed = [];

function check(name, ok, detail = '') {
  (ok ? passed : failed).push(name);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  -> ${detail}` : ''}`);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1000 });

  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));

  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2', timeout: 60000 });
  const inputs = await page.$$('input');
  await inputs[0].type(EMAIL);
  await inputs[1].type(PASSWORD);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 60000 }).catch(() => {}),
    page.click('button[type="submit"]'),
  ]);

  const token = await page.evaluate(() => localStorage.getItem('token'));
  const api = (method, url, body) => page.evaluate(async (m, u, b, t) => {
    const res = await fetch(u, {
      method: m,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${t}` },
      body: b ? JSON.stringify(b) : undefined,
    });
    return { status: res.status, data: await res.json().catch(() => ({})) };
  }, method, url, body, token);

  // 备份简历，跑完还原
  const list = await api('GET', '/api/resumes');
  const resumeId = list.data.items[0].id;
  const snapshot = await api('GET', `/api/resumes/${resumeId}`);
  const originalData = snapshot.data.data;

  const openDock = async () => {
    if (await page.$('.agent-fab')) {
      await page.click('.agent-fab');
      await sleep(700);
    }
  };

  const waitAnswer = () => page.waitForFunction(
    () => !document.querySelector('.agent-typing')
      && !document.querySelector('.agent-input textarea')?.disabled,
    { timeout: 180000, polling: 300 },
  );

  const ask = async (text) => {
    await openDock();
    const ta = await page.$('.agent-input textarea');
    await ta.click();
    await page.evaluate(() => { document.querySelector('.agent-input textarea').value = ''; });
    await ta.type(text);
    await page.keyboard.press('Enter');
    await waitAnswer();
    await sleep(500);
  };

  const lastProposal = () => page.evaluate(() => {
    const nodes = [...document.querySelectorAll('.agent-proposal')];
    const node = nodes[nodes.length - 1];
    if (!node) return null;
    return {
      title: node.querySelector('.agent-proposal-title')?.innerText || '',
      classes: node.className,
      lines: [...node.querySelectorAll('.agent-diff-line, .agent-diff-para')].map((n) => n.innerText),
      meta: node.querySelector('.agent-item-meta')?.innerText || '',
      chips: [...node.querySelectorAll('.agent-chip')].map((n) => n.innerText),
      buttons: [...node.querySelectorAll('.agent-proposal-actions button')].map((b) => b.innerText.trim()),
    };
  });

  const clickApply = async () => {
    const handle = await page.evaluateHandle(() => {
      const nodes = [...document.querySelectorAll('.agent-proposal')];
      const node = nodes[nodes.length - 1];
      return node ? [...node.querySelectorAll('.agent-proposal-actions button')]
        .find((b) => b.innerText.includes('应用')) : null;
    });
    const el = handle.asElement();
    if (!el) return false;
    await el.click();
    await sleep(3000);
    return true;
  };

  /* ------------------------------------------------ 1. 新增项目 */
  await page.goto(`${BASE}/resume`, { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(2500);
  await ask('帮我加一段项目经历：校园二手书交易小程序，我担任产品负责人，2025.09 到 2025.12，访谈 12 位同学并把卖书麻烦拆成 3 个场景，上线 4 周促成 186 笔交易');

  let card = await lastProposal();
  check('新增项目出 item 卡片', !!card && card.title.includes('新增项目'), card ? card.title : '无卡片');
  if (card) {
    check('卡片显示角色与时间', card.meta.includes('产品负责人'), card.meta);
    check('卡片列出要点', card.lines.length >= 1, `${card.lines.length} 行`);
    check('卡片有应用按钮', card.buttons.some((b) => b.includes('应用')), card.buttons.join('/'));
  }
  await page.screenshot({ path: path.join(OUT, 'tools-1-add-project.png') });

  check('应用新增项目', await clickApply());
  const afterAdd = await api('GET', `/api/resumes/${resumeId}`);
  const projects = afterAdd.data.data.projects || [];
  check('项目已落库', projects.length === (originalData.projects || []).length + 1,
    `${(originalData.projects || []).length} -> ${projects.length}`);
  check('项目名正确', (projects[projects.length - 1] || {}).name?.includes('二手书'),
    (projects[projects.length - 1] || {}).name);

  const shownProject = await page.evaluate(() => document.body.innerText.includes('校园二手书交易小程序'));
  check('页面已显示新项目', shownProject);

  /* ------------------------------------------------ 2. 润色自我介绍 */
  await ask('把我的一句话自我介绍润色一下，突出产品实习和数据结果');
  card = await lastProposal();
  check('润色出 text 卡片', !!card && card.title.includes('润色'), card ? card.title : '无卡片');
  if (card) {
    check('卡片有改前改后两段', card.lines.length >= 2, `${card.lines.length} 行`);
  }
  await page.screenshot({ path: path.join(OUT, 'tools-2-polish-summary.png') });

  check('应用润色', await clickApply());
  const afterPolish = await api('GET', `/api/resumes/${resumeId}`);
  const newSummary = afterPolish.data.data.basics.summary || '';
  check('自我介绍已变', newSummary !== (originalData.basics.summary || ''), newSummary.slice(0, 40));

  /* ------------------------------------------------ 3. 计划页勾任务 */
  await page.goto(`${BASE}/plan/internship`, { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(2500);

  const hasFab = await page.$('.agent-fab');
  check('计划页有助手入口', !!hasFab);
  if (hasFab) {
    await page.screenshot({ path: path.join(OUT, 'tools-3-plan-entry.png') });

    const planBefore = await api('GET', '/api/internship-plans/current');
    const pending = (planBefore.data.plan?.tasks || []).find((t) => !t.completed);

    if (!pending) {
      console.log('  （计划里没有未完成任务，跳过勾选）');
    } else {
      await ask(`第 ${pending.day_index} 天那个任务我做完了，帮我标成完成`);
      card = await lastProposal();
      check('勾任务出 status 卡片', !!card && card.chips.length === 2, card ? card.chips.join(' -> ') : '无卡片');
      await page.screenshot({ path: path.join(OUT, 'tools-4-plan-toggle.png') });

      check('应用勾选', await clickApply());
      const planAfter = await api('GET', '/api/internship-plans/current');
      const hit = (planAfter.data.plan.tasks || []).find((t) => t.id === pending.id);
      check('任务已标记完成', hit?.completed === true, `completed=${hit?.completed}`);

      const doneInDom = await page.evaluate(() => document.querySelectorAll('.plan-day.is-done').length);
      check('页面勾选状态已刷新', doneInDom > 0, `命中 ${doneInDom} 个已完成节点`);

      // 还原
      await api('PATCH', `/api/internship-plans/tasks/${pending.id}`, { completed: false });
    }
  }

  // 还原简历
  await api('PATCH', `/api/resumes/${resumeId}`, { data: originalData });
  console.log('  （演示简历已还原）');

  console.log();
  console.log(`通过 ${passed.length} 项，失败 ${failed.length} 项`);
  if (failed.length) console.log('失败项:', failed);
  if (errors.length) console.log('页面报错:', errors.slice(0, 5));

  await browser.close();
  process.exit(failed.length ? 1 : 0);
})();
