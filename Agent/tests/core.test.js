import test from 'node:test';
import assert from 'node:assert/strict';

import {
  applySafetyPlan,
  calculateRacePace,
  calculateWeeklyDistance,
  calculateCompletionRate,
  calculateWorkoutScore,
  completeWorkoutState,
  deriveReview,
  escapeHtml,
  formatPace,
  getGoalDistanceKm,
  isRagErrorText,
  isSafetyMode,
  normalizeState,
  parseSseBuffer,
} from '../public/core.js';
import demoData from '../public/data.js';

test('completion rate excludes rest days', () => {
  assert.equal(calculateCompletionRate([
    { status: 'completed' },
    { status: 'planned' },
    { status: 'rest' },
  ]), 50);
});

test('weekly distance excludes future planned distance', () => {
  assert.equal(calculateWeeklyDistance([
    { status: 'completed', distanceKm: 8, actual: { distanceKm: 8.1 } },
    { status: 'completed', distanceKm: 7 },
    { status: 'today', distanceKm: 13.2 },
    { status: 'planned', distanceKm: 16 },
    { status: 'rest' },
  ]), 15.1);
});

test('weekly distance uses an actual whenever one exists and a completed prescription otherwise', () => {
  assert.equal(calculateWeeklyDistance([
    { status: 'planned', distanceKm: 10, actual: { distanceKm: 9.4 } },
    { status: 'completed', distanceKm: 6 },
    { status: 'today', distanceKm: 13 },
    { status: 'completed', distanceKm: 5, actual: {} },
  ]), 15.4);
});

test('HTML escaping covers text and attribute metacharacters', () => {
  assert.equal(escapeHtml(`<img src=x onerror="alert('x')"> &`), '&lt;img src=x onerror=&quot;alert(&#39;x&#39;)&quot;&gt; &amp;');
  assert.equal(escapeHtml(null), '');
});

test('state normalization deep-merges partial persisted objects without mutation', () => {
  const seed = {
    version: 2,
    athlete: { name: '林野', preferences: { units: 'metric', theme: 'light' } },
    athleteState: { recoveryScore: 82, safetyMode: false },
    runtimeFlags: { hydrated: false },
    races: [{ id: 'race', name: '默认赛事', checked: [] }],
    weekPlan: [{ id: 'today', status: 'today' }],
    adjustments: [{ id: 'adjustment', status: 'available' }],
    optionalHistory: [],
    feedback: null,
    dynamicRecords: {},
  };
  const saved = {
    version: 1,
    athlete: { preferences: { theme: 'dark' } },
    athleteState: { safetyMode: true },
    runtimeFlags: { hydrated: true },
    races: [{ id: 'race', checked: [0] }],
    weekPlan: [],
    adjustments: null,
    optionalHistory: [{ id: 'persisted' }],
    feedback: { pain: 3 },
    dynamicRecords: { workout: { distanceKm: 10 } },
    unknown: 'discard me',
  };
  const normalized = normalizeState(seed, saved);

  assert.deepEqual(normalized.athlete, { name: '林野', preferences: { units: 'metric', theme: 'dark' } });
  assert.deepEqual(normalized.athleteState, { recoveryScore: 82, safetyMode: true });
  assert.deepEqual(normalized.runtimeFlags, { hydrated: true });
  assert.deepEqual(normalized.races, [{ id: 'race', name: '默认赛事', checked: [0] }]);
  assert.deepEqual(normalized.weekPlan, seed.weekPlan);
  assert.deepEqual(normalized.adjustments, seed.adjustments);
  assert.deepEqual(normalized.optionalHistory, saved.optionalHistory);
  assert.deepEqual(normalized.feedback, saved.feedback);
  assert.deepEqual(normalized.dynamicRecords, saved.dynamicRecords);
  assert.equal(normalized.version, 2);
  assert.equal('unknown' in normalized, false);
  assert.notEqual(normalized.athlete, seed.athlete);
  assert.notEqual(normalized.races, saved.races);
  assert.deepEqual(seed.runtimeFlags, { hydrated: false });
  assert.deepEqual(saved.athlete, { preferences: { theme: 'dark' } });
});

test('state normalization tolerates invalid inputs', () => {
  assert.deepEqual(normalizeState({ nested: { enabled: true }, required: [1] }, null), {
    nested: { enabled: true },
    required: [1],
  });
  assert.deepEqual(normalizeState(null, { unsafe: true }), {});
});

