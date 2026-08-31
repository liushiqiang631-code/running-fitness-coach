import { demoData } from './data.js';
import {
  applySafetyPlan,
  calculateCompletionRate,
  calculateRacePace,
  completeWorkoutState,
  deriveReview,
  escapeHtml,
  getGoalDistanceKm,
  isRagErrorText,
  isSafetyMode,
  normalizeState,
  parseSseBuffer,
} from './core.js';

const STORAGE_KEY = 'stride-coach-v1';
const app = document.querySelector('#app');
const clone = (value) => JSON.parse(JSON.stringify(value));
const readState = () => {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    return normalizeState(demoData, saved);
  } catch { return clone(demoData); }
};
let state = readState();
// Migrate the earlier demo seed without overwriting goals a user has genuinely customised.
if (state.goal.targetTime === '1:38:00') state.goal.targetTime = '1:40:00';
if (state.goal.targetPace === `4'39"/km`) state.goal.targetPace = `4'44"/km`;
if (state.goal.raceDate === '2026-10-18') state.goal.raceDate = '2026-10-26';
if (state.athlete.tenKPersonalBest === '45:08') state.athlete.tenKPersonalBest = '45:41';
state.athlete.vdot ||= 44;
if (state.athleteState.weeklyTargetKm === 52) state.athleteState.weeklyTargetKm = 45;
if (state.races[0].name === '上海半程马拉松') state.races[0].name = '海南半程马拉松';
if (state.races[0].date === '2026-10-18') state.races[0].date = '2026-10-26';
if (state.races[0].targetTime === '1:38:00') state.races[0].targetTime = '1:40:00';
if (state.races[0].targetPace === `4'39"/km`) state.races[0].targetPace = `4'44"/km`;
const thresholdRepCount = state.todayWorkout?.segments?.filter((segment) => segment.name?.startsWith('阈值')).length;
if (thresholdRepCount === 3) state.todayWorkout = clone(demoData.todayWorkout);
const recoveryOption = state.adjustments?.find((item) => item.id === 'easy-recovery-option');
if (recoveryOption?.proposed?.distanceKm === 6) {
  recoveryOption.title = '可接受：将周日轻松恢复跑调整为 7 km';
  recoveryOption.proposed = clone(demoData.adjustments[0].proposed);
}
const adjustedSunday = state.weekPlan?.find((item) => item.id === 'sun-0816' && item.adjusted);
if (adjustedSunday?.distanceKm === 6) {
  adjustedSunday.distanceKm = 7;
  adjustedSunday.durationMin = 45;
}
let planView = 'week';
let analyticsRange = '8w';
let onboardingStep = 0;
let activeTimer;
let activeSeconds = 18 * 60 + 42;
let activeRunning = true;
let abortCoach;

