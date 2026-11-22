import { useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight, ArrowUpRight, Briefcase, Compass, GraduationCap, Lock, Menu, Route, Sparkles, X } from 'lucide-react';
import { CAREER_MENTOR_PATH, CAREER_QUESTION_LIMIT, saveMentorDraft } from '../services/mentorDraft';
import './LandingPage.css';

const scenarios = [
  { label: '考研', title: '考研和实习怎么选？', question: '我现在大三，考研和实习之间还没想清楚，可以怎么比较？', icon: GraduationCap, summary: '从目标、时间和当前准备情况出发，看看两条路分别需要投入什么。', steps: ['列出你在意的三个选择标准', '核对关键准备节点', '定一个这周能完成的验证动作'] },
  { label: '实习', title: '第一份实习怎么找？', question: '大二没实习经历，第一份实习怎么找？', icon: Briefcase, summary: '不用等简历变得完美。先缩小范围，再把现有经历整理成能投递的材料。', steps: ['选一个岗位方向', '梳理两段课程或校园经历', '从三份岗位描述提取共性要求'] },
  { label: '求职', title: '简历没经历可写怎么办？', question: '我的简历没什么实习经历，怎么把课程项目写得有说服力？', icon: Route, summary: '先盘点自己做过的事，再用具体行动和结果说明你具备哪些能力。', steps: ['选出一个能说明能力的项目', '用行动和结果重写经历', '对照目标岗位调整顺序'] },
  { label: '方向', title: '不喜欢本专业还能转吗？', question: '我不太喜欢现在的专业，想探索别的方向，应该先做什么？' },
  { label: '成长', title: '总拖延，怎么每天推进？', question: '目标有了但总是在拖延，可以帮我把它拆成每天能做到的小事吗？' },
];

const sectionLinks = [['#experience', '产品'], ['#scenarios', '适用场景'], ['#approach', '关于我们']];
const signedIn = () => Boolean(localStorage.getItem('token'));

