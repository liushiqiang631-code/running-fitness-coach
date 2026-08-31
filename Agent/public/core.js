/**
 * Deterministic helpers shared by the browser UI and server-side rendering.
 * They deliberately have no DOM, storage, or framework dependencies.
 */

const finiteNumber = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
};

const clamp = (value, min = 0, max = 100) => Math.min(max, Math.max(min, finiteNumber(value)));

const isPlainObject = (value) => value !== null
  && typeof value === 'object'
  && !Array.isArray(value);

const cloneValue = (value) => {
  if (Array.isArray(value)) return value.map(cloneValue);
  if (isPlainObject(value)) {
    return Object.fromEntries(Object.entries(value).map(([key, nested]) => [key, cloneValue(nested)]));
  }
  return value;
};

export function escapeHtml(value = '') {
  return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;',
  })[character]);
}

export function formatPace(secondsPerKilometre) {
  const seconds = Math.round(finiteNumber(secondsPerKilometre));
  if (seconds <= 0) return '--';

  const minutes = Math.floor(seconds / 60);
  const remainder = String(seconds % 60).padStart(2, '0');
  return `${minutes}'${remainder}"/km`;
}

export function formatDuration(totalSeconds) {
  const seconds = Math.round(finiteNumber(totalSeconds));
  if (seconds <= 0) return '0:00';

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = String(seconds % 60).padStart(2, '0');
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${remainder}` : `${minutes}:${remainder}`;
}

export function formatDistance(kilometres, digits = 1) {
  const distance = finiteNumber(kilometres);
  if (distance <= 0) return '0 km';
  return `${distance.toFixed(Math.max(0, Math.min(2, Math.round(finiteNumber(digits)))))} km`;
}

export function calculateCompletionRate(plan = []) {
  const trainingDays = Array.isArray(plan)
    ? plan.filter((item) => item && item.status !== 'rest')
    : [];
  if (!trainingDays.length) return 0;

  const completed = trainingDays.filter((item) => item.status === 'completed').length;
  return Math.round((completed / trainingDays.length) * 100);
}

export function calculateWeeklyDistance(items = []) {
  if (!Array.isArray(items)) return 0;
  return Math.round(items.reduce((sum, item) => {
    if (!item || typeof item !== 'object') return sum;
    if (item.actual !== null && typeof item.actual === 'object') {
      return sum + Math.max(0, finiteNumber(item.actual.distanceKm));
    }
    if (item.status !== 'completed') return sum;
    return sum + Math.max(0, finiteNumber(item.distanceKm));
  }, 0) * 10) / 10;
}

export function calculateWorkoutScore(components = {}) {
  const score = {
    completion: Math.round(clamp(components.completion)),
    intensity: Math.round(clamp(components.intensity)),
    stability: Math.round(clamp(components.stability)),
    recovery: Math.round(clamp(components.recovery)),
  };
  score.total = Math.round((score.completion + score.intensity + score.stability + score.recovery) / 4);
  return score;
}

export function isSafetyMode(painScore) {
  return finiteNumber(painScore) >= 4;
}

/**
 * Restores the current seed shape while retaining valid persisted values.
 * Seed keys form the schema, preventing stale local-storage keys from leaking
 * back into runtime state. A non-empty seeded array is required application
 * data, so an empty or invalid persisted replacement falls back to the seed.
 */
export function normalizeState(seed, saved) {
  if (!isPlainObject(seed)) return {};

  const merge = (defaults, persisted, key = '') => {
    if (Array.isArray(defaults)) {
      if (!Array.isArray(persisted) || (defaults.length > 0 && persisted.length === 0)) {
        return cloneValue(defaults);
      }
      if (defaults.length > 0 && isPlainObject(defaults[0])) {
        const defaultById = new Map(defaults.filter((item) => item?.id).map((item) => [item.id, item]));
        const merged = persisted.map((item, index) => merge(defaultById.get(item?.id) || defaults[index] || defaults[0], item));
        const persistedIds = new Set(persisted.map((item) => item?.id).filter(Boolean));
        return [...merged, ...defaults.filter((item) => item?.id && !persistedIds.has(item.id)).map(cloneValue)];
      }
      return cloneValue(persisted);
    }

    if (isPlainObject(defaults)) {
      const persistedObject = isPlainObject(persisted) ? persisted : {};
      if (Object.keys(defaults).length === 0) return cloneValue(persistedObject);
      return Object.fromEntries(Object.entries(defaults).map(([nestedKey, defaultValue]) => [
        nestedKey,
        merge(defaultValue, persistedObject[nestedKey], nestedKey),
      ]));
    }

    // The seed owns the schema version so old persisted snapshots migrate to
    // the current contract instead of downgrading it.
    if (key === 'version') return cloneValue(defaults);
    if (persisted === undefined) return cloneValue(defaults);
    if (defaults === null) return cloneValue(persisted);
    if (persisted === null) return cloneValue(defaults);
    if (typeof persisted !== typeof defaults) return cloneValue(defaults);
    return cloneValue(persisted);
  };

  return merge(seed, saved);
}

export function deriveReview(feedback, targetRpe = 7) {
  const input = isPlainObject(feedback) ? feedback : {};
  const rpe = clamp(input.rpe, 0, 10);
  const pain = clamp(input.pain, 0, 10);
  const legs = typeof input.legs === 'string' ? input.legs.toLowerCase() : 'normal';
  const safety = pain >= 4;
  const adjustmentEligible = !safety
    && (rpe >= 8 || legs === 'heavy' || legs === 'sore' || pain === 3);

  const legRecoveryPenalty = legs === 'sore' ? 24 : legs === 'heavy' ? 14 : 0;
  const painRecoveryPenalty = pain * 8;
  const score = calculateWorkoutScore({
    completion: 99,
    intensity: 96 - Math.abs(rpe - clamp(targetRpe, 0, 10)) * 9,
    stability: 93,
    recovery: 92 - legRecoveryPenalty - painRecoveryPenalty,
  });

  return { ...score, safety, adjustmentEligible };
}

export function completeWorkoutState(state, { feedback, actual } = {}) {
  const next = cloneValue(isPlainObject(state) ? state : {});
  const safeFeedback = isPlainObject(feedback) ? cloneValue(feedback) : {};
  const safeActual = isPlainObject(actual) ? cloneValue(actual) : {};
  const workoutId = next.todayWorkout?.id || next.weekPlan?.find((item) => item?.status === 'today')?.workoutId || 'today';
  const planItem = Array.isArray(next.weekPlan)
    ? next.weekPlan.find((item) => item?.workoutId === workoutId)
      || next.weekPlan.find((item) => item?.status === 'today')
      || next.weekPlan.find((item) => item?.date && item.date === next.todayWorkout?.date)
    : undefined;
  const existingActuals = isPlainObject(next.workoutActuals) ? next.workoutActuals : {};
  const wasAlreadyRecorded = Object.hasOwn(existingActuals, workoutId)
    || (planItem?.status === 'completed' && isPlainObject(planItem.actual));

  // Completion is an append-once domain event. Replaying it (for example,
  // after a double submit) returns an independent clone of the stored state
  // without replacing its original feedback, actual, or safety decision.
  if (wasAlreadyRecorded) return next;

  next.feedback = safeFeedback;
  next.workoutActuals = { ...existingActuals, [workoutId]: safeActual };
  if (planItem) {
    planItem.status = 'completed';
    planItem.actual = cloneValue(safeActual);
  }

  if (!isPlainObject(next.athleteState)) next.athleteState = {};
  const priorDistance = Math.max(0, finiteNumber(next.athleteState.weeklyDistanceKm));
  const actualDistance = Math.max(0, finiteNumber(safeActual.distanceKm));
  next.athleteState.weeklyDistanceKm = Math.round((priorDistance + actualDistance) * 100) / 100;
  if (isSafetyMode(safeFeedback.pain)) next.athleteState.safetyMode = true;

  return next;
}

export function applySafetyPlan(state) {
  const next = cloneValue(isPlainObject(state) ? state : {});
  if (!isPlainObject(next.athleteState)) next.athleteState = {};
  next.athleteState.safetyMode = true;

  const safetyReason = '疼痛评分达到安全阈值，暂停跑步负荷并以无痛恢复和观察为先。';
  const changes = {
    'sat-0815': { type: 'Recovery', title: '无痛恢复或休息', distanceKm: 0, durationMin: 30 },
    'sun-0816': { type: 'Rest', title: '休息与疼痛观察', distanceKm: 0, durationMin: 0, status: 'rest' },
  };

  if (Array.isArray(next.weekPlan)) {
    next.weekPlan = next.weekPlan.map((item) => {
      const change = changes[item?.id];
      if (!change) return item;
      const reason = item.reason || item.adjustmentReason || safetyReason;
      return {
        ...item,
        ...change,
        adjusted: true,
        previousTitle: item.previousTitle || item.title,
        reason,
        adjustmentReason: reason,
      };
    });
  }

  return next;
}

export function calculateRacePace(targetTime, distanceKm) {
  if (typeof targetTime !== 'string') return '--';
  const parts = targetTime.trim().split(':');
  if (parts.length !== 2 && parts.length !== 3) return '--';
  if (!parts.every((part) => /^\d+$/.test(part))) return '--';

  const numbers = parts.map(Number);
  const [hours, minutes, seconds] = parts.length === 3
    ? numbers
    : [0, numbers[0], numbers[1]];
  if (minutes >= 60 && parts.length === 3) return '--';
  if (seconds >= 60) return '--';

  const distance = Number(distanceKm);
  const totalSeconds = hours * 3600 + minutes * 60 + seconds;
  if (!Number.isFinite(distance) || distance <= 0 || totalSeconds <= 0) return '--';
  return formatPace(totalSeconds / distance);
}

export function getGoalDistanceKm(goalType) {
  const label = typeof goalType === 'string' ? goalType.trim().toLowerCase().replace(/\s+/g, '') : '';
  if (label.includes('全马') || label.includes('全程马拉松') || label.includes('marathon')) return 42.195;
  if (label.includes('10公里') || label.includes('10千米') || label === '10k') return 10;
  if (label.includes('健康跑') || label === '5k' || label.includes('5公里') || label.includes('5千米')) return 5;
  if (label.includes('半马') || label.includes('半程马拉松') || label.includes('half')) return 21.0975;
  return 21.0975;
}

export function isRagErrorText(text) {
  if (typeof text !== 'string') return false;
  return text.includes('[出错]') || text.includes('[错误]');
}

/**
 * Parses complete SSE frames and leaves an incomplete final frame untouched so
 * the caller can concatenate the next ReadableStream chunk before re-parsing.
 */
export function parseSseBuffer(buffer = '') {
  const source = typeof buffer === 'string' ? buffer : String(buffer ?? '');
  const events = [];
  let cursor = 0;

  while (cursor < source.length) {
    const match = /\r?\n\r?\n/g;
    match.lastIndex = cursor;
    const separator = match.exec(source);
    if (!separator) break;

    const frame = source.slice(cursor, separator.index).replace(/\r/g, '');
    cursor = separator.index + separator[0].length;
    const data = frame
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n');

    if (!data) continue;
    if (data === '[DONE]') {
      events.push({ type: 'done' });
      continue;
    }

    try {
      const payload = JSON.parse(data);
      if (payload && typeof payload === 'object') {
        if (Array.isArray(payload.sources)) {
          events.push({ type: 'sources', sources: payload.sources });
        } else if (typeof payload.delta === 'string' || typeof payload.content === 'string') {
          events.push({ type: 'delta', ...payload });
        }
      }
    } catch {
      // A complete malformed frame must not prevent later valid frames.
    }
  }

  return { events, remainder: source.slice(cursor) };
}