test('state normalization restores required seeded array entries missing from an older snapshot', () => {
  const seed = {
    weekPlan: [
      { id: 'today', status: 'today', title: '阈值跑' },
      { id: 'sunday', status: 'planned', title: '恢复跑' },
    ],
  };
  const saved = { weekPlan: [{ id: 'today', status: 'completed' }] };

  assert.deepEqual(normalizeState(seed, saved).weekPlan, [
    { id: 'today', status: 'completed', title: '阈值跑' },
    { id: 'sunday', status: 'planned', title: '恢复跑' },
  ]);
});

test('review derivation makes only non-safety fatigue signals adjustment eligible', () => {
  const normal = deriveReview({ rpe: 7, legs: 'normal', pain: 0 });
  const highRpe = deriveReview({ rpe: 8, legs: 'normal', pain: 0 });
  const heavyLegs = deriveReview({ rpe: 7, legs: 'heavy', pain: 0 });
  const painThree = deriveReview({ rpe: 7, legs: 'fresh', pain: 3 });
  const safety = deriveReview({ rpe: 9, legs: 'sore', pain: 4 });

  assert.equal(normal.safety, false);
  assert.equal(normal.adjustmentEligible, false);
  assert.equal(highRpe.adjustmentEligible, true);
  assert.equal(heavyLegs.adjustmentEligible, true);
  assert.equal(painThree.adjustmentEligible, true);
  assert.equal(safety.safety, true);
  assert.equal(safety.adjustmentEligible, false);
  assert.ok(highRpe.intensity < normal.intensity);
  assert.ok(heavyLegs.recovery < normal.recovery);
  assert.ok(safety.recovery < painThree.recovery);
  assert.equal(normal.total, calculateWorkoutScore(normal).total);
});

test('workout completion records actual volume once and never clears safety', () => {
  const original = structuredClone(demoData);
  const feedback = { rpe: 8, legs: 'heavy', pain: 4, note: 'left knee' };
  const actual = { distanceKm: 13.08, durationSec: 3978, paceSec: 304 };
  const completed = completeWorkoutState(original, { feedback, actual });
  const completedAgain = completeWorkoutState(completed, { feedback: { ...feedback, pain: 0 }, actual });
  const today = completed.weekPlan.find((item) => item.id === 'thu-0813');

  assert.notEqual(completed, original);
  assert.equal(original.feedback, null);
  assert.deepEqual(completed.feedback, feedback);
  assert.deepEqual(completed.workoutActuals['today-threshold'], actual);
  assert.equal(today.status, 'completed');
  assert.deepEqual(today.actual, actual);
  assert.equal(completed.athleteState.weeklyDistanceKm, 28.18);
  assert.equal(completedAgain.athleteState.weeklyDistanceKm, 28.18);
  assert.equal(completedAgain.athleteState.safetyMode, true);
});

test('repeating workout completion preserves the first feedback, actual, and safety state', () => {
  const first = completeWorkoutState(structuredClone(demoData), {
    feedback: { rpe: 7, legs: 'normal', pain: 0, note: 'first' },
    actual: { distanceKm: 13.08, durationSec: 3978 },
  });
  const repeated = completeWorkoutState(first, {
    feedback: { rpe: 10, legs: 'sore', pain: 8, note: 'replacement' },
    actual: { distanceKm: 99, durationSec: 1 },
  });

  assert.deepEqual(repeated, first);
  assert.notEqual(repeated, first);
  assert.notEqual(repeated.feedback, first.feedback);
});

test('safety plan persists globally and directly downgrades both weekend sessions', () => {
  const original = structuredClone(demoData);
  const once = applySafetyPlan(original);
  const twice = applySafetyPlan(once);
  const saturday = once.weekPlan.find((item) => item.id === 'sat-0815');
  const sunday = once.weekPlan.find((item) => item.id === 'sun-0816');

  assert.equal(original.athleteState.safetyMode, false);
  assert.equal(once.athleteState.safetyMode, true);
  assert.deepEqual(
    { type: saturday.type, title: saturday.title, distanceKm: saturday.distanceKm, durationMin: saturday.durationMin, adjusted: saturday.adjusted },
    { type: 'Recovery', title: '无痛恢复或休息', distanceKm: 0, durationMin: 30, adjusted: true },
  );
  assert.deepEqual(
    { type: sunday.type, title: sunday.title, distanceKm: sunday.distanceKm, durationMin: sunday.durationMin, status: sunday.status, adjusted: sunday.adjusted },
    { type: 'Rest', title: '休息与疼痛观察', distanceKm: 0, durationMin: 0, status: 'rest', adjusted: true },
  );
  assert.equal(saturday.previousTitle, '长距离耐力跑');
  assert.equal(sunday.previousTitle, '轻松恢复跑');
  assert.ok(saturday.adjustmentReason);
  assert.ok(sunday.adjustmentReason);
  assert.deepEqual(twice, once);
});