const save = () => localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
save();
const route = () => (location.hash.replace(/^#/, '') || '/').split('?')[0];
const go = (path) => { location.hash = path; };
const daysUntilRace = () => Math.max(0, Math.ceil((new Date(`${state.races[0].date}T00:00:00+08:00`) - new Date('2026-08-13T00:00:00+08:00')) / 86400000));
const raceDateZh = () => {
  const [year, month, day] = state.races[0].date.split('-').map(Number);
  return `${year} 年 ${month} 月 ${day} 日`;
};
const toast = (message) => {
  const el = document.createElement('div'); el.className = 'toast'; el.textContent = message;
  document.querySelector('#toast-region').append(el); setTimeout(() => el.remove(), 2800);
};
const icon = (name) => {
  const paths = {
    home:'<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10.5V20h13v-9.5"/><path d="M9 20v-6h6v6"/>',
    calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>',
    run:'<circle cx="13" cy="4" r="2"/><path d="m10 8 3 2 2 4 4 2M13 10l-3 5-4 2M10 15l3 5M15 14l-3 1"/>',
    chart:'<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    coach:'<path d="M4 5h16v12H8l-4 4z"/><path d="M8 9h8M8 13h5"/>',
    flag:'<path d="M5 21V4M5 5h11l-2 4 2 4H5"/>',
    user:'<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    arrow:'<path d="m9 18 6-6-6-6"/>',
    play:'<path d="m8 5 11 7-11 7z"/>',
    check:'<path d="m5 12 4 4L19 6"/>',
    spark:'<path d="m12 3 1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5z"/><path d="m19 16 .7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7z"/>',
    menu:'<path d="M4 7h16M4 12h16M4 17h16"/>',
  };
  return `<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.arrow}</svg>`;
};
const navItems = [
  ['/', 'home', '首页'], ['/plan', 'calendar', '计划'], ['/workout/today', 'run', '训练'], ['/analytics', 'chart', '数据'],
  ['/coach', 'coach', 'AI 教练'], ['/race', 'flag', '比赛'], ['/profile', 'user', '我的'],
];
const currentNav = (href) => href === '/' ? route() === '/' : route().startsWith(href.split('/')[1] === 'workout' ? '/workout' : href);
const shell = (content) => {
  const date = new Intl.DateTimeFormat('zh-CN', { month:'long', day:'numeric', weekday:'long' }).format(new Date('2026-08-13'));
  return `<div class="shell">
    <aside class="sidebar"><a href="#/" class="brand"><span class="brand-mark"></span>STRIDE</a><div class="brand-sub">INTELLIGENT RUNNING</div>
      <nav class="nav" aria-label="主导航">${navItems.map(([href,ic,label]) => `<a href="#${href}" class="${currentNav(href)?'active':''}">${icon(ic)}<span>${label}</span></a>`).join('')}</nav>
      <div class="side-goal"><p class="eyebrow">A RACE TO RUN</p><div class="goal-name">${escapeHtml(state.races[0].name)}</div><div class="goal-count">${daysUntilRace()} DAYS</div><div class="small muted">目标 ${escapeHtml(state.goal.targetTime)}</div></div>
    </aside>
    <header class="topbar"><a class="mobile-brand" href="#/"><span class="brand-mark"></span>STRIDE</a><div class="today-label">${date} · 训练周期第 8 周</div><div class="top-actions"><a class="status-pill" href="#/profile"><span class="status-dot"></span><span>${state.athleteState.safetyMode?'安全模式':'恢复 '+state.athleteState.recoveryScore+' · '+escapeHtml(state.athleteState.recoveryLabel)}</span></a><a class="avatar" aria-label="打开个人档案" href="#/profile">${escapeHtml(state.athlete.avatarInitials || '林')}</a></div></header>
    <main id="app-main">${content}</main>
    <nav class="bottom-nav" aria-label="移动端导航">${navItems.slice(0,5).map(([href,ic,label]) => `<a href="#${href}" class="${currentNav(href)?'active':''}">${icon(ic)}<span>${label}</span></a>`).join('')}</nav>
  </div>`;
};
const pageHeader = (eyebrow, title, subtitle, action='') => `<header class="page-header"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1><div class="muted">${subtitle}</div></div>${action}</header>`;
const tag = (type, text, extra='') => `<span class="tag ${String(type).toLowerCase()} ${extra}">${text}</span>`;
const metric = (value,label,delta='') => `<div class="card"><div class="metric-value">${value}</div><div class="metric-label">${label}</div>${delta?`<div class="metric-delta">${delta}</div>`:''}</div>`;

function dashboard() {
  const completion = calculateCompletionRate(state.weekPlan);
  const safety = state.athleteState.safetyMode;
  const completedToday = state.weekPlan.find((item)=>item.workoutId===state.todayWorkout.id)?.status==='completed';
  const trainingDays = state.weekPlan.filter((item)=>item.status!=='rest');
  const completedCount = trainingDays.filter((item)=>item.status==='completed').length;
  const remainingKm = Math.max(0,state.athleteState.weeklyTargetKm-state.athleteState.weeklyDistanceKm).toFixed(1);
  const insights = state.insights.slice(0,3).map((item)=>{
    if(item.id==='risk' && safety) return {...item,title:'疼痛风险已触发安全计划',body:'今日高强度入口已暂停；周末计划已降级为无痛恢复与观察。'};
    if(item.id==='risk' && completedToday) return {...item,title:'今日阈值课已完成',body:`已记录 ${state.workoutActuals?.[state.todayWorkout.id]?.distanceKm||13.08} km；先依据反馈恢复，不额外加练。`};
    if(item.id==='load') return {...item,title:`本周还差 ${remainingKm} km`,body:safety?'周末跑量目标让位于疼痛管理；不需要为凑里程补跑。':completedToday?'今日关键课已计入跑量；周末按恢复状态执行，不为凑里程加码。':'周末长跑是完成本周跑量目标的关键，不建议把今天的阈值段额外加长。'};
    return item;
  });
  return shell(`<div class="page">
    ${pageHeader('THURSDAY · BUILD 08', `早上好，${escapeHtml(state.athlete.name)}`, safety?'已识别疼痛风险：今天不执行高强度训练。':'今天要做的事很明确：稳稳跑完，不加码。', `<a class="btn goal-link" href="#/race"><span>目标赛 · ${daysUntilRace()} 天</span>${icon('arrow')}</a>`)}
    ${safety?'<div class="safety-banner" style="margin-bottom:18px"><strong>安全模式已开启</strong><br>周末计划已改为无痛恢复与观察；在疼痛消退或完成专业评估前，不恢复高强度。</div>':''}
    <div class="grid dashboard-grid"><div class="stack">
      <article class="card dark hero-workout"><div class="track-line" aria-label="本周训练轨道：已完成、今日、下一步"><span class="track-node done">✓</span><span class="track-node today">今</span><span class="track-node next">六</span></div>
        <div><div class="card-head">${tag('threshold','TODAY · 阈值')}<span class="small muted">预计 67 分钟</span></div><p class="eyebrow">PRIMARY SESSION</p><h2 class="workout-title">13.2 KM<br>阈值跑</h2><div class="workout-meta"><span>目标配速 <b>${state.todayWorkout.targetPace}</b></span><span>主观强度 <b>RPE ${state.todayWorkout.targetRpe}</b></span></div></div>
        <div class="hero-actions">${safety?'<a class="btn primary" href="#/plan">查看安全调整 '+icon('arrow')+'</a>':completedToday?'<a class="btn primary" href="#/workout/today/review">查看训练复盘 '+icon('arrow')+'</a>':`<a class="btn primary" href="#/workout/today">查看训练结构 ${icon('arrow')}</a><a class="btn ghost" href="#/workout/today/active">${icon('play')} 直接开始</a>`}</div>
      </article>
      <div class="grid grid-3 metric-grid">${metric(`${state.athleteState.weeklyDistanceKm}<small> km</small>`,'本周跑量','较上周同期 +2.4 km')}${metric(`${completion}<small>%</small>`,'训练完成率',`${completedCount} / ${trainingDays.length} 项已完成`)}${metric(`6:48<small> h</small>`,'训练时间','强度分布合理')}</div>
      <section class="card"><div class="card-head"><div><p class="eyebrow">COACH SIGNALS</p><h2>值得注意</h2></div><a class="small" href="#/analytics">查看全部 →</a></div>
        ${insights.map((item,i)=>`<div class="insight"><span class="insight-icon">${['↗','◫','+'][i]}</span><div><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.body)}</p></div></div>`).join('')}
      </section>
    </div><aside class="stack">
      <section class="card"><div class="card-head"><div><p class="eyebrow">READINESS</p><h3>今日恢复</h3></div>${tag(safety?'interval':'easy',safety?'疼痛风险':'适合训练')}</div><div class="recovery"><div class="ring" style="--value:${state.athleteState.recoveryScore}"><div><strong>${state.athleteState.recoveryScore}</strong><small>恢复分</small></div></div><div><p><strong>${state.athleteState.sleepHours} h</strong><br><span class="small muted">睡眠</span></p><p><strong>${state.athleteState.restingHeartRate} bpm</strong><br><span class="small muted">静息心率</span></p></div></div></section>
      <section class="card accent"><p class="eyebrow">THIS WEEK</p><h3>${state.athleteState.weeklyDistanceKm} / ${state.athleteState.weeklyTargetKm} km</h3><div class="progress"><span style="width:${Math.min(100,Math.round(state.athleteState.weeklyDistanceKm/state.athleteState.weeklyTargetKm*100))}%"></span></div><p class="small" style="margin:14px 0 0">${safety?'安全优先，本周不为跑量目标补课。':completedToday?'今日关键课已计入；周末根据恢复状态执行。':'周末长跑将是达成跑量目标的关键。'}</p></section>
      <section class="card"><p class="eyebrow">NEXT MILESTONE</p><h3>${escapeHtml(state.races[0].name)}</h3><div class="goal-count" style="color:var(--ink)">10.26</div><p class="muted small">目标 ${escapeHtml(state.goal.targetTime)} · ${escapeHtml(state.goal.targetPace)}</p><a href="#/race" class="btn" style="width:100%">查看比赛项目</a></section>
    </aside></div>
  </div>`);
}