export default function LandingPage() {
  const [question, setQuestion] = useState('');
  const [needsQuestion, setNeedsQuestion] = useState(false);
  const [active, setActive] = useState(1);
  const [menuOpen, setMenuOpen] = useState(false);
  const inputRef = useRef(null);
  const navigate = useNavigate();
  const example = scenarios[active];
  const ExampleIcon = example.icon;

  function pickQuestion(text) {
    setQuestion(text);
    setNeedsQuestion(false);
    inputRef.current?.focus();
  }

  function submitQuestion(event) {
    event.preventDefault();
    if (!question.trim()) {
      setNeedsQuestion(true);
      inputRef.current?.focus();
      return;
    }
    saveMentorDraft(question);
    if (signedIn()) navigate(CAREER_MENTOR_PATH);
    else navigate('/login', { state: { returnTo: CAREER_MENTOR_PATH } });
  }

  return (
    <div className="career-site">
      <a className="career-skip" href="#main-content">跳到主要内容</a>
      <header className="career-header">
        <nav className="career-nav" aria-label="主导航" onKeyDown={(event) => { if (event.key === 'Escape' && menuOpen) { setMenuOpen(false); event.currentTarget.querySelector('.career-menu-toggle')?.focus(); } }}>
          <a className="career-brand" href="#top" onClick={() => setMenuOpen(false)}><span className="career-brand-mark"><Compass size={19} strokeWidth={2.3} aria-hidden="true" /></span>启程 AI</a>
          <div className="career-nav-links">{sectionLinks.map(([href, label]) => <a key={href} href={href}>{label}</a>)}</div>
          <div className="career-nav-actions">
            <Link className="career-signin" to={signedIn() ? '/dashboard' : '/login'}>{signedIn() ? '我的工作台' : '登录'}</Link>
            <a className="career-button career-button-small" href="#start" onClick={() => inputRef.current?.focus()}>开始体验</a>
          </div>
          <button className="career-menu-toggle" aria-label={menuOpen ? '关闭菜单' : '打开菜单'} aria-expanded={menuOpen} aria-controls="career-mobile-nav" type="button" onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size={23} /> : <Menu size={23} />}</button>
          <div className="career-mobile-nav" id="career-mobile-nav" hidden={!menuOpen}>
            {sectionLinks.map(([href, label]) => <a key={href} href={href} onClick={() => setMenuOpen(false)}>{label}</a>)}
            <Link to={signedIn() ? '/dashboard' : '/login'}>{signedIn() ? '我的工作台' : '登录'}</Link>
            <a href="#start" onClick={() => { setMenuOpen(false); inputRef.current?.focus(); }}>开始体验</a>
          </div>
        </nav>
      </header>
      <main id="main-content">
        <section className="career-hero" id="top" aria-labelledby="career-title">
          <div className="career-hero-content">
            <h1 id="career-title"><span>考研、实习、求职</span><span className="career-accent">把迷茫变成下一步</span></h1>
            <p className="career-hero-lede">说说你现在的目标和困惑。<br />一起比较选择，找到一件今天就能开始的事。</p>
            <div className="career-composer-area" id="start">
              <form className="career-composer" onSubmit={submitQuestion}>
                <span className="career-composer-icon" aria-hidden="true"><Sparkles size={22} strokeWidth={1.7} /></span>
                <label htmlFor="career-question" className="career-sr-only">你想问 AI 导师什么？</label>
                <input id="career-question" ref={inputRef} placeholder="大三了，考研还是先找实习？" type="text" enterKeyHint="go" autoComplete="off" maxLength={CAREER_QUESTION_LIMIT} value={question} aria-invalid={needsQuestion || undefined} aria-describedby={needsQuestion ? 'career-question-error' : undefined} onChange={(event) => { setQuestion(event.target.value); setNeedsQuestion(false); }} />
                <button className="career-button career-submit" type="submit">开始规划 <ArrowRight size={17} aria-hidden="true" /></button>
              </form>
              {needsQuestion && <p className="career-input-error" id="career-question-error" role="alert">先写下你的问题，或者试试下面的示例。</p>}
              <div className="career-chips" aria-label="快捷问题">
                {scenarios.map(({ label, title, question: preset }) => <button className="career-chip" type="button" key={label} onClick={() => pickQuestion(preset)}><span>{label}</span>{title}</button>)}
              </div>
            </div>
          </div>
        </section>
        <section className="career-section" id="experience" aria-labelledby="experience-title">
          <p className="career-kicker">产品体验</p>
          <h2 id="experience-title">不用一次想清楚，先走好下一步</h2>
          <p className="career-section-lede">把目标讲清、看见不同选择，再把行动拆小。</p>
          <div className="career-scenario-panel" id="scenarios">
            <div className="career-scenario-tabs" role="group" aria-label="选择规划场景">
              {scenarios.slice(0, 3).map(({ label, icon: Icon }, index) => <button type="button" key={label} aria-pressed={active === index} onClick={() => setActive(index)}><Icon size={18} aria-hidden="true" />{label}规划</button>)}
            </div>
            <div className="career-scenario-content">
              <div><p className="career-example-label"><ExampleIcon size={18} aria-hidden="true" /> {example.label}场景</p><h3>{example.title}</h3><p>{example.summary}</p><a className="career-detail-link" href="#start" onClick={() => pickQuestion(example.question)}>用这个问题试试 <ArrowUpRight size={16} aria-hidden="true" /></a>{example.label === '实习' && <Link className="career-plan-link" to="/plan/internship">直接生成 7 天实习计划 <ArrowRight size={16} aria-hidden="true" /></Link>}</div>
              <div className="career-scenario-card"><p><Sparkles size={17} aria-hidden="true" /> 行动建议示例</p><ol>{example.steps.map((step) => <li key={step}>{step}</li>)}</ol><small>示例用于介绍产品，实际建议取决于你的提问。</small></div>
            </div>
          </div>
        </section>
        <section className="career-section career-approach" id="approach" aria-labelledby="approach-title">
          <p className="career-kicker">关于启程</p><h2 id="approach-title">给你选择依据，不替你做决定</h2>
          <p className="career-section-lede">启程 AI 是面向大学生的生涯对话工具。它可以帮你梳理思路，但不承诺录取、实习或就业结果。</p>
          <div className="career-faq">
            <details><summary>我需要先注册才能提问吗？</summary><p>你可以先在首页写下问题。登录后会恢复草稿，确认后再发送给 AI 导师。</p></details>
            <details><summary>首页草稿会发给服务器吗？</summary><p><Lock size={15} aria-hidden="true" /> 不会。草稿暂存在当前浏览器标签页，最多保留 30 分钟；只有在导师页发送时才会提交。请勿输入密码、证件号等信息。</p></details>
            <details><summary>AI 的建议应该怎么用？</summary><p>把它当成梳理选择的起点。涉及院校政策、招聘要求或截止时间，请以相关机构的正式信息为准。</p></details>
          </div>
        </section>
      </main>
      <footer className="career-footer"><a className="career-brand" href="#top"><Compass size={20} aria-hidden="true" />启程 AI</a><span>大学生生涯规划 · Web 体验版</span><Link to={signedIn() ? '/dashboard' : '/login'}>{signedIn() ? '我的工作台' : '登录工作台'} <ArrowRight size={14} aria-hidden="true" /></Link></footer>
    </div>
  );
}