test('race pace parses race times and rejects malformed values', () => {
  assert.equal(calculateRacePace('1:40:00', 21.0975), `4'44"/km`);
  assert.equal(calculateRacePace('40:00', 10), `4'00"/km`);
  assert.equal(calculateRacePace('1:60:00', 21.1), '--');
  assert.equal(calculateRacePace('not-a-time', 21.1), '--');
});

test('goal distance maps common Chinese race labels and defaults to a half marathon', () => {
  assert.equal(getGoalDistanceKm('半马'), 21.0975);
  assert.equal(getGoalDistanceKm('半程马拉松'), 21.0975);
  assert.equal(getGoalDistanceKm('10公里'), 10);
  assert.equal(getGoalDistanceKm('10 公里'), 10);
  assert.equal(getGoalDistanceKm('全马'), 42.195);
  assert.equal(getGoalDistanceKm('健康跑'), 5);
  assert.equal(getGoalDistanceKm('越野挑战'), 21.0975);
  assert.equal(getGoalDistanceKm(null), 21.0975);
});

test('RAG error detection recognizes only supported leading markers', () => {
  assert.equal(isRagErrorText('  [出错] 服务暂不可用'), true);
  assert.equal(isRagErrorText('\n[错误] 请求失败'), true);
  assert.equal(isRagErrorText('已有部分回答……[错误] 流式请求中断'), true);
  assert.equal(isRagErrorText('已有部分回答……[出错] 流式请求中断'), true);
  assert.equal(isRagErrorText('普通回答，没有错误标记'), false);
  assert.equal(isRagErrorText(null), false);
});

test('pain score four activates safety mode', () => {
  assert.equal(isSafetyMode(3), false);
  assert.equal(isSafetyMode(4), true);
});

test('pace formats seconds per kilometre', () => {
  assert.equal(formatPace(275), `4'35"/km`);
  assert.equal(formatPace(0), '--');
});

test('workout score averages four bounded components', () => {
  assert.deepEqual(calculateWorkoutScore({
    completion: 96,
    intensity: 88,
    stability: 92,
    recovery: 84,
  }), { completion: 96, intensity: 88, stability: 92, recovery: 84, total: 90 });
});

test('SSE parser preserves a partial trailing frame', () => {
  assert.deepEqual(parseSseBuffer('data: {"delta":"你好"}\n\ndata: [DONE]\n\ndata: {"del'), {
    events: [{ type: 'delta', delta: '你好' }, { type: 'done' }],
    remainder: 'data: {"del',
  });
});

test('SSE parser recognizes a sources event after streamed text across chunks', () => {
  const first = parseSseBuffer('data: {"delta":"先给结论"}\n\ndata: {"sources":[{"title":"丹尼尔斯经典跑步训练法","pa');
  const second = parseSseBuffer(`${first.remainder}ge":"91"}]}\n\ndata: [DONE]\n\n`);

  assert.deepEqual(first.events, [{ type: 'delta', delta: '先给结论' }]);
  assert.deepEqual(second.events, [
    { type: 'sources', sources: [{ title: '丹尼尔斯经典跑步训练法', page: '91' }] },
    { type: 'done' },
  ]);
  assert.equal(second.remainder, '');
});

test('demo workout matches the four-by-two-kilometre threshold prescription', () => {
  const thresholdReps = demoData.todayWorkout.segments.filter((segment) => segment.name.startsWith('阈值'));
  const prescribedDistance = demoData.todayWorkout.segments.reduce((sum, segment) => sum + segment.distanceKm, 0);

  assert.equal(thresholdReps.length, 4);
  assert.ok(thresholdReps.every((segment) => segment.distanceKm === 2));
  assert.ok(Math.abs(prescribedDistance - 13.2) < Number.EPSILON * 16);
  assert.equal(demoData.todayWorkout.targetPace, `4'32"–4'38"/km`);
});

test('demo adjustment preserves a seven-kilometre recovery run', () => {
  assert.deepEqual(demoData.adjustments[0].proposed, {
    type: 'Recovery',
    distanceKm: 7,
    durationMin: 45,
  });
});