const dayType = (type) => ({Easy:'轻松',Threshold:'阈值',Strength:'力量',Rest:'休息',Long:'长距离',Recovery:'恢复'}[type] || type);
function plan() {
  const adjusted = state.weekPlan.find(x=>x.adjusted);
  let body = '';
  if (planView === 'week') body = `<div class="week-strip">${state.weekPlan.map((d,i)=>`<a href="${d.status==='today'?'#/workout/today':'#/plan'}" class="day-card ${d.status}" aria-label="${d.weekday}，${d.title}${d.status==='today'?'，打开训练详情':''}"><div class="day-date"><span>${d.weekday}</span><span>${10+i}</span></div><div class="day-name">${d.status==='completed'?'✓ ':''}${d.title}</div>${tag(d.type,dayType(d.type),d.adjusted?'adjusted':'')}<div class="day-km" style="margin-top:14px">${d.distanceKm ? d.distanceKm+' km' : d.durationMin ? d.durationMin+' min':'—'}</div>${d.adjusted?'<div class="small" style="margin-top:8px;color:#567800">AI 已调整</div>':''}</a>`).join('')}</div>`;
  if (planView === 'month') body = `<div class="month-grid">${Array.from({length:35},(_,i)=>{const day=i+1; const run=[1,3,5,7,10,12,13,15,16,18,20,22,24,26,28,30].includes(day); return `<div class="month-day ${run?'has-run':''}" style="--type:${day===13?'var(--threshold)':'var(--easy)'}"><strong>${day<=31?day:''}</strong>${day<=31&&run?(day===13?'阈值跑':'轻松跑'):''}</div>`}).join('')}</div>`;
  if (planView === 'phase') body = `<div class="phase-list">${state.trainingStructure.map((p,i)=>`<div class="phase ${p.status==='current'?'active':''}"><div><strong>${p.phase}</strong><div class="small muted">${p.dates}</div></div><div><div class="small">重点 · ${p.focus}</div><div class="phase-bar" style="margin-top:9px"><span style="width:${p.status==='completed'?100:p.status==='current'?48:0}%"></span></div></div>${tag(p.status==='current'?'adjusted':'rest',p.status==='completed'?'已完成':p.status==='current'?'进行中':'待开始')}</div>`).join('')}</div>`;
  return shell(`<div class="page">${pageHeader('TRAINING ARCHITECTURE','训练计划','每一次调整，都保留原计划和理由。',`<button class="btn" data-action="reset-demo">恢复演示数据</button>`)}
    <div class="card"><div class="card-head"><div class="tabs" role="tablist" aria-label="计划视图"><button role="tab" aria-selected="${planView==='week'}" class="tab ${planView==='week'?'active':''}" data-plan="week">周</button><button role="tab" aria-selected="${planView==='month'}" class="tab ${planView==='month'?'active':''}" data-plan="month">月</button><button role="tab" aria-selected="${planView==='phase'}" class="tab ${planView==='phase'?'active':''}" data-plan="phase">周期</button></div><span class="small muted">第 8 周 · 专项耐力</span></div>${body}</div>
    ${adjusted?`<section class="card" style="margin-top:18px"><div class="card-head"><div><p class="eyebrow">ACCEPTED CHANGE</p><h2>计划调整已生效</h2></div>${tag('adjusted','已同步')}</div><div class="compare"><div class="compare-card muted"><div class="small">原计划</div><strong>${adjusted.previousTitle || '8 km 轻松恢复跑'}</strong></div><div class="compare-arrow">→</div><div class="compare-card"><div class="small">当前计划</div><strong>${adjusted.title} · ${adjusted.distanceKm} km</strong></div></div><p class="chart-summary">${adjusted.adjustmentReason}</p></section>`:''}
  </div>`);
}

function workoutDetail() {
  const safety = state.athleteState.safetyMode;
  const completed = state.weekPlan.find((item)=>item.workoutId===state.todayWorkout.id)?.status==='completed';
  return shell(`<div class="page">${pageHeader('TODAY · THRESHOLD','13.2 km 阈值跑','预计 67 分钟 · 目标 RPE 7',safety?'<a class="btn" href="#/plan">已暂停 · 查看调整</a>':completed?'<a class="btn primary" href="#/workout/today/review">已完成 · 查看复盘</a>':`<a class="btn primary" href="#/workout/today/active">${icon('play')} 开始训练</a>`)}${safety?'<div class="safety-banner" style="margin-bottom:18px"><strong>高强度训练已暂停</strong><br>当前存在疼痛风险标记，请先执行计划中的无痛恢复与观察。</div>':''}
    <div class="grid dashboard-grid"><section class="card"><div class="card-head"><div><p class="eyebrow">SESSION PRESCRIPTION</p><h2>训练结构</h2></div>${tag('threshold','关键课')}</div><div class="timeline">${state.todayWorkout.segments.map((s,i)=>`<div class="step ${s.name.includes('阈值')?'key':''}"><span class="step-dot">${i+1}</span><div><strong>${s.name}</strong><p>${s.instruction}</p></div><b class="mono">${s.distanceKm} km</b></div>`).join('')}</div></section>
      <aside class="stack"><section class="card accent"><p class="eyebrow">OBJECTIVE</p><h3>今天为什么这样练</h3><p>${state.todayWorkout.objective}</p></section><section class="card"><h3>执行提示</h3><div class="insight"><span class="insight-icon">1</span><div><strong>配速是上限，不是命令</strong><p>逆风或起伏路段以呼吸和 RPE 为准。</p></div></div><div class="insight"><span class="insight-icon">2</span><div><strong>最后一组不冲刺</strong><p>结束时应感觉还能维持约 5 分钟。</p></div></div></section>${safety?'<a class="btn dark" href="#/plan">查看安全计划 '+icon('arrow')+'</a>':completed?'<a class="btn dark" href="#/workout/today/review">查看训练复盘 '+icon('arrow')+'</a>':`<a class="btn dark" href="#/workout/today/active">进入训练模式 ${icon('arrow')}</a>`}</aside>
    </div></div>`);
}

function activeWorkout() {
  if (state.athleteState.safetyMode) return shell('<div class="page"><div class="safety-banner"><strong>训练未开始</strong><br>疼痛安全模式已开启，高强度训练入口已暂停。请返回计划查看无痛恢复安排。</div><a class="btn dark" style="margin-top:18px" href="#/plan">返回计划</a></div>');
  if (state.weekPlan.find((item)=>item.workoutId===state.todayWorkout.id)?.status==='completed') return shell('<div class="page"><div class="card empty"><div class="empty-mark">✓</div><h2>本次训练已经完成</h2><p class="muted">训练数据已记录，不会重复计入本周跑量。</p><a class="btn dark" href="#/workout/today/review">查看复盘</a></div></div>');
  const mm=String(Math.floor(activeSeconds/60)).padStart(2,'0'), ss=String(activeSeconds%60).padStart(2,'0');
  return shell(`<section class="active-screen"><div class="active-top"><a class="btn ghost" href="#/workout/today">← 退出</a><div>${tag('threshold','阈值 1 · 1 / 4')}</div><button class="btn ghost" data-action="toggle-active">${activeRunning?'暂停':'继续'}</button></div><div class="active-center"><div><p class="eyebrow" style="color:#9da8a0">CURRENT PACE</p><div class="active-number">4'34"</div><div class="active-unit">每公里 · 目标 4'32"–4'38"</div></div></div><div><div class="active-stats"><div class="active-stat"><strong>${mm}:${ss}</strong><small>用时</small></div><div class="active-stat"><strong>4.08</strong><small>公里</small></div><div class="active-stat"><strong>161</strong><small>心率 bpm</small></div></div><div class="next-stage"><span>本组进度 <strong>1.58 / 2.00 km</strong></span><span>下一阶段 · 慢跑恢复 0.4 km</span></div><div class="hero-actions" style="margin-top:18px;justify-content:center"><a class="btn primary" href="#/workout/today/feedback">结束并记录</a></div></div></section>`);
}

function feedback() {
  if (state.athleteState.safetyMode) return shell('<div class="page"><div class="safety-banner"><strong>本次训练未开放记录</strong><br>疼痛安全模式已开启，高强度训练已暂停，不能通过直接链接补录为已完成。</div><a class="btn dark" style="margin-top:18px" href="#/plan">查看安全计划</a></div>');
  if (state.weekPlan.find((item)=>item.workoutId===state.todayWorkout.id)?.status==='completed') return shell('<div class="page"><div class="card empty"><div class="empty-mark">✓</div><h2>本次训练已经记录</h2><p class="muted">原始训练与反馈会保留，不能通过直接链接覆盖。</p><a class="btn dark" href="#/workout/today/review">查看复盘</a></div></div>');
  const f=state.feedback||{};
  return shell(`<div class="page">${pageHeader('POST RUN CHECK-IN','这次训练感觉如何？','用 30 秒记录真实感受，复盘会据此调整下一步。')}<form id="feedback-form" class="grid dashboard-grid"><section class="card form-grid"><fieldset><legend>主观强度 RPE</legend><p class="small muted">1 很轻松 · 10 已到极限</p><div class="choice-row">${[1,2,3,4,5,6,7,8,9,10].map(n=>`<label class="choice"><input type="radio" name="rpe" value="${n}" ${Number(f.rpe||7)===n?'checked':''}><span>${n}</span></label>`).join('')}</div></fieldset><fieldset><legend>腿部状态</legend><div class="choice-row">${[['fresh','轻快'],['normal','正常'],['heavy','偏沉'],['sore','酸痛']].map(([v,t])=>`<label class="choice"><input type="radio" name="legs" value="${v}" ${(f.legs||'normal')===v?'checked':''}><span>${t}</span></label>`).join('')}</div></fieldset><fieldset><legend>疼痛评分</legend><p class="small muted">0 无疼痛；4 分及以上会触发安全模式</p><div class="choice-row">${[0,1,2,3,4,5,6,7,8,9,10].map(n=>`<label class="choice"><input type="radio" name="pain" value="${n}" ${Number(f.pain||0)===n?'checked':''}><span>${n}</span></label>`).join('')}</div></fieldset><div class="field"><label for="feedback-note">训练备注（选填）</label><textarea id="feedback-note" name="note" placeholder="例如：第三组逆风，后半程呼吸仍可控。">${escapeHtml(f.note||'')}</textarea></div><button class="btn dark" type="submit">生成训练复盘 ${icon('arrow')}</button></section><aside class="stack"><section class="card dark"><p class="eyebrow">RECORDED</p><h2>13.08 km</h2><div class="grid grid-2"><div><strong class="mono">1:06:18</strong><div class="small muted">总时间</div></div><div><strong class="mono">5'04"</strong><div class="small muted">平均配速</div></div><div><strong class="mono">158</strong><div class="small muted">平均心率</div></div><div><strong class="mono">4'33"</strong><div class="small muted">阈值段均配</div></div></div></section><section class="card"><h3>疼痛提示</h3><p class="small muted">尖锐、持续或改变步态的疼痛不属于正常训练反应。请如实评分。</p></section></aside></form></div>`);
}

function review() {
  if (!state.feedback) return shell(`<div class="page"><div class="card empty"><div class="empty-mark">?</div><h2>还没有训练反馈</h2><p class="muted">先记录 RPE、腿部状态和疼痛，再生成可靠复盘。</p><a class="btn dark" href="#/workout/today/feedback">填写反馈</a></div></div>`);
  const safety=isSafetyMode(state.feedback.pain);
  const score=deriveReview(state.feedback,state.todayWorkout.targetRpe);
  const adjustment=state.adjustments[0]; const accepted=state.weekPlan.some(x=>x.adjusted);
  return shell(`<div class="page">${pageHeader('SESSION REVIEW','训练完成得很扎实',safety?'已进入安全模式：恢复优先。':'阈值段稳定，强度控制在计划范围内。',`<a class="btn" href="#/analytics">查看趋势</a>`)}${safety?`<div class="safety-banner" style="margin-bottom:18px"><strong>暂停高强度训练</strong><br>疼痛评分为 ${state.feedback.pain}。不建议提高强度；如疼痛持续、加重或改变步态，请停止跑步并寻求专业评估。</div>`:''}
    <section class="card"><div class="score-board"><div><p class="eyebrow">WORKOUT SCORE</p><div class="score-total">${score.total}</div><span class="muted">/ 100 · ${score.total>=85?'优秀':'完成'}</span></div><div class="score-bars">${[['完成度',score.completion],['强度控制',score.intensity],['配速稳定',score.stability],['恢复影响',score.recovery]].map(([l,v])=>`<div class="score-row"><span>${l}</span><div class="progress"><span style="width:${v}%;background:${v<60?'var(--interval)':'var(--accent)'}"></span></div><b>${v}</b></div>`).join('')}</div></div></section>
    <div class="grid grid-2" style="margin-top:18px"><section class="card"><p class="eyebrow">COACH JUDGEMENT</p><h2>${safety?'先处理疼痛信号':score.adjustmentEligible?'训练完成，恢复信号需照顾':'强度到位，没有透支'}</h2><p>${safety?'本次数据只用于识别风险，不应据此继续增加负荷。后续恢复跑也应以无痛为前提。':`4 组阈值均配 4'33"/km；RPE ${state.feedback.rpe}，腿部状态 ${dayType(state.feedback.legs)==state.feedback.legs?({'fresh':'轻快','normal':'正常','heavy':'偏沉','sore':'酸痛'}[state.feedback.legs]||state.feedback.legs):dayType(state.feedback.legs)}。${score.adjustmentEligible?'恢复信号支持降低下一次跑量。':'当前反馈不需要额外降低计划。'}`}</p>${tag('adjusted','教练判断')}<p class="chart-summary">事实依据：手表训练记录 + 你的训练反馈。${state.feedback.note?`备注：${escapeHtml(state.feedback.note)}。`:''}文献依据由 AI 教练对话单独提供。</p></section><section class="card"><p class="eyebrow">NEXT DECISION</p><h2>${safety?'周末计划已安全降级':score.adjustmentEligible?'是否微调周日恢复跑？':'维持原计划'}</h2><div class="compare"><div class="compare-card"><div class="small muted">原计划</div><strong>8 km 轻松跑</strong><div class="small">约 50 分钟</div></div><div class="compare-arrow">→</div><div class="compare-card"><div class="small muted">建议</div><strong>${safety?'休息与疼痛观察':score.adjustmentEligible?'7 km 恢复跑':'8 km 轻松跑'}</strong><div class="small">${safety?'已同步安全计划':score.adjustmentEligible?'约 45 分钟':'无需调整'}</div></div></div><p class="chart-summary">${safety?'安全原因：疼痛评分达到 4 分阈值，周末强度已直接降级。':score.adjustmentEligible?'原因：RPE、腿部状态或轻度疼痛提示应保留恢复质量。':'当前反馈与计划目标一致，维持原安排。'}</p>${safety?'<a class="btn" style="width:100%" href="#/plan">查看安全计划</a>':score.adjustmentEligible?(accepted?`<button class="btn" disabled style="width:100%">已接受并同步计划</button>`:`<button class="btn primary" data-action="accept-adjustment" style="width:100%">接受调整并同步计划</button>`):'<button class="btn" disabled style="width:100%">计划保持不变</button>'}</section></div><div style="margin-top:18px"><a class="btn dark" href="#/">完成复盘，回到首页</a></div></div>`);
}

function lineChart(values, labels, second=[]) {
  const w=620,h=220,p=28,max=Math.max(...values,...second,1),min=Math.min(...values,...second,max*.7); const x=i=>p+i*(w-2*p)/(values.length-1); const y=v=>h-p-(v-min)/(max-min||1)*(h-2*p); const points=arr=>arr.map((v,i)=>`${x(i)},${y(v)}`).join(' ');
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="趋势图">${[0,1,2,3].map(i=>`<line class="chart-grid" x1="${p}" y1="${p+i*55}" x2="${w-p}" y2="${p+i*55}"/>`).join('')}<polyline class="chart-line" points="${points(values)}"/>${second.length?`<polyline class="chart-accent" points="${points(second)}"/>`:''}${values.map((v,i)=>`<circle cx="${x(i)}" cy="${y(v)}" r="4" fill="#18201B"/><text x="${x(i)}" y="${h-5}" text-anchor="middle">${labels[i]}</text>`).join('')}</svg>`;
}
function analytics() {
  let weekly=state.analytics.weekly;
  if(analyticsRange==='4w') weekly=weekly.slice(-4);
  if(analyticsRange==='12w') weekly=[{week:'05/25',distanceKm:34,intensityPct:17,efficiency:.88},{week:'06/01',distanceKm:36,intensityPct:18,efficiency:.89},{week:'06/08',distanceKm:39,intensityPct:19,efficiency:.90},{week:'06/15',distanceKm:35,intensityPct:15,efficiency:.90},...weekly];
  const dist=state.analytics.intensityDistribution; let angle=0; const stops=dist.map(x=>{const start=angle;angle+=x.percent;return `${x.color} ${start}% ${angle}%`}).join(',');
  const completedToday=state.weekPlan.find((item)=>item.workoutId===state.todayWorkout.id)?.status==='completed';
  const safety=state.athleteState.safetyMode;
  const volumeSummary=safety?`当前 ${state.athleteState.weeklyDistanceKm} km；疼痛风险已触发，本周目标让位于安全恢复。`:`降载周后跑量恢复到 52 km；${completedToday?'今日关键课已经完成，周末训练尚待执行':'今日关键课和周末训练尚待执行'}，当前 ${state.athleteState.weeklyDistanceKm} km 不代表趋势下降。`;
  return shell(`<div class="page">${pageHeader('TRAINING INTELLIGENCE','数据不是终点','看清趋势，然后做一个更好的训练决定。',`<div class="tabs" role="tablist" aria-label="数据范围">${[['4w','4 周'],['8w','8 周'],['12w','12 周']].map(([v,l])=>`<button role="tab" aria-selected="${analyticsRange===v}" class="tab ${analyticsRange===v?'active':''}" data-range="${v}">${l}</button>`).join('')}</div>`)}
    <div class="grid grid-4 metric-grid">${metric('48.0<small> km</small>','周均跑量','过去周期 +11%')}${metric('88<small>%</small>','计划完成率','稳定在目标区间')}${metric('80 / 20','低强度 / 高强度','分布合理')}${metric('1.02','有氧效率指数','连续 3 周改善')}</div>
    <div class="grid grid-2" style="margin-top:18px"><section class="card"><div class="card-head"><div><p class="eyebrow">VOLUME</p><h2>${analyticsRange==='4w'?4:analyticsRange==='12w'?12:8} 周跑量</h2></div>${tag('easy','稳定增长')}</div>${lineChart(weekly.map(x=>x.distanceKm),weekly.map(x=>x.week))}<p class="chart-summary">${volumeSummary}</p></section>
    <section class="card"><div class="card-head"><div><p class="eyebrow">INTENSITY MIX</p><h2>强度分布</h2></div></div><div style="display:grid;grid-template-columns:minmax(120px,190px) 1fr;gap:28px;align-items:center"><div style="aspect-ratio:1;border-radius:50%;background:conic-gradient(${stops});position:relative"><div style="position:absolute;inset:30%;border-radius:50%;background:white;display:grid;place-items:center;text-align:center"><strong>80%</strong><small>低强度</small></div></div><div>${dist.map(x=>`<div class="insight"><span style="width:10px;height:10px;border-radius:50%;background:${x.color};margin-top:6px"></span><div><strong>${x.label} ${x.percent}%</strong><p>${x.type}</p></div></div>`).join('')}</div></div><p class="chart-summary">高强度占比 20%，没有超过本周期的建议上限。</p></section>
    <section class="card"><div class="card-head"><div><p class="eyebrow">EFFICIENCY</p><h2>配速 × 心率效率</h2></div>${tag('adjusted','改善')}</div>${lineChart(weekly.map(x=>x.efficiency*100),weekly.map(x=>x.week))}<p class="chart-summary">相近心率下，轻松跑配速约提升 7 秒/km。趋势需继续观察，不因此提高本周强度。</p></section>
    <section class="card dark"><p class="eyebrow">COACH ANALYSIS</p><h2>${safety?'先把疼痛风险降下来。':'本周期正在按预期工作。'}</h2><p>${safety?'本周决策从完成跑量改为无痛恢复与观察，不安排补跑。':'跑量增长、强度比例和效率趋势方向一致。下一步不是跑得更快，而是按恢复状态完成周末计划。'}</p><div class="insight" style="border-color:#3c463f"><span class="insight-icon">1</span><div><strong>风险</strong><p>${safety?'当前有疼痛风险标记，强度计划已降级。':'目前没有明显负荷风险信号。'}</p></div></div><div class="insight" style="border-color:#3c463f"><span class="insight-icon">2</span><div><strong>${safety?'决策':'偏差'}</strong><p>${safety?'不以本周跑量差额驱动训练。':`本周跑量仍差 ${Math.max(0,state.athleteState.weeklyTargetKm-state.athleteState.weeklyDistanceKm).toFixed(1)} km。`}</p></div></div><a class="btn primary" href="#/coach">问 AI 教练如何安排</a></section></div></div>`);
}

const sourceLabels = (sources) => (Array.isArray(sources) ? sources : [])
  .filter((item) => item && item.title && item.page)
  .map((item) => `《${item.title}》p${item.page}`);

const renderSources = (sources) => {
  const labels = sourceLabels(sources).map(escapeHtml);
  return labels.length ? `<span class="source">参考来源：${labels.join('；')}</span>` : '';
};

function coach() {
  state.chat ||= [];
  const messages=state.chat.length?state.chat:`<div class="empty" id="chat-empty"><div class="empty-mark">✦</div><h2>把训练问题说具体一点</h2><p class="muted">我会结合你的训练档案；知识依据来自已连接的 RAG 服务。</p></div>`;
  return shell(`<div class="page">${pageHeader('EVIDENCE + CONTEXT','AI 教练','训练计划独立可用；知识回答只来自真实连接。')}<section class="coach-layout"><aside class="coach-aside"><p class="eyebrow">QUICK QUESTIONS</p><h3>从一个问题开始</h3><div class="prompt-list">${['今天的阈值跑为什么安排 4 组？','疼痛和普通酸痛怎么区分？','半马比赛前多久开始减量？','轻松跑心率偏高怎么办？','长跑中如何安排补给？','为什么不建议今天加练？'].map(q=>`<button class="prompt" data-prompt="${q}">${q}</button>`).join('')}</div></aside><div class="chat"><div class="chat-head"><div><strong>STRIDE Coach</strong><div class="small muted">基于档案 + 跑步知识库</div></div><span class="tag" id="coach-status"><span class="status-dot"></span>连接时检测</span></div><div class="chat-messages" id="chat-messages">${typeof messages==='string'?messages:messages.map(m=>`<div class="bubble ${m.role} ${m.error?'error':''}">${escapeHtml(m.content)}${renderSources(m.sources) || (m.source?`<span class="source">${escapeHtml(m.source)}</span>`:'')}</div>`).join('')}</div><form class="chat-form" id="coach-form"><label class="skip-link" for="coach-input">给教练留言</label><input id="coach-input" name="query" autocomplete="off" placeholder="例如：今天跑完后腿有点沉，周日怎么安排？"><button class="btn dark" type="submit">发送</button></form></div></section></div>`);
}

function race() {
  const race=state.races[0]; race.checked ||= [];
  const distanceKm=Number(race.distanceKm)||getGoalDistanceKm(state.goal.type);
  const distanceLabel=distanceKm===21.0975?'21.1':String(distanceKm);
  const [,month,day]=race.date.split('-');
  return shell(`<div class="page">${pageHeader('RACE PROJECT','一个目标，一套系统','从今天的训练，一直连接到比赛日。')}<section class="card race-hero"><div class="grid grid-2"><div><p class="eyebrow">A RACE · ${escapeHtml(month)}.${escapeHtml(day)}</p><h2>${escapeHtml(race.name)}</h2><div class="race-distance">${escapeHtml(distanceLabel)}</div><div class="muted">KILOMETRES</div></div><div style="align-self:end"><div class="grid grid-2"><div><div class="small muted">目标成绩</div><strong class="metric-value">${escapeHtml(race.targetTime)}</strong></div><div><div class="small muted">目标配速</div><strong class="metric-value" style="font-size:28px">${escapeHtml(race.targetPace)}</strong></div></div><div class="progress" style="margin-top:28px"><span style="width:${race.readiness}%"></span></div><p class="small muted" style="margin-top:8px">比赛准备度 ${race.readiness}% · 距离比赛 ${daysUntilRace()} 天</p></div></div></section><div class="grid grid-2" style="margin-top:18px"><section class="card"><p class="eyebrow">CURRENT PHASE</p><h2>${escapeHtml(race.phase)}</h2><div class="phase-list">${state.trainingStructure.map(p=>`<div class="phase ${p.status==='current'?'active':''}"><div><strong>${escapeHtml(p.phase)}</strong><div class="small muted">${escapeHtml(p.dates)}</div></div><span class="small">${escapeHtml(p.focus)}</span>${p.status==='current'?tag('adjusted','现在'):''}</div>`).join('')}</div></section><section class="card"><p class="eyebrow">RACE WEEK</p><h2>比赛周清单</h2><div class="checklist">${race.checklist.map((x,i)=>`<label class="check-item"><input type="checkbox" data-race-check="${i}" ${race.checked.includes(i)?'checked':''}><span>${escapeHtml(x)}</span></label>`).join('')}<label class="check-item"><input type="checkbox" data-race-check="3" ${race.checked.includes(3)?'checked':''}><span>确认号码布、交通与存包时间</span></label><label class="check-item"><input type="checkbox" data-race-check="4" ${race.checked.includes(4)?'checked':''}><span>准备比赛鞋、能量胶与防磨用品</span></label></div><p class="chart-summary">完成状态会保存在这台设备上。</p></section></div></div>`);
}

function profile() {
  const a=state.athlete;
  return shell(`<div class="page">${pageHeader('ATHLETE FILE','运动员档案','这些长期信息会影响计划和教练判断。',`<button class="btn danger" data-action="reset-demo">恢复演示数据</button>`)}<div class="grid dashboard-grid"><section class="card"><div class="profile-summary"><div class="profile-avatar">${escapeHtml(a.avatarInitials)}</div><div><h2 style="margin-bottom:4px">${escapeHtml(a.name)}</h2><div class="muted">${escapeHtml(a.level)} · ${escapeHtml(a.city)}</div></div></div><hr style="border:0;border-top:1px solid var(--line);margin:24px 0"><form id="profile-form" class="form-grid"><div class="grid grid-2"><div class="field"><label for="profile-name">姓名</label><input id="profile-name" name="name" value="${escapeHtml(a.name)}" required></div><div class="field"><label for="profile-city">常住城市</label><input id="profile-city" name="city" value="${escapeHtml(a.city)}"></div><div class="field"><label for="profile-level">跑步水平</label><select id="profile-level" name="level"><option ${a.level==='进阶跑者'?'selected':''}>进阶跑者</option><option ${a.level==='初级跑者'?'selected':''}>初级跑者</option><option ${a.level==='竞技跑者'?'selected':''}>竞技跑者</option></select></div><div class="field"><label for="profile-time">常用训练时间</label><select id="profile-time" name="time"><option ${a.preferredTrainingTime==='清晨'?'selected':''}>清晨</option><option ${a.preferredTrainingTime==='晚间'?'selected':''}>晚间</option></select></div><div class="field"><label for="profile-10k">10K 最好成绩</label><input id="profile-10k" name="tenK" value="${escapeHtml(a.tenKPersonalBest)}"></div><div class="field"><label for="profile-vdot">VDOT</label><input id="profile-vdot" name="vdot" type="number" value="${a.vdot || 44}"></div><div class="field"><label for="profile-half">半马最好成绩</label><input id="profile-half" name="half" value="${escapeHtml(a.halfMarathonPersonalBest)}"></div></div><button class="btn dark" type="submit">保存档案</button></form></section><aside class="stack"><section class="card accent"><p class="eyebrow">PRIMARY GOAL</p><h2>${escapeHtml(state.goal.type)} · ${escapeHtml(state.goal.targetTime)}</h2><p>${escapeHtml(state.races[0].name)} · ${raceDateZh()}</p><a class="btn dark" href="#/race">管理比赛</a></section><section class="card"><h3>数据与隐私</h3><p class="small muted">演示版会把完整演示状态（档案、训练、反馈、计划调整、比赛清单和对话记录）保存在当前浏览器的 <code>${STORAGE_KEY}</code> 中，不会上传到产品业务后端。</p><a class="btn" href="#/login">退出到登录页</a></section></aside></div></div>`);
}

function login() {
  return `<main class="auth-page" id="app-main"><section class="auth-visual"><a href="#/login" class="brand"><span class="brand-mark"></span>STRIDE</a><h1>跑出下一步，<br>而不是更多数据。</h1><p>把计划、训练、反馈和判断连成一条线。</p></section><section class="auth-panel"><div><p class="eyebrow">WELCOME BACK</p><h2>继续你的训练周期</h2><form id="login-form" class="form-grid"><div class="field"><label for="email">邮箱</label><input id="email" type="email" value="demo@example.com" required></div><div class="field"><label for="password">密码</label><input id="password" type="password" value="stride2026" required></div><button class="btn primary" type="submit">进入 STRIDE</button></form><p class="small muted" style="margin-top:18px">还没有档案？ <a href="#/onboarding"><strong>开始建档</strong></a></p></div></section></main>`;
}

const onboardingSteps=[
  {eyebrow:'STEP 1 / 5',title:'你想跑向哪里？',body:()=>{const goal=state.onboardingDraft?.goal||state.goal.type||'半程马拉松';return `<div class="field"><label for="ob-goal">主要目标</label><select id="ob-goal" name="goal">${['半程马拉松','10 公里','全程马拉松','健康跑'].map((option)=>`<option ${goal===option?'selected':''}>${option}</option>`).join('')}</select></div><div class="field"><label for="ob-time">目标成绩</label><input id="ob-time" name="targetTime" value="${escapeHtml(state.onboardingDraft?.targetTime||state.goal.targetTime||'1:40:00')}"></div>`;}},
  {eyebrow:'STEP 2 / 5',title:'你现在跑到哪里？',body:()=>`<div class="field"><label for="ob-10k">10K 最近成绩</label><input id="ob-10k" name="tenK" value="${escapeHtml(state.onboardingDraft.tenK||'45:41')}"></div><div class="field"><label for="ob-vdot">VDOT</label><input id="ob-vdot" name="vdot" type="number" value="${Number(state.onboardingDraft.vdot)||44}"></div><div class="field"><label for="ob-volume">当前周跑量</label><input id="ob-volume" name="volume" type="number" value="${Number(state.onboardingDraft.volume)||45}"></div>`},
  {eyebrow:'STEP 3 / 5',title:'一周能练几天？',body:()=>`<fieldset><legend>可训练天数</legend><div class="choice-row">${[3,4,5,6].map(n=>`<label class="choice"><input type="radio" name="days" value="${n}" ${Number(state.onboardingDraft.days||5)===n?'checked':''}><span>${n} 天</span></label>`).join('')}</div></fieldset><div class="field"><label for="ob-timepref">偏好时间</label><select id="ob-timepref" name="time"><option ${state.onboardingDraft.time!=='晚间'?'selected':''}>清晨</option><option ${state.onboardingDraft.time==='晚间'?'selected':''}>晚间</option></select></div>`},
  {eyebrow:'STEP 4 / 5',title:'恢复与健康信号',body:()=>`<fieldset><legend>近期是否有影响跑步的疼痛？</legend><div class="choice-row"><label class="choice"><input type="radio" name="pain" value="no" ${state.onboardingDraft.pain!=='yes'?'checked':''}><span>没有</span></label><label class="choice"><input type="radio" name="pain" value="yes" ${state.onboardingDraft.pain==='yes'?'checked':''}><span>有，需要留意</span></label></div></fieldset><div class="field"><label for="ob-sleep">平均睡眠时长</label><input id="ob-sleep" name="sleep" type="number" step="0.5" value="${Number(state.onboardingDraft.sleep)||7.5}"></div>`},
  {eyebrow:'STEP 5 / 5',title:'锁定目标比赛',body:()=>`<div class="field"><label for="ob-race">比赛名称</label><input id="ob-race" name="race" value="${escapeHtml(state.onboardingDraft.race||'海南半程马拉松')}"></div><div class="field"><label for="ob-date">比赛日期</label><input id="ob-date" name="date" type="date" value="${escapeHtml(state.onboardingDraft.date||'2026-10-26')}"></div>`},
];
function onboarding() { const s=onboardingSteps[onboardingStep]; return `<main id="app-main" class="onboarding-shell"><a href="#/login" class="brand" style="color:var(--ink)"><span class="brand-mark" style="background:var(--action)"></span>STRIDE</a><section class="card" style="margin-top:28px"><p class="eyebrow">${s.eyebrow}</p><h1>${s.title}</h1><div class="stepper">${onboardingSteps.map((_,i)=>`<span class="${i<=onboardingStep?'active':''}"></span>`).join('')}</div><form id="onboarding-form" class="form-grid">${s.body()}<div class="hero-actions">${onboardingStep?'<button class="btn" type="button" data-action="ob-back">上一步</button>':''}<button class="btn dark" type="submit">${onboardingStep===4?'完成建档':'继续'} ${icon('arrow')}</button></div></form></section></main>`; }

const renderers = {'/':dashboard,'/plan':plan,'/workout/today':workoutDetail,'/workout/today/active':activeWorkout,'/workout/today/feedback':feedback,'/workout/today/review':review,'/analytics':analytics,'/coach':coach,'/race':race,'/profile':profile,'/login':login,'/onboarding':onboarding};
function render() {
  clearInterval(activeTimer);
  const fn=renderers[route()]||dashboard; app.innerHTML=fn(); window.scrollTo(0,0);
  document.title=`${app.querySelector('h1,h2')?.textContent?.trim()||'STRIDE'} · 跑步教练`;
  if(window.__strideRenderedOnce) app.querySelector('#app-main')?.setAttribute('tabindex','-1');
  window.__strideRenderedOnce=true;
  if(route()==='/workout/today/active'&&activeRunning) activeTimer=setInterval(()=>{activeSeconds++; const center=document.querySelector('.active-stat strong'); if(center){const mm=String(Math.floor(activeSeconds/60)).padStart(2,'0'),ss=String(activeSeconds%60).padStart(2,'0');center.textContent=`${mm}:${ss}`;}},1000);
  if(route()==='/coach') checkCoachHealth();
}

let _coachHealthTimer=null;
async function _checkCoachHealthOnce(){
  const el=document.querySelector('#coach-status'); if(!el)return;
  try{const r=await fetch('/api/rag/health',{signal:AbortSignal.timeout(2500)}); if(!r.ok)throw 0; const x=await r.json(); el.innerHTML=`<span class="status-dot"></span>${x.qdrant?.ok===false?'BM25 降级':'知识服务在线'}`;}
  catch{el.textContent='知识服务未连接';el.style.color='var(--danger)';}
}
function checkCoachHealth(){
  clearInterval(_coachHealthTimer);
  _checkCoachHealthOnce();
  _coachHealthTimer=setInterval(_checkCoachHealthOnce,8000); // RAG 预热期自动重试,就绪后页面自愈
}
async function sendCoach(query){
  state.chat ||= []; state.chat.push({role:'user',content:query}); save(); render();
  const box=document.querySelector('#chat-messages'); const bubble=document.createElement('div');bubble.className='bubble coach typing';bubble.textContent='';box.append(bubble);box.scrollTop=box.scrollHeight;
  try{
    abortCoach?.abort(); abortCoach=new AbortController();
    const history=state.chat.slice(-9,-1).map(m=>({role:m.role==='coach'?'assistant':'user',content:m.content}));
    const response=await fetch('/api/rag/chat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({query,history}),signal:abortCoach.signal});
    if(!response.ok||!response.body){let msg='';try{msg=(await response.json()).error}catch{}throw new Error(msg)}
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',answer='',sources=[];
    while(true){const {done,value}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});const parsed=parseSseBuffer(buffer);buffer=parsed.remainder;for(const event of parsed.events){if(event.type==='delta'){answer+=event.delta||event.content||'';bubble.textContent=answer;box.scrollTop=box.scrollHeight;}if(event.type==='sources')sources=event.sources;}if(done)break;}
    if(isRagErrorText(answer)) throw new Error('RAG_STREAM_ERROR');
    bubble.classList.remove('typing'); if(!answer) answer='知识服务已连接，但没有返回可显示的内容。';bubble.textContent=answer;const labels=sourceLabels(sources);if(labels.length){const source=document.createElement('span');source.className='source';source.textContent=`参考来源：${labels.join('；')}`;bubble.append(source);}state.chat.push({role:'coach',content:answer,sources});save();
  }catch(error){const message=error.name==='AbortError'?'本次回答已停止。':'知识服务暂未连接；请启动 rag/start_server.bat 后重试。训练计划与数据功能仍可使用。';bubble.className='bubble coach error';bubble.textContent=message;state.chat.push({role:'coach',content:message,error:true});save();}
}

window.addEventListener('hashchange',render);
document.addEventListener('click',(event)=>{
  const plan=event.target.closest('[data-plan]');if(plan){planView=plan.dataset.plan;render();return;}
  const range=event.target.closest('[data-range]');if(range){analyticsRange=range.dataset.range;render();return;}
  const prompt=event.target.closest('[data-prompt]');if(prompt){const input=document.querySelector('#coach-input');input.value=prompt.dataset.prompt;input.focus();return;}
  const action=event.target.closest('[data-action]')?.dataset.action;
  if(action==='accept-adjustment'){const sunday=state.weekPlan.find(x=>x.id==='sun-0816');sunday.previousTitle=`${sunday.distanceKm} km ${sunday.title}`;sunday.title='恢复跑';sunday.distanceKm=7;sunday.durationMin=45;sunday.adjusted=true;sunday.adjustmentReason='阈值课完成度高但主观疲劳需要控制；保留恢复质量，为下周训练蓄力。';state.adjustments[0].status='accepted';save();toast('已接受调整，并同步到周计划');render();}
  if(action==='reset-demo'){if(confirm('恢复演示数据会清除当前设备上的反馈与调整，继续吗？')){state=clone(demoData);save();toast('已恢复演示数据');render();}}
  if(action==='toggle-active'){activeRunning=!activeRunning;render();}
  if(action==='ob-back'){onboardingStep=Math.max(0,onboardingStep-1);render();}
});
document.addEventListener('change',(event)=>{if(event.target.matches('[data-race-check]')){const i=Number(event.target.dataset.raceCheck);state.races[0].checked ||= [];state.races[0].checked=event.target.checked?[...new Set([...state.races[0].checked,i])]:state.races[0].checked.filter(x=>x!==i);save();toast(event.target.checked?'清单已完成':'已取消完成');}});
document.addEventListener('submit',(event)=>{
  event.preventDefault(); const form=event.target; const data=new FormData(form);
  if(form.id==='feedback-form'){
    const todayPlan=state.weekPlan.find((item)=>item.workoutId===state.todayWorkout.id);
    if(state.athleteState.safetyMode){toast('安全模式下不能记录高强度训练');go('/plan');return;}
    if(todayPlan?.status==='completed'){toast('本次训练已经记录，原始数据已保留');go('/workout/today/review');return;}
    const feedback={rpe:Number(data.get('rpe')),legs:data.get('legs'),pain:Number(data.get('pain')),note:String(data.get('note')||''),submittedAt:new Date().toISOString()};
    const actual={distanceKm:13.08,durationSec:3978,paceSec:304,avgHr:158,thresholdPaceSec:273};
    state=completeWorkoutState(state,{feedback,actual});
    if(feedback.pain>=4) state=applySafetyPlan(state);
    state.analytics.weekly[state.analytics.weekly.length-1].distanceKm=state.athleteState.weeklyDistanceKm;
    save();go('/workout/today/review');
  }
  if(form.id==='profile-form'){state.athlete={...state.athlete,name:data.get('name'),avatarInitials:String(data.get('name')).slice(0,1),city:data.get('city'),level:data.get('level'),preferredTrainingTime:data.get('time'),tenKPersonalBest:data.get('tenK'),vdot:Number(data.get('vdot'))||44,halfMarathonPersonalBest:data.get('half')};save();toast('档案已保存');render();}
  if(form.id==='login-form'){toast('已进入 STRIDE');go('/');}
  if(form.id==='onboarding-form'){
    state.onboardingDraft ||= {};
    for (const [key,value] of data.entries()) state.onboardingDraft[key]=value;
    if(onboardingStep<4){onboardingStep++;save();render();}
    else{
      state.onboardingComplete=true;
      state.goal.type=state.onboardingDraft.goal||state.goal.type;
      state.goal.targetTime=state.onboardingDraft.targetTime||state.goal.targetTime;
      const goalDistanceKm=getGoalDistanceKm(state.goal.type);
      state.goal.targetPace=calculateRacePace(state.goal.targetTime,goalDistanceKm);
      state.athlete.tenKPersonalBest=state.onboardingDraft.tenK||state.athlete.tenKPersonalBest;
      state.athlete.vdot=Number(state.onboardingDraft.vdot)||44;
      state.athleteState.weeklyTargetKm=Number(state.onboardingDraft.volume)||45;
      state.athlete.trainingDaysPerWeek=Number(state.onboardingDraft.days)||5;
      state.athlete.preferredTrainingTime=state.onboardingDraft.time||state.athlete.preferredTrainingTime;
      state.athleteState.sleepHours=Number(state.onboardingDraft.sleep)||state.athleteState.sleepHours;
      state.athleteState.recentPain=state.onboardingDraft.pain==='yes';
      if(state.onboardingDraft.race) state.races[0].name=state.onboardingDraft.race;
      if(state.onboardingDraft.date){state.races[0].date=state.onboardingDraft.date;state.goal.raceDate=state.onboardingDraft.date;}
      state.races[0].targetTime=state.goal.targetTime;
      state.races[0].targetPace=state.goal.targetPace;
      state.races[0].distanceKm=goalDistanceKm;
      state.races[0].distance=`${goalDistanceKm} km`;
      if(state.athleteState.recentPain) state=applySafetyPlan(state);
      state.onboardingDraft={};
      save();toast('建档完成，计划已准备好');go('/');
    }
  }
  if(form.id==='coach-form'){const q=String(data.get('query')||'').trim();if(q)sendCoach(q);}
});

render();
